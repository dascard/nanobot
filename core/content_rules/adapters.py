"""数据库兼容规则到 ContentRuleDescriptor 的显式 Adapter。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from core.content_rules.contracts import (
    ContentRuleAction,
    ContentRuleAuditPolicy,
    ContentRuleDescriptor,
    ContentRuleFailurePolicy,
    ContentRuleMatchKind,
    ContentRuleScope,
)


def _rule_identifier(prefix: str, raw_id: object, index: int) -> str:
    try:
        numeric = int(raw_id)
    except (TypeError, ValueError):
        numeric = index
    if numeric < 0:
        numeric = index
    return f"{prefix}.{numeric}.{index}"


def _content_actions(rule: Mapping[str, Any]) -> tuple[ContentRuleAction, ...]:
    actions: list[ContentRuleAction] = []
    for field, action, default in (
        ("no_reply", ContentRuleAction.NO_REPLY, False),
        ("no_context", ContentRuleAction.NO_CONTEXT, False),
        ("no_learn", ContentRuleAction.NO_LEARN, True),
    ):
        if bool(rule.get(field, default)):
            actions.append(action)
    return tuple(actions or (ContentRuleAction.SIGNAL,))


def content_rule_descriptor_from_mapping(
    rule: Mapping[str, Any],
    *,
    index: int,
) -> ContentRuleDescriptor:
    match_kind = ContentRuleMatchKind(
        str(rule.get("match_type") or "contains")
    )
    scope = ContentRuleScope(
        str(rule.get("scope_type") or "session")
    )
    pattern = str(rule.get("pattern") or "")
    return ContentRuleDescriptor(
        rule_id=_rule_identifier(
            "content.rule",
            rule.get("rule_id"),
            index,
        ),
        version="1.0.0",
        owner_module="content.moderation",
        scope=scope,
        scope_id=(
            str(rule.get("chat_stream_id") or "")
            if scope is ContentRuleScope.SESSION
            else ""
        ),
        match_kind=match_kind,
        pattern=pattern,
        actions=_content_actions(rule),
        priority=int(rule.get("priority", index)),
        input_max_length=int(rule.get("input_max_length", 4096)),
        match_max_count=int(rule.get("match_max_count", 1)),
        failure_policy=ContentRuleFailurePolicy(
            str(rule.get("failure_policy") or "fail_open")
        ),
        audit_policy=ContentRuleAuditPolicy.MATCH_ONLY,
        positive_examples=(pattern,),
        negative_examples=("__nanobot_rule_negative_example__",),
        performance_budget_ms=int(
            rule.get("performance_budget_ms", 10)
        ),
        source=str(rule.get("source") or "legacy_database"),
        web_editable=False,
    )


def content_rule_descriptors_from_mappings(
    rules: Sequence[Mapping[str, Any]],
) -> tuple[
    tuple[ContentRuleDescriptor, ...],
    dict[str, Mapping[str, Any]],
]:
    descriptors: list[ContentRuleDescriptor] = []
    source_by_id: dict[str, Mapping[str, Any]] = {}
    for index, rule in enumerate(rules):
        descriptor = content_rule_descriptor_from_mapping(
            rule,
            index=index,
        )
        descriptors.append(descriptor)
        source_by_id[descriptor.rule_id] = rule
    return tuple(descriptors), source_by_id


def user_block_rule_descriptor(
    rule: Any,
    *,
    index: int,
    group_id_normalizer: Callable[[str], str],
) -> ContentRuleDescriptor:
    target_type = str(getattr(rule, "target_type", "") or "private")
    rule_group_id = str(getattr(rule, "group_id", "") or "")
    if target_type == "group":
        scope = ContentRuleScope.GROUP
        scope_id = (
            group_id_normalizer(rule_group_id)
            if rule_group_id
            else ""
        )
        chat_types = ("group",)
    elif target_type == "private":
        scope = ContentRuleScope.USER
        scope_id = ""
        chat_types = ("private",)
    elif target_type == "all":
        scope = ContentRuleScope.GLOBAL
        scope_id = ""
        chat_types = ()
    else:
        raise ValueError("UserBlockRule target_type 无效")
    user_id = str(getattr(rule, "user_id", "") or "")
    return ContentRuleDescriptor(
        rule_id=_rule_identifier(
            "user.block",
            getattr(rule, "id", None),
            index,
        ),
        version="1.0.0",
        owner_module="content.user_block",
        scope=scope,
        scope_id=scope_id,
        match_kind=ContentRuleMatchKind.IDENTITY,
        pattern=user_id,
        actions=(ContentRuleAction.BLOCK,),
        priority=index,
        input_max_length=1,
        match_max_count=1,
        failure_policy=ContentRuleFailurePolicy.FAIL_OPEN,
        audit_policy=ContentRuleAuditPolicy.MATCH_ONLY,
        positive_examples=(user_id,),
        negative_examples=("__nanobot_other_user__",),
        performance_budget_ms=1,
        source="legacy_database",
        web_editable=False,
        applicable_chat_types=chat_types,
    )


def validate_web_content_rule(
    *,
    pattern: str,
    match_kind: ContentRuleMatchKind,
    scope: ContentRuleScope,
    scope_id: str,
    actions: tuple[ContentRuleAction, ...],
) -> None:
    """Web 只允许可证明有界的 contains/exact 子集。"""

    normalized_pattern = str(pattern or "")
    if match_kind is ContentRuleMatchKind.REGEX:
        raise ValueError("Web 规则不允许保存任意 regex")
    if match_kind not in {
        ContentRuleMatchKind.CONTAINS,
        ContentRuleMatchKind.EXACT,
    }:
        raise ValueError("Web 规则只允许 contains/exact")
    if scope not in {
        ContentRuleScope.GLOBAL,
        ContentRuleScope.SESSION,
    }:
        raise ValueError("Web 规则 scope 只允许 global/session")
    if scope is ContentRuleScope.SESSION:
        from core.chat_stream_identity import (
            ChatStreamIdentityError,
            parse_canonical_chat_stream_id,
        )

        try:
            parse_canonical_chat_stream_id(str(scope_id or ""))
        except ChatStreamIdentityError as exc:
            raise ValueError(
                "session Web 规则必须使用 canonical chat_stream_id"
            ) from exc
    if (
        not normalized_pattern.strip()
        or len(normalized_pattern) > 256
        or "\x00" in normalized_pattern
    ):
        raise ValueError("Web 规则 pattern 必须为 1～256 字符且不含 NUL")
    allowed_actions = {
        ContentRuleAction.NO_REPLY,
        ContentRuleAction.NO_CONTEXT,
        ContentRuleAction.NO_LEARN,
    }
    if not actions or not set(actions).issubset(allowed_actions):
        raise ValueError("Web 规则 action 超出安全子集")


__all__ = [
    "content_rule_descriptor_from_mapping",
    "content_rule_descriptors_from_mappings",
    "user_block_rule_descriptor",
    "validate_web_content_rule",
]
