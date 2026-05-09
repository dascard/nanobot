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
            "reply_to_message_id": {"type": "string", "description": "（可选）要引用的消息 ID"},
            "mentions": {"type": "array", "items": {"type": "string"}, "description": "（可选）要 @ 的用户 QQ 号列表"},
            "quote": {"type": "boolean", "description": "（可选）是否引用被回复消息原文"},
            "at_sender": {"type": "boolean", "description": "（可选）是否 @ 当前消息发送者"},
            "send_mode": {"type": "string", "enum": ["normal", "quote", "mention", "quote_and_mention"],
                          "description": "normal/quote/mention/quote_and_mention"},
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
        mentions = args.get("mentions")
        if isinstance(mentions, list):
            mentions = [str(m)[:20] for m in mentions if str(m).strip()][:10]
        else:
            mentions = []
        reply_meta = {
            "content": content,
            "reply_to_message_id": str(args.get("reply_to_message_id", ""))[:50] or None,
            "mentions": mentions,
            "quote": bool(args.get("quote")),
            "at_sender": bool(args.get("at_sender")),
            "send_mode": str(args.get("send_mode", "normal") or "normal"),
        }
        return ToolResult(
            output=json.dumps({REPLY_MARKER: reply_meta}, ensure_ascii=False),
            exit_code=0,
        )
