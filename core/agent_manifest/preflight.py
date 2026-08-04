"""Agent Manifest 编译前使用的不可变环境快照与诊断合同。"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import Any

from core.agent_runtime import RuntimeCapabilities, RuntimePrincipal

from .canonical import canonical_value, content_sha256
from .validation import (
    enum_value,
    identifier,
    required_text,
    sha256,
    unique_identifiers,
    version,
)
from .values import AgentExtensionKind, AgentSecretRef


class AgentPinnedResourceKind(str, Enum):
    PROMPT_BUNDLE = "prompt_bundle"
    OUTPUT_CONTRACT = "output_contract"


class AgentExportNamespace(str, Enum):
    SKILL = "skill"
    MCP_SERVER = "mcp_server"
    TOOL = "tool"
    HOOK = "hook"


class AgentDiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


def _snapshot_digest(value: object, *, excluded: frozenset[str]) -> str:
    payload = {
        item.name: canonical_value(getattr(value, item.name))
        for item in fields(value)
        if item.name not in excluded
    }
    return content_sha256(payload)


def _freeze_snapshot_digest(
    value: object,
    declared: object,
    *,
    name: str,
) -> str:
    digest = _snapshot_digest(value, excluded=frozenset({"snapshot_sha256"}))
    normalized = sha256(declared, name, allow_empty=True)
    if normalized and normalized != digest:
        raise ValueError(f"{name} 与快照内容不匹配")
    return digest


@dataclass(frozen=True, slots=True)
class AgentManifestDiagnostic:
    severity: AgentDiagnosticSeverity
    code: str
    path: str
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "severity",
            enum_value(
                self.severity,
                AgentDiagnosticSeverity,
                "diagnostic.severity",
            ),
        )
        object.__setattr__(
            self,
            "code",
            identifier(self.code, "diagnostic.code"),
        )
        object.__setattr__(
            self,
            "path",
            identifier(self.path, "diagnostic.path"),
        )
        object.__setattr__(
            self,
            "message",
            required_text(
                self.message,
                "diagnostic.message",
                max_length=1024,
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentPinnedResourceSnapshot:
    kind: AgentPinnedResourceKind
    resource_id: str
    version: str
    content_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            enum_value(
                self.kind,
                AgentPinnedResourceKind,
                "resource.kind",
            ),
        )
        object.__setattr__(
            self,
            "resource_id",
            identifier(self.resource_id, "resource.resource_id"),
        )
        object.__setattr__(
            self,
            "version",
            version(self.version, "resource.version"),
        )
        object.__setattr__(
            self,
            "content_sha256",
            sha256(self.content_sha256, "resource.content_sha256"),
        )

    @property
    def identity(self) -> tuple[AgentPinnedResourceKind, str, str]:
        return self.kind, self.resource_id, self.version


@dataclass(frozen=True, slots=True)
class AgentModelRouteSnapshot:
    route_key: str
    capabilities: frozenset[str]
    revision: str
    available: bool = True
    snapshot_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "route_key",
            identifier(self.route_key, "model_route.route_key"),
        )
        object.__setattr__(
            self,
            "capabilities",
            frozenset(
                unique_identifiers(
                    self.capabilities,
                    "model_route.capability",
                )
            ),
        )
        object.__setattr__(
            self,
            "revision",
            required_text(
                self.revision,
                "model_route.revision",
                max_length=128,
            ),
        )
        if not isinstance(self.available, bool):
            raise ValueError("model_route.available 必须是 bool")
        object.__setattr__(
            self,
            "snapshot_sha256",
            _freeze_snapshot_digest(
                self,
                self.snapshot_sha256,
                name="model_route.snapshot_sha256",
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentExportedName:
    namespace: AgentExportNamespace
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "namespace",
            enum_value(
                self.namespace,
                AgentExportNamespace,
                "extension_export.namespace",
            ),
        )
        object.__setattr__(
            self,
            "name",
            identifier(self.name, "extension_export.name"),
        )

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace.value}:{self.name}"


@dataclass(frozen=True, slots=True)
class AgentExtensionCatalogEntry:
    kind: AgentExtensionKind
    provider_id: str
    resource_id: str
    version: str
    content_sha256: str
    exports: tuple[AgentExportedName, ...]
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            enum_value(
                self.kind,
                AgentExtensionKind,
                "extension_entry.kind",
            ),
        )
        object.__setattr__(
            self,
            "provider_id",
            identifier(self.provider_id, "extension_entry.provider_id"),
        )
        object.__setattr__(
            self,
            "resource_id",
            identifier(self.resource_id, "extension_entry.resource_id"),
        )
        object.__setattr__(
            self,
            "version",
            version(self.version, "extension_entry.version"),
        )
        object.__setattr__(
            self,
            "content_sha256",
            sha256(
                self.content_sha256,
                "extension_entry.content_sha256",
            ),
        )
        exports = tuple(
            sorted(self.exports, key=lambda item: item.qualified_name)
        )
        if not exports or any(
            not isinstance(item, AgentExportedName) for item in exports
        ):
            raise ValueError("extension_entry.exports 至少需要一个有效导出")
        names = [item.qualified_name for item in exports]
        if len(names) != len(set(names)):
            raise ValueError("extension_entry.exports 不能重复")
        object.__setattr__(self, "exports", exports)
        object.__setattr__(
            self,
            "dependencies",
            unique_identifiers(
                self.dependencies,
                "extension_entry.dependency",
            ),
        )

    @property
    def binding_id(self) -> str:
        return f"{self.kind.value}:{self.provider_id}:{self.resource_id}"

    @property
    def qualified_id(self) -> str:
        return f"{self.binding_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class AgentExtensionCatalog:
    revision: str
    entries: tuple[AgentExtensionCatalogEntry, ...]
    snapshot_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "revision",
            required_text(
                self.revision,
                "extension_catalog.revision",
                max_length=128,
            ),
        )
        entries = tuple(sorted(self.entries, key=lambda item: item.qualified_id))
        if any(not isinstance(item, AgentExtensionCatalogEntry) for item in entries):
            raise ValueError("extension_catalog.entries 包含无效条目")
        identities = [item.qualified_id for item in entries]
        if len(identities) != len(set(identities)):
            raise ValueError("extension_catalog.entries 不能包含重复版本")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(
            self,
            "snapshot_sha256",
            _freeze_snapshot_digest(
                self,
                self.snapshot_sha256,
                name="extension_catalog.snapshot_sha256",
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentWorkspacePreflightSnapshot:
    owner: RuntimePrincipal
    workspace_profile_id: str
    memory_policy_ids: frozenset[str]
    artifact_policy_ids: frozenset[str]
    sandbox_profile_ids: frozenset[str]
    quota_bytes: int
    used_bytes: int
    acl_allowed: bool
    quota_ready: bool
    sandbox_ready: bool
    snapshot_sha256: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.owner, RuntimePrincipal):
            raise ValueError("workspace.owner 必须是 RuntimePrincipal")
        object.__setattr__(
            self,
            "workspace_profile_id",
            identifier(
                self.workspace_profile_id,
                "workspace.workspace_profile_id",
            ),
        )
        for field_name in (
            "memory_policy_ids",
            "artifact_policy_ids",
            "sandbox_profile_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                frozenset(
                    unique_identifiers(
                        getattr(self, field_name),
                        f"workspace.{field_name}",
                    )
                ),
            )
        for field_name in ("quota_bytes", "used_bytes"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"workspace.{field_name} 必须是非负整数")
        for field_name in ("acl_allowed", "quota_ready", "sandbox_ready"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"workspace.{field_name} 必须是 bool")
        object.__setattr__(
            self,
            "snapshot_sha256",
            _freeze_snapshot_digest(
                self,
                self.snapshot_sha256,
                name="workspace.snapshot_sha256",
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentSecurityPreflightSnapshot:
    permission_policy_ids: frozenset[str]
    allowed_actions: frozenset[str]
    secret_refs: tuple[AgentSecretRef, ...]
    snapshot_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "permission_policy_ids",
            frozenset(
                unique_identifiers(
                    self.permission_policy_ids,
                    "security.permission_policy_id",
                )
            ),
        )
        object.__setattr__(
            self,
            "allowed_actions",
            frozenset(
                unique_identifiers(
                    self.allowed_actions,
                    "security.allowed_action",
                )
            ),
        )
        refs = tuple(
            sorted(
                self.secret_refs,
                key=lambda item: (
                    item.binding,
                    item.provider_id,
                    item.secret_id,
                ),
            )
        )
        if any(not isinstance(item, AgentSecretRef) for item in refs):
            raise ValueError("security.secret_refs 包含无效引用")
        identities = [
            (item.provider_id, item.secret_id)
            for item in refs
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("security.secret_refs 不能重复")
        object.__setattr__(self, "secret_refs", refs)
        object.__setattr__(
            self,
            "snapshot_sha256",
            _freeze_snapshot_digest(
                self,
                self.snapshot_sha256,
                name="security.snapshot_sha256",
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentBudgetCeilings:
    token_limit: int
    cost_limit_microunits: int
    step_limit: int
    time_limit_ms: int
    concurrency_limit: int

    def __post_init__(self) -> None:
        for field_name in (
            "token_limit",
            "cost_limit_microunits",
            "step_limit",
            "time_limit_ms",
            "concurrency_limit",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"budget_ceiling.{field_name} 必须是正整数")


@dataclass(frozen=True, slots=True)
class AgentManifestCompilationEnvironment:
    revision: str
    runtime: RuntimeCapabilities
    model_route: AgentModelRouteSnapshot
    pinned_resources: tuple[AgentPinnedResourceSnapshot, ...]
    extensions: AgentExtensionCatalog
    workspace: AgentWorkspacePreflightSnapshot
    security: AgentSecurityPreflightSnapshot
    budget_ceilings: AgentBudgetCeilings
    snapshot_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "revision",
            required_text(
                self.revision,
                "compilation_environment.revision",
                max_length=128,
            ),
        )
        expected_types = {
            "runtime": RuntimeCapabilities,
            "model_route": AgentModelRouteSnapshot,
            "extensions": AgentExtensionCatalog,
            "workspace": AgentWorkspacePreflightSnapshot,
            "security": AgentSecurityPreflightSnapshot,
            "budget_ceilings": AgentBudgetCeilings,
        }
        for field_name, expected_type in expected_types.items():
            if not isinstance(getattr(self, field_name), expected_type):
                raise ValueError(
                    f"compilation_environment.{field_name} 必须是 "
                    f"{expected_type.__name__}"
                )
        resources = tuple(
            sorted(
                self.pinned_resources,
                key=lambda item: (
                    item.kind.value,
                    item.resource_id,
                    item.version,
                ),
            )
        )
        if any(not isinstance(item, AgentPinnedResourceSnapshot) for item in resources):
            raise ValueError("compilation_environment.pinned_resources 包含无效条目")
        identities = [item.identity for item in resources]
        if len(identities) != len(set(identities)):
            raise ValueError("compilation_environment.pinned_resources 不能重复")
        object.__setattr__(self, "pinned_resources", resources)
        object.__setattr__(
            self,
            "snapshot_sha256",
            _freeze_snapshot_digest(
                self,
                self.snapshot_sha256,
                name="compilation_environment.snapshot_sha256",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        value = canonical_value(self)
        if not isinstance(value, dict):
            raise TypeError("编译环境序列化结果无效")
        return value
