"""受管 Agent Skill 工具的 KT 薄 Adapter。"""

from __future__ import annotations

from typing import Any

from app.tool_services.skill import execute_skill
from nanobot_kt.optional_tool_api import BaseTool, ExecutionMode, ToolResult
from nanobot_kt.tools.result_adapter import to_kt_tool_result


class SkillTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "skill"

    @property
    def description(self) -> str:
        return "按当前请求冻结锁加载已授权 Skill 正文或单个文本资源。"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 64},
                "resource": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                },
            },
            "required": ["name"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        del kwargs
        return to_kt_tool_result(await execute_skill(args))


__all__ = ["SkillTool"]
