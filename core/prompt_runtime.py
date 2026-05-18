"""运行时 PromptManager 接入辅助。

这里不做检索、记忆筛选或上下文裁剪，只在模型调用前按模式渲染模板，
并在失败时回退到调用方传入的 legacy messages/content。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("nanobot.prompt_runtime")


def get_prompt_system_mode(default: str = "shadow") -> str:
    try:
        from core.settings_service import settings

        mode = str(settings.get("prompt_system.mode", default) or default).strip().lower()
    except Exception:
        mode = default
    return mode if mode in {"legacy", "shadow", "managed"} else default


def _trace_ids(trace_id: str = "", run_id: str = "") -> tuple[str, str]:
    if trace_id or run_id:
        return trace_id, run_id
    try:
        from core.tracing_context import get_trace_context

        return get_trace_context()
    except Exception:
        return "", ""


def render_model_messages(
    prompt_key: str,
    variables: dict[str, Any],
    legacy_messages: list[dict[str, Any]],
    *,
    mode: str | None = None,
    trace_id: str = "",
    run_id: str = "",
) -> list[dict[str, Any]]:
    """按 legacy/shadow/managed 模式为模型调用准备 messages。"""
    active_mode = mode or get_prompt_system_mode()
    if active_mode == "legacy" or not prompt_key:
        return legacy_messages
    trace_id, run_id = _trace_ids(trace_id, run_id)
    try:
        from core.prompts import get_prompt_manager

        rendered = get_prompt_manager().render(
            prompt_key,
            variables,
            trace_id=trace_id,
            run_id=run_id,
            mode=active_mode,
            strict=False,
        )
        if active_mode == "managed":
            return list(rendered.messages)
    except Exception as e:
        logger.warning(
            "[PromptManager] model-call render failed key=%s mode=%s fallback=legacy error=%s",
            prompt_key,
            active_mode,
            e,
        )
        try:
            from core.tracing import PromptTracer

            PromptTracer.record_render(
                trace_id=trace_id,
                run_id=run_id,
                prompt_key=prompt_key,
                mode=active_mode,
                variables=variables,
                error=str(e),
            )
        except Exception:
            pass
    return legacy_messages


def render_prompt_content(
    prompt_key: str,
    variables: dict[str, Any],
    legacy_content: str,
    *,
    mode: str | None = None,
    trace_id: str = "",
    run_id: str = "",
) -> str:
    """按模式渲染单段 prompt 文本；shadow 只记录，managed 返回新内容。"""
    active_mode = mode or get_prompt_system_mode()
    if active_mode == "legacy" or not prompt_key:
        return legacy_content
    trace_id, run_id = _trace_ids(trace_id, run_id)
    try:
        from core.prompts import get_prompt_manager

        rendered = get_prompt_manager().render(
            prompt_key,
            variables,
            trace_id=trace_id,
            run_id=run_id,
            mode=active_mode,
            strict=False,
        )
        if active_mode == "managed":
            return rendered.content
    except Exception as e:
        logger.warning(
            "[PromptManager] content render failed key=%s mode=%s fallback=legacy error=%s",
            prompt_key,
            active_mode,
            e,
        )
        try:
            from core.tracing import PromptTracer

            PromptTracer.record_render(
                trace_id=trace_id,
                run_id=run_id,
                prompt_key=prompt_key,
                mode=active_mode,
                variables=variables,
                error=str(e),
            )
        except Exception:
            pass
    return legacy_content
