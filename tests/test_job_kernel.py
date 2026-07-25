"""Durable Job Kernel 的状态机、fencing 与幂等合同测试。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _now() -> datetime:
    return datetime(2026, 7, 23, 12, 0, 0)


def test_builtin_job_descriptors_cover_registered_execution_modes():
    from core.jobs import (
        JOB_DESCRIPTOR_REGISTRY,
        JobLifecycle,
        JobRepositoryMode,
    )

    descriptors = {
        item.job_type: item
        for item in JOB_DESCRIPTOR_REGISTRY.descriptors()
    }

    assert tuple(descriptors) == (
        "group_memory_learning",
        "memory_digest",
        "outbound_delivery",
        "sandbox_admin_operation",
        "semantic_index",
        "session_summary",
    )
    assert descriptors["group_memory_learning"].lifecycle is (
        JobLifecycle.ACTIVE
    )
    assert descriptors["group_memory_learning"].repository_mode is (
        JobRepositoryMode.PORT_ADAPTER
    )
    assert descriptors["outbound_delivery"].repository_mode is (
        JobRepositoryMode.PORT_ADAPTER
    )
    assert all(descriptor.owner_module for descriptor in descriptors.values())
    assert all(descriptor.retry_policy_id for descriptor in descriptors.values())
    assert all(
        descriptor.schedule_policy_id
        for descriptor in descriptors.values()
    )


def test_enqueue_is_idempotent_and_does_not_store_payload_body():
    from core.jobs import InMemoryJobRepository, JobCorrelation

    repository = InMemoryJobRepository()
    correlation = JobCorrelation(
        trace_id="trace-1",
        run_id="run-1",
        task_id="task-1",
        tool_call_id="tool-1",
    )

    first = repository.enqueue(
        descriptor_id="session_summary",
        idempotency_key="session:u1:1-20",
        payload_ref="summary-source://session/u1/1-20",
        payload_sha256="a" * 64,
        correlation=correlation,
        now=_now(),
    )
    second = repository.enqueue(
        descriptor_id="session_summary",
        idempotency_key="session:u1:1-20",
        payload_ref="summary-source://session/u1/1-20",
        payload_sha256="a" * 64,
        correlation=correlation,
        now=_now(),
    )

    assert first == second
    assert repository.mutation_count == 1
    assert first.payload_ref == "summary-source://session/u1/1-20"
    assert first.payload_sha256 == "a" * 64
    assert not hasattr(first, "payload")
    assert first.correlation == correlation


def test_claim_is_atomic_and_empty_poll_has_no_write_amplification():
    from core.jobs import InMemoryJobRepository, require_job_schedule_policy

    repository = InMemoryJobRepository()
    schedule = require_job_schedule_policy("background.standard.v1")
    repository.enqueue(
        descriptor_id="session_summary",
        idempotency_key="session:u1:1-20",
        payload_ref="summary-source://session/u1/1-20",
        payload_sha256="a" * 64,
        now=_now(),
    )

    first = repository.claim(
        descriptor_id="session_summary",
        worker_id="worker-a",
        schedule_policy=schedule,
        now=_now(),
    )
    second = repository.claim(
        descriptor_id="session_summary",
        worker_id="worker-b",
        schedule_policy=schedule,
        now=_now(),
    )
    mutations_after_claim = repository.mutation_count
    empty = repository.claim(
        descriptor_id="session_summary",
        worker_id="worker-c",
        schedule_policy=schedule,
        now=_now(),
    )

    assert first is not None
    assert first.lease.worker_id == "worker-a"
    assert first.lease.generation == 1
    assert first.record.status.value == "running"
    assert second is None
    assert empty is None
    assert repository.mutation_count == mutations_after_claim


def test_heartbeat_and_settle_require_job_owner_token_generation_and_running():
    from core.jobs import (
        InMemoryJobRepository,
        JobLease,
        JobLeaseLost,
        JobResult,
        require_job_retry_policy,
        require_job_schedule_policy,
    )

    repository = InMemoryJobRepository()
    repository.enqueue(
        descriptor_id="semantic_index",
        idempotency_key="semantic:item:1",
        payload_ref="semantic-source://item/1",
        payload_sha256="b" * 64,
        now=_now(),
    )
    schedule = require_job_schedule_policy("background.standard.v1")
    claim = repository.claim(
        descriptor_id="semantic_index",
        worker_id="worker-a",
        schedule_policy=schedule,
        now=_now(),
    )
    assert claim is not None

    stale = JobLease(
        job_id=claim.lease.job_id,
        worker_id=claim.lease.worker_id,
        owner_token="0" * 64,
        generation=claim.lease.generation,
        attempt_no=claim.lease.attempt_no,
        expires_at=claim.lease.expires_at,
    )
    with pytest.raises(JobLeaseLost):
        repository.heartbeat(
            stale,
            schedule_policy=schedule,
            now=_now() + timedelta(seconds=1),
        )
    with pytest.raises(JobLeaseLost):
        repository.settle(
            stale,
            JobResult.succeeded(result_ref="semantic://item/1"),
            retry_policy=require_job_retry_policy(
                "semantic_index.v1"
            ),
            now=_now() + timedelta(seconds=1),
        )

    renewed = repository.heartbeat(
        claim.lease,
        schedule_policy=schedule,
        now=_now() + timedelta(seconds=1),
    )
    settled = repository.settle(
        renewed,
        JobResult.succeeded(result_ref="semantic://item/1"),
        retry_policy=require_job_retry_policy("semantic_index.v1"),
        now=_now() + timedelta(seconds=2),
    )
    assert settled.status.value == "succeeded"
    with pytest.raises(JobLeaseLost):
        repository.settle(
            renewed,
            JobResult.succeeded(result_ref="semantic://item/1"),
            retry_policy=require_job_retry_policy(
                "semantic_index.v1"
            ),
            now=_now() + timedelta(seconds=3),
        )


def test_expired_lease_recovery_rejects_old_owner_and_uses_retry_wait():
    from core.jobs import (
        InMemoryJobRepository,
        JobLeaseLost,
        JobResult,
        require_job_retry_policy,
        require_job_schedule_policy,
    )

    repository = InMemoryJobRepository()
    repository.enqueue(
        descriptor_id="memory_digest",
        idempotency_key="digest:group:1:2026-07-23",
        payload_ref="digest-source://group/1/2026-07-23",
        payload_sha256="c" * 64,
        now=_now(),
    )
    schedule = require_job_schedule_policy("background.standard.v1")
    old_claim = repository.claim(
        descriptor_id="memory_digest",
        worker_id="worker-old",
        schedule_policy=schedule,
        now=_now(),
    )
    assert old_claim is not None

    recovered = repository.recover_expired(
        now=old_claim.lease.expires_at,
        retry_policy_resolver=require_job_retry_policy,
    )
    record = repository.get(old_claim.record.job_id)
    assert recovered == 1
    assert record is not None
    assert record.status.value == "retry_wait"
    assert record.next_attempt_at is not None

    with pytest.raises(JobLeaseLost):
        repository.settle(
            old_claim.lease,
            JobResult.succeeded(result_ref="digest://1"),
            retry_policy=require_job_retry_policy("memory_digest.v1"),
            now=old_claim.lease.expires_at + timedelta(seconds=1),
        )


def test_kernel_reuses_stable_side_effect_idempotency_key_across_retry():
    from core.jobs import (
        DurableJobKernel,
        InMemoryJobRepository,
        JobFailure,
        JobResult,
    )
    from core.resilience import FailureCategory

    repository = InMemoryJobRepository()
    repository.enqueue(
        descriptor_id="session_summary",
        idempotency_key="summary:session:1",
        payload_ref="summary-source://session/1",
        payload_sha256="d" * 64,
        now=_now(),
    )
    seen_keys: list[str] = []

    class Handler:
        def handle(self, record, context):
            seen_keys.append(context.side_effect_idempotency_key)
            if len(seen_keys) == 1:
                return JobResult.failed(
                    JobFailure(
                        code="provider_unavailable",
                        category=FailureCategory.UNAVAILABLE,
                        retryable=True,
                        safe_summary="Provider 暂不可用",
                    )
                )
            return JobResult.succeeded(result_ref="summary://1")

    kernel = DurableJobKernel(
        repository,
        handlers={"session_summary.handler": Handler()},
    )

    first = kernel.run_once(
        "session_summary",
        worker_id="worker-a",
        now=_now(),
    )
    assert first is not None
    assert first.status.value == "retry_wait"
    second = kernel.run_once(
        "session_summary",
        worker_id="worker-b",
        now=first.next_attempt_at,
    )

    assert second is not None
    assert second.status.value == "succeeded"
    assert seen_keys == [
        "summary:session:1",
        "summary:session:1",
    ]
    assert second.generation == 2
    assert second.attempt_count == 2


def test_kernel_emits_safe_claim_retry_and_settlement_telemetry():
    from core.jobs import (
        DurableJobKernel,
        InMemoryJobRepository,
        JobCorrelation,
        JobFailure,
        JobResult,
    )
    from core.resilience import FailureCategory
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import (
        InMemoryRuntimeEventSink,
        RuntimeEventEmitter,
    )

    repository = InMemoryJobRepository()
    correlation = JobCorrelation(
        request_id="request-job-kernel",
        session_id="qq:private:u1",
        turn_id="turn-11",
        trace_id="trace-job-kernel",
        run_id="run-job-kernel",
        task_id="session_summary",
        tool_call_id="tool-job-kernel",
        delivery_id="delivery-job-kernel",
        parent_job_id="parent-job-kernel",
    )
    repository.enqueue(
        descriptor_id="session_summary",
        idempotency_key="summary:telemetry:1",
        payload_ref="summary-source://telemetry/1",
        payload_sha256="f" * 64,
        correlation=correlation,
        now=_now(),
    )

    attempts = 0

    class Handler:
        def handle(self, record, context):
            nonlocal attempts
            del record, context
            attempts += 1
            if attempts == 1:
                return JobResult.failed(
                    JobFailure(
                        code="provider_unavailable",
                        category=FailureCategory.UNAVAILABLE,
                        retryable=True,
                        safe_summary="Provider 暂不可用",
                    )
                )
            return JobResult.succeeded(result_ref="summary://telemetry/1")

    sink = InMemoryRuntimeEventSink()
    kernel = DurableJobKernel(
        repository,
        handlers={"session_summary.handler": Handler()},
        event_emitter=RuntimeEventEmitter(
            RUNTIME_EVENT_REGISTRY,
            (sink,),
        ),
    )

    first = kernel.run_once(
        "session_summary",
        worker_id="worker-a",
        now=_now(),
    )
    assert first is not None
    second = kernel.run_once(
        "session_summary",
        worker_id="worker-b",
        now=first.next_attempt_at,
    )

    assert second is not None
    assert [
        event.attributes["transition"]
        for event in sink.events
    ] == [
        "lease_claimed",
        "retry_scheduled",
        "lease_claimed",
        "settled",
    ]
    assert [
        event.attributes["status"]
        for event in sink.events
    ] == [
        "running",
        "retry_wait",
        "running",
        "succeeded",
    ]
    assert sink.events[1].attributes["failure_code"] == (
        "provider_unavailable"
    )
    assert sink.events[1].attributes["retry_scheduled"] is True
    assert sink.events[3].attributes["retry_scheduled"] is False
    assert all(
        event.context.request_id == correlation.request_id
        and event.context.session_id == correlation.session_id
        and event.context.parent_job_id == correlation.parent_job_id
        for event in sink.events
    )
    assert all(
        event.context.job_id == second.job_id
        for event in sink.events
    )
    assert "owner_token" not in repr(sink.events)


def test_kernel_job_telemetry_failure_does_not_change_business_result():
    from core.jobs import (
        DurableJobKernel,
        InMemoryJobRepository,
        JobResult,
    )
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import RuntimeEventEmitter

    repository = InMemoryJobRepository()
    repository.enqueue(
        descriptor_id="session_summary",
        idempotency_key="summary:telemetry-failure",
        payload_ref="summary-source://telemetry-failure",
        payload_sha256="1" * 64,
        now=_now(),
    )

    class Handler:
        def handle(self, record, context):
            del record, context
            return JobResult.succeeded(result_ref="summary://success")

    class BrokenSink:
        def emit(self, event):
            del event
            raise RuntimeError("telemetry unavailable")

    kernel = DurableJobKernel(
        repository,
        handlers={"session_summary.handler": Handler()},
        event_emitter=RuntimeEventEmitter(
            RUNTIME_EVENT_REGISTRY,
            (BrokenSink(),),
            fail_open=False,
        ),
    )

    settled = kernel.run_once(
        "session_summary",
        worker_id="worker-a",
        now=_now(),
    )

    assert settled is not None
    assert settled.status.value == "succeeded"


def test_kernel_leaves_programming_error_running_for_fenced_recovery():
    from core.jobs import DurableJobKernel, InMemoryJobRepository

    repository = InMemoryJobRepository()
    queued = repository.enqueue(
        descriptor_id="session_summary",
        idempotency_key="summary:programming-error",
        payload_ref="summary-source://programming-error",
        payload_sha256="e" * 64,
        now=_now(),
    )

    class BrokenHandler:
        def handle(self, record, context):
            del record, context
            raise TypeError("handler programming error")

    kernel = DurableJobKernel(
        repository,
        handlers={"session_summary.handler": BrokenHandler()},
    )

    with pytest.raises(TypeError, match="programming error"):
        kernel.run_once(
            "session_summary",
            worker_id="worker-a",
            now=_now(),
        )

    running = repository.get(queued.job_id)
    assert running is not None
    assert running.status.value == "running"


def test_job_failure_and_correlation_are_safe_metadata_only():
    from core.jobs import JobCorrelation, JobFailure
    from core.resilience import FailureCategory

    failure = JobFailure(
        code="provider_unavailable",
        category=FailureCategory.UNAVAILABLE,
        retryable=True,
        safe_summary="失败\n" + ("x" * 400),
        cause_type="HTTPError\tsecret",
        trace_ref="trace\n1",
    )
    correlation = JobCorrelation(
        request_id="request\n1",
        session_id="session\t1",
        trace_id="trace\n1",
        run_id="run\n1",
        task_id="task\n1",
        tool_call_id="tool\n1",
    )

    assert len(failure.safe_summary) == 240
    assert "\n" not in failure.safe_summary
    assert failure.cause_type == "HTTPError secret"
    assert failure.trace_ref == "trace 1"
    assert correlation.request_id == "request 1"
    assert correlation.tool_call_id == "tool 1"


def test_session_summary_adapter_rejects_stale_same_owner_lease(db_session):
    from app.session_memory.jobs import (
        SessionSummaryJobLeaseLost,
        assert_summary_job_lease,
        claim_summary_job,
        recover_stale_running_jobs,
        renew_summary_job_lease,
        session_summary_job_lease,
    )
    from core.db.models.session_memory import SessionSummaryJob

    job = SessionSummaryJob(
        session_id="lease-generation-session",
        user_id="u1",
        status="pending",
        max_retry=3,
    )
    db_session.add(job)
    db_session.commit()

    first_row = claim_summary_job(
        db_session,
        job.id,
        owner="same-worker",
        lease_seconds=30,
        now=_now(),
    )
    assert first_row is not None
    first = session_summary_job_lease(first_row)

    assert recover_stale_running_jobs(
        db_session,
        now=first.expires_at,
    ) == 1
    second_row = claim_summary_job(
        db_session,
        job.id,
        owner="same-worker",
        lease_seconds=30,
        now=first.expires_at,
    )
    assert second_row is not None
    second = session_summary_job_lease(second_row)

    assert second.owner_token != first.owner_token
    assert second.generation == first.generation + 1
    assert renew_summary_job_lease(
        db_session,
        lease=first,
        now=first.expires_at + timedelta(seconds=1),
    ) is False
    with pytest.raises(SessionSummaryJobLeaseLost):
        assert_summary_job_lease(
            db_session,
            first,
            now=first.expires_at + timedelta(seconds=1),
        )
    assert renew_summary_job_lease(
        db_session,
        lease=second,
        now=first.expires_at + timedelta(seconds=1),
    ) is True


def test_session_summary_settlement_requires_claimed_lease(db_session):
    from app.session_memory.jobs import (
        claim_summary_job,
        recover_stale_running_jobs,
        session_summary_job_lease,
    )
    from app.session_memory.llm_summarizer import (
        fail_claimed_session_summary_job,
    )
    from core.db.models.session_memory import SessionSummaryJob

    job = SessionSummaryJob(
        session_id="lease-settlement-session",
        user_id="u1",
        status="pending",
        max_retry=3,
    )
    db_session.add(job)
    db_session.commit()

    first_row = claim_summary_job(
        db_session,
        job.id,
        owner="same-worker",
        lease_seconds=30,
        now=_now(),
    )
    assert first_row is not None
    first = session_summary_job_lease(first_row)
    assert recover_stale_running_jobs(
        db_session,
        now=first.expires_at,
    ) == 1

    second_row = claim_summary_job(
        db_session,
        job.id,
        owner="same-worker",
        lease_seconds=30,
        now=first.expires_at,
    )
    assert second_row is not None
    second = session_summary_job_lease(second_row)

    assert fail_claimed_session_summary_job(
        db_session,
        lease=first,
        error="stale_worker_must_not_settle",
        retryable=False,
        now=first.expires_at + timedelta(seconds=1),
    ) is False
    db_session.expire_all()
    current = db_session.get(SessionSummaryJob, job.id)
    assert current.status == "running"
    assert current.lease_token == second.owner_token

    assert fail_claimed_session_summary_job(
        db_session,
        lease=second,
        error="current_worker_can_settle",
        retryable=False,
        now=first.expires_at + timedelta(seconds=1),
    ) is True
    db_session.expire_all()
    current = db_session.get(SessionSummaryJob, job.id)
    assert current.status == "failed"
    assert current.lease_token == ""
    assert current.finished_at is not None


def test_session_summary_migration_adds_kernel_fencing_columns():
    from sqlalchemy import create_engine, inspect

    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    run_schema_migrations(engine)

    columns = {
        column["name"]
        for column in inspect(engine).get_columns(
            "session_summary_jobs"
        )
    }
    assert {
        "lease_token",
        "lease_expires_at",
        "generation",
        "attempt_count",
        "finished_at",
    } <= columns


def test_production_job_lease_adapters_cover_existing_state_machines():
    from datetime import datetime

    from app.memory_digest.jobs import MemoryDigestJobClaim
    from app.session_memory.jobs import SessionSummaryJobLease
    from bootstrap.job_adapters import build_job_lease_adapter_registry
    from core.db.group_learning_schedule_contracts import (
        GroupLearningScheduleClaim,
    )
    from core.jobs import JobLease
    from core.outbound.contracts import DeliveryClaimHandle
    from core.sandbox.admin_operations import ClaimedSandboxOperation
    from core.semantic.jobs import SemanticJobLease

    expires_at = datetime(2026, 7, 23, 13, 0, 0)
    registry = build_job_lease_adapter_registry()
    sources = {
        "group_memory_learning": GroupLearningScheduleClaim(
            chat_stream_id="qq:42:group",
            aspects=(
                "topics",
                "expressions",
                "slang",
                "style",
            ),
            interval_minutes=1440,
            window_hours=24,
            config_generation=1,
            lease=JobLease(
                job_id="group-learning-42",
                worker_id="group-learning-worker",
                owner_token="0" * 64,
                generation=1,
                attempt_no=1,
                expires_at=expires_at,
            ),
        ),
        "session_summary": SessionSummaryJobLease(
            job_id=11,
            worker_id="session-worker",
            owner_token="a" * 64,
            generation=3,
            attempt_no=4,
            expires_at=expires_at,
            stable_hash="b" * 64,
        ),
        "memory_digest": MemoryDigestJobClaim(
            decision="claimed",
            job_id=12,
            lease_token="c" * 64,
            worker_id="memory-worker",
            lease_expires_at=expires_at,
            attempt_count=2,
            source_revision="d" * 64,
        ),
        "semantic_index": SemanticJobLease(
            job_id=13,
            worker_id="semantic-worker",
            lease_token="e" * 64,
            lease_expires_at=expires_at,
            source_revision="f" * 64,
            attempt_count=5,
        ),
        "sandbox_admin_operation": ClaimedSandboxOperation(
            operation_id="operation-14",
            operation_type="set_quota",
            chat_stream_id="",
            workspace_id="workspace-1",
            desired_capability="",
            previous_capability="",
            desired_quota_bytes=1024,
            expected_grant_version=None,
            expected_quota_generation=7,
            request_id="request-14",
            attempt_count=6,
            max_attempts=8,
            worker_id="sandbox-worker",
            lease_token="1" * 64,
            lease_expires_at=expires_at,
        ),
        "outbound_delivery": DeliveryClaimHandle(
            outbox_id=15,
            run_id=20,
            attempt_id=21,
            attempt_no=7,
            worker_owner="outbound-worker",
            lease_token="2" * 64,
            lease_expires_at=expires_at,
            endpoint_key="qq_push",
            target_type="private",
            endpoint_config_revision="revision-1",
            destination_snapshot_json="{}",
            payload_json="{}",
            payload_sha256="3" * 64,
            payload_contract_fingerprint="contract-1",
        ),
    }

    assert registry.job_types() == tuple(sources)
    projected = {
        job_type: registry.require(job_type).project_lease(source)
        for job_type, source in sources.items()
    }
    assert projected["session_summary"].generation == 3
    assert projected["session_summary"].attempt_no == 4
    assert projected["memory_digest"].worker_id == "memory-worker"
    assert projected["memory_digest"].generation == 2
    assert projected["semantic_index"].owner_token == "e" * 64
    assert projected["semantic_index"].attempt_no == 5
    assert projected["sandbox_admin_operation"].job_id == "operation-14"
    assert projected["sandbox_admin_operation"].generation == 6
    assert projected["outbound_delivery"].job_id == "15"
    assert projected["outbound_delivery"].attempt_no == 7
