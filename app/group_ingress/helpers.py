"""群聊入口 helper，供 app 层复用，避免依赖 API 路由模块。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from core.context_builder import (
    build_chat_context,
    format_group_planner_message,
    sanitize_prompt_text,
)
from core.database import ChatLog, ConversationTurn
from core.group_runtime.ids import normalize_group_session_id
from core.settings_service import settings
from core.sqlite_retry import run_sqlite_locked_retry

logger = logging.getLogger("nanobot.group_ingress")

_MAX_SEGMENTS = 30
_MAX_MENTIONS = 20
_MAX_REPLY_CONTENT = 300
_MAX_SEGMENT_TEXT = 500
_MAX_MENTION_NICK = 80

_ALLOWED_SEGMENT_KEYS = {
    "image": ("file", "url", "file_id", "summary", "sub_type"),
    "mface": ("file", "url", "emoji_id", "summary", "key", "emoji_package_id"),
    "file": ("file", "name", "file_name", "file_size", "url", "file_id"),
    "forward": ("id", "summary"),
}


def safe_meta(meta_json: str) -> dict:
    try:
        data = json.loads(meta_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def short_text(text: str, limit: int = 400) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "...[截断]"


def is_html_reply(text: str) -> bool:
    value = str(text or "").lstrip().lower()
    return value.startswith(("<!doctype", "<html", "<article"))


def format_group_reply_for_transport(text: str, *, max_chars: int = 4000) -> str:
    """返回给 QQbot 的群聊回复。HTML 需要完整文档，不能按文本截断。"""
    value = str(text or "")
    if is_html_reply(value):
        return value
    return sanitize_prompt_text(value, max_chars=max_chars)


def normalize_files(files: list[str] | None) -> list[str]:
    return [file for file in (files or []) if isinstance(file, str) and file.strip()]


def get_group_talk_value(session_id: str) -> float:
    try:
        from core.expression_memory import get_stream_config, normalize_chat_stream_id

        raw = session_id.removeprefix("group_")
        cfg = get_stream_config(normalize_chat_stream_id(raw, chat_type="group", platform="qq"))
        return float(cfg.get("talk_value", 0.5))
    except Exception:
        return 0.5


def check_user_blocked(db, user_id: str, target_type: str = "private", group_id: str = "") -> bool:
    try:
        from core.database import UserBlockRule

        rules = db.query(UserBlockRule).filter(
            UserBlockRule.user_id == user_id,
            UserBlockRule.enabled == 1,
        ).all()
        norm_group = normalize_group_session_id(group_id) if group_id else ""
        for rule in rules:
            if rule.target_type in (target_type, "all"):
                if rule.target_type == "group" and rule.group_id:
                    if norm_group and normalize_group_session_id(rule.group_id) != norm_group:
                        continue
                return True
    except Exception as exc:
        logger.warning("[BlockRule] check failed user=%s group=%s: %s", user_id, group_id, exc)
    return False


def normalize_onebot_segments(req: Any) -> list[dict]:
    result: list[dict] = []
    for seg in getattr(req, "segments", [])[: _MAX_SEGMENTS]:
        if not isinstance(seg, dict):
            continue
        segment_type = str(seg.get("type", "")).strip()
        if not segment_type:
            continue
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        if segment_type == "text":
            text = str(data.get("text", ""))[:_MAX_SEGMENT_TEXT]
            result.append({"type": segment_type, "data": {"text": text}})
        elif segment_type == "at":
            result.append({"type": segment_type, "data": {"qq": str(data.get("qq", ""))[:20]}})
        elif segment_type == "reply":
            result.append({"type": segment_type, "data": {"id": str(data.get("id", ""))[:50]}})
        elif segment_type in _ALLOWED_SEGMENT_KEYS:
            keys = _ALLOWED_SEGMENT_KEYS[segment_type]
            filtered = {k: str(data.get(k, ""))[:500] for k in keys if data.get(k) is not None}
            result.append({"type": segment_type, "data": filtered})
    return result


def extract_mentions_from_segments(segments: list[dict], req: Any) -> list[dict]:
    bot_id = getattr(req, "bot_id", "") or getattr(req, "self_id", "")
    mentions: list[dict] = []
    seen: set[str] = set()
    for seg in segments:
        if seg.get("type") != "at":
            continue
        qq = str(seg.get("data", {}).get("qq", "")).strip()
        if not qq or qq == "all" or qq in seen:
            continue
        seen.add(qq)
        mentions.append({"user_id": qq, "is_bot": bool(bot_id and qq == bot_id), "nickname": ""})
    return mentions


def normalize_group_mentions(req: Any, segments: list[dict]) -> list[dict]:
    explicit = [
        {
            "user_id": str(m.get("user_id", "")).strip()[:20],
            "nickname": str(m.get("nickname", ""))[:_MAX_MENTION_NICK],
            "is_bot": bool(m.get("is_bot")),
        }
        for m in getattr(req, "mentions", [])
        if isinstance(m, dict) and str(m.get("user_id", "")).strip()
    ]
    derived = extract_mentions_from_segments(segments, req)
    merged: dict[str, dict] = {}
    for mention in derived + explicit:
        uid = mention["user_id"]
        if uid not in merged:
            merged[uid] = mention
        else:
            if mention["nickname"]:
                merged[uid]["nickname"] = mention["nickname"]
            if mention["is_bot"]:
                merged[uid]["is_bot"] = True
    return list(merged.values())[:_MAX_MENTIONS]


def normalize_group_reply_to(req: Any, segments: list[dict]) -> dict | None:
    req_reply_to = getattr(req, "reply_to", None)
    if req_reply_to and isinstance(req_reply_to, dict) and req_reply_to.get("message_id"):
        reply_to = req_reply_to
        return {
            "message_id": str(reply_to.get("message_id", ""))[:50],
            "sender_id": str(reply_to.get("sender_id", ""))[:20],
            "sender_name": str(reply_to.get("sender_name", ""))[:80],
            "content": str(reply_to.get("content", ""))[:_MAX_REPLY_CONTENT],
            "is_bot": bool(reply_to.get("is_bot")),
        }
    if getattr(req, "reply_to_message_id", None):
        bot_id = getattr(req, "bot_id", "") or getattr(req, "self_id", "")
        sender_id = str(getattr(req, "reply_to_sender_id", "") or "")[:20]
        return {
            "message_id": str(req.reply_to_message_id)[:50],
            "sender_id": sender_id,
            "sender_name": str(getattr(req, "reply_to_sender_name", "") or "")[:80],
            "content": str(getattr(req, "reply_to_content", "") or "")[:_MAX_REPLY_CONTENT],
            "is_bot": bool(getattr(req, "is_reply_to_bot", False) or (bot_id and sender_id == bot_id)),
        }
    for seg in segments:
        if seg.get("type") == "reply":
            message_id = str(seg.get("data", {}).get("id", "")).strip()
            if message_id:
                return {"message_id": message_id[:50], "sender_id": "", "sender_name": "", "content": "", "is_bot": False}
    return None


def derive_group_direction(req: Any, mentions: list[dict], reply_to: dict | None) -> dict:
    bot_id = getattr(req, "bot_id", "") or getattr(req, "self_id", "")
    direction = {
        "at_bot": bool(getattr(req, "is_at_bot", False)),
        "reply_to_bot": bool(getattr(req, "is_reply_to_bot", False)),
        "at_others": False,
        "reply_to_others": False,
        "directed_to_other": False,
        "mentions_bot": bool(getattr(req, "is_at_bot", False)),
        "mentions_others": False,
    }
    for mention in mentions:
        uid = str(mention.get("user_id") or "")
        is_bot = bool(mention.get("is_bot")) or bool(bot_id and uid == bot_id)
        if is_bot:
            direction["mentions_bot"] = True
            direction["at_bot"] = True
        elif uid:
            direction["mentions_others"] = True
            direction["at_others"] = True
    if reply_to:
        reply_sender_id = reply_to.get("sender_id") or ""
        is_reply_to_bot = (
            bool(reply_to.get("is_bot"))
            or bool(bot_id and reply_sender_id == bot_id)
            or bool(getattr(req, "is_reply_to_bot", False))
        )
        if is_reply_to_bot:
            direction["reply_to_bot"] = True
        elif reply_to.get("sender_id") or reply_to.get("message_id"):
            direction["reply_to_others"] = True

    explicit_other = bool(getattr(req, "is_directed_to_other", False))
    inferred_other = direction["at_others"] or direction["reply_to_others"]
    direction["directed_to_other"] = (
        (explicit_other or inferred_other)
        and not direction["at_bot"]
        and not direction["reply_to_bot"]
        and not direction["mentions_bot"]
    )
    return direction


def detect_group_bot_sender(req: Any) -> str:
    sender_id = str(getattr(req, "sender_id", "") or "").strip()
    self_id = str(getattr(req, "self_id", "") or "").strip()
    bot_id = str(getattr(req, "bot_id", "") or "").strip()
    if sender_id and sender_id in {self_id, bot_id}:
        return "current_bot"
    if bool(getattr(req, "sender_is_bot", False)):
        return "explicit_bot"
    meta = getattr(req, "client_meta", None) if isinstance(getattr(req, "client_meta", None), dict) else {}
    if meta.get("sender_is_bot") or meta.get("is_bot") or meta.get("sender_type") == "bot":
        return "client_meta"
    return ""


def build_group_message_meta(req: Any, registered_stickers: list[dict]) -> dict:
    segments = normalize_onebot_segments(req)
    mentions = normalize_group_mentions(req, segments)
    reply_to = normalize_group_reply_to(req, segments)
    directed = derive_group_direction(req, mentions, reply_to)
    bot_sender_kind = detect_group_bot_sender(req)
    return {
        "message_type": "group_message",
        "raw_message": str(getattr(req, "raw_message", "") or "")[:2000],
        "sender": {
            "id": str(getattr(req, "sender_id", "") or "")[:40],
            "name": str(getattr(req, "sender_name", "") or "")[:80],
            "is_bot": bool(bot_sender_kind),
            "bot_sender_kind": bot_sender_kind,
        },
        "segments": segments,
        "mentions": mentions,
        "reply_to": reply_to,
        "directed": directed,
        "bot": {
            "self_id": str(getattr(req, "self_id", "") or "")[:20],
            "bot_id": str(getattr(req, "bot_id", "") or getattr(req, "self_id", "") or "")[:20],
            "bot_name": str(getattr(req, "bot_name", "") or "")[:80],
        },
        "files": normalize_files(getattr(req, "files", None)),
        "client_meta": getattr(req, "client_meta", None) if isinstance(getattr(req, "client_meta", None), dict) else {},
    }


def safe_group_client_meta(req: Any) -> dict:
    return getattr(req, "client_meta", None) if isinstance(getattr(req, "client_meta", None), dict) else {}


def group_sticker_payloads(req: Any) -> list[dict]:
    meta = safe_group_client_meta(req)
    files = normalize_files(getattr(req, "files", None))
    raw_stickers = meta.get("stickers")
    payloads: list[dict] = []
    if isinstance(raw_stickers, list):
        for item in raw_stickers:
            if not isinstance(item, dict):
                continue
            file_ref = item.get("file_ref") or item.get("file") or item.get("url") or item.get("path") or ""
            if not isinstance(file_ref, str) or not file_ref.strip():
                continue
            payloads.append({**item, "file_ref": file_ref.strip()})
    message_type = str(meta.get("message_type") or "").lower()
    if not payloads and message_type in {"sticker", "emoji", "mface"}:
        for file_ref in files:
            payloads.append({"file_ref": file_ref, "source": "files"})
    return payloads


def render_segments_to_text(segments: list[dict], mentions: list[dict]) -> str:
    nick_by_id = {str(m.get("user_id", "")): str(m.get("nickname", "")) for m in mentions}
    parts: list[str] = []
    for seg in segments:
        segment_type = seg.get("type")
        data = seg.get("data") or {}
        if segment_type == "text":
            parts.append(str(data.get("text", "")))
        elif segment_type == "at":
            qq = str(data.get("qq", ""))
            label = nick_by_id.get(qq) or qq
            parts.append(f"@{label}")
        elif segment_type == "image":
            parts.append("[图片:1张]")
        elif segment_type == "mface":
            parts.append("[表情包]")
        elif segment_type == "file":
            name = str(data.get("name") or data.get("file_name", "文件"))
            parts.append(f"[文件:{name[:80]}]")
    return " ".join(p for p in parts if p and p.strip()).strip()


def build_group_message_text(req: Any) -> str:
    segments = normalize_onebot_segments(req)
    if segments:
        mentions = normalize_group_mentions(req, segments)
        rendered = render_segments_to_text(segments, mentions)
        if rendered:
            return rendered
    text = str(getattr(req, "message", "") or "").strip()
    if text:
        return text
    stickers = group_sticker_payloads(req)
    if stickers:
        hints = []
        for item in stickers[:3]:
            hint = str(item.get("name") or item.get("description") or "").strip()
            if hint:
                hints.append(hint)
        suffix = f" {' / '.join(hints)}" if hints else ""
        return f"[表情包]{suffix}"
    files = normalize_files(getattr(req, "files", None))
    if files:
        return f"[图片] {len(files)} 张"
    return ""


def register_group_stickers_from_message(db, req: Any, *, background_tasks: Any = None) -> list[dict]:
    payloads = group_sticker_payloads(req)
    if not payloads:
        return []
    from core.group_runtime.ids import normalize_group_stream_id
    from core.sticker_memory import auto_describe_sticker, register_sticker

    chat_stream_id = normalize_group_stream_id(req.group_id)
    registered: list[dict] = []
    describe_tasks = 0
    for item in payloads:
        file_ref = str(item.get("file_ref") or "").strip()
        if not file_ref:
            continue
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        emotions = item.get("emotions") if isinstance(item.get("emotions"), list) else []
        sticker = run_sqlite_locked_retry(
            lambda: register_sticker(
                db,
                chat_stream_id=chat_stream_id,
                file_ref=file_ref,
                sticker_hash=str(
                    item.get("hash")
                    or item.get("file_unique")
                    or item.get("file_id")
                    or item.get("emoji_id")
                    or item.get("id")
                    or ""
                ),
                send_code=str(item.get("send_code") or ""),
                name=str(item.get("name") or item.get("summary") or ""),
                description=str(item.get("description") or ""),
                tags=tags,
                emotions=emotions,
                source_type="auto",
                status=str(item.get("status") or "active"),
                meta={
                    "group_id": req.group_id,
                    "message_id": req.message_id or "",
                    "sender_id": req.sender_id or "",
                    "client_meta": item,
                },
            ),
            rollback=db.rollback,
            label="group_sticker_register",
            logger=logger,
        )
        registered.append(sticker)

        sticker_id = int(sticker.get("id") or 0)
        if sticker_id:
            if background_tasks is not None:
                from core.sticker_preview_jobs import cache_sticker_preview_bg

                background_tasks.add_task(cache_sticker_preview_bg, sticker_id)

        if (
            background_tasks is not None
            and settings.get_bool("sticker.auto_describe_enabled", True)
            and not sticker.get("description")
            and describe_tasks < max(0, settings.get_int("sticker.auto_describe_max_per_cycle", 3))
        ):
            background_tasks.add_task(auto_describe_sticker, sticker["id"])
            describe_tasks += 1
    return registered


def annotate_group_timing_event(
    db,
    ambient_log: ChatLog | None,
    result: dict,
    *,
    trigger_reason: str,
    latency_ms: int,
    mode: str = "message",
) -> None:
    if ambient_log is None:
        return
    try:
        meta = safe_meta(ambient_log.meta_json)
        error_type = str(result.get("error_type") or "")
        timing = {
            "mode": mode,
            "action": str(result.get("action") or "no_reply"),
            "delay_seconds": result.get("delay_seconds"),
            "reason": str(result.get("reason") or "")[:300],
            "generation": int(result.get("generation") or 0),
            "trigger_reason": trigger_reason,
            "latency_ms": int(result.get("latency_ms") or latency_ms or 0),
            "cooldown_ago": result.get("cooldown_ago"),
            "pending_count": result.get("pending_count"),
            "pending_text": str(result.get("pending_text") or "")[:500],
            "context_chars": result.get("context_chars"),
            "raw": short_text(str(result.get("raw") or ""), 1000),
            "error_type": error_type or None,
            "parse_error": error_type == "parse_error",
            "fallback_action": result.get("fallback_action") or (result.get("action") if error_type else None),
            "context_summary": short_text(str(result.get("context") or ""), 1200),
            "context": str(result.get("context") or "")[:8000],
            "hard_rule": result.get("hard_rule"),
            "directed_to_other": result.get("directed_to_other"),
            "scoring": result.get("timing_scoring"),
        }
        meta["timing_gate"] = {k: v for k, v in timing.items() if v not in ("", None)}
        def operation() -> None:
            ambient_log.meta_json = json.dumps(meta, ensure_ascii=False)
            db.commit()

        run_sqlite_locked_retry(
            operation,
            rollback=db.rollback,
            label="group_timing_event",
            logger=logger,
        )
    except Exception as exc:
        logger.warning("[TimingGate] annotate meta failed log_id=%s: %s", getattr(ambient_log, "id", None), exc)


def pop_bridge_reply_meta(bridge: Any, session_id: str) -> dict | None:
    if getattr(type(bridge), "pop_last_reply_meta", None) is None:
        return None
    try:
        meta = bridge.pop_last_reply_meta(session_id)
    except Exception as exc:
        logger.warning("[ReplyMeta] pop failed session=%s: %s", session_id, exc)
        return None
    return meta if isinstance(meta, dict) else None


def derive_group_agent_result(bridge: Any, session_id: str, reply_meta: dict | None = None) -> str:
    meta = reply_meta or {}
    agent_result = str(meta.get("_agent_result") or "")
    if meta.get("_no_reply") or agent_result == "no_reply_tool":
        return "no_reply_tool"
    if agent_result in (
        "fake_tool_call_claim",
        "structured_buffer_reply",
        "structured_buffer_no_reply",
        "prompt_v2_audit_failed",
    ):
        return agent_result

    if hasattr(bridge, "is_no_reply_session") and bridge.is_no_reply_session(session_id):
        return "no_reply_tool"
    if hasattr(bridge, "is_fake_tool_call_claim") and bridge.is_fake_tool_call_claim(session_id):
        return "fake_tool_call_claim"
    if hasattr(bridge, "is_no_tool_call") and bridge.is_no_tool_call(session_id):
        store = bridge._reply_meta_store() if hasattr(bridge, "_reply_meta_store") else {}
        stored_result = store.get(session_id, {}).get("_agent_result", "")
        if stored_result in ("structured_buffer_reply", "structured_buffer_no_reply"):
            return stored_result
        return "no_tool_call"
    return "no_tool_call"


def derive_group_trigger_reason(req: Any) -> str:
    text = (getattr(req, "message", "") or "").strip()
    if getattr(req, "is_at_bot", False):
        return "at_bot"
    if getattr(req, "is_reply_to_bot", False):
        return "reply_to_bot"
    if text.startswith(("/", "！", "!")):
        return "direct_call"
    if any(alias in text for alias in (getattr(req, "bot_aliases", None) or [])):
        return "bot_name_mentioned"
    return "ambient"


def normalize_reply_for_duplicate(text: str) -> str:
    import re

    return re.sub(r"[\s，。！？!?,.、:：;；\"'“”‘’`~～（）()【】\[\]{}<>《》\-—_]+", "", text or "").lower()


def find_recent_duplicate_group_reply(
    db,
    group_user_id: str,
    answer: str,
    *,
    min_chars: int = 60,
    threshold: float = 0.9,
    window_seconds: int = 300,
) -> dict | None:
    normalized = normalize_reply_for_duplicate(answer)
    if len(normalized) < min_chars:
        return None

    cutoff = datetime.now() - timedelta(seconds=window_seconds)
    rows = (
        db.query(ChatLog)
        .filter(ChatLog.session_id == group_user_id, ChatLog.role == "assistant", ChatLog.created_at >= cutoff)
        .order_by(ChatLog.created_at.desc(), ChatLog.id.desc())
        .limit(5)
        .all()
    )
    for row in rows:
        previous = normalize_reply_for_duplicate(row.content or "")
        if len(previous) < min_chars:
            continue
        similarity = SequenceMatcher(None, normalized, previous).ratio()
        if similarity >= threshold:
            return {
                "previous_log_id": row.id,
                "similarity": round(similarity, 4),
                "previous_created_at": row.created_at.isoformat() if row.created_at else "",
            }
    return None


def log_group_no_reply(db, user_id: str, query: str, agent_result: str, message_id: str = ""):
    def operation() -> None:
        db.add(ChatLog(
            user_id=user_id,
            session_id=user_id,
            role="system",
            content=f"[NO_SEND] agent_result={agent_result}",
            message_id=message_id,
            meta_json=json.dumps({
                "agent_result": agent_result,
                "no_send": True,
                "note": "群聊主流程未调用 reply/no_reply 工具，无消息发送",
                "source_message_id": message_id,
            }, ensure_ascii=False),
        ))
        db.commit()

    run_sqlite_locked_retry(
        operation,
        rollback=db.rollback,
        label="group_no_reply",
        logger=logger,
    )


def persist_group_bridge_reply(
    db,
    *,
    group_user_id: str,
    sender_name: str,
    session_name: str,
    query: str,
    answer: str,
    bot_name: str = "nanobot",
    message_id: str | None = None,
    source_message_ids: list[str] | None = None,
    reply_meta: dict | None = None,
) -> None:
    source_ids = list(source_message_ids or [])
    if message_id and message_id not in source_ids:
        source_ids.insert(0, message_id)
    source_ids_json = json.dumps(source_ids, ensure_ascii=False) if source_ids else "[]"
    meta = {"kind": "group_reply"}
    if reply_meta:
        meta["reply_meta"] = reply_meta

    def operation() -> None:
        db.add(ChatLog(
            user_id=group_user_id,
            session_id=group_user_id,
            role="assistant",
            content=answer,
            sender_name=(bot_name or "nanobot"),
            session_name=session_name or "",
            processed=1,
            meta_json=json.dumps(meta, ensure_ascii=False),
        ))
        db.add(ConversationTurn(
            user_id=group_user_id,
            session_id=group_user_id,
            role="user",
            content=sanitize_prompt_text(query, 300),
            source_message_ids_json=source_ids_json,
            meta_json=json.dumps({
                "kind": "chat",
                "source": "group_message",
                "sender_name": sender_name or "",
            }, ensure_ascii=False),
        ))
        db.add(ConversationTurn(
            user_id=group_user_id,
            session_id=group_user_id,
            role="assistant",
            content=answer,
            meta_json=json.dumps({"kind": "chat", "bot_name": (bot_name or "nanobot")}, ensure_ascii=False),
        ))
        db.commit()

    run_sqlite_locked_retry(
        operation,
        rollback=db.rollback,
        label="group_bridge_reply",
        logger=logger,
    )
