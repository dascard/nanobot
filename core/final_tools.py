"""最终工具业务解析；请求过滤原语位于 LLM foundation。"""

from __future__ import annotations

from typing import Any

from foundation.llm.tool_policy import (
    FinalToolSet,
    filter_payload_tools,
    filter_sdk_kwargs,
    final_tools_scope,
    get_current_final_tools,
    reset_current_final_tools,
    set_current_final_tools,
    tool_name,
)


def resolve_final_tools(
    chat_type: str = "group",
    group_id: str = "",
    user_id: str = "",
    platform: str = "",
    runtime_preset: str = "full",
    db: Any = None,
) -> FinalToolSet:
    """把 RuntimeTool 结果转成真实请求出口使用的最终工具集。"""

    from core.runtime_tool_service import resolve_effective_tools

    enabled, disabled = resolve_effective_tools(
        chat_type=chat_type,
        group_id=group_id,
        user_id=user_id,
        platform=platform,
        runtime_preset=runtime_preset,
        db=db,
    )
    allowed = {name for name, is_enabled in enabled.items() if is_enabled}
    return FinalToolSet(
        allowed=allowed,
        disabled=dict(disabled or {}),
        sent_tools=sorted(allowed),
        hidden_framework_tools=["skill"],
        enabled=dict(enabled),
    )


__all__ = [
    "FinalToolSet",
    "filter_payload_tools",
    "filter_sdk_kwargs",
    "final_tools_scope",
    "get_current_final_tools",
    "reset_current_final_tools",
    "resolve_final_tools",
    "set_current_final_tools",
    "tool_name",
]
