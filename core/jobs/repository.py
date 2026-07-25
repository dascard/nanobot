"""Durable Job Port 的线程安全内存参考实现。"""

from __future__ import annotations

from datetime import datetime, timedelta
from threading import RLock
from typing import Callable

from core.fencing import new_fencing_token
from core.jobs.contracts import (
    JobClaim,
    JobCorrelation,
    JobFailure,
    JobLease,
    JobLeaseLost,
    JobLifecycle,
    JobLifecycleError,
    JobOutcome,
    JobRecord,
    JobResult,
    JobStatus,
)
from core.jobs.policies import JobRetryPolicy, JobSchedulePolicy
from core.jobs.registry import JOB_DESCRIPTOR_REGISTRY
from core.resilience import FailureCategory


class InMemoryJobRepository:
    """测试与 Adapter conformance 使用；不作为生产持久化。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, JobRecord] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._next_id = 1
        self._mutation_count = 0

    @property
    def mutation_count(self) -> int:
        with self._lock:
            return self._mutation_count

    def enqueue(
        self,
        *,
        descriptor_id: str,
        idempotency_key: str,
        payload_ref: str,
        payload_sha256: str,
        correlation: JobCorrelation = JobCorrelation(),
        now: datetime,
    ) -> JobRecord:
        descriptor = JOB_DESCRIPTOR_REGISTRY.require(descriptor_id)
        if descriptor.lifecycle is not JobLifecycle.ACTIVE:
            raise JobLifecycleError(
                f"Durable Job 尚未启用：{descriptor.job_type}"
            )
        key = (descriptor.job_type, str(idempotency_key or "").strip())
        with self._lock:
            existing_id = self._idempotency.get(key)
            if existing_id is not None:
                return self._records[existing_id]
            job_id = f"job_{self._next_id:016d}"
            self._next_id += 1
            record = JobRecord(
                job_id=job_id,
                descriptor_id=descriptor.job_type,
                status=JobStatus.PENDING,
                generation=0,
                attempt_count=0,
                idempotency_key=idempotency_key,
                payload_ref=payload_ref,
                payload_sha256=payload_sha256,
                correlation=correlation,
                created_at=now,
                updated_at=now,
            )
            self._records[job_id] = record
            self._idempotency[key] = job_id
            self._mutation_count += 1
            return record

    def claim(
        self,
        *,
        descriptor_id: str,
        worker_id: str,
        schedule_policy: JobSchedulePolicy,
        now: datetime,
    ) -> JobClaim | None:
        descriptor = JOB_DESCRIPTOR_REGISTRY.require(descriptor_id)
        if descriptor.lifecycle is not JobLifecycle.ACTIVE:
            raise JobLifecycleError(
                f"Durable Job 尚未启用：{descriptor.job_type}"
            )
        with self._lock:
            candidates = sorted(
                (
                    record
                    for record in self._records.values()
                    if record.descriptor_id == descriptor.job_type
                    and (
                        record.status is JobStatus.PENDING
                        or (
                            record.status is JobStatus.RETRY_WAIT
                            and record.next_attempt_at is not None
                            and record.next_attempt_at <= now
                        )
                    )
                ),
                key=lambda item: (item.created_at, item.job_id),
            )
            if not candidates:
                return None
            current = candidates[0]
            generation = current.generation + 1
            attempt_no = current.attempt_count + 1
            lease = JobLease(
                job_id=current.job_id,
                worker_id=worker_id,
                owner_token=new_fencing_token(),
                generation=generation,
                attempt_no=attempt_no,
                expires_at=now + timedelta(
                    seconds=schedule_policy.lease_seconds
                ),
            )
            running = current.changed(
                status=JobStatus.RUNNING,
                generation=generation,
                attempt_count=attempt_no,
                next_attempt_at=None,
                lease=lease,
                failure=None,
                finished_at=None,
                updated_at=now,
            )
            self._records[current.job_id] = running
            self._mutation_count += 1
            return JobClaim(record=running, lease=lease)

    @staticmethod
    def _lease_matches(
        current: JobRecord,
        lease: JobLease,
        *,
        now: datetime,
    ) -> bool:
        active = current.lease
        return bool(
            current.status is JobStatus.RUNNING
            and active is not None
            and active.job_id == lease.job_id
            and active.worker_id == lease.worker_id
            and active.owner_token == lease.owner_token
            and active.generation == lease.generation
            and active.attempt_no == lease.attempt_no
            and active.expires_at > now
        )

    def _require_lease(
        self,
        lease: JobLease,
        *,
        now: datetime,
    ) -> JobRecord:
        current = self._records.get(lease.job_id)
        if current is None or not self._lease_matches(
            current,
            lease,
            now=now,
        ):
            raise JobLeaseLost("durable_job_lease_lost")
        return current

    def heartbeat(
        self,
        lease: JobLease,
        *,
        schedule_policy: JobSchedulePolicy,
        now: datetime,
    ) -> JobLease:
        with self._lock:
            current = self._require_lease(lease, now=now)
            renewed = JobLease(
                job_id=lease.job_id,
                worker_id=lease.worker_id,
                owner_token=lease.owner_token,
                generation=lease.generation,
                attempt_no=lease.attempt_no,
                expires_at=now + timedelta(
                    seconds=schedule_policy.lease_seconds
                ),
            )
            self._records[current.job_id] = current.changed(
                lease=renewed,
                updated_at=now,
            )
            self._mutation_count += 1
            return renewed

    def settle(
        self,
        lease: JobLease,
        result: JobResult,
        *,
        retry_policy: JobRetryPolicy,
        now: datetime,
    ) -> JobRecord:
        with self._lock:
            current = self._require_lease(lease, now=now)
            if result.outcome is JobOutcome.SUCCEEDED:
                status = JobStatus.SUCCEEDED
                next_attempt_at = None
                failure = None
                finished_at = now
            elif result.outcome is JobOutcome.CANCELLED:
                status = JobStatus.CANCELLED
                next_attempt_at = None
                failure = None
                finished_at = now
            else:
                failure = result.failure
                if failure is None:  # pragma: no cover - JobResult 已校验
                    raise ValueError("failed JobResult 缺少 failure")
                retry = retry_policy.allows_retry(
                    failure,
                    attempt_count=current.attempt_count,
                )
                status = (
                    JobStatus.RETRY_WAIT if retry else JobStatus.FAILED
                )
                next_attempt_at = (
                    now + timedelta(
                        seconds=retry_policy.delay_seconds(
                            attempt_count=current.attempt_count
                        )
                    )
                    if retry
                    else None
                )
                finished_at = None if retry else now
            settled = current.changed(
                status=status,
                lease=None,
                next_attempt_at=next_attempt_at,
                failure=failure,
                result_ref=result.result_ref,
                finished_at=finished_at,
                updated_at=now,
            )
            self._records[current.job_id] = settled
            self._mutation_count += 1
            return settled

    def recover_expired(
        self,
        *,
        now: datetime,
        retry_policy_resolver: Callable[[str], JobRetryPolicy],
    ) -> int:
        with self._lock:
            expired = [
                record
                for record in self._records.values()
                if record.status is JobStatus.RUNNING
                and record.lease is not None
                and record.lease.expires_at <= now
            ]
            for current in expired:
                descriptor = JOB_DESCRIPTOR_REGISTRY.require(
                    current.descriptor_id
                )
                retry_policy = retry_policy_resolver(
                    descriptor.retry_policy_id
                )
                failure = JobFailure(
                    code="lease_expired",
                    category=FailureCategory.TIMEOUT,
                    retryable=True,
                    safe_summary="Job 租约到期，等待安全恢复",
                    trace_ref=current.correlation.trace_id,
                )
                retry = retry_policy.allows_retry(
                    failure,
                    attempt_count=current.attempt_count,
                )
                next_attempt_at = (
                    now + timedelta(
                        seconds=retry_policy.delay_seconds(
                            attempt_count=current.attempt_count
                        )
                    )
                    if retry
                    else None
                )
                self._records[current.job_id] = current.changed(
                    status=(
                        JobStatus.RETRY_WAIT
                        if retry
                        else JobStatus.FAILED
                    ),
                    lease=None,
                    next_attempt_at=next_attempt_at,
                    failure=failure,
                    finished_at=None if retry else now,
                    updated_at=now,
                )
                self._mutation_count += 1
            return len(expired)

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._records.get(str(job_id or ""))


__all__ = ["InMemoryJobRepository"]
