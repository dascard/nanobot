"""群聊 ChatLog Rolling Summary 的确定性发现与入队。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.session_memory import config
from app.session_memory.jobs import enqueue_session_summary_job
from app.session_memory.rolling_summary import (
    SUMMARY_SOURCE_CHAT_LOG,
    get_best_session_summary,
    summary_covered_until,
)
from app.session_memory.windowing import (
    estimate_tokens,
    is_context_eligible_chat_log,
)
from core.db.models.chat import ChatLog, User
from core.db.models.session_memory import RollingSessionSummary, SessionSummaryJob
from core.time_utils import db_now_naive, to_db_naive


@dataclass(frozen=True, slots=True)
class GroupRollupDecision:
    """一次群聊摘要触发判断的完整数据库快照。"""

    session_id: str
    pending_rows: tuple[ChatLog, ...]
    protected_rows: tuple[ChatLog, ...]
    pending_tokens: int
    protected_tokens: int
    force: bool
    should_enqueue: bool
    reason: str
    latest_message_at: datetime | None
    previous_summary_id: int


def group_chatlog_token_cost(row: ChatLog) -> int:
    """估算群消息进入 canonical Prompt 后的 token 成本。"""

    sender = str(getattr(row, "sender_name", "") or "")
    message_id = str(getattr(row, "message_id", "") or "")
    content = str(getattr(row, "content", "") or "")
    # 时间、字段标签和换行是稳定开销；阈值只用于量级判断。
    return max(1, estimate_tokens(content) + estimate_tokens(sender + message_id) + 18)


def _chatlog_query(
    db: Session,
    *,
    session_id: str,
    after_source_id: int,
    after_clear_at: datetime | None,
):
    query = db.query(ChatLog).filter(
        ChatLog.session_id == session_id,
        ChatLog.id > int(after_source_id or 0),
        ChatLog.role.in_(("ambient", "user", "assistant")),
    )
    if after_clear_at is not None:
        query = query.filter(ChatLog.created_at > after_clear_at)
    return query


def _load_protected_tail(
    db: Session,
    *,
    session_id: str,
    after_source_id: int,
    after_clear_at: datetime | None,
) -> tuple[tuple[ChatLog, ...], int, datetime | None]:
    selected_desc: list[ChatLog] = []
    token_count = 0
    latest_message_at: datetime | None = None
    rows = (
        _chatlog_query(
            db,
            session_id=session_id,
            after_source_id=after_source_id,
            after_clear_at=after_clear_at,
        )
        .order_by(ChatLog.id.desc())
        .yield_per(500)
    )
    for row in rows:
        eligible, _reason = is_context_eligible_chat_log(row)
        if not eligible:
            continue
        if latest_message_at is None:
            latest_message_at = row.created_at
        selected_desc.append(row)
        token_count += group_chatlog_token_cost(row)
        if token_count >= config.GROUP_RAW_WINDOW_MAX_TOKENS:
            break
    return tuple(reversed(selected_desc)), token_count, latest_message_at


def _load_pending_chunk(
    db: Session,
    *,
    session_id: str,
    after_source_id: int,
    before_source_id: int,
    after_clear_at: datetime | None,
) -> tuple[tuple[ChatLog, ...], int]:
    if before_source_id <= 0:
        return (), 0
    selected: list[ChatLog] = []
    token_count = 0
    rows = (
        _chatlog_query(
            db,
            session_id=session_id,
            after_source_id=after_source_id,
            after_clear_at=after_clear_at,
        )
        .filter(ChatLog.id < int(before_source_id))
        .order_by(ChatLog.id.asc())
        .yield_per(500)
    )
    for row in rows:
        eligible, _reason = is_context_eligible_chat_log(row)
        if not eligible:
            continue
        selected.append(row)
        token_count += group_chatlog_token_cost(row)
        if token_count >= config.GROUP_ROLLING_JOB_MAX_TOKENS:
            break
    return tuple(selected), token_count


def build_group_rollup_decision(
    db: Session,
    *,
    session_id: str,
    now: datetime | None = None,
) -> GroupRollupDecision:
    """只读判断一个群是否应入队，不在回复链路调用模型。"""

    checked_at = to_db_naive(now) or db_now_naive()
    user = db.get(User, session_id)
    history_clear_at = user.history_clear_at if user is not None else None
    active_summary = get_best_session_summary(
        db,
        session_id,
        source_type=SUMMARY_SOURCE_CHAT_LOG,
        allow_fallback=False,
        after_clear_at=history_clear_at,
        mutate_stale=False,
    )
    covered_until = summary_covered_until(active_summary)
    protected_rows, protected_tokens, latest_message_at = _load_protected_tail(
        db,
        session_id=session_id,
        after_source_id=covered_until,
        after_clear_at=history_clear_at,
    )
    raw_start_id = int(protected_rows[0].id or 0) if protected_rows else 0
    pending_rows, pending_tokens = _load_pending_chunk(
        db,
        session_id=session_id,
        after_source_id=covered_until,
        before_source_id=raw_start_id,
        after_clear_at=history_clear_at,
    )

    force = pending_tokens >= config.GROUP_ROLLING_FORCE_TOKENS
    if not pending_rows:
        reason = "empty_pending"
        should_enqueue = False
    elif force:
        reason = "force_threshold"
        should_enqueue = True
    elif pending_tokens < config.GROUP_ROLLING_MIN_TOKENS:
        reason = "below_threshold"
        should_enqueue = False
    else:
        cooldown_ready = (
            active_summary is None
            or active_summary.updated_at is None
            or active_summary.updated_at
            <= checked_at
            - timedelta(seconds=config.GROUP_ROLLING_COOLDOWN_SECONDS)
        )
        idle_ready = (
            latest_message_at is not None
            and latest_message_at
            <= checked_at - timedelta(seconds=config.GROUP_ROLLING_IDLE_SECONDS)
        )
        if not cooldown_ready:
            reason = "cooldown"
            should_enqueue = False
        elif not idle_ready:
            reason = "group_active"
            should_enqueue = False
        else:
            reason = "idle_threshold"
            should_enqueue = True

    return GroupRollupDecision(
        session_id=session_id,
        pending_rows=pending_rows,
        protected_rows=protected_rows,
        pending_tokens=pending_tokens,
        protected_tokens=protected_tokens,
        force=force,
        should_enqueue=should_enqueue,
        reason=reason,
        latest_message_at=latest_message_at,
        previous_summary_id=int(getattr(active_summary, "id", 0) or 0),
    )


def _has_inflight_job(db: Session, session_id: str) -> bool:
    return (
        db.query(SessionSummaryJob.id)
        .filter(
            SessionSummaryJob.session_id == session_id,
            SessionSummaryJob.source_type == SUMMARY_SOURCE_CHAT_LOG,
            SessionSummaryJob.status.in_(("pending", "running")),
        )
        .first()
        is not None
    )


def _has_failed_same_coverage(
    db: Session,
    *,
    session_id: str,
    first_source_id: int,
    last_source_id: int,
) -> bool:
    return (
        db.query(SessionSummaryJob.id)
        .filter(
            SessionSummaryJob.session_id == session_id,
            SessionSummaryJob.source_type == SUMMARY_SOURCE_CHAT_LOG,
            SessionSummaryJob.covered_from_source_id == first_source_id,
            SessionSummaryJob.covered_until_source_id == last_source_id,
            SessionSummaryJob.status == "failed",
        )
        .first()
        is not None
    )


def _candidate_group_session_ids(
    db: Session,
    *,
    limit: int,
) -> list[str]:
    rows = (
        db.query(
            ChatLog.session_id,
            func.max(ChatLog.id).label("latest_id"),
        )
        .filter(
            ChatLog.session_id.like("group_%"),
            ChatLog.role.in_(("ambient", "user", "assistant")),
        )
        .group_by(ChatLog.session_id)
        .order_by(func.max(ChatLog.id).desc())
        .limit(max(1, int(limit)))
        .all()
    )
    return [str(row.session_id) for row in rows if str(row.session_id or "").strip()]


def discover_group_summary_jobs(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """扫描群聊并按确定性条件入队；未命中时绝不调用模型。"""

    stats = {
        "scanned": 0,
        "created": 0,
        "inflight": 0,
        "failed_same_coverage": 0,
        "below_threshold": 0,
    }
    session_ids = _candidate_group_session_ids(
        db,
        limit=limit or config.GROUP_SUMMARY_DISCOVERY_BATCH_SIZE,
    )
    for session_id in session_ids:
        stats["scanned"] += 1
        if _has_inflight_job(db, session_id):
            stats["inflight"] += 1
            continue
        decision = build_group_rollup_decision(
            db,
            session_id=session_id,
            now=now,
        )
        if not decision.should_enqueue:
            stats["below_threshold"] += 1
            continue
        first_source_id = int(decision.pending_rows[0].id)
        last_source_id = int(decision.pending_rows[-1].id)
        if _has_failed_same_coverage(
            db,
            session_id=session_id,
            first_source_id=first_source_id,
            last_source_id=last_source_id,
        ):
            stats["failed_same_coverage"] += 1
            continue

        previous_summary = (
            db.get(RollingSessionSummary, decision.previous_summary_id)
            if decision.previous_summary_id
            else None
        )
        job, created = enqueue_session_summary_job(
            db,
            session_id=session_id,
            user_id=session_id,
            chat_type="group",
            pending_turns=decision.pending_rows,
            previous_summary=previous_summary,
            fallback_summary=None,
            source_type=SUMMARY_SOURCE_CHAT_LOG,
            recent_raw_turn_ids=[
                int(row.id) for row in decision.protected_rows
            ],
            current_user_input="",
        )
        if not created:
            continue
        try:
            meta = json.loads(job.meta_json or "{}")
            if not isinstance(meta, dict):
                meta = {}
        except (TypeError, json.JSONDecodeError):
            meta = {}
        meta["group_rollup"] = {
            "trigger": decision.reason,
            "force": decision.force,
            "pending_tokens": decision.pending_tokens,
            "protected_tokens": decision.protected_tokens,
            "latest_message_at": (
                decision.latest_message_at.isoformat()
                if decision.latest_message_at is not None
                else ""
            ),
        }
        job.meta_json = json.dumps(meta, ensure_ascii=False)
        stats["created"] += 1
    db.flush()
    return stats
