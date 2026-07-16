"""普通 API 定时任务路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.database import get_db
from core.outbound_delivery import OutboundFencingError, OutboundSafetyError
from core.scheduled_task_outbound import (
    ScheduledTaskNotFoundError,
    cancel_scheduled_task_deliveries,
    enqueue_scheduled_task_occurrence,
)


logger = logging.getLogger("nanobot.routes")
router = APIRouter(tags=["tasks"])


class ScheduledTaskCreate(BaseModel):
    name: str
    cron_expr: str = "0 9 * * *"
    target_type: str = "private"
    target_id: str
    prompt_template: str


@router.post("/tasks")
def create_scheduled_task(
    req: ScheduledTaskCreate,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """创建定时任务。例如每天9点推送AI新闻到私聊。"""
    from core.database import ScheduledTask as ST

    task = ST(
        name=req.name,
        cron_expr=req.cron_expr,
        target_type=req.target_type,
        target_id=req.target_id,
        prompt_template=req.prompt_template,
    )
    db.add(task)
    db.commit()
    logger.info("Scheduled task created: %s cron=%s", req.name, req.cron_expr)
    return {"status": "ok", "id": task.id}


@router.get("/tasks")
def list_scheduled_tasks(
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """列出所有定时任务。"""
    from core.database import ScheduledTask as ST

    tasks = db.query(ST).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "cron": t.cron_expr,
            "target_type": t.target_type,
            "target_configured": bool(str(t.target_id or "").strip()),
            "enabled": t.enabled,
            "last_run": t.last_run_at.isoformat() if t.last_run_at else None,
            "last_attempt_at": (
                t.last_attempt_at.isoformat() if t.last_attempt_at else None
            ),
            "last_success_at": (
                t.last_success_at.isoformat() if t.last_success_at else None
            ),
            "delivery_status": t.delivery_status,
        }
        for t in tasks
    ]


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
    cancellation = cancel_scheduled_task_deliveries(
        db,
        task=t,
        reason_type="task_updated",
        safe_summary="任务定义已修改",
    )
    if cancellation.unsafe:
        db.rollback()
        raise HTTPException(status_code=409, detail="任务仍有投递中或结果不确定记录")
    t.name = req.name
    t.cron_expr = req.cron_expr
    t.target_type = req.target_type
    t.target_id = req.target_id
    t.prompt_template = req.prompt_template
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
    """幂等登记一次手动运行；生成后先持久化再投递。"""
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key 不能为空")
    from core import database

    try:
        result = await enqueue_scheduled_task_occurrence(
            db,
            task_id=task_id,
            trigger_type="manual",
            manual_idempotency_key=normalized_key,
            session_factory=database.SessionLocal,
        )
    except ScheduledTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except (OutboundFencingError, OutboundSafetyError) as exc:
        raise HTTPException(status_code=409, detail="任务当前不可安全执行") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="幂等键或任务参数无效") from exc

    if result.status == "failed" and result.outbox_id is None:
        raise HTTPException(status_code=502, detail="任务内容生成失败")
    if result.status == "blocked":
        raise HTTPException(status_code=503, detail="投递通道当前不可用")
    logger.info(
        "Manual scheduled task accepted task_id=%s run_id=%s status=%s",
        task_id,
        result.run_id,
        result.status,
    )
    return {
        "status": result.status,
        "run_id": result.run_id,
        "outbox_id": result.outbox_id,
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
