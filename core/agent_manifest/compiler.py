"""把 Agent Manifest 预检并编译为不可变运行快照。"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from core.agent_runtime import RuntimeCapabilities

from .canonical import canonical_json, canonical_value, content_sha256
from .contracts import AgentManifest
from .preflight import (
    AgentBudgetCeilings,
    AgentDiagnosticSeverity,
    AgentExtensionCatalogEntry,
    AgentManifestCompilationEnvironment,
    AgentManifestDiagnostic,
    AgentModelRouteSnapshot,
    AgentPinnedResourceKind,
    AgentPinnedResourceSnapshot,
)
from .validation import sha256
from .values import AgentExtensionKind


class AgentManifestCompilationError(RuntimeError):
    """Manifest 预检未通过；只暴露稳定诊断，不回显秘密引用 ID。"""

    def __init__(self, diagnostics: tuple[AgentManifestDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        error_codes = tuple(
            item.code
            for item in diagnostics
            if item.severity is AgentDiagnosticSeverity.ERROR
        )
        super().__init__(
            "Agent Manifest 预检失败：" + ", ".join(error_codes)
        )


def _snapshot_payload(value: object) -> dict[str, object]:
    return {
        item.name: canonical_value(getattr(value, item.name))
        for item in fields(value)
        if item.name != "snapshot_sha256"
    }


@dataclass(frozen=True, slots=True)
class CompiledAgentSnapshot:
    manifest: AgentManifest
    runtime: RuntimeCapabilities
    model_route: AgentModelRouteSnapshot
    prompt_resource: AgentPinnedResourceSnapshot
    output_resource: AgentPinnedResourceSnapshot
    extensions: tuple[AgentExtensionCatalogEntry, ...]
    environment_revision: str
    environment_sha256: str
    workspace_snapshot_sha256: str
    security_snapshot_sha256: str
    budget_ceilings: AgentBudgetCeilings
    diagnostics: tuple[AgentManifestDiagnostic, ...]
    snapshot_sha256: str = ""

    def __post_init__(self) -> None:
        expected_types = {
            "manifest": AgentManifest,
            "runtime": RuntimeCapabilities,
            "model_route": AgentModelRouteSnapshot,
            "prompt_resource": AgentPinnedResourceSnapshot,
            "output_resource": AgentPinnedResourceSnapshot,
            "budget_ceilings": AgentBudgetCeilings,
        }
        for field_name, expected_type in expected_types.items():
            if not isinstance(getattr(self, field_name), expected_type):
                raise ValueError(
                    f"compiled.{field_name} 必须是 {expected_type.__name__}"
                )
        extensions = tuple(
            sorted(self.extensions, key=lambda item: item.qualified_id)
        )
        if any(not isinstance(item, AgentExtensionCatalogEntry) for item in extensions):
            raise ValueError("compiled.extensions 包含无效条目")
        object.__setattr__(self, "extensions", extensions)
        diagnostics = tuple(self.diagnostics)
        if any(not isinstance(item, AgentManifestDiagnostic) for item in diagnostics):
            raise ValueError("compiled.diagnostics 包含无效诊断")
        if any(
            item.severity is AgentDiagnosticSeverity.ERROR
            for item in diagnostics
        ):
            raise ValueError("compiled.diagnostics 不能包含 error")
        object.__setattr__(self, "diagnostics", diagnostics)
        for field_name in (
            "environment_sha256",
            "workspace_snapshot_sha256",
            "security_snapshot_sha256",
        ):
            object.__setattr__(
                self,
                field_name,
                sha256(getattr(self, field_name), f"compiled.{field_name}"),
            )
        digest = content_sha256(_snapshot_payload(self))
        declared = sha256(
            self.snapshot_sha256,
            "compiled.snapshot_sha256",
            allow_empty=True,
        )
        if declared and declared != digest:
            raise ValueError("compiled.snapshot_sha256 与快照内容不匹配")
        object.__setattr__(self, "snapshot_sha256", digest)

    def to_dict(self) -> dict[str, Any]:
        value = canonical_value(self)
        if not isinstance(value, dict):
            raise TypeError("编译快照序列化结果无效")
        return value

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())


def _diagnostic(
    severity: AgentDiagnosticSeverity,
    code: str,
    path: str,
    message: str,
) -> AgentManifestDiagnostic:
    return AgentManifestDiagnostic(
        severity=severity,
        code=code,
        path=path,
        message=message,
    )


def _stage_passed(
    diagnostics: list[AgentManifestDiagnostic],
    start: int,
    *,
    code: str,
    path: str,
    message: str,
) -> None:
    if any(
        item.severity is AgentDiagnosticSeverity.ERROR
        for item in diagnostics[start:]
    ):
        return
    diagnostics.append(
        _diagnostic(
            AgentDiagnosticSeverity.INFO,
            code,
            path,
            message,
        )
    )


def _find_pinned_resource(
    environment: AgentManifestCompilationEnvironment,
    *,
    kind: AgentPinnedResourceKind,
    resource_id: str,
    version: str,
) -> AgentPinnedResourceSnapshot | None:
    target = kind, resource_id, version
    for resource in environment.pinned_resources:
        if resource.identity == target:
            return resource
    return None


def _check_runtime(
    manifest: AgentManifest,
    environment: AgentManifestCompilationEnvironment,
    diagnostics: list[AgentManifestDiagnostic],
) -> None:
    start = len(diagnostics)
    if environment.runtime.runtime_id != manifest.runtime.runtime_id:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "runtime.id_mismatch",
                "runtime.runtime_id",
                "目标 Runtime 与编译环境不一致",
            )
        )
    missing = environment.runtime.missing(
        manifest.runtime.required_capabilities
    )
    if missing:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "runtime.capability_missing",
                "runtime.required_capabilities",
                "Runtime 缺少最低能力："
                + ", ".join(item.value for item in missing),
            )
        )
    _stage_passed(
        diagnostics,
        start,
        code="runtime.validated",
        path="runtime",
        message="Runtime 身份与最低能力已固定",
    )


def _check_model(
    manifest: AgentManifest,
    environment: AgentManifestCompilationEnvironment,
    diagnostics: list[AgentManifestDiagnostic],
) -> None:
    start = len(diagnostics)
    route = environment.model_route
    if route.route_key != manifest.model.route_key:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "model.route_mismatch",
                "model.route_key",
                "模型 Route 与编译环境不一致",
            )
        )
    if not route.available:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "model.route_unavailable",
                "model.route_key",
                "模型 Route 当前不可用",
            )
        )
    missing = tuple(
        sorted(
            set(manifest.model.required_capabilities) - route.capabilities
        )
    )
    if missing:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "model.capability_missing",
                "model.required_capabilities",
                "模型 Route 缺少能力：" + ", ".join(missing),
            )
        )
    _stage_passed(
        diagnostics,
        start,
        code="model.validated",
        path="model",
        message="模型 Route 与最低能力已固定",
    )


def _check_pinned_resources(
    manifest: AgentManifest,
    environment: AgentManifestCompilationEnvironment,
    diagnostics: list[AgentManifestDiagnostic],
) -> tuple[
    AgentPinnedResourceSnapshot | None,
    AgentPinnedResourceSnapshot | None,
]:
    start = len(diagnostics)
    prompt = _find_pinned_resource(
        environment,
        kind=AgentPinnedResourceKind.PROMPT_BUNDLE,
        resource_id=manifest.prompt.bundle_id,
        version=manifest.prompt.version,
    )
    if prompt is None:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "prompt.bundle_missing",
                "prompt",
                "Prompt bundle 不存在或版本不匹配",
            )
        )
    elif prompt.content_sha256 != manifest.prompt.content_sha256:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "prompt.digest_mismatch",
                "prompt.content_sha256",
                "Prompt bundle 内容摘要不匹配",
            )
        )

    output = _find_pinned_resource(
        environment,
        kind=AgentPinnedResourceKind.OUTPUT_CONTRACT,
        resource_id=manifest.output.contract_id,
        version=manifest.output.version,
    )
    if output is None:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "output.contract_missing",
                "output",
                "输出合同不存在或版本不匹配",
            )
        )
    elif (
        manifest.output.schema_sha256
        and output.content_sha256 != manifest.output.schema_sha256
    ):
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "output.digest_mismatch",
                "output.schema_sha256",
                "输出合同 schema 摘要不匹配",
            )
        )
    _stage_passed(
        diagnostics,
        start,
        code="resources.validated",
        path="prompt",
        message="Prompt bundle 与输出合同已按版本和摘要固定",
    )
    return prompt, output


def _requested_extensions(
    manifest: AgentManifest,
) -> tuple[tuple[AgentExtensionKind, str, str, str, str, str], ...]:
    requested: list[
        tuple[AgentExtensionKind, str, str, str, str, str]
    ] = []
    for references in (
        manifest.extensions.skills,
        manifest.extensions.mcp_servers,
        manifest.extensions.tools,
    ):
        requested.extend(
            (
                item.kind,
                item.provider_id,
                item.resource_id,
                item.version,
                item.content_sha256,
                item.binding_id,
            )
            for item in references
        )
    requested.extend(
        (
            AgentExtensionKind.HOOK,
            item.provider_id,
            item.hook_id,
            item.version,
            item.content_sha256,
            item.binding_id,
        )
        for item in manifest.extensions.hooks
    )
    return tuple(sorted(requested, key=lambda item: (*item[:4], item[5])))


def _check_extensions(
    manifest: AgentManifest,
    environment: AgentManifestCompilationEnvironment,
    diagnostics: list[AgentManifestDiagnostic],
) -> tuple[AgentExtensionCatalogEntry, ...]:
    start = len(diagnostics)
    catalog = {
        (entry.kind, entry.provider_id, entry.resource_id, entry.version): entry
        for entry in environment.extensions.entries
    }
    selected: list[AgentExtensionCatalogEntry] = []
    for kind, provider_id, resource_id, item_version, digest, binding_id in (
        _requested_extensions(manifest)
    ):
        entry = catalog.get((kind, provider_id, resource_id, item_version))
        if entry is None:
            diagnostics.append(
                _diagnostic(
                    AgentDiagnosticSeverity.ERROR,
                    "extension.missing",
                    "extensions",
                    f"扩展未解析：{binding_id}",
                )
            )
            continue
        if digest and digest != entry.content_sha256:
            diagnostics.append(
                _diagnostic(
                    AgentDiagnosticSeverity.ERROR,
                    "extension.digest_mismatch",
                    "extensions",
                    f"扩展内容摘要不匹配：{binding_id}",
                )
            )
            continue
        selected.append(entry)

    selected_bindings = {entry.binding_id for entry in selected}
    for entry in selected:
        missing = tuple(
            dependency
            for dependency in entry.dependencies
            if dependency not in selected_bindings
        )
        if missing:
            diagnostics.append(
                _diagnostic(
                    AgentDiagnosticSeverity.ERROR,
                    "extension.dependency_missing",
                    "extensions",
                    f"扩展 {entry.binding_id} 缺少依赖："
                    + ", ".join(missing),
                )
            )

    exports: dict[str, list[str]] = {}
    for entry in selected:
        for exported in entry.exports:
            exports.setdefault(exported.qualified_name, []).append(
                entry.binding_id
            )
    for exported_name, owners in sorted(exports.items()):
        if len(owners) < 2:
            continue
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "extension.name_conflict",
                "extensions",
                f"导出名称冲突 {exported_name}：" + ", ".join(sorted(owners)),
            )
        )
    _stage_passed(
        diagnostics,
        start,
        code="extensions.validated",
        path="extensions",
        message="Skill、MCP、工具、Hook 的版本、依赖和导出名称已固定",
    )
    return tuple(sorted(selected, key=lambda item: item.qualified_id))


def _check_workspace(
    manifest: AgentManifest,
    environment: AgentManifestCompilationEnvironment,
    diagnostics: list[AgentManifestDiagnostic],
) -> None:
    start = len(diagnostics)
    workspace = environment.workspace
    if workspace.owner != manifest.identity.owner or not workspace.acl_allowed:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "workspace.acl_denied",
                "state.workspace_profile_id",
                "Workspace owner 或 ACL 不允许当前 Agent",
            )
        )
    if workspace.workspace_profile_id != manifest.state.workspace_profile_id:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "workspace.profile_missing",
                "state.workspace_profile_id",
                "Workspace Profile 不存在或未绑定",
            )
        )
    policy_checks = (
        (
            manifest.state.memory_policy_id,
            workspace.memory_policy_ids,
            "state.memory_policy_id",
            "memory.policy_missing",
            "Memory Policy 不存在",
        ),
        (
            manifest.state.artifact_policy_id,
            workspace.artifact_policy_ids,
            "state.artifact_policy_id",
            "artifact.policy_missing",
            "Artifact Policy 不存在",
        ),
        (
            manifest.state.sandbox_profile_id,
            workspace.sandbox_profile_ids,
            "state.sandbox_profile_id",
            "sandbox.profile_missing",
            "Sandbox Profile 不存在",
        ),
    )
    for policy_id, available, path, code, message in policy_checks:
        if policy_id not in available:
            diagnostics.append(
                _diagnostic(
                    AgentDiagnosticSeverity.ERROR,
                    code,
                    path,
                    message,
                )
            )
    if (
        not workspace.quota_ready
        or workspace.quota_bytes <= 0
        or workspace.used_bytes >= workspace.quota_bytes
    ):
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "workspace.quota_unavailable",
                "state.workspace_profile_id",
                "Workspace 配额未应用或已耗尽",
            )
        )
    if not workspace.sandbox_ready:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "sandbox.not_ready",
                "state.sandbox_profile_id",
                "Sandbox Profile 尚未就绪",
            )
        )
    _stage_passed(
        diagnostics,
        start,
        code="workspace.validated",
        path="state",
        message="Workspace owner、ACL、配额和状态策略已固定",
    )


def _check_security(
    manifest: AgentManifest,
    environment: AgentManifestCompilationEnvironment,
    diagnostics: list[AgentManifestDiagnostic],
) -> None:
    start = len(diagnostics)
    security = environment.security
    if manifest.permissions.policy_id not in security.permission_policy_ids:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "permission.policy_missing",
                "permissions.policy_id",
                "Permission Policy 不存在",
            )
        )
    missing_actions = tuple(
        sorted(
            set(manifest.permissions.required_actions)
            - security.allowed_actions
        )
    )
    if missing_actions:
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "permission.action_denied",
                "permissions.required_actions",
                "Permission Policy 未授权动作：" + ", ".join(missing_actions),
            )
        )
    available_secrets = {
        (item.provider_id, item.secret_id)
        for item in security.secret_refs
    }
    for secret_ref in manifest.permissions.secret_refs:
        if (secret_ref.provider_id, secret_ref.secret_id) in available_secrets:
            continue
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "secret.reference_missing",
                "permissions.secret_refs",
                f"秘密引用 binding 未解析：{secret_ref.binding}",
            )
        )
    _stage_passed(
        diagnostics,
        start,
        code="security.validated",
        path="permissions",
        message="权限动作与秘密引用已按 ID 预检",
    )


def _check_budget(
    manifest: AgentManifest,
    environment: AgentManifestCompilationEnvironment,
    diagnostics: list[AgentManifestDiagnostic],
) -> None:
    start = len(diagnostics)
    for field_name in (
        "token_limit",
        "cost_limit_microunits",
        "step_limit",
        "time_limit_ms",
        "concurrency_limit",
    ):
        if getattr(manifest.budget, field_name) <= getattr(
            environment.budget_ceilings,
            field_name,
        ):
            continue
        diagnostics.append(
            _diagnostic(
                AgentDiagnosticSeverity.ERROR,
                "budget.ceiling_exceeded",
                f"budget.{field_name}",
                f"预算 {field_name} 超过部署环境上限",
            )
        )
    _stage_passed(
        diagnostics,
        start,
        code="budget.validated",
        path="budget",
        message="Token、费用、步骤、时长和并发预算已固定",
    )


def compile_agent_manifest(
    manifest: AgentManifest,
    environment: AgentManifestCompilationEnvironment,
) -> CompiledAgentSnapshot:
    """执行完整预检；任何 error 都阻止产生运行快照。"""

    if not isinstance(manifest, AgentManifest):
        raise TypeError("manifest 必须是 AgentManifest")
    if not isinstance(environment, AgentManifestCompilationEnvironment):
        raise TypeError("environment 必须是 AgentManifestCompilationEnvironment")

    diagnostics: list[AgentManifestDiagnostic] = []
    _check_runtime(manifest, environment, diagnostics)
    _check_model(manifest, environment, diagnostics)
    prompt, output = _check_pinned_resources(
        manifest,
        environment,
        diagnostics,
    )
    extensions = _check_extensions(manifest, environment, diagnostics)
    _check_workspace(manifest, environment, diagnostics)
    _check_security(manifest, environment, diagnostics)
    _check_budget(manifest, environment, diagnostics)

    errors = tuple(
        item
        for item in diagnostics
        if item.severity is AgentDiagnosticSeverity.ERROR
    )
    if errors:
        raise AgentManifestCompilationError(tuple(diagnostics))
    if prompt is None or output is None:
        raise RuntimeError("Manifest 预检状态不一致")
    diagnostics.append(
        _diagnostic(
            AgentDiagnosticSeverity.INFO,
            "manifest.compiled",
            "manifest",
            "Manifest 已通过 fail-closed 预检并编译为不可变快照",
        )
    )
    return CompiledAgentSnapshot(
        manifest=manifest,
        runtime=environment.runtime,
        model_route=environment.model_route,
        prompt_resource=prompt,
        output_resource=output,
        extensions=extensions,
        environment_revision=environment.revision,
        environment_sha256=environment.snapshot_sha256,
        workspace_snapshot_sha256=environment.workspace.snapshot_sha256,
        security_snapshot_sha256=environment.security.snapshot_sha256,
        budget_ceilings=environment.budget_ceilings,
        diagnostics=tuple(diagnostics),
    )


__all__ = [
    "AgentManifestCompilationError",
    "CompiledAgentSnapshot",
    "compile_agent_manifest",
]
