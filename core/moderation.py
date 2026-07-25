"""内容审核模块——ContentBlockRule 匹配 + no_reply/no_learn/no_context 决策。"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.content_rules import (
    ContentRuleAction,
    ContentRuleEngine,
    ContentRuleInput,
    content_rule_descriptors_from_mappings,
)

logger = logging.getLogger("nanobot.moderation")


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

    eligible_rules = [
        rule
        for rule in rules
        if rule.get("enabled", True)
        and str(rule.get("pattern") or "")
    ]
    if not eligible_rules:
        return None
    try:
        descriptors, source_by_id = (
            content_rule_descriptors_from_mappings(eligible_rules)
        )
        evaluation = ContentRuleEngine(descriptors).evaluate(
            ContentRuleInput(
                message=message,
                chat_stream_id=chat_stream_id,
            )
        )
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[Moderation] rule descriptor invalid type=%s",
            type(exc).__name__,
        )
        return None
    if not evaluation.matches:
        return None
    primary = source_by_id[
        evaluation.matches[0].descriptor.rule_id
    ]
    actions = set(evaluation.actions)
    return {
        "pattern": str(primary.get("pattern") or ""),
        "match_type": str(
            primary.get("match_type") or "contains"
        ),
        "rule_id": primary.get("rule_id"),
        "category": str(primary.get("category") or "no_learn"),
        "reason": str(primary.get("reason") or ""),
        "scope_type": str(
            primary.get("scope_type") or "session"
        ),
        "no_reply": ContentRuleAction.NO_REPLY in actions,
        "no_learn": ContentRuleAction.NO_LEARN in actions,
        "no_context": ContentRuleAction.NO_CONTEXT in actions,
    }


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
        .order_by(ContentBlockRule.id.asc())
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
            "source": "legacy_database",
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
            value = json.loads(meta_json)
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def is_no_learn_meta(meta_json: Any) -> bool:
    """检查 ChatLog.meta_json 是否标记为 no_learn。"""
    meta = _safe_meta(meta_json)
    moderation = meta.get("moderation")
    return bool(
        meta.get("no_learn")
        or (
            isinstance(moderation, dict)
            and moderation.get("no_learn", False)
        )
    )


def is_no_context_meta(meta_json: Any) -> bool:
    """检查 ChatLog.meta_json 是否标记为 no_context。"""
    meta = _safe_meta(meta_json)
    moderation = meta.get("moderation")
    return bool(
        meta.get("no_context")
        or (
            isinstance(moderation, dict)
            and moderation.get("no_context", False)
        )
    )
