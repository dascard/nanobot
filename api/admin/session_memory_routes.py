"""Rolling session summary 管理接口。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from app.session_memory.rolling_summary import (
    archive_active_summaries_for_session,
    get_best_session_summary,
    maybe_rollup_session_summary,
)
from app.session_memory.windowing import (
    load_latest_raw_window,
    load_pending_for_summary_turns,
    raw_window_limits,
)
from app.session_memory.jobs import enqueue_session_summary_job, retry_session_summary_job
from app.session_memory.admin_browser import AdminSessionMemoryBrowser, _session_aliases
from core.daily_digest import generate_daily_digest_for_date
from core.database import ConversationTurn, MemoryDigest, RollingSessionSummary, SessionSummaryJob, User, get_db

router = APIRouter()


class RollingSummaryRunRequest(BaseModel):
    user_id: str = ""
    chat_type: Literal["private", "group"] = "private"
    force: bool = False
    dry_run: bool = False
    current_user_input: str = ""


class RollingSummaryEnqueueRequest(BaseModel):
    user_id: str = ""
    chat_type: Literal["private", "group"] = "private"
    force: bool = False
    summary_id: int | None = None


class MemoryDigestRunAdminRequest(BaseModel):
    target_date: str = ""
    user_id: str = ""
    force: bool = True


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


def _job_to_dict(row: SessionSummaryJob | None) -> dict:
    if row is None:
        return {}
    return {
        "id": row.id,
        "session_id": row.session_id,
        "user_id": row.user_id,
        "chat_type": row.chat_type,
        "covered_from_turn_id": row.covered_from_turn_id,
        "covered_until_turn_id": row.covered_until_turn_id,
        "source_turn_ids_json": row.source_turn_ids_json,
        "previous_summary_id": row.previous_summary_id,
        "fallback_summary_id": row.fallback_summary_id,
        "result_summary_id": row.result_summary_id,
        "status": row.status,
        "retry_count": row.retry_count,
        "max_retry": row.max_retry,
        "next_retry_at": row.next_retry_at.isoformat() if row.next_retry_at else "",
        "locked_by": row.locked_by,
        "locked_at": row.locked_at.isoformat() if row.locked_at else "",
        "error": row.error,
        "stable_hash": row.stable_hash,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _load_turns_by_ids(db: Session, turn_ids: list[int]) -> list[ConversationTurn]:
    if not turn_ids:
        return []
    rows = db.query(ConversationTurn).filter(ConversationTurn.id.in_(turn_ids)).all()
    by_id = {int(row.id): row for row in rows}
    return [by_id[turn_id] for turn_id in turn_ids if turn_id in by_id]


def _turn_ids_from_summary(row: RollingSessionSummary) -> list[int]:
    import json

    try:
        raw = json.loads(row.source_turn_ids_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    result: list[int] = []
    for item in raw:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _build_rollup_inputs(
    db: Session,
    *,
    session_id: str,
    user_id: str,
    chat_type: str,
):
    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    history_clear_at = user.history_clear_at if user else None
    active_summary = get_best_session_summary(db, session_id, after_clear_at=history_clear_at)
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


@router.get("/session-memory/sessions")
def list_session_memory_sessions(
    session_limit: int = 50,
    cursor: str = "",
    kind: str = "all",
    include_system_sessions: bool = False,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    return AdminSessionMemoryBrowser(db).list_sessions(
        session_limit=session_limit,
        cursor=cursor,
        kind=kind,
        include_system_sessions=include_system_sessions,
    )


@router.get("/session-memory/sessions/{session_id}/summaries")
def list_session_memory_summaries(
    session_id: str,
    summary_limit_per_session: int = 20,
    include_content: bool = False,
    include_archived: bool = True,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    return AdminSessionMemoryBrowser(db).list_summaries(
        session_id,
        summary_limit_per_session=summary_limit_per_session,
        include_content=include_content,
        include_archived=include_archived,
    )


@router.get("/session-memory/sessions/{session_id}/digests")
def list_session_memory_digests(
    session_id: str,
    digest_limit_per_session: int = 50,
    include_content: bool = False,
    date_start: str = "",
    date_end: str = "",
    level: int = -1,
    parent_id: int | None = None,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    return AdminSessionMemoryBrowser(db).list_digests(
        session_id,
        digest_limit_per_session=digest_limit_per_session,
        include_content=include_content,
        date_start=date_start,
        date_end=date_end,
        level=level,
        parent_id=parent_id,
    )


@router.post("/session-memory/{session_id}/digests/run")
def run_session_memory_digest(
    session_id: str,
    body: MemoryDigestRunAdminRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    aliases = _session_aliases(session_id)
    latest = (
        db.query(MemoryDigest)
        .filter(MemoryDigest.session_id.in_(aliases))
        .order_by(MemoryDigest.digest_date.desc(), MemoryDigest.id.desc())
        .first()
    )
    target_date = str(body.target_date or "").strip()
    if not target_date:
        target_date = str(getattr(latest, "digest_date", "") or "").strip()
    if not target_date:
        raise HTTPException(status_code=404, detail="latest memory digest date not found")

    selected_user_id = str(body.user_id or getattr(latest, "user_id", "") or session_id).strip()
    created = generate_daily_digest_for_date(
        target_date=target_date,
        user_id=selected_user_id or None,
        session_id=session_id,
        force=bool(body.force),
    )
    return {
        "session_id": session_id,
        "target_date": target_date,
        "user_id": selected_user_id,
        "force": bool(body.force),
        "created_sessions": created,
    }


@router.get("/session-memory/{session_id}/rolling-summary")
def get_rolling_summary(
    session_id: str,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    active = get_best_session_summary(db, session_id)
    rows = (
        db.query(RollingSessionSummary)
        .filter(RollingSessionSummary.session_id == session_id)
        .order_by(RollingSessionSummary.id.desc())
        .limit(20)
        .all()
    )
    jobs = (
        db.query(SessionSummaryJob)
        .filter(SessionSummaryJob.session_id == session_id)
        .order_by(SessionSummaryJob.id.desc())
        .limit(20)
        .all()
    )
    return {
        "session_id": session_id,
        "active_summary": _summary_to_dict(active),
        "items": [_summary_to_dict(row) for row in rows],
        "jobs": [_job_to_dict(row) for row in jobs],
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


@router.post("/session-memory/{session_id}/rolling-summary/enqueue-llm")
def enqueue_llm_summary(
    session_id: str,
    db: Session = Depends(get_db),
    body: RollingSummaryEnqueueRequest | None = None,
    _auth=Depends(verify_admin),
):
    body = body or RollingSummaryEnqueueRequest()
    base_summary = None
    if body.summary_id:
        base_summary = (
            db.query(RollingSessionSummary)
            .filter(
                RollingSessionSummary.id == int(body.summary_id),
                RollingSessionSummary.session_id == session_id,
            )
            .first()
        )
        if base_summary is None:
            raise HTTPException(status_code=404, detail="rolling summary not found")
    else:
        base_summary = (
            db.query(RollingSessionSummary)
            .filter(
                RollingSessionSummary.session_id == session_id,
                RollingSessionSummary.status == "active",
                RollingSessionSummary.summary_kind == "deterministic_fallback",
            )
            .order_by(RollingSessionSummary.id.desc())
            .first()
        )
        if base_summary is None:
            base_summary = get_best_session_summary(db, session_id)
    if base_summary is None:
        raise HTTPException(status_code=404, detail="active rolling summary not found")

    turn_ids = _turn_ids_from_summary(base_summary)
    turns = _load_turns_by_ids(db, turn_ids)
    if not turns:
        raise HTTPException(status_code=409, detail="rolling summary source turns are missing")

    previous = None
    if (base_summary.summary_kind or "") == "deterministic_fallback":
        previous = (
            db.query(RollingSessionSummary)
            .filter(
                RollingSessionSummary.session_id == session_id,
                RollingSessionSummary.status == "active",
                RollingSessionSummary.summary_kind.in_(("llm_episode", "llm_summary")),
                RollingSessionSummary.id != base_summary.id,
            )
            .order_by(RollingSessionSummary.id.desc())
            .first()
        )
    job, created = enqueue_session_summary_job(
        db,
        session_id=session_id,
        user_id=body.user_id or base_summary.user_id or "",
        chat_type=body.chat_type or base_summary.chat_type or "private",
        pending_turns=turns,
        previous_summary=previous,
        fallback_summary=base_summary,
        force=bool(body.force),
    )
    db.commit()
    return {
        "session_id": session_id,
        "source_summary_id": int(base_summary.id or 0),
        "source_summary_kind": base_summary.summary_kind or "deterministic_fallback",
        "created": created,
        "job": _job_to_dict(job),
    }


@router.post("/session-memory/jobs/{job_id}/retry")
def retry_llm_summary_job(
    job_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        job = retry_session_summary_job(db, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return {"job": _job_to_dict(job)}
