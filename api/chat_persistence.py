"""聊天落库 writer。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.orm import Session

from api import chat_content_helpers, chat_recovery
from core.db import ChatPersistenceRepositoryPort, chat_persistence_repository
from core.inbound_idempotency import CompletedInboundResponse, InboundClaimKey
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
    # 私聊/群聊;块式会话记忆仅对 private 归块,group 完全短路。
    chat_type: str = "private"
    event_time: str = ""


@dataclass(frozen=True)
class ClaimedChatTurnPersistenceResult:
    pending: int
    completion: CompletedInboundResponse


@dataclass(frozen=True)
class _PreparedChatTurn:
    is_silent: bool
    processed_val: int
    assistant_processed_val: int
    archive_user_content: str
    archive_display_content: str
    context_display_content: str
    source_ids_json: str
    user_meta: dict[str, Any]
    assistant_turn_meta: dict[str, Any]
    assistant_chat_meta: dict[str, Any]
    turn_answer: str


def safe_meta(meta_json: str) -> dict:
    try:
        data = json.loads(meta_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _source_message_ids_json(
    req: ChatTurnPersistenceInput,
    *,
    message_id: str | None = None,
) -> str:
    source_ids = list(req.source_message_ids or [])
    canonical_message_id = req.message_id if message_id is None else message_id
    if canonical_message_id and canonical_message_id not in source_ids:
        source_ids.insert(0, canonical_message_id)
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


def _prepare_chat_turn(
    req: ChatTurnPersistenceInput,
    answer: str,
    guardrail_status: str | None,
    *,
    assistant_meta: dict | None,
    assistant_processed: int | None,
    timing_meta: dict | None,
    message_id: str | None = None,
    user_kind: str = "chat",
) -> _PreparedChatTurn:
    is_injection = guardrail_status == "injection"
    is_silent = guardrail_status == "silent"
    processed_val = -1 if is_injection else 0
    assistant_processed_val = (
        processed_val if assistant_processed is None else int(assistant_processed)
    )
    archive_user_content = chat_content_helpers.build_chatlog_user_content(
        req.query,
        req.files,
    )
    context_user_content = chat_content_helpers.build_conversation_user_content(
        req.query,
        req.files,
    )
    if is_silent:
        archive_display_content = "[敏感数据]"
        context_display_content = "[敏感数据]"
    else:
        archive_display_content = (
            "[安全提示: 检测到注入已被拦截]"
            if is_injection
            else archive_user_content
        )
        context_display_content = (
            "[安全提示: 检测到注入已被拦截]"
            if is_injection
            else context_user_content
        )

    turn_answer, turn_answer_kind = _turn_answer(answer, guardrail_status)
    user_meta = safe_meta(json.dumps(req.client_meta or {}, ensure_ascii=False))
    user_meta["kind"] = user_kind
    if req.event_time:
        user_meta["event_time"] = str(req.event_time)
    if req.sender_name:
        user_meta["sender_name"] = str(req.sender_name)
    if req.session_name:
        user_meta["session_name"] = str(req.session_name)
    if req.message_id:
        user_meta["current_message_id"] = str(req.message_id)
    if timing_meta:
        from core.private_timing import get_effort_constraint

        effort_constraint = get_effort_constraint(
            str(timing_meta.get("effort") or "")
        )
        if effort_constraint:
            user_meta["effort_constraint"] = effort_constraint
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

    return _PreparedChatTurn(
        is_silent=is_silent,
        processed_val=processed_val,
        assistant_processed_val=assistant_processed_val,
        archive_user_content=archive_user_content,
        archive_display_content=archive_display_content,
        context_display_content=context_display_content,
        source_ids_json=_source_message_ids_json(req, message_id=message_id),
        user_meta=user_meta,
        assistant_turn_meta=assistant_turn_meta,
        assistant_chat_meta=assistant_chat_meta,
        turn_answer=turn_answer,
    )


def _pending_chat_log_count(
    repository: ChatPersistenceRepositoryPort,
    user_id: str,
) -> int:
    from core.evolution import _evolution_running

    if user_id in _evolution_running:
        return 0
    return repository.count_pending_chat_logs(user_id)


def _assign_block_safely(
    db: Session,
    *,
    session_id: str,
    user_id: str,
    chat_type: str,
    turns: list[Any],
) -> None:
    """把新增对话 turn 归入块;best-effort,失败降级跳过(不阻塞回复)。

    kill-switch 关闭时零开销直接返回;开启时在 SAVEPOINT 内归块,归块出错只回滚
    savepoint(已 flush 的 turn 不受影响),外层照常提交。见块式会话记忆 spec §8。
    """

    from app.session_memory.blocks import (
        assign_turns_to_block,
        is_block_memory_enabled,
    )

    if not is_block_memory_enabled(session_id):
        return

    try:
        with db.begin_nested():
            assign_turns_to_block(
                db,
                session_id=session_id,
                user_id=user_id,
                chat_type=chat_type,
                turns=turns,
            )
    except Exception:
        logger.warning(
            "[block] 归块失败,降级跳过(不阻塞回复) session=%s",
            session_id,
            exc_info=True,
        )


def ensure_private_request_journal(
    db: Session,
    req: ChatTurnPersistenceInput,
    *,
    key: InboundClaimKey | None,
    request_sha256: str,
) -> Any | None:
    """为非空私聊 claim 建立或验证唯一 request journal。"""

    if key is None:
        return None
    if type(key) is not InboundClaimKey or key.chat_type != "private":
        raise TypeError("key 必须是 private InboundClaimKey 或 null")
    repository = chat_persistence_repository(db)

    def operation() -> int:
        loaded = chat_recovery.load_private_request_journal(
            repository,
            key=key,
            request_sha256=request_sha256,
        )
        if loaded is not None:
            journal = loaded[0]
            if not str(journal.content or ""):
                journal.content = chat_content_helpers.build_chatlog_user_content(
                    req.query,
                    req.files,
                )
            row_id = int(journal.id)
            repository.commit()
            return row_id

        meta = chat_recovery.attach_private_request_fingerprint(
            {"kind": chat_recovery.REQUEST_JOURNAL_KIND},
            request_sha256,
        )
        row = repository.add_chat_log(
            user_id=req.user_id,
            session_id=key.session_id,
            role="user",
            content=chat_content_helpers.build_chatlog_user_content(
                req.query,
                req.files,
            ),
            sender_name=req.sender_name or "",
            session_name=req.session_name or "",
            processed=1,
            message_id=key.message_id,
            source_message_ids_json=_source_message_ids_json(
                req,
                message_id=key.message_id,
            ),
            meta_json=json.dumps(meta, ensure_ascii=False),
        )
        repository.commit()
        return int(row.id)

    try:
        row_id = run_sqlite_locked_retry(
            operation,
            rollback=repository.rollback,
            label="private_request_journal",
            logger=logger,
        )
    except BaseException:
        repository.rollback()
        raise
    row = repository.get_chat_log(row_id)
    if row is None:
        raise chat_recovery.PrivateRecoveryCorruptError(
            "private request journal 提交后不可见"
        )
    return row


def persist_claimed_chat_turn(
    db: Session,
    req: ChatTurnPersistenceInput,
    answer: str,
    guardrail_status: str | None = None,
    *,
    key: InboundClaimKey,
    request_sha256: str,
    completion: CompletedInboundResponse,
    assistant_meta: dict | None = None,
    assistant_processed: int | None = None,
    timing_meta: dict | None = None,
) -> ClaimedChatTurnPersistenceResult:
    """原子提交私聊业务行与 recoverable completion。"""

    if type(key) is not InboundClaimKey or key.chat_type != "private":
        raise TypeError("key 必须是 private InboundClaimKey")
    if type(completion) is not CompletedInboundResponse:
        raise TypeError("completion 必须是 CompletedInboundResponse")
    if completion.outcome == "respond" and completion.reply != answer:
        raise ValueError("respond completion 必须与 answer 一致")

    prepared = _prepare_chat_turn(
        req,
        answer,
        guardrail_status,
        assistant_meta=assistant_meta,
        assistant_processed=assistant_processed,
        timing_meta=timing_meta,
        message_id=key.message_id,
        user_kind=chat_recovery.REQUEST_JOURNAL_KIND,
    )
    repository = chat_persistence_repository(db)

    def operation() -> ClaimedChatTurnPersistenceResult:
        loaded = chat_recovery.load_private_request_journal(
            repository,
            key=key,
            request_sha256=request_sha256,
        )
        if loaded is None:
            raise chat_recovery.PrivateRecoveryCorruptError(
                "claimed chat turn 缺少 private request journal"
            )
        journal, journal_meta = loaded

        if prepared.is_silent:
            repository.add_sensitive_data(
                user_id=req.user_id,
                session_id=key.session_id,
                content=prepared.archive_user_content,
                guardrail_status="silent",
                sender_name=req.sender_name or "",
                session_name=req.session_name or "",
            )

        journal.user_id = req.user_id
        journal.session_id = key.session_id
        journal.content = prepared.archive_display_content
        journal.sender_name = req.sender_name or ""
        journal.session_name = req.session_name or ""
        journal.processed = prepared.processed_val
        journal.message_id = key.message_id
        journal.source_message_ids_json = prepared.source_ids_json

        assistant_chat_meta = dict(prepared.assistant_chat_meta)
        assistant_chat_meta.setdefault(
            "kind",
            prepared.assistant_turn_meta.get("kind", "chat"),
        )
        repository.add_chat_log(
            user_id=req.user_id,
            session_id=key.session_id,
            role="assistant",
            content=answer,
            sender_name="nanobot",
            session_name=req.session_name or "",
            processed=prepared.assistant_processed_val,
            message_id=key.message_id,
            meta_json=json.dumps(assistant_chat_meta, ensure_ascii=False),
        )
        user_turn = repository.add_conversation_turn(
            user_id=req.user_id,
            session_id=key.session_id,
            role="user",
            content=prepared.context_display_content,
            source_message_ids_json=prepared.source_ids_json,
            meta_json=json.dumps(prepared.user_meta, ensure_ascii=False),
        )
        assistant_turn = repository.add_conversation_turn(
            user_id=req.user_id,
            session_id=key.session_id,
            role="assistant",
            content=prepared.turn_answer,
            meta_json=json.dumps(prepared.assistant_turn_meta, ensure_ascii=False),
        )
        repository.flush()
        _assign_block_safely(
            db,
            session_id=key.session_id,
            user_id=req.user_id,
            chat_type="private",
            turns=[user_turn, assistant_turn],
        )
        pending = _pending_chat_log_count(repository, req.user_id)
        final_completion = replace(completion, unprocessed_logs=pending)
        completed_journal_meta = dict(prepared.user_meta)
        completed_journal_meta[chat_recovery.REQUEST_META_FIELD] = dict(
            journal_meta[chat_recovery.REQUEST_META_FIELD]
        )
        journal.meta_json = json.dumps(
            chat_recovery.attach_private_completion_recovery(
                completed_journal_meta,
                key=key,
                request_sha256=request_sha256,
                completion=final_completion,
            ),
            ensure_ascii=False,
        )
        repository.commit()
        return ClaimedChatTurnPersistenceResult(
            pending=pending,
            completion=final_completion,
        )

    try:
        return run_sqlite_locked_retry(
            operation,
            rollback=repository.rollback,
            label="claimed_chat_turn_persist",
            logger=logger,
        )
    except BaseException:
        repository.rollback()
        raise


def persist_private_claim_completion(
    db: Session,
    req: ChatTurnPersistenceInput,
    *,
    key: InboundClaimKey,
    request_sha256: str,
    completion: CompletedInboundResponse,
    journal_content: str | None = None,
    journal_processed: int | None = None,
) -> CompletedInboundResponse:
    """只在 request journal 上原子保存非回复型完成结果。"""

    if type(key) is not InboundClaimKey or key.chat_type != "private":
        raise TypeError("key 必须是 private InboundClaimKey")
    if type(completion) is not CompletedInboundResponse:
        raise TypeError("completion 必须是 CompletedInboundResponse")
    if completion.outcome == "respond":
        raise ValueError("respond completion 必须通过 persist_claimed_chat_turn 保存")
    repository = chat_persistence_repository(db)

    def operation() -> CompletedInboundResponse:
        loaded = chat_recovery.load_private_request_journal(
            repository,
            key=key,
            request_sha256=request_sha256,
        )
        if loaded is None:
            raise chat_recovery.PrivateRecoveryCorruptError(
                "claim completion 缺少 private request journal"
            )
        journal, journal_meta = loaded
        if chat_recovery.RECOVERY_META_FIELD in journal_meta:
            existing = chat_recovery.decode_private_completion_recovery(
                journal_meta,
                key=key,
                request_sha256=request_sha256,
            )
            if existing != completion:
                raise chat_recovery.PrivateRecoveryCorruptError(
                    "private request journal 已存在冲突 completion"
                )
            repository.commit()
            return existing

        completed_meta = safe_meta(
            json.dumps(req.client_meta or {}, ensure_ascii=False)
        )
        completed_meta["kind"] = chat_recovery.REQUEST_JOURNAL_KIND
        completed_meta[chat_recovery.REQUEST_META_FIELD] = dict(
            journal_meta[chat_recovery.REQUEST_META_FIELD]
        )
        journal.meta_json = json.dumps(
            chat_recovery.attach_private_completion_recovery(
                completed_meta,
                key=key,
                request_sha256=request_sha256,
                completion=completion,
            ),
            ensure_ascii=False,
        )
        journal.user_id = req.user_id
        journal.session_id = key.session_id
        journal.sender_name = req.sender_name or ""
        journal.session_name = req.session_name or ""
        journal.message_id = key.message_id
        journal.source_message_ids_json = _source_message_ids_json(
            req,
            message_id=key.message_id,
        )
        if journal_content is not None:
            journal.content = str(journal_content)
        if journal_processed is not None:
            journal.processed = int(journal_processed)
        repository.commit()
        return completion

    try:
        return run_sqlite_locked_retry(
            operation,
            rollback=repository.rollback,
            label="private_claim_completion",
            logger=logger,
        )
    except BaseException:
        repository.rollback()
        raise


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
    prepared = _prepare_chat_turn(
        req,
        answer,
        guardrail_status,
        assistant_meta=assistant_meta,
        assistant_processed=assistant_processed,
        timing_meta=timing_meta,
    )
    repository = chat_persistence_repository(db)

    def operation() -> None:
        if prepared.is_silent:
            repository.add_sensitive_data(
                user_id=req.user_id,
                session_id=req.session_id,
                content=prepared.archive_user_content,
                guardrail_status="silent",
                sender_name=req.sender_name or "",
                session_name=req.session_name or "",
            )
        repository.add_chat_log(
            user_id=req.user_id,
            session_id=req.session_id,
            role="user",
            content=prepared.archive_display_content,
            sender_name=req.sender_name or "",
            session_name=req.session_name or "",
            processed=prepared.processed_val,
            message_id=req.message_id,
            source_message_ids_json=prepared.source_ids_json,
            meta_json=json.dumps(prepared.user_meta, ensure_ascii=False),
        )
        repository.add_chat_log(
            user_id=req.user_id,
            session_id=req.session_id,
            role="assistant",
            content=answer,
            sender_name="nanobot",
            session_name=req.session_name or "",
            processed=prepared.assistant_processed_val,
            meta_json=json.dumps(prepared.assistant_chat_meta, ensure_ascii=False),
        )
        user_turn = repository.add_conversation_turn(
            user_id=req.user_id,
            session_id=req.session_id,
            role="user",
            content=prepared.context_display_content,
            source_message_ids_json=prepared.source_ids_json,
            meta_json=json.dumps(prepared.user_meta, ensure_ascii=False),
        )
        assistant_turn = repository.add_conversation_turn(
            user_id=req.user_id,
            session_id=req.session_id,
            role="assistant",
            content=prepared.turn_answer,
            meta_json=json.dumps(prepared.assistant_turn_meta, ensure_ascii=False),
        )
        repository.flush()
        _assign_block_safely(
            db,
            session_id=req.session_id,
            user_id=req.user_id,
            chat_type=req.chat_type,
            turns=[user_turn, assistant_turn],
        )
        repository.commit()

    run_sqlite_locked_retry(
        operation,
        rollback=repository.rollback,
        label="chat_turn_persist",
        logger=logger,
    )

    return _pending_chat_log_count(repository, req.user_id)
