"""主动出站人工重放与 circuit 重置适配器。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.db.models.outbound import (
    OutboundDeliveryCircuit,
    OutboundDeliveryOutbox,
    OutboundRun,
)
from core.outbound.contracts import (
    CircuitResetResult,
    OutboundConflictError,
    OutboundSafetyError,
    ReplayResult,
)
from core.outbound.control import _control, _locked_current, _open_circuit_row
from core.outbound.delivery_claims import _outbox_circuit_facts
from core.outbound.policy import (
    audit_datetime as _audit_datetime,
    audit_request_sha256 as _audit_request_sha256,
    fingerprint as _fingerprint,
    require_text as _text,
    utc_naive as _utc_naive,
)


_CIRCUIT_SCOPE_TYPES = frozenset({"endpoint", "destination", "payload_contract"})


def _replay_request_sha256(
    *,
    parent: OutboundDeliveryOutbox,
    manual_request_key: str,
    reason: str,
    max_attempts: int,
    retry_deadline_at: datetime,
    endpoint_config_revision: str,
) -> str:
    return _audit_request_sha256(
        "manual_replay",
        {
            "parent_outbox_id": int(parent.id),
            "parent_replay_sequence": int(parent.replay_sequence),
            "manual_request_key": manual_request_key,
            "reason": reason,
            "max_attempts": max_attempts,
            "retry_deadline_at": _audit_datetime(retry_deadline_at),
            "endpoint_config_revision": endpoint_config_revision,
            "destination_snapshot_json": str(parent.destination_snapshot_json),
            "destination_fingerprint": str(parent.destination_fingerprint),
            "target_type": str(parent.target_type),
            "endpoint_key": str(parent.endpoint_key),
            "payload_json": str(parent.payload_json),
            "payload_sha256": str(parent.payload_sha256),
            "payload_contract_fingerprint": str(
                parent.payload_contract_fingerprint
            ),
            "cutover_epoch": int(parent.cutover_epoch),
        },
    )


def create_delivery_replay(
    db: Session,
    *,
    parent_outbox_id: int,
    manual_request_key: str,
    confirm_duplicate_risk: bool,
    reason: str,
    max_attempts: int,
    retry_deadline_at: datetime,
    endpoint_config_revision: str,
    now: datetime | None = None,
) -> ReplayResult:
    if confirm_duplicate_risk is not True:
        raise OutboundSafetyError("ambiguous replay 必须显式确认重复投递风险")
    request_key = _text(
        manual_request_key,
        name="manual_request_key",
        max_length=255,
    )
    normalized_reason = _text(reason, name="reason", max_length=1000)
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts 必须是正整数")
    parent_probe = db.get(OutboundDeliveryOutbox, int(parent_outbox_id))
    run_probe = (
        db.get(OutboundRun, int(parent_probe.run_id))
        if parent_probe is not None
        else None
    )
    if parent_probe is None or run_probe is None:
        raise OutboundSafetyError("replay parent 缺少 outbox 或 run")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    deadline = _utc_naive(retry_deadline_at)
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    parent = db.get(OutboundDeliveryOutbox, int(parent_outbox_id))
    if parent is None or parent.status != "ambiguous":
        raise OutboundSafetyError("只有 active ambiguous leaf 可以 replay")
    run = db.get(OutboundRun, int(parent.run_id))
    if run is None:
        raise OutboundSafetyError("replay parent 缺少 run")
    replay_key = _fingerprint(
        "manual_replay",
        str(parent.id),
        request_key,
    )
    replay_request_sha256 = _replay_request_sha256(
        parent=parent,
        manual_request_key=request_key,
        reason=normalized_reason,
        max_attempts=max_attempts,
        retry_deadline_at=deadline,
        endpoint_config_revision=revision,
    )
    existing = (
        db.query(OutboundDeliveryOutbox)
        .filter(OutboundDeliveryOutbox.idempotency_key == replay_key)
        .first()
    )
    if existing is not None:
        expected_sequence = int(parent.replay_sequence) + 1
        immutable_facts_match = (
            existing.replay_of_outbox_id == parent.id
            and int(existing.run_id) == int(run.id)
            and int(existing.replay_sequence) == expected_sequence
            and existing.destination_snapshot_json == parent.destination_snapshot_json
            and existing.destination_fingerprint == parent.destination_fingerprint
            and existing.target_type == parent.target_type
            and existing.endpoint_key == parent.endpoint_key
            and existing.payload_json == parent.payload_json
            and existing.payload_sha256 == parent.payload_sha256
            and int(existing.max_attempts) == int(max_attempts)
            and existing.retry_deadline_at == deadline
            and existing.endpoint_config_revision == revision
            and existing.payload_contract_fingerprint
            == parent.payload_contract_fingerprint
            and existing.replay_request_sha256 == replay_request_sha256
        )
        if not immutable_facts_match:
            raise OutboundConflictError("同一 manual replay key 的不可变事实不一致")
        return ReplayResult(
            outbox_id=int(existing.id),
            run_id=int(existing.run_id),
            replay_sequence=int(existing.replay_sequence),
            created=False,
        )
    if deadline <= current:
        raise ValueError("retry_deadline_at 必须晚于 now")
    if run.active_outbox_id != parent.id:
        raise OutboundConflictError("parent 已不是 active replay leaf")
    control = _control(db, str(run.source_type))
    if (
        control.mode != "outbox_active"
        or int(control.cutover_epoch) != int(run.cutover_epoch)
        or int(parent.cutover_epoch) != int(run.cutover_epoch)
    ):
        raise OutboundSafetyError("当前 cutover control 禁止 replay")
    if _open_circuit_row(
        db,
        _outbox_circuit_facts(parent, actual_config_revision=revision),
    ) is not None:
        raise OutboundSafetyError("适用 circuit 打开时不能 replay")

    sequence = int(parent.replay_sequence) + 1
    insert_result = db.execute(
        sqlite_insert(OutboundDeliveryOutbox)
        .values(
            run_id=int(run.id),
            idempotency_key=replay_key,
            destination_snapshot_json=str(parent.destination_snapshot_json),
            destination_fingerprint=str(parent.destination_fingerprint),
            target_type=str(parent.target_type),
            endpoint_key=str(parent.endpoint_key),
            payload_json=str(parent.payload_json),
            payload_sha256=str(parent.payload_sha256),
            status="pending",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            next_attempt_at=None,
            allocated_attempt_count=0,
            request_started_count=0,
            max_attempts=max_attempts,
            retry_deadline_at=deadline,
            last_error_type="manual_replay",
            last_error_summary=normalized_reason,
            delivered_at=None,
            cancelled_at=None,
            cancel_reason_type=None,
            replay_of_outbox_id=int(parent.id),
            replay_sequence=sequence,
            replay_request_sha256=replay_request_sha256,
            cutover_epoch=int(parent.cutover_epoch),
            endpoint_config_revision=revision,
            payload_contract_fingerprint=str(
                parent.payload_contract_fingerprint
            ),
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_nothing()
    )
    child = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.run_id == int(run.id),
            OutboundDeliveryOutbox.destination_fingerprint
            == parent.destination_fingerprint,
            OutboundDeliveryOutbox.replay_sequence == sequence,
        )
        .first()
    )
    if child is None:
        raise RuntimeError("原子创建 replay 后未找到 child")
    if child.idempotency_key != replay_key:
        raise OutboundConflictError("active leaf 已由其他 replay 请求推进")
    created = insert_result.rowcount == 1
    if created:
        updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.active_outbox_id == int(parent.id),
                OutboundRun.status == "ambiguous",
                OutboundRun.cutover_epoch == int(parent.cutover_epoch),
            )
            .update(
                {
                    OutboundRun.active_outbox_id: int(child.id),
                    OutboundRun.status: "queued",
                    OutboundRun.has_ambiguous_ancestor: True,
                    OutboundRun.succeeded_at: None,
                    OutboundRun.failure_type: "",
                    OutboundRun.failure_summary: "",
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            raise OutboundConflictError("active replay leaf CAS 失败")
    db.flush()
    result = ReplayResult(
        outbox_id=int(child.id),
        run_id=int(run.id),
        replay_sequence=sequence,
        created=created,
    )
    db.expire_all()
    return result


def reset_delivery_circuit(
    db: Session,
    *,
    scope_type: str,
    scope_fingerprint: str,
    config_revision: str,
    expected_updated_at: datetime,
    now: datetime | None = None,
) -> CircuitResetResult:
    scope = _text(scope_type, name="scope_type", max_length=32)
    if scope not in _CIRCUIT_SCOPE_TYPES:
        raise ValueError("scope_type 非法")
    fingerprint = _text(
        scope_fingerprint,
        name="scope_fingerprint",
        max_length=64,
    )
    revision = _text(config_revision, name="config_revision", max_length=128)
    expected = _utc_naive(expected_updated_at)
    current = _utc_naive(now)
    row = (
        db.query(OutboundDeliveryCircuit)
        .filter(
            OutboundDeliveryCircuit.scope_type == scope,
            OutboundDeliveryCircuit.scope_fingerprint == fingerprint,
            OutboundDeliveryCircuit.config_revision == revision,
        )
        .first()
    )
    if row is None:
        return CircuitResetResult(applied=False, circuit_id=None, status="missing")
    updated = (
        db.query(OutboundDeliveryCircuit)
        .filter(
            OutboundDeliveryCircuit.id == int(row.id),
            OutboundDeliveryCircuit.status == "open",
            OutboundDeliveryCircuit.updated_at == expected,
        )
        .update(
            {
                OutboundDeliveryCircuit.status: "closed",
                OutboundDeliveryCircuit.reason_type: "",
                OutboundDeliveryCircuit.opened_at: None,
                OutboundDeliveryCircuit.opened_by_attempt_id: None,
                OutboundDeliveryCircuit.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    result = CircuitResetResult(
        applied=updated == 1,
        circuit_id=int(row.id),
        status="closed" if updated == 1 else str(row.status),
    )
    db.expire_all()
    return result



__all__ = ["create_delivery_replay", "reset_delivery_circuit"]
