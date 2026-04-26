"""
Schedule Task tool — manage cron-based push tasks from within KT conversation.
"""

import logging
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

logger = logging.getLogger("nanobot.tool.schedule_task")


class ScheduleTaskTool(BaseTool):
    """Manage timed push notification tasks for QQ."""

    @property
    def tool_name(self) -> str:
        return "schedule_task"

    @property
    def description(self) -> str:
        return (
            "管理定时推送任务。支持创建、查看、修改、启停和删除。"
            "用户说'每天X点推送Y'时创建，'看看定时任务'时列出，'停掉XX任务'时禁用。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "create(创建) | list(列出全部) | update(修改) | toggle(启停) | delete(删除)",
                    "enum": ["create", "list", "update", "toggle", "delete"],
                },
                "task_id": {"type": "integer", "description": "任务ID（update/toggle/delete 必填）"},
                "name": {"type": "string", "description": "任务名（create/update）"},
                "cron_expr": {"type": "string", "description": "cron 表达式（create/update）"},
                "target_type": {"type": "string", "description": "推送类型: private 或 group"},
                "target_id": {"type": "string", "description": "QQ号 或 群号"},
                "prompt_template": {"type": "string", "description": "LLM 生成内容的提示模板"},
            },
            "required": ["action"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        from core.database import SessionLocal, ScheduledTask

        action = str(args.get("action", "create")).strip()
        task_id = args.get("task_id")

        try:
            db = SessionLocal()
            try:
                if action == "list":
                    tasks = db.query(ScheduledTask).all()
                    if not tasks:
                        return ToolResult(output="暂无定时任务", exit_code=0)
                    lines = []
                    for t in tasks:
                        s = "启用" if t.enabled else "禁用"
                        last = t.last_run_at.strftime("%m-%d %H:%M") if t.last_run_at else "从未"
                        lines.append(
                            f"[{t.id}] {s} {t.name} | cron={t.cron_expr} "
                            f"| ->{t.target_type}/{t.target_id} | 上次={last}"
                        )
                    return ToolResult(output="\n".join(lines), exit_code=0)

                if action == "delete":
                    if not task_id:
                        return ToolResult(error="delete 需要 task_id")
                    t = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                    if not t:
                        return ToolResult(error=f"任务 {task_id} 不存在")
                    db.delete(t)
                    db.commit()
                    return ToolResult(output=f"任务 {task_id} ({t.name}) 已删除", exit_code=0)

                if action == "toggle":
                    if not task_id:
                        return ToolResult(error="toggle 需要 task_id")
                    t = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                    if not t:
                        return ToolResult(error=f"任务 {task_id} 不存在")
                    t.enabled = 0 if t.enabled else 1
                    db.commit()
                    s = "启用" if t.enabled else "禁用"
                    return ToolResult(output=f"任务 {task_id} ({t.name}) 已{s}", exit_code=0)

                if action == "update":
                    if not task_id:
                        return ToolResult(error="update 需要 task_id")
                    t = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                    if not t:
                        return ToolResult(error=f"任务 {task_id} 不存在")
                    for f in ("name", "cron_expr", "target_type", "target_id", "prompt_template"):
                        v = args.get(f)
                        if v is not None and str(v).strip():
                            setattr(t, f, str(v).strip())
                    db.commit()
                    return ToolResult(output=f"任务 {task_id} ({t.name}) 已更新", exit_code=0)

                # create
                name = str(args.get("name", "")).strip()
                cron = str(args.get("cron_expr", "")).strip()
                ttype = str(args.get("target_type", "private")).strip()
                tid = str(args.get("target_id", "")).strip()
                prompt = str(args.get("prompt_template", "")).strip()
                if not all([name, cron, tid, prompt]):
                    return ToolResult(error="create 需要 name, cron_expr, target_id, prompt_template")
                t = ScheduledTask(name=name, cron_expr=cron, target_type=ttype, target_id=tid, prompt_template=prompt)
                db.add(t)
                db.commit()
                logger.info(f"[schedule_task] Created: {name} cron={cron} -> {ttype}/{tid}")
                return ToolResult(output=f"已创建 (id={t.id}): {name} | cron={cron}", exit_code=0)
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[schedule_task] Failed: {e}", exc_info=True)
            return ToolResult(error=str(e))
