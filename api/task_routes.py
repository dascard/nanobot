"""普通 API 定时任务路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.database import get_db
from core.schedule_spec import (
    ResolvedScheduleFields,
    ScheduleSpecError,
    resolve_schedule_fields,
    utc_now_naive,
)
from core.scheduled_task_outbound import (
    ScheduledTaskNotFoundError,
    cancel_scheduled_task_deliveries,
)
from core.scheduled_task_contract import (
    MAX_SCHEDULED_TASK_NAME_CHARS,
    MAX_SCHEDULED_TASK_PROGRAM_BYTES,
    MAX_SCHEDULED_TASK_PROMPT_CHARS,
    ScheduledTaskContractError,
    apply_scheduled_task_program,
    apply_scheduled_task_owner,
    normalize_scheduled_task_definition,
    scheduled_task_owner_from_target,
)
from core.scheduled_workflow import (
    enqueue_scheduled_task_execution,
)


logger = logging.getLogger("nanobot.routes")
router = APIRouter(tags=["tasks"])


class ScheduledTaskCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=MAX_SCHEDULED_TASK_NAME_CHARS,
    )
    schedule: str | None = None
    cron_expr: str = "0 9 * * *"
    target_type: str = "private"
    target_id: str
    prompt_template: str | None = Field(
        default=None,
        max_length=MAX_SCHEDULED_TASK_PROMPT_CHARS,
    )
    content: str | None = Field(
        default=None,
        max_length=MAX_SCHEDULED_TASK_PROGRAM_BYTES,
    )
    program: dict | None = None


def _resolved_schedule_or_422(req: ScheduledTaskCreate) -> ResolvedScheduleFields:
    try:
        return resolve_schedule_fields(
            schedule=req.schedule,
            cron_expr=req.cron_expr,
            now_utc=utc_now_naive(),
        )
    except ScheduleSpecError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"schedule 无效: {exc}",
        ) from exc


def _owner_and_definition_or_422(req: ScheduledTaskCreate):
    try:
        owner = scheduled_task_owner_from_target(
            target_type=req.target_type,
            target_id=req.target_id,
            platform="qq",
            created_by_actor_id="admin:api",
        )
        (
            name,
            prompt,
            program,
            program_json,
            program_sha256,
        ) = normalize_scheduled_task_definition(
            name=req.name,
            prompt_template=req.prompt_template,
            program=req.program,
            content=req.content,
        )
        return (
            owner,
            name,
            prompt,
            program,
            program_json,
            program_sha256,
        )
    except ScheduledTaskContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/tasks")
def create_scheduled_task(
    req: ScheduledTaskCreate,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """创建定时任务。例如每天9点推送AI新闻到私聊。"""
    from core.database import ScheduledTask as ST

    fields = _resolved_schedule_or_422(req)
    (
        owner,
        name,
        prompt,
        program,
        _program_json,
        _program_sha256,
    ) = _owner_and_definition_or_422(req)
    task = ST(
        cron_expr=fields.cron_expr,
        schedule_kind=fields.schedule_kind,
        schedule_spec=fields.schedule_spec,
        next_fire_at=fields.next_fire_at,
        target_type=owner.target_type,
        target_id=owner.target_id,
        definition_version=1,
    )
    apply_scheduled_task_program(
        task,
        name=name,
        prompt_template=prompt,
        program=program,
    )
    apply_scheduled_task_owner(task, owner)
    db.add(task)
    db.commit()
    logger.info(
        "Scheduled task created: %s schedule=%s", name, fields.display
    )
    return {"status": "ok", "id": task.id}


@router.get("/tasks")
def list_scheduled_tasks(
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """列出所有定时任务。"""
    from core.database import ScheduledTask as ST, ScheduledTaskExecution

    tasks = db.query(ST).all()
    result = []
    for task in tasks:
        latest_execution = (
            db.query(ScheduledTaskExecution)
            .filter(ScheduledTaskExecution.task_id == int(task.id))
            .order_by(ScheduledTaskExecution.id.desc())
            .first()
        )
        result.append({
            "id": task.id,
            "name": task.name,
            "cron": task.cron_expr,
            "schedule_kind": task.schedule_kind,
            "next_fire_at": (
                task.next_fire_at.isoformat()
                if task.next_fire_at
                else None
            ),
            "target_type": task.target_type,
            "definition_version": task.definition_version,
            "owner_migration_required": bool(
                task.owner_migration_required
            ),
            "owner_configured": bool(
                str(task.owner_chat_stream_id or "").strip()
            ),
            "target_configured": bool(
                str(task.target_id or "").strip()
            ),
            "enabled": task.enabled,
            "last_run": (
                task.last_run_at.isoformat()
                if task.last_run_at
                else None
            ),
            "last_attempt_at": (
                task.last_attempt_at.isoformat()
                if task.last_attempt_at
                else None
            ),
            "last_success_at": (
                task.last_success_at.isoformat()
                if task.last_success_at
                else None
            ),
            "delivery_status": task.delivery_status,
            "workflow_status": (
                str(latest_execution.status)
                if latest_execution is not None
                else "never"
            ),
            "workflow_error_code": (
                str(latest_execution.last_error_code or "")
                if latest_execution is not None
                else ""
            ),
        })
    return result


@router.put("/tasks/{task_id}")
def update_scheduled_task(
    task_id: int,
    req: ScheduledTaskCreate,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """修改定时任务。"""
    from core.database import ScheduledTask as ST

    t = db.query(ST).filter(ST.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    fields = _resolved_schedule_or_422(req)
    (
        owner,
        name,
        prompt,
        program,
        _program_json,
        _program_sha256,
    ) = _owner_and_definition_or_422(req)
    cancellation = cancel_scheduled_task_deliveries(
        db,
        task=t,
        reason_type="task_updated",
        safe_summary="任务定义已修改",
    )
    if cancellation.unsafe:
        db.rollback()
        raise HTTPException(status_code=409, detail="任务仍有投递中或结果不确定记录")
    t.cron_expr = fields.cron_expr
    t.schedule_kind = fields.schedule_kind
    t.schedule_spec = fields.schedule_spec
    t.next_fire_at = fields.next_fire_at
    t.target_type = owner.target_type
    t.target_id = owner.target_id
    apply_scheduled_task_program(
        t,
        name=name,
        prompt_template=prompt,
        program=program,
    )
    apply_scheduled_task_owner(t, owner)
    t.definition_version = int(t.definition_version or 0) + 1
    t.updated_at = utc_now_naive()
    db.commit()
    return {"status": "ok"}


@router.post("/tasks/{task_id}/toggle")
def toggle_scheduled_task(
    task_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """启用/禁用定时任务。"""
    from core.database import ScheduledTask as ST

    t = db.query(ST).filter(ST.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    cancellation = cancel_scheduled_task_deliveries(
        db,
        task=t,
        reason_type="task_toggled",
        safe_summary="任务启停状态已修改",
    )
    if cancellation.unsafe:
        db.rollback()
        raise HTTPException(status_code=409, detail="任务仍有投递中或结果不确定记录")
    t.enabled = 0 if t.enabled else 1
    if t.enabled:
        # 重新启用时由调度器按当前时间重排，不能沿用禁用前的过期槽位。
        t.next_fire_at = None
    t.definition_version = int(t.definition_version or 0) + 1
    t.updated_at = utc_now_naive()
    db.commit()
    return {"status": "ok", "enabled": bool(t.enabled)}


@router.post("/tasks/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_scheduled_task_now(
    task_id: int,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=512,
    ),
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """幂等登记一次手动执行；worker 异步执行 program。"""
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key 不能为空")
    try:
        result = enqueue_scheduled_task_execution(
            db,
            task_id=task_id,
            trigger_type="manual",
            manual_idempotency_key=normalized_key,
        )
    except ScheduledTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="幂等键或任务参数无效") from exc
    db.commit()
    logger.info(
        "Manual scheduled task accepted task_id=%s execution_id=%s status=%s",
        task_id,
        result.execution_id,
        result.status,
    )
    return {
        "status": result.status,
        "execution_id": result.execution_id,
        "deduplicated": result.deduplicated,
    }


@router.delete("/tasks/{task_id}")
def delete_scheduled_task(
    task_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """删除定时任务。"""
    from core.database import ScheduledTask as ST

    t = db.query(ST).filter(ST.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    cancellation = cancel_scheduled_task_deliveries(
        db,
        task=t,
        reason_type="task_deleted",
        safe_summary="任务已删除",
    )
    if cancellation.unsafe:
        db.rollback()
        raise HTTPException(status_code=409, detail="任务仍有投递中或结果不确定记录")
    db.delete(t)
    db.commit()
    return {"status": "ok"}
