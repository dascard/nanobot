"""当前用户画像刷新应用服务。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any

from core.tool_contracts.result import ToolServiceResult


logger = logging.getLogger("nanobot.app.persona.update")


class _PersonaUpdateProvider:
    """为画像候选提取提供带有限重试的模型调用。"""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def invoke_raw(
        self,
        query: str,
        system_prompt: str,
        user_id: str,
        model_tier: str = "smart",
        manual_model: str = "",
    ) -> str:
        del user_id
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]
        last_error = ""
        from core.llm_trace_context import llm_trace_scope

        for attempt in range(3):
            with llm_trace_scope(source="persona_update"):
                response = await self.client.chat_completion(
                    messages=messages,
                    model_tier=model_tier,
                    manual_model=manual_model,
                )
            if isinstance(response, dict) and "choices" in response:
                return str(
                    response["choices"][0]["message"]["content"]
                )
            if isinstance(response, Mapping):
                last_error = str(
                    response.get("error", response)
                )[:200]
            else:
                last_error = str(response)[:200]
            logger.warning(
                "[persona_update] LLM attempt %d/3 failed: %s",
                attempt + 1,
                last_error,
            )
            if attempt < 2:
                await asyncio.sleep(2.0 * (attempt + 1))
        return f"API Error after 3 retries: {last_error}"


def _format_change_summary(stats: Mapping[str, Any]) -> str:
    parts: list[str] = []
    if stats.get("created"):
        parts.append(f"新建 {stats['created']} 条")
    if stats.get("merged"):
        parts.append(f"合并 {stats['merged']} 条")
    if stats.get("conflicts"):
        parts.append(f"冲突 {stats['conflicts']} 条")
    return "、".join(parts) if parts else "无变化"


async def execute_persona_update(
    args: Mapping[str, Any],
    *,
    user_id: str,
) -> ToolServiceResult:
    """刷新受信 Runtime actor 自己的画像。"""

    actor_id = str(user_id or "").strip()
    if not actor_id:
        return ToolServiceResult(
            error="Persona update authorization failed"
        )

    requested_user_id = str(args.get("user_id", "")).strip()
    if requested_user_id and requested_user_id != actor_id:
        return ToolServiceResult(
            error="Persona update authorization failed"
        )
    if str(args.get("instructions", "")).strip():
        return ToolServiceResult(
            error="Unsupported persona update arguments"
        )

    try:
        from core.db.models.chat import ChatLog
        from core.db.models.persona import Persona
        from core.legacy_adapter import SQLiteMemory
        from core.model_provider.chat_runtime import (
            RuntimeChatCompletionClient,
        )
        from core.uow import UnitOfWork

        uow = UnitOfWork()
        db = uow.open()
        try:
            persona_obj = (
                db.query(Persona)
                .filter(
                    Persona.user_id == actor_id,
                    Persona.status == "active",
                )
                .first()
            )
            existing_persona = (
                persona_obj.persona_json if persona_obj else "{}"
            )

            logs = (
                db.query(ChatLog)
                .filter(ChatLog.user_id == actor_id)
                .order_by(ChatLog.id.desc())
                .limit(50)
                .all()
            )
            logs.reverse()
            if not logs:
                return ToolServiceResult(
                    output="没有找到该用户的对话日志",
                    exit_code=0,
                )

            log_dicts = [
                {
                    "id": log.id,
                    "role": log.role,
                    "content": log.content,
                    "session_id": log.session_id or "",
                    "created_at": (
                        str(log.created_at) if log.created_at else ""
                    ),
                }
                for log in logs
            ]
            provider = _PersonaUpdateProvider(
                RuntimeChatCompletionClient()
            )

            logger.info(
                "[persona_update] Extracting candidates for user=%s",
                actor_id,
            )
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
                    return ToolServiceResult(
                        output="没有找到该用户的对话消息",
                        exit_code=0,
                    )

                logs_text = format_candidate_logs(user_log_dicts[-30:])
                extraction_prompt = build_candidate_extraction_prompt(
                    existing_persona,
                    logs_text,
                )
                candidate_raw = await provider.invoke_raw(
                    query=extraction_prompt,
                    system_prompt=(
                        get_candidate_extraction_system_prompt()
                    ),
                    user_id=actor_id,
                    model_tier="fast",
                )
                from core.prompt_v2.task_contracts import (
                    TaskOutputContractError,
                    parse_task_output,
                )

                try:
                    parsed = parse_task_output(
                        "memory_extract",
                        candidate_raw,
                    )
                except TaskOutputContractError:
                    candidate_raw = await provider.invoke_raw(
                        query=(
                            f"{extraction_prompt}\n\n"
                            "[输出契约修正] 只输出 JSON object，且必须显式包含 "
                            "candidates 数组。"
                        ),
                        system_prompt=(
                            get_candidate_extraction_system_prompt()
                        ),
                        user_id=actor_id,
                        model_tier="fast",
                    )
                    parsed = parse_task_output(
                        "memory_extract",
                        candidate_raw,
                    )
                candidates = parsed["candidates"]
                if not candidates:
                    return ToolServiceResult(
                        output="未提取到新的画像信息",
                        exit_code=0,
                    )

                state_machine = PersonaStateMachine(db, actor_id)
                stats = state_machine.process_candidates(candidates)
                persona_summary = state_machine.build_summary()
                logger.info(
                    "[persona_update] StateMachine: user=%s, %s",
                    actor_id,
                    stats,
                )
            except Exception as exc:
                logger.error(
                    "[persona_update] StateMachine failed: %s",
                    exc,
                )
                return ToolServiceResult(
                    error=f"画像生成失败: {exc}"
                )

            memory = SQLiteMemory()
            if memory.update_persona(actor_id, persona_summary) is False:
                return ToolServiceResult(error="画像持久化被拒绝")

            count = json.loads(persona_summary).get("count", 0)
            return ToolServiceResult(
                output=(
                    f"画像更新完成 (user={actor_id})\n"
                    f"{_format_change_summary(stats)}\n"
                    f"总事实数: {count}"
                ),
                exit_code=0,
            )
        finally:
            uow.close()
    except Exception as exc:
        logger.error(
            "[persona_update] Failed: %s",
            exc,
            exc_info=True,
        )
        return ToolServiceResult(
            error=f"Persona update failed: {exc}"
        )


__all__ = ["execute_persona_update"]
