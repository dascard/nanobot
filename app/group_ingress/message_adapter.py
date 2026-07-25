"""OneBot／NapCat 群消息到框架无关 MessageContract 的薄适配器。"""

from __future__ import annotations

from typing import Any

from app.group_ingress import helpers as h
from foundation.identity import (
    ActorIdentity,
    Principal,
    RecipientIdentity,
    resolve_chat_stream_identity,
)
from foundation.message_contract import (
    AttachmentKind,
    FileContent,
    ForwardContent,
    GatewayMetadata,
    ImageContent,
    InboundMessageContract,
    MentionReference,
    MessageAttachment,
    MessageContractError,
    MessageTrace,
    ReplyReference,
    TextContent,
)


_ANONYMOUS_ACTOR_ID = "anonymous"
_SUPPORTED_ONEBOT_SEGMENTS = frozenset(
    {"text", "at", "reply", "image", "mface", "file", "forward"}
)


def _platform(req: Any) -> str:
    client_meta = (
        req.client_meta
        if isinstance(getattr(req, "client_meta", None), dict)
        else {}
    )
    return str(client_meta.get("platform") or "qq").strip().lower() or "qq"


def _validate_segment_types(req: Any) -> None:
    for segment in list(getattr(req, "segments", []) or [])[:30]:
        if not isinstance(segment, dict):
            raise MessageContractError(
                "invalid_content_part",
                "OneBot 消息段必须是对象",
            )
        segment_type = segment.get("type")
        if (
            type(segment_type) is not str
            or segment_type not in _SUPPORTED_ONEBOT_SEGMENTS
        ):
            raise MessageContractError(
                "unsupported_content_part",
                "OneBot 消息段 type 不受支持",
            )
        if not isinstance(segment.get("data", {}), dict):
            raise MessageContractError(
                "invalid_content_part",
                "OneBot 消息段 data 必须是对象",
            )


def _first_ref(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _segment_content_parts(
    req: Any,
    segments: list[dict],
) -> tuple[tuple[object, ...], tuple[MessageAttachment, ...]]:
    parts: list[object] = []
    attachments: list[MessageAttachment] = []
    attachment_refs: set[str] = set()
    for segment in segments:
        segment_type = segment["type"]
        data = segment.get("data") or {}
        if segment_type == "text":
            text = str(data.get("text") or "")
            if text.strip():
                parts.append(TextContent(text))
            continue
        if segment_type in {"at", "reply"}:
            continue
        if segment_type in {"image", "mface"}:
            ref = _first_ref(data, "url", "file", "file_id")
            if not ref:
                raise MessageContractError(
                    "invalid_image_ref",
                    "图片消息段缺少可用引用",
                )
            parts.append(
                ImageContent(
                    ref,
                    alt_text=str(data.get("summary") or ""),
                )
            )
            if ref not in attachment_refs:
                attachments.append(
                    MessageAttachment(
                        kind=AttachmentKind.IMAGE,
                        ref=ref,
                    )
                )
                attachment_refs.add(ref)
            continue
        if segment_type == "file":
            ref = _first_ref(data, "url", "file", "file_id")
            if not ref:
                raise MessageContractError(
                    "invalid_file_ref",
                    "文件消息段缺少可用引用",
                )
            name = str(
                data.get("file_name") or data.get("name") or ""
            )
            size_text = str(data.get("file_size") or "").strip()
            size_bytes = int(size_text) if size_text.isdecimal() else None
            parts.append(
                FileContent(
                    ref,
                    name=name,
                    size_bytes=size_bytes,
                )
            )
            if ref not in attachment_refs:
                attachments.append(
                    MessageAttachment(
                        kind=AttachmentKind.FILE,
                        ref=ref,
                        name=name,
                        size_bytes=size_bytes,
                    )
                )
                attachment_refs.add(ref)
            continue
        if segment_type == "forward":
            ref = _first_ref(data, "id")
            if not ref:
                raise MessageContractError(
                    "invalid_forward_ref",
                    "转发消息段缺少可用引用",
                )
            parts.append(
                ForwardContent(
                    ref,
                    summary=str(data.get("summary") or ""),
                )
            )

    for raw_ref in getattr(req, "files", None) or []:
        ref = str(raw_ref or "").strip()
        if not ref or ref in attachment_refs:
            continue
        parts.append(ImageContent(ref))
        attachments.append(
            MessageAttachment(
                kind=AttachmentKind.IMAGE,
                ref=ref,
            )
        )
        attachment_refs.add(ref)
    return tuple(parts), tuple(attachments)


def _mention_references(
    req: Any,
    *,
    platform: str,
    segments: list[dict],
) -> tuple[MentionReference, ...]:
    return tuple(
        MentionReference(
            actor=ActorIdentity(
                platform=platform,
                actor_id=str(item.get("user_id") or ""),
            ),
            display_name=str(item.get("nickname") or ""),
            is_bot=bool(item.get("is_bot")),
        )
        for item in h.normalize_group_mentions(req, segments)
    )


def _reply_reference(
    req: Any,
    *,
    platform: str,
    segments: list[dict],
) -> ReplyReference | None:
    reply = h.normalize_group_reply_to(req, segments)
    if not reply:
        return None
    sender_id = str(reply.get("sender_id") or "").strip()
    actor = (
        ActorIdentity(platform=platform, actor_id=sender_id)
        if sender_id
        else None
    )
    return ReplyReference(
        message_id=str(reply.get("message_id") or ""),
        actor=actor,
        actor_name=str(reply.get("sender_name") or ""),
        text=str(reply.get("content") or ""),
        is_bot=bool(reply.get("is_bot")),
    )


def _message_trace(req: Any) -> MessageTrace:
    client_meta = (
        req.client_meta
        if isinstance(getattr(req, "client_meta", None), dict)
        else {}
    )
    trace = client_meta.get("trace")
    trace = trace if isinstance(trace, dict) else {}
    return MessageTrace(
        request_id=str(trace.get("request_id") or ""),
        trace_id=str(trace.get("trace_id") or ""),
        correlation_id=str(trace.get("correlation_id") or ""),
        idempotency_key=str(getattr(req, "message_id", "") or ""),
    )


def build_group_message_contract(req: Any) -> InboundMessageContract:
    _validate_segment_types(req)
    platform = _platform(req)
    group_id = str(getattr(req, "group_id", "") or "").strip()
    chat_stream = resolve_chat_stream_identity(
        platform=platform,
        chat_type="group",
        session_id=group_id,
    )
    segments = h.normalize_onebot_segments(req)
    message_text = h.build_group_message_text(req)
    parts, attachments = _segment_content_parts(req, segments)
    if not parts and message_text.strip():
        parts = (TextContent(message_text),)
    sender_id = (
        str(getattr(req, "sender_id", "") or "").strip()
        or _ANONYMOUS_ACTOR_ID
    )
    client_meta = (
        req.client_meta
        if isinstance(getattr(req, "client_meta", None), dict)
        else {}
    )
    trace = client_meta.get("trace")
    trace = trace if isinstance(trace, dict) else {}
    return InboundMessageContract(
        message_id=str(getattr(req, "message_id", "") or ""),
        chat_stream=chat_stream,
        actor=ActorIdentity(
            platform=platform,
            actor_id=sender_id,
        ),
        recipient=RecipientIdentity(
            platform=platform,
            recipient_type="group",
            recipient_id=chat_stream.external_session_id,
        ),
        principal=Principal(
            platform=platform,
            owner_type="group",
            owner_id=chat_stream.external_session_id,
        ),
        text=message_text,
        parts=parts,
        attachments=attachments,
        mentions=_mention_references(
            req,
            platform=platform,
            segments=segments,
        ),
        reply_to=_reply_reference(
            req,
            platform=platform,
            segments=segments,
        ),
        gateway=GatewayMetadata(
            source=str(trace.get("source") or ""),
            session_name=str(
                getattr(req, "session_name", "") or ""
            ),
            self_id=str(getattr(req, "self_id", "") or ""),
            bot_id=str(getattr(req, "bot_id", "") or ""),
            bot_name=str(getattr(req, "bot_name", "") or ""),
            bot_aliases=tuple(getattr(req, "bot_aliases", []) or []),
            sender_is_bot=bool(
                getattr(req, "sender_is_bot", False)
            ),
        ),
        trace=_message_trace(req),
    )


__all__ = ["build_group_message_contract"]
