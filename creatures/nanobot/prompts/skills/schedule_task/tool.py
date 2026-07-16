"""
Schedule Task tool — manage cron-based push tasks from within KT conversation.
"""

import logging
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

logger = logging.getLogger("nanobot.tool.schedule_task")


class ScheduleTaskTool(BaseTool):
    """Manage timed push notification tasks for QQ."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "schedule_task"

    @property
    def description(self) -> str:
        return (
            "管理定时推送任务。支持创建、查看、修改、启停、立即执行和删除。"
            "用户说'每天X点推送Y'时创建，'看看定时任务'时列出，"
            "'停掉XX任务'时禁用，'现在执行XX任务'时立即运行。"
            "cron 按 Asia/Shanghai 解释，格式为'分 时 日 月 周'。"
            "创建任务时如果用户没有明确目标会话，可使用当前 runtime_context 对应的私聊或群聊。"
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
                    "description": "create(创建) | list(列出) | update(修改) | toggle(启停) | run(立即执行) | delete(删除)",
                    "enum": ["create", "list", "update", "toggle", "run", "delete"],
                },
                "task_id": {"type": "integer", "description": "任务ID（update/toggle/delete 必填）"},
                "name": {"type": "string", "description": "任务名（create/update）"},
                "cron_expr": {
                    "type": "string",
                    "description": "cron 表达式（create/update），Asia/Shanghai 时区，格式'分 时 日 月 周'，如每天9点为 0 9 * * *",
                },
                "target_type": {
                    "type": "string",
                    "description": "推送类型: private 或 group；创建时留空则尝试使用当前会话类型",
                    "enum": ["private", "group"],
                },
                "target_id": {
                    "type": "string",
                    "description": "QQ号或群号；创建时留空则尝试使用当前 runtime_context 的 user_id/group_id",
                },
                "prompt_template": {
                    "type": "string",
                    "description": "LLM 生成推送内容的提示模板，不是直接发送的固定文本",
                },
                "idempotency_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                    "description": "手动 run 必填；同一请求重试必须复用同一幂等键",
                },
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
                        attempted = (
                            t.last_attempt_at.strftime("%m-%d %H:%M")
                            if t.last_attempt_at
                            else "从未"
                        )
                        succeeded = (
                            t.last_success_at.strftime("%m-%d %H:%M")
                            if t.last_success_at
                            else "从未"
                        )
                        lines.append(
                            f"[{t.id}] {s} {t.name} | cron={t.cron_expr} "
                            f"| 最近尝试={attempted} | 最近成功={succeeded} "
                            f"| 投递状态={t.delivery_status}"
                        )
                    return ToolResult(output="\n".join(lines), exit_code=0)

                if action == "run":
                    if not task_id:
                        return ToolResult(error="run 需要 task_id")
                    idempotency_key = str(args.get("idempotency_key") or "").strip()
                    if not idempotency_key:
                        return ToolResult(error="run 需要显式幂等键")
                    from core.scheduled_task_outbound import (
                        ScheduledTaskNotFoundError,
                        enqueue_scheduled_task_occurrence,
                    )
                    try:
                        result = await enqueue_scheduled_task_occurrence(
                            db,
                            task_id=int(task_id),
                            trigger_type="manual",
                            manual_idempotency_key=idempotency_key,
                            session_factory=SessionLocal,
                        )
                    except ScheduledTaskNotFoundError:
                        return ToolResult(error=f"任务 {task_id} 不存在")
                    if result.status in {"queued", "pending"}:
                        return ToolResult(
                            output=(
                                f"任务已入队 run_id={result.run_id} "
                                f"outbox_id={result.outbox_id}"
                            ),
                            exit_code=0,
                        )
                    if result.status == "delivered":
                        return ToolResult(
                            output=f"任务已确认投递 run_id={result.run_id}",
                            exit_code=0,
                        )
                    if result.status in {"retry_wait", "delivering"}:
                        return ToolResult(
                            output=(
                                f"相同请求已存在 当前状态={result.status} "
                                f"run_id={result.run_id}"
                            ),
                            exit_code=0,
                        )
                    if result.status == "ambiguous":
                        return ToolResult(error="投递结果不确定 需要人工核验")
                    if result.status == "blocked":
                        return ToolResult(error="投递通道当前已阻断")
                    return ToolResult(error="任务生成或入队失败")

                if action == "delete":
                    if not task_id:
                        return ToolResult(error="delete 需要 task_id")
                    t = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                    if not t:
                        return ToolResult(error=f"任务 {task_id} 不存在")
                    from core.scheduled_task_outbound import (
                        cancel_scheduled_task_deliveries,
                    )

                    cancellation = cancel_scheduled_task_deliveries(
                        db,
                        task=t,
                        reason_type="task_deleted",
                        safe_summary="任务已删除",
                    )
                    if cancellation.unsafe:
                        db.rollback()
                        return ToolResult(error="任务仍有投递中或结果不确定记录")
                    task_name = str(t.name)
                    db.delete(t)
                    db.commit()
                    return ToolResult(output=f"任务 {task_id} ({task_name}) 已删除", exit_code=0)

                if action == "toggle":
                    if not task_id:
                        return ToolResult(error="toggle 需要 task_id")
                    t = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                    if not t:
                        return ToolResult(error=f"任务 {task_id} 不存在")
                    from core.scheduled_task_outbound import (
                        cancel_scheduled_task_deliveries,
                    )

                    cancellation = cancel_scheduled_task_deliveries(
                        db,
                        task=t,
                        reason_type="task_toggled",
                        safe_summary="任务启停状态已修改",
                    )
                    if cancellation.unsafe:
                        db.rollback()
                        return ToolResult(error="任务仍有投递中或结果不确定记录")
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
                    from core.scheduled_task_outbound import (
                        cancel_scheduled_task_deliveries,
                    )

                    cancellation = cancel_scheduled_task_deliveries(
                        db,
                        task=t,
                        reason_type="task_updated",
                        safe_summary="任务定义已修改",
                    )
                    if cancellation.unsafe:
                        db.rollback()
                        return ToolResult(error="任务仍有投递中或结果不确定记录")
                    for f in ("name", "cron_expr", "target_type", "target_id", "prompt_template"):
                        v = args.get(f)
                        if v is not None and str(v).strip():
                            setattr(t, f, str(v).strip())
                    db.commit()
                    return ToolResult(output=f"任务 {task_id} ({t.name}) 已更新", exit_code=0)

                # create
                name = str(args.get("name", "")).strip()
                cron = str(args.get("cron_expr", "")).strip()
                metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
                context = kwargs.get("context")
                session = getattr(context, "session", None) if context is not None else None
                session_extra = getattr(session, "extra", {}) if session is not None else {}
                runtime_meta = {}
                if isinstance(session_extra, dict):
                    runtime_meta = session_extra.get("nanobot_runtime_context") or {}
                if isinstance(runtime_meta, dict):
                    metadata = {**runtime_meta, **metadata}
                meta_is_group = bool(metadata.get("is_group") or metadata.get("chat_type") == "group")
                default_ttype = "group" if meta_is_group else "private"
                default_tid = str(
                    metadata.get("group_id") if meta_is_group else metadata.get("user_id")
                    or ""
                ).strip()
                ttype = str(args.get("target_type") or default_ttype).strip()
                tid = str(args.get("target_id", "")).strip()
                if not tid:
                    tid = default_tid
                prompt = str(args.get("prompt_template", "")).strip()
                if not all([name, cron, tid, prompt]):
                    return ToolResult(error="create 需要 name, cron_expr, target_id, prompt_template")
                t = ScheduledTask(name=name, cron_expr=cron, target_type=ttype, target_id=tid, prompt_template=prompt)
                db.add(t)
                db.commit()
                logger.info("[schedule_task] Created id=%s cron=%s", t.id, cron)
                return ToolResult(output=f"已创建 (id={t.id}): {name} | cron={cron}", exit_code=0)
            finally:
                db.close()
        except Exception as exc:
            logger.error(
                "[schedule_task] Failed error_type=%s",
                type(exc).__name__,
            )
            return ToolResult(error="定时任务操作失败")
