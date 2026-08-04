"""Session Plan 工具的 KT 薄 Adapter。"""

from __future__ import annotations

from typing import Any

from app.tool_services.session_plan import (
    execute_session_plan_read,
    execute_session_plan_write,
)
from nanobot_kt.optional_tool_api import BaseTool, ExecutionMode, ToolResult
from nanobot_kt.tools.result_adapter import to_kt_tool_result


class SessionPlanReadTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "session_plan_read"

    @property
    def description(self) -> str:
        return "读取当前 Session Goal 的不可变计划版本。"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "revision": {"type": "integer", "minimum": 0},
            },
            "required": [],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        del kwargs
        return to_kt_tool_result(await execute_session_plan_read(args))


class SessionPlanWriteTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "session_plan_write"

    @property
    def description(self) -> str:
        return "仅在服务端 Plan Mode 中写入当前 Session Goal 的新计划版本。"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "content": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 262144,
                },
                "expected_version": {"type": "integer", "minimum": 1},
            },
            "required": ["content", "expected_version"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        del kwargs
        return to_kt_tool_result(await execute_session_plan_write(args))


__all__ = ["SessionPlanReadTool", "SessionPlanWriteTool"]
