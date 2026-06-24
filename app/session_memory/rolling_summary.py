"""Rolling Session Summary 读写与触发判断。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.session_memory import config
from app.session_memory.summarizer import build_rolling_summary_payload, render_summary_text
from app.session_memory.windowing import estimate_tokens, should_rollup
from core.database import ConversationTurn, RollingSessionSummary


@dataclass
class RollupResult:
    summary: RollingSessionSummary | None = None
    summary_text: str = ""
    pending_turn_ids: list[int] = field(default_factory=list)
    recent_raw_turn_ids: list[int] = field(default_factory=list)
    skipped_reason: str = ""
    error: str = ""
    dry_run: bool = False
    threshold: dict[str, Any] = field(default_factory=dict)
    summary_job_id: int = 0


def get_active_summary(
    db: Session,
    session_id: str,
    *,
    after_clear_at: datetime | None = None,
) -> RollingSessionSummary | None:
    row = (
        db.query(RollingSessionSummary)
        .filter(
            RollingSessionSummary.session_id == session_id,
            RollingSessionSummary.status == "active",
        )
        .order_by(RollingSessionSummary.id.desc())
        .first()
    )
    if row is None:
        return None
    if after_clear_at and row.updated_at and row.updated_at <= after_clear_at:
        row.status = "archived"
        row.updated_at = datetime.now()
        db.flush()
        return None
    return row


def get_best_session_summary(
    db: Session,
    session_id: str,
    *,
    after_clear_at: datetime | None = None,
) -> RollingSessionSummary | None:
    """返回运行时应注入的最佳 active summary。

    LLM 摘要质量更高，优先级高于 deterministic fallback；fallback 只作为
    LLM 摘要缺失或失败时的同步兜底。
    """
    rows = (
        db.query(RollingSessionSummary)
        .filter(
            RollingSessionSummary.session_id == session_id,
            RollingSessionSummary.status == "active",
        )
        .order_by(RollingSessionSummary.id.desc())
        .all()
    )
    if not rows:
        return None

    valid: list[RollingSessionSummary] = []
    for row in rows:
        if after_clear_at and row.updated_at and row.updated_at <= after_clear_at:
            row.status = "archived"
            row.updated_at = datetime.now()
            continue
        valid.append(row)
    if len(valid) != len(rows):
        db.flush()
    if not valid:
        return None

    llm_rows = [
        row for row in valid
        if (row.summary_kind or "") in {"llm_episode", "llm_summary"}
    ]
    fallback_rows = [
        row for row in valid
        if (row.summary_kind or "") == "deterministic_fallback"
    ]
    best_llm = max(
        llm_rows,
        key=lambda row: (int(row.covered_until_turn_id or 0), int(row.id or 0)),
        default=None,
    )
    best_fallback = max(
        fallback_rows,
        key=lambda row: (int(row.covered_until_turn_id or 0), int(row.id or 0)),
        default=None,
    )
    if best_llm and (
        best_fallback is None
        or int(best_llm.covered_until_turn_id or 0) >= int(best_fallback.covered_until_turn_id or 0)
    ):
        return best_llm
    if best_fallback:
        return best_fallback
    return valid[0]


def archive_active_summaries_for_session(db: Session, session_id: str) -> int:
    rows = (
        db.query(RollingSessionSummary)
        .filter(
            RollingSessionSummary.session_id == session_id,
            RollingSessionSummary.status == "active",
        )
        .all()
    )
    for row in rows:
        row.status = "archived"
        row.updated_at = datetime.now()
    if rows:
        db.flush()
    return len(rows)


def archive_active_summaries_for_user(db: Session, user_id: str) -> int:
    rows = (
        db.query(RollingSessionSummary)
        .filter(
            RollingSessionSummary.user_id == user_id,
            RollingSessionSummary.status == "active",
        )
        .all()
    )
    for row in rows:
        row.status = "archived"
        row.updated_at = datetime.now()
    if rows:
        db.flush()
    return len(rows)


def audit_rolling_summary(
    *,
    summary_json: dict[str, Any],
    pending_turn_ids: list[int],
    recent_raw_turn_ids: list[int],
    current_user_input: str = "",
) -> tuple[bool, list[str]]:
    issues: list[str] = []
    text = json.dumps(summary_json, ensure_ascii=False)
    recent_ids = {int(turn_id) for turn_id in recent_raw_turn_ids}
    pending_ids = {int(turn_id) for turn_id in pending_turn_ids}
    if any(f"turn_id={turn_id}" in text for turn_id in recent_ids - pending_ids):
        issues.append("summary_mentions_recent_raw_turn")
    current = str(current_user_input or "").strip()
    if current and len(current) >= 12 and current[:80] in text:
        issues.append("summary_contains_current_user_input")
    if len(str(summary_json.get("summary") or "")) > config.ROLLING_SUMMARY_MAX_CHARS:
        issues.append("summary_too_long")
    if "<user_input>" in text or "</user_input>" in text:
        issues.append("contains_user_input_tag")
    if "必须调用" in text and "工具" in text:
        issues.append("possible_tool_contract_leak")
    return not issues, issues


def save_new_active_summary(
    db: Session,
    *,
    old_summary: RollingSessionSummary | None,
    session_id: str,
    user_id: str,
    chat_type: str,
    summary_json: dict[str, Any],
    pending_turns: Sequence[ConversationTurn],
    raw_window_start_turn_id: int,
    model: str,
    prompt_sha256: str,
) -> RollingSessionSummary:
    if not pending_turns:
        raise ValueError("pending_turns is required")
    archive_active_summaries_for_session(db, session_id)

    summary_text = render_summary_text(summary_json)
    source_turn_ids = [int(turn.id) for turn in pending_turns]
    quality = summary_json.get("quality")
    quality = quality if isinstance(quality, dict) else {}
    issues = quality.get("issues")
    issues = issues if isinstance(issues, list) else []

    summary_kind = "deterministic_fallback"
    stable_hash = hashlib.sha256(
        json.dumps({
            "kind": summary_kind,
            "session_id": session_id,
            "source_turn_ids": source_turn_ids,
            "summary": summary_json.get("summary", ""),
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    row = RollingSessionSummary(
        session_id=session_id,
        user_id=user_id or "",
        chat_type=chat_type or "private",
        status="active",
        summary_kind=summary_kind,
        summary_text=summary_text,
        summary_json=json.dumps(summary_json, ensure_ascii=False),
        covered_from_turn_id=source_turn_ids[0],
        covered_until_turn_id=source_turn_ids[-1],
        source_turn_ids_json=json.dumps(source_turn_ids, ensure_ascii=False),
        source_turn_count=len(source_turn_ids),
        source_token_estimate=sum(estimate_tokens(turn.content or "") for turn in pending_turns),
        source_char_count=sum(len(turn.content or "") for turn in pending_turns),
        raw_window_start_turn_id=int(raw_window_start_turn_id or 0),
        quality_score=float(quality.get("score") or 0.0),
        issues_json=json.dumps(issues, ensure_ascii=False),
        model=model or "",
        prompt_sha256=prompt_sha256 or "",
        stable_hash=stable_hash,
        meta_json=json.dumps({
            "schema_version": 1,
            "created_by": "rolling_session_summary",
            "summary_kind": summary_kind,
        }, ensure_ascii=False),
    )
    db.add(row)
    db.flush()
    return row


def maybe_rollup_session_summary(
    db: Session,
    *,
    session_id: str,
    user_id: str = "",
    chat_type: str = "private",
    active_summary: RollingSessionSummary | None,
    pending_turns: Sequence[ConversationTurn],
    recent_raw_turn_ids: Sequence[int],
    raw_window_start_turn_id: int,
    current_user_input: str = "",
    force: bool = False,
    dry_run: bool = False,
) -> RollupResult:
    pending_ids = [int(turn.id) for turn in pending_turns]
    result = RollupResult(
        pending_turn_ids=pending_ids,
        recent_raw_turn_ids=[int(x) for x in recent_raw_turn_ids],
        dry_run=dry_run,
    )
    if not config.ROLLING_SUMMARY_ENABLED and not force:
        result.skipped_reason = "disabled"
        return result

    ok, threshold = should_rollup(pending_turns, chat_type=chat_type, force=force)
    result.threshold = threshold
    if not ok:
        result.skipped_reason = str(threshold.get("reason") or "below_threshold")
        return result

    old_covered = int(getattr(active_summary, "covered_until_turn_id", 0) or 0)
    fresh = get_active_summary(db, session_id)
    if fresh is not None and int(fresh.covered_until_turn_id or 0) > old_covered:
        result.summary = fresh
        result.skipped_reason = "already_rolled"
        return result

    payload = build_rolling_summary_payload(
        previous_summary=active_summary,
        pending_turns=pending_turns,
    )
    audit_ok, issues = audit_rolling_summary(
        summary_json=payload,
        pending_turn_ids=pending_ids,
        recent_raw_turn_ids=result.recent_raw_turn_ids,
        current_user_input=current_user_input,
    )
    if not audit_ok:
        result.error = ",".join(issues)
        return result

    prompt_sha256 = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    summary_text = render_summary_text(payload)
    result.summary_text = summary_text
    if dry_run:
        return result

    result.summary = save_new_active_summary(
        db,
        old_summary=active_summary,
        session_id=session_id,
        user_id=user_id,
        chat_type=chat_type,
        summary_json=payload,
        pending_turns=pending_turns,
        raw_window_start_turn_id=raw_window_start_turn_id,
        model="deterministic",
        prompt_sha256=prompt_sha256,
    )
    if config.SESSION_SUMMARY_LLM_ENABLED:
        from app.session_memory.jobs import enqueue_session_summary_job

        job, _created = enqueue_session_summary_job(
            db,
            session_id=session_id,
            user_id=user_id,
            chat_type=chat_type,
            pending_turns=pending_turns,
            previous_summary=active_summary,
            fallback_summary=result.summary,
        )
        result.summary_job_id = int(job.id or 0)
    return result
