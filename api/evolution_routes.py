"""普通 API 自进化手动触发路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from api.common_auth import verify_token
from core.evolution import evolution_task


logger = logging.getLogger("nanobot.routes.evolution")
router = APIRouter(tags=["evolution"])


class EvolutionTriggerRequest(BaseModel):
    user_id: str


@router.post("/evolution/trigger")
def trigger_evolution(
    req: EvolutionTriggerRequest,
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_token),
):
    """
    手动触发自进化：通过 API 强制开启画像提炼与同步，不再依赖日志计数阈值。
    """
    logger.info("Manual evolution triggered for user [%s]", req.user_id)
    background_tasks.add_task(evolution_task, req.user_id)
    return {"status": "ok", "message": f"Evolution task queued for {req.user_id}"}
