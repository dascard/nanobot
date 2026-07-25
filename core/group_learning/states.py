"""群学习状态、来源与人工治理动作的代码所有枚举。"""

from __future__ import annotations


GROUP_LEARNING_CANDIDATE_STATUSES = (
    "raw",
    "pending_model_review",
    "waiting_for_evidence",
    "accepted",
    "rejected",
    "merged",
    "alias",
    "conflict",
    "superseded",
)
GROUP_LEARNING_CANDIDATE_SOURCES = (
    "rule",
    "model",
    "legacy_expression",
    "legacy_jargon",
    "legacy_group_memory",
    "human",
)
GROUP_LEARNING_RUN_STATUSES = (
    "pending",
    "running",
    "candidate_persisted",
    "pending_model_review",
    "succeeded",
    "failed",
    "cancelled",
)
GROUP_LEARNING_HUMAN_ACTIONS = (
    "accept",
    "edit_accept",
    "reject",
    "merge",
    "resolve_conflict",
)
GROUP_LEARNING_CONFLICT_RESOLUTIONS = (
    "keep_target",
    "replace_target",
)
GROUP_LEARNING_MODEL_ACTIONS = (
    "new",
    "merge_into",
    "add_alias",
    "conflict_with",
    "reject",
)


def sql_string_values(values: tuple[str, ...]) -> str:
    """仅供静态 SQLAlchemy CheckConstraint 生成安全字符串字面量。"""

    if not values or any(
        not value
        or value.replace("_", "").replace("-", "").isalnum() is False
        for value in values
    ):
        raise ValueError("SQL 枚举值无效")
    return ", ".join(f"'{value}'" for value in values)


__all__ = [
    "GROUP_LEARNING_CANDIDATE_SOURCES",
    "GROUP_LEARNING_CANDIDATE_STATUSES",
    "GROUP_LEARNING_CONFLICT_RESOLUTIONS",
    "GROUP_LEARNING_HUMAN_ACTIONS",
    "GROUP_LEARNING_MODEL_ACTIONS",
    "GROUP_LEARNING_RUN_STATUSES",
    "sql_string_values",
]
