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

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "persona_update"

    @property
    def description(self) -> str:
        return (
            "刷新当前用户已持久化聊天日志形成的画像。仅当用户明确请求刷新画像时使用；"
            "普通聊天里的新信息由后台画像进化链路异步处理。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")
        session = getattr(context, "session", None) if context is not None else None
        session_extra = getattr(session, "extra", {}) if session is not None else {}
        runtime_context = (
            session_extra.get("nanobot_runtime_context", {})
            if isinstance(session_extra, dict)
            else {}
        )
        user_id = (
            str(runtime_context.get("user_id", "")).strip()
            if isinstance(runtime_context, dict)
            else ""
        )

        if not user_id:
            return ToolResult(error="Persona update authorization failed")

        requested_user_id = str(args.get("user_id", "")).strip()
        if requested_user_id and requested_user_id != user_id:
            return ToolResult(error="Persona update authorization failed")

        if str(args.get("instructions", "")).strip():
            return ToolResult(error="Unsupported persona update arguments")

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
                persona_obj = db.query(Persona).filter(
                    Persona.user_id == user_id,
                    Persona.status == "active",
                ).first()
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
                    {"id": log.id, "role": log.role, "content": log.content, "session_id": log.session_id or "",
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
                        from core.llm_trace_context import llm_trace_scope
                        for attempt in range(3):
                            with llm_trace_scope(source="persona_update"):
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
                await log_analyst.run(log_dicts, provider)

                # 5. 新版状态机：LLM 候选提取 + Python 去重聚类（替代旧 PersonaArchitectAgent）
                logger.info(f"[persona_update] Extracting candidates for user={user_id}")
                try:
                    from core.persona_preprocess import (
                        PersonaStateMachine, build_candidate_extraction_prompt,
                        CANDIDATE_EXTRACTION_SYSTEM_PROMPT, filter_user_messages,
                        format_candidate_logs,
                    )
                    user_log_dicts = filter_user_messages(log_dicts)
                    if not user_log_dicts:
                        return ToolResult(output="没有找到该用户的对话消息", exit_code=0)

                    logs_text = format_candidate_logs(user_log_dicts[-30:])
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
