"""Prompt Contribution 的代码侧描述符、冻结注册表与确定性编排。

Flow 只选择当前分支和声明可视拓扑；权威、信任、阶段、优先级、渲染器及追踪
策略由本模块固定。共享的冲突、依赖、冻结、generation 和内容 Hash 语义复用
``core.registry``，这里仅实现 Prompt 领域特有的适用范围与排序规则。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import re
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable

from core.prompt_v2.section_descriptors import (
    PromptSectionDescriptor,
    descriptor_for_node,
    descriptors_for_ordered_nodes,
    list_canonical_section_descriptors,
)
from core.registry import (
    RegistryBuilder,
    RegistryConflictError,
    RegistryDependencyError,
    RegistrySnapshot,
)
from core.runtime.extensions import (
    PROTECTED_TRANSFORM_INVARIANTS,
    RuntimeExtensionKind,
)


PromptContributionMultiplicity = Literal["singleton", "many"]
PromptContributionRendererId = Literal["template", "runtime"]
PromptSensitiveTracePolicy = Literal["hash_and_size", "metadata_only"]

_PHASE_ORDER = MappingProxyType({
    "platform": 0,
    "policy": 1,
    "identity": 2,
    "context": 3,
    "tool": 4,
    "request": 5,
})
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._/-][a-z0-9]+)*$")
_VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CHAT_TYPES = frozenset({"private", "group"})
_RENDERER_IDS = frozenset({"template", "runtime"})
_MULTIPLICITIES = frozenset({"singleton", "many"})
_TRACE_POLICIES = frozenset({"hash_and_size", "metadata_only"})
_DECLARATION_FIELDS = (
    "contribution_id",
    "kind",
    "input_contract",
    "output_contract",
    "priority",
    "before",
    "after",
    "required_variables",
    "multiplicity",
    "renderer_id",
    "sensitive_trace_policy",
    "trusted_builtin",
    "protected_invariants",
)


class PromptContributionError(RuntimeError):
    """Prompt Contribution 合同错误。"""


class PromptContributionConflictError(PromptContributionError):
    """同一插槽出现无法确定顺序的 singleton。"""


class PromptContributionOrderError(PromptContributionError):
    """Contribution 依赖与阶段顺序冲突。"""


class PromptContributionRendererError(PromptContributionError):
    """Contribution 渲染器缺失或违反 Port 合同。"""


def _normalize_ids(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(f"{field_name} 包含非法 ID: {value!r}")
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _normalize_scope(
    values: Iterable[str],
    *,
    field_name: str,
) -> frozenset[str]:
    normalized = frozenset(
        str(value or "").strip().lower()
        for value in values
        if str(value or "").strip()
    )
    if field_name == "chat_types":
        invalid = normalized - _CHAT_TYPES
        if invalid:
            raise ValueError(f"chat_types 不支持: {sorted(invalid)}")
    return normalized


@dataclass(frozen=True, slots=True)
class PromptContributionDescriptor:
    """一个可审计 Prompt 贡献的代码侧完整声明。"""

    contribution_id: str
    section_descriptor: PromptSectionDescriptor
    priority: int
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    platforms: frozenset[str] = frozenset()
    chat_types: frozenset[str] = frozenset()
    required_variables: frozenset[str] = frozenset()
    multiplicity: PromptContributionMultiplicity = "singleton"
    renderer_id: PromptContributionRendererId = "runtime"
    sensitive_trace_policy: PromptSensitiveTracePolicy = "hash_and_size"

    def __post_init__(self) -> None:
        contribution_id = str(self.contribution_id or "").strip()
        if not _IDENTIFIER_PATTERN.fullmatch(contribution_id):
            raise ValueError(
                f"Prompt contribution_id 非法: {contribution_id!r}"
            )
        if contribution_id != self.section_descriptor.section_id:
            raise ValueError(
                "Prompt contribution_id 必须与 section_descriptor.section_id 一致"
            )
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not 0 <= self.priority <= 1_000_000
        ):
            raise ValueError("Prompt contribution priority 必须是 0～1000000 的整数")
        before = _normalize_ids(self.before, field_name="before")
        after = _normalize_ids(
            (*self.after, *self.section_descriptor.dependencies),
            field_name="after",
        )
        if contribution_id in before or contribution_id in after:
            raise ValueError("Prompt Contribution 不能依赖自身")
        if set(before) & set(after):
            raise ValueError("Prompt Contribution 的 before/after 不能重叠")
        platforms = _normalize_scope(
            self.platforms,
            field_name="platforms",
        )
        chat_types = _normalize_scope(
            self.chat_types,
            field_name="chat_types",
        )
        required_variables = frozenset(
            str(value or "").strip()
            for value in self.required_variables
            if str(value or "").strip()
        )
        invalid_variables = sorted(
            value
            for value in required_variables
            if not _VARIABLE_PATTERN.fullmatch(value)
        )
        if invalid_variables:
            raise ValueError(
                f"required_variables 包含非法变量: {invalid_variables}"
            )
        if self.multiplicity not in _MULTIPLICITIES:
            raise ValueError(
                f"Prompt contribution multiplicity 不支持: {self.multiplicity}"
            )
        if self.renderer_id not in _RENDERER_IDS:
            raise ValueError(
                f"Prompt contribution renderer_id 不支持: {self.renderer_id}"
            )
        if self.sensitive_trace_policy not in _TRACE_POLICIES:
            raise ValueError(
                "Prompt contribution sensitive_trace_policy 不支持: "
                f"{self.sensitive_trace_policy}"
            )
        object.__setattr__(self, "contribution_id", contribution_id)
        object.__setattr__(self, "before", before)
        object.__setattr__(self, "after", after)
        object.__setattr__(self, "platforms", platforms)
        object.__setattr__(self, "chat_types", chat_types)
        object.__setattr__(self, "required_variables", required_variables)

    @property
    def owner_module(self) -> str:
        return self.section_descriptor.owner_module

    @property
    def domain(self) -> str:
        return self.section_descriptor.domain

    @property
    def phase(self) -> str:
        return self.section_descriptor.phase

    @property
    def authority(self) -> str:
        return self.section_descriptor.authority

    @property
    def trust(self) -> str:
        return self.section_descriptor.trust

    @property
    def source_precedence(self) -> tuple[str, ...]:
        return self.section_descriptor.source_precedence

    @property
    def editable(self) -> bool:
        return self.section_descriptor.editable

    @property
    def failure_policy(self) -> str:
        return self.section_descriptor.failure_policy

    @property
    def kind(self) -> RuntimeExtensionKind:
        return RuntimeExtensionKind.TRANSFORM

    @property
    def input_contract(self) -> str:
        return "prompt.contribution.render_context.v1"

    @property
    def output_contract(self) -> str:
        return "prompt.contribution.render_result.v1"

    @property
    def trusted_builtin(self) -> bool:
        # Flow 只能选择固定 template/runtime Renderer，不能注册执行代码。
        return True

    @property
    def protected_invariants(self) -> tuple[str, ...]:
        return PROTECTED_TRANSFORM_INVARIANTS

    def applies_to(self, *, platform: str, chat_type: str) -> bool:
        normalized_platform = str(platform or "qq").strip().lower() or "qq"
        normalized_chat_type = (
            str(chat_type or "private").strip().lower() or "private"
        )
        return (
            (not self.platforms or normalized_platform in self.platforms)
            and (not self.chat_types or normalized_chat_type in self.chat_types)
        )

    @property
    def registry_namespace(self) -> str:
        return "prompt_contribution"

    @property
    def registry_id(self) -> str:
        return self.contribution_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return self.after

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "contribution_id": self.contribution_id,
            "kind": self.kind.value,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "section": self.section_descriptor.to_dict(),
            "priority": self.priority,
            "before": list(self.before),
            "after": list(self.after),
            "platforms": sorted(self.platforms),
            "chat_types": sorted(self.chat_types),
            "required_variables": sorted(self.required_variables),
            "multiplicity": self.multiplicity,
            "renderer_id": self.renderer_id,
            "sensitive_trace_policy": self.sensitive_trace_policy,
            "trusted_builtin": self.trusted_builtin,
            "protected_invariants": list(self.protected_invariants),
        }

    def metadata(self) -> dict[str, object]:
        return {
            **self.section_descriptor.to_dict(),
            "contribution_id": self.contribution_id,
            "kind": self.kind.value,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "priority": self.priority,
            "before": list(self.before),
            "after": list(self.after),
            "platforms": sorted(self.platforms),
            "chat_types": sorted(self.chat_types),
            "required_variables": sorted(self.required_variables),
            "multiplicity": self.multiplicity,
            "renderer_id": self.renderer_id,
            "sensitive_trace_policy": self.sensitive_trace_policy,
            "trusted_builtin": self.trusted_builtin,
            "protected_invariants": list(self.protected_invariants),
        }


@dataclass(frozen=True, slots=True)
class PromptContributionResolution:
    """一次 platform/chat type 下的冻结、确定性 Contribution 投影。"""

    registry_snapshot: RegistrySnapshot[PromptContributionDescriptor]
    ordered_ids: tuple[str, ...]
    descriptors: Mapping[str, PromptContributionDescriptor]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "descriptors",
            MappingProxyType(dict(self.descriptors)),
        )

    @property
    def generation(self) -> int:
        return self.registry_snapshot.generation

    @property
    def sha256(self) -> str:
        return self.registry_snapshot.sha256


@dataclass(frozen=True, slots=True)
class PromptContributionRenderContext:
    """Renderer Port 的最小输入；不允许渲染器读取全局请求对象。"""

    descriptor: PromptContributionDescriptor
    node: Mapping[str, Any]
    template_values: Mapping[str, Any]
    runtime_sections: Mapping[str, Any]
    input_variables: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field_name in (
            "node",
            "template_values",
            "runtime_sections",
            "input_variables",
        ):
            value = getattr(self, field_name)
            object.__setattr__(
                self,
                field_name,
                MappingProxyType(dict(value)),
            )


@dataclass(frozen=True, slots=True)
class PromptContributionRenderResult:
    content: Any
    template_path: str = ""
    active_source: str = "request"
    template_resolution: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.template_resolution is not None:
            object.__setattr__(
                self,
                "template_resolution",
                MappingProxyType(dict(self.template_resolution)),
            )


@runtime_checkable
class PromptContributionRendererPort(Protocol):
    @property
    def renderer_id(self) -> str: ...

    def render(
        self,
        context: PromptContributionRenderContext,
    ) -> PromptContributionRenderResult: ...


def require_prompt_renderer(
    renderers: Mapping[str, PromptContributionRendererPort],
    descriptor: PromptContributionDescriptor,
) -> PromptContributionRendererPort:
    renderer = renderers.get(descriptor.renderer_id)
    if renderer is None:
        raise PromptContributionRendererError(
            f"Prompt Contribution {descriptor.contribution_id} 缺少渲染器 "
            f"{descriptor.renderer_id}"
        )
    if not isinstance(renderer, PromptContributionRendererPort):
        raise PromptContributionRendererError(
            f"Prompt renderer {descriptor.renderer_id} 不满足 Port 合同"
        )
    if renderer.renderer_id != descriptor.renderer_id:
        raise PromptContributionRendererError(
            f"Prompt renderer ID 不一致: {renderer.renderer_id!r}"
        )
    return renderer


def render_prompt_contribution(
    renderer: PromptContributionRendererPort,
    context: PromptContributionRenderContext,
) -> PromptContributionRenderResult:
    """执行受信内建 Renderer，并验证 Transform 输出合同。"""

    descriptor = context.descriptor
    if (
        descriptor.kind is not RuntimeExtensionKind.TRANSFORM
        or not descriptor.trusted_builtin
        or set(descriptor.protected_invariants)
        != set(PROTECTED_TRANSFORM_INVARIANTS)
    ):
        raise PromptContributionRendererError(
            "Prompt Contribution 未满足受信 Transform 合同"
        )
    if not isinstance(renderer, PromptContributionRendererPort):
        raise PromptContributionRendererError(
            "Prompt renderer 不满足 Port 合同"
        )
    if renderer.renderer_id != descriptor.renderer_id:
        raise PromptContributionRendererError(
            f"Prompt renderer ID 不一致: {renderer.renderer_id!r}"
        )
    result = renderer.render(context)
    if not isinstance(result, PromptContributionRenderResult):
        raise PromptContributionRendererError(
            "Prompt renderer 必须返回 PromptContributionRenderResult"
        )
    return result


def validate_prompt_contribution_inputs(
    context: PromptContributionRenderContext,
) -> None:
    """验证 Renderer 所需变量由编译输入显式提供，不检查值是否为空。"""

    available = set(context.input_variables)
    missing = sorted(context.descriptor.required_variables - available)
    if missing:
        raise PromptContributionRendererError(
            f"Prompt Contribution {context.descriptor.contribution_id} "
            f"缺少渲染变量: {missing}"
        )


class PromptContributionRegistry:
    """启动期构建、服务期冻结的 Prompt Contribution Registry。"""

    def __init__(
        self,
        descriptors: Iterable[PromptContributionDescriptor] = (),
    ) -> None:
        self._declared = tuple(descriptors)
        self._descriptors: Mapping[str, PromptContributionDescriptor] = (
            MappingProxyType({})
        )
        self._registry_snapshot: (
            RegistrySnapshot[PromptContributionDescriptor] | None
        ) = None

    @property
    def frozen(self) -> bool:
        return self._registry_snapshot is not None

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[PromptContributionDescriptor]:
        if self._registry_snapshot is None:
            raise PromptContributionError(
                "Prompt Contribution Registry 尚未冻结"
            )
        return self._registry_snapshot

    def freeze(
        self,
        *,
        generation: int = 1,
    ) -> "PromptContributionRegistry":
        if self._registry_snapshot is not None:
            return self
        declared_by_id: dict[str, PromptContributionDescriptor] = {}
        for descriptor in self._declared:
            if descriptor.contribution_id in declared_by_id:
                raise RegistryConflictError(
                    "Registry prompt_contribution 重复注册 ID: "
                    f"{descriptor.contribution_id}"
                )
            declared_by_id[descriptor.contribution_id] = descriptor

        dependencies = {
            descriptor_id: set(descriptor.after)
            for descriptor_id, descriptor in declared_by_id.items()
        }
        for descriptor in declared_by_id.values():
            for target in descriptor.before:
                if target not in declared_by_id:
                    raise RegistryDependencyError(
                        "Registry prompt_contribution 缺少依赖: "
                        f"{descriptor.contribution_id} before {target}"
                    )
                dependencies[target].add(descriptor.contribution_id)

        normalized: dict[str, PromptContributionDescriptor] = {}
        for descriptor_id, descriptor in declared_by_id.items():
            normalized[descriptor_id] = replace(
                descriptor,
                after=tuple(sorted(dependencies[descriptor_id])),
            )

        builder = RegistryBuilder[PromptContributionDescriptor](
            "prompt_contribution"
        )
        for descriptor_id in sorted(normalized):
            builder.register(normalized[descriptor_id])
        self._registry_snapshot = builder.freeze(generation=generation)
        self._descriptors = MappingProxyType(normalized)
        return self

    def resolve(
        self,
        active_ids: Iterable[str],
        *,
        platform: str,
        chat_type: str,
    ) -> PromptContributionResolution:
        snapshot = self.registry_snapshot
        requested_ids = frozenset(str(value) for value in active_ids)
        unknown = requested_ids - set(self._descriptors)
        if unknown:
            raise PromptContributionError(
                f"Prompt Contribution 未注册: {sorted(unknown)}"
            )
        applicable = {
            descriptor_id: descriptor
            for descriptor_id, descriptor in self._descriptors.items()
            if descriptor_id in requested_ids
            and descriptor.applies_to(
                platform=platform,
                chat_type=chat_type,
            )
        }
        applicable_ids = set(applicable)
        active_descriptors = {
            descriptor_id: replace(
                descriptor,
                after=tuple(
                    dependency
                    for dependency in descriptor.after
                    if dependency in applicable_ids
                ),
                before=tuple(
                    target
                    for target in descriptor.before
                    if target in applicable_ids
                ),
            )
            for descriptor_id, descriptor in applicable.items()
        }
        active_builder = RegistryBuilder[PromptContributionDescriptor](
            "prompt_contribution"
        )
        for descriptor_id in sorted(active_descriptors):
            active_builder.register(active_descriptors[descriptor_id])
        active_snapshot = active_builder.freeze(generation=snapshot.generation)
        ordered_ids = _resolve_prompt_order(active_descriptors)
        _validate_singleton_conflicts(active_descriptors, ordered_ids)
        return PromptContributionResolution(
            registry_snapshot=active_snapshot,
            ordered_ids=ordered_ids,
            descriptors=active_descriptors,
        )


def _depends_on(
    descriptors: Mapping[str, PromptContributionDescriptor],
    start: str,
    target: str,
) -> bool:
    pending = list(descriptors[start].after)
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in seen or current not in descriptors:
            continue
        seen.add(current)
        pending.extend(descriptors[current].after)
    return False


def _validate_singleton_conflicts(
    descriptors: Mapping[str, PromptContributionDescriptor],
    ordered_ids: Sequence[str],
) -> None:
    groups: dict[tuple[str, int], list[str]] = {}
    for descriptor_id in ordered_ids:
        descriptor = descriptors[descriptor_id]
        if descriptor.multiplicity != "singleton":
            continue
        groups.setdefault(
            (descriptor.phase, descriptor.priority),
            [],
        ).append(descriptor_id)
    for (phase, priority), descriptor_ids in groups.items():
        for index, left in enumerate(descriptor_ids):
            for right in descriptor_ids[index + 1:]:
                if _depends_on(descriptors, left, right) or _depends_on(
                    descriptors,
                    right,
                    left,
                ):
                    continue
                raise PromptContributionConflictError(
                    "Prompt singleton Contribution 顺序冲突: "
                    f"phase={phase}, priority={priority}, "
                    f"ids={left},{right}"
                )


def _resolve_prompt_order(
    descriptors: Mapping[str, PromptContributionDescriptor],
) -> tuple[str, ...]:
    remaining = set(descriptors)
    resolved: list[str] = []
    resolved_set: set[str] = set()
    while remaining:
        ready = [
            descriptor_id
            for descriptor_id in remaining
            if set(descriptors[descriptor_id].after).issubset(
                resolved_set
            )
        ]
        if not ready:
            raise PromptContributionOrderError(
                "Prompt Contribution 存在循环或未满足的依赖: "
                f"{sorted(remaining)}"
            )
        descriptor_id = min(
            ready,
            key=lambda candidate_id: (
                _PHASE_ORDER[descriptors[candidate_id].phase],
                descriptors[candidate_id].priority,
                candidate_id,
            ),
        )
        remaining.remove(descriptor_id)
        resolved.append(descriptor_id)
        resolved_set.add(descriptor_id)
    return tuple(resolved)


_CANONICAL_ORDERING = MappingProxyType({
    "base_contract": (100, (), ()),
    "qq_common_policy": (100, (), ("base_contract",)),
    "group_policy": (
        200,
        (),
        ("base_contract", "qq_common_policy"),
    ),
    "private_policy": (
        200,
        (),
        ("base_contract", "qq_common_policy"),
    ),
    "qq_group_policy": (300, (), ("group_policy",)),
    "identity_context": (
        100,
        (),
        ("group_policy", "private_policy", "qq_group_policy"),
    ),
    "session_guidance": (100, (), ("identity_context",)),
    "persona_reference": (200, (), ("session_guidance",)),
    "runtime_tool_prompt": (300, (), ("persona_reference",)),
    "conversation_context_header": (400, (), ("runtime_tool_prompt",)),
    "history_messages": (500, (), ("conversation_context_header",)),
    "group_context": (600, (), ("history_messages",)),
    "effort_constraint": (
        700,
        (),
        ("history_messages", "group_context"),
    ),
    "runtime_context": (800, (), ("effort_constraint",)),
    "current_user_event": (100, (), ("runtime_context",)),
})
_CANONICAL_SCOPES = MappingProxyType({
    "qq_common_policy": (frozenset({"qq"}), frozenset()),
    "group_policy": (frozenset(), frozenset({"group"})),
    "qq_group_policy": (frozenset({"qq"}), frozenset({"group"})),
    "private_policy": (frozenset(), frozenset({"private"})),
    "group_context": (frozenset(), frozenset({"group"})),
})
_CANONICAL_REQUIRED_VARIABLES = MappingProxyType({
    "identity_context": frozenset({
        "alias_names",
        "character_name",
        "is_super_user",
        "name_hint",
        "sender_id",
    }),
    "runtime_context": frozenset({
        "chat_type",
        "platform",
        "session_id",
        "user_id",
    }),
    "session_guidance": frozenset({"session_guidance"}),
    "persona_reference": frozenset({"persona_text", "user_id"}),
    "conversation_context_header": frozenset({"history_header"}),
    "history_messages": frozenset({"history_messages"}),
    "group_context": frozenset({"group_profile_context"}),
    "effort_constraint": frozenset({"effort_constraint"}),
    "runtime_tool_prompt": frozenset({"runtime_tool_prompt"}),
    "current_user_event": frozenset({"user_input"}),
})
_CANONICAL_TEMPLATE_IDS = frozenset({
    "base_contract",
    "qq_common_policy",
    "group_policy",
    "qq_group_policy",
    "private_policy",
    "identity_context",
})


def _canonical_descriptors() -> tuple[PromptContributionDescriptor, ...]:
    result: list[PromptContributionDescriptor] = []
    for section in list_canonical_section_descriptors():
        priority, before, after = _CANONICAL_ORDERING[section.section_id]
        platforms, chat_types = _CANONICAL_SCOPES.get(
            section.section_id,
            (frozenset(), frozenset()),
        )
        result.append(PromptContributionDescriptor(
            contribution_id=section.section_id,
            section_descriptor=section,
            priority=priority,
            before=before,
            after=after,
            platforms=platforms,
            chat_types=chat_types,
            required_variables=_CANONICAL_REQUIRED_VARIABLES.get(
                section.section_id,
                frozenset(),
            ),
            multiplicity="singleton",
            renderer_id=(
                "template"
                if section.section_id in _CANONICAL_TEMPLATE_IDS
                else "runtime"
            ),
            sensitive_trace_policy="hash_and_size",
        ))
    return tuple(result)


_CANONICAL_CONTRIBUTIONS = _canonical_descriptors()
PROMPT_CONTRIBUTION_REGISTRY = PromptContributionRegistry(
    _CANONICAL_CONTRIBUTIONS
).freeze()


def canonical_prompt_contributions() -> tuple[PromptContributionDescriptor, ...]:
    return tuple(PROMPT_CONTRIBUTION_REGISTRY.registry_snapshot)


def _extension_contribution(
    node: Mapping[str, Any],
    section: PromptSectionDescriptor,
) -> PromptContributionDescriptor:
    raw_chat_types = node.get("chat_types") or ()
    if isinstance(raw_chat_types, str):
        raw_chat_types = (raw_chat_types,)
    raw_platforms = node.get("platforms") or ()
    if isinstance(raw_platforms, str):
        raw_platforms = (raw_platforms,)
    return PromptContributionDescriptor(
        contribution_id=section.section_id,
        section_descriptor=section,
        priority=900_000,
        after=section.dependencies,
        platforms=frozenset(str(value) for value in raw_platforms),
        chat_types=frozenset(str(value) for value in raw_chat_types),
        required_variables=frozenset(),
        multiplicity="many",
        renderer_id=(
            "template"
            if str(node.get("type") or "") == "template"
            else "runtime"
        ),
        sensitive_trace_policy="hash_and_size",
    )


def resolve_prompt_contributions(
    flow: Mapping[str, Any],
    ordered_nodes: Sequence[Mapping[str, Any]],
    *,
    platform: str,
    chat_type: str,
) -> PromptContributionResolution:
    """把 Flow 选择结果投影到代码所有的 Contribution Registry。"""

    node_dicts = [dict(node) for node in ordered_nodes]
    sections = descriptors_for_ordered_nodes(
        dict(flow),
        node_dicts,
        chat_type,
        platform=platform,
    )
    canonical = PROMPT_CONTRIBUTION_REGISTRY.registry_snapshot.items
    contributions: list[PromptContributionDescriptor] = []
    for node, section in zip(node_dicts, sections, strict=True):
        registered = canonical.get(section.section_id)
        if registered is None:
            contributions.append(_extension_contribution(node, section))
            continue
        contributions.append(replace(
            registered,
            section_descriptor=section,
            after=tuple(
                dict.fromkeys(
                    (*registered.after, *section.dependencies)
                )
            ),
        ))
    active_ids = {item.contribution_id for item in contributions}
    contributions = [
        replace(
            item,
            after=tuple(
                dependency
                for dependency in item.after
                if dependency in active_ids
            ),
            before=tuple(
                target
                for target in item.before
                if target in active_ids
            ),
        )
        for item in contributions
    ]
    registry = PromptContributionRegistry(contributions).freeze(
        generation=PROMPT_CONTRIBUTION_REGISTRY.registry_snapshot.generation
    )
    return registry.resolve(
        active_ids,
        platform=platform,
        chat_type=chat_type,
    )


def contribution_for_node(
    node: Mapping[str, Any],
) -> PromptContributionDescriptor:
    """返回单节点的代码侧贡献声明，供 fallback 与 Flow 校验使用。"""

    section = descriptor_for_node(dict(node))
    registered = PROMPT_CONTRIBUTION_REGISTRY.registry_snapshot.get(
        section.section_id
    )
    if registered is not None:
        return replace(
            registered,
            section_descriptor=section,
        )
    return _extension_contribution(node, section)


def validate_node_contribution_declaration(
    node: Mapping[str, Any],
) -> None:
    """禁止 Flow JSON 覆盖代码侧 Contribution 的安全与排序声明。"""

    descriptor = contribution_for_node(node)
    expected = descriptor.metadata()
    for field in _DECLARATION_FIELDS:
        if field not in node:
            continue
        actual = node[field]
        if isinstance(expected.get(field), list) and isinstance(actual, tuple):
            actual = list(actual)
        if actual != expected.get(field):
            raise PromptContributionError(
                "Prompt contribution "
                f"{descriptor.contribution_id}.{field} 由代码侧固定为 "
                f"{expected.get(field)!r}，Flow 不能声明 {actual!r}"
            )
