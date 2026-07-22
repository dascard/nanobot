"""通用主动出站的同步事务状态机。"""

from __future__ import annotations

from core.db.models.outbound import (
    OutboundDeliveryAttempt as OutboundDeliveryAttempt,
    OutboundDeliveryCircuit as OutboundDeliveryCircuit,
    OutboundDeliveryControl as OutboundDeliveryControl,
    OutboundDeliveryOutbox as OutboundDeliveryOutbox,
    OutboundRun as OutboundRun,
)
from core.outbound.contracts import (
    OUTBOUND_PROTOCOL_VERSION,
    PROACTIVE_GENERATED_TASK_KIND,
    PROACTIVE_GENERATION_KINDS,
    PROACTIVE_GENERATION_METADATA_KEY,
    PROACTIVE_PREPARED_TASK_KIND,
    CircuitResetResult as CircuitResetResult,
    ControlTransitionResult as ControlTransitionResult,
    DeliveryClaimHandle as DeliveryClaimHandle,
    DeliverySettlementResult as DeliverySettlementResult,
    GenerationAttemptHandle as GenerationAttemptHandle,
    InvalidOutboundTransitionError as InvalidOutboundTransitionError,
    LeaseExpirySummary as LeaseExpirySummary,
    LegacyOutreachResolutionResult as LegacyOutreachResolutionResult,
    OutboundConflictError as OutboundConflictError,
    OutboundDeliveryError,
    OutboundFencingError as OutboundFencingError,
    OutboundGenerationGate as OutboundGenerationGate,
    OutboundSafetyError as OutboundSafetyError,
    OutboxCancellationResult as OutboxCancellationResult,
    OutboxCommitResult as OutboxCommitResult,
    ReplayResult as ReplayResult,
    RequestStartResult as RequestStartResult,
    RunClaimDecision as RunClaimDecision,
    RunClaimRenewal as RunClaimRenewal,
    SourceCancellationSummary as SourceCancellationSummary,
    WriterLeaseDecision as WriterLeaseDecision,
    WriterReleaseResult as WriterReleaseResult,
)
from core.outbound.control import (
    acquire_or_renew_delivery_writer as acquire_or_renew_delivery_writer,
    check_outbound_generation_gate as check_outbound_generation_gate,
    lock_outbound_source_control as lock_outbound_source_control,
    release_delivery_writer as release_delivery_writer,
)
from core.outbound.control_transitions import (
    transition_delivery_control as transition_delivery_control,
)
from core.outbound.run_claims import (
    claim_outbound_run as claim_outbound_run,
    quarantine_expired_generation_run as quarantine_expired_generation_run,
    renew_outbound_run_claim as renew_outbound_run_claim,
    start_generation_attempt as start_generation_attempt,
)
from core.outbound.delivery_claims import (
    cancel_delivery_before_send as cancel_delivery_before_send,
    cancel_invalid_delivery_before_send as cancel_invalid_delivery_before_send,
    cancel_safe_deliveries_for_source as cancel_safe_deliveries_for_source,
    cancel_safe_outbox as cancel_safe_outbox,
    claim_due_outbox as claim_due_outbox,
    claim_legacy_direct_outbox as claim_legacy_direct_outbox,
    mark_delivery_request_started as mark_delivery_request_started,
    resolve_legacy_ambiguous_outreach as resolve_legacy_ambiguous_outreach,
    terminalize_expired_outboxes as terminalize_expired_outboxes,
)
from core.outbound.replay import (
    create_delivery_replay as create_delivery_replay,
    reset_delivery_circuit as reset_delivery_circuit,
)
from core.outbound.settlement import (
    expire_stale_delivery_leases as expire_stale_delivery_leases,
    settle_delivery_attempt as settle_delivery_attempt,
)
from core.outbound.generation import (
    commit_generated_outbox as commit_generated_outbox,
    commit_prepared_outbox as commit_prepared_outbox,
    fail_outbound_generation as fail_outbound_generation,
)
from core.outbound.policy import (
    destination_circuit_fingerprint as destination_circuit_fingerprint,
    endpoint_circuit_fingerprint as endpoint_circuit_fingerprint,
    payload_contract_circuit_fingerprint as payload_contract_circuit_fingerprint,
    prepare_proactive_generation_grounding,
    proactive_generation_metadata,
    proactive_outreach_delivery_key,
    proactive_outreach_destination_fingerprint,
    proactive_outreach_generated_source_revision,
    proactive_outreach_generated_source_snapshot,
    proactive_outreach_occurrence_key,
    proactive_outreach_source_revision,
    proactive_outreach_source_snapshot,
    utc_naive,
)
from core.outbound.projection import proactive_outreach_linkage_is_current


# 兼容旧测试和管理路由对私有时钟函数的定点替换；新代码应直接依赖 policy。
_utc_naive = utc_naive


__all__ = [
    "OUTBOUND_PROTOCOL_VERSION",
    "PROACTIVE_GENERATED_TASK_KIND",
    "PROACTIVE_GENERATION_KINDS",
    "PROACTIVE_GENERATION_METADATA_KEY",
    "PROACTIVE_PREPARED_TASK_KIND",
    "OutboundDeliveryError",
    "prepare_proactive_generation_grounding",
    "proactive_generation_metadata",
    "proactive_outreach_delivery_key",
    "proactive_outreach_destination_fingerprint",
    "proactive_outreach_generated_source_revision",
    "proactive_outreach_generated_source_snapshot",
    "proactive_outreach_linkage_is_current",
    "proactive_outreach_occurrence_key",
    "proactive_outreach_source_revision",
    "proactive_outreach_source_snapshot",
]
