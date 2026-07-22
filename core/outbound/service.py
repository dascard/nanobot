"""主动出站状态机的稳定应用入口。

调用方从这里使用事务级命令；具体 SQLAlchemy 实现按职责分布在相邻适配器模块。
旧 ``core.outbound_delivery`` 仅用于兼容既有导入。
"""

from core.outbound.contracts import (
    OUTBOUND_PROTOCOL_VERSION,
    InvalidOutboundTransitionError,
    OutboundConflictError,
    OutboundFencingError,
    OutboundSafetyError,
)
from core.outbound.control import (
    acquire_or_renew_delivery_writer,
    check_outbound_generation_gate,
    lock_outbound_source_control,
    release_delivery_writer,
)
from core.outbound.control_transitions import transition_delivery_control
from core.outbound.delivery_claims import (
    cancel_delivery_before_send,
    cancel_invalid_delivery_before_send,
    cancel_safe_deliveries_for_source,
    cancel_safe_outbox,
    claim_due_outbox,
    claim_legacy_direct_outbox,
    mark_delivery_request_started,
    resolve_legacy_ambiguous_outreach,
    terminalize_expired_outboxes,
)
from core.outbound.generation import (
    commit_generated_outbox,
    commit_prepared_outbox,
    fail_outbound_generation,
)
from core.outbound.replay import create_delivery_replay, reset_delivery_circuit
from core.outbound.run_claims import (
    claim_outbound_run,
    quarantine_expired_generation_run,
    renew_outbound_run_claim,
    start_generation_attempt,
)
from core.outbound.settlement import (
    expire_stale_delivery_leases,
    settle_delivery_attempt,
)


__all__ = [
    "OUTBOUND_PROTOCOL_VERSION",
    "InvalidOutboundTransitionError",
    "OutboundConflictError",
    "OutboundFencingError",
    "OutboundSafetyError",
    "acquire_or_renew_delivery_writer",
    "cancel_delivery_before_send",
    "cancel_invalid_delivery_before_send",
    "cancel_safe_deliveries_for_source",
    "cancel_safe_outbox",
    "check_outbound_generation_gate",
    "claim_due_outbox",
    "claim_legacy_direct_outbox",
    "claim_outbound_run",
    "commit_generated_outbox",
    "commit_prepared_outbox",
    "create_delivery_replay",
    "expire_stale_delivery_leases",
    "fail_outbound_generation",
    "lock_outbound_source_control",
    "mark_delivery_request_started",
    "quarantine_expired_generation_run",
    "release_delivery_writer",
    "renew_outbound_run_claim",
    "reset_delivery_circuit",
    "resolve_legacy_ambiguous_outreach",
    "settle_delivery_attempt",
    "start_generation_attempt",
    "terminalize_expired_outboxes",
    "transition_delivery_control",
]
