"""聊天落库 writer。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from api import chat_content_helpers
from core.database import ChatLog, ConversationTurn, SensitiveData
from core.sqlite_retry import run_sqlite_locked_retry

logger = logging.getLogger("nanobot.api")


@dataclass(frozen=True)
class ChatTurnPersistenceInput:
    user_id: str
    session_id: str
    query: str
    files: list[str] | None = None
    sender_name: str | None = None
    session_name: str | None = None
    message_id: str | None = None
    source_message_ids: list[str] | None = None
    client_meta: dict | None = None


def safe_meta(meta_json: str) -> dict:
    try:
        data = json.loads(meta_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _source_message_ids_json(req: ChatTurnPersistenceInput) -> str:
    source_ids = list(req.source_message_ids or [])
    if req.message_id and req.message_id not in source_ids:
        source_ids.insert(0, req.message_id)
    return json.dumps(source_ids, ensure_ascii=False) if source_ids else "[]"


def _turn_answer(answer: str, guardrail_status: str | None) -> tuple[str, str]:
    turn_answer = answer
    turn_answer_kind = "casual_template" if guardrail_status == "casual_template" else "chat"
    if not answer:
        return turn_answer, turn_answer_kind

    answer_lower = answer.lstrip()[:500].lower()
    html_markers = ("<!doctype", "<html", "<head", "<body", "<article", "<style")
    if any(answer_lower.startswith(marker) for marker in html_markers):
        return f"[HTML报告: 已渲染为图片/HTML，{len(answer)}字符]", "artifact_summary"
    if len(answer) > 2000:
        return answer[:2000] + "\n...[截断]", turn_answer_kind
    return turn_answer, turn_answer_kind


def persist_chat_turn(
    db: Session,
    req: ChatTurnPersistenceInput,
    answer: str,
    guardrail_status: str | None = None,
    *,
    assistant_meta: dict | None = None,
    assistant_processed: int | None = None,
    timing_meta: dict | None = None,
) -> int:
    """Persist a chat turn to both ChatLog (evolution) and ConversationTurn (context)."""
    is_injection = guardrail_status == "injection"
    is_silent = guardrail_status == "silent"
    processed_val = -1 if is_injection else 0
    assistant_processed_val = processed_val if assistant_processed is None else int(assistant_processed)
    archive_user_content = chat_content_helpers.build_chatlog_user_content(req.query, req.files)
    context_user_content = chat_content_helpers.build_conversation_user_content(req.query, req.files)

    if is_silent:
        archive_display_content = "[敏感数据]"
        context_display_content = "[敏感数据]"
    else:
        archive_display_content = "[安全提示: 检测到注入已被拦截]" if is_injection else archive_user_content
        context_display_content = "[安全提示: 检测到注入已被拦截]" if is_injection else context_user_content

    source_ids_json = _source_message_ids_json(req)
    meta = json.dumps(req.client_meta or {}, ensure_ascii=False)
    turn_answer, turn_answer_kind = _turn_answer(answer, guardrail_status)

    user_meta = safe_meta(meta)
    user_meta["kind"] = "chat"
    if timing_meta:
        user_meta["timing_gate"] = timing_meta

    assistant_turn_meta: dict[str, Any] = {"kind": turn_answer_kind}
    if timing_meta:
        assistant_turn_meta["timing_gate"] = timing_meta
    if assistant_meta:
        assistant_turn_meta.update(assistant_meta)

    assistant_chat_meta = dict(assistant_meta or {})
    if timing_meta:
        assistant_chat_meta["timing_gate"] = timing_meta

    def operation() -> None:
        if is_silent:
            db.add(SensitiveData(
                user_id=req.user_id,
                session_id=req.session_id,
                content=archive_user_content,
                guardrail_status="silent",
                sender_name=req.sender_name or "",
                session_name=req.session_name or "",
            ))
        db.add(ChatLog(
            user_id=req.user_id,
            session_id=req.session_id,
            role="user",
            content=archive_display_content,
            sender_name=req.sender_name or "",
            session_name=req.session_name or "",
            processed=processed_val,
            message_id=req.message_id,
            source_message_ids_json=source_ids_json,
            meta_json=json.dumps(user_meta, ensure_ascii=False),
        ))
        db.add(ChatLog(
            user_id=req.user_id,
            session_id=req.session_id,
            role="assistant",
            content=answer,
            sender_name="nanobot",
            session_name=req.session_name or "",
            processed=assistant_processed_val,
            meta_json=json.dumps(assistant_chat_meta, ensure_ascii=False),
        ))
        db.add(ConversationTurn(
            user_id=req.user_id,
            session_id=req.session_id,
            role="user",
            content=context_display_content,
            source_message_ids_json=source_ids_json,
            meta_json=json.dumps(user_meta, ensure_ascii=False),
        ))
        db.add(ConversationTurn(
            user_id=req.user_id,
            session_id=req.session_id,
            role="assistant",
            content=turn_answer,
            meta_json=json.dumps(assistant_turn_meta, ensure_ascii=False),
        ))
        db.commit()

    run_sqlite_locked_retry(
        operation,
        rollback=db.rollback,
        label="chat_turn_persist",
        logger=logger,
    )

    from core.evolution import _evolution_running
    if req.user_id in _evolution_running:
        return 0
    return db.query(ChatLog).filter(ChatLog.user_id == req.user_id, ChatLog.processed == 0).count()
