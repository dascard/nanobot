"""兼容入口的冻结目录、移除门禁与安全用量观测。"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType

from core.registry import RegistryBuilder, RegistrySnapshot


class CompatibilityKind(str, Enum):
    """受治理兼容入口的固定类别。"""

    SETTING = "setting"
    ROUTE = "route"
    TOOL = "tool"
    ENDPOINT = "endpoint"
    IDENTITY = "identity"
    SCHEMA = "schema"


class CompatibilityWarningPolicy(str, Enum):
    """兼容入口的告警方式。"""

    LOG_ONCE = "log_once"
    LOG_EVERY = "log_every"
    RESPONSE_HEADER = "response_header"
    SILENT_USAGE_ONLY = "silent_usage_only"


class CompatibilityTombstoneBehavior(str, Enum):
    """兼容入口被命中时的稳定行为。"""

    FORWARD = "forward"
    PRESERVE = "preserve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class CompatibilityRemovalGate:
    """删除兼容入口前必须同时满足的生产门禁。"""

    consecutive_zero_usage_days: int
    minimum_full_releases: int
    require_migration_reconciliation: bool
    require_rollback_drill: bool
    require_backup_restore_drill: bool
    required_approvers: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.consecutive_zero_usage_days <= 0:
            raise ValueError("兼容移除零使用天数必须大于 0")
        if self.minimum_full_releases <= 0:
            raise ValueError("兼容移除至少跨过一次完整发布")
        if not self.required_approvers:
            raise ValueError("兼容移除必须声明审批人")
        if len(self.required_approvers) != len(set(self.required_approvers)):
            raise ValueError("兼容移除审批人不能重复")

    @classmethod
    def production_default(cls) -> "CompatibilityRemovalGate":
        return cls(
            consecutive_zero_usage_days=30,
            minimum_full_releases=1,
            require_migration_reconciliation=True,
            require_rollback_drill=True,
            require_backup_restore_drill=True,
            required_approvers=(
                "release_owner",
                "group_memory_data_owner",
                "production_operator",
            ),
        )

    def metadata(self) -> dict[str, object]:
        return {
            "consecutive_zero_usage_days": (
                self.consecutive_zero_usage_days
            ),
            "minimum_full_releases": self.minimum_full_releases,
            "require_migration_reconciliation": (
                self.require_migration_reconciliation
            ),
            "require_rollback_drill": self.require_rollback_drill,
            "require_backup_restore_drill": (
                self.require_backup_restore_drill
            ),
            "required_approvers": list(self.required_approvers),
        }


@dataclass(frozen=True, slots=True)
class CompatibilityDescriptor:
    """一个显式兼容入口及其退役证据要求。"""

    compatibility_id: str
    kind: CompatibilityKind
    alias_value: str
    canonical_replacement: str
    introduced_version: str
    warning_policy: CompatibilityWarningPolicy
    removal_gate: CompatibilityRemovalGate
    tombstone_behavior: CompatibilityTombstoneBehavior
    owner_module: str
    test_ids: tuple[str, ...]
    removal_conditions: tuple[str, ...]
    environment_alias: str | None = None

    def __post_init__(self) -> None:
        required = {
            "compatibility_id": self.compatibility_id,
            "alias_value": self.alias_value,
            "canonical_replacement": self.canonical_replacement,
            "introduced_version": self.introduced_version,
            "owner_module": self.owner_module,
        }
        for field_name, value in required.items():
            normalized = str(value or "").strip()
            if not normalized:
                raise ValueError(f"Compatibility {field_name} 不能为空")
            if any(ord(char) < 32 for char in normalized):
                raise ValueError(
                    f"Compatibility {field_name} 不能包含控制字符"
                )
            object.__setattr__(self, field_name, normalized)
        if not self.test_ids:
            raise ValueError(
                f"Compatibility {self.compatibility_id} 必须声明测试"
            )
        if not self.removal_conditions:
            raise ValueError(
                f"Compatibility {self.compatibility_id} 的 removal "
                "conditions 不能为空"
            )
        for test_id in self.test_ids:
            if not str(test_id or "").strip():
                raise ValueError(
                    f"Compatibility {self.compatibility_id} 的测试 ID 不能为空"
                )
        for condition in self.removal_conditions:
            if not str(condition or "").strip():
                raise ValueError(
                    f"Compatibility {self.compatibility_id} 的 removal "
                    "condition 不能为空"
                )
        if self.environment_alias is not None:
            environment_alias = str(self.environment_alias or "").strip()
            if not environment_alias:
                raise ValueError("Compatibility environment alias 不能为空")
            object.__setattr__(
                self,
                "environment_alias",
                environment_alias,
            )

    @property
    def registry_namespace(self) -> str:
        return "compatibility"

    @property
    def registry_id(self) -> str:
        return self.compatibility_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "compatibility_id": self.compatibility_id,
            "kind": self.kind.value,
            "alias_value": self.alias_value,
            "canonical_replacement": self.canonical_replacement,
            "introduced_version": self.introduced_version,
            "warning_policy": self.warning_policy.value,
            "removal_gate": self.removal_gate.metadata(),
            "tombstone_behavior": self.tombstone_behavior.value,
            "owner_module": self.owner_module,
            "test_ids": list(self.test_ids),
            "removal_conditions": list(self.removal_conditions),
            "environment_alias": self.environment_alias,
        }


@dataclass(frozen=True, slots=True)
class CompatibilityResolution:
    """类型化 alias 解析结果。"""

    descriptor: CompatibilityDescriptor

    @property
    def canonical_replacement(self) -> str:
        return self.descriptor.canonical_replacement


@dataclass(frozen=True, slots=True)
class CompatibilityUsage:
    """不包含原始 alias 或业务正文的聚合用量。"""

    compatibility_id: str
    kind: CompatibilityKind
    count: int
    last_used_at: datetime


class InMemoryCompatibilityUsageRecorder:
    """线程安全的进程内兼容用量聚合器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[str, CompatibilityUsage] = {}

    def record(
        self,
        descriptor: CompatibilityDescriptor,
    ) -> CompatibilityUsage:
        now = datetime.now(timezone.utc)
        with self._lock:
            previous = self._usage.get(descriptor.compatibility_id)
            usage = CompatibilityUsage(
                compatibility_id=descriptor.compatibility_id,
                kind=descriptor.kind,
                count=(previous.count if previous is not None else 0) + 1,
                last_used_at=now,
            )
            self._usage[descriptor.compatibility_id] = usage
            return usage

    def snapshot(self) -> Mapping[str, CompatibilityUsage]:
        with self._lock:
            return MappingProxyType(dict(self._usage))


class CompatibilityRegistry:
    """兼容 alias 的唯一冻结解析目录。"""

    def __init__(
        self,
        descriptors: Iterable[CompatibilityDescriptor],
    ) -> None:
        builder = RegistryBuilder[CompatibilityDescriptor]("compatibility")
        aliases: dict[
            tuple[CompatibilityKind, str],
            CompatibilityDescriptor,
        ] = {}
        for descriptor in descriptors:
            builder.register(descriptor)
            alias_key = (descriptor.kind, descriptor.alias_value)
            if alias_key in aliases:
                raise ValueError(
                    "Compatibility alias 冲突: "
                    f"{descriptor.kind.value}:{descriptor.alias_value}"
                )
            aliases[alias_key] = descriptor
        self._snapshot = builder.freeze()
        self._aliases = MappingProxyType(aliases)

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[CompatibilityDescriptor]:
        return self._snapshot

    def require(self, compatibility_id: str) -> CompatibilityDescriptor:
        normalized = str(compatibility_id or "").strip()
        descriptor = self._snapshot.get(normalized)
        if descriptor is None:
            raise KeyError(f"未登记的 Compatibility: {normalized}")
        return descriptor

    def descriptors(
        self,
        kind: CompatibilityKind | None = None,
    ) -> tuple[CompatibilityDescriptor, ...]:
        return tuple(
            descriptor
            for descriptor in self._snapshot
            if kind is None or descriptor.kind is kind
        )

    def find_alias(
        self,
        kind: CompatibilityKind,
        alias_value: str,
    ) -> CompatibilityDescriptor | None:
        return self._aliases.get((kind, str(alias_value or "").strip()))

    def resolve_alias(
        self,
        kind: CompatibilityKind,
        alias_value: str,
        *,
        recorder: InMemoryCompatibilityUsageRecorder | None = None,
    ) -> CompatibilityResolution | None:
        descriptor = self.find_alias(kind, alias_value)
        if descriptor is None:
            return None
        if recorder is not None:
            usage = recorder.record(descriptor)
            if recorder is _COMPATIBILITY_USAGE_RECORDER:
                _emit_compatibility_usage(descriptor, usage)
        return CompatibilityResolution(descriptor=descriptor)


_REMOVAL_CONDITIONS = (
    "continuous_zero_usage_window_satisfied",
    "migration_and_drill_evidence_approved",
)
_REMOVAL_GATE = CompatibilityRemovalGate.production_default()


def _descriptor(
    compatibility_id: str,
    kind: CompatibilityKind,
    alias_value: str,
    canonical_replacement: str,
    *,
    owner_module: str,
    test_ids: tuple[str, ...],
    tombstone_behavior: CompatibilityTombstoneBehavior = (
        CompatibilityTombstoneBehavior.FORWARD
    ),
    warning_policy: CompatibilityWarningPolicy = (
        CompatibilityWarningPolicy.LOG_ONCE
    ),
    environment_alias: str | None = None,
) -> CompatibilityDescriptor:
    return CompatibilityDescriptor(
        compatibility_id=compatibility_id,
        kind=kind,
        alias_value=alias_value,
        canonical_replacement=canonical_replacement,
        introduced_version="2026.07",
        warning_policy=warning_policy,
        removal_gate=_REMOVAL_GATE,
        tombstone_behavior=tombstone_behavior,
        owner_module=owner_module,
        test_ids=test_ids,
        removal_conditions=_REMOVAL_CONDITIONS,
        environment_alias=environment_alias,
    )


_COMPATIBILITY_DESCRIPTORS = (
    _descriptor(
        "setting.daily_digest_enabled",
        CompatibilityKind.SETTING,
        "daily_digest.enabled",
        "memory_digest.scheduler_enabled",
        owner_module="memory.runtime",
        test_ids=("tests/test_config_registry.py",),
        environment_alias="DAILY_DIGEST_ENABLED",
    ),
    _descriptor(
        "setting.daily_digest_hour",
        CompatibilityKind.SETTING,
        "daily_digest.hour",
        "memory_digest.schedule_hour",
        owner_module="memory.runtime",
        test_ids=("tests/test_config_registry.py",),
        environment_alias="DAILY_DIGEST_HOUR",
    ),
    _descriptor(
        "route.vision",
        CompatibilityKind.ROUTE,
        "vision",
        "sticker_describe",
        owner_module="model.runtime",
        test_ids=("tests/test_model_route_registry.py",),
    ),
    _descriptor(
        "route.classifier_legacy",
        CompatibilityKind.ROUTE,
        "classifier_legacy",
        "private_decision",
        owner_module="model.runtime",
        test_ids=("tests/test_classifier.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.PRESERVE,
    ),
    _descriptor(
        "endpoint.render",
        CompatibilityKind.ENDPOINT,
        "/api/v1/render",
        "retired.without_replacement",
        owner_module="api.agent_step",
        test_ids=("tests/test_api_agent_step_routes_split.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.PRESERVE,
        warning_policy=CompatibilityWarningPolicy.RESPONSE_HEADER,
    ),
    _descriptor(
        "endpoint.group_timing",
        CompatibilityKind.ENDPOINT,
        "/api/v1/group_timing",
        "/api/v1/group/message",
        owner_module="api.group_utility",
        test_ids=("tests/test_api_group_utility_routes_split.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.PRESERVE,
        warning_policy=CompatibilityWarningPolicy.RESPONSE_HEADER,
    ),
    _descriptor(
        "identity.group_prefix",
        CompatibilityKind.IDENTITY,
        "group_*",
        "canonical.chat_stream_id",
        owner_module="identity.adapter",
        test_ids=("tests/test_chat_stream_identity.py",),
    ),
    _descriptor(
        "identity.private_prefix",
        CompatibilityKind.IDENTITY,
        "private_*",
        "canonical.chat_stream_id",
        owner_module="identity.adapter",
        test_ids=("tests/test_chat_stream_identity.py",),
    ),
    _descriptor(
        "tool.python_sandbox",
        CompatibilityKind.TOOL,
        "python_sandbox",
        "sql_analysis",
        owner_module="tool.runtime",
        test_ids=("tests/test_tool_registration.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.REJECT,
    ),
    _descriptor(
        "tool.bash",
        CompatibilityKind.TOOL,
        "bash",
        "sandbox_exec",
        owner_module="tool.runtime",
        test_ids=("tests/test_tool_registration.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.REJECT,
    ),
    _descriptor(
        "tool.read",
        CompatibilityKind.TOOL,
        "read",
        "workspace_read",
        owner_module="tool.runtime",
        test_ids=("tests/test_tool_registration.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.REJECT,
    ),
    _descriptor(
        "tool.write",
        CompatibilityKind.TOOL,
        "write",
        "workspace_write",
        owner_module="tool.runtime",
        test_ids=("tests/test_tool_registration.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.REJECT,
    ),
    _descriptor(
        "tool.edit",
        CompatibilityKind.TOOL,
        "edit",
        "workspace_apply_patch",
        owner_module="tool.runtime",
        test_ids=("tests/test_tool_registration.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.REJECT,
    ),
    _descriptor(
        "tool.grep",
        CompatibilityKind.TOOL,
        "grep",
        "workspace_search",
        owner_module="tool.runtime",
        test_ids=("tests/test_tool_registration.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.REJECT,
    ),
    _descriptor(
        "tool.glob",
        CompatibilityKind.TOOL,
        "glob",
        "workspace_list",
        owner_module="tool.runtime",
        test_ids=("tests/test_tool_registration.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.REJECT,
    ),
    _descriptor(
        "tool.memory_read",
        CompatibilityKind.TOOL,
        "memory_read",
        "retired.without_replacement",
        owner_module="tool.runtime",
        test_ids=("tests/test_tool_registration.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.REJECT,
    ),
    _descriptor(
        "tool.memory_write",
        CompatibilityKind.TOOL,
        "memory_write",
        "retired.without_replacement",
        owner_module="tool.runtime",
        test_ids=("tests/test_tool_registration.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.REJECT,
    ),
    _descriptor(
        "schema.classifier_legacy_output",
        CompatibilityKind.SCHEMA,
        "legacy_reply_v1",
        "private_decision_v2",
        owner_module="task.runtime",
        test_ids=("tests/test_prompt_v2_task_contracts.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.PRESERVE,
    ),
    _descriptor(
        "schema.group_analysis_omitted_aspects",
        CompatibilityKind.SCHEMA,
        "group_analysis.aspects_omitted",
        "group_analysis.aspects_explicit",
        owner_module="group.learning",
        test_ids=("tests/test_group_analysis_tool.py",),
        tombstone_behavior=CompatibilityTombstoneBehavior.PRESERVE,
        warning_policy=(
            CompatibilityWarningPolicy.SILENT_USAGE_ONLY
        ),
    ),
    _descriptor(
        "schema.chat_config_use_expression",
        CompatibilityKind.SCHEMA,
        "chat_stream_config.use_expression",
        "chat_stream_config.group_profile_mode",
        owner_module="group.learning",
        test_ids=("tests/test_admin_chat_config_routes_split.py",),
        tombstone_behavior=(
            CompatibilityTombstoneBehavior.FORWARD
        ),
        warning_policy=(
            CompatibilityWarningPolicy.SILENT_USAGE_ONLY
        ),
    ),
    _descriptor(
        "schema.legacy_expression_memory_read",
        CompatibilityKind.SCHEMA,
        "expression_memories.read",
        "group_learning.group_memory_query",
        owner_module="group.learning",
        test_ids=("tests/test_group_learning_stage7d.py",),
        tombstone_behavior=(
            CompatibilityTombstoneBehavior.PRESERVE
        ),
        warning_policy=(
            CompatibilityWarningPolicy.SILENT_USAGE_ONLY
        ),
    ),
    _descriptor(
        "schema.legacy_jargon_memory_read",
        CompatibilityKind.SCHEMA,
        "jargon_memories.read",
        "group_learning.group_memory_query",
        owner_module="group.learning",
        test_ids=("tests/test_group_learning_stage7d.py",),
        tombstone_behavior=(
            CompatibilityTombstoneBehavior.PRESERVE
        ),
        warning_policy=(
            CompatibilityWarningPolicy.SILENT_USAGE_ONLY
        ),
    ),
    _descriptor(
        "schema.legacy_expression_memory_write",
        CompatibilityKind.SCHEMA,
        "expression_memories.write",
        "group_learning.candidate_command",
        owner_module="group.learning",
        test_ids=("tests/test_group_learning_stage7d.py",),
        tombstone_behavior=(
            CompatibilityTombstoneBehavior.REJECT
        ),
        warning_policy=(
            CompatibilityWarningPolicy.SILENT_USAGE_ONLY
        ),
    ),
    _descriptor(
        "schema.legacy_jargon_memory_write",
        CompatibilityKind.SCHEMA,
        "jargon_memories.write",
        "group_learning.candidate_command",
        owner_module="group.learning",
        test_ids=("tests/test_group_learning_stage7d.py",),
        tombstone_behavior=(
            CompatibilityTombstoneBehavior.REJECT
        ),
        warning_policy=(
            CompatibilityWarningPolicy.SILENT_USAGE_ONLY
        ),
    ),
    _descriptor(
        "schema.legacy_group_analysis_memory_candidate_write",
        CompatibilityKind.SCHEMA,
        "group_analysis.memory_candidates.write",
        "group_analysis.application_service",
        owner_module="group.learning",
        test_ids=("tests/test_group_learning_stage7d.py",),
        tombstone_behavior=(
            CompatibilityTombstoneBehavior.REJECT
        ),
        warning_policy=(
            CompatibilityWarningPolicy.SILENT_USAGE_ONLY
        ),
    ),
)


COMPATIBILITY_REGISTRY = CompatibilityRegistry(
    _COMPATIBILITY_DESCRIPTORS
)
_COMPATIBILITY_USAGE_RECORDER = InMemoryCompatibilityUsageRecorder()


def _emit_compatibility_usage(
    descriptor: CompatibilityDescriptor,
    usage: CompatibilityUsage,
) -> None:
    """延迟导入事件总线，避免 Lifecycle Registry 反向依赖组合根。"""

    from core.runtime.event_bus import emit_runtime_event

    emit_runtime_event(
        "compatibility.alias_used",
        "state_changed",
        attributes={
            "compatibility_id": descriptor.compatibility_id,
            "kind": descriptor.kind.value,
            "warning_policy": descriptor.warning_policy.value,
            "usage_count": usage.count,
        },
    )


def record_compatibility_usage(
    compatibility_id: str,
) -> CompatibilityUsage:
    descriptor = COMPATIBILITY_REGISTRY.require(compatibility_id)
    usage = _COMPATIBILITY_USAGE_RECORDER.record(descriptor)
    _emit_compatibility_usage(descriptor, usage)
    return usage


def get_compatibility_usage_snapshot(
) -> Mapping[str, CompatibilityUsage]:
    return _COMPATIBILITY_USAGE_RECORDER.snapshot()


def resolve_compatibility_alias(
    kind: CompatibilityKind,
    alias_value: str,
) -> CompatibilityResolution | None:
    return COMPATIBILITY_REGISTRY.resolve_alias(
        kind,
        alias_value,
        recorder=_COMPATIBILITY_USAGE_RECORDER,
    )


__all__ = [
    "COMPATIBILITY_REGISTRY",
    "CompatibilityDescriptor",
    "CompatibilityKind",
    "CompatibilityRegistry",
    "CompatibilityRemovalGate",
    "CompatibilityResolution",
    "CompatibilityTombstoneBehavior",
    "CompatibilityUsage",
    "CompatibilityWarningPolicy",
    "InMemoryCompatibilityUsageRecorder",
    "get_compatibility_usage_snapshot",
    "record_compatibility_usage",
    "resolve_compatibility_alias",
]
