"""Reply tool——Planner 通过此工具生成用户可见回复，而非直接输出文本。"""

import json
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

REPLY_MARKER = "NANOBOT_REPLY_OUTPUT"
_ALLOWED_SEND_MODES = {"normal", "quote", "mention", "quote_and_mention"}


def build_reply_payload(
    content: str,
    *,
    reply_to_message_id: Any = None,
    mentions: Any = None,
    quote: bool = False,
    at_sender: bool = False,
    send_mode: str = "normal",
) -> dict[str, Any]:
    """构造 bridge 可识别的最终回复协议。"""
    text = str(content or "").strip()
    if isinstance(mentions, list):
        clean_mentions = [
            str(m)[:20] for m in mentions
            if str(m).strip() and str(m).strip().isdigit()
        ][:10]
    else:
        clean_mentions = []

    mode = str(send_mode or "normal")
    if mode not in _ALLOWED_SEND_MODES:
        mode = "normal"

    return {
        REPLY_MARKER: {
            "content": text,
            "reply_to_message_id": str(reply_to_message_id or "")[:50] or None,
            "mentions": clean_mentions,
            "quote": bool(quote),
            "at_sender": bool(at_sender),
            "send_mode": mode,
        }
    }


def build_reply_output(content: str, **kwargs: Any) -> str:
    return json.dumps(build_reply_payload(content, **kwargs), ensure_ascii=False)


def build_reply_tool_result(content: str, **kwargs: Any) -> ToolResult:
    return ToolResult(output=build_reply_output(content, **kwargs), exit_code=0)


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
        return build_reply_tool_result(
            content,
            reply_to_message_id=args.get("reply_to_message_id"),
            mentions=args.get("mentions"),
            quote=bool(args.get("quote")),
            at_sender=bool(args.get("at_sender")),
            send_mode=str(args.get("send_mode", "normal") or "normal"),
        )


class NoReplyTool(BaseTool):
    """主动选择不回复——与 reply() 互斥，只调用其中一个。"""

    @property
    def tool_name(self) -> str:
        return "no_reply"

    @property
    def description(self) -> str:
        return (
            "主动决定不回复当前消息。当群聊内容不需要 bot 参与（闲聊、语气词、"
            "签到打卡、bot 未被点名等），调用此工具。调用后不会发送任何消息。"
            "和 reply() 互斥——每轮只调用其中一个。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "reason": {"type": "string", "description": "不回复的原因（内部日志用，不会发送给用户）"},
        }, "required": ["reason"]}

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        reason = str(args.get("reason", "")).strip()[:200]
        return ToolResult(
            output=json.dumps(
                {REPLY_MARKER: {"content": "", "no_reply": True, "reason": reason}},
                ensure_ascii=False,
            ),
            exit_code=0,
        )
