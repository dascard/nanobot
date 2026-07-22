"""Rolling Session Summary 读写与触发判断。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.session_memory import config
from app.session_memory.summarizer import build_rolling_summary_payload, render_summary_text
from app.session_memory.windowing import estimate_tokens, should_rollup
from core.db.models.chat import ConversationTurn, User
from core.db.models.session_memory import RollingSessionSummary
from core.time_utils import db_now_naive, to_db_naive


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
    requires_commit: bool = False


class _RollupFenceRejected(RuntimeError):
    """历史清除或来源 turn 已变化，当前 rollup 不得继续。"""


class _RollupHeadChanged(RuntimeError):
    """active summary 已变化，当前 rollup 应让位给新 head。"""


def get_active_summary(
    db: Session,
    session_id: str,
    *,
    after_clear_at: datetime | None = None,
    mutate_stale: bool = True,
) -> RollingSessionSummary | None:
    """读取可消费摘要；过期过滤不得在读路径隐式修改业务状态。"""

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
        return None
    return row


def get_best_session_summary(
    db: Session,
    session_id: str,
    *,
    after_clear_at: datetime | None = None,
    mutate_stale: bool = True,
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
            continue
        valid.append(row)
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


def _enqueue_archived_summary_delete_jobs(
    db: Session,
    rows: Sequence[RollingSessionSummary],
    *,
    reason: str,
) -> None:
    from core.semantic.jobs import enqueue_index_job

    grouped: dict[str, list[RollingSessionSummary]] = {}
    for row in rows:
        source_id = str(row.session_id or "").strip()
        if source_id:
            grouped.setdefault(source_id, []).append(row)
    for source_id, source_rows in sorted(grouped.items()):
        document_ids = sorted(int(row.id or 0) for row in source_rows if row.id)
        revision_payload = {
            "v": 1,
            "operation": "delete",
            "reason": str(reason or "summary_archived"),
            "session_id": source_id,
            "document_ids": document_ids,
            "stable_hashes": sorted(
                str(row.stable_hash or "") for row in source_rows
            ),
        }
        source_revision = "delete_" + hashlib.sha256(
            json.dumps(
                revision_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        enqueue_index_job(
            db,
            source_type="session_summary",
            source_id=source_id,
            job_type="delete",
            index_version="",
            source_revision=source_revision,
            meta={
                "contract_version": 2,
                "job_origin": "business",
                "operation": "delete",
                "reason": str(reason or "summary_archived"),
                "document_ids": document_ids,
                "delete_source_ids": [
                    source_id,
                    *(str(item) for item in document_ids),
                ],
            },
            commit=False,
        )


def archive_active_summaries_for_session(
    db: Session,
    session_id: str,
    *,
    enqueue_semantic_delete: bool = False,
    delete_reason: str = "summary_archived",
) -> int:
    rows = (
        db.query(RollingSessionSummary)
        .filter(
            RollingSessionSummary.session_id == session_id,
            RollingSessionSummary.status == "active",
        )
        .all()
    )
    archived_at = db_now_naive()
    for row in rows:
        row.status = "archived"
        row.updated_at = archived_at
    if rows:
        if enqueue_semantic_delete:
            _enqueue_archived_summary_delete_jobs(
                db,
                rows,
                reason=delete_reason,
            )
        db.flush()
    return len(rows)


def archive_active_summaries_for_user(
    db: Session,
    user_id: str,
    *,
    enqueue_semantic_delete: bool = False,
    delete_reason: str = "summary_archived",
) -> int:
    rows = (
        db.query(RollingSessionSummary)
        .filter(
            RollingSessionSummary.user_id == user_id,
            RollingSessionSummary.status == "active",
        )
        .all()
    )
    archived_at = db_now_naive()
    for row in rows:
        row.status = "archived"
        row.updated_at = archived_at
    if rows:
        if enqueue_semantic_delete:
            _enqueue_archived_summary_delete_jobs(
                db,
                rows,
                reason=delete_reason,
            )
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
    superseded_rows = (
        db.query(RollingSessionSummary)
        .filter(
            RollingSessionSummary.session_id == str(session_id or ""),
            RollingSessionSummary.status == "active",
        )
        .order_by(RollingSessionSummary.id.asc())
        .all()
    )
    archive_active_summaries_for_session(db, session_id)

    summary_text = render_summary_text(summary_json)
    pending_turn_ids = [int(turn.id) for turn in pending_turns]
    try:
        previous_turn_ids_raw = json.loads(getattr(old_summary, "source_turn_ids_json", "[]") or "[]")
    except (TypeError, json.JSONDecodeError):
        previous_turn_ids_raw = []
    previous_turn_ids = [
        int(item) for item in previous_turn_ids_raw
        if str(item).isdigit()
    ] if isinstance(previous_turn_ids_raw, list) else []
    source_turn_ids = list(dict.fromkeys([*previous_turn_ids, *pending_turn_ids]))
    covered_from_turn_id = int(getattr(old_summary, "covered_from_turn_id", 0) or 0)
    if covered_from_turn_id <= 0:
        covered_from_turn_id = source_turn_ids[0]
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
        covered_from_turn_id=covered_from_turn_id,
        covered_until_turn_id=source_turn_ids[-1],
        source_turn_ids_json=json.dumps(source_turn_ids, ensure_ascii=False),
        source_turn_count=len(source_turn_ids),
        source_token_estimate=(
            int(getattr(old_summary, "source_token_estimate", 0) or 0)
            + sum(estimate_tokens(turn.content or "") for turn in pending_turns)
        ),
        source_char_count=(
            int(getattr(old_summary, "source_char_count", 0) or 0)
            + sum(len(turn.content or "") for turn in pending_turns)
        ),
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
    from core.semantic.adapters import session_summary_source_revision
    from core.semantic.jobs import enqueue_index_job

    superseded_document_ids = sorted(
        int(item.id or 0)
        for item in superseded_rows
        if int(item.id or 0) > 0
    )
    enqueue_index_job(
        db,
        source_type="session_summary",
        source_id=str(row.session_id),
        job_type="replace",
        index_version="",
        source_revision=session_summary_source_revision(row),
        meta={
            "contract_version": 2,
            "job_origin": "business",
            "document_id": int(row.id or 0),
            "superseded_document_ids": superseded_document_ids,
            "delete_source_ids": sorted({
                str(row.id),
                *(str(item) for item in superseded_document_ids),
            }),
        },
        commit=False,
    )
    return row


def _acquire_rollup_write_serialization(
    db: Session,
    *,
    user_id: str,
    session_id: str,
    pending_turn_ids: Sequence[int],
) -> None:
    """在创建 savepoint 前建立物理根写事务，避免 SQLite 提前释放写锁。"""

    if user_id:
        statement = (
            update(User)
            .where(User.id == user_id)
            .values(history_clear_at=User.history_clear_at)
        )
    else:
        statement = (
            update(ConversationTurn)
            .where(
                ConversationTurn.id == int(pending_turn_ids[0]),
                ConversationTurn.session_id == session_id,
            )
            .values(id=ConversationTurn.id)
        )
    db.execute(statement.execution_options(synchronize_session=False))


def _verify_rollup_write_fence(
    db: Session,
    *,
    user_id: str,
    session_id: str,
    pending_turn_ids: Sequence[int],
    expected_history_clear_at: datetime | None,
) -> None:
    if user_id:
        user_row = db.execute(
            select(User.id, User.history_clear_at).where(User.id == user_id)
        ).one_or_none()
        current_history_clear_at = user_row[1] if user_row is not None else None
        if to_db_naive(current_history_clear_at) != to_db_naive(
            expected_history_clear_at
        ):
            raise _RollupFenceRejected("history_clear_changed")

    unique_turn_ids = list(dict.fromkeys(int(item) for item in pending_turn_ids))
    if len(unique_turn_ids) != len(pending_turn_ids):
        raise _RollupFenceRejected("history_clear_changed")
    update_result = db.execute(
        update(ConversationTurn)
        .where(
            ConversationTurn.id.in_(unique_turn_ids),
            ConversationTurn.session_id == session_id,
        )
        .values(id=ConversationTurn.id)
        .execution_options(synchronize_session=False)
    )
    if int(update_result.rowcount or 0) != len(unique_turn_ids):
        raise _RollupFenceRejected("history_clear_changed")


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
    after_clear_at: datetime | None = None,
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
    old_summary_id = int(getattr(active_summary, "id", 0) or 0)
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

    try:
        _acquire_rollup_write_serialization(
            db,
            user_id=user_id,
            session_id=session_id,
            pending_turn_ids=pending_ids,
        )
        with db.begin_nested():
            _verify_rollup_write_fence(
                db,
                user_id=user_id,
                session_id=session_id,
                pending_turn_ids=pending_ids,
                expected_history_clear_at=after_clear_at,
            )
            db.expire_all()
            fresh = get_best_session_summary(
                db,
                session_id,
                after_clear_at=after_clear_at,
                mutate_stale=True,
            )
            fresh_summary_id = int(getattr(fresh, "id", 0) or 0)
            fresh_covered = int(
                getattr(fresh, "covered_until_turn_id", 0) or 0
            )
            if (fresh_summary_id, fresh_covered) != (
                old_summary_id,
                old_covered,
            ):
                raise _RollupHeadChanged("active_summary_changed")

            result.summary = save_new_active_summary(
                db,
                old_summary=fresh,
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
                    previous_summary=fresh,
                    fallback_summary=result.summary,
                    recent_raw_turn_ids=result.recent_raw_turn_ids,
                    current_user_input=current_user_input,
                )
                result.summary_job_id = int(job.id or 0)
        result.requires_commit = True
        return result
    except _RollupFenceRejected:
        db.rollback()
        result.summary = None
        result.summary_text = ""
        result.summary_job_id = 0
        result.requires_commit = False
        result.skipped_reason = "history_clear_changed"
        return result
    except _RollupHeadChanged:
        db.rollback()
        result.summary = get_best_session_summary(
            db,
            session_id,
            after_clear_at=after_clear_at,
            mutate_stale=False,
        )
        result.summary_text = ""
        result.summary_job_id = 0
        result.requires_commit = False
        result.skipped_reason = "already_rolled"
        return result
    except Exception:
        db.rollback()
        raise
