"""群聊入站完成结果与 HTTP 响应之间的统一适配器。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.group_ingress import helpers as h
from core.inbound_idempotency import CompletedInboundResponse, GroupReplayFields
from core.message_transport_adapters import render_group_json
from foundation.identity import RecipientIdentity
from foundation.message_contract import (
    MessageAction,
    OutboundMessageContract,
    TextContent,
    TextFormat,
)


logger = logging.getLogger("nanobot.group_ingress")


def _request_platform(req: Any) -> str:
    client_meta = getattr(req, "client_meta", None)
    client_meta = client_meta if isinstance(client_meta, dict) else {}
    return str(client_meta.get("platform") or "qq").strip().lower() or "qq"


def _request_meta(req: Any) -> dict[str, Any]:
    meta = {
        "platform": _request_platform(req),
        "chat_type": "group",
        "group_id": getattr(req, "group_id", ""),
        "message_id": getattr(req, "message_id", "") or "",
        "sender_id": getattr(req, "sender_id", "") or "",
    }
    message_contract = getattr(req, "_message_contract", None)
    chat_stream = getattr(message_contract, "chat_stream", None)
    chat_stream_id = str(
        getattr(chat_stream, "chat_stream_id", "") or ""
    )
    if chat_stream_id:
        meta["chat_stream_id"] = chat_stream_id
    return meta


def _safe_warning(message: str, *args: Any, **kwargs: Any) -> None:
    try:
        logger.warning(message, *args, **kwargs)
    except BaseException:
        pass


def _format_transport_answer(answer: str) -> str:
    formatted = h.format_group_reply_for_transport(answer, max_chars=4000)
    try:
        from core.generated_images import expand_generated_image_refs_in_content

        return expand_generated_image_refs_in_content(formatted, allow_base64=False)
    except Exception:
        _safe_warning("[GroupMsg] generated image ref expansion failed", exc_info=True)
        return formatted


def build_completed_group_response(
    *,
    outcome: str,
    reply: str = "",
    reply_meta: Mapping[str, Any] | None = None,
    reason: str = "",
    generation: int | None = None,
    delay_seconds: int | float | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    duplicate_reply: Mapping[str, Any] | None = None,
    hard_rule: str = "",
) -> CompletedInboundResponse:
    """构造可持久化的群聊业务完成结果，reply 始终保留原始文本。"""

    return CompletedInboundResponse(
        outcome=outcome,
        reply=reply,
        reply_meta={} if reply_meta is None else reply_meta,
        reason=reason,
        group=GroupReplayFields(
            generation=generation,
            delay_seconds=delay_seconds,
            diagnostics={} if diagnostics is None else diagnostics,
            duplicate_reply={} if duplicate_reply is None else duplicate_reply,
            hard_rule=hard_rule,
        ),
    )


def _outbound_action(outcome: str) -> MessageAction:
    return {
        "respond": MessageAction.REPLY,
        "wait": MessageAction.WAIT,
        "silent": MessageAction.SILENT,
        "blocked": MessageAction.BLOCKED,
    }.get(outcome, MessageAction.NO_REPLY)


def _recipient(req: Any) -> RecipientIdentity:
    message_contract = getattr(req, "_message_contract", None)
    recipient = getattr(message_contract, "recipient", None)
    if isinstance(recipient, RecipientIdentity):
        return recipient
    return RecipientIdentity(
        platform=_request_platform(req),
        recipient_type="group",
        recipient_id=str(
            getattr(req, "group_id", "") or "unknown"
        ),
    )


def _outbound_message(
    req: Any,
    *,
    outcome: str,
    reply: str = "",
) -> OutboundMessageContract:
    action = _outbound_action(outcome)
    parts = ()
    if action is MessageAction.REPLY:
        text_format = (
            TextFormat.HTML
            if h.is_html_reply(reply)
            else TextFormat.PLAIN
        )
        parts = (TextContent(reply, format=text_format),)
    return OutboundMessageContract(
        action=action,
        recipient=_recipient(req),
        parts=parts,
    )


def _response_meta(
    req: Any,
    *,
    generation: int | None = None,
    reason: str = "",
    delay_seconds: int | float | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    duplicate_reply: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    meta = _request_meta(req)
    optional = {
        "generation": generation,
        "reason": reason,
        "delay_seconds": delay_seconds,
        "diagnostics": (
            dict(diagnostics)
            if isinstance(diagnostics, Mapping)
            else None
        ),
        "duplicate_reply": (
            dict(duplicate_reply)
            if isinstance(duplicate_reply, Mapping)
            else None
        ),
    }
    meta.update(
        {
            key: value
            for key, value in optional.items()
            if value is not None and value != ""
        }
    )
    return meta


def completed_group_response_payload(
    req: Any,
    response: CompletedInboundResponse,
) -> dict[str, Any]:
    """使用当前请求身份把业务完成结果重建为兼容群聊 envelope。"""

    if type(response) is not CompletedInboundResponse:
        raise TypeError("response 必须是 CompletedInboundResponse")
    if response.group is None:
        raise ValueError("群聊完成结果必须包含 group 字段")

    group = response.group
    answer = ""
    if response.outcome == "respond":
        answer = _format_transport_answer(response.reply)

    reason = str(response.reason or "")[:120]
    diagnostics = dict(group.diagnostics) if group.diagnostics else None
    duplicate_reply = dict(group.duplicate_reply) if group.duplicate_reply else None
    payload = render_group_json(
        _outbound_message(
            req,
            outcome=response.outcome,
            reply=answer,
        ),
        reply_meta=dict(response.reply_meta),
        meta=_response_meta(
            req,
            generation=group.generation,
            reason=reason,
            delay_seconds=group.delay_seconds,
            diagnostics=diagnostics,
            duplicate_reply=duplicate_reply,
        ),
    )
    if group.generation is not None:
        payload["generation"] = group.generation
    if group.delay_seconds is not None:
        payload["delay_seconds"] = group.delay_seconds
    if reason:
        payload["reason"] = reason
    if diagnostics:
        payload["diagnostics"] = diagnostics
    if duplicate_reply:
        payload["duplicate_reply"] = duplicate_reply
    if group.hard_rule:
        payload["hard_rule"] = group.hard_rule
    return payload


def technical_group_response_payload(
    req: Any,
    *,
    reason: str,
    reply_meta: Mapping[str, Any] | None = None,
    generation: int | None = None,
    delay_seconds: int | float | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """使用当前请求身份构造不可持久化为业务完成结果的技术响应。"""

    reason_text = str(reason or "")[:120]
    diagnostics_payload = None if diagnostics is None else dict(diagnostics)
    payload = render_group_json(
        _outbound_message(
            req,
            outcome="blocked",
        ),
        reply_meta={} if reply_meta is None else dict(reply_meta),
        meta=_response_meta(
            req,
            generation=generation,
            reason=reason_text,
            delay_seconds=delay_seconds,
            diagnostics=diagnostics_payload,
        ),
    )
    if generation is not None:
        payload["generation"] = generation
    if delay_seconds is not None:
        payload["delay_seconds"] = delay_seconds
    if reason_text:
        payload["reason"] = reason_text
    if diagnostics_payload:
        payload["diagnostics"] = diagnostics_payload
    return payload


def duplicate_inflight_group_response_payload(req: Any) -> dict[str, Any]:
    """使用当前请求身份构造不属于业务完成结果的处理中重复响应。"""

    reason = "duplicate_inflight"
    payload = render_group_json(
        _outbound_message(
            req,
            outcome="no_reply",
        ),
        meta=_response_meta(req, reason=reason),
    )
    payload["status"] = reason
    payload["action"] = reason
    payload["reason"] = reason
    return payload
