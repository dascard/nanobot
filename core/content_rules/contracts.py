"""安全内容规则的框架无关合同。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


_VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$"
)


class ContentRuleScope(str, Enum):
    GLOBAL = "global"
    SESSION = "session"
    USER = "user"
    GROUP = "group"


class ContentRuleMatchKind(str, Enum):
    CONTAINS = "contains"
    EXACT = "exact"
    REGEX = "regex"
    IDENTITY = "identity"


class ContentRuleAction(str, Enum):
    BLOCK = "block"
    NO_REPLY = "no_reply"
    NO_CONTEXT = "no_context"
    NO_LEARN = "no_learn"
    REDACT = "redact"
    SIGNAL = "signal"


class ContentRuleFailurePolicy(str, Enum):
    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


class ContentRuleAuditPolicy(str, Enum):
    NONE = "none"
    MATCH_ONLY = "match_only"
    ALWAYS = "always"


@dataclass(frozen=True, slots=True)
class ContentRuleDescriptor:
    """一次规则匹配所需的全部类型化约束。"""

    rule_id: str
    version: str
    owner_module: str
    scope: ContentRuleScope
    scope_id: str
    match_kind: ContentRuleMatchKind
    pattern: str
    actions: tuple[ContentRuleAction, ...]
    priority: int
    input_max_length: int
    match_max_count: int
    failure_policy: ContentRuleFailurePolicy
    audit_policy: ContentRuleAuditPolicy
    positive_examples: tuple[str, ...]
    negative_examples: tuple[str, ...]
    performance_budget_ms: int
    source: str
    web_editable: bool
    applicable_chat_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        required = {
            "rule_id": self.rule_id,
            "owner": self.owner_module,
            "source": self.source,
        }
        for field_name, value in required.items():
            normalized = str(value or "").strip()
            if not normalized:
                raise ValueError(f"ContentRule {field_name} 不能为空")
            if any(ord(char) < 32 for char in normalized):
                raise ValueError(
                    f"ContentRule {field_name} 不能包含控制字符"
                )
        if _VERSION_PATTERN.fullmatch(self.version) is None:
            raise ValueError("ContentRule version 必须是语义版本")
        if not self.pattern or "\x00" in self.pattern:
            raise ValueError("ContentRule pattern 不能为空或包含 NUL")
        if len(self.pattern) > 4096:
            raise ValueError("ContentRule pattern 不能超过 4096 字符")
        if not self.actions:
            raise ValueError("ContentRule actions 不能为空")
        if len(self.actions) != len(set(self.actions)):
            raise ValueError("ContentRule actions 不能重复")
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not 0 <= self.priority <= 1_000_000
        ):
            raise ValueError("ContentRule priority 必须位于 [0, 1000000]")
        if not 1 <= self.input_max_length <= 1_000_000:
            raise ValueError("ContentRule input max length 不合法")
        if not 1 <= self.match_max_count <= 1000:
            raise ValueError("ContentRule match max count 不合法")
        if not 1 <= self.performance_budget_ms <= 1000:
            raise ValueError("ContentRule performance budget 不合法")
        if not self.positive_examples or not self.negative_examples:
            raise ValueError("ContentRule 必须声明正反例")
        if self.scope is ContentRuleScope.SESSION and not self.scope_id:
            raise ValueError("session ContentRule 必须声明 scope_id")
        if any(
            item not in {"private", "group"}
            for item in self.applicable_chat_types
        ):
            raise ValueError("ContentRule chat type 只允许 private/group")
        if len(self.applicable_chat_types) != len(
            set(self.applicable_chat_types)
        ):
            raise ValueError("ContentRule chat type 不能重复")
        if (
            self.web_editable
            and self.match_kind is ContentRuleMatchKind.REGEX
        ):
            raise ValueError("Web 可编辑规则不能使用 regex")

    @property
    def registry_namespace(self) -> str:
        return "content_rule"

    @property
    def registry_id(self) -> str:
        return self.rule_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        encoded = self.pattern.encode("utf-8")
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "owner_module": self.owner_module,
            "scope": self.scope.value,
            "scope_id": self.scope_id,
            "match_kind": self.match_kind.value,
            "pattern_sha256": hashlib.sha256(encoded).hexdigest(),
            "pattern_chars": len(self.pattern),
            "actions": [item.value for item in self.actions],
            "priority": self.priority,
            "input_max_length": self.input_max_length,
            "match_max_count": self.match_max_count,
            "failure_policy": self.failure_policy.value,
            "audit_policy": self.audit_policy.value,
            "positive_example_count": len(self.positive_examples),
            "negative_example_count": len(self.negative_examples),
            "performance_budget_ms": self.performance_budget_ms,
            "source": self.source,
            "web_editable": self.web_editable,
            "applicable_chat_types": list(self.applicable_chat_types),
        }


@dataclass(frozen=True, slots=True)
class ContentRuleInput:
    message: str = ""
    chat_stream_id: str = ""
    user_id: str = ""
    chat_type: str = ""
    group_id: str = ""


@dataclass(frozen=True, slots=True)
class ContentRuleFailure:
    rule_id: str
    code: str
    safe_summary: str


@dataclass(frozen=True, slots=True)
class ContentRuleMatch:
    descriptor: ContentRuleDescriptor
    match_count: int
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class ContentRuleEvaluation:
    matches: tuple[ContentRuleMatch, ...] = ()
    actions: tuple[ContentRuleAction, ...] = ()
    failures: tuple[ContentRuleFailure, ...] = ()
    evaluated_rule_count: int = 0

    @property
    def matched_rule_ids(self) -> tuple[str, ...]:
        return tuple(
            match.descriptor.rule_id for match in self.matches
        )


@dataclass(frozen=True, slots=True)
class ContentRuleSetDescriptor:
    """可选的静态 Rule Set 元数据，供组合根和 Admin 解释。"""

    rule_set_id: str
    owner_module: str
    rule_ids: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "ContentRuleAction",
    "ContentRuleAuditPolicy",
    "ContentRuleDescriptor",
    "ContentRuleEvaluation",
    "ContentRuleFailure",
    "ContentRuleFailurePolicy",
    "ContentRuleInput",
    "ContentRuleMatch",
    "ContentRuleMatchKind",
    "ContentRuleScope",
    "ContentRuleSetDescriptor",
]
