"""Rolling session summary 管理接口。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from app.session_memory.rolling_summary import (
    archive_active_summaries_for_session,
    get_active_summary,
    maybe_rollup_session_summary,
)
from app.session_memory.windowing import (
    load_latest_raw_window,
    load_pending_for_summary_turns,
    raw_window_limits,
)
from core.database import RollingSessionSummary, User, get_db

router = APIRouter()


class RollingSummaryRunRequest(BaseModel):
    user_id: str = ""
    chat_type: Literal["private", "group"] = "private"
    force: bool = False
    dry_run: bool = False
    current_user_input: str = ""


def _summary_to_dict(row: RollingSessionSummary | None) -> dict:
    if row is None:
        return {}
    return {
        "id": row.id,
        "session_id": row.session_id,
        "user_id": row.user_id,
        "chat_type": row.chat_type,
        "status": row.status,
        "summary_kind": getattr(row, "summary_kind", "") or "deterministic_fallback",
        "summary_text": row.summary_text,
        "summary_json": row.summary_json,
        "covered_from_turn_id": row.covered_from_turn_id,
        "covered_until_turn_id": row.covered_until_turn_id,
        "source_turn_ids_json": row.source_turn_ids_json,
        "source_turn_count": row.source_turn_count,
        "source_token_estimate": row.source_token_estimate,
        "source_char_count": row.source_char_count,
        "raw_window_start_turn_id": row.raw_window_start_turn_id,
        "quality_score": row.quality_score,
        "issues_json": row.issues_json,
        "model": row.model,
        "prompt_sha256": row.prompt_sha256,
        "llm_status": getattr(row, "llm_status", "") or "",
        "llm_model": getattr(row, "llm_model", "") or "",
        "llm_request_log_id": getattr(row, "llm_request_log_id", None),
        "llm_error": getattr(row, "llm_error", "") or "",
        "retry_count": int(getattr(row, "retry_count", 0) or 0),
        "next_retry_at": row.next_retry_at.isoformat() if getattr(row, "next_retry_at", None) else "",
        "supersedes_summary_id": getattr(row, "supersedes_summary_id", None),
        "stable_hash": getattr(row, "stable_hash", "") or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _build_rollup_inputs(
    db: Session,
    *,
    session_id: str,
    user_id: str,
    chat_type: str,
):
    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    history_clear_at = user.history_clear_at if user else None
    active_summary = get_active_summary(db, session_id, after_clear_at=history_clear_at)
    last_covered_id = int(active_summary.covered_until_turn_id or 0) if active_summary else 0
    max_turns, max_tokens = raw_window_limits(chat_type)
    raw_window, raw_debug = load_latest_raw_window(
        db,
        session_id=session_id,
        chat_type=chat_type,
        max_turns=max_turns,
        max_tokens=max_tokens,
        after_clear_at=history_clear_at,
        after_turn_id=last_covered_id,
    )
    pending, pending_debug = load_pending_for_summary_turns(
        db,
        session_id=session_id,
        last_covered_id=last_covered_id,
        raw_window_start_turn_id=int(raw_debug.get("raw_window_start_turn_id") or 0),
        after_clear_at=history_clear_at,
    )
    eligible_debug = {
        "skipped": (
            list(raw_debug.get("raw_window_skipped") or [])
            + list(pending_debug.get("pending_skipped") or [])
        ),
        "raw_window": raw_debug,
        "pending": pending_debug,
    }
    return active_summary, pending, raw_window, raw_debug, eligible_debug


@router.get("/session-memory/{session_id}/rolling-summary")
def get_rolling_summary(
    session_id: str,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    active = get_active_summary(db, session_id)
    rows = (
        db.query(RollingSessionSummary)
        .filter(RollingSessionSummary.session_id == session_id)
        .order_by(RollingSessionSummary.id.desc())
        .limit(20)
        .all()
    )
    return {
        "session_id": session_id,
        "active_summary": _summary_to_dict(active),
        "items": [_summary_to_dict(row) for row in rows],
    }


@router.post("/session-memory/{session_id}/rolling-summary/run")
def run_rolling_summary(
    session_id: str,
    body: RollingSummaryRunRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    active, pending, raw_window, raw_debug, eligible_debug = _build_rollup_inputs(
        db,
        session_id=session_id,
        user_id=body.user_id,
        chat_type=body.chat_type,
    )
    result = maybe_rollup_session_summary(
        db,
        session_id=session_id,
        user_id=body.user_id,
        chat_type=body.chat_type,
        active_summary=active,
        pending_turns=pending,
        recent_raw_turn_ids=raw_debug.get("raw_window_turn_ids") or [],
        raw_window_start_turn_id=int(raw_debug.get("raw_window_start_turn_id") or 0),
        current_user_input=body.current_user_input,
        force=body.force,
        dry_run=body.dry_run,
    )
    if not body.dry_run and result.summary is not None:
        db.commit()
    else:
        db.rollback()
    return {
        "session_id": session_id,
        "active_summary_id": int(getattr(result.summary, "id", 0) or 0),
        "summary_job_id": int(getattr(result, "summary_job_id", 0) or 0),
        "covered_until_turn_id": int(getattr(result.summary, "covered_until_turn_id", 0) or 0),
        "pending_turn_ids": result.pending_turn_ids,
        "recent_raw_turn_ids": result.recent_raw_turn_ids,
        "raw_window_turn_ids": raw_debug.get("raw_window_turn_ids") or [],
        "summary_text": result.summary_text or str(getattr(result.summary, "summary_text", "") or ""),
        "skipped_reason": result.skipped_reason,
        "error": result.error,
        "dry_run": body.dry_run,
        "eligible_debug": eligible_debug,
        "raw_window_count": len(raw_window),
    }


@router.post("/session-memory/{session_id}/rolling-summary/archive")
def archive_rolling_summary(
    session_id: str,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    archived = archive_active_summaries_for_session(db, session_id)
    db.commit()
    return {"session_id": session_id, "archived": archived}
