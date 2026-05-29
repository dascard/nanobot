"""异步 LLM rolling session summary 生成与审计。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.session_memory import config
from app.session_memory.jobs import (
    claim_summary_job,
    fetch_pending_summary_jobs,
    mark_summary_job_done,
    mark_summary_job_failed,
)
from app.session_memory.rolling_summary import archive_active_summaries_for_session, audit_rolling_summary
from app.session_memory.summarizer import render_summary_text
from app.session_memory.windowing import estimate_tokens, is_context_eligible_turn
from core.database import ConversationTurn, RollingSessionSummary, SessionSummaryJob

logger = logging.getLogger("nanobot.session_summary.llm")

SESSION_SUMMARY_SYSTEM_PROMPT = """你是对话滚动摘要器。
你只总结输入中列出的 pending ConversationTurn。
不要总结 recent raw window，不要总结当前用户输入。
不要输出工具调用要求，不要生成新的用户请求。
不要把系统契约、工具契约、重试指令当作用户偏好。
不要逐字复述原始对话，不要输出 turn_id、时间戳、role 标签。
请用中文归纳主题、用户意图、已确认结论和待跟进事项。
输出严格 JSON，不要 Markdown，不要代码块。
"""


@dataclass(frozen=True)
class PreparedSessionSummaryJob:
    job_id: int
    messages: list[dict[str, str]]


def _safe_json_loads(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("json_parse_failed: empty_response")
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)```", text)
        if not match:
            raise ValueError("json_parse_failed")
        try:
            value = json.loads(match.group(1).strip())
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("json_parse_failed") from exc
    if not isinstance(value, dict):
        raise ValueError("json_schema_invalid: root_not_object")
    return value


def parse_llm_summary_response(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        payload = dict(raw)
    else:
        payload = _safe_json_loads(str(raw or ""))
    payload.setdefault("open_threads", [])
    payload.setdefault("decisions", [])
    payload.setdefault("important_user_requests", [])
    payload.setdefault("resolved_items", [])
    payload.setdefault("artifacts", [])
    payload.setdefault("participants", [])
    payload.setdefault("keywords", [])
    quality = payload.get("quality")
    if not isinstance(quality, dict):
        quality = {}
    quality.setdefault("score", 0.0)
    quality.setdefault("issues", [])
    payload["quality"] = quality
    return payload


def _json_list(value: str | None) -> list[int]:
    try:
        raw = json.loads(value or "[]")
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


def _load_source_turns(db: Session, job: SessionSummaryJob) -> list[ConversationTurn]:
    ids = _json_list(job.source_turn_ids_json)
    if not ids:
        return []
    rows = (
        db.query(ConversationTurn)
        .filter(ConversationTurn.id.in_(ids))
        .all()
    )
    by_id = {int(row.id): row for row in rows}
    return [by_id[turn_id] for turn_id in ids if turn_id in by_id]


def _format_turn_for_llm(turn: ConversationTurn) -> str:
    from core.context_builder import sanitize_prompt_text

    content = sanitize_prompt_text(turn.content or "", max_chars=1200).strip()
    ts = turn.created_at.strftime("%Y-%m-%d %H:%M:%S") if turn.created_at else ""
    return f"[turn_id={turn.id}][{ts}][{turn.role}] {content}".strip()


def build_llm_summary_messages(
    *,
    previous_summary: RollingSessionSummary | None,
    source_turns: list[ConversationTurn],
) -> list[dict[str, str]]:
    from core.context_builder import sanitize_prompt_text

    previous_text = sanitize_prompt_text(
        getattr(previous_summary, "summary_text", "") or "",
        max_chars=1800,
    )
    pending_lines = [_format_turn_for_llm(turn) for turn in source_turns]
    pending_text = "\n".join(pending_lines)
    if len(pending_text) > config.SESSION_SUMMARY_LLM_MAX_INPUT_CHARS:
        pending_text = pending_text[:config.SESSION_SUMMARY_LLM_MAX_INPUT_CHARS].rstrip()

    user_prompt = (
        "<previous_summary>\n"
        f"{previous_text}\n"
        "</previous_summary>\n\n"
        "<pending_turns>\n"
        f"{pending_text}\n"
        "</pending_turns>\n\n"
        "请输出严格 JSON，字段为 summary、open_threads、decisions、important_user_requests、"
        "resolved_items、artifacts、participants、keywords、quality。"
        "summary 不超过 1200 字，quality.score 必须是 0 到 1 的数字。"
        "不要把 pending_turns 当日志转写，不要保留 turn_id、时间戳或 role 标签。"
        "如果只能摘录，请改写为简洁要点。"
    )
    return [
        {"role": "system", "content": SESSION_SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


async def default_llm_summary_summarizer_async(messages: list[dict[str, str]]) -> str:
    from config import NEW_API_KEY
    from clients.new_api_client import NewAPIClient

    client = NewAPIClient(api_key=NEW_API_KEY)
    response = await client.chat_completion(
        messages=messages,
        temperature=0.1,
        model_tier="fast",
        max_tokens=1200,
        llm_source="session_summary",
        enable_thinking=False,
    )
    if isinstance(response, dict) and response.get("error"):
        raise RuntimeError(str(response.get("detail") or response.get("error")))
    try:
        return str(response["choices"][0]["message"].get("content") or "")
    except Exception as exc:
        raise RuntimeError("llm_response_missing_content") from exc


def default_llm_summary_summarizer(messages: list[dict[str, str]]) -> str:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(default_llm_summary_summarizer_async(messages))
    raise RuntimeError("default_llm_summary_summarizer must run in a sync worker process")


def _call_summarizer(
    summarizer: Callable[[list[dict[str, str]]], Any],
    messages: list[dict[str, str]],
) -> Any:
    result = summarizer(messages)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def audit_llm_session_summary(
    *,
    payload: dict[str, Any],
    source_turns: list[ConversationTurn],
    job: SessionSummaryJob,
) -> tuple[bool, list[str]]:
    issues: list[str] = []
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        issues.append("summary_empty")
    if len(summary) > config.ROLLING_SUMMARY_MAX_CHARS:
        issues.append("summary_too_long")

    for key in [
        "open_threads",
        "decisions",
        "important_user_requests",
        "resolved_items",
        "artifacts",
        "participants",
        "keywords",
    ]:
        if not isinstance(payload.get(key), list):
            issues.append(f"{key}_not_list")

    expected_ids = _json_list(job.source_turn_ids_json)
    actual_ids = [int(turn.id) for turn in source_turns]
    if expected_ids != actual_ids:
        issues.append("source_turn_ids_mismatch")

    for turn in source_turns:
        ok, reason = is_context_eligible_turn(turn)
        if not ok:
            issues.append(f"source_turn_not_eligible:{turn.id}:{reason}")

    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
    try:
        score = float(quality.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score < config.SESSION_SUMMARY_PROMOTE_MIN_SCORE:
        issues.append("quality_score_below_threshold")

    quality_issues = quality.get("issues")
    quality_issues = quality_issues if isinstance(quality_issues, list) else []
    blocking_quality_issues = [
        str(item) for item in quality_issues
        if not str(item).lower().startswith(("warning", "warn:", "警告"))
    ]
    if blocking_quality_issues:
        issues.append("quality_issues_present")

    audit_ok, audit_issues = audit_rolling_summary(
        summary_json=payload,
        pending_turn_ids=actual_ids,
        recent_raw_turn_ids=[],
        current_user_input="",
    )
    if not audit_ok:
        issues.extend(audit_issues)

    text = json.dumps(payload, ensure_ascii=False)
    forbidden_tags = ["<system", "</system>", "<user_input", "</user_input>", "<runtime_context"]
    if any(tag in text for tag in forbidden_tags):
        issues.append("contains_prompt_control_tag")

    return not issues, issues


def _summary_stable_hash(
    *,
    session_id: str,
    source_turn_ids: list[int],
    payload: dict[str, Any],
) -> str:
    return hashlib.sha256(
        json.dumps({
            "kind": "llm_episode",
            "session_id": session_id,
            "source_turn_ids": source_turn_ids,
            "summary": payload.get("summary") or "",
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def save_llm_session_summary(
    db: Session,
    *,
    job: SessionSummaryJob,
    payload: dict[str, Any],
    source_turns: list[ConversationTurn],
    model: str = "async_llm",
    llm_request_log_id: int | None = None,
) -> RollingSessionSummary:
    if not source_turns:
        raise ValueError("source_turns is required")
    fallback = db.get(RollingSessionSummary, int(job.fallback_summary_id or 0)) if job.fallback_summary_id else None
    previous = db.get(RollingSessionSummary, int(job.previous_summary_id or 0)) if job.previous_summary_id else None
    archive_active_summaries_for_session(db, job.session_id)

    source_turn_ids = [int(turn.id) for turn in source_turns]
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
    issues = quality.get("issues") if isinstance(quality.get("issues"), list) else []
    summary_text = render_summary_text(payload)
    row = RollingSessionSummary(
        session_id=job.session_id,
        user_id=job.user_id or "",
        chat_type=job.chat_type or "private",
        status="active",
        summary_kind="llm_episode",
        summary_text=summary_text,
        summary_json=json.dumps(payload, ensure_ascii=False),
        covered_from_turn_id=source_turn_ids[0],
        covered_until_turn_id=source_turn_ids[-1],
        source_turn_ids_json=json.dumps(source_turn_ids, ensure_ascii=False),
        source_turn_count=len(source_turn_ids),
        source_token_estimate=sum(estimate_tokens(turn.content or "") for turn in source_turns),
        source_char_count=sum(len(turn.content or "") for turn in source_turns),
        raw_window_start_turn_id=int(getattr(fallback, "raw_window_start_turn_id", 0) or 0),
        quality_score=float(quality.get("score") or 0.0),
        issues_json=json.dumps(issues, ensure_ascii=False),
        model=model or "async_llm",
        prompt_sha256=hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        llm_status="success",
        llm_model=model or "async_llm",
        llm_request_log_id=llm_request_log_id,
        supersedes_summary_id=int(fallback.id) if fallback and fallback.id else None,
        stable_hash=_summary_stable_hash(
            session_id=job.session_id,
            source_turn_ids=source_turn_ids,
            payload=payload,
        ),
        meta_json=json.dumps({
            "schema_version": 1,
            "created_by": "session_summary_worker",
            "summary_kind": "llm_episode",
            "fallback_summary_id": int(getattr(fallback, "id", 0) or 0),
            "previous_summary_id": int(getattr(previous, "id", 0) or 0),
        }, ensure_ascii=False),
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(row)
    db.flush()
    return row


def prepare_claimed_session_summary_job(
    db: Session,
    job_id: int,
    *,
    owner: str = "session-summary-worker",
) -> PreparedSessionSummaryJob | None:
    job = db.get(SessionSummaryJob, int(job_id))
    if job is None:
        return None
    if job.status != "running":
        return None
    if job.locked_by and job.locked_by != owner:
        return None
    source_turns = _load_source_turns(db, job)
    if not source_turns:
        raise ValueError("source_turns_empty")
    previous = db.get(RollingSessionSummary, int(job.previous_summary_id or 0)) if job.previous_summary_id else None
    messages = build_llm_summary_messages(
        previous_summary=previous,
        source_turns=source_turns,
    )
    return PreparedSessionSummaryJob(job_id=int(job.id or 0), messages=messages)


def finalize_claimed_session_summary_job(
    db: Session,
    prepared: PreparedSessionSummaryJob,
    *,
    raw: Any,
    owner: str = "session-summary-worker",
) -> bool:
    job = db.get(SessionSummaryJob, int(prepared.job_id))
    if job is None:
        return False
    if job.status != "running":
        return False
    if job.locked_by and job.locked_by != owner:
        return False
    source_turns = _load_source_turns(db, job)
    if not source_turns:
        raise ValueError("source_turns_empty")
    payload = parse_llm_summary_response(raw)
    audit_ok, issues = audit_llm_session_summary(
        payload=payload,
        source_turns=source_turns,
        job=job,
    )
    if not audit_ok:
        raise ValueError(",".join(issues))

    summary = save_llm_session_summary(
        db,
        job=job,
        payload=payload,
        source_turns=source_turns,
        model="async_llm",
    )
    mark_summary_job_done(db, job, result_summary_id=int(summary.id or 0))
    db.flush()
    return True


def fail_claimed_session_summary_job(
    db: Session,
    job_id: int,
    *,
    owner: str = "session-summary-worker",
    error: str,
) -> bool:
    job = db.get(SessionSummaryJob, int(job_id))
    if job is None:
        return False
    if job.status != "running":
        return False
    if job.locked_by and job.locked_by != owner:
        return False
    mark_summary_job_failed(db, job, error=error)
    db.flush()
    return True


def _fail_claimed_job_with_factory(
    session_factory: Callable[[], Session],
    *,
    job_id: int,
    owner: str,
    error: str,
) -> bool:
    db = session_factory()
    try:
        failed = fail_claimed_session_summary_job(db, job_id, owner=owner, error=error)
        db.commit()
        return failed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def process_claimed_session_summary_job_short_transactions(
    session_factory: Callable[[], Session],
    *,
    job_id: int,
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
    owner: str = "session-summary-worker",
) -> bool:
    """处理已抢占的 job，避免 LLM 调用期间持有写事务。"""
    summarizer = summarizer or default_llm_summary_summarizer
    db = session_factory()
    try:
        prepared = prepare_claimed_session_summary_job(db, int(job_id), owner=owner)
        db.commit()
    except Exception as exc:
        db.rollback()
        _fail_claimed_job_with_factory(
            session_factory,
            job_id=int(job_id),
            owner=owner,
            error=str(exc),
        )
        return False
    finally:
        db.close()

    if prepared is None:
        return False

    try:
        raw = _call_summarizer(summarizer, prepared.messages)
    except Exception as exc:
        logger.warning("session summary job failed before finalize: job_id=%s error=%s", job_id, exc)
        _fail_claimed_job_with_factory(
            session_factory,
            job_id=int(job_id),
            owner=owner,
            error=str(exc),
        )
        return False

    db = session_factory()
    try:
        ok = finalize_claimed_session_summary_job(
            db,
            prepared,
            raw=raw,
            owner=owner,
        )
        db.commit()
        return ok
    except Exception as exc:
        db.rollback()
        logger.warning("session summary job failed: job_id=%s error=%s", job_id, exc)
        _fail_claimed_job_with_factory(
            session_factory,
            job_id=int(job_id),
            owner=owner,
            error=str(exc),
        )
        return False
    finally:
        db.close()


def process_session_summary_job(
    db: Session,
    job: SessionSummaryJob,
    *,
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
    owner: str = "session-summary-worker",
) -> bool:
    summarizer = summarizer or default_llm_summary_summarizer
    if job.status == "pending":
        claimed = claim_summary_job(db, int(job.id or 0), owner=owner)
        if claimed is None:
            return False
        job = claimed
    elif job.status != "running":
        return False
    elif job.locked_by and job.locked_by != owner:
        return False
    try:
        prepared = prepare_claimed_session_summary_job(db, int(job.id or 0), owner=owner)
        if prepared is None:
            return False
        raw = _call_summarizer(summarizer, prepared.messages)
        ok = finalize_claimed_session_summary_job(
            db,
            prepared,
            raw=raw,
            owner=owner,
        )
        return ok
    except Exception as exc:
        logger.warning("session summary job failed: job_id=%s error=%s", getattr(job, "id", 0), exc)
        fail_claimed_session_summary_job(db, int(job.id or 0), owner=owner, error=str(exc))
        db.flush()
        return False


def run_session_summary_worker_once(
    db: Session,
    *,
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
    owner: str = "session-summary-worker",
    limit: int | None = None,
) -> dict[str, int]:
    jobs = fetch_pending_summary_jobs(db, limit=limit)
    stats = {"processed": 0, "done": 0, "failed": 0}
    for job in jobs:
        claimed = claim_summary_job(db, int(job.id or 0), owner=owner)
        if claimed is None:
            continue
        stats["processed"] += 1
        ok = process_session_summary_job(
            db,
            claimed,
            summarizer=summarizer,
            owner=owner,
        )
        if ok:
            stats["done"] += 1
        else:
            stats["failed"] += 1
    return stats
