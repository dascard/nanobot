"""普通 API 表情包与公开媒体代理路由。"""
from __future__ import annotations

import logging
import os
from hmac import compare_digest

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.database import StickerMemory, get_db
from core.generated_images import get_generated_image_path
from core.sticker_memory import (
    auto_describe_sticker,
    disable_sticker,
    register_sticker,
    search_stickers,
)
from core.sticker_preview import (
    cache_sticker_preview,
    media_type_for_path,
    safe_existing_local_path,
)

logger = logging.getLogger("nanobot.routes.sticker_media")
router = APIRouter(tags=["sticker-media"])


class StickerRegisterRequest(BaseModel):
    group_id: str = ""
    chat_stream_id: str = ""
    file_ref: str
    sticker_hash: str = ""
    send_code: str = ""
    name: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    emotions: list[str] = Field(default_factory=list)
    source_type: str = "manual"
    status: str = "active"
    auto_describe: bool = False
    client_meta: dict | None = None


@router.post("/stickers/register")
def register_sticker_endpoint(
    req: StickerRegisterRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """注册或更新表情包；可选后台 Qwen 描述补全。"""
    try:
        sticker = register_sticker(
            db,
            chat_stream_id=req.chat_stream_id,
            group_id=req.group_id,
            file_ref=req.file_ref,
            sticker_hash=req.sticker_hash,
            send_code=req.send_code,
            name=req.name,
            description=req.description,
            tags=req.tags,
            emotions=req.emotions,
            source_type=req.source_type,
            status=req.status,
            meta=req.client_meta or {},
        )
        if req.auto_describe and not sticker.get("description"):
            background_tasks.add_task(auto_describe_sticker, sticker["id"])
        return {"status": "ok", "sticker": sticker}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/stickers/search")
def search_sticker_endpoint(
    query: str = "",
    group_id: str = "",
    chat_stream_id: str = "",
    limit: int = 5,
    include_global: bool = True,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """搜索当前群或全局表情包。"""
    return {
        "status": "ok",
        "results": search_stickers(
            db,
            query,
            group_id=group_id,
            chat_stream_id=chat_stream_id,
            limit=max(1, min(limit, 20)),
            include_global=include_global,
        ),
    }


@router.get("/stickers/{sticker_id}/image")
def public_sticker_image(
    sticker_id: int,
    token: str = "",
    db: Session = Depends(get_db),
):
    """公开表情包图片代理端点——用于 OneBot/NapCat 通过 HTTP 拉取本地缓存。"""
    expected_token = str(os.environ.get("NANOBOT_STICKER_IMAGE_TOKEN") or "").strip()
    if expected_token and not compare_digest(str(token or ""), expected_token):
        raise HTTPException(status_code=403, detail="invalid sticker image token")

    try:
        row = db.query(StickerMemory).filter(StickerMemory.id == int(sticker_id)).first()
        if row is None:
            raise HTTPException(status_code=404, detail="sticker not found")

        # duplicate -> 先跳转到 canonical，再判断状态
        if row.duplicate_of_id:
            canonical = db.query(StickerMemory).filter(
                StickerMemory.id == row.duplicate_of_id
            ).first()
            if canonical is None:
                raise HTTPException(status_code=404, detail="canonical sticker not found")
            row = canonical

        if str(row.status or "") not in ("active",):
            raise HTTPException(status_code=404, detail="sticker not active")

        local = safe_existing_local_path(row.local_path or "")
        if not local:
            result = cache_sticker_preview(db, row.id, force=True)
            if not result.ok:
                raise HTTPException(
                    status_code=404,
                    detail=f"sticker cache unavailable: {result.status}",
                )
            local = safe_existing_local_path(result.local_path)

        if not local:
            raise HTTPException(status_code=404, detail="local sticker file missing")

        return FileResponse(
            local,
            media_type=media_type_for_path(local),
            filename=os.path.basename(local),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[StickerImage] serve failed id={sticker_id}: {e}")
        raise HTTPException(status_code=500, detail="sticker image serve failed")


@router.get("/generated-images/{image_id}/image")
def public_generated_image(
    image_id: str,
    token: str = "",
):
    """公开生成图片代理端点——用于 OneBot/NapCat 通过 HTTP 拉取。"""
    expected_token = str(os.environ.get("NANOBOT_GENERATED_IMAGE_TOKEN") or "").strip()
    if expected_token and not compare_digest(str(token or ""), expected_token):
        raise HTTPException(status_code=403, detail="invalid generated image token")

    try:
        path = get_generated_image_path(image_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="generated image not found")

    return FileResponse(path, media_type="image/png")


@router.post("/stickers/{sticker_id}/disable")
def disable_sticker_endpoint(
    sticker_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """禁用表情包，搜索工具默认不再返回。"""
    try:
        return {"status": "ok", "sticker": disable_sticker(db, sticker_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
