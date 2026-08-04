"""声明式 Agent Manifest 的不可变值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.agent_runtime import (
    RuntimeCapability,
    RuntimePrincipal,
)

from .validation import (
    enum_value,
    identifier,
    media_type,
    reject_inline_secret,
    required_text,
    sha256,
    unique_identifiers,
    version,
)


class AgentSourceKind(str, Enum):
    BUILTIN = "builtin"
    PROJECT = "project"
    IMPORTED = "imported"


class AgentExtensionKind(str, Enum):
    SKILL = "skill"
    MCP_SERVER = "mcp_server"
    TOOL = "tool"
    HOOK = "hook"


class AgentHookEvent(str, Enum):
    BEFORE_PROMPT = "before_prompt"
    BEFORE_MODEL = "before_model"
    AFTER_MODEL = "after_model"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    ON_ERROR = "on_error"
    ON_COMPLETE = "on_complete"


@dataclass(frozen=True, slots=True)
class AgentSourceRef:
    kind: AgentSourceKind
    source_id: str
    revision: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            enum_value(self.kind, AgentSourceKind, "source.kind"),
        )
        object.__setattr__(
            self,
            "source_id",
            identifier(self.source_id, "source.source_id"),
        )
        object.__setattr__(
            self,
            "revision",
            required_text(self.revision, "source.revision", max_length=128),
        )


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    agent_id: str
    display_name: str
    description: str
    owner: RuntimePrincipal

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "agent_id",
            identifier(self.agent_id, "identity.agent_id"),
        )
        object.__setattr__(
            self,
            "display_name",
            required_text(
                self.display_name,
                "identity.display_name",
                max_length=128,
            ),
        )
        object.__setattr__(
            self,
            "description",
            required_text(
                self.description,
                "identity.description",
                max_length=2048,
            ),
        )
        if not isinstance(self.owner, RuntimePrincipal):
            raise ValueError("identity.owner 必须是 RuntimePrincipal")


@dataclass(frozen=True, slots=True)
class AgentRuntimeSpec:
    runtime_id: str
    required_capabilities: frozenset[RuntimeCapability]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "runtime_id",
            identifier(self.runtime_id, "runtime.runtime_id"),
        )
        capabilities = frozenset(
            enum_value(item, RuntimeCapability, "runtime.required_capability")
            for item in self.required_capabilities
        )
        if RuntimeCapability.RUN not in capabilities:
            raise ValueError("runtime.required_capabilities 必须包含 run")
        object.__setattr__(self, "required_capabilities", capabilities)


@dataclass(frozen=True, slots=True)
class AgentModelSpec:
    route_key: str
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "route_key",
            identifier(self.route_key, "model.route_key"),
        )
        object.__setattr__(
            self,
            "required_capabilities",
            unique_identifiers(
                self.required_capabilities,
                "model.required_capability",
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentPromptBundleRef:
    bundle_id: str
    version: str
    content_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "bundle_id",
            identifier(self.bundle_id, "prompt.bundle_id"),
        )
        object.__setattr__(
            self,
            "version",
            version(self.version, "prompt.version"),
        )
        object.__setattr__(
            self,
            "content_sha256",
            sha256(self.content_sha256, "prompt.content_sha256"),
        )


@dataclass(frozen=True, slots=True)
class AgentOutputContractRef:
    contract_id: str
    version: str
    media_types: tuple[str, ...] = ("text/plain",)
    schema_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "contract_id",
            identifier(self.contract_id, "output.contract_id"),
        )
        object.__setattr__(
            self,
            "version",
            version(self.version, "output.version"),
        )
        normalized_media = tuple(
            sorted(media_type(item, "output.media_type") for item in self.media_types)
        )
        if not normalized_media:
            raise ValueError("output.media_types 至少需要一项")
        if len(normalized_media) != len(set(normalized_media)):
            raise ValueError("output.media_types 不能重复")
        object.__setattr__(self, "media_types", normalized_media)
        object.__setattr__(
            self,
            "schema_sha256",
            sha256(
                self.schema_sha256,
                "output.schema_sha256",
                allow_empty=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentExtensionRef:
    kind: AgentExtensionKind
    provider_id: str
    resource_id: str
    version: str
    content_sha256: str = ""
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            enum_value(self.kind, AgentExtensionKind, "extension.kind"),
        )
        object.__setattr__(
            self,
            "provider_id",
            identifier(self.provider_id, "extension.provider_id"),
        )
        object.__setattr__(
            self,
            "resource_id",
            identifier(self.resource_id, "extension.resource_id"),
        )
        object.__setattr__(
            self,
            "version",
            version(self.version, "extension.version"),
        )
        object.__setattr__(
            self,
            "content_sha256",
            sha256(
                self.content_sha256,
                "extension.content_sha256",
                allow_empty=True,
            ),
        )
        if not isinstance(self.required, bool):
            raise ValueError("extension.required 必须是 bool")

    @property
    def qualified_id(self) -> str:
        return (
            f"{self.kind.value}:{self.provider_id}:"
            f"{self.resource_id}@{self.version}"
        )

    @property
    def binding_id(self) -> str:
        return f"{self.kind.value}:{self.provider_id}:{self.resource_id}"


@dataclass(frozen=True, slots=True)
class AgentHookRef:
    provider_id: str
    hook_id: str
    event: AgentHookEvent
    version: str
    order: int = 0
    content_sha256: str = ""
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_id",
            identifier(self.provider_id, "hook.provider_id"),
        )
        object.__setattr__(
            self,
            "hook_id",
            identifier(self.hook_id, "hook.hook_id"),
        )
        object.__setattr__(
            self,
            "event",
            enum_value(self.event, AgentHookEvent, "hook.event"),
        )
        object.__setattr__(self, "version", version(self.version, "hook.version"))
        if type(self.order) is not int or not -10_000 <= self.order <= 10_000:
            raise ValueError("hook.order 必须是 -10000 到 10000 的整数")
        object.__setattr__(
            self,
            "content_sha256",
            sha256(
                self.content_sha256,
                "hook.content_sha256",
                allow_empty=True,
            ),
        )
        if not isinstance(self.required, bool):
            raise ValueError("hook.required 必须是 bool")

    @property
    def qualified_id(self) -> str:
        return f"{self.provider_id}:{self.hook_id}@{self.version}"

    @property
    def binding_id(self) -> str:
        return f"hook:{self.provider_id}:{self.hook_id}"


@dataclass(frozen=True, slots=True)
class AgentExtensionSet:
    skills: tuple[AgentExtensionRef, ...] = ()
    mcp_servers: tuple[AgentExtensionRef, ...] = ()
    tools: tuple[AgentExtensionRef, ...] = ()
    hooks: tuple[AgentHookRef, ...] = ()

    def __post_init__(self) -> None:
        normalized_groups: list[tuple[str, AgentExtensionKind]] = [
            ("skills", AgentExtensionKind.SKILL),
            ("mcp_servers", AgentExtensionKind.MCP_SERVER),
            ("tools", AgentExtensionKind.TOOL),
        ]
        all_ids: list[str] = []
        for field_name, expected_kind in normalized_groups:
            values = tuple(getattr(self, field_name))
            if any(not isinstance(item, AgentExtensionRef) for item in values):
                raise ValueError(f"extensions.{field_name} 包含无效引用")
            if any(item.kind is not expected_kind for item in values):
                raise ValueError(f"extensions.{field_name} 的 kind 不匹配")
            values = tuple(sorted(values, key=lambda item: item.qualified_id))
            object.__setattr__(self, field_name, values)
            all_ids.extend(item.binding_id for item in values)
        hooks = tuple(self.hooks)
        if any(not isinstance(item, AgentHookRef) for item in hooks):
            raise ValueError("extensions.hooks 包含无效引用")
        hooks = tuple(
            sorted(
                hooks,
                key=lambda item: (
                    item.event.value,
                    item.order,
                    item.qualified_id,
                ),
            )
        )
        object.__setattr__(self, "hooks", hooks)
        all_ids.extend(item.binding_id for item in hooks)
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("extensions 不能包含重复引用")


@dataclass(frozen=True, slots=True)
class AgentStatePolicy:
    memory_policy_id: str
    workspace_profile_id: str
    artifact_policy_id: str
    sandbox_profile_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "memory_policy_id",
            "workspace_profile_id",
            "artifact_policy_id",
            "sandbox_profile_id",
        ):
            object.__setattr__(
                self,
                field_name,
                identifier(getattr(self, field_name), f"state.{field_name}"),
            )


@dataclass(frozen=True, slots=True)
class AgentSecretRef:
    binding: str
    provider_id: str
    secret_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "binding",
            identifier(self.binding, "secret.binding"),
        )
        object.__setattr__(
            self,
            "provider_id",
            identifier(self.provider_id, "secret.provider_id"),
        )
        object.__setattr__(
            self,
            "secret_id",
            reject_inline_secret(self.secret_id, "secret.secret_id"),
        )


@dataclass(frozen=True, slots=True)
class AgentPermissionSpec:
    policy_id: str
    required_actions: tuple[str, ...] = ()
    secret_refs: tuple[AgentSecretRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_id",
            identifier(self.policy_id, "permission.policy_id"),
        )
        object.__setattr__(
            self,
            "required_actions",
            unique_identifiers(
                self.required_actions,
                "permission.required_action",
            ),
        )
        refs = tuple(self.secret_refs)
        if any(not isinstance(item, AgentSecretRef) for item in refs):
            raise ValueError("permission.secret_refs 包含无效引用")
        refs = tuple(sorted(refs, key=lambda item: item.binding))
        bindings = [item.binding for item in refs]
        if len(bindings) != len(set(bindings)):
            raise ValueError("permission.secret_refs 的 binding 不能重复")
        object.__setattr__(self, "secret_refs", refs)


@dataclass(frozen=True, slots=True)
class AgentBudget:
    token_limit: int
    cost_limit_microunits: int
    step_limit: int
    time_limit_ms: int
    concurrency_limit: int

    def __post_init__(self) -> None:
        limits = {
            "token_limit": 100_000_000,
            "cost_limit_microunits": 10_000_000_000,
            "step_limit": 100_000,
            "time_limit_ms": 86_400_000,
            "concurrency_limit": 1024,
        }
        for field_name, maximum in limits.items():
            value = getattr(self, field_name)
            if type(value) is not int or not 0 < value <= maximum:
                raise ValueError(
                    f"budget.{field_name} 必须是 1 到 {maximum} 的整数"
                )


@dataclass(frozen=True, slots=True)
class AgentEvaluationPolicy:
    eval_set_ids: tuple[str, ...] = ()
    required_gate_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "eval_set_ids",
            unique_identifiers(self.eval_set_ids, "evaluation.eval_set_id"),
        )
        object.__setattr__(
            self,
            "required_gate_ids",
            unique_identifiers(
                self.required_gate_ids,
                "evaluation.required_gate_id",
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentRolloutPolicy:
    scope_ids: tuple[str, ...]
    percentage_basis_points: int = 10_000

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scope_ids",
            unique_identifiers(
                self.scope_ids,
                "rollout.scope_id",
                require_one=True,
            ),
        )
        if (
            type(self.percentage_basis_points) is not int
            or not 0 <= self.percentage_basis_points <= 10_000
        ):
            raise ValueError(
                "rollout.percentage_basis_points 必须是 0 到 10000 的整数"
            )
