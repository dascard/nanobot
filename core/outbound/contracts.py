"""主动出站领域的框架无关合同。

此模块只描述公开错误、协议常量和返回值，不依赖 SQLAlchemy、HTTP 客户端或
具体 worker。状态机实现可以继续演进，调用方只依赖这里的稳定语义。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


OUTBOUND_PROTOCOL_VERSION = 2
PROACTIVE_PREPARED_TASK_KIND = "proactive_outreach_prepared"
PROACTIVE_GENERATED_TASK_KIND = "proactive_outreach_generated"
PROACTIVE_GENERATION_METADATA_KEY = "_outbound_generation"
PROACTIVE_GENERATION_KINDS = frozenset({"message", "research", "forced"})


class OutboundDeliveryError(RuntimeError):
    """主动出站状态机错误基类。"""


class OutboundConflictError(OutboundDeliveryError):
    """幂等身份相同但不可变事实不同。"""


class OutboundFencingError(OutboundDeliveryError):
    """owner、token、租约或活动叶已经失效。"""


class OutboundSafetyError(OutboundDeliveryError):
    """当前 circuit、control 或风险状态禁止继续。"""


class InvalidOutboundTransitionError(OutboundDeliveryError):
    """请求了未定义的状态转换。"""


@dataclass(frozen=True, slots=True)
class WriterLeaseDecision:
    acquired: bool
    source_type: str
    owner: str
    token: str
    protocol_version: int
    writer_version: int
    lease_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class WriterReleaseResult:
    applied: bool
    source_type: str
    writer_version: int


@dataclass(frozen=True, slots=True)
class RunClaimDecision:
    acquired: bool
    run_id: int
    status: str
    owner: str
    claim_token: str
    claim_expires_at: datetime | None
    generation: int
    attempt_no: int
    delivery_mode: str
    cutover_epoch: int
    source_snapshot_json: str
    source_snapshot_sha256: str
    delivery_contract_json: str
    delivery_contract_sha256: str


@dataclass(frozen=True, slots=True)
class RunClaimRenewal:
    applied: bool
    run_id: int
    claim_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class GenerationAttemptHandle:
    run_id: int
    attempt_id: int | None
    attempt_no: int | None
    owner: str
    fencing_token: str
    status: str
    reason_type: str


@dataclass(frozen=True, slots=True)
class OutboxCommitResult:
    outbox_id: int | None
    run_id: int
    created: bool
    payload_sha256: str
    status: str
    reason_type: str


@dataclass(frozen=True, slots=True)
class DeliveryClaimHandle:
    outbox_id: int
    run_id: int
    attempt_id: int
    attempt_no: int
    worker_owner: str
    lease_token: str
    lease_expires_at: datetime
    endpoint_key: str
    target_type: str
    endpoint_config_revision: str
    destination_snapshot_json: str
    payload_json: str
    payload_sha256: str
    payload_contract_fingerprint: str


@dataclass(frozen=True, slots=True)
class RequestStartResult:
    applied: bool
    outbox_id: int
    attempt_id: int
    request_started_count: int


@dataclass(frozen=True, slots=True)
class DeliverySettlementResult:
    applied: bool
    outbox_id: int
    attempt_id: int
    outbox_status: str
    run_status: str


@dataclass(frozen=True, slots=True)
class LeaseExpirySummary:
    abandoned_before_send: int
    ambiguous: int

    @property
    def total(self) -> int:
        return self.abandoned_before_send + self.ambiguous


@dataclass(frozen=True, slots=True)
class SourceCancellationSummary:
    cancelled: int
    unsafe: int


@dataclass(frozen=True, slots=True)
class ReplayResult:
    outbox_id: int
    run_id: int
    replay_sequence: int
    created: bool


@dataclass(frozen=True, slots=True)
class CircuitResetResult:
    applied: bool
    circuit_id: int | None
    status: str


@dataclass(frozen=True, slots=True)
class ControlTransitionResult:
    applied: bool
    source_type: str
    mode: str
    cutover_epoch: int
    writer_version: int
    effective_from: datetime


@dataclass(frozen=True, slots=True)
class OutboxCancellationResult:
    applied: bool
    outbox_id: int
    run_id: int
    status: str


@dataclass(frozen=True, slots=True)
class LegacyOutreachResolutionResult:
    applied: bool
    outreach_log_id: int
    status: str


@dataclass(frozen=True, slots=True)
class OutboundGenerationGate:
    allowed: bool
    delivery_mode: str
    cutover_epoch: int
    reason_type: str
    reason_summary: str


__all__ = [
    "OUTBOUND_PROTOCOL_VERSION",
    "PROACTIVE_GENERATED_TASK_KIND",
    "PROACTIVE_GENERATION_KINDS",
    "PROACTIVE_GENERATION_METADATA_KEY",
    "PROACTIVE_PREPARED_TASK_KIND",
    "CircuitResetResult",
    "ControlTransitionResult",
    "DeliveryClaimHandle",
    "DeliverySettlementResult",
    "GenerationAttemptHandle",
    "InvalidOutboundTransitionError",
    "LeaseExpirySummary",
    "LegacyOutreachResolutionResult",
    "OutboundConflictError",
    "OutboundDeliveryError",
    "OutboundFencingError",
    "OutboundGenerationGate",
    "OutboundSafetyError",
    "OutboxCancellationResult",
    "OutboxCommitResult",
    "ReplayResult",
    "RequestStartResult",
    "RunClaimDecision",
    "RunClaimRenewal",
    "SourceCancellationSummary",
    "WriterLeaseDecision",
    "WriterReleaseResult",
]
