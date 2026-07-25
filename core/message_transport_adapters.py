"""类型化出站消息到现有 HTTP、SSE、群响应和 QQ 的薄适配器。"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from core.message_envelope import sanitize_reply_meta
from core.qq_outbound_renderer import (
    QQOutboundRenderResult,
    render_qq_outbound_envelope,
)
from foundation.message_contract import (
    MessageAction,
    MessagePhase,
    OutboundMessageContract,
    content_part_to_payload,
)


def _clean_meta(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, Mapping):
        return {}
    return {
        str(key): value
        for key, value in meta.items()
        if value is not None and value != ""
    }


def _base_envelope(
    message: OutboundMessageContract,
    *,
    status: str,
    meta: Mapping[str, Any] | None,
    reply_meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(message, OutboundMessageContract):
        raise TypeError("message 必须是 OutboundMessageContract")
    return {
        "status": str(status),
        "reply": message.text,
        "messages": [
            content_part_to_payload(part)
            for part in message.parts
        ],
        "reply_meta": sanitize_reply_meta(reply_meta),
        "meta": _clean_meta(meta),
    }


def render_chat_json(
    message: OutboundMessageContract,
    *,
    status: str | None = None,
    meta: Mapping[str, Any] | None = None,
    reply_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_status = status
    if not resolved_status:
        resolved_status = {
            MessageAction.REPLY: "ok",
            MessageAction.NO_REPLY: "no_reply",
            MessageAction.WAIT: "wait",
            MessageAction.SILENT: "silent",
            MessageAction.BLOCKED: "blocked",
        }[message.action]
    return _base_envelope(
        message,
        status=resolved_status,
        meta=meta,
        reply_meta=reply_meta,
    )


def render_sse_event(
    message: OutboundMessageContract,
    *,
    meta: Mapping[str, Any] | None = None,
    reply_meta: Mapping[str, Any] | None = None,
) -> str:
    if message.phase is MessagePhase.PROGRESS:
        event: dict[str, Any] = {
            "status": "delta",
            "text": message.text,
            "messages": [
                content_part_to_payload(part)
                for part in message.parts
            ],
            "retract_policy": message.retract_policy.value,
            "meta": _clean_meta(meta),
        }
    else:
        event = render_chat_json(
            message,
            status="done",
            meta=meta,
            reply_meta=reply_meta,
        )
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def render_group_json(
    message: OutboundMessageContract,
    *,
    meta: Mapping[str, Any] | None = None,
    reply_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    action = {
        MessageAction.REPLY: "continue",
        MessageAction.NO_REPLY: "no_reply",
        MessageAction.WAIT: "wait",
        # 群入口当前把静默和阻断渲染为 no_reply，具体原因留在 meta。
        MessageAction.SILENT: "no_reply",
        MessageAction.BLOCKED: "no_reply",
    }[message.action]
    status = "ok" if action == "continue" else action
    envelope = _base_envelope(
        message,
        status=status,
        meta=meta,
        reply_meta=reply_meta,
    )
    envelope["action"] = action
    return envelope


def render_qq_message(
    message: OutboundMessageContract,
    *,
    reply_meta: Mapping[str, Any] | None = None,
    allow_base64: bool = False,
) -> QQOutboundRenderResult:
    envelope = _base_envelope(
        message,
        status="ok",
        meta=None,
        reply_meta=reply_meta,
    )
    return render_qq_outbound_envelope(
        envelope,
        allow_base64=allow_base64,
    )


__all__ = [
    "render_chat_json",
    "render_group_json",
    "render_qq_message",
    "render_sse_event",
]
