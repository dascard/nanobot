"""普通 API 群聊入口路由。"""
from __future__ import annotations

import sys
from typing import Any, Optional, List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.client_meta import ClientMetaValidationError, normalize_client_meta
from core.database import get_db
from nanobot_kt.bridge import get_bridge as _default_get_bridge

router = APIRouter(tags=["group-message"])


def _current_bridge_provider():
    routes = sys.modules.get("api.routes")
    if routes is not None and hasattr(routes, "get_bridge"):
        return getattr(routes, "get_bridge")
    return _default_get_bridge


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
    files: Optional[List[str]] = None
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


@router.post("/group/message")
async def group_message(
    req: GroupMessageRequest,
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None,
    _auth=Depends(verify_token),
):
    """统一群聊入口：route 只做依赖注入，业务流程在 GroupIngressService。"""
    from app.group_ingress.service import GroupIngressService

    _normalize_request_client_meta(req, expected_chat_type="group")
    service = GroupIngressService(
        db=db,
        background_tasks=background_tasks,
        bridge_provider=_current_bridge_provider(),
    )
    return await service.handle(req)
