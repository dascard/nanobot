"""有界、确定性且不提升权限的内容 Rule Engine。"""

from __future__ import annotations

from collections.abc import Iterable

import regex as regex_engine

from core.content_rules.contracts import (
    ContentRuleAction,
    ContentRuleDescriptor,
    ContentRuleEvaluation,
    ContentRuleFailure,
    ContentRuleFailurePolicy,
    ContentRuleInput,
    ContentRuleMatch,
    ContentRuleMatchKind,
    ContentRuleScope,
)
from core.registry import RegistryBuilder, RegistrySnapshot


_ACTION_ORDER = {
    action: index for index, action in enumerate(ContentRuleAction)
}


class ContentRuleRegistry:
    """一次完整规则集的冻结快照。"""

    def __init__(
        self,
        descriptors: Iterable[ContentRuleDescriptor],
    ) -> None:
        builder = RegistryBuilder[ContentRuleDescriptor]("content_rule")
        collected = tuple(descriptors)
        for descriptor in collected:
            builder.register(descriptor)
        self._snapshot = builder.freeze()
        self._ordered = tuple(sorted(
            collected,
            key=lambda item: (item.priority, item.rule_id),
        ))

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[ContentRuleDescriptor]:
        return self._snapshot

    @property
    def ordered_descriptors(
        self,
    ) -> tuple[ContentRuleDescriptor, ...]:
        return self._ordered


class ContentRuleEngine:
    """执行冻结规则集；故障行为只能来自 Descriptor。"""

    def __init__(
        self,
        descriptors: Iterable[ContentRuleDescriptor],
    ) -> None:
        self._registry = ContentRuleRegistry(descriptors)

    @property
    def registry(self) -> ContentRuleRegistry:
        return self._registry

    @staticmethod
    def _applies(
        descriptor: ContentRuleDescriptor,
        value: ContentRuleInput,
    ) -> bool:
        if (
            descriptor.applicable_chat_types
            and value.chat_type not in descriptor.applicable_chat_types
        ):
            return False
        if descriptor.scope is ContentRuleScope.GLOBAL:
            return True
        if descriptor.scope is ContentRuleScope.SESSION:
            return (
                bool(value.chat_stream_id)
                and value.chat_stream_id == descriptor.scope_id
            )
        if descriptor.scope is ContentRuleScope.USER:
            return (
                not descriptor.scope_id
                or value.user_id == descriptor.scope_id
            )
        if descriptor.scope is ContentRuleScope.GROUP:
            if value.chat_type != "group":
                return False
            return (
                not descriptor.scope_id
                or not value.group_id
                or value.group_id == descriptor.scope_id
            )
        return False

    @staticmethod
    def _match_count(
        descriptor: ContentRuleDescriptor,
        value: ContentRuleInput,
    ) -> tuple[int, ContentRuleFailure | None]:
        if descriptor.match_kind is ContentRuleMatchKind.IDENTITY:
            return (
                (1 if value.user_id == descriptor.pattern else 0),
                None,
            )
        message = str(value.message or "")
        if len(message) > descriptor.input_max_length:
            return 0, ContentRuleFailure(
                rule_id=descriptor.rule_id,
                code="input_too_long",
                safe_summary="规则输入超过声明上限",
            )
        try:
            if descriptor.match_kind is ContentRuleMatchKind.EXACT:
                count = 1 if message == descriptor.pattern else 0
            elif descriptor.match_kind is ContentRuleMatchKind.CONTAINS:
                count = message.count(descriptor.pattern)
            else:
                count = 0
                timeout_seconds = (
                    descriptor.performance_budget_ms / 1000
                )
                for _match in regex_engine.finditer(
                    descriptor.pattern,
                    message,
                    timeout=timeout_seconds,
                ):
                    count += 1
                    if count > descriptor.match_max_count:
                        break
        except regex_engine.error:
            return 0, ContentRuleFailure(
                rule_id=descriptor.rule_id,
                code="invalid_pattern",
                safe_summary="规则模式无效",
            )
        except TimeoutError:
            return 0, ContentRuleFailure(
                rule_id=descriptor.rule_id,
                code="execution_timeout",
                safe_summary="规则执行超过性能预算",
            )
        if count > descriptor.match_max_count:
            return 0, ContentRuleFailure(
                rule_id=descriptor.rule_id,
                code="match_limit_exceeded",
                safe_summary="规则命中数超过声明上限",
            )
        return count, None

    def evaluate(
        self,
        value: ContentRuleInput,
    ) -> ContentRuleEvaluation:
        matches: list[ContentRuleMatch] = []
        failures: list[ContentRuleFailure] = []
        actions: set[ContentRuleAction] = set()
        evaluated = 0
        for descriptor in self._registry.ordered_descriptors:
            if not self._applies(descriptor, value):
                continue
            evaluated += 1
            count, failure = self._match_count(descriptor, value)
            if failure is not None:
                failures.append(failure)
                if (
                    descriptor.failure_policy
                    is ContentRuleFailurePolicy.FAIL_CLOSED
                ):
                    matches.append(ContentRuleMatch(
                        descriptor=descriptor,
                        match_count=0,
                        degraded=True,
                    ))
                    actions.update(descriptor.actions)
                continue
            if count <= 0:
                continue
            matches.append(ContentRuleMatch(
                descriptor=descriptor,
                match_count=count,
            ))
            actions.update(descriptor.actions)
        return ContentRuleEvaluation(
            matches=tuple(matches),
            actions=tuple(sorted(
                actions,
                key=lambda item: _ACTION_ORDER[item],
            )),
            failures=tuple(failures),
            evaluated_rule_count=evaluated,
        )


__all__ = [
    "ContentRuleEngine",
    "ContentRuleRegistry",
]
