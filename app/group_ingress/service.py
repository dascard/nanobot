"""群聊统一入口服务。"""

from __future__ import annotations

import json
import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any

from app.group_ingress import helpers as h
from core.database import ChatLog, User
from core.moderation import check_message_moderation_db
from core.sqlite_retry import is_sqlite_locked_error
from core.sqlite_retry import run_sqlite_locked_retry

logger = logging.getLogger("nanobot.group_ingress")


@dataclass
class GroupIngressResult:
    action: str
    reply: str = ""
    reason: str = ""
    reply_meta: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)


class GroupIngressService:
    """处理 /group/message 的业务流程。"""

    def __init__(self, *, db: Any, background_tasks: Any = None, bridge_provider: Any = None):
        self.db = db
        self.background_tasks = background_tasks
        self.bridge_provider = bridge_provider

    async def handle(self, req: Any) -> dict:
        from core.timing_runtime import get_group_runtime

        db = self.db
        group_user_id = h.normalize_group_session_id(req.group_id)
        message_text = h.build_group_message_text(req)

        logger.info("[GroupMsg] recv group=%s sender=%s len=%d at=%s reply=%s",
                    req.group_id, req.sender_name, len(message_text or ""),
                    req.is_at_bot, req.is_reply_to_bot)

        if req.message_id:
            dup = db.query(ChatLog).filter(
                ChatLog.session_id == group_user_id,
                ChatLog.message_id == req.message_id,
                ChatLog.role == "ambient",
            ).first()
            if dup:
                logger.info("[GroupMsg] duplicate ignored group=%s message_id=%s", group_user_id, req.message_id)
                return {"action": "no_reply", "reason": "duplicate_message"}

        registered_stickers = h.register_group_stickers_from_message(
            db,
            req,
            background_tasks=self.background_tasks,
        )
        try:
            self._sync_group_user(group_user_id, req.session_name or "")
        except Exception as exc:
            if is_sqlite_locked_error(exc):
                logger.warning("[GroupMsg] db locked while syncing group user group=%s: %s", req.group_id, exc)
                return {"action": "no_reply", "reason": "db_locked:group_user_sync"}
            raise

        from core.context_builder import build_timing_recent_context
        recent_ctx = build_timing_recent_context(
            db, group_user_id, limit=5,
        )

        formatted = f"[{req.sender_name}]: {message_text}" if message_text else ""
        meta = h.build_group_message_meta(req, registered_stickers)
        if registered_stickers:
            meta["registered_sticker_ids"] = [item["id"] for item in registered_stickers]
        self._schedule_image_precache(
            meta.get("files"),
            group_id=req.group_id,
            message_id=req.message_id or "",
        )
        try:
            ambient_log = self._save_ambient_log(
                group_user_id=group_user_id,
                sender_name=req.sender_name,
                session_name=req.session_name,
                formatted=formatted,
                message_id=req.message_id,
                meta=meta,
            )
        except Exception as exc:
            if is_sqlite_locked_error(exc):
                logger.warning("[GroupMsg] db locked while saving ambient group=%s: %s", req.group_id, exc)
                return {"action": "no_reply", "reason": "db_locked:ambient_log"}
            raise
        logger.info("[GroupMsg] ambient_saved group=%s message_id=%s", group_user_id, req.message_id or "-")

        bot_sender_kind = str(meta.get("sender", {}).get("bot_sender_kind") or "")
        if bot_sender_kind:
            result = {
                "action": "no_reply",
                "reason": f"bot_sender:{bot_sender_kind}",
                "generation": 0,
                "hard_rule": "bot_sender_no_timing",
            }
            h.annotate_group_timing_event(
                db, ambient_log, result,
                trigger_reason="bot_sender",
                latency_ms=0,
            )
            return result

        if h.check_user_blocked(db, req.sender_id, target_type="group", group_id=req.group_id):
            logger.info("[GroupMsg] blocked group=%s sender=%s", req.group_id, req.sender_id)
            h.annotate_group_timing_event(
                db, ambient_log,
                {"action": "no_reply", "reason": "user_blocked", "generation": 0},
                trigger_reason="user_blocked",
                latency_ms=0,
            )
            return {"action": "no_reply", "reason": "user_blocked"}

        from core.group_runtime.ids import normalize_group_stream_id
        stream_id = normalize_group_stream_id(req.group_id)
        mod_result = check_message_moderation_db(db, message_text, chat_stream_id=stream_id)
        if mod_result:
            meta["moderation"] = {
                "matched": True,
                "match_type": "content_rule",
                "pattern": mod_result["pattern"],
                "rule_id": mod_result.get("rule_id"),
                "category": mod_result.get("category", ""),
                "rule_match_type": mod_result.get("match_type", "contains"),
                "scope_type": mod_result.get("scope_type", ""),
                "reason": mod_result.get("reason", ""),
                "no_reply": mod_result["no_reply"],
                "no_learn": mod_result["no_learn"],
                "no_context": mod_result["no_context"],
            }

            def operation() -> None:
                ambient_log.meta_json = json.dumps(meta, ensure_ascii=False)
                db.commit()

            run_sqlite_locked_retry(
                operation,
                rollback=db.rollback,
                label="group_moderation_meta",
                logger=logger,
            )
            if mod_result["no_reply"]:
                logger.info("[GroupMsg] content-blocked group=%s pattern=%s",
                            req.group_id, mod_result["pattern"])
                h.annotate_group_timing_event(
                    db, ambient_log,
                    {"action": "no_reply", "reason": "content_blocked", "generation": 0},
                    trigger_reason="content_blocked",
                    latency_ms=0,
                )
                return {"action": "no_reply", "reason": "content_blocked"}

        reason = h.derive_group_trigger_reason(req)
        logger.info("[GroupMsg] trigger=%s enter_timing=true", reason)

        runtime = get_group_runtime()
        t0 = _time.time()
        try:
            ambient_meta = meta
            result = await runtime.process_message(
                req.group_id,
                {
                    "sender_id": req.sender_id,
                    "sender_name": req.sender_name,
                    "message": message_text,
                    "message_id": req.message_id or "",
                    "is_reply_to_bot": bool(ambient_meta.get("directed", {}).get("reply_to_bot")),
                    "is_at_bot": bool(ambient_meta.get("directed", {}).get("at_bot")),
                    "segments": ambient_meta.get("segments", []),
                    "mentions": ambient_meta.get("mentions", []),
                    "reply_to": ambient_meta.get("reply_to"),
                    "directed": ambient_meta.get("directed", {}),
                    "is_directed_to_other": bool(ambient_meta.get("directed", {}).get("directed_to_other")),
                    "self_id": ambient_meta.get("bot", {}).get("self_id", ""),
                    "bot_id": ambient_meta.get("bot", {}).get("bot_id", ""),
                    "bot_name": ambient_meta.get("bot", {}).get("bot_name", ""),
                },
                session_name=req.session_name or "",
                bot_aliases=list(req.bot_aliases or []),
                trigger_reason=reason,
                recent_context=recent_ctx,
                talk_value=h.get_group_talk_value(group_user_id),
            )
            elapsed_ms = int((_time.time() - t0) * 1000)
            action = result.get("action", "no_reply")
            h.annotate_group_timing_event(
                db, ambient_log, result,
                trigger_reason=reason,
                latency_ms=elapsed_ms,
            )

            logger.info(
                "[GroupMsg] group=%s trigger=%s -> %s delay=%s gen=%d latency=%dms cooldown=%.0fs reason=%.80s",
                req.group_id, reason, action,
                result.get("delay_seconds"), result.get("generation", 0),
                elapsed_ms, result.get("cooldown_ago", 0) or 0,
                str(result.get("reason", ""))[:80],
            )

            if action == "continue":
                return await self._continue_to_bridge(
                    req=req,
                    result=result,
                    reason=reason,
                    group_user_id=group_user_id,
                    message_text=message_text,
                    ambient_meta=ambient_meta,
                    runtime=runtime,
                )

            return {
                "action": action,
                "delay_seconds": result.get("delay_seconds"),
                "generation": result.get("generation", 0),
                "reason": str(result.get("reason", ""))[:120],
            }

        except Exception as exc:
            logger.warning("[GroupMsg] group=%s FAILED: %s", req.group_id, exc)
            return {"action": "no_reply", "reason": f"error: {exc}"}

    async def _continue_to_bridge(
        self,
        *,
        req: Any,
        result: dict,
        reason: str,
        group_user_id: str,
        message_text: str,
        ambient_meta: dict,
        runtime: Any,
    ) -> dict:
        db = self.db
        try:
            if self.bridge_provider is None:
                from nanobot_kt.bridge import get_bridge

                bridge = get_bridge()
            else:
                bridge = self.bridge_provider()
            source_message_ids = [
                str(x) for x in (result.get("source_message_ids") or [])
                if str(x).strip()
            ]
            bridge_files = self._collect_bridge_files(
                group_user_id=group_user_id,
                source_message_ids=source_message_ids,
                ambient_meta=ambient_meta,
            )
            chat_query = str(result.get("pending_text") or "").strip()
            if not chat_query:
                chat_query = h.format_group_planner_message(
                    sender_name=req.sender_name,
                    content=message_text,
                    message_id=req.message_id or "",
                )
                source_message_ids = [req.message_id] if req.message_id else []
            memory_header, history_messages, ctx_debug = h.build_chat_context(
                db, group_user_id, user_id=group_user_id,
                is_group=True, group_id=req.group_id,
                exclude_message_ids=source_message_ids,
                current_user_input=chat_query,
            )
            from core.identity import build_identity_vars
            sender_id = str(getattr(req, "sender_id", "") or getattr(req, "user_id", "") or "")
            identity_vars = build_identity_vars(
                sender_id=sender_id,
                bot_name=ambient_meta.get("bot", {}).get("bot_name", ""),
                bot_aliases=list(req.bot_aliases or []),
            )
            bridge_meta = {
                "chat_type": "group",
                "user_id": group_user_id,
                "session_id": group_user_id,
                "sender_name": req.sender_name,
                "sender_id": sender_id,
                "is_group": True,
                "history_header": memory_header,
                "history_messages": history_messages,
                "group_id": req.group_id,
                "session_name": req.session_name or "",
                "trigger_reason": reason,
                "message_id": req.message_id or "",
                "files": bridge_files,
                "timing_decision": "continue",
                "source_message_ids": source_message_ids,
                "context_debug": ctx_debug,
                "self_id": ambient_meta.get("bot", {}).get("self_id", ""),
                "bot_id": ambient_meta.get("bot", {}).get("bot_id", ""),
                "bot_name": ambient_meta.get("bot", {}).get("bot_name", ""),
                "bot_aliases": list(req.bot_aliases or []),
                **identity_vars,
            }
            enriched = f"<user_input>\n{chat_query}\n</user_input>"

            reply = await bridge.handle_message(
                enriched, session_id=group_user_id, user_id=group_user_id,
                metadata=bridge_meta,
            )
            answer = reply if isinstance(reply, str) else str(reply or "")
            reply_meta = h.pop_bridge_reply_meta(bridge, group_user_id)
            if answer.strip():
                duplicate = h.find_recent_duplicate_group_reply(db, group_user_id, answer)
                if duplicate:
                    agent_result = "duplicate_reply_suppressed"
                    h.log_group_no_reply(db, group_user_id, chat_query, agent_result, req.message_id)
                    return {
                        "action": "no_reply",
                        "reason": agent_result,
                        "duplicate_reply": duplicate,
                        "generation": result.get("generation", 0),
                    }
                h.persist_group_bridge_reply(
                    db,
                    group_user_id=group_user_id,
                    sender_name=req.sender_name,
                    session_name=req.session_name or "",
                    query=chat_query,
                    answer=answer,
                    bot_name=ambient_meta.get("bot", {}).get("bot_name", "") or "nanobot",
                    message_id=req.message_id,
                    source_message_ids=source_message_ids,
                    reply_meta=reply_meta,
                )
                runtime.note_bot_replied(req.group_id)
            else:
                agent_result = h.derive_group_agent_result(bridge, group_user_id, reply_meta)
                h.log_group_no_reply(db, group_user_id, chat_query, agent_result, req.message_id)
                if agent_result == "prompt_v2_audit_failed":
                    return {
                        "action": "no_reply",
                        "reply": "",
                        "reply_meta": reply_meta,
                        "generation": result.get("generation", 0),
                        "reason": agent_result,
                        "diagnostics": {
                            "timing_action": result.get("action", "continue"),
                            "agent_result": agent_result,
                        },
                    }
            return {
                "action": "continue",
                "reply": h.format_group_reply_for_transport(answer, max_chars=4000),
                "reply_meta": reply_meta,
                "generation": result.get("generation", 0),
                "reason": str(result.get("reason", ""))[:120],
            }
        except Exception as exc:
            logger.error("[GroupMsg] bridge failed group=%s: %s", req.group_id, exc)
            return {"action": "no_reply", "reason": f"bridge_error: {exc}"}

    def _sync_group_user(self, group_user_id: str, session_name: str) -> None:
        db = self.db

        def operation() -> None:
            user = db.query(User).filter(User.id == group_user_id).first()
            if not user:
                db.add(User(id=group_user_id, name=session_name or ""))
                db.commit()
                return
            if session_name and user.name != session_name:
                user.name = session_name
                db.commit()

        run_sqlite_locked_retry(
            operation,
            rollback=db.rollback,
            label="group_user_sync",
            logger=logger,
        )

    def _save_ambient_log(
        self,
        *,
        group_user_id: str,
        sender_name: str,
        session_name: str,
        formatted: str,
        message_id: str,
        meta: dict,
    ) -> ChatLog:
        db = self.db

        def operation() -> ChatLog:
            ambient_log = ChatLog(
                user_id=group_user_id,
                session_id=group_user_id,
                sender_name=sender_name,
                session_name=session_name,
                role="ambient",
                content=formatted,
                processed=1,
                message_id=message_id,
                meta_json=json.dumps(meta, ensure_ascii=False),
            )
            db.add(ambient_log)
            db.commit()
            db.refresh(ambient_log)
            return ambient_log

        return run_sqlite_locked_retry(
            operation,
            rollback=db.rollback,
            label="group_ambient_log",
            logger=logger,
        )

    def _schedule_image_precache(self, files: Any, *, group_id: str, message_id: str) -> None:
        normalized = h.normalize_files(files)
        if not normalized or self.background_tasks is None:
            return
        self.background_tasks.add_task(
            _precache_group_images_bg,
            normalized,
            group_id=group_id,
            message_id=message_id,
        )

    def _collect_bridge_files(
        self,
        *,
        group_user_id: str,
        source_message_ids: list[str],
        ambient_meta: dict,
    ) -> list[str]:
        files: list[str] = []

        def add_many(values: Any) -> None:
            for item in h.normalize_files(values):
                if item not in files:
                    files.append(item)

        add_many(ambient_meta.get("files"))
        if not source_message_ids:
            return files

        rows = (
            self.db.query(ChatLog)
            .filter(
                ChatLog.session_id == group_user_id,
                ChatLog.role == "ambient",
                ChatLog.message_id.in_(source_message_ids),
            )
            .all()
        )
        for row in rows:
            add_many(h.safe_meta(row.meta_json).get("files"))
        return files


def _precache_group_images_bg(files: list[str], *, group_id: str, message_id: str) -> None:
    from nanobot_kt.image_pipeline import precache_image_sources

    results = precache_image_sources(
        files,
        source_type="group_message",
        source_name_prefix=f"group_{group_id}_{message_id or 'message'}",
    )
    ok_count = sum(1 for item in results if item.get("ok"))
    if ok_count < len(results):
        logger.warning(
            "[GroupMsg] image precache partial group=%s message_id=%s ok=%d total=%d",
            group_id,
            message_id or "-",
            ok_count,
            len(results),
        )
