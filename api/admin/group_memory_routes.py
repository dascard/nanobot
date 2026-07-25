"""Admin Group Memory 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from api.admin.contract_models import (
    GroupMemoryExtractRequest,
    GroupMemoryExtractResponse,
    GroupMemoryInjectionConfigRequest,
    GroupMemoryInjectionConfigResponse,
    GroupMemoryInjectionPreviewRequest,
    GroupMemoryInjectionPreviewResponse,
    GroupMemoryListResponse,
    GroupMemoryOverviewResponse,
    GroupMemoryUpdateRequest,
    GroupMemoryUpdateResponse,
)
from api.endpoint_contracts import standard_error_responses
from core.db import (
    get_db,
    group_memory_record_to_dict,
    group_memory_repository,
)

router = APIRouter(tags=["admin-group-memory"])


@router.get(
    "/groups/{group_id:path}/memories",
    operation_id="adminGroupMemoriesListLegacy",
    response_model=GroupMemoryListResponse,
    responses=standard_error_responses(401, 422),
    deprecated=True,
)
def group_memories_list(
    group_id: str,
    memory_type: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """查询某群的 GroupMemory 列表——用于 WebUI 群体记忆页。"""
    return _group_memories_payload(db, group_id, memory_type)


def _group_memory_row_dict(r) -> dict:
    return dict(group_memory_record_to_dict(r))


def _group_memories_payload(db: Session, group_id: str, memory_type: str = "") -> dict:
    from app.group_memory.query_service import (
        GroupMemoryQueryService,
    )

    result = GroupMemoryQueryService(
        group_memory_repository(db)
    ).list_memories(
        group_id,
        platform="qq",
        memory_type=memory_type,
        limit=100,
    )
    return {
        "group_id": result.group_id,
        "chat_stream_id": result.chat_stream_id,
        "total": result.total,
        "memories": [
            _group_memory_row_dict(row)
            for row in result.memories
        ],
    }


@router.get(
    "/group-memories/overview",
    operation_id="adminGroupMemoryOverview",
    response_model=GroupMemoryOverviewResponse,
    responses=standard_error_responses(401, 422),
)
def group_memories_overview(
    limit: int = 300,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """群体记忆覆盖概览——列出有群聊日志或已有记忆的群。"""
    from app.group_memory.query_service import GroupMemoryQueryService

    items = GroupMemoryQueryService(
        group_memory_repository(db)
    ).build_overview(limit=limit)
    return {
        "total": len(items),
        "items": [item.to_dict() for item in items],
    }


@router.get(
    "/group-memories/{group_id:path}/items",
    operation_id="adminGroupMemoryItems",
    response_model=GroupMemoryListResponse,
    responses=standard_error_responses(401, 422),
)
def group_memory_items(
    group_id: str,
    memory_type: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """群体记忆专用列表接口，避免 /groups/{group_id:path} 路由吞掉子路径。"""
    return _group_memories_payload(db, group_id, memory_type)


@router.post(
    "/group-memories/{group_id:path}/extract",
    operation_id="adminGroupMemoryExtract",
    response_model=GroupMemoryExtractResponse,
    responses=standard_error_responses(
        400,
        401,
        404,
        409,
        422,
        500,
        502,
    ),
)
async def group_memory_extract_alias(
    group_id: str,
    body: GroupMemoryExtractRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """群体记忆专用提取接口，避免 /groups/{group_id:path} 路由吞掉子路径。"""
    return await _extract_group_memories_response(group_id, body, request, db)


@router.put(
    "/group-memories/{group_id:path}/injection-config",
    operation_id="adminGroupMemoryInjectionConfig",
    response_model=GroupMemoryInjectionConfigResponse,
    responses=standard_error_responses(401, 422),
)
def group_memory_injection_config(
    group_id: str,
    body: GroupMemoryInjectionConfigRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """开启/关闭群体记忆注入；写入 canonical qq:<raw>:group 配置。"""
    from app.group_memory.command_service import (
        GroupMemoryCommandService,
    )
    from app.group_memory.injection_service import (
        group_memory_config_ids,
    )

    chat_stream_id = group_memory_config_ids(group_id)[0]
    row = GroupMemoryCommandService(
        group_memory_repository(db)
    ).set_injection_mode(
        chat_stream_id=chat_stream_id,
        mode=body.group_profile_mode,
    )
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


@router.post(
    "/group-memories/{group_id:path}/injection-preview",
    operation_id="adminGroupMemoryInjectionPreview",
    response_model=GroupMemoryInjectionPreviewResponse,
    responses=standard_error_responses(401, 422),
)
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
        allow_model_calls=False,
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


@router.patch(
    "/group-memories/items/{memory_id}",
    operation_id="adminGroupMemoryUpdateItem",
    response_model=GroupMemoryUpdateResponse,
    responses=standard_error_responses(
        400,
        401,
        404,
        409,
        422,
    ),
)
def group_memory_update_item(
    memory_id: int,
    body: GroupMemoryUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """编辑群体记忆治理字段。"""
    from app.group_memory.command_service import (
        GroupMemoryCommandService,
        GroupMemoryDuplicate,
        GroupMemoryNotFound,
    )

    try:
        row = GroupMemoryCommandService(
            group_memory_repository(db)
        ).update_memory(
            memory_id,
            content=body.content,
            status=body.status,
            inject_policy=body.inject_policy,
            disabled_reason=body.disabled_reason,
            rejected_reason=body.rejected_reason,
        )
    except GroupMemoryNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except GroupMemoryDuplicate as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    updates = {
        field: value
        for field in (
            "content",
            "status",
            "inject_policy",
            "disabled_reason",
            "rejected_reason",
        )
        if (value := getattr(body, field)) is not None
    }
    audit_request(
        db,
        request,
        "update_group_memory_item",
        "group_memory",
        str(memory_id),
        updates,
    )
    return {
        "ok": True,
        "memory": _group_memory_row_dict(row),
    }


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
            aspects=body.aspects,
        )
    except extraction_service.GroupMemoryGroupNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except extraction_service.GroupMemoryInsufficientData as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except extraction_service.GroupMemoryLearningDisabled as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except extraction_service.GroupMemoryLearningFailed as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
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


@router.post(
    "/groups/{group_id:path}/memories/extract",
    operation_id="adminGroupMemoriesExtractLegacy",
    response_model=GroupMemoryExtractResponse,
    responses=standard_error_responses(
        400,
        401,
        404,
        409,
        422,
        500,
        502,
    ),
    deprecated=True,
)
async def group_memories_extract(
    group_id: str,
    body: GroupMemoryExtractRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """手动触发群体记忆提取。"""
    return await _extract_group_memories_response(group_id, body, request, db)
