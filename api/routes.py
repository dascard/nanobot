"""
FastAPI 路由模块。
定义所有 HTTP 端点，含 Bearer Token 认证中间件。
"""
import logging
import json
import asyncio
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from typing import Any

from config import (
    NANOBOT_API_TOKEN as NANOBOT_API_TOKEN,
    EVOLUTION_THRESHOLD,
)
from core.database import (
    get_db,
    run_session_phase_async,
    session_factory_from_session,
    User,
    Persona,
    ChatLog,
)
from core.context_builder import (
    sanitize_prompt_text as _sanitize_prompt_text,  # noqa: F401 - 旧 api.routes 导入路径兼容
    estimate_tokens as _estimate_tokens,
    build_chat_context as _build_chat_context,
    build_session_memory as _build_session_memory,  # noqa: F401 - 旧 api.routes 导入路径兼容
)
from core.evolution import evolution_task
from core.inbound_claim_lifecycle import InboundClaimOwner
from core.inbound_idempotency import (
    ClaimDecisionKind,
    acquire_inbound_claim,
    normalize_inbound_claim_key,
)
from core.identity import is_super_user_id
from core.legacy_adapter import SQLiteMemory  # Keep for evolution; UnifiedProvider/Controller replaced by KT
from core.time_utils import db_now_naive
from core import user_block_rules
from core.agent_runtime.gateway import get_agent_gateway as get_bridge
from clients.classifier_client import get_guardrail
from app.group_ingress import helpers as group_ingress_helpers
from api import (
    chat_content_helpers,
    chat_guardrail_facade,
    chat_media_precache,
    chat_non_streaming_result,
    chat_pre_bridge_decision,
    chat_pre_bridge_route_result,
    chat_persistence,
    chat_persona_context,
    chat_persona_lookup,
    chat_private_buffer,
    chat_push_envelope,
    chat_request_contract,
    chat_response_contract,
    chat_recovery,
    chat_route_runner,
    chat_runtime_facade,
    chat_runtime_route_context,
    chat_streaming_helpers,
)
from api.common_auth import verify_token
from api.evolution_routes import (
    EvolutionTriggerRequest as EvolutionTriggerRequest,
    router as evolution_router,
    trigger_evolution as trigger_evolution,
)
from api.history_log_routes import (
    AmbientLogRequest as AmbientLogRequest,
    LogRequest as LogRequest,
    compact_history as compact_history,
    get_context as get_context,
    get_history_summary as get_history_summary,
    mark_clear as mark_clear,
    router as history_log_router,
    search_history_logs as search_history_logs,
    submit_ambient_log as submit_ambient_log,
    submit_log as submit_log,
)
from api.memory_routes import (
    MemoryDigestRunRequest as MemoryDigestRunRequest,
    _build_expand_chain as _build_expand_chain,
    _calc_recall_confidence as _calc_recall_confidence,
    _short_text as _short_text,
    _validate_memory_digest_date_filters as _validate_memory_digest_date_filters,
    get_memory_digests as get_memory_digests,
    recall_memory as recall_memory,
    router as memory_router,
    run_memory_digests as run_memory_digests,
)
from api.model_routes import (
    ModelSyncRequest as ModelSyncRequest,
    list_models as list_models,
    router as model_router,
    sync_models as sync_models,
)
from api.task_routes import (
    ScheduledTaskCreate as ScheduledTaskCreate,
    create_scheduled_task as create_scheduled_task,
    delete_scheduled_task as delete_scheduled_task,
    list_scheduled_tasks as list_scheduled_tasks,
    router as task_router,
    run_scheduled_task_now as run_scheduled_task_now,
    toggle_scheduled_task as toggle_scheduled_task,
    update_scheduled_task as update_scheduled_task,
)
from api.sticker_media_routes import (
    StickerRegisterRequest as StickerRegisterRequest,
    disable_sticker_endpoint as disable_sticker_endpoint,
    public_generated_image as public_generated_image,
    public_sticker_image as public_sticker_image,
    register_sticker_endpoint as register_sticker_endpoint,
    router as sticker_media_router,
    search_sticker_endpoint as search_sticker_endpoint,
)
from api.agent_step_routes import (
    AgentStepRequest as AgentStepRequest,
    agent_step_event_payload as agent_step_event_payload,
    agent_step_sse_data as agent_step_sse_data,
    chat_step as chat_step,
    render_markdown as render_markdown,
    router as agent_step_router,
    run_agent_step as run_agent_step,
    run_agent_step_stream as run_agent_step_stream,
)
from api.asset_routes import router as asset_router
from api.group_message_routes import (
    GroupMessageRequest as GroupMessageRequest,
    OneBotMessageSegmentPayload as OneBotMessageSegmentPayload,
    group_message as group_message,
    router as group_message_router,
)
from api.group_utility_routes import (
    GroupTimingRequest as GroupTimingRequest,
    GroupTimingTimerRequest as GroupTimingTimerRequest,
    UpdateGroupNameRequest as UpdateGroupNameRequest,
    _build_group_timing_context as _build_group_timing_context,
    group_timing_deprecated as group_timing_deprecated,
    group_timing_timer as group_timing_timer,
    router as group_utility_router,
    update_group_name as update_group_name,
)

logger = logging.getLogger("nanobot.routes")
router = APIRouter(prefix="/api/v1")
EMPTY_ASSISTANT_PLACEHOLDER = "（无回复内容）"
SAFE_STREAM_ERROR_MESSAGE = "系统暂时不可用，请稍后再试"
CHAT_STREAM_QUEUE_MAXSIZE = 128


def _safe_log(method_name: str, message: str, *args: Any, **kwargs: Any) -> None:
    try:
        log_method = getattr(logger, method_name)
        log_method(message, *args, **kwargs)
    except BaseException:
        pass


async def _best_effort_fail_chat_claim(
    owner: InboundClaimOwner | None,
    error: BaseException,
) -> None:
    if owner is None:
        return
    try:
        await owner.fail(error)
    except BaseException as cleanup_error:
        _safe_log(
            "error",
            "[/chat] Claim cleanup failed: primary=%r cleanup=%r",
            error,
            cleanup_error,
        )


def _normalize_chat_stream_event(event: Any) -> dict[str, Any] | None:
    return chat_response_contract.normalize_chat_stream_event(event)


def _chat_sse_data(event: dict[str, Any]) -> str:
    return chat_response_contract.chat_sse_data(event)


def _stream_error_event() -> dict[str, str]:
    return chat_response_contract.stream_error_event(SAFE_STREAM_ERROR_MESSAGE)


# 私聊缓冲：基础 5 秒窗口；只要有文件附件就延长到 10 秒
_private_buffers: dict[str, dict] = {}
_private_lock = asyncio.Lock()
_private_buffer_store = chat_private_buffer.PrivateBufferStore(
    _private_buffers,
    _private_lock,
)
MAX_BUFFERED_MESSAGES = 10  # 单用户 5s 窗口内最多收集条数
PRIVATE_BUFFER_WINDOW_SECONDS = 5.0
PRIVATE_BUFFER_WINDOW_WITH_FILES_SECONDS = 10.0
PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS = 900.0

# Prompt budget caps to avoid hidden context blow-up.
MAX_QUERY_CHARS = 2000
MAX_PERSONA_CHARS = 1600
MAX_MEMORY_PER_MSG_CHARS = 300
MAX_MEMORY_TOTAL_CHARS = 4000

# --- Legacy Memory (for evolution endpoints) ---
memory = None

def init_legacy_memory():
    """Initialize SQLiteMemory for evolution endpoints. Called from server.py lifespan."""
    global memory
    memory = SQLiteMemory()
    logger.info("Legacy SQLiteMemory initialized for evolution endpoints")

# 旧函数已移至 core/context_builder.py，此处仅保留向后兼容 re-export


def _format_persona_for_prompt(persona_data: dict, max_chars: int = MAX_PERSONA_CHARS) -> str:
    return chat_persona_context.format_persona_for_prompt(persona_data, max_chars=max_chars)


def _resolve_chat_persona_snapshot(db: Session, user_id: str) -> chat_persona_lookup.ChatPersonaSnapshot:
    """旧管理/兼容入口；在线聊天主链路只使用 PersonaInjectionService。"""

    return chat_persona_lookup.resolve_chat_persona_snapshot(
        db, user_id, persona_model=Persona, format_persona=_format_persona_for_prompt
    )



ChatProxyRequest = chat_request_contract.ChatProxyRequest

def _safe_meta(meta_json: str) -> dict:
    return chat_persistence.safe_meta(meta_json)


def _clone_chat_request(req: ChatProxyRequest, **updates) -> ChatProxyRequest:
    return chat_request_contract.clone_chat_request(req, **updates)


def _private_buffer_config() -> chat_private_buffer.PrivateBufferConfig:
    return chat_private_buffer.PrivateBufferConfig(
        max_messages=MAX_BUFFERED_MESSAGES, window_seconds=PRIVATE_BUFFER_WINDOW_SECONDS,
        window_with_files_seconds=PRIVATE_BUFFER_WINDOW_WITH_FILES_SECONDS,
        follower_timeout_seconds=PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS,
    )


def _join_buffered_messages(messages: list[str]) -> str:
    return chat_private_buffer.join_buffered_messages(messages)


def _normalize_files(files: list[str] | None) -> list[str]:
    return chat_content_helpers.normalize_files(files)


def _schedule_image_precache(
    background_tasks: BackgroundTasks | None,
    files: list[str] | None,
    *,
    source_type: str,
    source_name_prefix: str,
) -> None:
    return chat_media_precache.schedule_image_precache(
        background_tasks, files, source_type=source_type,
        source_name_prefix=source_name_prefix, normalize_files=_normalize_files,
    )


def _get_group_talk_value(session_id: str) -> float:
    """获取群聊 talk_value，用于控制发言频率。"""
    try:
        from core.expression_memory import get_stream_config, normalize_chat_stream_id
        raw = session_id.removeprefix("group_")
        cfg = get_stream_config(normalize_chat_stream_id(raw, chat_type="group", platform="qq"))
        return float(cfg.get("talk_value", 0.5))
    except Exception:
        return 0.5


def _check_user_blocked(db, user_id: str, target_type: str = "private", group_id: str = "") -> bool:
    """检查用户是否被屏蔽——命中规则时返回 True。"""
    try:
        return user_block_rules.is_user_blocked(db, user_id, target_type=target_type, group_id=group_id)
    except Exception as e:
        logger.warning("[BlockRule] check failed user=%s group=%s: %s", user_id, group_id, e)
    return False


def _merge_buffered_files(existing: list[str], incoming: list[str] | None) -> list[str]:
    return chat_private_buffer.merge_buffered_files(existing, _normalize_files(incoming))


def _private_buffer_window_seconds(files: list[str] | None) -> float:
    return chat_private_buffer.private_buffer_window_seconds(_normalize_files(files), _private_buffer_config())


async def _wait_private_buffer_deadline(user_id: str) -> bool:
    return await _private_buffer_store.wait_until_deadline(user_id, now=_time.time, sleep=asyncio.sleep)


def _build_guardrail_input(query: str, files: list[str] | None) -> str:
    return chat_content_helpers.build_guardrail_input(query, files)


def _detect_guardrail(guardrail, message: str, *, allow_passthrough: bool = False) -> dict:
    return chat_guardrail_facade.detect_guardrail(guardrail, message, allow_passthrough=allow_passthrough)


def _detect_guardrail_for_pre_bridge(guardrail: Any, message: str, allow_passthrough: bool) -> dict[str, Any]:
    return _detect_guardrail(guardrail, message, allow_passthrough=allow_passthrough)


def _build_multimodal_user_input_text(query: str, files: list[str] | None, *, max_chars: int = 0) -> str:
    return chat_content_helpers.build_multimodal_user_input_text(query, files, max_chars=max_chars)


def _build_file_archive_summary(files: list[str] | None, *, include_refs: bool) -> str:
    return chat_content_helpers.build_file_archive_summary(files, include_refs=include_refs)


def _build_chatlog_user_content(query: str, files: list[str] | None) -> str:
    return chat_content_helpers.build_chatlog_user_content(query, files)


def _build_conversation_user_content(query: str, files: list[str] | None) -> str:
    return chat_content_helpers.build_conversation_user_content(query, files)


def _resolve_push_target_id(req: ChatProxyRequest, is_group: bool) -> str:
    return chat_request_contract.resolve_push_target_id(req, is_group)


def _extract_group_id_from_chat_request(req: ChatProxyRequest) -> str:
    return chat_request_contract.extract_group_id_from_chat_request(req)


def _chat_request_platform(req: ChatProxyRequest) -> str:
    return chat_request_contract.chat_request_platform(req)


def _chat_request_type(req: ChatProxyRequest) -> str:
    return chat_request_contract.chat_request_type(req)


def _normalize_request_client_meta(req: Any, *, expected_chat_type: str) -> dict[str, Any]:
    return chat_request_contract.normalize_request_client_meta(
        req,
        expected_chat_type=expected_chat_type,
    )


def _split_chat_answer_chunks(answer: str) -> list[str]:
    return chat_response_contract.split_chat_answer_chunks(answer)


def _chat_response_payload(
    req: ChatProxyRequest,
    *, status: str, answer: str = "", reply_meta: dict | None = None, platform: str = "",
    chat_type: str = "", unprocessed_logs: int | None = None, reason: str = "",
    source: str = "", intent: str = "", guardrail_status: str | None = None,
    include_answer_chunks: bool = False, extra_meta: dict | None = None,
) -> dict[str, Any]:
    return chat_response_contract.chat_response_payload(
        req, status=status, answer=answer, reply_meta=reply_meta, platform=platform,
        chat_type=chat_type, unprocessed_logs=unprocessed_logs, reason=reason,
        source=source, intent=intent, guardrail_status=guardrail_status,
        include_answer_chunks=include_answer_chunks, extra_meta=extra_meta,
    )


def _expand_chat_transport_answer(answer: str) -> str:
    return chat_push_envelope.expand_chat_transport_answer(answer)


def _build_chat_push_envelope(
    req: ChatProxyRequest,
    **kwargs: Any,
) -> chat_push_envelope.ChatPushEnvelope:
    return chat_push_envelope.build_chat_push_envelope(req, **kwargs)


def _completed_chat_route_response(
    req: ChatProxyRequest,
    completed_response: Any,
) -> dict[str, Any] | StreamingResponse:
    answer = completed_response.reply
    if completed_response.outcome == "respond":
        try:
            answer = _expand_chat_transport_answer(completed_response.reply)
        except Exception:
            answer = completed_response.reply
    payload = chat_response_contract.completed_chat_response_payload(
        req,
        completed_response,
        answer_override=answer,
    )
    if req.stream and completed_response.outcome == "respond":
        return StreamingResponse(
            iter((_chat_sse_data(payload),)),
            media_type="text/event-stream",
        )
    return payload


def _is_guardrail_superuser(user_id: str) -> bool:
    return is_super_user_id(user_id)


async def _finalize_private_buffer(user_id: str, answer: str | None = None, *, clear_window: bool = True) -> None:
    await _private_buffer_store.finalize(user_id, answer, clear_window=clear_window)


def _private_prompt_audit_failure_meta() -> dict:
    return chat_request_contract.private_prompt_audit_failure_meta()


def _private_timing_meta(decision: Any | None) -> dict[str, Any] | None:
    return chat_request_contract.private_timing_meta(decision)


def get_private_gate() -> Any:
    from core.private_timing import get_private_gate as _core_get_private_gate

    return _core_get_private_gate()


def get_effort_constraint(effort: str | None) -> str:
    from core.private_timing import get_effort_constraint as _core_get_effort_constraint

    return _core_get_effort_constraint(effort)


def _get_casual_reply_for_pre_bridge(intent: str) -> str:
    from core.reply_templates import get_casual_reply as _core_get_casual_reply

    return _core_get_casual_reply(intent)


def _chat_pre_bridge_services() -> chat_pre_bridge_decision.ChatPreBridgeServices:
    return chat_pre_bridge_decision.ChatPreBridgeServices(
        private_buffer_store=_private_buffer_store,
        private_buffer_config=_private_buffer_config,
        private_buffer_follower_timeout_seconds=PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS,
        now=_time.time,
        sleep=asyncio.sleep,
        wait_private_buffer_deadline=_wait_private_buffer_deadline,
        finalize_private_buffer=_finalize_private_buffer,
        normalize_files=_normalize_files,
        join_buffered_messages=_join_buffered_messages,
        build_guardrail_input=_build_guardrail_input,
        get_guardrail=get_guardrail,
        detect_guardrail=_detect_guardrail_for_pre_bridge,
        guardrail_status_from_result=chat_guardrail_facade.guardrail_status_from_result,
        is_guardrail_superuser=_is_guardrail_superuser,
        get_private_gate=get_private_gate,
        get_casual_reply=_get_casual_reply_for_pre_bridge,
        private_timing_meta=_private_timing_meta,
        logger=logger,
    )


async def _resolve_chat_pre_bridge_decision(
    req: ChatProxyRequest,
    *,
    is_group: bool,
    is_superuser: bool,
) -> chat_pre_bridge_decision.ChatPreBridgeEarlyReturn | chat_pre_bridge_decision.ChatPreBridgeContinue:
    return await chat_pre_bridge_decision.resolve_chat_pre_bridge_decision(
        req,
        is_group=is_group,
        is_superuser=is_superuser,
        services=_chat_pre_bridge_services(),
    )


def _persist_chat_turn(
    db: Session,
    req: ChatProxyRequest,
    answer: str,
    guardrail_status: str | None = None,
    *,
    assistant_meta: dict | None = None,
    assistant_processed: int | None = None,
    timing_meta: dict | None = None,
) -> int:
    """Persist a chat turn to both ChatLog (evolution) and ConversationTurn (context)."""
    return chat_persistence.persist_chat_turn(
        db,
        _chat_persistence_input(req),
        answer,
        guardrail_status,
        assistant_meta=assistant_meta,
        assistant_processed=assistant_processed,
        timing_meta=timing_meta,
    )


def _chat_persistence_input(req: ChatProxyRequest) -> chat_persistence.ChatTurnPersistenceInput:
    return chat_persistence.ChatTurnPersistenceInput(
        user_id=req.user_id,
        session_id=req.session_id,
        query=req.query,
        files=req.files,
        sender_name=req.sender_name,
        session_name=req.session_name,
        message_id=req.message_id,
        source_message_ids=req.source_message_ids,
        client_meta=req.client_meta,
        chat_type=_chat_request_type(req),
        event_time=str(
            (
                req.client_meta
                if isinstance(req.client_meta, dict)
                else {}
            ).get("event_time")
            or ""
        ),
    )


def _persist_claimed_chat_turn(
    db: Session,
    req: ChatProxyRequest,
    answer: str,
    guardrail_status: str | None = None,
    **kwargs: Any,
) -> chat_persistence.ClaimedChatTurnPersistenceResult:
    return chat_persistence.persist_claimed_chat_turn(
        db,
        _chat_persistence_input(req),
        answer,
        guardrail_status,
        **kwargs,
    )


def _chat_pre_bridge_route_callbacks(
    db: Session,
    *,
    claim_key: Any | None = None,
    request_sha256: str = "",
    offload_persistence: bool = False,
    session_factory: Callable[[], Session] | None = None,
) -> chat_pre_bridge_route_result.ChatPreBridgeRouteCallbacks:
    if offload_persistence:
        async def persist_chat_turn(
            req: ChatProxyRequest,
            answer: str,
            guardrail_status: str | None = None,
            **kwargs: Any,
        ) -> int:
            return await run_session_phase_async(
                lambda phase_db: _persist_chat_turn(
                    phase_db,
                    req,
                    answer,
                    guardrail_status=guardrail_status,
                    **kwargs,
                ),
                session_factory=session_factory,
            )

        async def persist_claimed_response(
            req: ChatProxyRequest,
            completion: Any,
            *,
            persist_answer: str | None,
            guardrail_status: str | None,
            timing_meta: dict[str, Any] | None,
        ) -> Any:
            if claim_key is None or not request_sha256:
                raise RuntimeError("claimed pre-bridge 持久化缺少 claim identity")

            def operation(phase_db: Session) -> Any:
                if persist_answer is None:
                    return chat_persistence.persist_private_claim_completion(
                        phase_db,
                        _chat_persistence_input(req),
                        key=claim_key,
                        request_sha256=request_sha256,
                        completion=completion,
                    )
                result = _persist_claimed_chat_turn(
                    phase_db,
                    req,
                    persist_answer,
                    guardrail_status,
                    key=claim_key,
                    request_sha256=request_sha256,
                    completion=completion,
                    timing_meta=timing_meta,
                )
                return result.completion

            return await run_session_phase_async(
                operation,
                session_factory=session_factory,
            )

        return chat_pre_bridge_route_result.ChatPreBridgeRouteCallbacks(
            clone_chat_request=_clone_chat_request,
            persist_chat_turn=persist_chat_turn,
            chat_response_payload=_chat_response_payload,
            finalize_private_buffer=_finalize_private_buffer,
            persist_claimed_response=(
                persist_claimed_response if claim_key is not None else None
            ),
        )

    def persist_chat_turn(
        req: ChatProxyRequest,
        answer: str,
        guardrail_status: str | None = None,
        **kwargs: Any,
    ) -> int:
        return _persist_chat_turn(db, req, answer, guardrail_status=guardrail_status, **kwargs)

    def persist_claimed_response(
        req: ChatProxyRequest,
        completion: Any,
        *,
        persist_answer: str | None,
        guardrail_status: str | None,
        timing_meta: dict[str, Any] | None,
    ) -> Any:
        if claim_key is None or not request_sha256:
            raise RuntimeError("claimed pre-bridge 持久化缺少 claim identity")
        if persist_answer is None:
            return chat_persistence.persist_private_claim_completion(
                db,
                _chat_persistence_input(req),
                key=claim_key,
                request_sha256=request_sha256,
                completion=completion,
            )
        result = _persist_claimed_chat_turn(
            db,
            req,
            persist_answer,
            guardrail_status,
            key=claim_key,
            request_sha256=request_sha256,
            completion=completion,
            timing_meta=timing_meta,
        )
        return result.completion

    return chat_pre_bridge_route_result.ChatPreBridgeRouteCallbacks(
        clone_chat_request=_clone_chat_request,
        persist_chat_turn=persist_chat_turn,
        chat_response_payload=_chat_response_payload,
        finalize_private_buffer=_finalize_private_buffer,
        persist_claimed_response=(
            persist_claimed_response if claim_key is not None else None
        ),
    )


async def _resolve_pre_bridge_route_result(
    db: Session,
    req: ChatProxyRequest,
    pre_bridge: Any,
    *,
    claim_key: Any | None = None,
    request_sha256: str = "",
    session_factory: Callable[[], Session] | None = None,
) -> Any:
    return await chat_pre_bridge_route_result.resolve_pre_bridge_route_result(
        req,
        pre_bridge,
        callbacks=_chat_pre_bridge_route_callbacks(
            db,
            claim_key=claim_key,
            request_sha256=request_sha256,
            offload_persistence=True,
            session_factory=session_factory,
        ),
    )


def _build_persona_injection_context(
    db: Session,
    *,
    user_id: str,
    current_user_input: str,
    recent_messages: list[dict[str, str]],
) -> Any:
    from app.persona.injection_service import PersonaInjectionResult, PersonaInjectionService
    from core.settings_service import settings

    if not settings.get_bool("persona.injection_enabled", False):
        return PersonaInjectionResult(debug={
            "persona_injected": False,
            "persona_fact_ids": [],
            "persona_skipped": [{"reason": "persona_injection_disabled"}],
            "persona_context_chars": 0,
            "persona_context": "",
            "score_components": {},
            "disabled_reason": "persona_injection_disabled",
        })

    return PersonaInjectionService(db).build_context(
        user_id=user_id,
        current_user_input=current_user_input,
        recent_messages=recent_messages,
    )


def _chat_runtime_route_services(db: Session) -> chat_runtime_route_context.ChatRuntimeRouteServices:
    def build_persona_context(**kwargs: Any) -> Any:
        return _build_persona_injection_context(db, **kwargs)

    return chat_runtime_route_context.ChatRuntimeRouteServices(
        build_multimodal_user_input_text=_build_multimodal_user_input_text,
        max_query_chars=MAX_QUERY_CHARS,
        estimate_tokens=_estimate_tokens,
        get_effort_constraint=get_effort_constraint,
        chat_request_platform=_chat_request_platform,
        build_runtime_payload=chat_runtime_facade.build_chat_runtime_payload,
        build_persona_context=build_persona_context,
        logger=logger,
    )


def _build_chat_runtime_route_context(
    runtime_input: chat_runtime_route_context.ChatRuntimeRouteInput,
    *,
    services: chat_runtime_route_context.ChatRuntimeRouteServices,
) -> chat_runtime_route_context.ChatRuntimeRouteContext:
    return chat_runtime_route_context.build_chat_runtime_route_context(
        runtime_input,
        services=services,
    )


def _chat_route_runner_callbacks(
    background_tasks: BackgroundTasks,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> chat_route_runner.ChatRouteRunnerCallbacks:
    from core.daily_digest import push_envelope_to_qq

    async def persist_chat_turn(
        _db: Any,
        req: ChatProxyRequest,
        answer: str,
        guardrail_status: str | None = None,
        **kwargs: Any,
    ) -> int:
        return await run_session_phase_async(
            lambda phase_db: _persist_chat_turn(
                phase_db,
                req,
                answer,
                guardrail_status,
                **kwargs,
            ),
            session_factory=session_factory,
        )

    async def persist_claimed_chat_turn(
        _db: Any,
        req: ChatProxyRequest,
        answer: str,
        guardrail_status: str | None = None,
        **kwargs: Any,
    ) -> Any:
        return await run_session_phase_async(
            lambda phase_db: _persist_claimed_chat_turn(
                phase_db,
                req,
                answer,
                guardrail_status,
                **kwargs,
            ),
            session_factory=session_factory,
        )

    return chat_route_runner.ChatRouteRunnerCallbacks(
        call_bridge_non_streaming=getattr(chat_runtime_facade, "call_bridge_non_streaming"),
        finalize_private_buffer=_finalize_private_buffer,
        persist_chat_turn=persist_chat_turn,
        pop_bridge_reply_meta=_pop_bridge_reply_meta,
        private_prompt_audit_failure_meta=_private_prompt_audit_failure_meta,
        expand_chat_transport_answer=_expand_chat_transport_answer,
        build_chat_push_envelope=_build_chat_push_envelope,
        push_envelope_to_qq=push_envelope_to_qq,
        chat_response_payload=_chat_response_payload,
        chat_sse_data=_chat_sse_data,
        stream_error_event=_stream_error_event,
        drain_stream_queue_until_task_done=chat_streaming_helpers.drain_stream_queue_until_task_done,
        finalize_non_streaming_chat_result=chat_non_streaming_result.finalize_non_streaming_chat_result,
        add_background_task=background_tasks.add_task,
        evolution_task=evolution_task,
        persist_claimed_chat_turn=persist_claimed_chat_turn,
    )


def _chat_route_runner_context(
    *,
    req: ChatProxyRequest,
    persist_req: ChatProxyRequest,
    bridge: Any,
    enriched_query: str,
    bridge_meta: dict[str, Any],
    platform: str,
    guardrail_status: str | None,
    private_timing_meta: dict[str, Any] | None,
    background_tasks: BackgroundTasks,
    session_factory: Callable[[], Session] | None = None,
    claim_owner: InboundClaimOwner | None = None,
    claim_key: Any | None = None,
    request_sha256: str = "",
    message_contract: Any | None = None,
) -> chat_route_runner.ChatRouteRunnerContext:
    return chat_route_runner.ChatRouteRunnerContext(
        req=req,
        persist_req=persist_req,
        bridge=bridge,
        enriched_query=enriched_query,
        bridge_meta=bridge_meta,
        platform=platform,
        guardrail_status=guardrail_status,
        private_timing_meta=private_timing_meta,
        queue_maxsize=CHAT_STREAM_QUEUE_MAXSIZE,
        empty_assistant_placeholder=EMPTY_ASSISTANT_PLACEHOLDER,
        safe_error_message=SAFE_STREAM_ERROR_MESSAGE,
        evolution_threshold=EVOLUTION_THRESHOLD,
        callbacks=_chat_route_runner_callbacks(
            background_tasks,
            session_factory=session_factory,
        ),
        claim_owner=claim_owner,
        claim_key=claim_key,
        request_sha256=request_sha256,
        message_contract=message_contract,
    )


# 旧群消息 helper 已收敛到 app.group_ingress.helpers；此处保留旧私有导入路径兼容。
_normalize_onebot_segments = group_ingress_helpers.normalize_onebot_segments
_extract_mentions_from_segments = group_ingress_helpers.extract_mentions_from_segments
_normalize_group_mentions = group_ingress_helpers.normalize_group_mentions
_normalize_group_reply_to = group_ingress_helpers.normalize_group_reply_to
_derive_group_direction = group_ingress_helpers.derive_group_direction
_detect_group_bot_sender = group_ingress_helpers.detect_group_bot_sender
_build_group_message_meta = group_ingress_helpers.build_group_message_meta
_safe_group_client_meta = group_ingress_helpers.safe_group_client_meta
_group_sticker_payloads = group_ingress_helpers.group_sticker_payloads
_render_segments_to_text = group_ingress_helpers.render_segments_to_text
_build_group_message_text = group_ingress_helpers.build_group_message_text
_register_group_stickers_from_message = group_ingress_helpers.register_group_stickers_from_message
_annotate_group_timing_event = group_ingress_helpers.annotate_group_timing_event
_normalize_reply_for_duplicate = group_ingress_helpers.normalize_reply_for_duplicate
_pop_bridge_reply_meta = group_ingress_helpers.pop_bridge_reply_meta
_derive_group_agent_result = group_ingress_helpers.derive_group_agent_result
_find_recent_duplicate_group_reply = group_ingress_helpers.find_recent_duplicate_group_reply
_log_group_no_reply = group_ingress_helpers.log_group_no_reply
_persist_group_bridge_reply = group_ingress_helpers.persist_group_bridge_reply
_derive_group_trigger_reason = group_ingress_helpers.derive_group_trigger_reason


router.include_router(group_message_router)
router.include_router(group_utility_router)
router.include_router(agent_step_router)


@dataclass(frozen=True)
class _ChatDatabasePreparation:
    blocked_completion: Any | None
    memory_header: str
    history_messages: list[dict[str, str]]
    context_debug: dict[str, Any]


def _prepare_chat_database_phase(
    db: Session,
    req: ChatProxyRequest,
    *,
    is_group: bool,
    private_claim_key: Any | None,
    private_request_sha256: str,
) -> _ChatDatabasePreparation:
    """完成 Bridge 前的数据库读写；调用方必须把整个阶段放入工作线程。"""

    event_time = db_now_naive()
    if private_claim_key is not None:
        journal = (
            db.query(ChatLog)
            .filter(
                ChatLog.session_id == req.session_id,
                ChatLog.message_id == req.message_id,
                ChatLog.role == "user",
            )
            .order_by(ChatLog.id.asc())
            .first()
        )
        if journal is not None and journal.created_at is not None:
            event_time = journal.created_at
    client_meta = (
        dict(req.client_meta)
        if isinstance(req.client_meta, dict)
        else {}
    )
    client_meta["event_time"] = event_time.strftime("%Y-%m-%d %H:%M:%S CST")
    req.client_meta = client_meta

    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        db.add(User(id=req.user_id, name=(req.sender_name or "")))
        db.commit()
    elif req.sender_name and user.name != req.sender_name:
        user.name = req.sender_name
        db.commit()

    # 屏蔽规则按会话类型匹配:群会话必须用 target_type="group" 且带 group_id,
    # 否则群消息只会命中私聊规则、群规则永不生效(与群路径 service.py 对齐)。
    if is_group:
        blocked = _check_user_blocked(
            db,
            req.user_id,
            target_type="group",
            group_id=_extract_group_id_from_chat_request(req),
        )
    else:
        blocked = _check_user_blocked(db, req.user_id, target_type="private")
    if blocked:
        blocked_completion = chat_response_contract.build_completed_inbound_response(
            outcome="blocked",
            reason="user_blocked",
            guardrail_status="silent",
        )
        blocked_content = _build_chatlog_user_content(req.query, req.files)
        if private_claim_key is not None:
            chat_persistence.persist_private_claim_completion(
                db,
                _chat_persistence_input(req),
                key=private_claim_key,
                request_sha256=private_request_sha256,
                completion=blocked_completion,
                journal_content=blocked_content,
                journal_processed=1,
            )
        else:
            db.add(ChatLog(
                user_id=req.user_id,
                session_id=req.session_id,
                role="user",
                content=blocked_content,
                sender_name=req.sender_name or "",
                session_name=req.session_name or "",
                processed=1,
                message_id=req.message_id or "",
                meta_json=json.dumps(
                    {"blocked": True, "reason": "user_blocked"},
                    ensure_ascii=False,
                ),
            ))
            db.commit()
        return _ChatDatabasePreparation(
            blocked_completion=blocked_completion,
            memory_header="",
            history_messages=[],
            context_debug={},
        )

    memory_header, history_messages, context_debug = _build_chat_context(
        db,
        req.session_id,
        user_id=req.user_id,
        max_per_msg=MAX_MEMORY_PER_MSG_CHARS,
        is_group=is_group,
        group_id=_extract_group_id_from_chat_request(req) if is_group else "",
        max_total=MAX_MEMORY_TOTAL_CHARS,
        current_user_input=req.query,
    )
    return _ChatDatabasePreparation(
        blocked_completion=None,
        memory_header=memory_header,
        history_messages=history_messages,
        context_debug=context_debug,
    )


@router.post("/chat")
async def proxy_chat(
    req: ChatProxyRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    统一网关：接收客户端的发问，通过 KT Agent 处理，返回结果并双向落库。
    """
    _normalize_request_client_meta(req, expected_chat_type=_chat_request_type(req))
    message_contract = (
        chat_request_contract.normalize_request_message_contract(req)
    )
    normalized_files = _normalize_files(req.files)
    req.files = normalized_files
    phase_session_factory = (
        session_factory_from_session(db)
        if isinstance(db, Session)
        else None
    )
    claim_key = normalize_inbound_claim_key(
        _chat_request_platform(req),
        _chat_request_type(req),
        req.session_id,
        req.message_id,
    )
    claim_decision = await run_session_phase_async(
        lambda phase_db: acquire_inbound_claim(phase_db, claim_key),
        session_factory=phase_session_factory,
    )

    if claim_decision.kind == ClaimDecisionKind.DUPLICATE_INFLIGHT:
        return chat_response_contract.duplicate_inflight_chat_response_payload(req)

    if claim_decision.kind == ClaimDecisionKind.REPLAY:
        completed_response = claim_decision.response
        if completed_response is None:
            raise RuntimeError("completed claim 缺少完成结果")
        return _completed_chat_route_response(req, completed_response)

    claim_owner = None
    private_claim_key = None
    private_request_sha256 = ""
    try:
        if claim_decision.kind == ClaimDecisionKind.ACQUIRED:
            if claim_decision.handle is None:
                raise RuntimeError("acquired claim 缺少 owner handle")
            claim_owner = InboundClaimOwner(
                claim_decision.handle,
                session_factory=phase_session_factory,
            )

        if claim_owner is not None:
            await claim_owner.start()
            if claim_key is not None and claim_key.chat_type == "private":
                private_claim_key = claim_key
                private_business_input = chat_recovery.build_private_business_input(
                    req,
                    key=private_claim_key,
                )
                private_request_sha256 = (
                    chat_recovery.private_business_input_sha256(
                        private_business_input
                    )
                )
                handle = claim_decision.handle
                if handle is None:
                    raise RuntimeError("acquired claim 缺少 owner handle")
                def journal_phase(phase_db: Session) -> Any | None:
                    chat_persistence.ensure_private_request_journal(
                        phase_db,
                        _chat_persistence_input(req),
                        key=private_claim_key,
                        request_sha256=private_request_sha256,
                    )
                    if handle.attempt_count <= 1:
                        return None
                    return chat_recovery.load_private_recoverable_completion(
                            phase_db,
                            key=private_claim_key,
                            request_sha256=private_request_sha256,
                        )

                recovered_completion = await run_session_phase_async(
                    journal_phase,
                    session_factory=phase_session_factory,
                )
                if recovered_completion is not None:
                    completed = await claim_owner.complete(
                        recovered_completion
                    )
                    if completed is not True:
                        raise RuntimeError(
                            "私聊恢复 claim complete 未成功"
                        )
                    return _completed_chat_route_response(
                        req,
                        recovered_completion,
                    )

        _safe_log(
            "info",
            "[/chat] Request START: user=%s, session=%s, sender=%s, file_count=%d, session_name=%s",
            req.user_id,
            req.session_id,
            req.sender_name,
            len(normalized_files),
            req.session_name,
        )

        is_group = message_contract.chat_stream.chat_type == "group"
        is_superuser = (not is_group) and _is_guardrail_superuser(req.user_id)
        db_preparation = await run_session_phase_async(
            lambda phase_db: _prepare_chat_database_phase(
                phase_db,
                req,
                is_group=is_group,
                private_claim_key=private_claim_key,
                private_request_sha256=private_request_sha256,
            ),
            session_factory=phase_session_factory,
        )

        # 1.5 检查用户屏蔽规则——持久化已在同一个短事务阶段完成
        if db_preparation.blocked_completion is not None:
            _safe_log("info", "[/chat] blocked user=%s", req.user_id)
            blocked_payload = _chat_response_payload(
                req,
                status="silent",
                reason="user_blocked",
                guardrail_status="silent",
                include_answer_chunks=True,
            )
            if claim_owner is not None:
                completed = await claim_owner.complete(
                    db_preparation.blocked_completion
                )
                if completed is not True:
                    raise RuntimeError("blocked claim complete 未成功")
            return blocked_payload

        _schedule_image_precache(
            background_tasks,
            req.files,
            source_type="chat_request",
            source_name_prefix=f"{req.session_id}_{req.message_id or 'message'}",
        )

        # 2. 会话记忆已在短数据库阶段构建；画像只在 runtime route 中动态选择一次。
        memory_header = db_preparation.memory_header
        history_messages = db_preparation.history_messages
        _ctx_debug = db_preparation.context_debug

        # 4a. 私聊三态分类、guardrail 和 private buffer pre-bridge 决策
        pre_bridge = await _resolve_chat_pre_bridge_decision(
            req,
            is_group=is_group,
            is_superuser=is_superuser,
        )

        pre_bridge_route = await _resolve_pre_bridge_route_result(
            db,
            req,
            pre_bridge,
            claim_key=private_claim_key,
            request_sha256=private_request_sha256,
            session_factory=phase_session_factory,
        )
        if isinstance(pre_bridge_route, chat_pre_bridge_route_result.ChatPreBridgeRouteEarlyResponse):
            if claim_owner is not None:
                if pre_bridge_route.completion is None:
                    raise RuntimeError("pre-bridge early response 缺少完成结果")
                completed = await claim_owner.complete(
                    pre_bridge_route.completion
                )
                if completed is not True:
                    raise RuntimeError("pre-bridge claim complete 未成功")
            return pre_bridge_route.payload

        final_query = pre_bridge_route.final_query
        final_files = pre_bridge_route.final_files
        _private_decision = pre_bridge_route.private_decision
        private_timing_meta = pre_bridge_route.private_timing_meta
        guardrail_status = pre_bridge_route.guardrail_status
        _classifier_ran = pre_bridge_route.classifier_ran
        persist_req = pre_bridge_route.persist_req

        # 4b. 组装 runtime payload — 使用缓冲合并后的查询
        runtime_input = chat_runtime_route_context.ChatRuntimeRouteInput(
                req=req,
                final_query=final_query,
                final_files=final_files,
                memory_header=memory_header,
                history_messages=history_messages,
                ctx_debug=_ctx_debug,
                is_group=is_group,
                is_superuser=is_superuser,
                private_decision=_private_decision,
                guardrail_status=guardrail_status,
                classifier_ran=_classifier_ran,
            )
        runtime_route_context = await run_session_phase_async(
            lambda phase_db: _build_chat_runtime_route_context(
                runtime_input,
                services=_chat_runtime_route_services(phase_db),
            ),
            session_factory=phase_session_factory,
        )
        enriched_query = runtime_route_context.enriched_query
        bridge_meta = runtime_route_context.bridge_meta
        platform = runtime_route_context.platform
        # 5. 通过 KT Bridge 调用 Agent (KT 自动处理工具循环、session 管理等)
        bridge = get_bridge()
        route_runner_context = _chat_route_runner_context(
            req=req,
            persist_req=persist_req,
            bridge=bridge,
            enriched_query=enriched_query,
            bridge_meta=bridge_meta,
            platform=platform,
            guardrail_status=guardrail_status,
            private_timing_meta=private_timing_meta,
            background_tasks=background_tasks,
            session_factory=phase_session_factory,
            claim_owner=claim_owner,
            claim_key=private_claim_key,
            request_sha256=private_request_sha256,
            message_contract=message_contract,
        )

        if req.stream:
            if claim_owner is not None:
                paused = await claim_owner.pause()
                if paused is not True:
                    raise RuntimeError("流式 claim owner 暂停续租失败")
            return StreamingResponse(
                chat_route_runner.ColdChatStreamingBody(route_runner_context),
                media_type="text/event-stream",
            )
    except BaseException as exc:
        await _best_effort_fail_chat_claim(claim_owner, exc)
        raise

    non_streaming_result = await chat_route_runner.run_non_streaming_chat_response(
        db,
        route_runner_context,
    )
    if non_streaming_result.http_error is not None:
        raise HTTPException(
            status_code=non_streaming_result.http_error.status_code,
            detail=non_streaming_result.http_error.detail,
        )

    return non_streaming_result.payload

router.include_router(evolution_router)
router.include_router(history_log_router)
router.include_router(memory_router)
router.include_router(model_router)
router.include_router(task_router)
router.include_router(sticker_media_router)
router.include_router(asset_router)

@router.get("/health")
def health_check():
    """健康检查端点。"""
    return {"status": "healthy", "version": "0.2.0"}


@router.get("/ready")
def readiness_check():
    """本地依赖 readiness；不探测外部 LLM，避免瞬时网络抖动摘流量。"""

    from core.runtime_health import readiness_snapshot

    payload = readiness_snapshot()
    return JSONResponse(payload, status_code=200 if payload["ready"] else 503)
