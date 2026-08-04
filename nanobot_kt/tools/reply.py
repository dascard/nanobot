"""最终回复工具的 KT 薄 Adapter。"""

from __future__ import annotations

from typing import Any

from nanobot_kt.optional_tool_api import BaseTool, ExecutionMode, ToolResult

from app.tool_services.reply import execute_no_reply, execute_reply
from core.agent_runtime.request_scope import is_runtime_request_dry_run
from core.tool_contracts.reply import (
    REPLY_MARKER,
    build_reply_output,
    build_reply_payload,
)
from core.tool_contracts.result import ToolServiceResult
from nanobot_kt.tools.result_adapter import to_kt_tool_result


def build_reply_tool_result(content: str, **kwargs: Any) -> ToolResult:
    """保留旧公开 helper；wire payload 的事实源已迁入 Core。"""

    return to_kt_tool_result(
        ToolServiceResult(
            output=build_reply_output(content, **kwargs),
            exit_code=0,
        )
    )


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
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "发送给用户的回复内容",
                },
                "reply_to_message_id": {
                    "type": "string",
                    "description": "（可选）要引用的消息 ID",
                },
                "mentions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "（可选）要 @ 的用户 QQ 号列表",
                },
                "quote": {
                    "type": "boolean",
                    "description": "（可选）是否引用被回复消息原文",
                },
                "at_sender": {
                    "type": "boolean",
                    "description": "（可选）是否 @ 当前消息发送者",
                },
                "send_mode": {
                    "type": "string",
                    "enum": [
                        "normal",
                        "quote",
                        "mention",
                        "quote_and_mention",
                    ],
                    "description": (
                        "normal/quote/mention/quote_and_mention"
                    ),
                },
            },
            "required": ["content"],
        }

    async def _execute(
        self,
        args: dict[str, Any],
        **kwargs: Any,
    ) -> ToolResult:
        result = execute_reply(
            args,
            dry_run=(
                bool(kwargs.get("dry_run"))
                or is_runtime_request_dry_run()
            ),
        )
        return to_kt_tool_result(result)


class NoReplyTool(BaseTool):
    """主动选择不回复；与 reply 互斥。"""

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
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "不回复的原因（内部日志用，不会发送给用户）",
                }
            },
            "required": ["reason"],
        }

    async def _execute(
        self,
        args: dict[str, Any],
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        return to_kt_tool_result(execute_no_reply(args))


__all__ = [
    "NoReplyTool",
    "REPLY_MARKER",
    "ReplyTool",
    "build_reply_output",
    "build_reply_payload",
    "build_reply_tool_result",
]
