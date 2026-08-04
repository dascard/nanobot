"""画像刷新工具的 KT 薄 Adapter。"""

from __future__ import annotations

from typing import Any

from nanobot_kt.optional_tool_api import BaseTool, ExecutionMode, ToolResult

from app.persona.update_service import execute_persona_update
from core.agent_runtime.request_scope import get_current_runtime_context
from nanobot_kt.tools.result_adapter import to_kt_tool_result


class PersonaUpdateTool(BaseTool):
    """把 KT 请求转换为当前 Runtime actor 的画像刷新请求。"""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "persona_update"

    @property
    def description(self) -> str:
        return (
            "刷新当前用户已持久化聊天日志形成的画像。仅当用户明确请求刷新画像时使用；"
            "普通聊天里的新信息由后台画像进化链路异步处理。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def _execute(
        self,
        args: dict[str, Any],
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        runtime_context = get_current_runtime_context()
        user_id = str(
            runtime_context.get("user_id", "")
            if runtime_context is not None
            else ""
        ).strip()
        result = await execute_persona_update(args, user_id=user_id)
        return to_kt_tool_result(result)


__all__ = ["PersonaUpdateTool"]
