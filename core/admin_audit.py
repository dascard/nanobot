"""高风险治理操作的事务审计与跨存储审计意图。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Mapping
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db.models.admin import AdminAuditLog, AdminAuditOutboxRow


_OUTBOX_STATUSES = frozenset({"prepared", "finalized", "failed", "ambiguous"})


class AdminAuditError(RuntimeError):
    """治理审计合同或持久化失败。"""


class AdminAuditConflict(AdminAuditError):
    """同一审计事件标识绑定了不同事实。"""


class AdminAuditPreparationError(AdminAuditError):
    """外部治理操作执行前无法建立持久审计意图。"""


class AdminAuditFinalizationError(AdminAuditError):
    """外部治理操作完成后无法原子确认审计结果。"""


@dataclass(frozen=True, slots=True)
class AdminAuditIntent:
    """跨存储治理审计意图的只读快照。"""

    event_id: str
    admin_user: str
    action: str
    target_type: str
    target_id: str
    request_detail: dict[str, Any]
    ip_address: str
    status: str
    result_target_id: str
    result_detail: dict[str, Any]
    last_error_code: str


def _now() -> datetime:
    return datetime.now().replace(tzinfo=None)


def _text(value: object, name: str, *, maximum: int, required: bool = True) -> str:
    normalized = str(value or "").strip()
    if (
        (required and not normalized)
        or len(normalized) > maximum
        or any(ord(char) < 32 for char in normalized)
    ):
        raise AdminAuditConflict(f"{name} 无效")
    return normalized


def _detail(value: Mapping[str, Any] | None) -> dict[str, Any]:
    copied = dict(value or {})
    try:
        encoded = json.dumps(
            copied,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AdminAuditConflict("审计 detail 必须是 JSON 对象") from exc
    if not isinstance(decoded, dict):
        raise AdminAuditConflict("审计 detail 必须是 JSON 对象")
    return decoded


def _detail_json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(
        _detail(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_detail(value: object, name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AdminAuditConflict(f"{name} 无法解析") from exc
    if not isinstance(parsed, dict):
        raise AdminAuditConflict(f"{name} 必须是 JSON 对象")
    return parsed


def _intent_from_row(row: AdminAuditOutboxRow) -> AdminAuditIntent:
    status = str(row.status or "")
    if status not in _OUTBOX_STATUSES:
        raise AdminAuditConflict("治理审计意图状态无效")
    return AdminAuditIntent(
        event_id=str(row.event_id),
        admin_user=str(row.admin_user),
        action=str(row.action),
        target_type=str(row.target_type),
        target_id=str(row.target_id),
        request_detail=_parse_detail(
            row.request_detail_json,
            "request_detail_json",
        ),
        ip_address=str(row.ip_address or ""),
        status=status,
        result_target_id=str(row.result_target_id or ""),
        result_detail=_parse_detail(
            row.result_detail_json,
            "result_detail_json",
        ),
        last_error_code=str(row.last_error_code or ""),
    )


def load_admin_audit_intent(
    db: Session,
    event_id: str,
) -> AdminAuditIntent | None:
    row = db.get(AdminAuditOutboxRow, str(event_id or ""))
    return _intent_from_row(row) if row is not None else None


def _audit_matches(
    row: AdminAuditLog,
    *,
    action: str,
    target_type: str,
    target_id: str,
    detail_json: str,
    ip_address: str,
    admin_user: str,
) -> bool:
    return (
        str(row.action) == action
        and str(row.target_type or "") == target_type
        and str(row.target_id or "") == target_id
        and str(row.detail_json or "{}") == detail_json
        and str(row.ip_address or "") == ip_address
        and str(row.admin_user or "admin") == admin_user
    )


def stage_admin_audit(
    db: Session,
    *,
    action: str,
    target_type: str = "",
    target_id: str = "",
    detail: Mapping[str, Any] | None = None,
    ip_address: str = "",
    admin_user: str = "admin",
    event_id: str = "",
) -> AdminAuditLog:
    """只暂存审计行，不提交；由调用方与业务事实共同提交。"""

    normalized_action = _text(action, "action", maximum=128)
    normalized_target_type = _text(
        target_type,
        "target_type",
        maximum=128,
        required=False,
    )
    normalized_target_id = _text(
        target_id,
        "target_id",
        maximum=255,
        required=False,
    )
    normalized_admin = _text(admin_user, "admin_user", maximum=128)
    normalized_ip = _text(
        ip_address,
        "ip_address",
        maximum=45,
        required=False,
    )
    normalized_event_id = _text(
        event_id,
        "event_id",
        maximum=96,
        required=False,
    )
    encoded_detail = _detail_json(detail)
    if normalized_event_id:
        existing = db.execute(
            select(AdminAuditLog).where(
                AdminAuditLog.event_id == normalized_event_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            if not _audit_matches(
                existing,
                action=normalized_action,
                target_type=normalized_target_type,
                target_id=normalized_target_id,
                detail_json=encoded_detail,
                ip_address=normalized_ip,
                admin_user=normalized_admin,
            ):
                raise AdminAuditConflict("审计事件标识已绑定不同事实")
            return existing
    row = AdminAuditLog(
        event_id=normalized_event_id or None,
        admin_user=normalized_admin,
        action=normalized_action,
        target_type=normalized_target_type,
        target_id=normalized_target_id,
        detail_json=encoded_detail,
        ip_address=normalized_ip,
    )
    db.add(row)
    return row


def _validate_intent_identity(
    intent: AdminAuditIntent,
    *,
    admin_user: str,
    action: str,
    target_type: str,
    target_id: str,
    request_detail: Mapping[str, Any] | None,
    ip_address: str,
) -> None:
    if (
        intent.admin_user != admin_user
        or intent.action != action
        or intent.target_type != target_type
        or intent.target_id != target_id
        or intent.request_detail != _detail(request_detail)
        or intent.ip_address != ip_address
    ):
        raise AdminAuditConflict("治理审计意图已绑定不同请求")


def _prepared_audit_exists(db: Session, intent: AdminAuditIntent) -> bool:
    event_id = f"{intent.event_id}:prepared"
    row = db.execute(
        select(AdminAuditLog).where(AdminAuditLog.event_id == event_id)
    ).scalar_one_or_none()
    if row is None:
        return False
    detail = {
        "audit_event_id": intent.event_id,
        "audit_phase": "prepared",
        "request": intent.request_detail,
    }
    return _audit_matches(
        row,
        action=f"{intent.action}.prepared",
        target_type=intent.target_type,
        target_id=intent.target_id,
        detail_json=_detail_json(detail),
        ip_address=intent.ip_address,
        admin_user=intent.admin_user,
    )


def prepare_external_admin_audit(
    db: Session,
    *,
    action: str,
    target_type: str,
    target_id: str,
    detail: Mapping[str, Any] | None = None,
    ip_address: str = "",
    admin_user: str = "admin",
    event_id: str = "",
) -> AdminAuditIntent:
    """在文件或外部控制面变更前提交可恢复审计意图。"""

    normalized_action = _text(action, "action", maximum=112)
    normalized_target_type = _text(
        target_type,
        "target_type",
        maximum=128,
        required=False,
    )
    normalized_target_id = _text(
        target_id,
        "target_id",
        maximum=255,
        required=False,
    )
    normalized_admin = _text(admin_user, "admin_user", maximum=128)
    normalized_ip = _text(
        ip_address,
        "ip_address",
        maximum=45,
        required=False,
    )
    request_detail = _detail(detail)
    normalized_event_id = _text(
        event_id or f"audit_{uuid.uuid4().hex}",
        "event_id",
        maximum=64,
    )
    existing = load_admin_audit_intent(db, normalized_event_id)
    if existing is not None:
        _validate_intent_identity(
            existing,
            admin_user=normalized_admin,
            action=normalized_action,
            target_type=normalized_target_type,
            target_id=normalized_target_id,
            request_detail=request_detail,
            ip_address=normalized_ip,
        )
        if not _prepared_audit_exists(db, existing):
            raise AdminAuditConflict("治理审计意图缺少准备审计事实")
        return existing

    now = _now()
    prepared_detail = {
        "audit_event_id": normalized_event_id,
        "audit_phase": "prepared",
        "request": request_detail,
    }
    stage_admin_audit(
        db,
        event_id=f"{normalized_event_id}:prepared",
        admin_user=normalized_admin,
        action=f"{normalized_action}.prepared",
        target_type=normalized_target_type,
        target_id=normalized_target_id,
        detail=prepared_detail,
        ip_address=normalized_ip,
    )
    db.add(AdminAuditOutboxRow(
        event_id=normalized_event_id,
        admin_user=normalized_admin,
        action=normalized_action,
        target_type=normalized_target_type,
        target_id=normalized_target_id,
        request_detail_json=_detail_json(request_detail),
        ip_address=normalized_ip,
        status="prepared",
        result_target_id="",
        result_detail_json="{}",
        last_error_code="",
        created_at=now,
        updated_at=now,
    ))
    try:
        db.commit()
    except BaseException as exc:
        db.rollback()
        recovered = load_admin_audit_intent(db, normalized_event_id)
        if recovered is not None:
            _validate_intent_identity(
                recovered,
                admin_user=normalized_admin,
                action=normalized_action,
                target_type=normalized_target_type,
                target_id=normalized_target_id,
                request_detail=request_detail,
                ip_address=normalized_ip,
            )
            if _prepared_audit_exists(db, recovered):
                return recovered
        raise AdminAuditPreparationError(
            "治理审计意图未建立，外部操作不得执行"
        ) from exc
    recovered = load_admin_audit_intent(db, normalized_event_id)
    if recovered is None or not _prepared_audit_exists(db, recovered):
        raise AdminAuditPreparationError("治理审计意图提交后不可见")
    return recovered


def _final_audit_exists(
    db: Session,
    intent: AdminAuditIntent,
    *,
    target_id: str,
    result_detail: Mapping[str, Any],
) -> bool:
    event_id = f"{intent.event_id}:finalized"
    row = db.execute(
        select(AdminAuditLog).where(AdminAuditLog.event_id == event_id)
    ).scalar_one_or_none()
    if row is None:
        return False
    detail = {
        **_detail(result_detail),
        "audit_event_id": intent.event_id,
        "audit_phase": "finalized",
    }
    return _audit_matches(
        row,
        action=intent.action,
        target_type=intent.target_type,
        target_id=target_id,
        detail_json=_detail_json(detail),
        ip_address=intent.ip_address,
        admin_user=intent.admin_user,
    )


def _mark_ambiguous(db: Session, event_id: str, error_code: str) -> None:
    try:
        row = db.get(AdminAuditOutboxRow, event_id)
        if row is None or str(row.status) == "finalized":
            return
        row.status = "ambiguous"
        row.last_error_code = str(error_code or "audit_finalize_failed")[:128]
        row.updated_at = _now()
        db.commit()
    except BaseException:
        db.rollback()


def finalize_external_admin_audit(
    db: Session,
    intent: AdminAuditIntent,
    *,
    target_id: str,
    detail: Mapping[str, Any] | None = None,
) -> AdminAuditIntent:
    """原子写入成功审计并终结跨存储审计意图。"""

    normalized_target_id = _text(
        target_id,
        "target_id",
        maximum=255,
        required=False,
    )
    result_detail = _detail(detail)
    current = load_admin_audit_intent(db, intent.event_id)
    if current is None:
        raise AdminAuditFinalizationError("治理审计意图不存在")
    _validate_intent_identity(
        current,
        admin_user=intent.admin_user,
        action=intent.action,
        target_type=intent.target_type,
        target_id=intent.target_id,
        request_detail=intent.request_detail,
        ip_address=intent.ip_address,
    )
    if current.status == "finalized":
        if (
            current.result_target_id != normalized_target_id
            or current.result_detail != result_detail
            or not _final_audit_exists(
                db,
                current,
                target_id=normalized_target_id,
                result_detail=result_detail,
            )
        ):
            raise AdminAuditConflict("治理审计结果已绑定不同事实")
        return current
    if current.status == "failed":
        raise AdminAuditConflict("失败的治理审计意图不能改写为成功")

    row = db.get(AdminAuditOutboxRow, intent.event_id)
    if row is None:
        raise AdminAuditFinalizationError("治理审计意图不存在")
    now = _now()
    row.status = "finalized"
    row.result_target_id = normalized_target_id
    row.result_detail_json = _detail_json(result_detail)
    row.last_error_code = ""
    row.updated_at = now
    row.finalized_at = now
    final_detail = {
        **result_detail,
        "audit_event_id": intent.event_id,
        "audit_phase": "finalized",
    }
    stage_admin_audit(
        db,
        event_id=f"{intent.event_id}:finalized",
        admin_user=intent.admin_user,
        action=intent.action,
        target_type=intent.target_type,
        target_id=normalized_target_id,
        detail=final_detail,
        ip_address=intent.ip_address,
    )
    try:
        db.commit()
    except BaseException as exc:
        db.rollback()
        recovered = load_admin_audit_intent(db, intent.event_id)
        if (
            recovered is not None
            and recovered.status == "finalized"
            and recovered.result_target_id == normalized_target_id
            and recovered.result_detail == result_detail
            and _final_audit_exists(
                db,
                recovered,
                target_id=normalized_target_id,
                result_detail=result_detail,
            )
        ):
            return recovered
        _mark_ambiguous(db, intent.event_id, type(exc).__name__)
        raise AdminAuditFinalizationError(
            "治理操作结果未能写入成功审计，已保留歧义意图"
        ) from exc
    recovered = load_admin_audit_intent(db, intent.event_id)
    if recovered is None or recovered.status != "finalized":
        raise AdminAuditFinalizationError("治理审计结果提交后不可见")
    return recovered


def fail_external_admin_audit(
    db: Session,
    intent: AdminAuditIntent,
    *,
    error_code: str,
) -> AdminAuditIntent:
    """记录外部治理操作在产生成功结果前失败。"""

    normalized_error = _text(
        error_code or "external_operation_failed",
        "error_code",
        maximum=128,
    )
    current = load_admin_audit_intent(db, intent.event_id)
    if current is None:
        raise AdminAuditFinalizationError("治理审计意图不存在")
    if current.status == "finalized":
        raise AdminAuditConflict("已成功的治理审计意图不能改写为失败")
    if current.status == "failed" and current.last_error_code == normalized_error:
        return current
    row = db.get(AdminAuditOutboxRow, intent.event_id)
    if row is None:
        raise AdminAuditFinalizationError("治理审计意图不存在")
    row.status = "failed"
    row.last_error_code = normalized_error
    row.updated_at = _now()
    stage_admin_audit(
        db,
        event_id=f"{intent.event_id}:failed",
        admin_user=intent.admin_user,
        action=f"{intent.action}.failed",
        target_type=intent.target_type,
        target_id=intent.target_id,
        detail={
            "audit_event_id": intent.event_id,
            "audit_phase": "failed",
            "error_code": normalized_error,
        },
        ip_address=intent.ip_address,
    )
    try:
        db.commit()
    except BaseException as exc:
        db.rollback()
        recovered = load_admin_audit_intent(db, intent.event_id)
        if (
            recovered is not None
            and recovered.status == "failed"
            and recovered.last_error_code == normalized_error
        ):
            return recovered
        _mark_ambiguous(db, intent.event_id, type(exc).__name__)
        raise AdminAuditFinalizationError(
            "治理操作失败审计未能确认，已保留歧义意图"
        ) from exc
    recovered = load_admin_audit_intent(db, intent.event_id)
    if recovered is None or recovered.status != "failed":
        raise AdminAuditFinalizationError("治理操作失败审计提交后不可见")
    return recovered


def unresolved_admin_audit_intents(
    db: Session,
) -> tuple[AdminAuditIntent, ...]:
    rows = db.execute(
        select(AdminAuditOutboxRow)
        .where(AdminAuditOutboxRow.status.in_(("prepared", "ambiguous")))
        .order_by(AdminAuditOutboxRow.created_at, AdminAuditOutboxRow.event_id)
    ).scalars()
    return tuple(_intent_from_row(row) for row in rows)


def reconcile_prepared_admin_audit_intents(db: Session) -> int:
    """启动时将上个进程遗留的 prepared 意图提升为明确歧义。"""

    rows = tuple(db.execute(
        select(AdminAuditOutboxRow)
        .where(AdminAuditOutboxRow.status == "prepared")
        .order_by(AdminAuditOutboxRow.created_at, AdminAuditOutboxRow.event_id)
    ).scalars())
    if not rows:
        return 0
    event_ids: list[str] = []
    now = _now()
    for row in rows:
        intent = _intent_from_row(row)
        if not _prepared_audit_exists(db, intent):
            raise AdminAuditConflict("prepared 治理审计意图缺少准备审计事实")
        row.status = "ambiguous"
        row.last_error_code = "process_restart_before_audit_finalization"
        row.updated_at = now
        event_ids.append(intent.event_id)
    try:
        db.commit()
    except BaseException:
        db.rollback()
        recovered = tuple(
            load_admin_audit_intent(db, event_id) for event_id in event_ids
        )
        if all(
            item is not None
            and item.status == "ambiguous"
            and item.last_error_code
            == "process_restart_before_audit_finalization"
            for item in recovered
        ):
            return len(event_ids)
        raise
    return len(event_ids)


__all__ = [
    "AdminAuditConflict",
    "AdminAuditError",
    "AdminAuditFinalizationError",
    "AdminAuditIntent",
    "AdminAuditPreparationError",
    "fail_external_admin_audit",
    "finalize_external_admin_audit",
    "load_admin_audit_intent",
    "prepare_external_admin_audit",
    "reconcile_prepared_admin_audit_intents",
    "stage_admin_audit",
    "unresolved_admin_audit_intents",
]
