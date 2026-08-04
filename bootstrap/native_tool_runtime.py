"""Native Runtime 工具应用服务的 Composition Root。"""

from __future__ import annotations

from typing import Any

from app.tool_services.runtime_execution import (
    ServiceCallable,
    build_registered_tool_execution_port,
)
from core.agent_runtime.request_scope import (
    get_current_runtime_context,
    is_runtime_request_dry_run,
)
from core.tool_contracts.result import ToolServiceResult


async def _execute_ai_daily(args: dict[str, Any]) -> ToolServiceResult:
    from app.tool_services.ai_daily import execute_ai_daily
    from creatures.nanobot.prompts.skills.news_search import runtime_cache
    from nanobot_kt.tools.ai_daily import (
        _get_cached_news_result,
        _render_ai_daily_fallback,
        _run_news_daily_pipeline,
        _store_cached_news_result,
    )

    return await execute_ai_daily(
        args,
        pipeline=_run_news_daily_pipeline,
        make_cache_key=runtime_cache.make_ai_daily_cache_key,
        read_cache=_get_cached_news_result,
        write_cache=_store_cached_news_result,
        render_fallback=_render_ai_daily_fallback,
    )


async def _execute_group_analysis(args: dict[str, Any]) -> ToolServiceResult:
    from app.group_analysis.service import execute_group_analysis
    from core.tool_contracts.rich_output import build_rich_output

    try:
        html = await execute_group_analysis(args)
    except ValueError as exc:
        return ToolServiceResult(error=str(exc))
    return ToolServiceResult(
        output=build_rich_output(html, report_kind="group_analysis"),
        exit_code=0,
    )


async def _execute_image_generation(args: dict[str, Any]) -> ToolServiceResult:
    from app.tool_services.image_generation import execute_image_generation
    from bootstrap.media_tool_runtime import ImageGenerationProviderAdapter
    from config import (
        IMAGE_GENERATION_MODEL,
        IMAGE_GENERATION_PROMPT_MAX_CHARS,
    )

    provider = ImageGenerationProviderAdapter()
    return await execute_image_generation(
        args,
        generate=provider.generate,
        model=str(IMAGE_GENERATION_MODEL or ""),
        prompt_max_chars=IMAGE_GENERATION_PROMPT_MAX_CHARS,
    )


async def _execute_image_summary(args: dict[str, Any]) -> ToolServiceResult:
    from app.tool_services.image_summary import execute_image_summary
    from bootstrap.media_tool_runtime import ImageSummaryProviderAdapter

    provider = ImageSummaryProviderAdapter()
    return await execute_image_summary(
        args,
        summarize=lambda files, focus: provider.summarize(
            tuple(files),
            focus,
        ),
    )


async def _execute_memory_query(args: dict[str, Any]) -> object:
    from nanobot_kt.tools.memory_query import execute_memory_query

    return await execute_memory_query(args)


async def _execute_persona_update(args: dict[str, Any]) -> ToolServiceResult:
    from app.persona.update_service import execute_persona_update

    runtime_context = get_current_runtime_context() or {}
    return await execute_persona_update(
        args,
        user_id=str(runtime_context.get("user_id") or "").strip(),
    )


def _execute_reply(args: dict[str, Any]) -> ToolServiceResult:
    from app.tool_services.reply import execute_reply

    return execute_reply(args, dry_run=is_runtime_request_dry_run())


def _execute_no_reply(args: dict[str, Any]) -> ToolServiceResult:
    from app.tool_services.reply import execute_no_reply

    return execute_no_reply(args)


async def _execute_schedule_task(args: dict[str, Any]) -> ToolServiceResult:
    from app.tool_services.schedule_task import ScheduleTaskService

    return await ScheduleTaskService().execute(args)


async def _execute_skill(args: dict[str, Any]) -> ToolServiceResult:
    from app.tool_services.skill import execute_skill

    return await execute_skill(args)


def _execute_sql_analysis(args: dict[str, Any]) -> ToolServiceResult:
    from app.tool_services.sql_analysis import execute_sql_analysis
    from sandbox import AnalysisSandbox

    return execute_sql_analysis(
        args.get("sql", ""),
        sandbox_factory=AnalysisSandbox,
    )


async def _execute_web_search(args: dict[str, Any]) -> ToolServiceResult:
    from app.tool_services.web_search import execute_web_search
    from core.web_search.search_runtime import search_enabled_providers

    return await execute_web_search(args, search=search_enabled_providers)


def _service_bindings() -> dict[str, ServiceCallable]:
    from app.tool_services.knowledge_query import execute_knowledge_query
    from app.tool_services.sandbox import execute_sandbox_tool
    from app.tool_services.sticker_search import execute_sticker_search
    from app.tool_services.session_plan import (
        execute_session_plan_read,
        execute_session_plan_write,
    )

    bindings: dict[str, ServiceCallable] = {
        "tool.ai_daily.execute": _execute_ai_daily,
        "tool.group_analysis.execute": _execute_group_analysis,
        "tool.image_generation.execute": _execute_image_generation,
        "tool.image_summary.execute": _execute_image_summary,
        "tool.knowledge_query.execute": execute_knowledge_query,
        "tool.memory_query.execute": _execute_memory_query,
        "tool.no_reply.execute": _execute_no_reply,
        "tool.persona_update.execute": _execute_persona_update,
        "tool.reply.execute": _execute_reply,
        "tool.schedule_task.execute": _execute_schedule_task,
        "tool.session_plan_read.execute": execute_session_plan_read,
        "tool.session_plan_write.execute": execute_session_plan_write,
        "tool.skill.execute": _execute_skill,
        "tool.sql_analysis.execute": _execute_sql_analysis,
        "tool.sticker_search.execute": execute_sticker_search,
        "tool.web_search.execute": _execute_web_search,
    }
    for tool_name in (
        "asset_import",
        "asset_publish",
        "sandbox_exec",
        "sandbox_poll",
        "sandbox_terminate",
        "sandbox_write_stdin",
        "workspace_edit",
        "workspace_read",
        "workspace_search",
        "workspace_write",
    ):
        bindings[f"tool.{tool_name}.execute"] = (
            lambda args, name=tool_name: execute_sandbox_tool(name, args)
        )
    return bindings


def build_native_tool_execution_port():
    return build_registered_tool_execution_port(_service_bindings())


__all__ = ["build_native_tool_execution_port"]
