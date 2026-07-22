"""主动出站 delivery control 状态迁移适配器。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.db.models.outbound import (
    OutboundDeliveryAttempt,
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundRun,
)
from core.outbound.contracts import (
    OUTBOUND_PROTOCOL_VERSION,
    ControlTransitionResult,
    InvalidOutboundTransitionError,
    OutboundFencingError,
    OutboundSafetyError,
)
from core.outbound.control import _locked_current, _require_writer
from core.outbound.policy import (
    require_positive_seconds as _positive_seconds,
    require_text as _text,
    utc_naive as _utc_naive,
)


_CONTROL_TRANSITIONS = {
    ("legacy_direct", "outbox_hold"),
    ("outbox_hold", "outbox_active"),
    ("outbox_active", "outbox_draining"),
    ("outbox_draining", "legacy_direct"),
}


def _has_unsafe_legacy_run(db: Session, *, source_type: str) -> bool:
    unsafe_run = (
        db.query(OutboundRun.id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.delivery_mode == "legacy_direct",
            OutboundRun.status.in_((
                "claimed",
                "generating",
                "queued",
                "delivering",
                "blocked",
                "ambiguous",
            )),
        )
        .first()
    )
    if unsafe_run is not None:
        return True
    unsafe_leaf = (
        db.query(OutboundDeliveryOutbox.id)
        .join(OutboundRun, OutboundRun.id == OutboundDeliveryOutbox.run_id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.delivery_mode == "legacy_direct",
            OutboundRun.active_outbox_id == OutboundDeliveryOutbox.id,
            OutboundDeliveryOutbox.status.in_((
                "pending",
                "retry_wait",
                "leased",
                "blocked",
                "ambiguous",
            )),
        )
        .first()
    )
    if unsafe_leaf is not None:
        return True
    return (
        db.query(OutboundDeliveryAttempt.id)
        .join(
            OutboundDeliveryOutbox,
            OutboundDeliveryOutbox.id == OutboundDeliveryAttempt.outbox_id,
        )
        .join(OutboundRun, OutboundRun.id == OutboundDeliveryOutbox.run_id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.delivery_mode == "legacy_direct",
            OutboundDeliveryAttempt.status == "started",
        )
        .first()
        is not None
    )


def _has_unsafe_unmaterialized_outbox_run(
    db: Session,
    *,
    source_type: str,
) -> bool:
    return (
        db.query(OutboundRun.id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.delivery_mode == "outbox",
            OutboundRun.active_outbox_id.is_(None),
            OutboundRun.status.in_((
                "claimed",
                "generating",
                "delivering",
                "blocked",
                "ambiguous",
            )),
        )
        .first()
        is not None
    )


def _unsafe_for_control_transition(
    db: Session,
    *,
    source_type: str,
    epoch: int,
    include_legacy_runs: bool,
) -> bool:
    unsafe_generation = (
        db.query(OutboundRun.id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.cutover_epoch == int(epoch),
            OutboundRun.active_outbox_id.is_(None),
            OutboundRun.status.in_((
                "claimed",
                "generating",
                "delivering",
                "blocked",
                "ambiguous",
            )),
        )
        .first()
    )
    if unsafe_generation is not None:
        return True
    if include_legacy_runs and _has_unsafe_legacy_run(
        db,
        source_type=source_type,
    ):
        return True
    unsafe_outbox = (
        db.query(OutboundDeliveryOutbox.id)
        .join(OutboundRun, OutboundRun.id == OutboundDeliveryOutbox.run_id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundDeliveryOutbox.cutover_epoch == int(epoch),
            or_(
                OutboundDeliveryOutbox.status.in_((
                    "pending",
                    "retry_wait",
                    "leased",
                    "blocked",
                )),
                (
                    (OutboundDeliveryOutbox.status == "ambiguous")
                    & (
                        OutboundRun.active_outbox_id
                        == OutboundDeliveryOutbox.id
                    )
                ),
            ),
        )
        .first()
    )
    if unsafe_outbox is not None:
        return True
    return (
        db.query(OutboundDeliveryAttempt.id)
        .join(
            OutboundDeliveryOutbox,
            OutboundDeliveryOutbox.id == OutboundDeliveryAttempt.outbox_id,
        )
        .join(OutboundRun, OutboundRun.id == OutboundDeliveryOutbox.run_id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundDeliveryOutbox.cutover_epoch == int(epoch),
            OutboundDeliveryAttempt.status == "started",
        )
        .first()
        is not None
    )


def transition_delivery_control(
    db: Session,
    *,
    source_type: str,
    expected_mode: str,
    new_mode: str,
    expected_writer_version: int,
    actor_owner: str,
    actor_token: str,
    protocol_version: int,
    effective_from: datetime,
    writer_lease_seconds: int | float,
    now: datetime | None = None,
) -> ControlTransitionResult:
    source = _text(source_type, name="source_type", max_length=32)
    old_mode = _text(expected_mode, name="expected_mode", max_length=24)
    target_mode = _text(new_mode, name="new_mode", max_length=24)
    if (old_mode, target_mode) not in _CONTROL_TRANSITIONS:
        raise InvalidOutboundTransitionError(
            f"不允许的 control 转换: {old_mode} -> {target_mode}"
        )
    if type(expected_writer_version) is not int or expected_writer_version < 0:
        raise ValueError("expected_writer_version 必须是非负整数")
    if type(protocol_version) is not int or protocol_version < OUTBOUND_PROTOCOL_VERSION:
        raise OutboundSafetyError("cutover 需要当前 outbox writer 协议")
    owner = _text(actor_owner, name="actor_owner", max_length=128)
    token = _text(actor_token, name="actor_token", max_length=64)
    seconds = _positive_seconds(
        writer_lease_seconds,
        name="writer_lease_seconds",
    )
    current = _locked_current(db, source_type=source, now=now)
    requested_effective = _utc_naive(effective_from)
    control = _require_writer(
        db,
        source_type=source,
        owner=owner,
        token=token,
        protocol_version=protocol_version,
        current=current,
    )
    if (
        control.mode != old_mode
        or int(control.writer_version) != expected_writer_version
    ):
        raise OutboundFencingError("control mode 或 writer_version CAS 已失效")

    if (old_mode, target_mode) in {
        ("legacy_direct", "outbox_hold"),
        ("outbox_active", "outbox_draining"),
    }:
        if requested_effective <= current:
            raise OutboundSafetyError("cutover effective_from 必须严格晚于 now")
        next_effective = requested_effective
    else:
        if current < control.effective_from:
            raise OutboundSafetyError("尚未到达 control effective boundary")
        if requested_effective != control.effective_from:
            raise OutboundSafetyError("确认转换不得改变既有 effective boundary")
        next_effective = control.effective_from

    if old_mode == "legacy_direct" and _unsafe_for_control_transition(
        db,
        source_type=source,
        epoch=int(control.cutover_epoch),
        include_legacy_runs=True,
    ):
        raise OutboundSafetyError("仍有未安全结算的 legacy writer/run")
    if old_mode == "outbox_hold" and _has_unsafe_legacy_run(
        db,
        source_type=source,
    ):
        raise OutboundSafetyError("hold boundary 仍有未安全结算的 legacy run")
    if old_mode == "outbox_active" and (
        _has_unsafe_legacy_run(db, source_type=source)
        or _has_unsafe_unmaterialized_outbox_run(
            db,
            source_type=source,
        )
    ):
        raise OutboundSafetyError("仍有未安全结算的 legacy 或生成中 run")
    if old_mode == "outbox_draining" and _unsafe_for_control_transition(
        db,
        source_type=source,
        epoch=int(control.cutover_epoch),
        include_legacy_runs=True,
    ):
        raise OutboundSafetyError("draining epoch 仍有不安全队列或 attempt")

    next_epoch = int(control.cutover_epoch)
    if (old_mode, target_mode) in {
        ("legacy_direct", "outbox_hold"),
        ("outbox_draining", "legacy_direct"),
    }:
        next_epoch += 1
    next_writer_version = int(control.writer_version) + 1
    lease_expires_at = current + timedelta(seconds=seconds)
    updated = (
        db.query(OutboundDeliveryControl)
        .filter(
            OutboundDeliveryControl.source_type == source,
            OutboundDeliveryControl.mode == old_mode,
            OutboundDeliveryControl.writer_version == expected_writer_version,
            OutboundDeliveryControl.writer_owner == owner,
            OutboundDeliveryControl.writer_token == token,
            OutboundDeliveryControl.writer_lease_expires_at > current,
            OutboundDeliveryControl.protocol_version == protocol_version,
        )
        .update(
            {
                OutboundDeliveryControl.mode: target_mode,
                OutboundDeliveryControl.cutover_epoch: next_epoch,
                OutboundDeliveryControl.effective_from: next_effective,
                OutboundDeliveryControl.writer_version: next_writer_version,
                OutboundDeliveryControl.writer_lease_expires_at: lease_expires_at,
                OutboundDeliveryControl.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise OutboundFencingError("control transition CAS 失败")
    db.flush()
    result = ControlTransitionResult(
        applied=True,
        source_type=source,
        mode=target_mode,
        cutover_epoch=next_epoch,
        writer_version=next_writer_version,
        effective_from=next_effective,
    )
    db.expire_all()
    return result

__all__ = ["transition_delivery_control"]
