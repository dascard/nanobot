"""用户屏蔽规则匹配 helper。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.database import UserBlockRule
from core.group_runtime.ids import normalize_group_session_id


def _matches_user_block_rule(
    rule: Any,
    *,
    target_type: str,
    group_id: str,
    group_id_normalizer: Callable[[str], str],
) -> bool:
    rule_target_type = getattr(rule, "target_type", "")
    if rule_target_type not in (target_type, "all"):
        return False

    rule_group_id = getattr(rule, "group_id", "")
    if rule_target_type == "group" and rule_group_id:
        norm_group = group_id_normalizer(group_id) if group_id else ""
        if norm_group and group_id_normalizer(str(rule_group_id)) != norm_group:
            return False

    return True


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
    return any(
        _matches_user_block_rule(
            rule,
            target_type=target_type,
            group_id=group_id,
            group_id_normalizer=group_id_normalizer,
        )
        for rule in rules
    )
