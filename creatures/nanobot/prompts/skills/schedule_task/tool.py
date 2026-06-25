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

                if action == "run":
                    if not task_id:
                        return ToolResult(error="run 需要 task_id")
                    t = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                    if not t:
                        return ToolResult(error=f"任务 {task_id} 不存在")
                    from core.daily_digest import _generate_task_message, push_envelope_to_qq
                    from core.message_envelope import build_chat_response_envelope

                    logger.info(f"[schedule_task] Manual run: {t.name}")
                    content = await _generate_task_message(t)
                    if not content:
                        return ToolResult(error=f"任务 {t.name} 生成内容失败")
                    target_type = str(t.target_type or "").strip()
                    target_id = str(t.target_id or "").strip()
                    envelope = build_chat_response_envelope(
                        status="ok",
                        answer=content,
                        meta={
                            "platform": "qq",
                            "chat_type": "private" if target_type == "private" else "group",
                            "source": "schedule_task_tool",
                            "task_id": t.id,
                        },
                    )
                    ok = await push_envelope_to_qq(target_type, target_id, envelope)
                    if ok:
                        from core.time_utils import db_now_naive

                        t.last_run_at = db_now_naive()
                        db.commit()
                        preview = content[:200] + ("..." if len(content) > 200 else "")
                        return ToolResult(
                            output=f"任务 {t.name} 已执行并推送 → {t.target_type}/{t.target_id}\n预览: {preview}",
                            exit_code=0,
                        )
                    return ToolResult(error=f"任务 {t.name} 推送失败")

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
                logger.info(f"[schedule_task] Created: {name} cron={cron} -> {ttype}/{tid}")
                return ToolResult(output=f"已创建 (id={t.id}): {name} | cron={cron}", exit_code=0)
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[schedule_task] Failed: {e}", exc_info=True)
            return ToolResult(error=str(e))
