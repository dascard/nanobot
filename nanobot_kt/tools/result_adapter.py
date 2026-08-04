"""把框架无关工具服务结果转换为 KT ToolResult。"""

from __future__ import annotations

from typing import Any, Mapping

from nanobot_kt.optional_tool_api import ToolResult

from core.tool_contracts.result import ToolServiceResult


def _plain_mapping(value: Mapping[str, object]) -> dict[str, Any]:
    return dict(value)


def to_kt_tool_result(result: ToolServiceResult) -> ToolResult:
    return ToolResult(
        output=result.output,
        exit_code=result.exit_code,
        error=result.error,
        metadata=_plain_mapping(result.metadata),
    )


__all__ = ["to_kt_tool_result"]
