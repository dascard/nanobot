"""聊天推送信封辅助函数。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from api.chat_request_contract import ChatProxyRequest, resolve_push_target_id
from core.message_envelope import is_html_reply
from core.message_transport_adapters import render_chat_json
from foundation.identity import RecipientIdentity
from foundation.message_contract import (
    MessageAction,
    OutboundMessageContract,
    TextContent,
    TextFormat,
)


@dataclass(frozen=True)
class ChatPushEnvelope:
    target_type: str
    target_id: str
    envelope: dict[str, Any]


def build_chat_push_envelope(
    req: ChatProxyRequest,
    *,
    answer: str,
    platform: str,
    chat_type: str,
    is_group: bool,
    status: str = "ok",
    reply_meta: Mapping[str, Any] | None = None,
    extra_meta: Mapping[str, Any] | None = None,
) -> ChatPushEnvelope:
    target_type = "group" if is_group else "private"
    target_id = resolve_push_target_id(req, is_group)
    meta = {
        "platform": platform,
        "chat_type": chat_type,
        "user_id": req.user_id,
        "session_id": req.session_id,
        "target_type": target_type,
        "target_id": target_id,
    }
    reserved_meta_keys = {
        "platform",
        "chat_type",
        "user_id",
        "session_id",
        "target_type",
        "target_id",
    }
    if isinstance(extra_meta, Mapping):
        for key, value in extra_meta.items():
            if key not in reserved_meta_keys:
                meta[str(key)] = value
    inbound = getattr(req, "_message_contract", None)
    recipient = getattr(inbound, "recipient", None)
    if not isinstance(recipient, RecipientIdentity):
        recipient = RecipientIdentity(
            platform=platform,
            recipient_type="group" if is_group else "user",
            recipient_id=target_id,
        )
    normalized_answer = str(answer or "")
    if normalized_answer:
        action = MessageAction.REPLY
        parts = (
            TextContent(
                normalized_answer,
                format=(
                    TextFormat.HTML
                    if is_html_reply(normalized_answer)
                    else TextFormat.PLAIN
                ),
            ),
        )
    else:
        action = MessageAction.NO_REPLY
        parts = ()
    outbound = OutboundMessageContract(
        action=action,
        recipient=recipient,
        parts=parts,
    )
    return ChatPushEnvelope(
        target_type=target_type,
        target_id=target_id,
        envelope=render_chat_json(
            outbound,
            status=status,
            reply_meta=reply_meta,
            meta=meta,
        ),
    )


def expand_chat_transport_answer(answer: str) -> str:
    from core.asset_transport import (
        expand_artifact_refs_in_content,
        expand_asset_download_refs_in_content,
    )
    from core.generated_images import expand_generated_image_refs_in_content

    expanded = expand_generated_image_refs_in_content(answer, allow_base64=False)
    expanded = expand_artifact_refs_in_content(expanded, render_images=True)
    return expand_asset_download_refs_in_content(expanded)
