"""聊天推送信封辅助函数。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from api.chat_request_contract import ChatProxyRequest, resolve_push_target_id
from core.message_envelope import build_chat_response_envelope


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
    return ChatPushEnvelope(
        target_type=target_type,
        target_id=target_id,
        envelope=build_chat_response_envelope(
            status=status,
            answer=answer,
            reply_meta=reply_meta,
            meta=meta,
        ),
    )


def expand_chat_transport_answer(answer: str) -> str:
    from core.asset_transport import expand_asset_download_refs_in_content
    from core.generated_images import expand_generated_image_refs_in_content

    expanded = expand_generated_image_refs_in_content(answer, allow_base64=False)
    return expand_asset_download_refs_in_content(expanded)
