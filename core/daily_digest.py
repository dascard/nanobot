"""
Daily digest + scheduled task pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from collections.abc import Callable, Mapping
from typing import Any

import aiohttp
from sqlalchemy import and_

from app.memory_digest.builder import MemoryDigestBuilder
from app.memory_digest.jobs import (
    MemoryDigestJobClaim,
    MemoryDigestJobLeaseLost,
    claim_memory_digest_job,
    finish_memory_digest_job,
    heartbeat_memory_digest_job,
    memory_digest_job_retryable,
    memory_digest_source_snapshot,
    recover_expired_memory_digest_jobs,
    save_memory_digest_batch_checkpoint,
    settle_memory_digest_job_without_rows,
)
from app.memory_digest.llm_builder import build_memory_digest_with_llm, build_memory_digest_with_llm_async
from app.memory_digest.renderer import render_recall_card
from app.memory_digest.retrieval_service import safe_digest_meta, digest_status
from core.chat_stream_identity import (
    identity_storage_aliases,
    parse_compatibility_chat_stream_identity,
    resolve_chat_stream_identity,
)
from core.context_builder import sanitize_prompt_text
from core.database import ChatLog, MemoryDigest, ScheduledTask, SessionLocal
from core.group_runtime.ids import normalize_group_session_id
from core.outbound_transport import (
    DeliveryOutcome,
    deliver_qq_push_with_session,
    delivery_outcome_to_legacy,
    resolve_qq_push_token,
)
from core.safe_diagnostics import safe_response_summary
from core.schedule_spec import (
    SHANGHAI,
    grace_seconds,
    initial_anchor,
    next_fire_after,
    spec_from_fields,
)
from core.scheduled_task_outbound import (
    recover_expired_scheduled_task_occurrences,
)
from core.scheduled_workflow import (
    DEFAULT_WORKFLOW_CONCURRENCY,
    ScheduledWorkflowWorkerResult,
    enqueue_scheduled_task_execution,
    run_scheduled_task_workflow_worker,
)
from core.scheduled_workflow_runtime import (
    create_scheduled_workflow_callbacks,
)
from core.settings_service import settings
from core.time_utils import db_now_naive
logger = logging.getLogger("nanobot.daily_digest")

# QQbot / NoneBot 侧默认监听 8082；若部署环境不同，可通过环境变量覆盖。
QQBOT_PUSH_URL = os.environ.get("QQBOT_PUSH_URL", "http://172.17.0.1:8082/nanobot/push")
QQBOT_PUSH_TIMEOUT = float(os.environ.get("QQBOT_PUSH_TIMEOUT", "180"))
MEMORY_DIGEST_LLM_ENABLED = os.environ.get("MEMORY_DIGEST_LLM_ENABLED", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

TOPIC_KEYWORDS = {
    "model_release": ["发布", "release", "new model", "版本", "更新"],
    "pricing": ["价格", "pricing", "free", "试用", "优惠", "token", "白嫖", "便宜"],
    "tooling": ["sdk", "api", "插件", "workflow", "agent", "tool"],
    "community": ["reddit", "x.com", "twitter", "论坛", "社区", "评论"],
}

MODEL_HINTS = [
    "qwen",
    "deepseek",
    "kimi",
    "gpt",
    "claude",
    "gemini",
    "llama",
    "mistral",
    "hunyuan",
    "glm",
    "通义",
    "豆包",
    "混元",
    "智谱",
    "阶跃",
    "minimax",
]


def _to_day(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _next_run_delay_seconds(now: datetime, run_hour: int) -> int:
    run_hour = max(0, min(23, int(run_hour)))
    target = now.replace(hour=run_hour, minute=5, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return max(60, int((target - now).total_seconds()))


def _build_scheduled_task_query(task: ScheduledTask, now: datetime | None = None) -> str:
    now = now or db_now_naive()
    time_label = now.strftime("%Y-%m-%d %H:%M:%S")
    from core.scheduled_task_contract import (
        validate_scheduled_task_definition,
    )

    task_name_raw, prompt_raw = validate_scheduled_task_definition(
        name=task.name,
        prompt_template=task.prompt_template,
    )
    # 长度只在定义边界统一拒绝；执行时不得再静默裁剪任务语义。
    prompt = sanitize_prompt_text(prompt_raw).strip()
    task_name = sanitize_prompt_text(task_name_raw)
    target_type = sanitize_prompt_text(task.target_type or "", max_chars=40)
    target_id = sanitize_prompt_text(task.target_id or "", max_chars=80)
    return (
        "[定时任务执行]\n"
        f"当前时间（北京时间）：{time_label}\n"
        f"任务名称：{task_name}\n"
        f"推送目标：{target_type}/{target_id}\n\n"
        "执行规则：\n"
        "- 必须完成下面的任务模板，不要改写为闲聊。\n"
        "- 如果任务涉及今天、最新、新闻、资讯、日报、早报，必须先调用 ai_daily 获取实时来源；禁止只凭模型内置知识生成。\n"
        "- 如果任务涉及群聊总结、群日报、分析某个群，必须调用 group_analysis 获取真实聊天记录。\n"
        "- 如果任务涉及数据库、历史记录或统计查询，优先调用 sql_analysis。\n"
        "- 工具返回 HTML 报告时，直接保留 HTML，不要改写成 Markdown。\n\n"
        "<task_template>\n"
        f"{prompt}\n"
        "</task_template>"
    )


def _scheduled_task_session_id(task: ScheduledTask) -> str:
    from core.scheduled_task_contract import (
        scheduled_task_owner_from_persisted,
    )

    owner = scheduled_task_owner_from_persisted(
        chat_stream_id=task.owner_chat_stream_id,
        platform=task.owner_platform,
        chat_type=task.owner_chat_type,
        session_id=task.owner_session_id,
        created_by_actor_id=task.created_by_actor_id,
    )
    return owner.session_id


def _scheduled_task_metadata(task: ScheduledTask) -> dict:
    from core.scheduled_task_contract import (
        scheduled_task_owner_from_persisted,
    )

    owner = scheduled_task_owner_from_persisted(
        chat_stream_id=task.owner_chat_stream_id,
        platform=task.owner_platform,
        chat_type=task.owner_chat_type,
        session_id=task.owner_session_id,
        created_by_actor_id=task.created_by_actor_id,
    )
    is_group = owner.chat_type == "group"
    actor_user_id = (
        owner.created_by_actor_id
        or (owner.external_session_id if not is_group else "")
    )
    return {
        "raw_query": task.prompt_template or "",
        "session_name": f"定时任务:{task.name or task.id}",
        "is_group": is_group,
        "chat_type": owner.chat_type,
        "platform": owner.platform,
        "group_id": owner.external_session_id if is_group else "",
        "user_id": actor_user_id,
        "scheduled_task_owner_chat_stream_id": owner.chat_stream_id,
        "scheduled_task_definition_version": int(
            task.definition_version or 1
        ),
        # 防递归:定时任务会话内不得再创建/修改定时任务
        "disabled_tool_names": ["schedule_task"],
    }


def _normalize_group_session_id(group_id: str) -> str:
    return normalize_group_session_id(group_id)


def _normalize_chatlog_session_id(session_id: str, user_id: str = "") -> str:
    sid = (session_id or "").strip()
    uid = (user_id or "").strip()
    session_identity = parse_compatibility_chat_stream_identity(sid)
    user_identity = parse_compatibility_chat_stream_identity(uid)
    if (
        session_identity is not None
        and session_identity.chat_type == "group"
    ):
        return session_identity.legacy_runtime_session_id
    if user_identity is not None and user_identity.chat_type == "group":
        return user_identity.legacy_runtime_session_id
    return sid


def _session_filter_aliases(session_id: str | None) -> set[str]:
    sid = str(session_id or "").strip()
    if not sid:
        return set()
    identity = parse_compatibility_chat_stream_identity(sid)
    if identity is None:
        return {sid}
    return set(identity_storage_aliases(
        identity,
        include_raw_group_id=identity.chat_type == "group",
    ))


def _group_push_target_id(session_id: str) -> str:
    return resolve_chat_stream_identity(
        platform="qq",
        chat_type="group",
        session_id=session_id,
    ).external_session_id


_HTML_SIGNATURES = ("<!doctype", "<html", "<article", "<div class=", "```html")


def _is_html_blob(content: str) -> bool:
    c = (content or "").strip().lower()
    return any(c.startswith(sig) for sig in _HTML_SIGNATURES)


def _format_log_line(log: ChatLog) -> str | None:
    role = (log.role or "unknown").strip()

    # 工具调用/思考过程不进入记忆摘要
    if role in ("tool", "model", "system", "function"):
        return None

    ts = log.created_at.strftime("%H:%M") if log.created_at else "--:--"
    sender = (log.sender_name or "").strip()
    content = (log.content or "").strip()
    who = sender or role

    # HTML 日报/报告 → 存指针，不存全文
    if role == "assistant" and _is_html_blob(content):
        label = _html_label(content)
        return f"[{ts}] {role}({who}): {label}"

    # 普通文本消息
    content = content.replace("\n", " ")
    if len(content) > 280:
        content = content[:280] + "..."
    if not content:
        return None
    return f"[{ts}] {role}({who}): {content}"


def _html_label(content: str) -> str:
    import re
    c = content.lower()
    if "news-brief" in c or "日报" in c or "daily" in c:
        return f"[AI日报 {len(content)} chars]"
    if "group_analysis" in c or "群聊分析" in c:
        return f"[群聊分析报告 {len(content)} chars]"
    m = re.search(r"<title>(.*?)</title>", c, re.IGNORECASE)
    if m:
        return f"[HTML报告: {m.group(1)[:40]} {len(content)} chars]"
    return f"[HTML内容 {len(content)} chars]"


def _extract_structured_tags(logs: list[ChatLog]) -> dict:
    raw_text = "\n".join([(x.content or "") for x in logs]).lower()

    topics = []
    for topic, keys in TOPIC_KEYWORDS.items():
        if any(k in raw_text for k in keys):
            topics.append(topic)

    models = sorted({m for m in MODEL_HINTS if m in raw_text})
    value_signal_score = 0
    for k in TOPIC_KEYWORDS["pricing"]:
        if k in raw_text:
            value_signal_score += 1

    users = sum(1 for x in logs if (x.role or "") == "user")
    assistants = sum(1 for x in logs if (x.role or "") == "assistant")
    ambient = sum(1 for x in logs if (x.role or "") == "ambient")

    return {
        "topics": topics,
        "model_hints": models,
        "value_signal_score": value_signal_score,
        "message_stats": {
            "total": len(logs),
            "user": users,
            "assistant": assistants,
            "ambient": ambient,
        },
    }


def _already_digested(db, session_id: str, digest_date: str) -> bool:
    rows = (
        db.query(MemoryDigest)
        .filter(
            and_(
                MemoryDigest.session_id == session_id,
                MemoryDigest.digest_date == digest_date,
                MemoryDigest.level == 2,
            )
        )
        .all()
    )
    for row in rows:
        status = digest_status(safe_digest_meta(row.meta_json))
        if status in {"active", "skipped"}:
            return True
    return False


def _archive_existing_digests(db, session_id: str, digest_date: str) -> list[MemoryDigest]:
    rows = (
        db.query(MemoryDigest)
        .filter(
            and_(
                MemoryDigest.session_id == session_id,
                MemoryDigest.digest_date == digest_date,
            )
        )
        .all()
    )
    for row in rows:
        meta = safe_digest_meta(row.meta_json)
        meta["status"] = "archived"
        row.meta_json = json.dumps(meta, ensure_ascii=False)
    return rows


def _build_memory_digest_result(
    *,
    user_id: str,
    session_id: str,
    digest_date: str,
    logs: list[ChatLog],
    use_llm: bool | None,
    llm_summarizer: Callable[[list[dict[str, str]]], Any] | None,
):
    llm_enabled = MEMORY_DIGEST_LLM_ENABLED if use_llm is None else bool(use_llm)
    if not llm_enabled:
        return MemoryDigestBuilder().build(
            user_id=user_id,
            session_id=session_id,
            digest_date=digest_date,
            logs=logs,
        )
    return build_memory_digest_with_llm(
        user_id=user_id,
        session_id=session_id,
        digest_date=digest_date,
        logs=logs,
        summarizer=llm_summarizer,
        llm_enabled=True,
    )


async def _build_memory_digest_result_async(
    *,
    user_id: str,
    session_id: str,
    digest_date: str,
    logs: list[ChatLog],
    use_llm: bool | None,
    llm_summarizer: Callable[[list[dict[str, str]]], Any] | None,
    heartbeat: Callable[[], Any] | None = None,
    resume_checkpoint: dict[str, Any] | None = None,
    checkpoint_callback: Callable[[dict[str, Any]], Any] | None = None,
):
    llm_enabled = MEMORY_DIGEST_LLM_ENABLED if use_llm is None else bool(use_llm)
    if not llm_enabled:
        return MemoryDigestBuilder().build(
            user_id=user_id,
            session_id=session_id,
            digest_date=digest_date,
            logs=logs,
        )
    return await build_memory_digest_with_llm_async(
        user_id=user_id,
        session_id=session_id,
        digest_date=digest_date,
        logs=logs,
        summarizer=llm_summarizer,
        llm_enabled=True,
        heartbeat=heartbeat,
        resume_checkpoint=resume_checkpoint,
        checkpoint_callback=checkpoint_callback,
    )


def _digest_row_meta(
    meta: dict,
    *,
    summary_type: str,
    recall_card: dict | None = None,
    recall_card_index: int | None = None,
) -> dict:
    row_meta = dict(meta)
    cards = meta.get("recall_cards") if isinstance(meta.get("recall_cards"), list) else []
    row_meta["summary_type"] = summary_type
    row_meta["recall_card_count"] = len(cards)
    if recall_card is not None:
        row_meta["recall_cards"] = [recall_card]
        row_meta["recall_card"] = recall_card
        row_meta["recall_card_index"] = int(recall_card_index or 0)
    return row_meta


def _ensure_digest_source_meta(
    meta: dict,
    *,
    session_id: str,
    digest_date: str,
    start_id: int,
    end_id: int,
) -> dict:
    row_meta = dict(meta)
    raw = f"{digest_date}|{session_id}|{start_id}|{end_id}|memory_digest_v2"
    row_meta.setdefault("source_id", hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24])
    row_meta.setdefault("source_type", "date_session")
    _raw_range = f"log_id {start_id or 0}-{end_id or 0}"
    row_meta.setdefault("source_range", _raw_range)
    row_meta["raw_source_range"] = _raw_range
    source_stats = row_meta.get("source_stats") if isinstance(row_meta.get("source_stats"), dict) else {}
    row_meta.setdefault("message_count", int(source_stats.get("valid_log_count") or 0))
    row_meta.setdefault("generator", "deterministic_fallback")
    row_meta.setdefault("fallback_reason", None)
    row_meta.setdefault("prompt_template", "")
    row_meta.setdefault("prompt_version", {})
    return row_meta


def _collect_daily_digest_logs_by_session(
    db,
    *,
    target_date: str,
    user_id: str | None,
    session_id: str | None,
) -> dict[str, list[ChatLog]]:
    start = datetime.fromisoformat(target_date)
    end = start + timedelta(days=1)
    base_query = db.query(ChatLog).filter(ChatLog.created_at.isnot(None))
    base_query = base_query.filter(ChatLog.created_at >= start, ChatLog.created_at < end)
    if user_id:
        base_query = base_query.filter(ChatLog.user_id == user_id)

    all_logs = base_query.order_by(ChatLog.id.asc()).all()
    session_aliases = _session_filter_aliases(session_id)

    by_session: dict[str, list[ChatLog]] = {}
    for log in all_logs:
        if _to_day(log.created_at) != target_date:
            continue
        sid = _normalize_chatlog_session_id(log.session_id, log.user_id)
        if not sid:
            continue
        if session_aliases and sid not in session_aliases:
            continue
        by_session.setdefault(sid, []).append(log)
    return by_session


def _write_memory_digest_rows(
    db,
    *,
    session_id: str,
    target_date: str,
    logs: list[ChatLog],
    result,
    force: bool,
    generation_job_id: int | None = None,
) -> dict[str, Any] | None:
    if result.status != "active" or str(result.meta.get("generator") or "") != "llm":
        return None
    start_id = logs[0].id
    end_id = logs[-1].id
    uid = logs[0].user_id or ""
    meta = _ensure_digest_source_meta(
        dict(result.meta),
        session_id=session_id,
        digest_date=target_date,
        start_id=int(start_id or 0),
        end_id=int(end_id or 0),
    )
    archived_rows = []
    if force and result.status == "active":
        archived_rows = _archive_existing_digests(db, session_id, target_date)

    d0 = MemoryDigest(
        user_id=uid,
        session_id=session_id,
        digest_date=target_date,
        level=0,
        parent_id=None,
        content=result.level_contents.get(0, ""),
        meta_json=json.dumps(_digest_row_meta(meta, summary_type="detailed_digest"), ensure_ascii=False),
        source_start_log_id=start_id,
        source_end_log_id=end_id,
        generation_job_id=generation_job_id,
    )
    db.add(d0)
    db.flush()
    written_rows = [d0]

    d1 = MemoryDigest(
        user_id=uid,
        session_id=session_id,
        digest_date=target_date,
        level=1,
        parent_id=d0.id,
        content=result.level_contents.get(1, ""),
        meta_json=json.dumps(_digest_row_meta(meta, summary_type="preview_digest"), ensure_ascii=False),
        source_start_log_id=start_id,
        source_end_log_id=end_id,
        generation_job_id=generation_job_id,
    )
    db.add(d1)
    db.flush()
    written_rows.append(d1)

    cards = meta.get("recall_cards") if isinstance(meta.get("recall_cards"), list) else []
    if not cards:
        cards = [None]
    for index, card in enumerate(cards):
        card_meta = _digest_row_meta(
            meta,
            summary_type="recall_card",
            recall_card=card if isinstance(card, dict) else None,
            recall_card_index=index,
        )
        content = render_recall_card(card) if isinstance(card, dict) else result.level_contents.get(2, "")
        d2 = MemoryDigest(
            user_id=uid,
            session_id=session_id,
            digest_date=target_date,
            level=2,
            parent_id=d1.id,
            content=content,
            meta_json=json.dumps(card_meta, ensure_ascii=False),
            source_start_log_id=start_id,
            source_end_log_id=end_id,
            generation_job_id=generation_job_id,
        )
        db.add(d2)
        db.flush()
        written_rows.append(d2)
    from core.semantic.adapters import memory_digest_source_id, memory_digest_source_revision
    from core.semantic.jobs import enqueue_index_job

    logical_source_id = memory_digest_source_id(written_rows[0])
    source_revision = memory_digest_source_revision(written_rows)
    delete_source_ids = {
        str(row.id)
        for row in [*archived_rows, *written_rows]
        if int(getattr(row, "id", 0) or 0) > 0
    }
    for row in archived_rows:
        archived_source_id = memory_digest_source_id(row)
        if archived_source_id and archived_source_id != logical_source_id:
            delete_source_ids.add(archived_source_id)
    semantic_job = enqueue_index_job(
        db,
        source_type="memory_digest",
        source_id=logical_source_id,
        job_type="replace",
        index_version="",
        source_revision=source_revision,
        meta={
            "contract_version": 2,
            "job_origin": "business",
            "document_ids": [int(row.id) for row in written_rows],
            "delete_source_ids": sorted(delete_source_ids),
            "session_id": session_id,
            "digest_date": target_date,
            "generation_job_id": generation_job_id,
        },
        commit=False,
    )
    return {
        "digest_ids": [int(row.id) for row in written_rows],
        "logical_source_id": logical_source_id,
        "semantic_job_id": int(semantic_job.id),
    }


def generate_daily_digest_for_date(
    target_date: str,
    user_id: str | None = None,
    *,
    session_id: str | None = None,
    force: bool = False,
    use_llm: bool | None = None,
    llm_summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
) -> int:
    """
    Summarize one day of chat logs into 3 progressive layers.

    Returns number of sessions summarized.
    """
    db = SessionLocal()
    created = 0
    try:
        by_session = _collect_daily_digest_logs_by_session(
            db,
            target_date=target_date,
            user_id=user_id,
            session_id=session_id,
        )

        for session_id, logs in by_session.items():
            if not logs:
                continue
            if not force and _already_digested(db, session_id, target_date):
                continue

            result = _build_memory_digest_result(
                user_id=logs[0].user_id or "",
                session_id=session_id,
                digest_date=target_date,
                logs=logs,
                use_llm=use_llm,
                llm_summarizer=llm_summarizer,
            )

            if _write_memory_digest_rows(
                db,
                session_id=session_id,
                target_date=target_date,
                logs=logs,
                result=result,
                force=force,
            ):
                created += 1

        db.commit()
        if created > 0:
            logger.info(
                f"Daily digest generated for {created} session(s), date={target_date}"
            )
        return created
    except Exception:
        db.rollback()
        logger.exception(f"Daily digest failed for date={target_date}")
        return 0
    finally:
        db.close()


def _memory_digest_failure_type(result: object) -> str:
    meta = getattr(result, "meta", None)
    if not isinstance(meta, Mapping):
        return "build_failed"
    structured = str(meta.get("failure_type") or "").strip()
    if structured in {
        "model_error",
        "output_invalid",
        "output_capacity_exceeded",
        "quality_rejected",
        "generator_not_llm",
        "input_invalid",
        "template_invalid",
        "build_failed",
    }:
        return structured
    raw = " ".join(
        str(value or "")
        for value in (
            meta.get("fallback_reason"),
            meta.get("llm_error"),
            meta.get("status"),
            meta.get("llm_status"),
        )
    ).lower()
    if any(token in raw for token in ("json_", "schema_invalid", "output_invalid")):
        return "output_invalid"
    if any(token in raw for token in ("evidence", "quality", "audit", "recall_card")):
        return "quality_rejected"
    if any(token in raw for token in ("timeout", "connection", "provider", "gateway", "llm_")):
        return "model_error"
    if str(meta.get("generator") or "") != "llm":
        return "generator_not_llm"
    return "build_failed"


def _memory_digest_failure_retryable(error_type: str) -> bool:
    return error_type in {
        "model_error",
        "output_invalid",
        "quality_rejected",
        "build_failed",
    }


def _memory_digest_result_payload(
    *,
    session_id: str,
    status: str,
    job_id: int | None = None,
    retryable: bool = False,
    error_type: str = "",
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "status": status,
        "job_id": job_id,
        "retryable": bool(retryable),
        "error_type": str(error_type or "")[:64],
    }


def _memory_digest_job_trace_meta(meta: object) -> dict[str, Any]:
    if not isinstance(meta, Mapping):
        return {"schema_version": 1}
    quality = meta.get("quality")
    model_quality_score = meta.get("model_quality_score")
    if not isinstance(model_quality_score, (int, float)) or isinstance(
        model_quality_score,
        bool,
    ):
        model_quality_score = None
    if (
        model_quality_score is None
        and str(meta.get("generator") or "") == "llm"
        and isinstance(quality, Mapping)
    ):
        model_quality_score = quality.get("score")
    audit_issues = meta.get("audit_issues")
    return {
        "schema_version": 1,
        "llm_models": list(meta.get("llm_models") or []),
        "llm_requested_models": list(meta.get("llm_requested_models") or []),
        "llm_request_log_ids": list(meta.get("llm_request_log_ids") or []),
        "llm_actual_model_observed": bool(
            meta.get("llm_actual_model_observed", False)
        ),
        "quality_score": model_quality_score,
        "audit_issues": (
            [str(issue)[:128] for issue in audit_issues]
            if isinstance(audit_issues, list)
            else []
        ),
    }


def _memory_digest_report(
    *,
    target_date: str,
    results: list[dict[str, Any]],
    no_input: bool = False,
) -> dict[str, Any]:
    counts = {
        "created": 0,
        "skipped": 0,
        "no_input": 1 if no_input else 0,
        "failed": 0,
        "in_progress": 0,
    }
    for item in results:
        status = str(item.get("status") or "")
        if status in counts:
            counts[status] += 1
    if no_input:
        status = "no_input"
    elif counts["failed"] and counts["created"]:
        status = "partial"
    elif counts["failed"]:
        status = "failed"
    elif counts["in_progress"]:
        status = "partial"
    else:
        status = "ok"
    return {
        "status": status,
        "target_date": target_date,
        "created_sessions": counts["created"],
        "counts": counts,
        "results": results,
    }


def _settle_memory_digest_failure(
    claim: MemoryDigestJobClaim,
    *,
    session_id: str,
    error_type: str,
    retryable: bool,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = settle_memory_digest_job_without_rows(
            db,
            claim,
            status="failed",
            error_type=error_type,
            error_summary=f"memory_digest_failed:{error_type}",
            retryable=retryable,
            meta=meta,
        )
        if row is None:
            return _memory_digest_result_payload(
                session_id=session_id,
                status="in_progress",
                job_id=claim.job_id,
                error_type="lease_lost",
            )
        return _memory_digest_result_payload(
            session_id=session_id,
            status="failed",
            job_id=claim.job_id,
            retryable=bool(retryable and memory_digest_job_retryable(row)),
            error_type=error_type,
        )
    except Exception as exc:
        db.rollback()
        logger.error(
            "MemoryDigest failure settlement failed: session_id=%s error_type=%s",
            session_id,
            type(exc).__name__,
        )
        return _memory_digest_result_payload(
            session_id=session_id,
            status="failed",
            job_id=claim.job_id,
            error_type="job_settlement_failed",
        )
    finally:
        db.close()


async def _generate_memory_digest_session_report(
    *,
    target_date: str,
    current_session_id: str,
    logs: list[ChatLog],
    source_user_id: str | None,
    force: bool,
    retry_failed: bool,
    use_llm: bool | None,
    llm_summarizer: Callable[[list[dict[str, str]]], Any] | None,
) -> dict[str, Any]:
    snapshot = memory_digest_source_snapshot(
        session_id=current_session_id,
        digest_date=target_date,
        logs=logs,
    )
    claim_db = SessionLocal()
    try:
        if (
            not force
            and not retry_failed
            and _already_digested(claim_db, current_session_id, target_date)
        ):
            return _memory_digest_result_payload(
                session_id=current_session_id,
                status="skipped",
            )
        claim = claim_memory_digest_job(
            claim_db,
            snapshot,
            force=force,
            retry_failed=retry_failed,
        )
    except Exception as exc:
        claim_db.rollback()
        logger.error(
            "MemoryDigest claim failed: session_id=%s error_type=%s",
            current_session_id,
            type(exc).__name__,
        )
        return _memory_digest_result_payload(
            session_id=current_session_id,
            status="failed",
            error_type="claim_failed",
        )
    finally:
        claim_db.close()

    if claim.decision in {"done", "skipped"}:
        return _memory_digest_result_payload(
            session_id=current_session_id,
            status="skipped",
            job_id=claim.job_id,
        )
    if claim.decision == "in_progress":
        return _memory_digest_result_payload(
            session_id=current_session_id,
            status="in_progress",
            job_id=claim.job_id,
        )
    if claim.decision == "failed":
        return _memory_digest_result_payload(
            session_id=current_session_id,
            status="failed",
            job_id=claim.job_id,
            retryable=claim.retryable,
            error_type=claim.error_type,
        )

    def heartbeat() -> None:
        heartbeat_db = SessionLocal()
        try:
            heartbeat_memory_digest_job(heartbeat_db, claim)
        finally:
            heartbeat_db.close()

    def persist_checkpoint(checkpoint: dict[str, Any]) -> None:
        checkpoint_db = SessionLocal()
        try:
            save_memory_digest_batch_checkpoint(
                checkpoint_db,
                claim,
                checkpoint,
            )
        finally:
            checkpoint_db.close()

    try:
        result = await _build_memory_digest_result_async(
            user_id=snapshot.user_id,
            session_id=current_session_id,
            digest_date=target_date,
            logs=logs,
            use_llm=use_llm,
            llm_summarizer=llm_summarizer,
            heartbeat=heartbeat,
            resume_checkpoint=claim.checkpoint,
            checkpoint_callback=persist_checkpoint,
        )
    except MemoryDigestJobLeaseLost:
        return _memory_digest_result_payload(
            session_id=current_session_id,
            status="in_progress",
            job_id=claim.job_id,
            error_type="lease_lost",
        )
    except Exception as exc:
        logger.error(
            "MemoryDigest build raised: session_id=%s error_type=%s",
            current_session_id,
            type(exc).__name__,
        )
        return _settle_memory_digest_failure(
            claim,
            session_id=current_session_id,
            error_type="model_error",
            retryable=True,
        )

    if result.status == "skipped":
        settle_db = SessionLocal()
        try:
            row = settle_memory_digest_job_without_rows(
                settle_db,
                claim,
                status="skipped",
            )
        except Exception as exc:
            settle_db.rollback()
            logger.error(
                "MemoryDigest skipped settlement failed: session_id=%s error_type=%s",
                current_session_id,
                type(exc).__name__,
            )
            return _settle_memory_digest_failure(
                claim,
                session_id=current_session_id,
                error_type="job_settlement_failed",
                retryable=True,
            )
        finally:
            settle_db.close()
        return _memory_digest_result_payload(
            session_id=current_session_id,
            status="skipped" if row is not None else "in_progress",
            job_id=claim.job_id,
            error_type="" if row is not None else "lease_lost",
        )

    generator = str(getattr(result, "meta", {}).get("generator") or "")
    if result.status != "active" or generator != "llm":
        error_type = _memory_digest_failure_type(result)
        return _settle_memory_digest_failure(
            claim,
            session_id=current_session_id,
            error_type=error_type,
            retryable=_memory_digest_failure_retryable(error_type),
            meta=_memory_digest_job_trace_meta(getattr(result, "meta", None)),
        )

    write_db = SessionLocal()
    try:
        fresh_by_session = _collect_daily_digest_logs_by_session(
            write_db,
            target_date=target_date,
            user_id=source_user_id,
            session_id=current_session_id,
        )
        fresh_logs = fresh_by_session.get(current_session_id, [])
        fresh_snapshot = memory_digest_source_snapshot(
            session_id=current_session_id,
            digest_date=target_date,
            logs=fresh_logs,
        )
        if fresh_snapshot.source_revision != claim.source_revision:
            row = settle_memory_digest_job_without_rows(
                write_db,
                claim,
                status="failed",
                error_type="source_changed",
                error_summary="memory_digest_failed:source_changed",
                retryable=True,
            )
            return _memory_digest_result_payload(
                session_id=current_session_id,
                status="failed" if row is not None else "in_progress",
                job_id=claim.job_id,
                retryable=bool(
                    row is not None and memory_digest_job_retryable(row)
                ),
                error_type="source_changed" if row is not None else "lease_lost",
            )

        write_result = _write_memory_digest_rows(
            write_db,
            session_id=current_session_id,
            target_date=target_date,
            logs=fresh_logs,
            result=result,
            force=bool(force or retry_failed),
            generation_job_id=claim.job_id,
        )
        if not write_result:
            raise RuntimeError("memory_digest_rows_not_written")
        digest_ids = list(write_result["digest_ids"])
        finish_memory_digest_job(
            write_db,
            claim,
            result_digest_count=len(digest_ids),
            result_source_id=str(write_result["logical_source_id"]),
            result_root_digest_id=int(digest_ids[0]),
            result_semantic_job_id=int(write_result["semantic_job_id"]),
            meta=_memory_digest_job_trace_meta(result.meta),
        )
        write_db.commit()
    except MemoryDigestJobLeaseLost:
        write_db.rollback()
        return _memory_digest_result_payload(
            session_id=current_session_id,
            status="in_progress",
            job_id=claim.job_id,
            error_type="lease_lost",
        )
    except Exception as exc:
        write_db.rollback()
        logger.error(
            "MemoryDigest write failed: session_id=%s error_type=%s",
            current_session_id,
            type(exc).__name__,
        )
        return _settle_memory_digest_failure(
            claim,
            session_id=current_session_id,
            error_type="write_failed",
            retryable=True,
        )
    finally:
        write_db.close()

    return _memory_digest_result_payload(
        session_id=current_session_id,
        status="created",
        job_id=claim.job_id,
    )


async def generate_daily_digest_for_date_report(
    target_date: str,
    user_id: str | None = None,
    *,
    session_id: str | None = None,
    force: bool = False,
    retry_failed: bool = False,
    use_llm: bool | None = None,
    llm_summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
) -> dict[str, Any]:
    """生成结构化运行报告；LLM 调用期间不持有数据库会话。"""

    normalized_target_date = str(target_date or "").strip()
    try:
        parsed_target_date = datetime.strptime(
            normalized_target_date,
            "%Y-%m-%d",
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("target_date must be a valid YYYY-MM-DD date") from exc
    if parsed_target_date.strftime("%Y-%m-%d") != normalized_target_date:
        raise ValueError("target_date must be a valid YYYY-MM-DD date")
    try:
        parsed_target_date + timedelta(days=1)
    except OverflowError as exc:
        raise ValueError("target_date is outside the supported date range") from exc
    target_date = normalized_target_date

    maintenance_db = SessionLocal()
    try:
        recovered_jobs = recover_expired_memory_digest_jobs(maintenance_db)
        if recovered_jobs:
            logger.warning(
                "Recovered %d expired MemoryDigest job(s)",
                recovered_jobs,
            )
    finally:
        maintenance_db.close()

    snapshot_db = SessionLocal()
    try:
        by_session = _collect_daily_digest_logs_by_session(
            snapshot_db,
            target_date=target_date,
            user_id=user_id,
            session_id=session_id,
        )
    finally:
        snapshot_db.close()
    if not by_session:
        return _memory_digest_report(
            target_date=target_date,
            results=[],
            no_input=True,
        )

    results = []
    for current_session_id, logs in by_session.items():
        try:
            item = await _generate_memory_digest_session_report(
                target_date=target_date,
                current_session_id=current_session_id,
                logs=logs,
                source_user_id=user_id,
                force=force,
                retry_failed=retry_failed,
                use_llm=use_llm,
                llm_summarizer=llm_summarizer,
            )
        except Exception as exc:
            logger.error(
                "MemoryDigest session processing failed: session_id=%s error_type=%s",
                current_session_id,
                type(exc).__name__,
            )
            item = _memory_digest_result_payload(
                session_id=current_session_id,
                status="failed",
                error_type="session_processing_failed",
            )
        results.append(item)
    report = _memory_digest_report(target_date=target_date, results=results)
    if report["created_sessions"]:
        logger.info(
            "Daily digest generated for %d session(s), date=%s",
            report["created_sessions"],
            target_date,
        )
    return report


async def generate_daily_digest_for_date_async(
    target_date: str,
    user_id: str | None = None,
    *,
    session_id: str | None = None,
    force: bool = False,
    use_llm: bool | None = None,
    llm_summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
) -> int:
    """
    兼容旧调用方的异步入口，只返回成功创建的 session 数。
    """
    report = await generate_daily_digest_for_date_report(
        target_date=target_date,
        user_id=user_id,
        session_id=session_id,
        force=force,
        use_llm=use_llm,
        llm_summarizer=llm_summarizer,
    )
    return int(report["created_sessions"])


def run_daily_digest_once() -> int:
    # Summarize yesterday by default so the day is complete.
    from core.async_bridge import run_awaitable_sync

    yesterday = _to_day(db_now_naive() - timedelta(days=1))
    return run_awaitable_sync(generate_daily_digest_for_date_async(yesterday))


def daily_digest_scheduler(stop_event: threading.Event) -> None:
    """常驻记忆折叠调度器，短轮询热加载启停状态和执行小时。"""
    logger.info("Memory digest scheduler started")
    poll_seconds = 30
    catch_up_pending = True
    last_run_day: str | None = None
    while not stop_event.is_set():
        enabled = settings.get_bool("memory_digest.scheduler_enabled", True)
        run_hour = max(
            0,
            min(23, settings.get_int("memory_digest.schedule_hour", 4)),
        )
        schedule = (enabled, run_hour)
        now = db_now_naive()
        today = _to_day(now)

        if not enabled:
            catch_up_pending = True
            if stop_event.wait(poll_seconds):
                break
            continue

        if catch_up_pending:
            if last_run_day != today:
                run_daily_digest_once()
                last_run_day = today
            catch_up_pending = False

        delay = _next_run_delay_seconds(now, run_hour)
        schedule_changed = False
        while delay > 0 and not stop_event.is_set():
            step = min(poll_seconds, delay)
            if stop_event.wait(step):
                break
            refreshed = (
                settings.get_bool("memory_digest.scheduler_enabled", True),
                max(
                    0,
                    min(23, settings.get_int("memory_digest.schedule_hour", 4)),
                ),
            )
            if refreshed != schedule:
                schedule_changed = True
                break
            delay -= step

        if stop_event.is_set():
            break
        if schedule_changed:
            continue

        current_day = _to_day(db_now_naive())
        if current_day != last_run_day:
            run_daily_digest_once()
            last_run_day = current_day

    logger.info("Memory digest scheduler stopped")


# ── QQ 推送 ──


# 模块级单例 ClientSession：同一事件循环内复用连接池，避免逐请求握手开销。
# 注意 aiohttp ClientSession 绑定到创建它的 loop，跨 loop（如 daemon thread 的
# fresh loop）必须重建，因此按 running loop 校验。
_push_session: aiohttp.ClientSession | None = None
_push_session_loop: asyncio.AbstractEventLoop | None = None
_push_session_lock = asyncio.Lock()


async def _get_push_session() -> aiohttp.ClientSession:
    """返回当前 loop 的复用 ClientSession；loop 不匹配则重建。"""
    global _push_session, _push_session_loop
    loop = asyncio.get_running_loop()
    async with _push_session_lock:
        if _push_session is not None and _push_session_loop is loop and not _push_session.closed:
            return _push_session
        # 旧 session 属于别的 loop 或已关闭——先关闭再重建
        if _push_session is not None and not _push_session.closed:
            try:
                await _push_session.close()
            except Exception:
                logger.debug("[push] stale session close failed", exc_info=True)
        _push_session = aiohttp.ClientSession()
        _push_session_loop = loop
        return _push_session


async def close_push_session() -> None:
    """关闭并清空模块级 push session（lifespan 关闭 / 测试隔离用）。"""
    global _push_session, _push_session_loop
    async with _push_session_lock:
        if _push_session is not None and not _push_session.closed:
            try:
                await _push_session.close()
            except Exception:
                logger.debug("[push] session close failed", exc_info=True)
        _push_session = None
        _push_session_loop = None


async def push_to_qq(target_type: str, target_id: str, message: str) -> bool | None:
    """推送消息到 QQ（通过 qqbot 的 /nanobot/push 端点）。"""
    try:
        session = await _get_push_session()
    except Exception as exc:
        logger.error("Push error: %s", safe_response_summary(exc))
        return None
    return await push_to_qq_with_session(
        session,
        target_type,
        target_id,
        message,
    )


async def push_to_qq_with_session(
    session: aiohttp.ClientSession,
    target_type: str,
    target_id: str,
    message: str,
) -> bool | None:
    """使用调用方 session 推送，并保持旧调用方的精确三态语义。"""

    outcome = await push_to_qq_outcome_with_session(
        session,
        target_type,
        target_id,
        message,
    )
    return delivery_outcome_to_legacy(outcome)


async def push_to_qq_outcome_with_session(
    session: aiohttp.ClientSession,
    target_type: str,
    target_id: str,
    message: str,
) -> DeliveryOutcome:
    """使用调用方 session 推送并返回结构化传输结果。"""

    return await deliver_qq_push_with_session(
        session,
        push_url=QQBOT_PUSH_URL,
        push_token=resolve_qq_push_token(),
        target_type=target_type,
        target_id=target_id,
        message=message,
        timeout_seconds=QQBOT_PUSH_TIMEOUT,
    )


async def push_envelope_to_qq(
    target_type: str,
    target_id: str,
    envelope: Mapping[str, Any] | None,
) -> bool | None:
    """通过 QQ 出站渲染器派生旧 QQbot push message。"""
    message = _render_qq_push_envelope(target_type, target_id, envelope)
    if message is None:
        return False
    return await push_to_qq(target_type, target_id, message)


def _render_qq_push_envelope(
    target_type: str,
    target_id: str,
    envelope: Mapping[str, Any] | None,
) -> str | None:
    from core.qq_outbound_renderer import render_qq_outbound_envelope

    rendered = render_qq_outbound_envelope(envelope, allow_base64=False)
    if rendered.warnings:
        logger.warning(
            "QQ outbound render warnings target_type=%s warnings=%s",
            target_type,
            rendered.warnings,
        )
    message = rendered.message
    if not message.strip():
        logger.warning(
            "Skip empty QQ push envelope target_type=%s",
            target_type,
        )
        return None
    return message


async def push_envelope_to_qq_with_session(
    session: aiohttp.ClientSession,
    target_type: str,
    target_id: str,
    envelope: Mapping[str, Any] | None,
) -> bool | None:
    """使用调用方 session 渲染推送，并保持旧三态语义。"""
    outcome = await push_envelope_to_qq_outcome_with_session(
        session,
        target_type,
        target_id,
        envelope,
    )
    if outcome is None:
        return False
    return delivery_outcome_to_legacy(outcome)


async def push_envelope_to_qq_outcome_with_session(
    session: aiohttp.ClientSession,
    target_type: str,
    target_id: str,
    envelope: Mapping[str, Any] | None,
) -> DeliveryOutcome | None:
    """渲染 envelope，并返回结构化传输结果；空消息不发起请求。"""
    message = _render_qq_push_envelope(target_type, target_id, envelope)
    if message is None:
        return None
    return await push_to_qq_outcome_with_session(
        session,
        target_type,
        target_id,
        message,
    )


# ── 定时任务调度 ──


async def _generate_task_message(
    task: ScheduledTask,
    *,
    trace_id: str = "",
) -> str | None:
    """通过隔离 Agent Gateway 执行定时任务，让模型拥有真实工具调用能力。"""
    from core.agent_runtime.gateway import create_isolated_agent_gateway

    bridge = create_isolated_agent_gateway()
    try:
        await bridge.start()
        metadata = _scheduled_task_metadata(task)
        if trace_id:
            metadata["trace_id"] = trace_id
        response = await asyncio.wait_for(
            bridge.handle_message(
                _build_scheduled_task_query(task),
                user_id=str(metadata.get("user_id") or ""),
                session_id=_scheduled_task_session_id(task),
                sender_name="定时任务",
                metadata=metadata,
            ),
            timeout=600,
        )
        return response or None
    except Exception as exc:
        logger.error(
            "定时任务生成失败 task_id=%s error_type=%s",
            task.id or "unknown",
            type(exc).__name__,
        )
        raise
    finally:
        try:
            await bridge.stop()
        except Exception as exc:
            logger.warning(
                "定时任务 Bridge 停止失败 task_id=%s error_type=%s",
                task.id or "unknown",
                type(exc).__name__,
            )


def _local_naive_to_utc(local_now: datetime) -> datetime:
    """把上海墙钟 naive 时间转换为 UTC naive(next_fire_at 的约定)。"""

    localized = (
        local_now.replace(tzinfo=SHANGHAI)
        if local_now.tzinfo is None
        else local_now.astimezone(SHANGHAI)
    )
    return localized.astimezone(timezone.utc).replace(tzinfo=None)


def _advance_task_after_slot(
    task: ScheduledTask,
    spec: dict,
    *,
    slot: datetime,
) -> None:
    """槽已消费(领取/去重/失败记账)后推进下一次触发。

    once 不再有下一次:清空 next_fire_at 并禁用,视为已完成。
    """

    advanced = next_fire_after(spec, base_utc=slot)
    if advanced is None:
        task.enabled = 0
        task.next_fire_at = None
        logger.info(
            "Scheduled one-shot completed task_id=%s",
            task.id,
        )
    else:
        task.next_fire_at = advanced


async def run_scheduled_tasks(at: datetime | None = None) -> int:
    """只发现到期 trigger、冻结 execution 并推进 ``next_fire_at``。

    本函数不调用模型、不执行工具、不等待投递；执行由独立 workflow worker
    完成。数据库中的 ``next_fire_at`` 是恢复事实源，不再依赖内存分钟槽。
    """
    created = 0
    local_now = at or db_now_naive()
    now_utc = _local_naive_to_utc(local_now)
    discovery_db = SessionLocal()
    try:
        task_ids = [
            int(row[0])
            for row in (
                discovery_db.query(ScheduledTask.id)
                .filter(
                    ScheduledTask.enabled == 1,
                    ScheduledTask.owner_migration_required == 0,
                    ScheduledTask.owner_chat_stream_id != "",
                )
                .order_by(ScheduledTask.id.asc())
                .all()
            )
        ]
    finally:
        discovery_db.close()

    for task_id in task_ids:
        db = SessionLocal()
        try:
            task = db.get(ScheduledTask, task_id)
            if task is None or not task.enabled:
                continue
            spec = spec_from_fields(
                task.schedule_kind,
                task.schedule_spec,
                task.cron_expr,
            )
            if spec is None:
                logger.warning(
                    "Scheduled task schedule unparseable task_id=%s",
                    task_id,
                )
                continue
            if task.next_fire_at is None:
                initialized = next_fire_after(
                    spec,
                    base_utc=initial_anchor(now_utc),
                    include_missed=True,
                )
                if initialized is None:
                    task.enabled = 0
                    db.commit()
                    logger.info(
                        "Scheduled task has no future fire task_id=%s",
                        task_id,
                    )
                    continue
                task.next_fire_at = initialized
                db.commit()
            # 一次扫描可消费多个仍在宽限期内的数据库槽，恢复不再依赖
            # ``scheduled_task_loop`` 保存的最近 10 个内存分钟。
            consumed = 0
            while (
                task.enabled
                and task.next_fire_at is not None
                and task.next_fire_at <= now_utc
                and consumed < 100
            ):
                slot = task.next_fire_at
                late_seconds = (now_utc - slot).total_seconds()
                if late_seconds > grace_seconds(spec, base_utc=slot):
                    _advance_task_after_slot(task, spec, slot=now_utc)
                    db.commit()
                    logger.warning(
                        "Scheduled task missed beyond grace task_id=%s "
                        "late_seconds=%s",
                        task_id,
                        int(late_seconds),
                    )
                    break
                result = enqueue_scheduled_task_execution(
                    db,
                    task_id=task_id,
                    trigger_type="scheduled",
                    scheduled_for=slot,
                    now=now_utc,
                )
                _advance_task_after_slot(task, spec, slot=slot)
                db.commit()
                consumed += 1
                if not result.deduplicated:
                    created += 1
                logger.info(
                    "Scheduled task execution queued task_id=%s "
                    "execution_id=%s status=%s deduplicated=%s",
                    task_id,
                    result.execution_id,
                    result.status,
                    result.deduplicated,
                )
            if consumed >= 100 and task.next_fire_at is not None:
                logger.warning(
                    "Scheduled task 单次扫描达到 100 个 occurrence 上限 "
                    "task_id=%s next_fire_at=%s",
                    task_id,
                    task.next_fire_at,
                )
        except Exception as exc:
            db.rollback()
            logger.error(
                "Scheduled task producer failed task_id=%s error_type=%s",
                task_id,
                type(exc).__name__,
            )
        finally:
            db.close()
    return created


def _scheduled_workflow_concurrency() -> int:
    try:
        value = int(
            os.environ.get(
                "NANOBOT_SCHEDULED_WORKFLOW_CONCURRENCY",
                str(DEFAULT_WORKFLOW_CONCURRENCY),
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_WORKFLOW_CONCURRENCY
    return max(1, min(32, value))


async def run_scheduled_task_workflows_once():
    """有界并发执行一批已冻结 execution。

    delivery worker 早于 Agent Runtime 启动；Adapter 尚未由组合根绑定时只
    返回空批次，不领取 execution，避免在运行时未就绪阶段制造失败尝试。
    """

    callbacks = create_scheduled_workflow_callbacks()
    if callbacks is None:
        return ScheduledWorkflowWorkerResult(
            claimed=0,
            succeeded=0,
            waiting=0,
            failed=0,
            blocked=0,
            ambiguous=0,
        )
    return await run_scheduled_task_workflow_worker(
        session_factory=SessionLocal,
        callbacks=callbacks,
        max_concurrency=_scheduled_workflow_concurrency(),
    )


async def _scheduled_task_workflow_loop(
    stop_event: threading.Event,
    *,
    idle_poll_seconds: float = 1.0,
) -> None:
    """独立 worker 循环；慢任务不占用 trigger 扫描循环。"""

    legacy_recovery_due = True
    while not stop_event.is_set():
        worked = False
        try:
            if legacy_recovery_due:
                recovered = await recover_expired_scheduled_task_occurrences(
                    session_factory=SessionLocal,
                    generator=_generate_task_message,
                )
                worked = bool(recovered)
                legacy_recovery_due = False
            result = await run_scheduled_task_workflows_once()
            worked = worked or result.claimed > 0
        except Exception as exc:
            logger.error(
                "Scheduled workflow worker failed error_type=%s",
                type(exc).__name__,
            )
        if worked:
            continue
        remaining = max(0.1, float(idle_poll_seconds))
        while remaining > 0 and not stop_event.is_set():
            step = min(0.5, remaining)
            await asyncio.sleep(step)
            remaining -= step


async def scheduled_task_loop(stop_event: threading.Event, *, poll_interval_seconds: float = 60.0) -> None:
    """异步入口：数据库 trigger 扫描与 workflow worker 并行运行。"""
    logger.info("Scheduled task runner started")
    worker_task = asyncio.create_task(
        _scheduled_task_workflow_loop(stop_event)
    )
    try:
        while not stop_event.is_set():
            try:
                await run_scheduled_tasks(at=db_now_naive())
            except Exception as exc:
                logger.error(
                    "Scheduled task trigger scan failed error_type=%s",
                    type(exc).__name__,
                )

            # 对齐下一分钟墙钟边界；DB next_fire_at 负责补扫和恢复。
            now = db_now_naive()
            next_minute = (now + timedelta(minutes=1)).replace(
                second=0,
                microsecond=0,
            )
            remaining = max(
                1.0,
                min(
                    float(poll_interval_seconds),
                    (next_minute - now).total_seconds(),
                ),
            )
            while remaining > 0 and not stop_event.is_set():
                step = min(1.0, remaining)
                await asyncio.sleep(step)
                remaining -= step
    finally:
        if not worker_task.done():
            worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)
    logger.info("Scheduled task runner stopped")


def _run_scheduled_task_loop(stop_event: threading.Event) -> None:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(scheduled_task_loop(stop_event))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
            shutdown_default_executor = getattr(loop, "shutdown_default_executor", None)
            if shutdown_default_executor is not None:
                loop.run_until_complete(shutdown_default_executor())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def scheduled_task_runner(stop_event: threading.Event) -> None:
    """后台线程入口：在线程内持有一个事件循环运行异步调度主循环。"""
    _run_scheduled_task_loop(stop_event)
