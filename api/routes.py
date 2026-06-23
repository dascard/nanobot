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
from nanobot_kt.bridge import get_bridge
from clients.classifier_client import get_guardrail, get_timing_gate
from app.group_ingress import helpers as group_ingress_helpers
from api import (
    chat_content_helpers,
    chat_guardrail_facade,
    chat_persistence,
    chat_persona_context,
    chat_private_buffer,
    chat_push_envelope,
    chat_request_contract,
    chat_response_contract,
    chat_runtime_facade,
    chat_streaming_helpers,
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
    normalized_files = _normalize_files(files)
    if not normalized_files or background_tasks is None:
        return
    from nanobot_kt.image_pipeline import precache_image_sources

    background_tasks.add_task(
        precache_image_sources,
        normalized_files,
        source_type=source_type,
        source_name_prefix=source_name_prefix,
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
        from core.database import UserBlockRule
        from core.group_runtime.ids import normalize_group_session_id
        rules = db.query(UserBlockRule).filter(
            UserBlockRule.user_id == user_id,
            UserBlockRule.enabled == 1,
        ).all()
        norm_group = normalize_group_session_id(group_id) if group_id else ""
        for r in rules:
            if r.target_type in (target_type, "all"):
                if r.target_type == "group" and r.group_id:
                    if norm_group and normalize_group_session_id(r.group_id) != norm_group:
                        continue
                return True
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

    # 2. 加载用户画像 (PersonaArchitectAgent 实际输出的键: identity, communication_style, domain_profiles, persona_summary)
    # 兼容性：bot 端 user_id 格式可能变化（"12345" vs "private_12345" vs "group_xxx"）
    # 逐一尝试所有可能的 ID 变体
    def _find_persona(db: Session, uid: str) -> Persona | None:
        candidates: list[str] = [uid]
        # 添加前缀变体
        for prefix in ("private_", "group_"):
            if not uid.startswith(prefix):
                candidates.append(f"{prefix}{uid}")
        # 剥离前缀变体
        for prefix in ("private_", "group_"):
            if uid.startswith(prefix):
                candidates.append(uid[len(prefix):])
        # 去重后依次尝试
        for c in dict.fromkeys(candidates):
            p = db.query(Persona).filter(Persona.user_id == c).first()
            if p:
                if c != candidates[0]:
                    logger.info(f"[/chat] Persona found via fallback: tried={candidates[0]}, matched={c}")
                return p
        logger.debug(f"[/chat] No persona for user_id={uid} (tried {len(candidates)} variants)")
        return None
    persona_obj = _find_persona(db, req.user_id)
    persona_json_str = persona_obj.persona_json if persona_obj else "{}"
    try:
        persona_data = json.loads(persona_json_str)
    except (json.JSONDecodeError, TypeError):
        persona_data = {}
    persona_text = _format_persona_for_prompt(persona_data)

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

    # 4a. 私聊三态分类：先分类再路由
    guardrail_status: str | None = None
    _classifier_ran = False
    buffered_query: str | None = None
    buffered_files: list[str] | None = None
    _private_decision: PrivateDecision | None = None
    private_timing_meta: dict | None = None

    if not is_group and not req.classification_request:
        from core.private_timing import get_private_gate, PrivateDecision, get_effort_constraint
        try:
            private_gate = get_private_gate()
            try:
                _private_decision = await private_gate.classify(
                    req.query, user_id=req.user_id, has_files=bool(req.files),
                    is_superuser=is_superuser,
                )
            except TypeError as te:
                if "is_superuser" not in str(te):
                    raise
                _private_decision = await private_gate.classify(
                    req.query, user_id=req.user_id, has_files=bool(req.files),
                )
            private_timing_meta = _private_timing_meta(_private_decision)
            if _private_decision.action == "no_reply":
                _persist_chat_turn(db, req, "", guardrail_status=None, timing_meta=private_timing_meta)
                return _chat_response_payload(
                    req,
                    status="no_reply",
                    reason=_private_decision.reason,
                    include_answer_chunks=True,
                )
            if _private_decision.effort == "casual":
                from core.reply_templates import get_casual_reply
                reply = get_casual_reply(req.query, is_superuser=is_superuser)
                if reply:
                    _persist_chat_turn(
                        db,
                        req,
                        reply,
                        guardrail_status="casual_template",
                        timing_meta=private_timing_meta,
                    )
                    return _chat_response_payload(
                        req,
                        status="ok",
                        answer=reply,
                        source="casual_template",
                        intent=_private_decision.reason,
                        guardrail_status="casual_template",
                        include_answer_chunks=True,
                )
                # 无模板匹配——casual 不进 bridge，走默认短句
                fallback = "你先说事" if req.query else ""
                _persist_chat_turn(
                    db,
                    req,
                    fallback,
                    guardrail_status="casual_template",
                    timing_meta=private_timing_meta,
                )
                return _chat_response_payload(
                    req,
                    status="ok",
                    answer=fallback,
                    source="casual_template",
                    intent=_private_decision.reason,
                    guardrail_status="casual_template",
                    include_answer_chunks=True,
                )
            if _private_decision.action == "reply_now":
                messages = req.merged_messages or [req.query]
                buffered_query = _join_buffered_messages(messages)
                buffered_files = _normalize_files(req.files)
        except Exception as e:
            logger.warning("[/chat] PrivateGate classify failed user=%s: %s", req.user_id, e)

    if not is_group or req.classification_request:
        try:
            _classifier_ran = True
            guardrail = get_guardrail()
            messages = req.merged_messages or [req.query]
            merged = _join_buffered_messages(messages)
            guardrail_input = _build_guardrail_input(merged, req.files)

            def _guardrail_task_factory() -> asyncio.Task[Any]:
                return asyncio.create_task(
                    asyncio.to_thread(
                        _detect_guardrail,
                        guardrail,
                        guardrail_input,
                        allow_passthrough=_is_guardrail_superuser(req.user_id),
                    )
                )

            buffer_result = await _private_buffer_store.begin_or_append(
                req.user_id,
                merged_query=merged,
                files=_normalize_files(req.files),
                guardrail_task_factory=_guardrail_task_factory,
                now=_time.time(),
                config=_private_buffer_config(),
            )

            if isinstance(buffer_result, chat_private_buffer.PrivateBufferFollowerJoined):
                # 缓冲期内后续消息：等待第一条完成，但不返回 answer
                # 第一条消息已通过 HTTP 响应返回了 answer，后续消息只静默消费
                try:
                    await asyncio.wait_for(
                        buffer_result.done_event.wait(),
                        timeout=PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[/chat] Private buffer follower timed out: user=%s",
                        req.user_id,
                    )
                    await _finalize_private_buffer(req.user_id)
                return _chat_response_payload(
                    req,
                    status="silent",
                    reason="private_buffer_follower",
                    include_answer_chunks=True,
                )

            # 第一条消息负责等待“最后一条消息后的 5 秒静默期”
            if not await _wait_private_buffer_deadline(req.user_id):
                return _chat_response_payload(
                    req,
                    status="silent",
                    reason="private_buffer_missing",
                    include_answer_chunks=True,
                )

            snapshot = await _private_buffer_store.snapshot(req.user_id)
            if snapshot is None:
                return _chat_response_payload(
                    req,
                    status="silent",
                    reason="private_buffer_missing",
                    include_answer_chunks=True,
                )
            buffered_messages = snapshot.messages
            buffered_files = snapshot.files
            qwen_task = snapshot.guardrail_task

            buffered_query = _join_buffered_messages(buffered_messages)
            buffered_guardrail_input = _build_guardrail_input(buffered_query, buffered_files)
            if len(buffered_messages) > 1:
                result = await asyncio.to_thread(
                    _detect_guardrail,
                    guardrail,
                    buffered_guardrail_input,
                    allow_passthrough=_is_guardrail_superuser(req.user_id),
                )
            else:
                result = await qwen_task

            await _private_buffer_store.store_guardrail_result(req.user_id, result)

            guardrail_status = chat_guardrail_facade.guardrail_status_from_result(result)
            logger.info(
                "[/chat] Guardrail result: injection=%s, passthrough=%s, user=%s",
                result.get("injection", False),
                result.get("passthrough", False),
                req.user_id,
            )
        except asyncio.CancelledError:
            await _finalize_private_buffer(req.user_id)
            raise
        except Exception:
            await _finalize_private_buffer(req.user_id)
            raise

    # 持久化使用合并后的真实 query，避免后续缓冲消息静默丢失
    final_query = buffered_query or req.query
    final_files = buffered_files if buffered_files is not None else _normalize_files(req.files)
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

        async def _yield_queue_event(raw_event: Any):
            events = chat_streaming_helpers.collect_ready_stream_events(
                raw_event,
                stream_queue,
                normalize_event=_normalize_chat_stream_event,
                coalescer=coalescer,
            )
            for event in events:
                yield _chat_sse_data(event)

        async def _drain_stream_queue_until_runner_done() -> None:
            await chat_streaming_helpers.drain_stream_queue_until_task_done(
                stream_queue,
                runner_task,
            )

        async def _persist_stream_result_after_runner_done(
            *,
            push: bool,
            persist_db: Session | None = None,
            drain_stream: bool = False,
        ) -> None:
            drain_task = (
                asyncio.create_task(_drain_stream_queue_until_runner_done())
                if drain_stream else None
            )
            try:
                await runner_task
                if drain_task is not None:
                    await drain_task
                final_answer = EMPTY_ASSISTANT_PLACEHOLDER
                assistant_meta = None
                assistant_processed = None
                should_push = False

                if "error" in result_holder:
                    err_msg = str(result_holder.get("error") or "unknown")
                    logger.error(
                        f"[/chat] Stream-aborted runner failed: "
                        f"user={req.user_id}, session={req.session_id}, error={err_msg}"
                    )
                else:
                    private_reply_meta = _pop_bridge_reply_meta(bridge, req.session_id)
                    if (private_reply_meta or {}).get("_agent_result") == "prompt_v2_audit_failed":
                        assistant_meta = _private_prompt_audit_failure_meta()
                        assistant_processed = 1
                    else:
                        answer = str(result_holder.get("answer") or "")
                        if answer.strip():
                            final_answer = answer
                            should_push = push

                await _finalize_private_buffer(req.user_id, final_answer)

                def _write(db_for_write: Session) -> None:
                    _persist_chat_turn(
                        db_for_write,
                        persist_req,
                        final_answer,
                        guardrail_status,
                        assistant_meta=assistant_meta,
                        assistant_processed=assistant_processed,
                        timing_meta=private_timing_meta,
                    )

                if persist_db is not None:
                    _write(persist_db)
                else:
                    from core.uow import UnitOfWork

                    with UnitOfWork() as uow:
                        if uow.db is None:
                            raise RuntimeError("UnitOfWork session is not open")
                        _write(uow.db)

                if should_push:
                    from core.daily_digest import push_envelope_to_qq

                    # 推送前展开图片 token（禁用 base64，避免推送大负载）
                    push_answer = final_answer
                    try:
                        push_answer = _expand_chat_transport_answer(final_answer)
                    except Exception:
                        pass

                    push_payload = _build_chat_push_envelope(
                        req,
                        answer=push_answer,
                        platform=platform,
                        chat_type=str(bridge_meta.get("chat_type") or ""),
                        is_group=bool(bridge_meta.get("is_group")),
                    )
                    ok = await push_envelope_to_qq(
                        push_payload.target_type,
                        push_payload.target_id,
                        push_payload.envelope,
                    )
                    if ok:
                        logger.info(
                            f"[/chat] Stream-aborted result pushed: "
                            f"user={req.user_id}, len={len(final_answer)}"
                        )
                    else:
                        logger.error(
                            f"[/chat] Stream-aborted result push failed: "
                            f"user={req.user_id}, session={req.session_id}, len={len(final_answer)}"
                        )
            except Exception as e:
                logger.error(f"[/chat] Background finish failed: {e}")
            finally:
                if drain_task is not None and not drain_task.done():
                    drain_task.cancel()
                    await asyncio.gather(drain_task, return_exceptions=True)

        try:
            while True:
                if done.is_set() and stream_queue.empty():
                    break
                get_task = asyncio.create_task(stream_queue.get())
                done_task = asyncio.create_task(done.wait())
                try:
                    completed, pending = await asyncio.wait(
                        {get_task, done_task},
                        timeout=heartbeat_interval,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for pending_task in pending:
                        pending_task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    if not completed:
                        yield _chat_sse_data({"status": "heartbeat"})
                        continue

                    if get_task in completed:
                        async for chunk in _yield_queue_event(get_task.result()):
                            yield chunk
                        continue

                    # runner 已完成但没有新事件时，不再等 heartbeat 超时。
                    if done_task in completed:
                        break
                finally:
                    for task in (get_task, done_task):
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)

            await asyncio.sleep(0)
            while True:
                try:
                    event = stream_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                async for chunk in _yield_queue_event(event):
                    yield chunk
            pending_delta = coalescer.flush()
            if pending_delta is not None:
                yield _chat_sse_data(pending_delta)

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

    private_reply_meta = _pop_bridge_reply_meta(bridge, req.session_id)
    if (private_reply_meta or {}).get("_agent_result") == "prompt_v2_audit_failed":
        logger.error("[/chat] Prompt V2 audit failed: user=%s session=%s", req.user_id, req.session_id)
        try:
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
        except Exception as pe:
            logger.error(f"[/chat] Persist failed on prompt audit error path: {pe}")
        raise HTTPException(status_code=500, detail="系统暂时不可用，请稍后再试")

    logger.info(f"[/chat] Bridge returned: answer_len={len(answer)}, answer_stripped_empty={not answer.strip()}")
    if answer:
        logger.debug(f"[/chat] Answer preview: {answer[:300]}")
    else:
        logger.warning(f"[/chat] EMPTY ANSWER returned from bridge!")

    # 仅传输层展开图片 token，数据库仍存短 token
    # 禁用 base64——所有面向 QQbot 的响应都不应返回大 base64
    transport_answer = answer
    try:
        transport_answer = _expand_chat_transport_answer(answer)
    except Exception:
        logger.warning("[/chat] generated image ref expansion failed", exc_info=True)

    # 3. 落库 (KT 的 session 管理是独立的, nanobot 原有日志需手动写入)
    await _finalize_private_buffer(req.user_id, answer)
    pending = _persist_chat_turn(
        db,
        persist_req,
        answer,
        guardrail_status,
        timing_meta=private_timing_meta,
    )

    # 4. 检查进化触发阈值 (按 user_id 计数，而非 session_id，确保个人消息足够才触发进化)
    if pending >= EVOLUTION_THRESHOLD:
        logger.info(f"[/chat] Evolution triggered: user={req.user_id}, pending={pending}, threshold={EVOLUTION_THRESHOLD}")
        background_tasks.add_task(evolution_task, req.user_id)

    answer_chunks = _split_chat_answer_chunks(transport_answer)

    logger.info(f"[/chat] Response: answer_chunks_count={len(answer_chunks)}, status=ok")
    return _chat_response_payload(
        req,
        status="ok",
        answer=transport_answer,
        reply_meta=private_reply_meta,
        platform=platform,
        chat_type=str(bridge_meta.get("chat_type") or ""),
        unprocessed_logs=pending,
        guardrail_status=guardrail_status,
        include_answer_chunks=True,
    )

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
