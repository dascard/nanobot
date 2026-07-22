"""主动出站来源控制、writer lease 与生成前门禁。

本模块是 SQLAlchemy 出站适配器的一部分：只负责来源级控制行、writer fencing
以及昂贵生成前的 circuit/cutover 检查。纯值校验与事实计算继续由
``core.outbound.policy`` 提供。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.db.models.outbound import OutboundDeliveryCircuit, OutboundDeliveryControl
from core.outbound.contracts import (
    OUTBOUND_PROTOCOL_VERSION,
    OutboundFencingError,
    OutboundGenerationGate,
    OutboundSafetyError,
    WriterLeaseDecision,
    WriterReleaseResult,
)
from core.outbound.policy import (
    circuit_facts as _circuit_facts,
    require_positive_seconds as _positive_seconds,
    require_text as _text,
    safe_summary as _summary,
    utc_naive as _utc_naive,
)


def _open_circuit_row(
    db: Session,
    facts: tuple[tuple[str, str, str], ...],
) -> OutboundDeliveryCircuit | None:
    for scope_type, scope_fingerprint, config_revision in facts:
        row = (
            db.query(OutboundDeliveryCircuit)
            .filter(
                OutboundDeliveryCircuit.scope_type == scope_type,
                OutboundDeliveryCircuit.scope_fingerprint == scope_fingerprint,
                OutboundDeliveryCircuit.config_revision == config_revision,
                OutboundDeliveryCircuit.status == "open",
            )
            .first()
        )
        if row is not None:
            return row
    return None


def _control(db: Session, source_type: str) -> OutboundDeliveryControl:
    row = db.get(OutboundDeliveryControl, source_type)
    if row is None:
        raise OutboundSafetyError(f"source control 不存在: {source_type}")
    return row


def _lock_source_control(db: Session, source_type: str) -> None:
    """以无语义变更的 CAS 获取当前 source 的 SQLite 写锁。"""

    db.flush()
    updated = (
        db.query(OutboundDeliveryControl)
        .filter(OutboundDeliveryControl.source_type == source_type)
        .update(
            {
                OutboundDeliveryControl.writer_version: (
                    OutboundDeliveryControl.writer_version
                ),
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise OutboundSafetyError(f"source control 不存在: {source_type}")
    db.flush()
    db.expire_all()


def _locked_current(
    db: Session,
    *,
    source_type: str,
    now: datetime | None,
) -> datetime:
    _lock_source_control(db, source_type)
    return _utc_naive(now)


def lock_outbound_source_control(
    db: Session,
    *,
    source_type: str,
    now: datetime | None = None,
) -> datetime:
    """锁定来源控制行并清除 ORM 缓存，由调用方事务持有到提交。"""

    source = _text(source_type, name="source_type", max_length=32)
    return _locked_current(db, source_type=source, now=now)


def acquire_or_renew_delivery_writer(
    db: Session,
    *,
    source_type: str,
    owner: str,
    token: str,
    protocol_version: int,
    lease_seconds: int | float,
    now: datetime | None = None,
) -> WriterLeaseDecision:
    source = _text(source_type, name="source_type", max_length=32)
    normalized_owner = _text(owner, name="owner", max_length=128)
    normalized_token = _text(token, name="token", max_length=64)
    if type(protocol_version) is not int or protocol_version < 1:
        raise ValueError("protocol_version 必须是正整数")
    seconds = _positive_seconds(lease_seconds, name="lease_seconds")
    current = _locked_current(db, source_type=source, now=now)
    expires_at = current + timedelta(seconds=seconds)
    updated = (
        db.query(OutboundDeliveryControl)
        .filter(
            OutboundDeliveryControl.source_type == source,
            OutboundDeliveryControl.protocol_version <= protocol_version,
            or_(
                OutboundDeliveryControl.writer_lease_expires_at.is_(None),
                OutboundDeliveryControl.writer_lease_expires_at <= current,
                (
                    (OutboundDeliveryControl.writer_owner == normalized_owner)
                    & (OutboundDeliveryControl.writer_token == normalized_token)
                ),
            ),
        )
        .update(
            {
                OutboundDeliveryControl.protocol_version: protocol_version,
                OutboundDeliveryControl.writer_version: (
                    OutboundDeliveryControl.writer_version + 1
                ),
                OutboundDeliveryControl.writer_owner: normalized_owner,
                OutboundDeliveryControl.writer_token: normalized_token,
                OutboundDeliveryControl.writer_lease_expires_at: expires_at,
                OutboundDeliveryControl.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    db.expire_all()
    row = _control(db, source)
    return WriterLeaseDecision(
        acquired=updated == 1,
        source_type=source,
        owner=normalized_owner if updated == 1 else str(row.writer_owner or ""),
        token=normalized_token if updated == 1 else "",
        protocol_version=int(row.protocol_version),
        writer_version=int(row.writer_version),
        lease_expires_at=row.writer_lease_expires_at,
    )


def release_delivery_writer(
    db: Session,
    *,
    source_type: str,
    owner: str,
    token: str,
    protocol_version: int,
    expected_writer_version: int,
    now: datetime | None = None,
) -> WriterReleaseResult:
    source = _text(source_type, name="source_type", max_length=32)
    normalized_owner = _text(owner, name="owner", max_length=128)
    normalized_token = _text(token, name="token", max_length=64)
    if type(protocol_version) is not int or protocol_version < 1:
        raise ValueError("protocol_version 必须是正整数")
    if type(expected_writer_version) is not int or expected_writer_version < 0:
        raise ValueError("expected_writer_version 必须是非负整数")
    current = _locked_current(db, source_type=source, now=now)
    control = _require_writer(
        db,
        source_type=source,
        owner=normalized_owner,
        token=normalized_token,
        protocol_version=protocol_version,
        current=current,
    )
    if int(control.writer_version) != expected_writer_version:
        raise OutboundFencingError("writer_version CAS 已失效")
    next_writer_version = expected_writer_version + 1
    updated = (
        db.query(OutboundDeliveryControl)
        .filter(
            OutboundDeliveryControl.source_type == source,
            OutboundDeliveryControl.writer_owner == normalized_owner,
            OutboundDeliveryControl.writer_token == normalized_token,
            OutboundDeliveryControl.protocol_version == protocol_version,
            OutboundDeliveryControl.writer_version == expected_writer_version,
            OutboundDeliveryControl.writer_lease_expires_at > current,
        )
        .update(
            {
                OutboundDeliveryControl.writer_version: next_writer_version,
                OutboundDeliveryControl.writer_owner: None,
                OutboundDeliveryControl.writer_token: None,
                OutboundDeliveryControl.writer_lease_expires_at: None,
                OutboundDeliveryControl.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise OutboundFencingError("writer release CAS 失败")
    db.flush()
    result = WriterReleaseResult(
        applied=True,
        source_type=source,
        writer_version=next_writer_version,
    )
    db.expire_all()
    return result


def _require_writer(
    db: Session,
    *,
    source_type: str,
    owner: str,
    token: str,
    protocol_version: int,
    current: datetime,
) -> OutboundDeliveryControl:
    row = _control(db, source_type)
    if (
        row.writer_owner != owner
        or row.writer_token != token
        or int(row.protocol_version) != int(protocol_version)
        or row.writer_lease_expires_at is None
        or row.writer_lease_expires_at <= current
    ):
        raise OutboundFencingError("writer lease 已失效")
    return row


def _mode_for_occurrence(
    control: OutboundDeliveryControl,
    *,
    occurrence_at: datetime,
) -> tuple[str, int]:
    if (
        control.mode != "legacy_direct"
        and int(control.protocol_version) < OUTBOUND_PROTOCOL_VERSION
    ):
        raise OutboundSafetyError("outbox control 的协议版本不兼容")
    if control.mode in {"outbox_hold", "outbox_active"}:
        if occurrence_at < control.effective_from:
            return "legacy_direct", max(0, int(control.cutover_epoch) - 1)
        return "outbox", int(control.cutover_epoch)
    if control.mode == "outbox_draining":
        raise OutboundSafetyError("outbox_draining 禁止创建新 occurrence")
    if control.mode == "legacy_direct":
        if int(control.cutover_epoch) > 0 and occurrence_at < control.effective_from:
            raise OutboundSafetyError("该 occurrence 属于已经关闭的旧 epoch")
        return "legacy_direct", int(control.cutover_epoch)
    raise OutboundSafetyError(f"未知 delivery control mode: {control.mode}")


def check_outbound_generation_gate(
    db: Session,
    *,
    source_type: str,
    occurrence_at: datetime,
    endpoint_key: str,
    destination_fingerprint: str,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
    now: datetime | None = None,
) -> OutboundGenerationGate:
    """在调用昂贵生成链路前检查 cutover 与持久 circuit。"""

    source = _text(source_type, name="source_type", max_length=32)
    _locked_current(db, source_type=source, now=now)
    control = _control(db, source)
    try:
        delivery_mode, cutover_epoch = _mode_for_occurrence(
            control,
            occurrence_at=_utc_naive(occurrence_at),
        )
    except OutboundSafetyError as exc:
        return OutboundGenerationGate(
            allowed=False,
            delivery_mode="",
            cutover_epoch=int(control.cutover_epoch),
            reason_type="cutover_blocked",
            reason_summary=_summary(exc),
        )
    circuit = _open_circuit_row(
        db,
        _circuit_facts(
            endpoint_key=endpoint_key,
            destination_fingerprint=destination_fingerprint,
            payload_contract_fingerprint=payload_contract_fingerprint,
            config_revision=endpoint_config_revision,
        ),
    )
    if circuit is not None:
        return OutboundGenerationGate(
            allowed=False,
            delivery_mode=delivery_mode,
            cutover_epoch=cutover_epoch,
            reason_type="circuit_open",
            reason_summary=f"{circuit.scope_type} circuit 已打开",
        )
    return OutboundGenerationGate(
        allowed=True,
        delivery_mode=delivery_mode,
        cutover_epoch=cutover_epoch,
        reason_type="",
        reason_summary="",
    )


__all__ = [
    "acquire_or_renew_delivery_writer",
    "check_outbound_generation_gate",
    "lock_outbound_source_control",
    "release_delivery_writer",
]
