"""普通 API 定时任务路由。"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.database import get_db


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
            "target": f"{t.target_type}/{t.target_id}",
            "enabled": t.enabled,
            "last_run": t.last_run_at.isoformat() if t.last_run_at else None,
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
    t.enabled = 0 if t.enabled else 1
    db.commit()
    return {"status": "ok", "enabled": bool(t.enabled)}


@router.post("/tasks/{task_id}/run")
async def run_scheduled_task_now(
    task_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """立即执行指定定时任务（生成内容并推送）。"""
    from core.daily_digest import _generate_task_message, push_envelope_to_qq
    from core.database import ScheduledTask as ST
    from core.message_envelope import build_chat_response_envelope

    t = db.query(ST).filter(ST.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    logger.info("Manual run: %s", t.name)
    content = await _generate_task_message(t)
    if not content:
        raise HTTPException(status_code=500, detail="LLM returned no content")

    envelope = build_chat_response_envelope(
        status="ok",
        answer=content,
        meta={
            "platform": "qq",
            "chat_type": "scheduled_task",
            "task_id": t.id,
            "task_name": t.name,
            "target_type": t.target_type,
            "target_id": t.target_id,
        },
    )
    ok = await push_envelope_to_qq(t.target_type, t.target_id, envelope)
    if ok:
        t.last_run_at = datetime.now()
        db.commit()
        return {
            "status": "ok",
            "content": content[:200],
            "target": f"{t.target_type}/{t.target_id}",
        }
    raise HTTPException(status_code=502, detail="Push to QQ failed")


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
    db.delete(t)
    db.commit()
    return {"status": "ok"}
