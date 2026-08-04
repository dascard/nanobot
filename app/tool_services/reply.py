"""最终回复工具的应用服务。"""

from __future__ import annotations

from typing import Any

from core.tool_contracts.reply import (
    build_no_reply_output,
    build_reply_output,
)
from core.tool_contracts.result import ToolServiceResult


def execute_reply(
    args: dict[str, Any],
    *,
    dry_run: bool = False,
) -> ToolServiceResult:
    content = str(args.get("content", "")).strip()
    if not content:
        return ToolServiceResult(error="Missing 'content' argument")

    # 图片 token 只在最终发送出口展开，避免 base64 进入 conversation 和日志。
    try:
        from core.sticker_memory import (
            expand_sticker_refs_in_content,
            record_sticker_uses_in_content,
        )

        content = expand_sticker_refs_in_content(content)
        if not dry_run:
            record_sticker_uses_in_content(content)
    except Exception:
        pass

    return ToolServiceResult(
        output=build_reply_output(
            content,
            reply_to_message_id=args.get("reply_to_message_id"),
            mentions=args.get("mentions"),
            quote=bool(args.get("quote")),
            at_sender=bool(args.get("at_sender")),
            send_mode=str(
                args.get("send_mode", "normal") or "normal"
            ),
        ),
        exit_code=0,
    )


def execute_no_reply(args: dict[str, Any]) -> ToolServiceResult:
    return ToolServiceResult(
        output=build_no_reply_output(args.get("reason")),
        exit_code=0,
    )


__all__ = ["execute_no_reply", "execute_reply"]
