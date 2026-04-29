"""
Persona Update tool — trigger persona generation from within KT conversation.

Lets the agent (or user) manually update the user persona by running the
full PersonaArchitectAgent pipeline on recent chat logs.
"""

import json
import logging
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

logger = logging.getLogger("nanobot.tool.persona_update")


class PersonaUpdateTool(BaseTool):
    """Update user persona by running the evolution pipeline on recent logs."""

    @property
    def tool_name(self) -> str:
        return "persona_update"

    @property
    def description(self) -> str:
        return (
            "更新用户画像。根据最近的对话记录重新分析用户行为偏好并更新画像 JSON。"
            "当用户说 '更新我的画像' 'update my persona' 或对话中出现值得记录的新信息时使用。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "要更新画像的用户 ID（见系统提示中的 user= 标记）",
                },
                "instructions": {
                    "type": "string",
                    "description": "可选的更新指引。留空则全面更新",
                },
            },
            "required": ["user_id"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        user_id = str(args.get("user_id", "")).strip()
        instructions = str(args.get("instructions", "")).strip()

        if not user_id:
            return ToolResult(error="Missing 'user_id' argument")

        try:
            from core.database import SessionLocal, Persona, ChatLog
            from core.legacy_adapter import (
                PersonaArchitectAgent,
                LogAnalystAgent,
                EvolutionUtils,
                SQLiteMemory,
            )
            from clients.new_api_client import NewAPIClient
            from config import NEW_API_KEY, NEW_API_BASE_URL

            db = SessionLocal()
            try:
                # 1. Read existing persona

                # 1. Read existing persona
                persona_obj = db.query(Persona).filter(Persona.user_id == user_id).first()
                existing_persona = persona_obj.persona_json if persona_obj else "{}"

                # 2. Read recent chat logs (last 50)
                logs = (
                    db.query(ChatLog)
                    .filter(ChatLog.user_id == user_id)
                    .order_by(ChatLog.id.desc())
                    .limit(50)
                    .all()
                )
                logs.reverse()

                if not logs:
                    return ToolResult(output="没有找到该用户的对话日志", exit_code=0)

                log_dicts = [
                    {"role": log.role, "content": log.content, "session_id": log.session_id or ""}
                    for log in logs
                ]

                # 3. Create provider with retry
                client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL)

                class _ToolProvider:
                    """Provider wrapping NewAPIClient with retry for evolution agents."""
                    def __init__(self, c):
                        self.client = c

                    # 画像分析是高复杂度任务，跳过 cost 优先的路由器
                    _PERSONA_MODEL_MAP = {
                        "smart": "deepseek-v4-pro",
                        "reasoning": "deepseek-v4-flash-high",
                        "fast": "deepseek-v4-flash",
                    }

                    async def invoke_raw(self, query, system_prompt, user_id, model_tier="smart", manual_model=""):
                        # manual_model 由调用方指定则用调用方的，否则按 tier 映射
                        target = manual_model or self._PERSONA_MODEL_MAP.get(model_tier, "")
                        messages = [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": query},
                        ]
                        last_error = ""
                        for attempt in range(3):
                            resp = await self.client.chat_completion(
                                messages=messages, model_tier=model_tier,
                                manual_model=target,
                            )
                            if isinstance(resp, dict) and "choices" in resp:
                                return resp["choices"][0]["message"]["content"]
                            last_error = str(resp.get("error", resp))[:200]
                            logger.warning(f"[persona_update] LLM attempt {attempt+1}/3 failed: {last_error}")
                            if attempt < 2:
                                import asyncio as _asyncio
                                await _asyncio.sleep(2.0 * (attempt + 1))
                        return f"API Error after 3 retries: {last_error}"

                provider = _ToolProvider(client)

                # 4. Run analysis pipeline
                logger.info(f"[persona_update] Analyzing {len(log_dicts)} logs for user={user_id}")
                log_analyst = LogAnalystAgent()
                log_summary = await log_analyst.run(log_dicts, provider)

                # 5. Merge + critique
                logger.info(f"[persona_update] Updating persona for user={user_id}")
                if instructions:
                    log_summary["instructions"] = instructions

                persona_architect = PersonaArchitectAgent()
                new_persona = await persona_architect.run(existing_persona, log_summary, provider)

                if isinstance(new_persona, dict) and new_persona.get("parse_error"):
                    return ToolResult(error=f"Persona generation failed: {new_persona.get('raw', '')[:300]}")

                # 6. Save to DB
                new_json = json.dumps(new_persona, ensure_ascii=False)
                memory = SQLiteMemory()
                memory.update_persona_and_prompt(user_id, new_json, "")

                # 7. Build readable summary of what changed
                old_data = json.loads(existing_persona) if existing_persona and existing_persona != "{}" else {}
                changes = []
                if isinstance(new_persona, dict):
                    if new_persona.get("summary") != old_data.get("summary"):
                        changes.append(f"画像摘要: {new_persona.get('summary', 'N/A')}")
                    new_traits = set(new_persona.get("traits") or [])
                    old_traits = set(old_data.get("traits") or [])
                    added = new_traits - old_traits
                    removed = old_traits - new_traits
                    if added:
                        changes.append(f"新增特质: {', '.join(added)}")
                    if removed:
                        changes.append(f"移除特质: {', '.join(removed)}")
                    new_style = new_persona.get("response_style") or new_persona.get("communication_style") or ""
                    old_style = old_data.get("response_style") or old_data.get("communication_style") or ""
                    if new_style != old_style:
                        changes.append(f"回复风格: {new_style[:120]}")

                summary = "\n".join(changes) if changes else "画像已更新（无显著变化）"
                return ToolResult(
                    output=f"画像更新完成 (user={user_id})\n\n{summary}",
                    exit_code=0,
                )
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[persona_update] Failed: {e}", exc_info=True)
            return ToolResult(error=f"Persona update failed: {str(e)}")
