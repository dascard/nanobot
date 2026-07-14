"""普通 API 群工具与 legacy timing 路由。"""
from __future__ import annotations

import logging
import sys
import time as _time

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from app.group_ingress import helpers as group_ingress_helpers
from core.context_builder import (
    build_chat_context,
    build_timing_recent_context,
    sanitize_prompt_text,
)
from core.database import User, get_db, release_clean_session_transaction
from core.group_runtime.ids import normalize_group_session_id as _normalize_group_session_id
from core.identity import build_identity_vars
from nanobot_kt.bridge import get_bridge as _default_get_bridge

logger = logging.getLogger("nanobot.routes.group_utility")
router = APIRouter(tags=["group-utility"])
MAX_QUERY_CHARS = 2000


def _current_bridge_provider():
    routes_module = sys.modules.get("api.routes")
    if routes_module is not None and hasattr(routes_module, "get_bridge"):
        return getattr(routes_module, "get_bridge")
    return _default_get_bridge


class UpdateGroupNameRequest(BaseModel):
    group_id: str
    group_name: str


@router.post("/update_group_name")
def update_group_name(
    req: UpdateGroupNameRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """更新 users 表的 name 字段（群聊也用 users 表，id=group_xxx）。"""
    user_id = f"group_{req.group_id}" if not req.group_id.startswith("group_") else req.group_id
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.name = req.group_name
    else:
        db.add(User(id=user_id, name=req.group_name))
    db.commit()
    return {"status": "ok"}


class GroupTimingRequest(BaseModel):
    group_id: str
    sender_id: str = ""
    sender_name: str = ""
    message: str
    pending_messages: list[dict] = []
    message_id: str | None = None
    session_name: str | None = None
    is_reply_to_bot: bool = False
    trigger_reason: str = ""
    bot_aliases: list[str] = []


def _build_group_timing_context(
    req: GroupTimingRequest | None = None,
    **kwargs,
) -> str:
    """[DEPRECATED] wrapper——实际逻辑在 core.timing_runtime.GroupRuntime._build_timing_context()。"""
    from core.timing_runtime import PendingMessage as RPM, GroupRuntime

    if req is not None:
        pending = [
            RPM(
                sender_id=p.get("sender_id", ""),
                sender_name=p.get("sender_name", ""),
                message=p.get("message", ""),
                is_reply_to_bot=req.is_reply_to_bot or p.get("is_reply_to_bot", False),
            )
            for p in (req.pending_messages or [])
        ]
        return GroupRuntime._build_timing_context(
            pending=pending,
            session_name=req.session_name or "",
            trigger_reason=req.trigger_reason or "",
            bot_aliases=list(req.bot_aliases or []),
        )
    return GroupRuntime._build_timing_context(**kwargs)


class GroupTimingTimerRequest(BaseModel):
    """timer_fired 模式——wait 到期后 QQbot 回调。"""

    group_id: str
    generation: int
    timer_fired: bool = True
    trigger_reason: str = ""


@router.post("/group_timing")
async def group_timing_deprecated(req: GroupTimingRequest, _auth=Depends(verify_token)):
    """[DEPRECATED] 使用 /group/message 替代。"""
    logger.warning("[DEPRECATED] /group_timing called by group=%s — migrate to /group/message", req.group_id)
    from core.timing_runtime import get_group_runtime

    runtime = get_group_runtime()
    result = await runtime.process_message(
        req.group_id,
        {
            "sender_id": req.sender_id,
            "sender_name": req.sender_name,
            "message": req.message,
            "message_id": req.message_id or "",
            "is_reply_to_bot": req.is_reply_to_bot,
        },
        session_name=req.session_name or "",
        bot_aliases=list(req.bot_aliases or []),
        trigger_reason=req.trigger_reason or "mentioned",
    )
    return result


@router.post("/group_timing/timer")
async def group_timing_timer(
    req: GroupTimingTimerRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """Timing Gate timer 回调——wait 到期，若 continue 则内部生成回复。"""
    from core.timing_runtime import get_group_runtime

    group_user_id = _normalize_group_session_id(req.group_id)
    recent_ctx = build_timing_recent_context(db, group_user_id, limit=5)
    release_clean_session_transaction(db, label="group_timer_before_runtime", logger=logger)

    runtime = get_group_runtime()
    t0 = _time.time()
    try:
        result = await runtime.handle_timer_fired(
            req.group_id,
            req.generation,
            trigger_reason=req.trigger_reason,
            recent_context=recent_ctx,
        )
        elapsed_ms = int((_time.time() - t0) * 1000)
        action = result.get("action", "no_reply")
        logger.info(
            "[TimingGate.timer] group=%s gen=%d ➜ %s latency=%dms",
            req.group_id,
            req.generation,
            action,
            elapsed_ms,
        )

        if action == "continue":
            group_user_id = _normalize_group_session_id(req.group_id)
            try:
                bridge = _current_bridge_provider()()
                source_message_ids = [
                    str(x) for x in (result.get("source_message_ids") or [])
                    if str(x).strip()
                ]
                memory_header, history_messages, _ctx_debug = build_chat_context(
                    db,
                    group_user_id,
                    user_id=group_user_id,
                    is_group=True,
                    group_id=req.group_id,
                    exclude_message_ids=source_message_ids,
                )
                # 从 runtime state 取回调时保留的 bot identity。
                timer_state = runtime._states.get(_normalize_group_session_id(req.group_id))
                timer_bot_id = (timer_state.bot_id if timer_state else "") or ""
                timer_bot_name = (timer_state.bot_name if timer_state else "") or ""
                timer_bot_aliases = list(timer_state.bot_aliases if timer_state else []) or list(
                    getattr(req, "bot_aliases", []) or []
                )

                timer_sender_id = str(getattr(req, "sender_id", "") or getattr(req, "user_id", "") or "")
                timer_identity_vars = build_identity_vars(
                    sender_id=timer_sender_id,
                    bot_name=timer_bot_name,
                    bot_aliases=timer_bot_aliases,
                    is_super_user=False,
                )
                bridge_meta = {
                    "chat_type": "group",
                    "user_id": group_user_id,
                    "session_id": group_user_id,
                    "is_group": True,
                    "is_superuser": False,
                    "history_header": memory_header,
                    "history_messages": history_messages,
                    "group_id": req.group_id,
                    "sender_id": timer_sender_id,
                    "trigger_reason": req.trigger_reason or "timer",
                    "timing_decision": "continue",
                    "source_message_ids": source_message_ids,
                    "context_debug": _ctx_debug,
                    "self_id": timer_bot_id,
                    "bot_id": timer_bot_id,
                    "bot_name": timer_bot_name,
                    "bot_aliases": timer_bot_aliases,
                    **timer_identity_vars,
                }
                chat_query = sanitize_prompt_text(
                    str(result.get("pending_text") or ""),
                    max_chars=MAX_QUERY_CHARS,
                )
                if not chat_query.strip():
                    chat_query = "timer 触发回复"
                enriched = f"<user_input>\n{chat_query}\n</user_input>"
                release_clean_session_transaction(db, label="group_timer_before_bridge", logger=logger)
                reply = await bridge.handle_message(
                    enriched,
                    session_id=group_user_id,
                    user_id=group_user_id,
                    metadata=bridge_meta,
                )
                answer = reply if isinstance(reply, str) else str(reply or "")
                result["reply"] = group_ingress_helpers.format_group_reply_for_transport(
                    answer,
                    max_chars=4000,
                )
                reply_meta_timer = group_ingress_helpers.pop_bridge_reply_meta(bridge, group_user_id)
                result["reply_meta"] = reply_meta_timer
                result["group_id"] = req.group_id
                if answer.strip():
                    duplicate = group_ingress_helpers.find_recent_duplicate_group_reply(
                        db,
                        group_user_id,
                        answer,
                    )
                    if duplicate:
                        agent_result = "duplicate_reply_suppressed"
                        group_ingress_helpers.log_group_no_reply(
                            db,
                            group_user_id,
                            chat_query,
                            agent_result,
                            "",
                        )
                        result["action"] = "no_reply"
                        result["reason"] = agent_result
                        result["duplicate_reply"] = duplicate
                        result["reply"] = ""
                        logger.info(
                            "[TimingGate.timer] duplicate reply suppressed group=%s prev=%s sim=%s",
                            req.group_id,
                            duplicate.get("previous_log_id"),
                            duplicate.get("similarity"),
                        )
                        return result
                    group_ingress_helpers.persist_group_bridge_reply(
                        db,
                        group_user_id=group_user_id,
                        sender_name="",
                        session_name="",
                        query=chat_query,
                        answer=answer,
                        bot_name=timer_bot_name or "nanobot",
                        source_message_ids=source_message_ids,
                        reply_meta=reply_meta_timer,
                    )
                    runtime.note_bot_replied(req.group_id)
                else:
                    agent_result = group_ingress_helpers.derive_group_agent_result(
                        bridge,
                        group_user_id,
                        reply_meta_timer,
                    )
                    group_ingress_helpers.log_group_no_reply(db, group_user_id, chat_query, agent_result, "")
                logger.info("[TimingGate.timer] reply group=%s len=%d", req.group_id, len(answer))
            except Exception as e:
                logger.error("[TimingGate.timer] bridge failed group=%s: %s", req.group_id, e)
                result["action"] = "no_reply"
    except Exception as e:
        result = {"action": "no_reply", "reason": f"error: {e}"}
    return result
