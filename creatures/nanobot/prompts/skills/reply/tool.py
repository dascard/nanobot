"""Reply tool——Planner 通过此工具生成用户可见回复，而非直接输出文本。"""

import json
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

REPLY_MARKER = "NANOBOT_REPLY_OUTPUT"


class ReplyTool(BaseTool):

    @property
    def tool_name(self) -> str:
        return "reply"

    @property
    def description(self) -> str:
        return "生成最终用户可见回复。调用后系统会把你的回复发送给用户。只调用一次，调用后不需要再输出任何文本。"

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
        try:
            from core.sticker_memory import expand_sticker_refs_in_content, record_sticker_uses_in_content
            content = expand_sticker_refs_in_content(content)
            record_sticker_uses_in_content(content)
        except Exception:
            pass
        # 结构化输出——bridge 解析此 JSON，不依赖文本标签
        return ToolResult(
            output=json.dumps({REPLY_MARKER: {"content": content}}, ensure_ascii=False),
            exit_code=0,
        )
