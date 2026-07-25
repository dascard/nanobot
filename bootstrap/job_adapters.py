"""生产领域任务租约到 Durable Job 合同的 Composition Root 绑定。"""

from __future__ import annotations

from app.memory_digest.jobs import MemoryDigestJobClaim
from app.session_memory.jobs import SessionSummaryJobLease
from core.db.group_learning_schedule_contracts import (
    GroupLearningScheduleClaim,
)
from core.jobs import JobLease
from core.jobs.adapters import (
    JobLeaseAdapterBinding,
    JobLeaseAdapterDescriptor,
    JobLeaseAdapterRegistry,
)
from core.outbound.contracts import DeliveryClaimHandle
from core.sandbox.admin_operations import ClaimedSandboxOperation
from core.semantic.jobs import SemanticJobLease


def _session_summary(source: object) -> JobLease:
    lease = source
    if not isinstance(lease, SessionSummaryJobLease):
        raise TypeError("Session Summary 租约类型无效")
    return JobLease(
        job_id=str(lease.job_id),
        worker_id=lease.worker_id,
        owner_token=lease.owner_token,
        generation=lease.generation,
        attempt_no=lease.attempt_no,
        expires_at=lease.expires_at,
    )


def _group_learning(source: object) -> JobLease:
    claim = source
    if not isinstance(claim, GroupLearningScheduleClaim):
        raise TypeError("Group Learning 租约类型无效")
    return claim.lease


def _memory_digest(source: object) -> JobLease:
    claim = source
    if not isinstance(claim, MemoryDigestJobClaim):
        raise TypeError("Memory Digest 租约类型无效")
    if claim.decision != "claimed" or claim.lease_expires_at is None:
        raise ValueError("Memory Digest claim 不含活动租约")
    return JobLease(
        job_id=str(claim.job_id),
        worker_id=claim.worker_id,
        owner_token=claim.lease_token,
        generation=claim.attempt_count,
        attempt_no=claim.attempt_count,
        expires_at=claim.lease_expires_at,
    )


def _semantic_index(source: object) -> JobLease:
    lease = source
    if not isinstance(lease, SemanticJobLease):
        raise TypeError("Semantic Index 租约类型无效")
    return JobLease(
        job_id=str(lease.job_id),
        worker_id=lease.worker_id,
        owner_token=lease.lease_token,
        generation=lease.attempt_count,
        attempt_no=lease.attempt_count,
        expires_at=lease.lease_expires_at,
    )


def _sandbox_admin(source: object) -> JobLease:
    claim = source
    if not isinstance(claim, ClaimedSandboxOperation):
        raise TypeError("Sandbox Admin 租约类型无效")
    return JobLease(
        job_id=claim.operation_id,
        worker_id=claim.worker_id,
        owner_token=claim.lease_token,
        generation=claim.attempt_count,
        attempt_no=claim.attempt_count,
        expires_at=claim.lease_expires_at,
    )


def _outbound_delivery(source: object) -> JobLease:
    claim = source
    if not isinstance(claim, DeliveryClaimHandle):
        raise TypeError("Outbound Delivery 租约类型无效")
    return JobLease(
        job_id=str(claim.outbox_id),
        worker_id=claim.worker_owner,
        owner_token=claim.lease_token,
        generation=claim.attempt_no,
        attempt_no=claim.attempt_no,
        expires_at=claim.lease_expires_at,
    )


def build_job_lease_adapter_registry() -> JobLeaseAdapterRegistry:
    """显式绑定生产 Adapter；禁止目录扫描和动态 import。"""

    return JobLeaseAdapterRegistry((
        JobLeaseAdapterBinding(
            descriptor=JobLeaseAdapterDescriptor(
                job_type="group_memory_learning",
                owner_module="app.group_learning",
                source_type_name=(
                    "core.db.group_learning_schedule_contracts."
                    "GroupLearningScheduleClaim"
                ),
                projector_id="group_learning.schedule_claim.v1",
            ),
            source_type=GroupLearningScheduleClaim,
            _projector=_group_learning,
        ),
        JobLeaseAdapterBinding(
            descriptor=JobLeaseAdapterDescriptor(
                job_type="session_summary",
                owner_module="app.session_memory",
                source_type_name=(
                    "app.session_memory.jobs.SessionSummaryJobLease"
                ),
                projector_id="session_summary.lease.v1",
            ),
            source_type=SessionSummaryJobLease,
            _projector=_session_summary,
        ),
        JobLeaseAdapterBinding(
            descriptor=JobLeaseAdapterDescriptor(
                job_type="memory_digest",
                owner_module="app.memory_digest",
                source_type_name=(
                    "app.memory_digest.jobs.MemoryDigestJobClaim"
                ),
                projector_id="memory_digest.claim.v1",
                depends_on=("session_summary",),
            ),
            source_type=MemoryDigestJobClaim,
            _projector=_memory_digest,
        ),
        JobLeaseAdapterBinding(
            descriptor=JobLeaseAdapterDescriptor(
                job_type="semantic_index",
                owner_module="core.semantic",
                source_type_name="core.semantic.jobs.SemanticJobLease",
                projector_id="semantic_index.lease.v1",
                depends_on=("memory_digest",),
            ),
            source_type=SemanticJobLease,
            _projector=_semantic_index,
        ),
        JobLeaseAdapterBinding(
            descriptor=JobLeaseAdapterDescriptor(
                job_type="sandbox_admin_operation",
                owner_module="core.sandbox",
                source_type_name=(
                    "core.sandbox.admin_operations."
                    "ClaimedSandboxOperation"
                ),
                projector_id="sandbox_admin.claim.v1",
                depends_on=("semantic_index",),
            ),
            source_type=ClaimedSandboxOperation,
            _projector=_sandbox_admin,
        ),
        JobLeaseAdapterBinding(
            descriptor=JobLeaseAdapterDescriptor(
                job_type="outbound_delivery",
                owner_module="core.outbound",
                source_type_name=(
                    "core.outbound.contracts.DeliveryClaimHandle"
                ),
                projector_id="outbound_delivery.claim.v1",
                depends_on=("sandbox_admin_operation",),
            ),
            source_type=DeliveryClaimHandle,
            _projector=_outbound_delivery,
        ),
    ))


__all__ = ["build_job_lease_adapter_registry"]
