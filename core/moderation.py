"""内容审核模块——ContentBlockRule 匹配 + no_reply/no_learn/no_context 决策。"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("nanobot.moderation")


def check_message_moderation(
    message: str,
    *,
    chat_stream_id: str = "",
    rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """对给定消息匹配 ContentBlockRule，返回第一条命中的规则标记。

    返回 None 表示无规则命中。
    返回 dict 包含 {pattern, no_reply, no_learn, no_context}。
    可通过 rules= 参数传入规则列表（适合测试/无 DB 场景）。
    """
    if not message or not rules:
        return None

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        pattern = str(rule.get("pattern") or "")
        if not pattern:
            continue
        scope = str(rule.get("scope_type") or "session")
        if scope == "session" and chat_stream_id:
            rule_stream = str(rule.get("chat_stream_id") or "")
            if rule_stream and rule_stream != chat_stream_id:
                continue
        try:
            if re.search(pattern, message):
                return {
                    "pattern": pattern,
                    "no_reply": bool(rule.get("no_reply", False)),
                    "no_learn": bool(rule.get("no_learn", True)),
                    "no_context": bool(rule.get("no_context", False)),
                    "scope_type": scope,
                }
        except re.error:
            logger.warning("[Moderation] invalid regex pattern: %s", pattern)
            continue

    return None


def check_message_moderation_db(
    db,
    message: str,
    *,
    chat_stream_id: str = "",
) -> dict[str, Any] | None:
    """从 DB 加载 ContentBlockRule 后调用 check_message_moderation。"""
    from core.database import ContentBlockRule

    rows = (
        db.query(ContentBlockRule)
        .filter(ContentBlockRule.enabled == 1)
        .all()
    )
    if not rows:
        return None

    rules = [
        {
            "pattern": r.pattern,
            "scope_type": r.scope_type or "session",
            "chat_stream_id": r.chat_stream_id or "",
            "no_reply": bool(r.no_reply),
            "no_learn": bool(r.no_learn),
            "no_context": bool(r.no_context),
            "enabled": True,
        }
        for r in rows
    ]
    return check_message_moderation(message, chat_stream_id=chat_stream_id, rules=rules)
