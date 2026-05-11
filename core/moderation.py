"""内容审核模块——ContentBlockRule 匹配 + no_reply/no_learn/no_context 决策。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("nanobot.moderation")


def _match_rule(pattern: str, message: str, match_type: str = "contains") -> bool:
    """按 match_type 匹配消息。"""
    if match_type == "regex":
        try:
            return bool(re.search(pattern, message))
        except re.error:
            logger.warning("[Moderation] invalid regex pattern: %s", pattern)
            return False
    elif match_type == "exact":
        return message == pattern
    else:
        return pattern in message


def check_message_moderation(
    message: str,
    *,
    chat_stream_id: str = "",
    rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """对给定消息匹配 ContentBlockRule，返回第一条命中的规则标记。

    返回 None 表示无规则命中。
    返回 dict 包含 {pattern, no_reply, no_learn, no_context, match_type, scope_type}。
    """
    if not message or not rules:
        return None

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        pattern = str(rule.get("pattern") or "")
        if not pattern:
            continue

        # scope 过滤
        scope = str(rule.get("scope_type") or "session")
        if scope == "session":
            rule_stream = str(rule.get("chat_stream_id") or "")
            # session 规则必须有明确的 chat_stream_id；空 chat_stream_id 不匹配任何消息
            if not chat_stream_id or not rule_stream or rule_stream != chat_stream_id:
                continue
        # scope == "global" → 不限制 chat_stream_id

        match_type = str(rule.get("match_type") or "contains")
        if _match_rule(pattern, message, match_type):
            return {
                "pattern": pattern,
                "match_type": match_type,
                "rule_id": rule.get("rule_id"),
                "category": str(rule.get("category") or "no_learn"),
                "reason": str(rule.get("reason") or ""),
                "scope_type": scope,
                "no_reply": bool(rule.get("no_reply", False)),
                "no_learn": bool(rule.get("no_learn", True)),
                "no_context": bool(rule.get("no_context", False)),
            }

    return None


def check_message_moderation_db(
    db,
    message: str,
    *,
    chat_stream_id: str = "",
) -> dict[str, Any] | None:
    """从 DB 加载 ContentBlockRule 后调用 check_message_moderation。
    在 SQL 层已过滤 scope：只查 global 或匹配当前 stream 的 session 规则。"""
    from core.database import ContentBlockRule
    from sqlalchemy import or_

    rows = (
        db.query(ContentBlockRule)
        .filter(
            ContentBlockRule.enabled == 1,
            or_(
                ContentBlockRule.scope_type == "global",
                ContentBlockRule.chat_stream_id == chat_stream_id,
            ),
        )
        .all()
    )
    if not rows:
        return None

    rules = [
        {
            "rule_id": r.id,
            "pattern": r.pattern,
            "match_type": r.match_type or "contains",
            "scope_type": r.scope_type or "session",
            "chat_stream_id": r.chat_stream_id or "",
            "no_reply": bool(r.no_reply),
            "no_learn": bool(r.no_learn),
            "no_context": bool(r.no_context),
            "category": r.category or "no_learn",
            "reason": r.reason or "",
            "enabled": True,
        }
        for r in rows
    ]
    return check_message_moderation(message, chat_stream_id=chat_stream_id, rules=rules)


def _safe_meta(meta_json: Any) -> dict:
    """安全解析 meta_json 字段。"""
    if isinstance(meta_json, dict):
        return meta_json
    if isinstance(meta_json, str):
        try:
            return json.loads(meta_json)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def is_no_learn_meta(meta_json: Any) -> bool:
    """检查 ChatLog.meta_json 是否标记为 no_learn。"""
    return bool(_safe_meta(meta_json).get("moderation", {}).get("no_learn", False))


def is_no_context_meta(meta_json: Any) -> bool:
    """检查 ChatLog.meta_json 是否标记为 no_context。"""
    return bool(_safe_meta(meta_json).get("moderation", {}).get("no_context", False))
