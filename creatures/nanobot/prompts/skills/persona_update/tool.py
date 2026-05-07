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
                    "description": "要更新画像的用户 ID，优先使用 <runtime_context> 中的 user_id",
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
                    {"role": log.role, "content": log.content, "session_id": log.session_id or "",
                     "created_at": str(log.created_at) if log.created_at else ""}
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

                # 5. 新版状态机：LLM 候选提取 + Python 去重聚类（替代旧 PersonaArchitectAgent）
                logger.info(f"[persona_update] Extracting candidates for user={user_id}")
                try:
                    from core.persona_preprocess import (
                        PersonaStateMachine, build_candidate_extraction_prompt,
                        CANDIDATE_EXTRACTION_SYSTEM_PROMPT, filter_user_messages,
                    )
                    user_log_dicts = filter_user_messages(log_dicts)
                    if not user_log_dicts:
                        return ToolResult(output="没有找到该用户的对话消息", exit_code=0)

                    logs_text = "\n".join(
                        f"[{m.get('created_at', '')}] user: {m.get('content', '')}"
                        for m in user_log_dicts[-30:]
                    )
                    extraction_prompt = build_candidate_extraction_prompt(
                        existing_persona, logs_text
                    )
                    candidate_raw = await provider.invoke_raw(
                        query=extraction_prompt,
                        system_prompt=CANDIDATE_EXTRACTION_SYSTEM_PROMPT,
                        user_id=user_id,
                        model_tier="fast",
                    )
                    parsed = EvolutionUtils.json_repair(candidate_raw)
                    candidates = (
                        parsed.get("candidates", [])
                        if isinstance(parsed, dict) else []
                    )
                    if not candidates:
                        return ToolResult(output="未提取到新的画像信息", exit_code=0)

                    sm = PersonaStateMachine(db, user_id)
                    stats = sm.process_candidates(candidates)
                    persona_summary = sm.build_summary()
                    logger.info("[persona_update] StateMachine: user=%s, %s", user_id, stats)
                except Exception as e:
                    logger.error("[persona_update] StateMachine failed: %s", e)
                    return ToolResult(error=f"画像生成失败: {str(e)}")

                # 6. Save to DB: personas(压缩摘要) + persona_facts/behaviors 已由状态机写入
                memory = SQLiteMemory()
                memory.update_persona_and_prompt(user_id, persona_summary, "")

                # 7. Build summary from state machine stats
                parts = []
                if stats.get("created"):
                    parts.append(f"新建 {stats['created']} 条")
                if stats.get("merged"):
                    parts.append(f"合并 {stats['merged']} 条")
                if stats.get("conflicts"):
                    parts.append(f"冲突 {stats['conflicts']} 条")
                change_summary = "、".join(parts) if parts else "无变化"
                return ToolResult(
                    output=f"画像更新完成 (user={user_id})\n"
                           f"{change_summary}\n"
                           f"总事实数: {json.loads(persona_summary).get('count', 0)}",
                    exit_code=0,
                )
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[persona_update] Failed: {e}", exc_info=True)
            return ToolResult(error=f"Persona update failed: {str(e)}")
