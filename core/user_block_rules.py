"""用户屏蔽规则匹配 helper。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.content_rules import (
    ContentRuleAction,
    ContentRuleEngine,
    ContentRuleInput,
    user_block_rule_descriptor,
)
from core.database import UserBlockRule
from core.group_runtime.ids import normalize_group_session_id


def is_user_blocked(
    db: Any,
    user_id: str,
    *,
    target_type: str = "private",
    group_id: str = "",
    rule_model: Any = UserBlockRule,
    group_id_normalizer: Callable[[str], str] = normalize_group_session_id,
) -> bool:
    rules = db.query(rule_model).filter(
        rule_model.user_id == user_id,
        rule_model.enabled == 1,
    ).all()
    if not rules:
        return False
    try:
        descriptors = tuple(
            user_block_rule_descriptor(
                rule,
                index=index,
                group_id_normalizer=group_id_normalizer,
            )
            for index, rule in enumerate(rules)
        )
    except (TypeError, ValueError):
        return False
    normalized_group_id = (
        group_id_normalizer(group_id)
        if target_type == "group" and group_id
        else ""
    )
    result = ContentRuleEngine(descriptors).evaluate(
        ContentRuleInput(
            user_id=user_id,
            chat_type=target_type,
            group_id=normalized_group_id,
        )
    )
    return ContentRuleAction.BLOCK in result.actions
