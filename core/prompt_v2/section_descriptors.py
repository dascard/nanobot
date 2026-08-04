"""Prompt section 的类型化能力描述与权威边界。

Flow JSON 只负责选择节点和声明拓扑。节点的 phase、authority、trust 等安全
语义由代码侧注册表决定，运行时 Flow 不能自行把上下文提升为高权威指令。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from types import MappingProxyType
from typing import Any, Literal, Sequence

from core.registry import RegistryBuilder, RegistrySnapshot


PromptSectionPhase = Literal[
    "platform",
    "policy",
    "identity",
    "context",
    "tool",
    "request",
]
PromptSectionAuthority = Literal[
    "platform_security",
    "operator_policy",
    "application_policy",
    "tool",
    "user",
    "data",
]
PromptSectionTrust = Literal[
    "trusted_instruction",
    "untrusted_instruction",
    "trusted_data",
    "untrusted_data",
]
PromptSectionSource = Literal["runtime", "default", "built_in", "request"]
PromptSectionFailurePolicy = Literal["fail_closed", "skip_optional"]


class PromptSectionDescriptorError(ValueError):
    """Prompt section 的描述符声明不满足代码侧契约。"""


@dataclass(frozen=True)
class PromptSectionDescriptor:
    section_id: str
    owner_module: str
    domain: str
    phase: PromptSectionPhase
    authority: PromptSectionAuthority
    trust: PromptSectionTrust
    dependencies: tuple[str, ...]
    source_precedence: tuple[PromptSectionSource, ...]
    editable: bool
    failure_policy: PromptSectionFailurePolicy

    def with_dependencies(
        self,
        dependencies: Sequence[str],
    ) -> "PromptSectionDescriptor":
        normalized = tuple(dict.fromkeys(str(item) for item in dependencies if str(item)))
        if self.section_id in normalized:
            raise PromptSectionDescriptorError(
                f"Prompt section descriptor {self.section_id} 不能依赖自身"
            )
        return replace(self, dependencies=normalized)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["dependencies"] = list(self.dependencies)
        data["source_precedence"] = list(self.source_precedence)
        return data

    @property
    def registry_namespace(self) -> str:
        return "prompt_section"

    @property
    def registry_id(self) -> str:
        return self.section_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return self.dependencies

    def registry_payload(self) -> dict[str, Any]:
        return self.to_dict()


def _descriptor(
    section_id: str,
    *,
    owner_module: str,
    domain: str,
    phase: PromptSectionPhase,
    authority: PromptSectionAuthority,
    trust: PromptSectionTrust,
    editable: bool,
    failure_policy: PromptSectionFailurePolicy = "fail_closed",
    source_precedence: tuple[PromptSectionSource, ...] | None = None,
) -> PromptSectionDescriptor:
    if source_precedence is None:
        source_precedence = ("runtime", "default") if editable else ("request",)
    return PromptSectionDescriptor(
        section_id=section_id,
        owner_module=owner_module,
        domain=domain,
        phase=phase,
        authority=authority,
        trust=trust,
        dependencies=(),
        source_precedence=source_precedence,
        editable=editable,
        failure_policy=failure_policy,
    )


_CANONICAL_DESCRIPTORS = MappingProxyType({
    "base_contract": _descriptor(
        "base_contract",
        owner_module="core.prompt_v2",
        domain="chat_contract",
        phase="platform",
        # runtime/default 均可覆盖，因此它是 operator policy，不是假装成不可变
        # 的平台安全边界；真正安全不变量必须由代码执行。
        authority="operator_policy",
        trust="trusted_instruction",
        editable=True,
    ),
    "qq_common_policy": _descriptor(
        "qq_common_policy",
        owner_module="core.prompt_v2",
        domain="platform_policy",
        phase="policy",
        authority="operator_policy",
        trust="trusted_instruction",
        editable=True,
    ),
    "group_policy": _descriptor(
        "group_policy",
        owner_module="core.prompt_v2",
        domain="chat_routing",
        phase="policy",
        authority="application_policy",
        trust="trusted_instruction",
        editable=True,
    ),
    "qq_group_policy": _descriptor(
        "qq_group_policy",
        owner_module="core.prompt_v2",
        domain="platform_policy",
        phase="policy",
        authority="application_policy",
        trust="trusted_instruction",
        editable=True,
    ),
    "private_policy": _descriptor(
        "private_policy",
        owner_module="core.prompt_v2",
        domain="chat_routing",
        phase="policy",
        authority="application_policy",
        trust="trusted_instruction",
        editable=True,
    ),
    "runtime_context": _descriptor(
        "runtime_context",
        owner_module="core.prompt_v2.compiler",
        domain="request_context",
        phase="context",
        authority="data",
        trust="trusted_data",
        editable=False,
    ),
    "identity_context": _descriptor(
        "identity_context",
        owner_module="core.prompt_v2",
        domain="identity",
        phase="identity",
        authority="application_policy",
        trust="trusted_instruction",
        editable=True,
    ),
    "session_guidance": _descriptor(
        "session_guidance",
        owner_module="core.prompt_v2.compiler",
        domain="session_memory",
        phase="policy",
        authority="operator_policy",
        trust="trusted_instruction",
        editable=False,
    ),
    "persona_reference": _descriptor(
        "persona_reference",
        owner_module="core.prompt_v2.compiler",
        domain="persona",
        phase="context",
        authority="data",
        trust="untrusted_data",
        editable=False,
    ),
    "conversation_context_header": _descriptor(
        "conversation_context_header",
        owner_module="core.prompt_v2.compiler",
        domain="conversation",
        phase="context",
        authority="data",
        trust="trusted_data",
        editable=False,
    ),
    "history_messages": _descriptor(
        "history_messages",
        owner_module="core.prompt_v2.compiler",
        domain="conversation",
        phase="context",
        authority="data",
        trust="untrusted_data",
        editable=False,
    ),
    "group_context": _descriptor(
        "group_context",
        owner_module="app.group_memory",
        domain="group_memory",
        phase="context",
        authority="data",
        trust="untrusted_data",
        editable=False,
        failure_policy="skip_optional",
    ),
    "project_context": _descriptor(
        "project_context",
        owner_module="core.prompt_v2.compiler",
        domain="project_context",
        phase="context",
        authority="data",
        trust="untrusted_data",
        editable=False,
        failure_policy="skip_optional",
    ),
    "summary_context": _descriptor(
        "summary_context",
        owner_module="app.session_memory",
        domain="conversation_summary",
        phase="context",
        authority="data",
        trust="untrusted_data",
        editable=False,
        failure_policy="skip_optional",
    ),
    "current_user_event": _descriptor(
        "current_user_event",
        owner_module="core.prompt_v2.compiler",
        domain="request",
        phase="request",
        authority="user",
        trust="untrusted_instruction",
        editable=False,
    ),
})

_CANONICAL_BY_TEMPLATE_KEY = MappingProxyType({
    "chat/main": "base_contract",
    "chat/platform/qq/common": "qq_common_policy",
    "chat/branch_group": "group_policy",
    "chat/platform/qq/group": "qq_group_policy",
    "chat/branch_private": "private_policy",
    "chat/identity_context": "identity_context",
})
_CANONICAL_BY_RUNTIME_KEY = MappingProxyType({
    key: key
    for key in (
        "runtime_context",
        "session_guidance",
        "persona_reference",
        "conversation_context_header",
        "history_messages",
        "group_context",
        "project_context",
        "summary_context",
        "current_user_event",
    )
})


def _build_prompt_section_registry(
) -> RegistrySnapshot[PromptSectionDescriptor]:
    builder = RegistryBuilder[PromptSectionDescriptor](
        "prompt_section"
    )
    for descriptor_id in sorted(_CANONICAL_DESCRIPTORS):
        builder.register(_CANONICAL_DESCRIPTORS[descriptor_id])
    return builder.freeze()


PROMPT_SECTION_REGISTRY = _build_prompt_section_registry()

_DECLARATION_FIELDS = (
    "owner_module",
    "domain",
    "phase",
    "authority",
    "trust",
    "source_precedence",
    "editable",
    "failure_policy",
)


def list_canonical_section_descriptors() -> tuple[PromptSectionDescriptor, ...]:
    """返回代码侧冻结的 canonical section 描述符。"""

    return tuple(PROMPT_SECTION_REGISTRY)


def descriptor_for_template_key(
    template_key: str,
) -> PromptSectionDescriptor | None:
    descriptor_id = _CANONICAL_BY_TEMPLATE_KEY.get(str(template_key or "").strip())
    return _CANONICAL_DESCRIPTORS.get(descriptor_id) if descriptor_id else None


def _canonical_descriptor_id(node: dict[str, Any]) -> str | None:
    node_id = str(node.get("id") or "").strip()
    if node_id in _CANONICAL_DESCRIPTORS:
        return node_id
    template_key = str(node.get("template_key") or "").strip()
    if template_key in _CANONICAL_BY_TEMPLATE_KEY:
        return _CANONICAL_BY_TEMPLATE_KEY[template_key]
    runtime_key = str(node.get("runtime_key") or "").strip()
    return _CANONICAL_BY_RUNTIME_KEY.get(runtime_key)


def descriptor_for_node(
    node: dict[str, Any],
    *,
    dependencies: Sequence[str] = (),
) -> PromptSectionDescriptor:
    """解析节点描述符；未知扩展默认只能作为低权威、不可信数据。"""

    node_id = str(node.get("id") or "").strip()
    canonical_id = _canonical_descriptor_id(node)
    if canonical_id is not None:
        descriptor = _CANONICAL_DESCRIPTORS[canonical_id]
        if node_id and node_id != canonical_id:
            descriptor = replace(descriptor, section_id=node_id)
        return descriptor.with_dependencies(dependencies)

    node_type = str(node.get("type") or "").strip()
    source_precedence: tuple[PromptSectionSource, ...]
    editable = node_type == "template"
    source_precedence = ("runtime", "default") if editable else ("request",)
    return PromptSectionDescriptor(
        section_id=node_id,
        owner_module="extension",
        domain="extension_context",
        phase="context",
        authority="data",
        trust="untrusted_data",
        dependencies=(),
        source_precedence=source_precedence,
        editable=editable,
        failure_policy="skip_optional",
    ).with_dependencies(dependencies)


def validate_node_descriptor_declaration(node: dict[str, Any]) -> None:
    """拒绝 Flow JSON 覆盖代码侧的权威和信任声明。"""

    descriptor = descriptor_for_node(node)
    expected = descriptor.to_dict()
    for field in _DECLARATION_FIELDS:
        if field not in node:
            continue
        actual = node[field]
        if field == "source_precedence" and isinstance(actual, tuple):
            actual = list(actual)
        if actual != expected[field]:
            raise PromptSectionDescriptorError(
                f"Prompt section descriptor {descriptor.section_id}.{field} "
                f"由代码侧固定为 {expected[field]!r}，Flow 不能声明 {actual!r}"
            )
    if "dependencies" in node:
        raise PromptSectionDescriptorError(
            f"Prompt section descriptor {descriptor.section_id}.dependencies "
            "必须由 flow.edges 派生"
        )


def validate_descriptor_source(
    descriptor: PromptSectionDescriptor,
    active_source: str,
) -> None:
    source = str(active_source or "").strip()
    if source not in descriptor.source_precedence:
        raise PromptSectionDescriptorError(
            f"Prompt section descriptor {descriptor.section_id} source precedence "
            f"不允许来源 {source or '<empty>'}; "
            f"允许顺序为 {descriptor.source_precedence!r}"
        )


def _item_applies(item: dict[str, Any], chat_type: str, platform: str) -> bool:
    chat_types = item.get("chat_types")
    if chat_types:
        if isinstance(chat_types, str):
            chat_types = [chat_types]
        if chat_type not in {str(value).strip().lower() for value in chat_types}:
            return False
    platforms = item.get("platforms")
    if not platforms:
        return True
    if isinstance(platforms, str):
        platforms = [platforms]
    return platform in {str(value).strip().lower() for value in platforms}


def descriptors_for_ordered_nodes(
    flow: dict[str, Any],
    ordered_nodes: Sequence[dict[str, Any]],
    chat_type: str,
    *,
    platform: str,
) -> list[PromptSectionDescriptor]:
    normalized_chat_type = str(chat_type or "private").strip().lower() or "private"
    normalized_platform = str(platform or "qq").strip().lower() or "qq"
    ordered_ids = [str(node.get("id") or "").strip() for node in ordered_nodes]
    active_ids = set(ordered_ids)
    incoming: dict[str, list[str]] = {node_id: [] for node_id in ordered_ids}
    for raw_edge in flow.get("edges") or []:
        edge = dict(raw_edge or {})
        start = str(edge.get("from") or "").strip()
        end = str(edge.get("to") or "").strip()
        if start not in active_ids or end not in active_ids:
            continue
        if not _item_applies(edge, normalized_chat_type, normalized_platform):
            continue
        incoming[end].append(start)
    position = {node_id: index for index, node_id in enumerate(ordered_ids)}
    result: list[PromptSectionDescriptor] = []
    for node in ordered_nodes:
        node_id = str(node.get("id") or "").strip()
        dependencies = sorted(
            set(incoming.get(node_id, ())),
            key=lambda item: position.get(item, len(position)),
        )
        result.append(descriptor_for_node(node, dependencies=dependencies))
    return result


def describe_flow_sections_for_chat(
    flow: dict[str, Any],
    chat_type: str,
    *,
    platform: str = "qq",
) -> list[PromptSectionDescriptor]:
    from core.prompt_v2.flow import ordered_nodes_for_chat

    ordered_nodes = ordered_nodes_for_chat(flow, chat_type, platform=platform)
    return descriptors_for_ordered_nodes(
        flow,
        ordered_nodes,
        chat_type,
        platform=platform,
    )
