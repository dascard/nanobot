"""Admin Group Memory 路由。"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from core.database import ChatStreamConfig, get_db

router = APIRouter(tags=["admin-group-memory"])


class GroupMemoryExtractRequest(BaseModel):
    window_hours: int = Field(default=24, ge=0, le=720)
    instructions: str = ""


class GroupMemoryInjectionConfigRequest(BaseModel):
    group_profile_mode: Literal["off", "preview", "on"] = "on"


class GroupMemoryInjectionPreviewRequest(BaseModel):
    user_input: str = ""
    max_items: int = Field(default=10, ge=1, le=30)
    max_chars: int = Field(default=1200, ge=200, le=4000)


class GroupMemoryUpdateRequest(BaseModel):
    content: Optional[str] = None
    status: Optional[Literal["review", "active", "disabled", "archived", "rejected"]] = None
    inject_policy: Optional[Literal["auto", "manual_only", "never"]] = None
    disabled_reason: Optional[str] = None
    rejected_reason: Optional[str] = None


@router.get("/groups/{group_id:path}/memories")
def group_memories_list(
    group_id: str,
    memory_type: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """查询某群的 GroupMemory 列表——用于 WebUI 群体记忆页。"""
    return _group_memories_payload(db, group_id, memory_type)


def _group_memory_row_dict(r) -> dict:
    return {
        "id": r.id, "group_id": r.group_id,
        "memory_type": r.memory_type, "content": r.content,
        "content_hash": r.content_hash or "",
        "cluster_key": r.cluster_key or "",
        "confidence": r.confidence, "evidence_count": r.evidence_count,
        "decay_score": r.decay_score,
        "first_seen": r.first_seen.strftime("%Y-%m-%d") if r.first_seen else "",
        "last_seen": r.last_seen.strftime("%Y-%m-%d") if r.last_seen else "",
        "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else "",
        "status": r.status, "source": r.source or "group_analysis",
        "inject_policy": getattr(r, "inject_policy", "auto") or "auto",
        "disabled_reason": getattr(r, "disabled_reason", "") or "",
        "rejected_reason": getattr(r, "rejected_reason", "") or "",
        "merged_into_id": getattr(r, "merged_into_id", None),
        "last_injected_at": r.last_injected_at.strftime("%Y-%m-%d %H:%M") if getattr(r, "last_injected_at", None) else "",
        "injected_count": getattr(r, "injected_count", 0) or 0,
        "evidence_log_ids_json": r.evidence_log_ids_json,
    }


def _group_memories_payload(db: Session, group_id: str, memory_type: str = "") -> dict:
    from core.database import GroupMemory
    from core.group_runtime.ids import normalize_group_session_id

    norm = normalize_group_session_id(group_id)
    q = db.query(GroupMemory).filter(GroupMemory.group_id == norm)
    if memory_type:
        q = q.filter(GroupMemory.memory_type == memory_type)
    rows = q.order_by(GroupMemory.confidence.desc(), GroupMemory.last_seen.desc()).limit(100).all()
    return {"group_id": norm, "total": len(rows), "memories": [_group_memory_row_dict(r) for r in rows]}


@router.get("/group-memories/overview")
def group_memories_overview(
    limit: int = 300,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """群体记忆覆盖概览——列出有群聊日志或已有记忆的群。"""
    from app.group_memory.extraction_service import build_group_memory_overview

    items = build_group_memory_overview(db, limit=limit)
    return {"total": len(items), "items": items}


@router.get("/group-memories/{group_id:path}/items")
def group_memory_items(
    group_id: str,
    memory_type: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """群体记忆专用列表接口，避免 /groups/{group_id:path} 路由吞掉子路径。"""
    return _group_memories_payload(db, group_id, memory_type)


@router.post("/group-memories/{group_id:path}/extract")
async def group_memory_extract_alias(
    group_id: str,
    body: GroupMemoryExtractRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """群体记忆专用提取接口，避免 /groups/{group_id:path} 路由吞掉子路径。"""
    return await _extract_group_memories_response(group_id, body, request, db)


@router.put("/group-memories/{group_id:path}/injection-config")
def group_memory_injection_config(
    group_id: str,
    body: GroupMemoryInjectionConfigRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """开启/关闭群体记忆注入；写入 canonical qq:<raw>:group 配置。"""
    from app.group_memory.injection_service import group_memory_config_ids

    chat_stream_id = group_memory_config_ids(group_id)[0]
    row = db.query(ChatStreamConfig).filter(ChatStreamConfig.chat_stream_id == chat_stream_id).first()
    if not row:
        row = ChatStreamConfig(chat_stream_id=chat_stream_id)
        db.add(row)
        db.flush()
    row.group_profile_mode = body.group_profile_mode
    db.commit()
    audit_request(
        db,
        request,
        "update_group_memory_injection",
        "group_memory",
        group_id,
        {"chat_stream_id": chat_stream_id, "group_profile_mode": body.group_profile_mode},
    )
    return {
        "ok": True,
        "group_id": group_id,
        "chat_stream_id": chat_stream_id,
        "group_profile_mode": row.group_profile_mode,
    }


@router.post("/group-memories/{group_id:path}/injection-preview")
def group_memory_injection_preview(
    group_id: str,
    body: GroupMemoryInjectionPreviewRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """模拟当前输入下会注入哪些群体记忆。"""
    from app.group_memory.injection_service import GroupMemoryInjectionService

    result = GroupMemoryInjectionService(db).build_context(
        group_id=group_id,
        current_user_input=body.user_input,
        recent_messages=[],
        max_items=body.max_items,
        max_chars=body.max_chars,
    )
    return {
        "group_id": group_id,
        "group_profile_mode": result.debug.get("group_profile_mode", "off"),
        "group_memory_context": result.debug.get("group_memory_context", ""),
        "group_memory_ids": result.selected_ids,
        "group_memory_skipped": result.skipped,
        "group_memory_context_chars": result.debug.get("group_memory_context_chars", 0),
        "score_components": result.score_components,
        "debug": result.debug,
    }


@router.patch("/group-memories/items/{memory_id}")
def group_memory_update_item(
    memory_id: int,
    body: GroupMemoryUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """编辑群体记忆治理字段。"""
    from core.database import GroupMemory
    from core.group_memory import _cluster_key, _content_hash

    row = db.query(GroupMemory).filter(GroupMemory.id == memory_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="group memory not found")
    updates: dict = {}
    if body.content is not None:
        content = str(body.content).strip()
        if not content:
            raise HTTPException(status_code=400, detail="content is empty")
        content_hash = _content_hash(content)
        duplicate = db.query(GroupMemory).filter(
            GroupMemory.id != row.id,
            GroupMemory.group_id == row.group_id,
            GroupMemory.memory_type == row.memory_type,
            GroupMemory.content_hash == content_hash,
        ).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="已有相同记忆，可合并或归档")
        row.content = content
        row.content_hash = content_hash
        row.cluster_key = _cluster_key(content)
        updates["content"] = content
    for field in ("status", "inject_policy", "disabled_reason", "rejected_reason"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
            updates[field] = value
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="已有相同记忆，可合并或归档") from exc
    audit_request(
        db,
        request,
        "update_group_memory_item",
        "group_memory",
        str(memory_id),
        updates,
    )
    return {"ok": True, "memory": _group_memory_row_dict(row)}


async def _extract_group_memories_response(
    group_id: str,
    body: GroupMemoryExtractRequest,
    request: Request,
    db: Session,
) -> dict:
    from app.group_memory import extraction_service

    try:
        result = await extraction_service.extract_group_memories(
            db,
            group_id,
            window_hours=body.window_hours,
            instructions=body.instructions,
        )
    except extraction_service.GroupMemoryGroupNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except extraction_service.GroupMemoryInsufficientData as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    audit_request(
        db,
        request,
        "extract_group_memory",
        "group_memory",
        group_id,
        result.to_dict(),
    )
    payload = result.to_dict()
    payload.update(_group_memories_payload(db, payload.get("group_id") or group_id))
    return payload


@router.post("/groups/{group_id:path}/memories/extract")
async def group_memories_extract(
    group_id: str,
    body: GroupMemoryExtractRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """手动触发群体记忆提取。"""
    return await _extract_group_memories_response(group_id, body, request, db)
