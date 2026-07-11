"""群聊入站完成结果与 HTTP 响应之间的统一适配器。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.group_ingress import helpers as h
from core.inbound_idempotency import CompletedInboundResponse, GroupReplayFields
from core.message_envelope import build_group_response_envelope


logger = logging.getLogger("nanobot.group_ingress")


def _request_platform(req: Any) -> str:
    client_meta = getattr(req, "client_meta", None)
    client_meta = client_meta if isinstance(client_meta, dict) else {}
    return str(client_meta.get("platform") or "qq").strip().lower() or "qq"


def _request_meta(req: Any) -> dict[str, Any]:
    return {
        "platform": _request_platform(req),
        "chat_type": "group",
        "group_id": getattr(req, "group_id", ""),
        "message_id": getattr(req, "message_id", "") or "",
        "sender_id": getattr(req, "sender_id", "") or "",
    }


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


def _group_action(outcome: str) -> str:
    if outcome == "respond":
        return "continue"
    if outcome == "wait":
        return "wait"
    return "no_reply"


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
    action = _group_action(response.outcome)
    answer = ""
    if response.outcome == "respond":
        answer = _format_transport_answer(response.reply)

    reason = str(response.reason or "")[:120]
    diagnostics = dict(group.diagnostics) if group.diagnostics else None
    duplicate_reply = dict(group.duplicate_reply) if group.duplicate_reply else None
    payload = build_group_response_envelope(
        action=action,
        reply=answer,
        reply_meta=dict(response.reply_meta),
        generation=group.generation,
        reason=reason,
        delay_seconds=group.delay_seconds,
        diagnostics=diagnostics,
        duplicate_reply=duplicate_reply,
        meta=_request_meta(req),
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
    payload = build_group_response_envelope(
        action="no_reply",
        reply_meta={} if reply_meta is None else dict(reply_meta),
        generation=generation,
        reason=reason_text,
        delay_seconds=delay_seconds,
        diagnostics=diagnostics_payload,
        meta=_request_meta(req),
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
    payload = build_group_response_envelope(
        action=reason,
        reason=reason,
        meta=_request_meta(req),
    )
    payload["reason"] = reason
    return payload
