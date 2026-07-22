"""通用出站投递的脱敏管理查询与显式状态动作。"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, StrictBool, StrictInt
from sqlalchemy.orm import Session

from api.admin.common import client_ip, verify_admin
from core.outbound import service as outbound_delivery
from core.outbound.policy import proactive_outreach_source_revision
from core.database import (
    AdminAuditLog,
    OutboundDeliveryAttempt,
    OutboundDeliveryCircuit,
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundRun,
    ProactiveOutreachLog,
    get_db,
)
from core.outbound_delivery_service import resolve_qq_push_config_revision


router = APIRouter(
    prefix="/outbound-delivery",
    tags=["admin-outbound-delivery"],
)

_ADMIN_WRITER_OWNER = f"admin-outbound:{secrets.token_hex(16)}"
_ADMIN_WRITER_TOKEN = secrets.token_hex(32)
_ADMIN_WRITER_LEASE_SECONDS = 900.0


class ReplayRequest(BaseModel):
    manual_request_key: str
    confirm_duplicate_risk: StrictBool
    reason: str
    max_attempts: StrictInt
    retry_deadline_at: datetime


class CircuitResetRequest(BaseModel):
    expected_updated_at: datetime
    reason: str


class CancelRequest(BaseModel):
    reason: str


class ControlTransitionRequest(BaseModel):
    expected_mode: Literal[
        "legacy_direct",
        "outbox_hold",
        "outbox_active",
        "outbox_draining",
    ]
    new_mode: Literal[
        "legacy_direct",
        "outbox_hold",
        "outbox_active",
        "outbox_draining",
    ]
    expected_writer_version: StrictInt
    effective_from: datetime
    reason: str


class LegacyResolveRequest(BaseModel):
    resolution: Literal["cancel_without_replay"]
    reason: str
    expected_created_at: datetime
    expected_source_revision: str


def _datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fingerprint(value: object) -> str:
    digest = hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _hash_prefix(value: object) -> str:
    return str(value or "")[:12]


def _required_text(value: object, *, name: str, max_length: int) -> str:
    if type(value) is not str:
        raise HTTPException(status_code=422, detail=f"{name} 必须是字符串")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise HTTPException(
            status_code=422,
            detail=f"{name} 必须为 1-{max_length} 字符",
        )
    return normalized


def _run_payload(row: OutboundRun) -> dict:
    return {
        "id": int(row.id),
        "source_type": str(row.source_type),
        "source_id_fingerprint": _fingerprint(row.source_id),
        "status": str(row.status),
        "task_kind": str(row.task_kind),
        "trigger_type": str(row.trigger_type),
        "scheduled_for": _datetime(row.scheduled_for),
        "attempted_at": _datetime(row.attempted_at),
        "generated_at": _datetime(row.generated_at),
        "succeeded_at": _datetime(row.succeeded_at),
        "failure_type": str(row.failure_type or ""),
        "active_outbox_id": (
            int(row.active_outbox_id)
            if row.active_outbox_id is not None
            else None
        ),
        "has_ambiguous_ancestor": bool(row.has_ambiguous_ancestor),
        "delivery_mode": str(row.delivery_mode),
        "cutover_epoch": int(row.cutover_epoch),
        "created_at": _datetime(row.created_at),
        "updated_at": _datetime(row.updated_at),
    }


def _outbox_payload(row: OutboundDeliveryOutbox, source_type: str) -> dict:
    return {
        "id": int(row.id),
        "run_id": int(row.run_id),
        "source_type": str(source_type),
        "status": str(row.status),
        "target_type": str(row.target_type),
        "endpoint_key": str(row.endpoint_key),
        "destination_fingerprint": str(row.destination_fingerprint),
        "payload_sha256_prefix": _hash_prefix(row.payload_sha256),
        "allocated_attempt_count": int(row.allocated_attempt_count),
        "request_started_count": int(row.request_started_count),
        "max_attempts": int(row.max_attempts),
        "retry_deadline_at": _datetime(row.retry_deadline_at),
        "next_attempt_at": _datetime(row.next_attempt_at),
        "last_error_type": str(row.last_error_type or ""),
        "delivered_at": _datetime(row.delivered_at),
        "cancelled_at": _datetime(row.cancelled_at),
        "cancel_reason_type": row.cancel_reason_type,
        "replay_of_outbox_id": (
            int(row.replay_of_outbox_id)
            if row.replay_of_outbox_id is not None
            else None
        ),
        "replay_sequence": int(row.replay_sequence),
        "cutover_epoch": int(row.cutover_epoch),
        "endpoint_config_revision": str(row.endpoint_config_revision),
        "created_at": _datetime(row.created_at),
        "updated_at": _datetime(row.updated_at),
    }


def _attempt_payload(row: OutboundDeliveryAttempt) -> dict:
    return {
        "id": int(row.id),
        "outbox_id": int(row.outbox_id),
        "attempt_no": int(row.attempt_no),
        "status": str(row.status),
        "transport_phase": str(row.transport_phase),
        "request_started": bool(row.request_started),
        "endpoint_config_revision": str(row.endpoint_config_revision),
        "http_status": row.http_status,
        "result_category": str(row.result_category or ""),
        "error_type": str(row.error_type or ""),
        "duration_ms": row.duration_ms,
        "settlement_retry_at": _datetime(row.settlement_retry_at),
        "started_at": _datetime(row.started_at),
        "request_started_at": _datetime(row.request_started_at),
        "completed_at": _datetime(row.completed_at),
        "created_at": _datetime(row.created_at),
    }


def _circuit_payload(row: OutboundDeliveryCircuit) -> dict:
    return {
        "id": int(row.id),
        "scope_type": str(row.scope_type),
        "scope_fingerprint": str(row.scope_fingerprint),
        "config_revision": str(row.config_revision),
        "status": str(row.status),
        "reason_type": str(row.reason_type or ""),
        "opened_at": _datetime(row.opened_at),
        "opened_by_attempt_id": (
            int(row.opened_by_attempt_id)
            if row.opened_by_attempt_id is not None
            else None
        ),
        "created_at": _datetime(row.created_at),
        "updated_at": _datetime(row.updated_at),
    }


def _control_payload(row: OutboundDeliveryControl) -> dict:
    return {
        "source_type": str(row.source_type),
        "mode": str(row.mode),
        "cutover_epoch": int(row.cutover_epoch),
        "effective_from": _datetime(row.effective_from),
        "protocol_version": int(row.protocol_version),
        "writer_version": int(row.writer_version),
        "writer_lease_expires_at": _datetime(row.writer_lease_expires_at),
        "created_at": _datetime(row.created_at),
        "updated_at": _datetime(row.updated_at),
    }


def _legacy_proactive_payload(row: ProactiveOutreachLog) -> dict:
    return {
        "id": int(row.id),
        "source_type": "proactive_outreach",
        "status": str(row.status),
        "created_at": _datetime(row.created_at),
        "source_revision": (
            proactive_outreach_source_revision(row)
        ),
    }


def _add_action_audit(
    db: Session,
    *,
    admin_user: str,
    request: Request,
    action: str,
    target_type: str,
    target_id: int | str,
    detail: dict,
) -> None:
    db.add(AdminAuditLog(
        admin_user=str(admin_user or "admin")[:255],
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        detail_json=json.dumps(detail, ensure_ascii=False, sort_keys=True),
        ip_address=client_ip(request),
    ))


def _state_conflict(db: Session) -> None:
    db.rollback()
    raise HTTPException(status_code=409, detail="当前状态不允许该操作")


def _validation_failure(db: Session) -> None:
    db.rollback()
    raise HTTPException(status_code=422, detail="动作参数不符合状态机合同")


def _internal_failure(db: Session) -> None:
    db.rollback()
    raise HTTPException(status_code=500, detail="管理动作未提交")


@router.get("/runs")
def list_outbound_runs(
    source_type: str = "",
    status: str = "",
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    query = db.query(OutboundRun)
    if source_type:
        query = query.filter(OutboundRun.source_type == source_type)
    if status:
        query = query.filter(OutboundRun.status == status)
    total = query.count()
    rows = (
        query.order_by(OutboundRun.created_at.desc(), OutboundRun.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [_run_payload(row) for row in rows],
        "page": page,
        "limit": limit,
    }


@router.get("/runs/{run_id}")
def get_outbound_run(
    run_id: int,
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    row = db.get(OutboundRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="出站运行不存在")
    return _run_payload(row)


@router.get("/outboxes")
def list_outbound_outboxes(
    source_type: str = "",
    status: str = "",
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    query = db.query(OutboundDeliveryOutbox, OutboundRun.source_type).join(
        OutboundRun,
        OutboundRun.id == OutboundDeliveryOutbox.run_id,
    )
    if source_type:
        query = query.filter(OutboundRun.source_type == source_type)
    if status:
        query = query.filter(OutboundDeliveryOutbox.status == status)
    total = query.count()
    rows = (
        query.order_by(
            OutboundDeliveryOutbox.created_at.desc(),
            OutboundDeliveryOutbox.id.desc(),
        )
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [_outbox_payload(row, source) for row, source in rows],
        "page": page,
        "limit": limit,
    }


@router.get("/outboxes/{outbox_id}")
def get_outbound_outbox(
    outbox_id: int,
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    result = (
        db.query(OutboundDeliveryOutbox, OutboundRun.source_type)
        .join(OutboundRun, OutboundRun.id == OutboundDeliveryOutbox.run_id)
        .filter(OutboundDeliveryOutbox.id == outbox_id)
        .first()
    )
    if result is None:
        raise HTTPException(status_code=404, detail="出站队列记录不存在")
    row, source_type = result
    return _outbox_payload(row, source_type)


@router.get("/outboxes/{outbox_id}/attempts")
def list_outbound_attempts(
    outbox_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    if db.get(OutboundDeliveryOutbox, outbox_id) is None:
        raise HTTPException(status_code=404, detail="出站队列记录不存在")
    query = db.query(OutboundDeliveryAttempt).filter(
        OutboundDeliveryAttempt.outbox_id == outbox_id
    )
    total = query.count()
    rows = (
        query.order_by(
            OutboundDeliveryAttempt.attempt_no.desc(),
            OutboundDeliveryAttempt.id.desc(),
        )
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [_attempt_payload(row) for row in rows],
        "page": page,
        "limit": limit,
    }


@router.get("/circuits")
def list_outbound_circuits(
    status: str = "",
    scope_type: str = "",
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    query = db.query(OutboundDeliveryCircuit)
    if status:
        query = query.filter(OutboundDeliveryCircuit.status == status)
    if scope_type:
        query = query.filter(OutboundDeliveryCircuit.scope_type == scope_type)
    total = query.count()
    rows = (
        query.order_by(
            OutboundDeliveryCircuit.updated_at.desc(),
            OutboundDeliveryCircuit.id.desc(),
        )
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [_circuit_payload(row) for row in rows],
        "page": page,
        "limit": limit,
    }


@router.get("/controls")
def list_outbound_controls(
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    rows = db.query(OutboundDeliveryControl).order_by(
        OutboundDeliveryControl.source_type.asc()
    ).all()
    return {
        "total": len(rows),
        "items": [_control_payload(row) for row in rows],
    }


@router.get("/legacy-proactive")
def list_legacy_ambiguous_outreach(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    query = db.query(ProactiveOutreachLog).filter(
        ProactiveOutreachLog.status == "legacy_ambiguous_hold"
    )
    total = query.count()
    rows = (
        query.order_by(
            ProactiveOutreachLog.created_at.desc(),
            ProactiveOutreachLog.id.desc(),
        )
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [_legacy_proactive_payload(row) for row in rows],
        "page": page,
        "limit": limit,
    }


@router.post("/outboxes/{outbox_id}/replay")
def replay_outbound_delivery(
    outbox_id: int,
    body: ReplayRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    request_key = _required_text(
        body.manual_request_key,
        name="manual_request_key",
        max_length=255,
    )
    reason = _required_text(body.reason, name="reason", max_length=1000)
    if db.get(OutboundDeliveryOutbox, outbox_id) is None:
        raise HTTPException(status_code=404, detail="出站队列记录不存在")
    try:
        revision = resolve_qq_push_config_revision()
    except (TypeError, ValueError):
        raise HTTPException(status_code=503, detail="出站配置不可用") from None
    try:
        result = outbound_delivery.create_delivery_replay(
            db,
            parent_outbox_id=outbox_id,
            manual_request_key=request_key,
            confirm_duplicate_risk=body.confirm_duplicate_risk,
            reason=reason,
            max_attempts=body.max_attempts,
            retry_deadline_at=body.retry_deadline_at,
            endpoint_config_revision=revision,
            now=_utc_now(),
        )
        replay_outbox = db.get(OutboundDeliveryOutbox, result.outbox_id)
        replay_run = db.get(OutboundRun, result.run_id)
        if replay_outbox is None or replay_run is None:
            raise RuntimeError("replay 结果缺少 outbox 或 run")
        status = str(replay_outbox.status)
        run_status = str(replay_run.status)
        _add_action_audit(
            db,
            admin_user=_auth,
            request=request,
            action="replay_outbound_delivery",
            target_type="outbound_delivery_outbox",
            target_id=outbox_id,
            detail={
                "parent_outbox_id": outbox_id,
                "child_outbox_id": result.outbox_id,
                "created": result.created,
                "status": status,
                "run_status": run_status,
                "manual_request_key_fingerprint": _fingerprint(request_key),
                "reason": reason,
            },
        )
        db.commit()
    except (
        outbound_delivery.OutboundSafetyError,
        outbound_delivery.OutboundConflictError,
        outbound_delivery.OutboundFencingError,
        outbound_delivery.InvalidOutboundTransitionError,
    ):
        _state_conflict(db)
    except (TypeError, ValueError):
        _validation_failure(db)
    except Exception:
        _internal_failure(db)
    return {
        "applied": True,
        "created": result.created,
        "outbox_id": result.outbox_id,
        "run_id": result.run_id,
        "replay_sequence": result.replay_sequence,
        "status": status,
        "run_status": run_status,
    }


@router.post("/outboxes/{outbox_id}/cancel")
def cancel_outbound_delivery(
    outbox_id: int,
    body: CancelRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    reason = _required_text(body.reason, name="reason", max_length=1000)
    outbox = db.get(OutboundDeliveryOutbox, outbox_id)
    if outbox is None:
        raise HTTPException(status_code=404, detail="出站队列记录不存在")
    expected_status = str(outbox.status)
    expected_updated_at = outbox.updated_at
    try:
        result = outbound_delivery.cancel_safe_outbox(
            db,
            outbox_id=outbox_id,
            expected_status=expected_status,
            expected_updated_at=expected_updated_at,
            reason_type="admin_cancelled",
            safe_summary=reason,
        )
        _add_action_audit(
            db,
            admin_user=_auth,
            request=request,
            action="cancel_outbound_delivery",
            target_type="outbound_delivery_outbox",
            target_id=outbox_id,
            detail={
                "outbox_id": outbox_id,
                "previous_status": expected_status,
                "reason": reason,
            },
        )
        db.commit()
    except (
        outbound_delivery.OutboundSafetyError,
        outbound_delivery.OutboundConflictError,
        outbound_delivery.OutboundFencingError,
    ):
        _state_conflict(db)
    except (TypeError, ValueError):
        _validation_failure(db)
    except Exception:
        _internal_failure(db)
    return {
        "applied": result.applied,
        "outbox_id": result.outbox_id,
        "run_id": result.run_id,
        "status": result.status,
    }


@router.post("/circuits/{circuit_id}/reset")
def reset_outbound_delivery_circuit(
    circuit_id: int,
    body: CircuitResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    reason = _required_text(body.reason, name="reason", max_length=1000)
    circuit = db.get(OutboundDeliveryCircuit, circuit_id)
    if circuit is None:
        raise HTTPException(status_code=404, detail="出站熔断记录不存在")
    try:
        result = outbound_delivery.reset_delivery_circuit(
            db,
            scope_type=str(circuit.scope_type),
            scope_fingerprint=str(circuit.scope_fingerprint),
            config_revision=str(circuit.config_revision),
            expected_updated_at=body.expected_updated_at,
        )
        if not result.applied:
            _state_conflict(db)
        _add_action_audit(
            db,
            admin_user=_auth,
            request=request,
            action="reset_outbound_delivery_circuit",
            target_type="outbound_delivery_circuit",
            target_id=circuit_id,
            detail={"circuit_id": circuit_id, "reason": reason},
        )
        db.commit()
    except HTTPException:
        raise
    except (
        outbound_delivery.OutboundSafetyError,
        outbound_delivery.OutboundConflictError,
        outbound_delivery.OutboundFencingError,
    ):
        _state_conflict(db)
    except (TypeError, ValueError):
        _validation_failure(db)
    except Exception:
        _internal_failure(db)
    return {
        "applied": result.applied,
        "circuit_id": result.circuit_id,
        "status": result.status,
    }


@router.post("/controls/{source_type}/transition")
def transition_outbound_delivery_control(
    source_type: str,
    body: ControlTransitionRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    reason = _required_text(body.reason, name="reason", max_length=1000)
    control = db.get(OutboundDeliveryControl, source_type)
    if control is None:
        raise HTTPException(status_code=404, detail="出站控制记录不存在")
    if (
        str(control.mode) != body.expected_mode
        or int(control.writer_version) != body.expected_writer_version
    ):
        _state_conflict(db)
    current = _utc_now()
    try:
        writer = outbound_delivery.acquire_or_renew_delivery_writer(
            db,
            source_type=source_type,
            owner=_ADMIN_WRITER_OWNER,
            token=_ADMIN_WRITER_TOKEN,
            protocol_version=outbound_delivery.OUTBOUND_PROTOCOL_VERSION,
            lease_seconds=_ADMIN_WRITER_LEASE_SECONDS,
            now=current,
        )
        if (
            not writer.acquired
            or writer.writer_version != body.expected_writer_version + 1
        ):
            _state_conflict(db)
        result = outbound_delivery.transition_delivery_control(
            db,
            source_type=source_type,
            expected_mode=body.expected_mode,
            new_mode=body.new_mode,
            expected_writer_version=writer.writer_version,
            actor_owner=_ADMIN_WRITER_OWNER,
            actor_token=_ADMIN_WRITER_TOKEN,
            protocol_version=outbound_delivery.OUTBOUND_PROTOCOL_VERSION,
            effective_from=body.effective_from,
            writer_lease_seconds=_ADMIN_WRITER_LEASE_SECONDS,
            now=current,
        )
        released = outbound_delivery.release_delivery_writer(
            db,
            source_type=source_type,
            owner=_ADMIN_WRITER_OWNER,
            token=_ADMIN_WRITER_TOKEN,
            protocol_version=outbound_delivery.OUTBOUND_PROTOCOL_VERSION,
            expected_writer_version=result.writer_version,
            now=current,
        )
        _add_action_audit(
            db,
            admin_user=_auth,
            request=request,
            action="transition_outbound_delivery_control",
            target_type="outbound_delivery_control",
            target_id=source_type,
            detail={
                "source_type": source_type,
                "expected_mode": body.expected_mode,
                "new_mode": body.new_mode,
                "expected_writer_version": body.expected_writer_version,
                "writer_version": released.writer_version,
                "cutover_epoch": result.cutover_epoch,
                "reason": reason,
            },
        )
        db.commit()
    except HTTPException:
        raise
    except (
        outbound_delivery.OutboundSafetyError,
        outbound_delivery.OutboundConflictError,
        outbound_delivery.OutboundFencingError,
        outbound_delivery.InvalidOutboundTransitionError,
    ):
        _state_conflict(db)
    except (TypeError, ValueError):
        _validation_failure(db)
    except Exception:
        _internal_failure(db)
    return {
        "applied": result.applied,
        "source_type": result.source_type,
        "mode": result.mode,
        "cutover_epoch": result.cutover_epoch,
        "writer_version": released.writer_version,
        "effective_from": _datetime(result.effective_from),
    }


@router.post("/legacy-proactive/{log_id}/resolve")
def resolve_legacy_ambiguous_outreach(
    log_id: int,
    body: LegacyResolveRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    reason = _required_text(body.reason, name="reason", max_length=1000)
    if db.get(ProactiveOutreachLog, log_id) is None:
        raise HTTPException(status_code=404, detail="旧主动外呼记录不存在")
    try:
        result = outbound_delivery.resolve_legacy_ambiguous_outreach(
            db,
            outreach_log_id=log_id,
            expected_created_at=body.expected_created_at,
            expected_source_revision=body.expected_source_revision,
            resolution=body.resolution,
            reason=reason,
        )
        _add_action_audit(
            db,
            admin_user=_auth,
            request=request,
            action="resolve_legacy_ambiguous_outreach",
            target_type="proactive_outreach_log",
            target_id=log_id,
            detail={
                "log_id": log_id,
                "resolution": body.resolution,
                "reason": reason,
                "applied": result.applied,
            },
        )
        db.commit()
    except (
        outbound_delivery.OutboundSafetyError,
        outbound_delivery.OutboundConflictError,
        outbound_delivery.OutboundFencingError,
    ):
        _state_conflict(db)
    except (TypeError, ValueError):
        _validation_failure(db)
    except Exception:
        _internal_failure(db)
    return {
        "applied": result.applied,
        "outreach_log_id": result.outreach_log_id,
        "status": result.status,
    }
