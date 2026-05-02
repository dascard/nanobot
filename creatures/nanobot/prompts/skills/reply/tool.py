"""Reply tool——Planner 通过此工具生成用户可见回复，而非直接输出文本。"""

from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult


class ReplyTool(BaseTool):

    @property
    def tool_name(self) -> str:
        return "reply"

    @property
    def description(self) -> str:
        return "生成最终用户可见回复。调用后系统会把你的回复发送给用户。每次只调用一次，调用后不需要再输出任何文本。"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "content": {"type": "string", "description": "发送给用户的回复内容"},
        }, "required": ["content"]}

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        content = str(args.get("content", "")).strip()
        if not content:
            return ToolResult(error="Missing 'content' argument")
        return ToolResult(output=f"[REPLY]{content}[/REPLY]", exit_code=0)
