"""与具体 Agent 框架解耦的 Memory Provider 稳定合同。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable


_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")

MemoryProviderCapability: TypeAlias = Literal[
    "prompt_block",
    "prefetch",
    "sync_turn",
    "tools",
    "session_lifecycle",
    "compaction",
    "delegation",
]
MemoryProviderFailurePolicy: TypeAlias = Literal[
    "fail_closed",
    "skip_optional",
]
MemoryProviderRuntimeState: TypeAlias = Literal[
    "registered",
    "initialized",
    "failed",
    "stopped",
]

ALL_MEMORY_PROVIDER_CAPABILITIES: frozenset[MemoryProviderCapability] = frozenset({
    "prompt_block",
    "prefetch",
    "sync_turn",
    "tools",
    "session_lifecycle",
    "compaction",
    "delegation",
})
_FAILURE_POLICIES = frozenset({"fail_closed", "skip_optional"})


def _required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    return normalized


def _identifier(value: str, field_name: str) -> str:
    normalized = _required(value, field_name)
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} 不是合法标识符: {normalized!r}")
    return normalized


def freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """创建浅只读快照，避免请求间共享调用方持有的可变字典。"""

    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class MemoryProviderDescriptor:
    """Provider 的静态身份、能力、依赖和失败策略声明。"""

    id: str
    display_name: str
    priority: int = 100
    dependencies: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    capabilities: frozenset[MemoryProviderCapability] = ALL_MEMORY_PROVIDER_CAPABILITIES
    failure_policy: MemoryProviderFailurePolicy = "fail_closed"

    def __post_init__(self) -> None:
        provider_id = _identifier(self.id, "provider.id")
        dependencies = tuple(
            _identifier(item, "provider.dependency") for item in self.dependencies
        )
        tool_names = tuple(
            _identifier(item, "provider.tool_name") for item in self.tool_names
        )
        if provider_id in dependencies:
            raise ValueError("Provider 不能依赖自身")
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("Provider dependencies 不能重复")
        if len(tool_names) != len(set(tool_names)):
            raise ValueError("Provider tool_names 不能重复")
        capabilities = frozenset(self.capabilities)
        unknown_capabilities = capabilities - ALL_MEMORY_PROVIDER_CAPABILITIES
        if unknown_capabilities:
            raise ValueError(
                f"Provider capabilities 不支持: {sorted(unknown_capabilities)}"
            )
        if tool_names and "tools" not in capabilities:
            raise ValueError("声明 tool_names 的 Provider 必须具有 tools capability")
        if self.failure_policy not in _FAILURE_POLICIES:
            raise ValueError(
                f"Provider failure_policy 不支持: {self.failure_policy!r}"
            )
        object.__setattr__(self, "id", provider_id)
        object.__setattr__(
            self,
            "display_name",
            _required(self.display_name, "provider.display_name"),
        )
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "tool_names", tool_names)
        object.__setattr__(self, "capabilities", capabilities)

    def supports(self, capability: MemoryProviderCapability) -> bool:
        return capability in self.capabilities

    @property
    def registry_namespace(self) -> str:
        return "memory_provider"

    @property
    def registry_id(self) -> str:
        return self.id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return self.dependencies

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "priority": self.priority,
            "dependencies": list(self.dependencies),
            "tool_names": list(self.tool_names),
            "capabilities": sorted(self.capabilities),
            "failure_policy": self.failure_policy,
        }


@dataclass(frozen=True, slots=True)
class MemoryProviderDiagnostic:
    """不包含请求正文、查询文本或异常消息的运行时诊断快照。"""

    provider_id: str
    state: MemoryProviderRuntimeState
    capabilities: tuple[MemoryProviderCapability, ...]
    failure_policy: MemoryProviderFailurePolicy
    call_counts: Mapping[str, int] = field(default_factory=dict)
    failure_counts: Mapping[str, int] = field(default_factory=dict)
    last_error_type: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_id",
            _identifier(self.provider_id, "diagnostic.provider_id"),
        )
        if self.state not in {"registered", "initialized", "failed", "stopped"}:
            raise ValueError(f"Memory Provider diagnostic state 不支持: {self.state}")
        object.__setattr__(self, "capabilities", tuple(sorted(self.capabilities)))
        object.__setattr__(self, "call_counts", freeze_mapping(self.call_counts))
        object.__setattr__(self, "failure_counts", freeze_mapping(self.failure_counts))

    def metadata(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "state": self.state,
            "capabilities": list(self.capabilities),
            "failure_policy": self.failure_policy,
            "call_counts": dict(self.call_counts),
            "failure_counts": dict(self.failure_counts),
            "last_error_type": self.last_error_type,
        }


@dataclass(frozen=True, slots=True)
class MemoryProviderInitContext:
    runtime_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_id", _required(self.runtime_id, "runtime_id"))
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class MemoryRequestContext:
    request_id: str
    session_id: str
    principal_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _required(self.request_id, "request_id"))
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class MemoryPromptContext(MemoryRequestContext):
    """请求 Provider 生成系统提示块所需的稳定上下文。"""


@dataclass(frozen=True, slots=True)
class MemoryPrefetchContext(MemoryRequestContext):
    query: str = ""
    limit: int = 10

    def __post_init__(self) -> None:
        MemoryRequestContext.__post_init__(self)
        if self.limit <= 0:
            raise ValueError("prefetch.limit 必须大于 0")


@dataclass(frozen=True, slots=True)
class MemorySyncTurnContext(MemoryRequestContext):
    user_content: str = ""
    assistant_content: str = ""


@dataclass(frozen=True, slots=True)
class MemoryToolSchemaContext(MemoryRequestContext):
    """请求当前会话可见的 Memory 工具 Schema。"""


@dataclass(frozen=True, slots=True)
class MemorySessionContext:
    session_id: str
    principal_id: str = ""
    reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class MemoryCompactionContext:
    session_id: str
    source_turn_count: int
    retained_turn_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        if self.source_turn_count < 0 or self.retained_turn_count < 0:
            raise ValueError("compaction turn count 不能为负数")
        if self.retained_turn_count > self.source_turn_count:
            raise ValueError("retained_turn_count 不能大于 source_turn_count")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class MemoryDelegationContext:
    session_id: str
    delegation_id: str
    target: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(
            self,
            "delegation_id",
            _required(self.delegation_id, "delegation_id"),
        )
        object.__setattr__(self, "target", _required(self.target, "target"))
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class MemoryPromptBlock:
    provider_id: str
    content: str
    priority: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_id",
            _identifier(self.provider_id, "prompt_block.provider_id"),
        )
        object.__setattr__(self, "content", _required(self.content, "prompt_block.content"))


@dataclass(frozen=True, slots=True)
class MemoryToolCall(MemoryRequestContext):
    call_id: str = ""
    name: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        MemoryRequestContext.__post_init__(self)
        object.__setattr__(self, "call_id", _required(self.call_id, "call_id"))
        object.__setattr__(self, "name", _identifier(self.name, "tool.name"))
        object.__setattr__(self, "arguments", freeze_mapping(self.arguments))


@runtime_checkable
class MemoryProviderPort(Protocol):
    """Memory Provider 的完整生命周期与请求钩子。"""

    @property
    def descriptor(self) -> MemoryProviderDescriptor:
        ...

    async def initialize(self, context: MemoryProviderInitContext) -> None:
        ...

    async def system_prompt_block(
        self,
        context: MemoryPromptContext,
    ) -> MemoryPromptBlock | None:
        ...

    async def prefetch(self, context: MemoryPrefetchContext) -> tuple[object, ...]:
        ...

    async def sync_turn(self, context: MemorySyncTurnContext) -> None:
        ...

    async def tool_schemas(
        self,
        context: MemoryToolSchemaContext,
    ) -> tuple[Mapping[str, Any], ...]:
        ...

    async def handle_tool_call(self, call: MemoryToolCall) -> Mapping[str, Any]:
        ...

    async def on_session_start(self, context: MemorySessionContext) -> None:
        ...

    async def on_session_end(self, context: MemorySessionContext) -> None:
        ...

    async def on_compaction(self, context: MemoryCompactionContext) -> None:
        ...

    async def on_delegation_start(
        self,
        context: MemoryDelegationContext,
    ) -> None:
        ...

    async def on_delegation_end(self, context: MemoryDelegationContext) -> None:
        ...

    async def shutdown(self) -> None:
        ...
