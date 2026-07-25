"""普通 API 群聊入口路由。"""
from __future__ import annotations

import sys
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, PrivateAttr
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.client_meta import ClientMetaValidationError, normalize_client_meta
from core.database import get_db
from foundation.identity import ChatStreamIdentityError
from foundation.message_contract import (
    InboundMessageContract,
    MessageContractError,
)
from nanobot_kt.bridge import get_bridge as _default_get_bridge

router = APIRouter(tags=["group-message"])


def _current_bridge_provider():
    routes = sys.modules.get("api.routes")
    if routes is not None and hasattr(routes, "get_bridge"):
        return getattr(routes, "get_bridge")
    return _default_get_bridge


def _precache_image_sources(*args: Any, **kwargs: Any):
    from nanobot_kt.image_pipeline import precache_image_sources

    return precache_image_sources(*args, **kwargs)


def _normalize_request_client_meta(req: Any, *, expected_chat_type: str) -> dict[str, Any]:
    try:
        normalized = normalize_client_meta(
            getattr(req, "client_meta", None),
            expected_chat_type=expected_chat_type,
        )
    except ClientMetaValidationError as exc:
        raise HTTPException(400, f"invalid client_meta: {exc}") from exc
    req.client_meta = normalized
    return normalized


class OneBotMessageSegmentPayload(BaseModel):
    """OneBot/NapCat 消息段——不要和 NoneBot MessageSegment 混淆。"""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class GroupMessageRequest(BaseModel):
    group_id: str
    sender_id: str = ""
    sender_name: str = ""
    message: str = ""
    files: list[str] | None = None
    client_meta: dict | None = None
    message_id: str | None = None
    session_name: str | None = None
    is_at_bot: bool = False
    is_reply_to_bot: bool = False
    bot_aliases: list[str] = Field(default_factory=list)
    segments: list[dict] = Field(default_factory=list)
    raw_message: str = ""
    self_id: str = ""
    bot_id: str = ""
    bot_name: str = ""
    sender_is_bot: bool = False
    mentions: list[dict] = Field(default_factory=list)
    reply_to: dict | None = None
    reply_to_message_id: str | None = None
    reply_to_sender_id: str | None = None
    reply_to_sender_name: str | None = None
    reply_to_content: str | None = None
    is_directed_to_other: bool = False
    _message_contract: InboundMessageContract | None = PrivateAttr(
        default=None
    )


@router.post("/group/message")
async def group_message(
    req: GroupMessageRequest,
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None,
    _auth=Depends(verify_token),
):
    """统一群聊入口：route 只做依赖注入，业务流程在 GroupIngressService。"""
    from app.group_ingress.service import GroupIngressService
    from app.group_ingress.message_adapter import (
        build_group_message_contract,
    )

    _normalize_request_client_meta(req, expected_chat_type="group")
    try:
        req._message_contract = build_group_message_contract(req)
    except (ChatStreamIdentityError, MessageContractError) as exc:
        code = str(getattr(exc, "code", "invalid_identity"))
        raise HTTPException(
            400,
            f"invalid message contract: {code}",
        ) from exc
    service = GroupIngressService(
        db=db,
        background_tasks=background_tasks,
        bridge_provider=_current_bridge_provider(),
        image_precache=_precache_image_sources,
    )
    return await service.handle(req)
