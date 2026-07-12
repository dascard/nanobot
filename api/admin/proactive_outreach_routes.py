"""Admin 主动情感外呼管理路由。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.admin.common import audit, client_ip, verify_admin
from clients.classifier_client import strip_think_blocks
from core.config_registry import SETTING_DEFS, SettingDef
from core.database import LLMApiRequestLog, ProactiveOutreachLog, SystemSetting, get_db
from core.identity import get_super_user_ids
from core.proactive_outreach import (
    run_outreach_dry_run_once,
    run_outreach_due_once,
    run_outreach_once,
)
from core.settings_service import settings


router = APIRouter(prefix="/proactive-outreach", tags=["admin-proactive-outreach"])

_MANAGED_SETTING_KEYS = (
    "proactive_outreach.enabled",
    "proactive_outreach.fallback_interval_min",
    "proactive_outreach.min_interval_min",
    "proactive_outreach.max_check_interval_min",
    "proactive_outreach.max_silence_min",
    "proactive_outreach.ambiguous_hold_min",
    "proactive_outreach.surge_min_prob",
    "proactive_outreach.surge_max_prob",
)
_OUTREACH_LLM_SOURCES = (
    "classifier.timing_proactive",
    "classifier.outreach_extract",
    "classifier.outreach_judge",
    "classifier.outreach_generate",
    "classifier.reply",
)
_LIVE_DRY_RUN_QUALITY_BLOCK_REASONS = frozenset({
    "insufficient_sources",
    "empty_draft",
    "unverified_url",
    "draft_budget_too_small",
})


class ProactiveSettingUpdate(BaseModel):
    value: Any


class ProactiveRunOnceRequest(BaseModel):
    mode: Literal["due", "check"] = "due"


class ProactiveSimulationRequest(BaseModel):
    mode: Literal["scripted", "live_dry_run"] = "scripted"
    user_id: str = ""


def _parse_json_object(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _target_fingerprint(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def _redact_super_user_value(value: object) -> object:
    targets = sorted(get_super_user_ids(), key=len, reverse=True)

    def redact(item: object) -> object:
        if isinstance(item, Mapping):
            return {
                str(redact(str(key))): redact(nested)
                for key, nested in item.items()
            }
        if isinstance(item, list):
            return [redact(nested) for nested in item]
        if isinstance(item, tuple):
            return [redact(nested) for nested in item]
        if not isinstance(item, str):
            return item
        result = item
        for target in targets:
            if target:
                result = result.replace(target, "[目标已脱敏]")
        return result

    return redact(value)


def _classify_live_dry_run_result(result: object) -> tuple[bool, bool]:
    """按正向契约判定执行是否正常，以及是否形成完整候选。"""

    if not isinstance(result, Mapping):
        return False, False
    status = result.get("status")
    would_publish = result.get("would_publish")
    if status == "candidate":
        message = result.get("message")
        candidate_available = (
            would_publish is True
            and isinstance(message, str)
            and bool(strip_think_blocks(message).strip())
        )
        return candidate_available, candidate_available
    if status == "no_candidate":
        return would_publish is False, False
    if status == "research_blocked":
        reason_code = result.get("reason_code")
        execution_ok = (
            would_publish is False
            and isinstance(reason_code, str)
            and reason_code in _LIVE_DRY_RUN_QUALITY_BLOCK_REASONS
        )
        return execution_ok, False
    return False, False


def _setting_value(defn: SettingDef, raw_value: object) -> object:
    try:
        if defn.value_type == "bool":
            if isinstance(raw_value, bool):
                return raw_value
            return str(raw_value).lower() in {"1", "true", "yes", "on"}
        if defn.value_type == "int":
            value = int(raw_value)
            if defn.min_value is not None and value < defn.min_value:
                raise HTTPException(status_code=400, detail=f"Min: {defn.min_value}")
            if defn.max_value is not None and value > defn.max_value:
                raise HTTPException(status_code=400, detail=f"Max: {defn.max_value}")
            return value
        if defn.value_type == "float":
            value = float(raw_value)
            if defn.min_value is not None and value < defn.min_value:
                raise HTTPException(status_code=400, detail=f"Min: {defn.min_value}")
            if defn.max_value is not None and value > defn.max_value:
                raise HTTPException(status_code=400, detail=f"Max: {defn.max_value}")
            return value
        return str(raw_value)
    except HTTPException:
        raise
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _setting_payload(key: str, values: dict[str, object]) -> dict[str, Any]:
    defn = SETTING_DEFS[key]
    value = values.get(key, defn.default)
    return {
        "key": key,
        "value": None if defn.sensitive else value,
        "display_value": "****" if defn.sensitive else str(value),
        "default": defn.default,
        "value_type": defn.value_type,
        "category": defn.category,
        "description": defn.description,
        "restart_required": defn.restart_required,
        "dangerous": defn.dangerous,
        "sensitive": defn.sensitive,
        "min_value": defn.min_value,
        "max_value": defn.max_value,
    }


def _outreach_log_dict(row: ProactiveOutreachLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "target_fingerprint": _target_fingerprint(row.user_id),
        "grounding": _redact_super_user_value(_parse_json_object(row.grounding_json)),
        "judge_should": bool(row.judge_should),
        "judge_reason": _redact_super_user_value(row.judge_reason or ""),
        "next_check_at": _iso(row.next_check_at),
        "next_intent": _redact_super_user_value(row.next_intent or ""),
        "message": _redact_super_user_value(row.message or ""),
        "status": row.status or "",
        "forced": bool(row.forced),
        "created_at": _iso(row.created_at),
    }


def _llm_log_dict(row: LLMApiRequestLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "trace_id": row.trace_id or "",
        "run_id": row.run_id or "",
        "source": row.source or "",
        "provider": row.provider or "",
        "model": row.model or "",
        "status": row.status or "",
        "response_status": row.response_status or 0,
        "request_preview": str(_redact_super_user_value(str(row.request_preview or "")[:500])),
        "response_preview": str(_redact_super_user_value(str(row.response_preview or "")[:500])),
        "error": str(_redact_super_user_value(str(row.error or "")[:500])),
        "latency_ms": int(row.latency_ms or 0),
        "created_at": _iso(row.created_at),
        "finished_at": _iso(row.finished_at),
    }


def _status_counts(db: Session) -> dict[str, Any]:
    rows = (
        db.query(ProactiveOutreachLog.status, func.count(ProactiveOutreachLog.id))
        .group_by(ProactiveOutreachLog.status)
        .all()
    )
    by_status = {str(status or "unknown"): int(count or 0) for status, count in rows}
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
    }


def _assert_managed_setting(key: str) -> SettingDef:
    if key not in _MANAGED_SETTING_KEYS:
        raise HTTPException(status_code=400, detail="Setting is not managed here")
    defn = SETTING_DEFS.get(key)
    if defn is None:
        raise HTTPException(status_code=400, detail=f"Unknown setting: {key}")
    return defn


@router.get("/status")
def proactive_outreach_status(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    values = settings.all_values()
    latest_logs = (
        db.query(ProactiveOutreachLog)
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .limit(20)
        .all()
    )
    llm_logs = (
        db.query(LLMApiRequestLog)
        .filter(LLMApiRequestLog.source.in_(_OUTREACH_LLM_SOURCES))
        .order_by(LLMApiRequestLog.created_at.desc(), LLMApiRequestLog.id.desc())
        .limit(20)
        .all()
    )
    super_user_ids = get_super_user_ids()
    return {
        "enabled": bool(values.get("proactive_outreach.enabled", False)),
        "super_user_configured": bool(super_user_ids),
        "super_user_count": len(super_user_ids),
        "settings": [_setting_payload(key, values) for key in _MANAGED_SETTING_KEYS],
        "stats": _status_counts(db),
        "latest_logs": [_outreach_log_dict(row) for row in latest_logs],
        "llm_logs": [_llm_log_dict(row) for row in llm_logs],
        "version": settings.version,
    }


@router.get("/logs")
def proactive_outreach_logs(
    status: str = "",
    target_fingerprint: str = "",
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    query = db.query(ProactiveOutreachLog)
    if status:
        query = query.filter(ProactiveOutreachLog.status == status)
    normalized_fingerprint = str(target_fingerprint or "").strip().lower()
    if normalized_fingerprint:
        matching_targets = [
            user_id
            for user_id in get_super_user_ids()
            if _target_fingerprint(user_id) == normalized_fingerprint
        ]
        if matching_targets:
            query = query.filter(ProactiveOutreachLog.user_id.in_(matching_targets))
        else:
            query = query.filter(ProactiveOutreachLog.id == -1)
    total = query.count()
    items = (
        query.order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [_outreach_log_dict(row) for row in items],
        "page": page,
        "limit": limit,
    }


@router.put("/settings/{key:path}")
def update_proactive_outreach_setting(
    key: str,
    body: ProactiveSettingUpdate,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    defn = _assert_managed_setting(key)
    value = _setting_value(defn, body.value)
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row is None:
        row = SystemSetting(key=key, value=str(value), description=defn.description)
        db.add(row)
    else:
        row.value = str(value)
    db.commit()
    settings.invalidate()
    audit(
        db,
        "update_proactive_outreach_setting",
        "setting",
        key,
        {"value": str(value)},
        ip_address=client_ip(request),
    )
    return {
        "key": key,
        "value": value,
        "restart_required": defn.restart_required,
        "version": settings.version,
    }


@router.post("/settings/reload")
def reload_proactive_outreach_settings(
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    settings.invalidate()
    audit(
        db,
        "reload_proactive_outreach_settings",
        "setting",
        "proactive_outreach",
        {},
        ip_address=client_ip(request),
    )
    return {"ok": True, "version": settings.version}


@router.post("/run-once")
async def proactive_outreach_run_once(
    body: ProactiveRunOnceRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    user_ids = sorted(get_super_user_ids())
    if not user_ids:
        raise HTTPException(status_code=400, detail="No superuser configured")
    user_id = user_ids[0]

    if body.mode == "check":
        result = await run_outreach_once(user_id, db=db)
    else:
        result = await run_outreach_due_once(user_id, db=db)
    return {
        "ok": True,
        "target_fingerprint": _target_fingerprint(user_id),
        "mode": body.mode,
        "result": _redact_super_user_value(result),
    }


@router.post("/simulate")
async def proactive_outreach_simulate(
    body: ProactiveSimulationRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """运行独立脚本模拟或真实模型 dry-run；两者都没有发布出口。"""

    if body.mode == "scripted":
        from core.proactive_simulation import run_accelerated_simulation

        report = await run_accelerated_simulation()
        execution_ok = bool(report.get("passed"))
        return {
            "ok": execution_ok,
            "request_ok": True,
            "execution_ok": execution_ok,
            "candidate_available": False,
            "mode": body.mode,
            "report": report,
        }

    user_id = (body.user_id or "").strip()
    if not user_id:
        user_ids = sorted(get_super_user_ids())
        if not user_ids:
            raise HTTPException(status_code=400, detail="No superuser configured")
        user_id = user_ids[0]
    result = await run_outreach_dry_run_once(user_id, db=db)
    execution_ok, candidate_available = _classify_live_dry_run_result(result)
    return {
        "ok": execution_ok,
        "request_ok": True,
        "execution_ok": execution_ok,
        "candidate_available": candidate_available,
        "mode": body.mode,
        "target_fingerprint": _target_fingerprint(user_id),
        "result": _redact_super_user_value(result),
    }
