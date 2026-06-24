"""
FastAPI 路由模块。
定义所有 HTTP 端点，含 Bearer Token 认证中间件。
"""
import os
import logging
import json
import asyncio
import time as _time
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Any, Optional, List

from config import (
    NANOBOT_API_TOKEN, EVOLUTION_THRESHOLD, ADMIN_USER_ID,
    OPENAI_API_KEY, OPENAI_BASE_URL, LLM_PROVIDER, NEW_API_KEY, NEW_API_BASE_URL, NEW_API_TIMEOUT,
    LLM_MODEL_SMART, LLM_MODEL_FAST, LLM_MODEL_REASONING,
)
from core.database import (
    get_db,
    release_clean_session_transaction,
    User,
    Persona,
    ChatLog,
)
from core.evolution import evolution_task
from core.legacy_adapter import SQLiteMemory  # Keep for evolution; UnifiedProvider/Controller replaced by KT
from core.moderation import check_message_moderation_db
from core import user_block_rules
from nanobot_kt.bridge import get_bridge
from clients.classifier_client import get_guardrail, get_timing_gate
from app.group_ingress import helpers as group_ingress_helpers
from api import (
    chat_content_helpers,
    chat_guardrail_facade,
    chat_media_precache,
    chat_non_streaming_result,
    chat_pre_bridge_decision,
    chat_persistence,
    chat_persona_context,
    chat_persona_lookup,
    chat_private_buffer,
    chat_push_envelope,
    chat_request_contract,
    chat_response_contract,
    chat_runtime_facade,
    chat_sse_loop,
    chat_streaming_helpers,
    chat_streaming_result,
)
from api.common_auth import verify_token
from api.evolution_routes import (
    EvolutionTriggerRequest,
    router as evolution_router,
    trigger_evolution,
)
from api.history_log_routes import (
    AmbientLogRequest,
    LogRequest,
    compact_history,
    get_context,
    get_history_summary,
    mark_clear,
    router as history_log_router,
    search_history_logs,
    submit_ambient_log,
    submit_log,
)
from api.memory_routes import (
    MemoryDigestRunRequest,
    _build_expand_chain,
    _calc_recall_confidence,
    _short_text,
    _validate_memory_digest_date_filters,
    get_memory_digests,
    recall_memory,
    router as memory_router,
    run_memory_digests,
)
from api.model_routes import (
    ModelSyncRequest,
    list_models,
    router as model_router,
    sync_models,
)
from api.task_routes import (
    ScheduledTaskCreate,
    create_scheduled_task,
    delete_scheduled_task,
    list_scheduled_tasks,
    router as task_router,
    run_scheduled_task_now,
    toggle_scheduled_task,
    update_scheduled_task,
)
from api.sticker_media_routes import (
    StickerRegisterRequest,
    disable_sticker_endpoint,
    public_generated_image,
    public_sticker_image,
    register_sticker_endpoint,
    router as sticker_media_router,
    search_sticker_endpoint,
)
from api.agent_step_routes import (
    AgentStepRequest,
    agent_step_event_payload,
    agent_step_sse_data,
    chat_step,
    render_markdown,
    router as agent_step_router,
    run_agent_step,
    run_agent_step_stream,
)
from api.group_message_routes import (
    GroupMessageRequest,
    OneBotMessageSegmentPayload,
    group_message,
    router as group_message_router,
)
from api.group_utility_routes import (
    GroupTimingRequest,
    GroupTimingTimerRequest,
    UpdateGroupNameRequest,
    _build_group_timing_context,
    group_timing_deprecated,
    group_timing_timer,
    router as group_utility_router,
    update_group_name,
)

logger = logging.getLogger("nanobot.routes")
router = APIRouter(prefix="/api/v1")
EMPTY_ASSISTANT_PLACEHOLDER = "（无回复内容）"
SAFE_STREAM_ERROR_MESSAGE = "系统暂时不可用，请稍后再试"
CHAT_STREAM_QUEUE_MAXSIZE = 128


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
MAX_MEMORY_WINDOW_MINUTES = 30
MAX_MEMORY_PER_MSG_CHARS = 300
MAX_MEMORY_TOTAL_CHARS = 4000
MAX_TIMING_PENDING_MESSAGES = 5
MAX_TIMING_MESSAGE_CHARS = 200
MAX_TIMING_CONTEXT_CHARS = 1200

# --- Legacy Memory (for evolution endpoints) ---
memory = None

def init_legacy_memory():
    """Initialize SQLiteMemory for evolution endpoints. Called from server.py lifespan."""
    global memory
    memory = SQLiteMemory()
    logger.info("Legacy SQLiteMemory initialized for evolution endpoints")


from core.context_builder import (
    sanitize_prompt_text as _sanitize_prompt_text,
    estimate_tokens as _estimate_tokens,
    relative_time_label as _relative_time_label,
    build_chat_context as _build_chat_context,
    build_session_memory as _build_session_memory,
    format_group_planner_message as _format_group_planner_message,
    MAX_GROUP_CONTEXT_ROWS,
    MAX_PRIVATE_CONTEXT_ROWS,
)

# 旧函数已移至 core/context_builder.py，此处仅保留向后兼容 re-export


def _format_persona_for_prompt(persona_data: dict, max_chars: int = MAX_PERSONA_CHARS) -> str:
    return chat_persona_context.format_persona_for_prompt(
        persona_data,
        max_chars=max_chars,
    )


def _resolve_chat_persona_snapshot(db: Session, user_id: str) -> chat_persona_lookup.ChatPersonaSnapshot:
    return chat_persona_lookup.resolve_chat_persona_snapshot(
        db,
        user_id,
        persona_model=Persona,
        format_persona=_format_persona_for_prompt,
    )



ChatProxyRequest = chat_request_contract.ChatProxyRequest

def _safe_meta(meta_json: str) -> dict:
    return chat_persistence.safe_meta(meta_json)


def _clone_chat_request(req: ChatProxyRequest, **updates) -> ChatProxyRequest:
    return chat_request_contract.clone_chat_request(req, **updates)


def _private_buffer_config() -> chat_private_buffer.PrivateBufferConfig:
    return chat_private_buffer.PrivateBufferConfig(
        max_messages=MAX_BUFFERED_MESSAGES,
        window_seconds=PRIVATE_BUFFER_WINDOW_SECONDS,
        window_with_files_seconds=PRIVATE_BUFFER_WINDOW_WITH_FILES_SECONDS,
        follower_timeout_seconds=PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS,
    )


def _join_buffered_messages(messages: list[str]) -> str:
    return chat_private_buffer.join_buffered_messages(messages)


def _normalize_files(files: Optional[List[str]]) -> list[str]:
    return chat_content_helpers.normalize_files(files)


def _schedule_image_precache(
    background_tasks: BackgroundTasks | None,
    files: Optional[List[str]],
    *,
    source_type: str,
    source_name_prefix: str,
) -> None:
    return chat_media_precache.schedule_image_precache(
        background_tasks,
        files,
        source_type=source_type,
        source_name_prefix=source_name_prefix,
        normalize_files=_normalize_files,
    )


from core.settings_service import settings


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
        return user_block_rules.is_user_blocked(
            db,
            user_id,
            target_type=target_type,
            group_id=group_id,
        )
    except Exception as e:
        logger.warning("[BlockRule] check failed user=%s group=%s: %s", user_id, group_id, e)
    return False


def _merge_buffered_files(existing: list[str], incoming: Optional[List[str]]) -> list[str]:
    return chat_private_buffer.merge_buffered_files(existing, _normalize_files(incoming))


def _private_buffer_window_seconds(files: Optional[List[str]]) -> float:
    return chat_private_buffer.private_buffer_window_seconds(
        _normalize_files(files),
        _private_buffer_config(),
    )


async def _wait_private_buffer_deadline(user_id: str) -> bool:
    return await _private_buffer_store.wait_until_deadline(
        user_id,
        now=_time.time,
        sleep=asyncio.sleep,
    )


def _build_guardrail_input(query: str, files: Optional[List[str]]) -> str:
    return chat_content_helpers.build_guardrail_input(query, files)


def _detect_guardrail(guardrail, message: str, *, allow_passthrough: bool = False) -> dict:
    return chat_guardrail_facade.detect_guardrail(
        guardrail,
        message,
        allow_passthrough=allow_passthrough,
    )


def _detect_guardrail_for_pre_bridge(
    guardrail: Any,
    message: str,
    allow_passthrough: bool,
) -> dict[str, Any]:
    return _detect_guardrail(
        guardrail,
        message,
        allow_passthrough=allow_passthrough,
    )


def _build_multimodal_user_input_text(query: str, files: Optional[List[str]], *, max_chars: int = 0) -> str:
    return chat_content_helpers.build_multimodal_user_input_text(query, files, max_chars=max_chars)


def _build_file_archive_summary(files: Optional[List[str]], *, include_refs: bool) -> str:
    return chat_content_helpers.build_file_archive_summary(files, include_refs=include_refs)


def _build_chatlog_user_content(query: str, files: Optional[List[str]]) -> str:
    return chat_content_helpers.build_chatlog_user_content(query, files)


def _build_conversation_user_content(query: str, files: Optional[List[str]]) -> str:
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
    *,
    status: str,
    answer: str = "",
    reply_meta: dict | None = None,
    platform: str = "",
    chat_type: str = "",
    unprocessed_logs: int | None = None,
    reason: str = "",
    source: str = "",
    intent: str = "",
    guardrail_status: str | None = None,
    include_answer_chunks: bool = False,
    extra_meta: dict | None = None,
) -> dict[str, Any]:
    return chat_response_contract.chat_response_payload(
        req,
        status=status,
        answer=answer,
        reply_meta=reply_meta,
        platform=platform,
        chat_type=chat_type,
        unprocessed_logs=unprocessed_logs,
        reason=reason,
        source=source,
        intent=intent,
        guardrail_status=guardrail_status,
        include_answer_chunks=include_answer_chunks,
        extra_meta=extra_meta,
    )


def _expand_chat_transport_answer(answer: str) -> str:
    return chat_push_envelope.expand_chat_transport_answer(answer)


def _build_chat_push_envelope(
    req: ChatProxyRequest,
    **kwargs: Any,
) -> chat_push_envelope.ChatPushEnvelope:
    return chat_push_envelope.build_chat_push_envelope(req, **kwargs)


def _is_guardrail_superuser(user_id: str) -> bool:
    admin_user_id = str(ADMIN_USER_ID or "").strip()
    return bool(admin_user_id) and str(user_id or "").strip() == admin_user_id


async def _finalize_private_buffer(
    user_id: str,
    answer: str | None = None,
    *,
    clear_window: bool = True,
) -> None:
    await _private_buffer_store.finalize(
        user_id,
        answer,
        clear_window=clear_window,
    )


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


def _get_casual_reply_for_pre_bridge(query: str, is_superuser: bool) -> str:
    from core.reply_templates import get_casual_reply as _core_get_casual_reply

    return _core_get_casual_reply(query, is_superuser=is_superuser)


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
        chat_persistence.ChatTurnPersistenceInput(
            user_id=req.user_id,
            session_id=req.session_id,
            query=req.query,
            files=req.files,
            sender_name=req.sender_name,
            session_name=req.session_name,
            message_id=req.message_id,
            source_message_ids=req.source_message_ids,
            client_meta=req.client_meta,
        ),
        answer,
        guardrail_status,
        assistant_meta=assistant_meta,
        assistant_processed=assistant_processed,
        timing_meta=timing_meta,
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
    logger.info(f"[/chat] Request START: user={req.user_id}, session={req.session_id}, query={req.query[:100]}, sender={req.sender_name}, files={req.files}, session_name={req.session_name}")

    # 1. 自动注册用户 & 更新用户名
    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        db.add(User(id=req.user_id, name=(req.sender_name or "")))
        db.commit()
    elif req.sender_name and user.name != req.sender_name:
        user.name = req.sender_name
        db.commit()

    # 1.5 检查用户屏蔽规则——命中后只写 ChatLog，不回复
    if _check_user_blocked(db, req.user_id, target_type="private"):
        logger.info("[/chat] blocked user=%s", req.user_id)
        db.add(ChatLog(
            user_id=req.user_id, session_id=req.session_id,
            role="user",
            content=_build_chatlog_user_content(req.query, req.files),
            sender_name=req.sender_name or "",
            session_name=req.session_name or "",
            processed=1, message_id=req.message_id or "",
            meta_json=json.dumps({"blocked": True, "reason": "user_blocked"}, ensure_ascii=False),
        ))
        db.commit()
        return _chat_response_payload(
            req,
            status="silent",
            reason="user_blocked",
            guardrail_status="silent",
            include_answer_chunks=True,
        )

    _schedule_image_precache(
        background_tasks,
        req.files,
        source_type="chat_request",
        source_name_prefix=f"{req.session_id}_{req.message_id or 'message'}",
    )

    # 2. 加载用户画像 snapshot；动态 PersonaInjectionService 仍在 runtime payload 前处理
    persona_snapshot = _resolve_chat_persona_snapshot(db, req.user_id)
    persona_obj = persona_snapshot.persona_obj
    persona_json_str = persona_snapshot.persona_json
    persona_data = persona_snapshot.persona_data
    persona_text = persona_snapshot.persona_text
    if persona_snapshot.matched_user_id and persona_snapshot.matched_user_id != persona_snapshot.lookup_user_id:
        logger.info(
            "[/chat] Persona found via fallback: tried=%s, matched=%s",
            persona_snapshot.lookup_user_id,
            persona_snapshot.matched_user_id,
        )
    if persona_obj is None:
        logger.debug(
            "[/chat] No persona for user_id=%s (tried %s variants)",
            req.user_id,
            persona_snapshot.candidate_count,
        )

    logger.info(
        f"[/chat] Persona lookup: user_id={req.user_id}, "
        f"found={persona_obj is not None}, persona_raw_len={len(persona_json_str)}, "
        f"persona_text_len={len(persona_text)}, "
        f"keys={list(persona_data.keys()) if isinstance(persona_data, dict) else 'N/A'}"
    )

    is_group = not str(req.session_id).startswith("private_")
    is_superuser = (not is_group) and _is_guardrail_superuser(req.user_id)

    # 3. 构建会话记忆上下文 (时间窗口 + clear 标记感知)
    memory_header, history_messages, _ctx_debug = _build_chat_context(
        db,
        req.session_id,
        user_id=req.user_id,
        max_per_msg=MAX_MEMORY_PER_MSG_CHARS,
        is_group=is_group,
        group_id=_extract_group_id_from_chat_request(req) if is_group else "",
        max_total=MAX_MEMORY_TOTAL_CHARS,
        current_user_input=req.query,
    )
    release_clean_session_transaction(db, label="chat_before_private_decision", logger=logger)

    # 4a. 私聊三态分类、guardrail 和 private buffer pre-bridge 决策
    pre_bridge = await _resolve_chat_pre_bridge_decision(
        req,
        is_group=is_group,
        is_superuser=is_superuser,
    )

    if isinstance(pre_bridge, chat_pre_bridge_decision.ChatPreBridgeEarlyReturn):
        if pre_bridge.persist_answer is not None:
            _persist_chat_turn(
                db,
                req,
                pre_bridge.persist_answer,
                guardrail_status=pre_bridge.persist_guardrail_status,
                timing_meta=pre_bridge.persist_timing_meta,
            )
        return _chat_response_payload(
            req,
            status=pre_bridge.status,
            reason=pre_bridge.reason,
            answer=pre_bridge.answer,
            source=pre_bridge.source,
            intent=pre_bridge.intent,
            guardrail_status=pre_bridge.guardrail_status,
            include_answer_chunks=True,
        )

    final_query = pre_bridge.final_query
    final_files = pre_bridge.final_files
    _private_decision = pre_bridge.private_decision
    private_timing_meta = pre_bridge.private_timing_meta
    guardrail_status = pre_bridge.guardrail_status
    _classifier_ran = pre_bridge.classifier_ran
    persist_req = _clone_chat_request(req, query=final_query, files=final_files)

    if _classifier_ran and guardrail_status == "silent":
        await _finalize_private_buffer(req.user_id)
        _persist_chat_turn(
            db,
            persist_req,
            "（数据中转，自动静默）",
            guardrail_status,
            timing_meta=private_timing_meta,
        )
        return _chat_response_payload(
            req,
            status="silent",
            reason="guardrail_silent",
            guardrail_status=guardrail_status,
            include_answer_chunks=True,
        )

    # 4b. 组装 runtime payload — 使用缓冲合并后的查询
    safe_user_input = _build_multimodal_user_input_text(final_query, final_files, max_chars=MAX_QUERY_CHARS)
    if not is_group:
        try:
            from app.persona.injection_service import PersonaInjectionService

            persona_result = PersonaInjectionService(db).build_context(
                user_id=req.user_id,
                current_user_input=safe_user_input,
                recent_messages=history_messages,
            )
            _ctx_debug.update(persona_result.debug)
            if persona_result.context:
                persona_text = persona_result.context
        except Exception as exc:
            logger.warning("[/chat] persona injection context failed user=%s: %s", req.user_id, exc)
    release_clean_session_transaction(db, label="chat_before_bridge", logger=logger)

    def _empty_effort_constraint(_effort: str | None) -> str:
        return ""

    _effort_constraint_func = get_effort_constraint if _private_decision else _empty_effort_constraint
    runtime_payload = chat_runtime_facade.build_chat_runtime_payload(
        chat_runtime_facade.ChatRuntimeInput(
            final_query=final_query,
            final_files=final_files,
            req_user_id=req.user_id,
            req_session_id=req.session_id,
            sender_name=req.sender_name or "",
            session_name=req.session_name,
            message_id=req.message_id or "",
            persona_text=persona_text,
            memory_header=memory_header,
            history_messages=history_messages,
            is_group=is_group,
            is_superuser=is_superuser,
            stream=bool(req.stream),
            platform=_chat_request_platform(req),
            private_decision=_private_decision,
            guardrail_status=guardrail_status,
            classifier_ran=_classifier_ran,
        ),
        build_multimodal_user_input_text=_build_multimodal_user_input_text,
        max_query_chars=MAX_QUERY_CHARS,
        estimate_tokens=_estimate_tokens,
        get_effort_constraint=_effort_constraint_func,
    )
    safe_user_input = runtime_payload.safe_user_input
    enriched_query = runtime_payload.enriched_query
    bridge_meta = runtime_payload.bridge_meta
    platform = str(bridge_meta.get("platform") or "")
    prompt_budget = runtime_payload.prompt_budget

    if not runtime_payload.injection_mode:
        chat_type = "private" if str(req.session_id).startswith("private_") else "group"

        logger.info(
            f"[/chat] Prompt budget: type={chat_type}, "
            f"query_chars={prompt_budget['safe_user_input_chars']}, "
            f"query_tokens~{prompt_budget['safe_user_input_tokens']}, "
            f"persona_chars={prompt_budget['persona_chars']}, "
            f"persona_tokens~{prompt_budget['persona_tokens']}, "
            f"history_msgs={prompt_budget['history_messages']}, "
            f"history_total_chars~{prompt_budget['history_total_chars']}, "
            f"enriched_chars={prompt_budget['enriched_query_chars']}, "
            f"enriched_tokens~{prompt_budget['enriched_query_tokens']}"
        )
    else:
        logger.info(
            f"[/chat] Injection mode, using mock enriched_query, "
            f"persona_chars={prompt_budget['persona_chars']}, "
            f"persona_tokens~{prompt_budget['persona_tokens']}, "
            f"history_msgs={prompt_budget['history_messages']}"
        )

    # 5. 通过 KT Bridge 调用 Agent (KT 自动处理工具循环、session 管理等)
    bridge = get_bridge()

    async def _do_chat():
        return await chat_runtime_facade.call_bridge_non_streaming(
            bridge,
            enriched_query=enriched_query,
            user_id=req.user_id,
            session_id=req.session_id,
            sender_name=req.sender_name or "",
            metadata=bridge_meta,
        )

    async def _stream_chat():
        """SSE streaming with progress events and heartbeats."""
        result_holder: dict = {}
        done = asyncio.Event()
        stream_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=CHAT_STREAM_QUEUE_MAXSIZE)
        persisted = False

        async def runner():
            try:
                result_holder["answer"] = await bridge.handle_message(
                    enriched_query, user_id=req.user_id, session_id=req.session_id,
                    sender_name=req.sender_name or "", metadata=bridge_meta,
                    stream_queue=stream_queue,
                    stream=True,
                )
            except Exception as e:
                result_holder["error"] = str(e)
            finally:
                done.set()

        runner_task = asyncio.create_task(runner())
        heartbeat_interval = 5
        coalescer = chat_streaming_helpers.StreamEventCoalescer()
        sse_loop_callbacks = chat_sse_loop.ChatSseLoopCallbacks(
            normalize_event=_normalize_chat_stream_event,
        )
        from core.daily_digest import push_envelope_to_qq

        stream_result_callbacks = chat_streaming_result.ChatStreamResultCallbacks(
            drain_stream_queue_until_task_done=chat_streaming_helpers.drain_stream_queue_until_task_done,
            pop_bridge_reply_meta=_pop_bridge_reply_meta,
            private_prompt_audit_failure_meta=_private_prompt_audit_failure_meta,
            finalize_private_buffer=_finalize_private_buffer,
            persist_chat_turn=_persist_chat_turn,
            expand_chat_transport_answer=_expand_chat_transport_answer,
            build_chat_push_envelope=_build_chat_push_envelope,
            push_envelope_to_qq=push_envelope_to_qq,
        )
        stream_result_context = chat_streaming_result.ChatStreamResultContext(
            req=req,
            persist_req=persist_req,
            bridge=bridge,
            result_holder=result_holder,
            runner_task=runner_task,
            stream_queue=stream_queue,
            platform=platform,
            bridge_meta=bridge_meta,
            guardrail_status=guardrail_status,
            private_timing_meta=private_timing_meta,
            empty_assistant_placeholder=EMPTY_ASSISTANT_PLACEHOLDER,
            callbacks=stream_result_callbacks,
        )

        async def _persist_stream_result_after_runner_done(
            *,
            push: bool,
            persist_db: Session | None = None,
            drain_stream: bool = False,
        ) -> None:
            await chat_streaming_result.persist_stream_result_after_runner_done(
                stream_result_context,
                push=push,
                persist_db=persist_db,
                drain_stream=drain_stream,
            )

        try:
            async for event in chat_sse_loop.iter_chat_stream_events(
                stream_queue,
                done,
                heartbeat_interval=heartbeat_interval,
                coalescer=coalescer,
                callbacks=sse_loop_callbacks,
            ):
                yield _chat_sse_data(event)

            if "error" in result_holder:
                err_msg = str(result_holder.get("error") or "unknown")
                logger.error(f"[/chat] Stream runner failed: user={req.user_id}, session={req.session_id}, error={err_msg}")
                try:
                    await _finalize_private_buffer(req.user_id, EMPTY_ASSISTANT_PLACEHOLDER)
                    _persist_chat_turn(
                        db,
                        persist_req,
                        EMPTY_ASSISTANT_PLACEHOLDER,
                        guardrail_status,
                        timing_meta=private_timing_meta,
                    )
                    persisted = True
                except Exception as pe:
                    logger.error(f"[/chat] Stream persist failed on error path: {pe}")
                yield _chat_sse_data(_stream_error_event())
            else:
                answer = result_holder.get("answer", "")
                private_reply_meta = _pop_bridge_reply_meta(bridge, req.session_id)

                # SSE 流禁止 base64 展开——单个 data chunk 过大会导致 QQbot 侧 Chunk too big。
                # 有 public URL 时展开为短 CQ URL，否则保留短 token。
                transport_answer = answer
                try:
                    transport_answer = _expand_chat_transport_answer(answer)
                except Exception:
                    logger.warning("[/chat] stream generated image ref expansion failed", exc_info=True)

                if (private_reply_meta or {}).get("_agent_result") == "prompt_v2_audit_failed":
                    await _finalize_private_buffer(req.user_id, EMPTY_ASSISTANT_PLACEHOLDER)
                    _persist_chat_turn(
                        db,
                        persist_req,
                        EMPTY_ASSISTANT_PLACEHOLDER,
                        guardrail_status,
                        assistant_meta=_private_prompt_audit_failure_meta(),
                        assistant_processed=1,
                        timing_meta=private_timing_meta,
                    )
                    persisted = True
                    yield _chat_sse_data(_stream_error_event())
                else:
                    await _finalize_private_buffer(req.user_id, answer)
                    pending = _persist_chat_turn(
                        db,
                        persist_req,
                        answer,
                        guardrail_status,
                        timing_meta=private_timing_meta,
                    )
                    persisted = True
                    if pending >= EVOLUTION_THRESHOLD:
                        logger.info(f"[/chat] Evolution triggered: user={req.user_id}, pending={pending}, threshold={EVOLUTION_THRESHOLD}")
                        background_tasks.add_task(evolution_task, req.user_id)
                    done_payload = _chat_response_payload(
                        req,
                        status="done",
                        answer=transport_answer,
                        reply_meta=private_reply_meta,
                        platform=platform,
                        chat_type=str(bridge_meta.get("chat_type") or ""),
                        unprocessed_logs=pending,
                        guardrail_status=guardrail_status,
                    )
                    yield _chat_sse_data(done_payload)
        finally:
            if not persisted:
                if runner_task.done():
                    await _persist_stream_result_after_runner_done(push=False, persist_db=db)
                else:
                    # 客户端断连但 runner 还在跑 → 后台继续，完成后 push 结果
                    background_tasks.add_task(
                        _persist_stream_result_after_runner_done,
                        push=True,
                        persist_db=None,
                        drain_stream=True,
                    )
                    await _finalize_private_buffer(req.user_id)
                    logger.warning(
                        f"[/chat] Stream aborted, running in background: "
                        f"user={req.user_id}, session={req.session_id}"
                    )
            done.set()

    if req.stream:
        return StreamingResponse(_stream_chat(), media_type="text/event-stream")

    try:
        answer = await _do_chat()
    except asyncio.CancelledError:
        await _finalize_private_buffer(req.user_id, EMPTY_ASSISTANT_PLACEHOLDER)
        raise
    except Exception as e:
        logger.error(f"[/chat] KT Agent failed: {e}")
        try:
            await _finalize_private_buffer(req.user_id, EMPTY_ASSISTANT_PLACEHOLDER)
            _persist_chat_turn(
                db,
                persist_req,
                EMPTY_ASSISTANT_PLACEHOLDER,
                guardrail_status,
                timing_meta=private_timing_meta,
            )
        except Exception as pe:
            logger.error(f"[/chat] Persist failed on KT error path: {pe}")
        raise HTTPException(status_code=502, detail=SAFE_STREAM_ERROR_MESSAGE)

    non_streaming_callbacks = chat_non_streaming_result.ChatNonStreamingResultCallbacks(
        pop_bridge_reply_meta=_pop_bridge_reply_meta,
        private_prompt_audit_failure_meta=_private_prompt_audit_failure_meta,
        finalize_private_buffer=_finalize_private_buffer,
        persist_chat_turn=_persist_chat_turn,
        expand_chat_transport_answer=_expand_chat_transport_answer,
        chat_response_payload=_chat_response_payload,
    )
    non_streaming_context = chat_non_streaming_result.ChatNonStreamingResultContext(
        req=req,
        persist_req=persist_req,
        bridge=bridge,
        answer=answer,
        platform=platform,
        bridge_meta=bridge_meta,
        guardrail_status=guardrail_status,
        private_timing_meta=private_timing_meta,
        empty_assistant_placeholder=EMPTY_ASSISTANT_PLACEHOLDER,
        evolution_threshold=EVOLUTION_THRESHOLD,
        callbacks=non_streaming_callbacks,
    )
    non_streaming_result = await chat_non_streaming_result.finalize_non_streaming_chat_result(
        db,
        non_streaming_context,
    )
    if non_streaming_result.prompt_audit_failed:
        raise HTTPException(status_code=500, detail="系统暂时不可用，请稍后再试")
    if non_streaming_result.should_trigger_evolution:
        logger.info(
            "[/chat] Evolution triggered: user=%s, pending=%s, threshold=%s",
            req.user_id,
            non_streaming_result.pending,
            EVOLUTION_THRESHOLD,
        )
        background_tasks.add_task(evolution_task, req.user_id)

    return non_streaming_result.payload

router.include_router(evolution_router)
router.include_router(history_log_router)
router.include_router(memory_router)
router.include_router(model_router)
router.include_router(task_router)
router.include_router(sticker_media_router)

@router.get("/health")
def health_check():
    """健康检查端点。"""
    return {"status": "healthy", "version": "0.2.0"}
