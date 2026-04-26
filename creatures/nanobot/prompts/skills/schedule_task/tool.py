"""
Schedule Task tool — create cron-based push tasks from within KT conversation.
"""

import logging
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

logger = logging.getLogger("nanobot.tool.schedule_task")


class ScheduleTaskTool(BaseTool):
    """Create a timed push notification task that sends LLM-generated content to QQ."""

    @property
    def tool_name(self) -> str:
        return "schedule_task"

    @property
    def description(self) -> str:
        return (
            "创建定时推送任务。用户说'每天X点推送Y'时使用。"
            "支持 cron 表达式控制推送时间，LLM 根据提示模板生成内容后推送到 QQ。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "任务名称，如 daily-ai-news",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "cron 表达式：分 时 日 月 周。例：每天9点=0 9 * * *，每早8点=0 8 * * *，每周一18点=0 18 * * 1",
                },
                "target_type": {
                    "type": "string",
                    "description": "推送到 private（私聊）或 group（群聊）",
                    "enum": ["private", "group"],
                },
                "target_id": {
                    "type": "string",
                    "description": "QQ号（私聊）或群号（群聊）。当前用户QQ号见系统提示中的 user= 标记",
                },
                "prompt_template": {
                    "type": "string",
                    "description": "给 LLM 的提示词，用于生成推送内容。如'搜索今天AI新闻，3-5条中文总结'",
                },
            },
            "required": ["name", "cron_expr", "target_type", "target_id", "prompt_template"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        try:
            from core.database import SessionLocal, ScheduledTask

            name = str(args.get("name", "")).strip()
            cron_expr = str(args.get("cron_expr", "")).strip()
            target_type = str(args.get("target_type", "private")).strip()
            target_id = str(args.get("target_id", "")).strip()
            prompt_template = str(args.get("prompt_template", "")).strip()

            if not all([name, cron_expr, target_id, prompt_template]):
                return ToolResult(error="缺少必填参数: name, cron_expr, target_id, prompt_template")
            if target_type not in ("private", "group"):
                return ToolResult(error="target_type 必须是 private 或 group")

            db = SessionLocal()
            try:
                task = ScheduledTask(
                    name=name,
                    cron_expr=cron_expr,
                    target_type=target_type,
                    target_id=target_id,
                    prompt_template=prompt_template,
                )
                db.add(task)
                db.commit()
                task_id = task.id
            finally:
                db.close()

            logger.info(f"[schedule_task] Created: {name} cron={cron_expr} -> {target_type}/{target_id}")
            return ToolResult(
                output=f"定时任务已创建 (id={task_id}): {name}\n"
                       f"时间: {cron_expr} | 推送: {target_type}/{target_id}\n"
                       f"提示: {prompt_template}",
                exit_code=0,
            )
        except Exception as e:
            logger.error(f"[schedule_task] Failed: {e}", exc_info=True)
            return ToolResult(error=f"创建任务失败: {str(e)}")
