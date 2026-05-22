"""最终工具集解析与真实请求出口过滤。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class FinalToolSet:
    allowed: set[str]
    disabled: dict[str, str]
    sent_tools: list[str] = field(default_factory=list)
    hidden_framework_tools: list[str] = field(default_factory=list)
    enabled: dict[str, bool] = field(default_factory=dict)


_CURRENT_FINAL_TOOLS: ContextVar[FinalToolSet | None] = ContextVar(
    "nanobot_final_tools",
    default=None,
)


def get_current_final_tools() -> FinalToolSet | None:
    return _CURRENT_FINAL_TOOLS.get()


def set_current_final_tools(final_tools: FinalToolSet | None) -> Token[FinalToolSet | None]:
    return _CURRENT_FINAL_TOOLS.set(final_tools)


def reset_current_final_tools(token: Token[FinalToolSet | None]) -> None:
    _CURRENT_FINAL_TOOLS.reset(token)


@contextmanager
def final_tools_scope(final_tools: FinalToolSet | None) -> Iterator[None]:
    token = set_current_final_tools(final_tools)
    try:
        yield
    finally:
        reset_current_final_tools(token)


def resolve_final_tools(
    chat_type: str = "group",
    group_id: str = "",
    user_id: str = "",
    runtime_preset: str = "full",
    db: Any = None,
) -> FinalToolSet:
    """把 RuntimeTool 结果转成真实请求出口使用的最终工具集。"""
    from core.runtime_tool_service import resolve_effective_tools

    enabled, disabled = resolve_effective_tools(
        chat_type=chat_type,
        group_id=group_id,
        user_id=user_id,
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


def tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function.get("name") or "")
    if tool.get("name"):
        return str(tool.get("name") or "")
    return ""


def filter_payload_tools(
    payload: dict[str, Any],
    final_tools: FinalToolSet | None = None,
) -> dict[str, Any]:
    """按 final tools 硬裁剪 OpenAI-compatible payload.tools。"""
    if final_tools is None:
        final_tools = get_current_final_tools()
    if final_tools is None or "tools" not in payload:
        return dict(payload)

    filtered = dict(payload)
    tools = filtered.get("tools")
    if not isinstance(tools, list):
        return filtered

    allowed = set(final_tools.allowed or set())
    kept = [tool for tool in tools if tool_name(tool) in allowed]
    if kept:
        filtered["tools"] = kept
    else:
        filtered.pop("tools", None)
        filtered.pop("tool_choice", None)
    return filtered


def filter_sdk_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """过滤 OpenAI SDK create(**kwargs) 的 tools 参数。"""
    return filter_payload_tools(dict(kwargs))
