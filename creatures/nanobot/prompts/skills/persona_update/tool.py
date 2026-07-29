"""
Persona Update tool — trigger persona generation from within KT conversation.

Lets the agent (or user) manually update the user persona through the
canonical candidate-extraction task and deterministic state machine.
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
        _ = kwargs
        from core.agent_runtime.request_scope import get_current_runtime_context

        runtime_context = get_current_runtime_context()
        user_id = str(
            runtime_context.get("user_id", "") if runtime_context is not None else ""
        ).strip()

        if not user_id:
            return ToolResult(error="Persona update authorization failed")

        requested_user_id = str(args.get("user_id", "")).strip()
        if requested_user_id and requested_user_id != user_id:
            return ToolResult(error="Persona update authorization failed")

        if str(args.get("instructions", "")).strip():
            return ToolResult(error="Unsupported persona update arguments")

        try:
            from core.database import SessionLocal
            from core.db.models.chat import ChatLog
            from core.db.models.persona import Persona
            from core.legacy_adapter import SQLiteMemory
            from core.model_provider.chat_runtime import RuntimeChatCompletionClient

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
                client = RuntimeChatCompletionClient()

                class _ToolProvider:
                    """Provider wrapping NewAPIClient with retry for evolution agents."""
                    def __init__(self, c):
                        self.client = c

                    async def invoke_raw(self, query, system_prompt, user_id, model_tier="smart", manual_model=""):
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
                                    manual_model=manual_model,
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

                # 4. canonical 候选提取 + Python 去重聚类状态机。
                logger.info(f"[persona_update] Extracting candidates for user={user_id}")
                try:
                    from core.persona_preprocess import (
                        PersonaStateMachine,
                        build_candidate_extraction_prompt,
                        filter_user_messages,
                        format_candidate_logs,
                        get_candidate_extraction_system_prompt,
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
                        system_prompt=get_candidate_extraction_system_prompt(),
                        user_id=user_id,
                        model_tier="fast",
                    )
                    from core.prompt_v2.task_contracts import (
                        TaskOutputContractError,
                        parse_task_output,
                    )

                    try:
                        parsed = parse_task_output("memory_extract", candidate_raw)
                    except TaskOutputContractError:
                        candidate_raw = await provider.invoke_raw(
                            query=(
                                f"{extraction_prompt}\n\n"
                                "[输出契约修正] 只输出 JSON object，且必须显式包含 "
                                "candidates 数组。"
                            ),
                            system_prompt=get_candidate_extraction_system_prompt(),
                            user_id=user_id,
                            model_tier="fast",
                        )
                        parsed = parse_task_output("memory_extract", candidate_raw)
                    candidates = parsed["candidates"]
                    if not candidates:
                        return ToolResult(output="未提取到新的画像信息", exit_code=0)

                    sm = PersonaStateMachine(db, user_id)
                    stats = sm.process_candidates(candidates)
                    persona_summary = sm.build_summary()
                    logger.info("[persona_update] StateMachine: user=%s, %s", user_id, stats)
                except Exception as e:
                    logger.error("[persona_update] StateMachine failed: %s", e)
                    return ToolResult(error=f"画像生成失败: {str(e)}")

                # 5. personas(压缩摘要)；facts 已由状态机写入。
                memory = SQLiteMemory()
                if memory.update_persona(user_id, persona_summary) is False:
                    return ToolResult(error="画像持久化被拒绝")

                # 6. Build summary from state machine stats
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
