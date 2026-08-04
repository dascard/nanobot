"""定时任务工具的 KT 薄 Adapter。"""

from __future__ import annotations

from typing import Any

from nanobot_kt.optional_tool_api import BaseTool, ExecutionMode, ToolResult

from app.tool_services.schedule_task import ScheduleTaskService
from nanobot_kt.tools.result_adapter import to_kt_tool_result


class ScheduleTaskTool(BaseTool):
    """把 KT 调用转换为当前 owner 的定时任务应用请求。"""

    needs_context = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._service = ScheduleTaskService()

    @property
    def tool_name(self) -> str:
        return "schedule_task"

    @property
    def description(self) -> str:
        return self._service.description

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return self._service.get_parameters_schema()

    async def _execute(
        self,
        args: dict[str, Any],
        **kwargs: Any,
    ) -> ToolResult:
        result = await self._service.execute(args, **kwargs)
        return to_kt_tool_result(result)


__all__ = ["ScheduleTaskTool"]
