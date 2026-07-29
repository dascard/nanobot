"""工具能力注册与运行时 Profile 的冻结事实源。

本模块只描述 Nanobot 领域合同，不导入 KT。具体框架如何把
``ToolExecutionBinding.port_id`` 适配成可执行工具，由边界 Adapter 负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol

from core.lifecycle import (
    COMPATIBILITY_REGISTRY,
    FEATURE_LIFECYCLE_REGISTRY,
    CompatibilityKind,
    CompatibilityTombstoneBehavior,
    FeatureLifecycleState,
)
from core.registry import RegistryBuilder, RegistrySnapshot
from core.tool_registry import (
    SANDBOX_TOOL_NAMES,
    ToolDescriptor,
    list_tool_descriptors,
)


ToolRegistrationLifecycle = Literal["active", "retired", "framework"]
ToolProfileMode = Literal["default", "force_only", "allowlist", "ceiling"]


class ToolRegistrationError(ValueError):
    """工具能力注册失败。"""


class ToolSchemaProviderPort(Protocol):
    """按冻结 Registration 生成基础 wire Schema 的 Port。"""

    @property
    def provider_id(self) -> str: ...

    def build_schema(self, tool_name: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ToolExecutionBinding:
    """框架无关的执行 Port 绑定。"""

    port_id: str
    invocation_lifecycle: Literal["request"] = "request"


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    """一次绑定工具描述、Schema、执行、Prompt、Trace 与生命周期。"""

    name: str
    descriptor: ToolDescriptor
    schema_provider_id: str
    execution_binding: ToolExecutionBinding | None
    prompt_template_keys: tuple[str, ...]
    trace_policy: str
    lifecycle: ToolRegistrationLifecycle
    feature_lifecycle_id: str

    @property
    def registry_namespace(self) -> str:
        return "tool_registration"

    @property
    def registry_id(self) -> str:
        return self.name

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "descriptor": self.descriptor.registry_payload(),
            "schema_provider_id": self.schema_provider_id,
            "execution_binding": (
                {
                    "port_id": self.execution_binding.port_id,
                    "invocation_lifecycle": (
                        self.execution_binding.invocation_lifecycle
                    ),
                }
                if self.execution_binding is not None
                else None
            ),
            "prompt_template_keys": list(self.prompt_template_keys),
            "trace_policy": self.trace_policy,
            "lifecycle": self.lifecycle,
            "feature_lifecycle_id": self.feature_lifecycle_id,
        }


class ToolRegistrationRegistry:
    """启动后只读的工具能力注册快照。"""

    def __init__(self, registrations: Mapping[str, ToolRegistration]) -> None:
        self._registrations = MappingProxyType(dict(registrations))
        builder = RegistryBuilder[ToolRegistration]("tool_registration")
        for name in sorted(self._registrations):
            builder.register(self._registrations[name])
        self._registry_snapshot: RegistrySnapshot[ToolRegistration] = (
            builder.freeze()
        )

    @property
    def frozen(self) -> bool:
        return True

    @property
    def registry_snapshot(self) -> RegistrySnapshot[ToolRegistration]:
        return self._registry_snapshot

    @classmethod
    def from_descriptors(
        cls,
        descriptors: tuple[ToolDescriptor, ...],
        *,
        schema_provider_ids: Mapping[str, str],
        execution_port_ids: Mapping[str, str],
    ) -> "ToolRegistrationRegistry":
        registrations: dict[str, ToolRegistration] = {}
        for descriptor in descriptors:
            name = descriptor.name
            if name in registrations:
                raise ToolRegistrationError(
                    f"tool registration 重复登记: {name}"
                )

            if descriptor.framework_owned:
                lifecycle: ToolRegistrationLifecycle = "framework"
            elif descriptor.availability_policy == "force_disabled":
                lifecycle = "retired"
            else:
                lifecycle = "active"

            schema_provider_id = str(
                schema_provider_ids.get(name) or ""
            ).strip()
            execution_port_id = str(
                execution_port_ids.get(name) or ""
            ).strip()
            if lifecycle == "active" and not schema_provider_id:
                raise ToolRegistrationError(
                    f"active tool {name} 缺少 schema provider"
                )
            if lifecycle == "active" and not execution_port_id:
                raise ToolRegistrationError(
                    f"active tool {name} 缺少 execution binding"
                )
            if lifecycle != "active" and execution_port_id:
                raise ToolRegistrationError(
                    f"{lifecycle} tool {name} 不能保留 execution binding"
                )

            execution_binding = (
                ToolExecutionBinding(port_id=execution_port_id)
                if execution_port_id
                else None
            )
            feature_lifecycle_id = (
                "sandbox"
                if (
                    name in SANDBOX_TOOL_NAMES
                    and lifecycle != "retired"
                )
                else f"tool.{name}"
            )
            try:
                feature = FEATURE_LIFECYCLE_REGISTRY.require(
                    feature_lifecycle_id
                )
            except KeyError as exc:
                raise ToolRegistrationError(
                    f"tool {name} 引用了未登记 Feature "
                    f"{feature_lifecycle_id}"
                ) from exc
            if lifecycle == "retired":
                if feature.state is not FeatureLifecycleState.RETIRED:
                    raise ToolRegistrationError(
                        f"retired tool {name} 必须引用 retired Feature"
                    )
                compatibility = COMPATIBILITY_REGISTRY.find_alias(
                    CompatibilityKind.TOOL,
                    name,
                )
                if (
                    compatibility is None
                    or compatibility.tombstone_behavior
                    is not CompatibilityTombstoneBehavior.REJECT
                ):
                    raise ToolRegistrationError(
                        f"retired tool {name} 缺少 fail-closed tombstone"
                    )
            elif feature.state is FeatureLifecycleState.RETIRED:
                raise ToolRegistrationError(
                    f"{lifecycle} tool {name} 不能引用 retired Feature"
                )
            registrations[name] = ToolRegistration(
                name=name,
                descriptor=descriptor,
                schema_provider_id=schema_provider_id,
                execution_binding=execution_binding,
                prompt_template_keys=descriptor.prompt_template_keys,
                trace_policy=descriptor.trace_policy,
                lifecycle=lifecycle,
                feature_lifecycle_id=feature_lifecycle_id,
            )
        return cls(registrations)

    def get(self, name: str) -> ToolRegistration | None:
        return self._registrations.get(str(name or "").strip())

    def values(self) -> tuple[ToolRegistration, ...]:
        return tuple(self._registry_snapshot)

    def active_values(self) -> tuple[ToolRegistration, ...]:
        return tuple(
            registration
            for registration in self._registry_snapshot
            if registration.lifecycle == "active"
        )

    def user_values(self) -> tuple[ToolRegistration, ...]:
        return tuple(
            registration
            for registration in self._registry_snapshot
            if registration.lifecycle != "framework"
        )


_ALL_TOOL_DESCRIPTORS = tuple(list_tool_descriptors())
_CANONICAL_SCHEMA_PROVIDER_IDS: Mapping[str, str] = MappingProxyType({
    descriptor.name: "canonical"
    for descriptor in _ALL_TOOL_DESCRIPTORS
    if not descriptor.framework_owned
})
_ACTIVE_EXECUTION_PORT_IDS: Mapping[str, str] = MappingProxyType({
    descriptor.name: f"tool.{descriptor.name}.execute"
    for descriptor in _ALL_TOOL_DESCRIPTORS
    if not descriptor.framework_owned
    and descriptor.availability_policy != "force_disabled"
})


TOOL_REGISTRATION_REGISTRY = ToolRegistrationRegistry.from_descriptors(
    _ALL_TOOL_DESCRIPTORS,
    schema_provider_ids=_CANONICAL_SCHEMA_PROVIDER_IDS,
    execution_port_ids=_ACTIVE_EXECUTION_PORT_IDS,
)


def get_tool_registration(name: str) -> ToolRegistration | None:
    return TOOL_REGISTRATION_REGISTRY.get(name)


def list_tool_registrations() -> tuple[ToolRegistration, ...]:
    return TOOL_REGISTRATION_REGISTRY.values()


def list_user_tool_registrations() -> tuple[ToolRegistration, ...]:
    return TOOL_REGISTRATION_REGISTRY.user_values()


def list_active_tool_registrations() -> tuple[ToolRegistration, ...]:
    return TOOL_REGISTRATION_REGISTRY.active_values()


def validate_tool_registration_registry() -> None:
    """重建快照，供启动期执行完整 fail-closed 校验。"""

    ToolRegistrationRegistry.from_descriptors(
        _ALL_TOOL_DESCRIPTORS,
        schema_provider_ids=_CANONICAL_SCHEMA_PROVIDER_IDS,
        execution_port_ids=_ACTIVE_EXECUTION_PORT_IDS,
    )


@dataclass(frozen=True, slots=True)
class ToolProfileDescriptor:
    """运行时工具 Profile；只裁剪能力，不能替代 ToolPlan 授权。"""

    profile_id: str
    mode: ToolProfileMode
    aliases: tuple[str, ...]
    default_tool_names: tuple[str, ...]
    setting_key: str
    allow_scope_overrides: bool
    description: str

    @property
    def registry_namespace(self) -> str:
        return "tool_profile"

    @property
    def registry_id(self) -> str:
        return self.profile_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "profile_id": self.profile_id,
            "mode": self.mode,
            "aliases": list(self.aliases),
            "default_tool_names": list(self.default_tool_names),
            "setting_key": self.setting_key,
            "allow_scope_overrides": self.allow_scope_overrides,
            "description": self.description,
        }


class ToolProfileRegistry:
    """冻结的运行时工具 Profile 注册表。"""

    def __init__(
        self,
        descriptors: tuple[ToolProfileDescriptor, ...],
    ) -> None:
        builder = RegistryBuilder[ToolProfileDescriptor]("tool_profile")
        aliases: dict[str, str] = {}
        for descriptor in descriptors:
            builder.register(descriptor)
            for alias in (descriptor.profile_id, *descriptor.aliases):
                normalized = str(alias or "").strip().lower()
                if not normalized:
                    raise ToolRegistrationError(
                        f"tool profile {descriptor.profile_id} 包含空 alias"
                    )
                previous = aliases.get(normalized)
                if previous is not None and previous != descriptor.profile_id:
                    raise ToolRegistrationError(
                        f"tool profile alias 冲突: {normalized}"
                    )
                aliases[normalized] = descriptor.profile_id
        self._registry_snapshot = builder.freeze()
        self._aliases = MappingProxyType(aliases)
        if self._registry_snapshot.get("full") is None:
            raise ToolRegistrationError("tool profile 必须登记 full")

    @property
    def registry_snapshot(self) -> RegistrySnapshot[ToolProfileDescriptor]:
        return self._registry_snapshot

    def get(self, profile_id: str) -> ToolProfileDescriptor | None:
        return self._registry_snapshot.get(profile_id)

    def normalize(self, value: str = "full") -> str:
        normalized = str(value or "full").strip().lower()
        return self._aliases.get(normalized, "full")


_DEFAULT_LIGHTWEIGHT_TOOL_NAMES = (
    "reply",
    "no_reply",
    "image_summary",
    "image_generation",
    "sticker_search",
)
_RESEARCH_TOOL_NAMES = (
    "web_search",
    "reply",
    "no_reply",
)

TOOL_PROFILE_REGISTRY = ToolProfileRegistry((
    ToolProfileDescriptor(
        profile_id="full",
        mode="default",
        aliases=(),
        default_tool_names=(),
        setting_key="",
        allow_scope_overrides=True,
        description="使用 ToolPlan 默认值与作用域覆盖，不额外裁剪。",
    ),
    ToolProfileDescriptor(
        profile_id="none",
        mode="force_only",
        aliases=("off", "disabled"),
        default_tool_names=(),
        setting_key="",
        allow_scope_overrides=False,
        description="仅保留系统强制启用工具。",
    ),
    ToolProfileDescriptor(
        profile_id="lightweight",
        mode="allowlist",
        aliases=("light", "lite"),
        default_tool_names=_DEFAULT_LIGHTWEIGHT_TOOL_NAMES,
        setting_key="tool.lightweight_set",
        allow_scope_overrides=True,
        description="使用可配置的轻量工具集合。",
    ),
    ToolProfileDescriptor(
        profile_id="research",
        mode="ceiling",
        aliases=("research_companion",),
        default_tool_names=_RESEARCH_TOOL_NAMES,
        setting_key="",
        allow_scope_overrides=True,
        description="研究任务固定能力上限，覆盖不能扩大该集合。",
    ),
))


def normalize_tool_profile_id(value: str = "full") -> str:
    return TOOL_PROFILE_REGISTRY.normalize(value)


def get_tool_profile(profile_id: str) -> ToolProfileDescriptor:
    normalized = normalize_tool_profile_id(profile_id)
    descriptor = TOOL_PROFILE_REGISTRY.get(normalized)
    if descriptor is None:
        raise ToolRegistrationError(
            f"tool profile 未登记: {normalized}"
        )
    return descriptor


def get_default_lightweight_tool_names() -> frozenset[str]:
    descriptor = get_tool_profile("lightweight")
    return frozenset(descriptor.default_tool_names)


RESEARCH_TOOL_NAMES = frozenset(
    get_tool_profile("research").default_tool_names
)
