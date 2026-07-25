"""聊天请求契约与请求元信息 helper。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, PrivateAttr

from core.chat_stream_identity import (
    ChatStreamIdentityError,
    parse_compatibility_chat_stream_identity,
    resolve_chat_stream_identity,
)
from core.client_meta import ClientMetaValidationError, normalize_client_meta
from foundation.identity import (
    ActorIdentity,
    Principal,
    RecipientIdentity,
)
from foundation.message_contract import (
    AssetContent,
    AttachmentKind,
    GatewayMetadata,
    ImageContent,
    InboundMessageContract,
    MessageAttachment,
    MessageContractError,
    MessageTrace,
    TextContent,
)


class ChatProxyRequest(BaseModel):
    user_id: str = "default_user"
    session_id: str = "default_session"
    query: str = ""
    files: list[str] | None = None
    sender_name: str | None = None
    session_name: str | None = None
    stream: bool = False
    classification_request: bool = False
    merged_messages: list[str] | None = None
    message_id: str | None = None
    source_message_ids: list[str] | None = None
    client_meta: dict | None = None
    _message_contract: InboundMessageContract | None = PrivateAttr(
        default=None
    )


def clone_chat_request(req: ChatProxyRequest, **updates) -> ChatProxyRequest:
    if hasattr(req, "model_dump"):
        data = req.model_dump()
    else:
        data = req.dict()
    data.update(updates)
    return ChatProxyRequest(**data)


def resolve_push_target_id(req: ChatProxyRequest, is_group: bool) -> str:
    if not is_group:
        return req.user_id
    return _request_chat_stream(req).external_session_id


def extract_group_id_from_chat_request(req: ChatProxyRequest) -> str:
    identity = _request_chat_stream(req)
    if identity.chat_type != "group":
        return ""
    return identity.external_session_id


def chat_request_platform(req: ChatProxyRequest) -> str:
    client_meta = req.client_meta if isinstance(req.client_meta, dict) else {}
    return str(client_meta.get("platform") or "qq").strip().lower() or "qq"


def chat_request_type(req: ChatProxyRequest) -> str:
    return infer_chat_request_type(req)


def infer_chat_request_type(req: ChatProxyRequest) -> str:
    client_meta = req.client_meta if isinstance(req.client_meta, dict) else {}
    compatibility = parse_compatibility_chat_stream_identity(
        str(req.session_id or ""),
        legacy_platform=chat_request_platform(req),
    )
    if compatibility is not None:
        return compatibility.chat_type
    explicit = str(client_meta.get("chat_type") or "").strip().lower()
    if explicit in {"private", "group"}:
        return explicit
    # /chat 的无前缀历史请求一直代表群会话；这里只在协议 Adapter 保留。
    return "group"


def _request_chat_stream(req: ChatProxyRequest):
    bound = getattr(req, "_message_contract", None)
    if isinstance(bound, InboundMessageContract):
        return bound.chat_stream
    chat_type = infer_chat_request_type(req)
    session_id = str(req.session_id or "").strip()
    if not session_id and chat_type == "group":
        session_id = str(req.user_id or "").strip()
    return resolve_chat_stream_identity(
        platform=chat_request_platform(req),
        chat_type=chat_type,
        session_id=session_id,
    )


def _message_trace(req: ChatProxyRequest) -> MessageTrace:
    client_meta = req.client_meta if isinstance(req.client_meta, dict) else {}
    trace = client_meta.get("trace")
    trace = trace if isinstance(trace, dict) else {}
    return MessageTrace(
        request_id=str(trace.get("request_id") or ""),
        trace_id=str(trace.get("trace_id") or ""),
        correlation_id=str(trace.get("correlation_id") or ""),
        idempotency_key=str(req.message_id or ""),
    )


def _chat_parts_and_attachments(
    req: ChatProxyRequest,
) -> tuple[tuple[object, ...], tuple[MessageAttachment, ...]]:
    parts: list[object] = []
    attachments: list[MessageAttachment] = []
    if str(req.query or "").strip():
        parts.append(TextContent(str(req.query)))
    for raw_ref in req.files or []:
        ref = str(raw_ref or "").strip()
        if not ref:
            continue
        if ref.startswith("asset://"):
            parts.append(AssetContent(ref))
            kind = AttachmentKind.ASSET
        else:
            parts.append(ImageContent(ref))
            kind = AttachmentKind.IMAGE
        attachments.append(
            MessageAttachment(
                kind=kind,
                ref=ref,
            )
        )
    return tuple(parts), tuple(attachments)


def build_chat_message_contract(
    req: ChatProxyRequest,
) -> InboundMessageContract:
    platform = chat_request_platform(req)
    chat_type = infer_chat_request_type(req)
    chat_stream = _request_chat_stream(req)
    owner_type = "group" if chat_type == "group" else "user"
    owner_id = (
        chat_stream.external_session_id
        if chat_type == "group"
        else str(req.user_id or "").strip()
    )
    parts, attachments = _chat_parts_and_attachments(req)
    client_meta = req.client_meta if isinstance(req.client_meta, dict) else {}
    trace_meta = client_meta.get("trace")
    trace_meta = trace_meta if isinstance(trace_meta, dict) else {}
    return InboundMessageContract(
        message_id=str(req.message_id or ""),
        chat_stream=chat_stream,
        actor=ActorIdentity(
            platform=platform,
            actor_id=str(req.user_id or "").strip(),
        ),
        recipient=RecipientIdentity(
            platform=platform,
            recipient_type=owner_type,
            recipient_id=owner_id,
        ),
        principal=Principal(
            platform=platform,
            owner_type=owner_type,
            owner_id=owner_id,
        ),
        text=str(req.query or ""),
        parts=parts,
        attachments=attachments,
        gateway=GatewayMetadata(
            source=str(trace_meta.get("source") or ""),
            session_name=str(req.session_name or ""),
        ),
        trace=_message_trace(req),
    )


def bind_chat_message_contract(
    req: ChatProxyRequest,
) -> InboundMessageContract:
    message = build_chat_message_contract(req)
    req._message_contract = message
    return message


def normalize_request_message_contract(
    req: ChatProxyRequest,
) -> InboundMessageContract:
    try:
        return bind_chat_message_contract(req)
    except (ChatStreamIdentityError, MessageContractError) as exc:
        code = str(getattr(exc, "code", "invalid_identity"))
        raise HTTPException(
            400,
            f"invalid message contract: {code}",
        ) from exc


def normalize_request_client_meta(req: Any, *, expected_chat_type: str) -> dict[str, Any]:
    try:
        normalized = normalize_client_meta(
            getattr(req, "client_meta", None),
            expected_chat_type=expected_chat_type,
        )
    except ClientMetaValidationError as exc:
        raise HTTPException(400, f"invalid client_meta: {exc}") from exc
    req.client_meta = normalized
    return normalized


def private_prompt_audit_failure_meta() -> dict[str, Any]:
    return {
        "kind": "empty_reply",
        "no_context": True,
        "no_send": True,
        "agent_result": "prompt_v2_audit_failed",
    }


def private_timing_meta(decision: Any | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    scoring = getattr(decision, "timing_scoring", None)
    if not isinstance(scoring, dict):
        return None
    return {
        "mode": "private",
        "action": str(getattr(decision, "action", "") or ""),
        "effort": str(getattr(decision, "effort", "") or ""),
        "intent": str(getattr(decision, "intent", "") or ""),
        "response_mode": str(
            getattr(decision, "response_mode", "") or ""
        ),
        "confidence": float(
            getattr(decision, "confidence", 0.0) or 0.0
        ),
        "parse_quality": str(
            getattr(decision, "parse_quality", "") or ""
        ),
        "error_type": (
            str(getattr(decision, "error_type", "") or "") or None
        ),
        "reason_code": str(
            getattr(decision, "reason_code", "") or ""
        ),
        "contract_version": str(
            getattr(decision, "contract_version", "") or ""
        ),
        "task_run_id": str(
            getattr(decision, "task_run_id", "") or ""
        ),
        "policy_mode": str(
            getattr(decision, "policy_mode", "") or ""
        ),
        "policy_source": str(
            getattr(decision, "policy_source", "") or ""
        ),
        "proposed_action": str(
            getattr(decision, "proposed_action", "") or ""
        ),
        "proposed_response_mode": str(
            getattr(decision, "proposed_response_mode", "") or ""
        ),
        "runtime_preset": str(getattr(decision, "runtime_preset", "") or ""),
        "scoring": scoring,
    }
