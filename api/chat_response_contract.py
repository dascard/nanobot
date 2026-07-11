"""聊天响应契约 helper。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from core.client_meta import client_meta_request_id
from core.inbound_idempotency import (
    CompletedInboundResponse,
    GroupReplayFields,
)
from core.message_envelope import build_chat_response_envelope


def normalize_chat_stream_event(event: Any) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None

    status = str(event.get("status") or "")
    if status == "delta":
        text = event.get("text", "")
        if text is None:
            text = ""
        text = str(text)
        if not text:
            return None
        normalized = dict(event)
        normalized["status"] = "delta"
        normalized["text"] = text
        return normalized

    if status == "final":
        text = event.get("text", "")
        if text is None:
            text = ""
        text = str(text)
        if not text:
            return None
        return {
            "status": "final",
            "text": text,
            "replace": bool(event.get("replace", True)),
            "source": str(event.get("source") or "bridge"),
        }

    if status:
        normalized = dict(event)
        normalized["status"] = status
        return normalized

    return None


def chat_sse_data(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def stream_error_event(message: str) -> dict[str, str]:
    return {"status": "error", "message": message}


def split_chat_answer_chunks(answer: str) -> list[str]:
    text = str(answer or "")
    if text.lstrip().startswith("<article") or text.lstrip().startswith("<!doctype") or text.lstrip().startswith("<html"):
        return [text]
    if not text.strip():
        return []
    if "\n\n" in text:
        return [c.strip() for c in text.split("\n\n") if c.strip()]
    if "\n" in text:
        return [c.strip() for c in text.split("\n") if c.strip()]
    return [text]


def _chat_request_platform(req: Any) -> str:
    client_meta = getattr(req, "client_meta", None)
    client_meta = client_meta if isinstance(client_meta, dict) else {}
    return str(client_meta.get("platform") or "qq").strip().lower() or "qq"


def _chat_request_type(req: Any) -> str:
    client_meta = getattr(req, "client_meta", None)
    client_meta = client_meta if isinstance(client_meta, dict) else {}
    chat_type = str(client_meta.get("chat_type") or "").strip().lower()
    if chat_type in {"private", "group"}:
        return chat_type
    return "private" if str(getattr(req, "session_id", "")).startswith("private_") else "group"


def chat_response_meta(
    req: Any,
    *,
    platform: str = "",
    chat_type: str = "",
    unprocessed_logs: int | None = None,
    reason: str = "",
    source: str = "",
    intent: str = "",
    guardrail_status: str | None = None,
    extra_meta: dict | None = None,
) -> dict[str, Any]:
    client_meta = getattr(req, "client_meta", None)
    meta: dict[str, Any] = {
        "user_id": getattr(req, "user_id", ""),
        "session_id": getattr(req, "session_id", ""),
        "platform": platform or _chat_request_platform(req),
        "chat_type": chat_type or _chat_request_type(req),
    }
    request_id = client_meta_request_id(client_meta)
    if request_id:
        meta["request_id"] = request_id
    if unprocessed_logs is not None:
        meta["unprocessed_logs"] = unprocessed_logs
    if reason:
        meta["reason"] = reason
    if source:
        meta["source"] = source
    if intent:
        meta["intent"] = intent
    if guardrail_status:
        meta["guardrail_status"] = guardrail_status
    if isinstance(extra_meta, dict):
        meta.update(extra_meta)
    return meta


def chat_response_payload(
    req: Any,
    *,
    status: str,
    answer: str = "",
    reply_meta: dict | None = None,
    platform: str = "",
    chat_type: str = "",
    unprocessed_logs: int | None = None,
    reason: str = "",
    source: str = "",
    intent: str = "",
    guardrail_status: str | None = None,
    include_answer_chunks: bool = False,
    extra_meta: dict | None = None,
) -> dict[str, Any]:
    payload = build_chat_response_envelope(
        status=status,
        answer=answer,
        reply_meta=reply_meta,
        meta=chat_response_meta(
            req,
            platform=platform,
            chat_type=chat_type,
            unprocessed_logs=unprocessed_logs,
            reason=reason,
            source=source,
            intent=intent,
            guardrail_status=guardrail_status,
            extra_meta=extra_meta,
        ),
    )
    payload["user_id"] = getattr(req, "user_id", "")
    payload["answer"] = payload["reply"]
    if unprocessed_logs is not None:
        payload["unprocessed_logs"] = unprocessed_logs
    if reason:
        payload["reason"] = reason
    if source:
        payload["source"] = source
    if intent:
        payload["intent"] = intent
    if include_answer_chunks:
        payload["answer_chunks"] = split_chat_answer_chunks(payload["reply"])
    return payload


def build_completed_inbound_response(
    *,
    outcome: str,
    reply: str = "",
    reply_meta: Mapping[str, Any] | None = None,
    reason: str = "",
    source: str = "",
    intent: str = "",
    guardrail_status: str | None = None,
    unprocessed_logs: int | None = None,
    group: GroupReplayFields | None = None,
) -> CompletedInboundResponse:
    """只用业务字段构造可持久化的完成结果。"""

    return CompletedInboundResponse(
        outcome=outcome,
        reply=reply,
        reply_meta={} if reply_meta is None else reply_meta,
        reason=reason,
        source=source,
        intent=intent,
        guardrail_status=guardrail_status,
        unprocessed_logs=unprocessed_logs,
        group=group,
    )


def _completed_chat_status(req: Any, response: CompletedInboundResponse) -> str:
    if response.outcome == "respond":
        return "done" if bool(getattr(req, "stream", False)) else "ok"
    if response.outcome == "blocked":
        return "silent"
    return response.outcome


def completed_chat_response_payload(
    req: Any,
    response: CompletedInboundResponse,
    *,
    answer_override: str | None = None,
) -> dict[str, Any]:
    """以当前请求身份把业务完成结果重建成 ``/chat`` 响应。"""

    if type(response) is not CompletedInboundResponse:
        raise TypeError("response 必须是 CompletedInboundResponse")
    if answer_override is not None and type(answer_override) is not str:
        raise TypeError("answer_override 必须是字符串或 null")

    is_respond = response.outcome == "respond"
    if is_respond:
        answer = response.reply if answer_override is None else answer_override
    else:
        answer = ""
    stream = bool(getattr(req, "stream", False))
    return chat_response_payload(
        req,
        status=_completed_chat_status(req, response),
        answer=answer,
        reply_meta=dict(response.reply_meta),
        platform=_chat_request_platform(req),
        chat_type=_chat_request_type(req),
        unprocessed_logs=response.unprocessed_logs,
        reason=response.reason,
        source=response.source,
        intent=response.intent,
        guardrail_status=response.guardrail_status,
        include_answer_chunks=not stream or not is_respond,
    )


def duplicate_inflight_chat_response_payload(
    req: Any,
    *,
    reason: str = "duplicate_inflight",
) -> dict[str, Any]:
    """为当前请求构造不属于完成结果的处理中重复响应。"""

    return chat_response_payload(
        req,
        status="duplicate_inflight",
        platform=_chat_request_platform(req),
        chat_type=_chat_request_type(req),
        reason=reason,
        include_answer_chunks=True,
    )
