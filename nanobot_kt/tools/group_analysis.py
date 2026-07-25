"""群分析的 KT 薄 Adapter。"""

from __future__ import annotations

import copy
from typing import Any

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)

from app.group_analysis.service import execute_group_analysis
from core.tool_registry import get_tool_def
from nanobot_kt.reply_contract import build_rich_tool_result


class GroupAnalysisTool(BaseTool):
    """把 KT 调用转换为框架无关的群分析应用请求。"""

    @property
    def tool_name(self) -> str:
        return "group_analysis"

    @property
    def description(self) -> str:
        definition = get_tool_def(self.tool_name)
        return definition.description if definition is not None else ""

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        from core.tool_schema_preview import build_tool_schema

        schema = build_tool_schema(
            self.tool_name,
            include_template_overlay=False,
        )
        return copy.deepcopy(schema["function"]["parameters"])

    async def _execute(
        self,
        args: dict[str, Any],
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        try:
            html = await execute_group_analysis(args)
        except ValueError as exc:
            return ToolResult(error=str(exc))
        return build_rich_tool_result(
            html,
            report_kind="group_analysis",
        )

__all__ = ["GroupAnalysisTool"]
