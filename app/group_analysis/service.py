"""群分析应用编排；不依赖 KT 工具类型或传输格式。"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Mapping

from app.group_analysis import cache
from app.group_analysis.application_service import (
    GroupAnalysisApplicationService,
    build_group_analysis_learning_request,
)
from app.group_analysis.local_rag import (
    should_use_group_analysis_local_rag,
)
from app.group_analysis.preprocess import (
    build_analysis_payload,
    dedupe_group_logs,
    filter_analyzable_logs,
    resolve_analysis_window_hours,
)
from app.group_analysis.render import format_error_html
from app.group_analysis.repository import GroupAnalysisRepository
from app.group_learning.pipeline_service import (
    build_group_learning_processor,
)
from core.chat_stream_identity import resolve_chat_stream_identity
from core.db import UnitOfWork, release_clean_session_transaction


logger = logging.getLogger("nanobot.app.group_analysis")

GROUP_ANALYSIS_MAX_LOGS = int(
    os.environ.get("GROUP_ANALYSIS_MAX_LOGS", "5000")
)
GROUP_ANALYSIS_PROMPT_CHAR_BUDGET = int(
    os.environ.get("GROUP_ANALYSIS_PROMPT_CHAR_BUDGET", "60000")
)
GROUP_ANALYSIS_STYLE_PROMPT_CHAR_BUDGET = int(
    os.environ.get("GROUP_ANALYSIS_STYLE_PROMPT_CHAR_BUDGET", "24000")
)


def _resolve_tool_aspects(
    args: Mapping[str, Any],
) -> tuple[str, ...]:
    from core.group_learning import (
        default_tool_aspects,
        validate_aspect_selection,
    )

    raw_aspects = args.get("aspects")
    if "aspects" in args and raw_aspects is not None:
        return validate_aspect_selection(raw_aspects)

    try:
        from core.lifecycle import record_compatibility_usage

        record_compatibility_usage(
            "schema.group_analysis_omitted_aspects"
        )
    except Exception:
        logger.warning(
            "[group_analysis] compatibility usage telemetry failed"
        )
    return default_tool_aspects()


async def execute_group_analysis(args: Mapping[str, Any]) -> str:
    """执行群分析并返回 HTML；框架 Adapter 决定如何传输结果。"""

    group_id = str(args.get("group_id", "")).strip()
    instructions = str(args.get("instructions", "")).strip()
    if not group_id:
        raise ValueError("Missing 'group_id' argument")

    try:
        started_at = time.monotonic()
        aspects = _resolve_tool_aspects(args)
        with UnitOfWork() as uow:
            db = uow.db
            if db is None:
                raise RuntimeError("UnitOfWork session is not open")
            repo = GroupAnalysisRepository(db)
            group = repo.resolve_group(group_id)
            if not group:
                candidates = repo.get_group_candidates(group_id)
                if candidates:
                    lines = [
                        f"group_{candidate['id']} — {candidate['name']}"
                        for candidate in candidates[:10]
                    ]
                    return format_error_html(
                        "匹配到多个群",
                        (
                            f"关键词 \"{group_id}\" 匹配到 "
                            f"{len(candidates)} 个群，请使用精确的群号或群名："
                        ),
                        lines,
                    )
                return format_error_html(
                    "未找到群",
                    f"未找到群 \"{group_id}\"，群号或群名不匹配。",
                )

            window_hours = resolve_analysis_window_hours(
                args.get("window_hours"),
                instructions,
            )
            batch = repo.fetch_group_logs(
                group,
                window_hours=window_hours,
                limit=GROUP_ANALYSIS_MAX_LOGS,
            )
            cached = cache.get_cached_report(
                group.group_id,
                window_hours or 0,
                instructions,
                batch.latest_log_id,
                batch.raw_count,
                aspects=aspects,
            )
            if cached:
                cache.remember_group_analysis_report(cached)
                logger.info(
                    "[group_analysis] cache_hit=true group=%s",
                    group.group_id,
                )
                return cached

            eligible_logs = filter_analyzable_logs(batch.logs)
            logs = dedupe_group_logs(eligible_logs)
            if not logs:
                return format_error_html(
                    "消息不足",
                    f"群 {group.name} 暂无消息记录。",
                )

            preprocess_started_at = time.monotonic()
            from core.semantic.provider_factory import (
                get_embedding_provider,
                get_rag_runtime_config,
                get_reranker_provider,
            )

            rag_runtime = get_rag_runtime_config("group_analysis")
            enable_local_rag = (
                rag_runtime.enabled
                and should_use_group_analysis_local_rag(instructions)
            )
            local_rag_reranker = (
                get_reranker_provider()
                if enable_local_rag and rag_runtime.reranker_enabled
                else None
            )
            if (
                enable_local_rag
                and rag_runtime.reranker_enabled
                and local_rag_reranker is None
                and not rag_runtime.allow_degraded
            ):
                enable_local_rag = False
            payload = build_analysis_payload(
                logs,
                prompt_budget=GROUP_ANALYSIS_PROMPT_CHAR_BUDGET,
                style_budget=GROUP_ANALYSIS_STYLE_PROMPT_CHAR_BUDGET,
                local_rag_query=instructions if enable_local_rag else "",
                enable_local_rag=enable_local_rag,
                embedding_provider=(
                    get_embedding_provider()
                    if enable_local_rag
                    else None
                ),
                reranker_provider=(
                    local_rag_reranker
                    if enable_local_rag
                    else None
                ),
            )
            payload["group_stats"]["analysis_window"] = (
                "全部历史"
                if window_hours is None
                else f"最近{window_hours}小时"
            )
            preprocess_ms = round(
                (time.monotonic() - preprocess_started_at) * 1000
            )
            if len(payload["messages"]) < 3:
                return format_error_html(
                    "消息不足",
                    f"群 {group.name} 可分析的消息不足（需≥3条）。",
                )

            if not release_clean_session_transaction(
                db,
                label="group_analysis_before_llm",
                logger=logger,
            ):
                in_transaction = getattr(
                    db,
                    "in_transaction",
                    None,
                )
                if callable(in_transaction) and in_transaction():
                    raise RuntimeError(
                        "群分析在模型调用前仍持有数据库事务"
                    )
            llm_started_at = time.monotonic()
            identity = resolve_chat_stream_identity(
                platform="qq",
                chat_type="group",
                session_id=group.group_id,
            )
            learning_request = build_group_analysis_learning_request(
                chat_stream_id=identity.chat_stream_id,
                aspects=aspects,
                payload=payload,
                trigger="tool",
                cursor_start_chat_log_id=0,
                cursor_end_chat_log_id=int(
                    batch.latest_log_id or 0
                ),
            )
            application_result = await GroupAnalysisApplicationService(
                learning_pipeline=build_group_learning_processor(db),
            ).process_payload(
                aspects=aspects,
                payload=payload,
                group_name=group.name,
                instructions=instructions,
                learning_request=learning_request,
            )
            llm_ms = round(
                (time.monotonic() - llm_started_at) * 1000
            )
            learning_outcome = application_result.learning_outcome
            if (
                learning_outcome is not None
                and learning_outcome.status == "failed"
                and learning_outcome.error_code
                != "group_learning_disabled"
            ):
                logger.warning(
                    "[group_analysis] learning failed code=%s retryable=%s",
                    learning_outcome.error_code,
                    learning_outcome.retryable,
                )

            render_started_at = time.monotonic()
            report = application_result.report
            render_ms = round(
                (time.monotonic() - render_started_at) * 1000
            )
            cache.set_cached_report(
                group.group_id,
                window_hours or 0,
                instructions,
                batch.latest_log_id,
                batch.raw_count,
                report,
                aspects=aspects,
            )
            cache.remember_group_analysis_report(report)

            logger.info(
                "[group_analysis] group=%s window=%sh raw=%d "
                "deduped=%d cleaned=%d msg_chars=%d style_chars=%d "
                "preprocess_ms=%d llm_ms=%d render_ms=%d "
                "total_ms=%d cache_hit=false",
                group.group_id,
                window_hours or 0,
                batch.raw_count,
                len(eligible_logs),
                len(payload["messages"]),
                len(payload["msg_text"]),
                len(payload["style_msg_text"]),
                preprocess_ms,
                llm_ms,
                render_ms,
                round((time.monotonic() - started_at) * 1000),
            )
            return report
    except Exception as exc:
        logger.error(
            "[group_analysis] Failed: %s",
            exc,
            exc_info=True,
        )
        return format_error_html(
            "群聊分析失败",
            "工具执行时发生异常。",
            [str(exc)[:300]],
        )
