"""阶段 2.1：声明式 Agent Manifest 合同回归。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json

import pytest


def _extension(kind, resource_id: str):
    from core.agent_manifest import AgentExtensionRef

    return AgentExtensionRef(
        kind=kind,
        provider_id="builtin",
        resource_id=resource_id,
        version="1.0.0",
        content_sha256="a" * 64,
    )


def _manifest(*, reverse: bool = False):
    from core.agent_manifest import (
        AgentBudget,
        AgentEvaluationPolicy,
        AgentExtensionKind,
        AgentExtensionSet,
        AgentHookEvent,
        AgentHookRef,
        AgentIdentity,
        AgentManifest,
        AgentModelSpec,
        AgentOutputContractRef,
        AgentPermissionSpec,
        AgentPromptBundleRef,
        AgentRolloutPolicy,
        AgentRuntimeSpec,
        AgentSecretRef,
        AgentSourceKind,
        AgentSourceRef,
        AgentStatePolicy,
    )
    from core.agent_runtime import (
        RuntimeCapability,
        RuntimeOwnerType,
        RuntimePrincipal,
    )

    skills = (
        _extension(AgentExtensionKind.SKILL, "research"),
        _extension(AgentExtensionKind.SKILL, "reply"),
    )
    tools = (
        _extension(AgentExtensionKind.TOOL, "web-search"),
        _extension(AgentExtensionKind.TOOL, "reply"),
    )
    hooks = (
        AgentHookRef(
            provider_id="builtin",
            hook_id="audit",
            event=AgentHookEvent.ON_COMPLETE,
            version="1.0.0",
            order=20,
        ),
        AgentHookRef(
            provider_id="builtin",
            hook_id="permission",
            event=AgentHookEvent.BEFORE_TOOL,
            version="1.0.0",
            order=-10,
        ),
    )
    secrets = (
        AgentSecretRef(
            binding="model.primary",
            provider_id="settings",
            secret_id="prod-model-credential",
        ),
        AgentSecretRef(
            binding="mcp.github",
            provider_id="vault",
            secret_id="github-app-installation",
        ),
    )
    if reverse:
        skills = tuple(reversed(skills))
        tools = tuple(reversed(tools))
        hooks = tuple(reversed(hooks))
        secrets = tuple(reversed(secrets))

    return AgentManifest(
        schema_version=1,
        version="1.2.0",
        identity=AgentIdentity(
            agent_id="nanobot.research",
            display_name="Nanobot 调研助手",
            description="在受控工具与预算内完成中文资料调研。",
            owner=RuntimePrincipal(
                platform="nanobot",
                owner_type=RuntimeOwnerType.PROJECT,
                owner_id="main",
            ),
        ),
        source=AgentSourceRef(
            kind=AgentSourceKind.PROJECT,
            source_id="nanobot/agent-manifests",
            revision="git:0123456789abcdef",
        ),
        runtime=AgentRuntimeSpec(
            runtime_id="native",
            required_capabilities=frozenset({
                RuntimeCapability.RUN,
                RuntimeCapability.RUN_EVENT,
                RuntimeCapability.INTERRUPT,
            }),
        ),
        model=AgentModelSpec(
            route_key="chat.research",
            required_capabilities=("tools", "stream"),
        ),
        prompt=AgentPromptBundleRef(
            bundle_id="chat.research",
            version="2.0.0",
            content_sha256="1" * 64,
        ),
        output=AgentOutputContractRef(
            contract_id="reply.rich",
            version="1.0.0",
            media_types=("text/html", "text/plain"),
            schema_sha256="2" * 64,
        ),
        extensions=AgentExtensionSet(
            skills=skills,
            mcp_servers=(
                _extension(AgentExtensionKind.MCP_SERVER, "github"),
            ),
            tools=tools,
            hooks=hooks,
        ),
        state=AgentStatePolicy(
            memory_policy_id="conversation.default",
            workspace_profile_id="workspace.research",
            artifact_policy_id="artifact.immutable",
            sandbox_profile_id="sandbox.no-network",
        ),
        permissions=AgentPermissionSpec(
            policy_id="permission.research",
            required_actions=("artifact.publish", "tool.web-search"),
            secret_refs=secrets,
        ),
        budget=AgentBudget(
            token_limit=100_000,
            cost_limit_microunits=5_000_000,
            step_limit=64,
            time_limit_ms=600_000,
            concurrency_limit=4,
        ),
        evaluation=AgentEvaluationPolicy(
            eval_set_ids=("research.grounded", "reply.contract"),
            required_gate_ids=("architecture", "behavior-baseline"),
        ),
        rollout=AgentRolloutPolicy(
            scope_ids=("project.main", "user.super"),
            percentage_basis_points=2500,
        ),
    )


def _compilation_environment(manifest=None, *, reverse: bool = False):
    from core.agent_manifest import (
        AgentBudgetCeilings,
        AgentExportedName,
        AgentExportNamespace,
        AgentExtensionCatalog,
        AgentExtensionCatalogEntry,
        AgentExtensionKind,
        AgentManifestCompilationEnvironment,
        AgentModelRouteSnapshot,
        AgentPinnedResourceKind,
        AgentPinnedResourceSnapshot,
        AgentSecurityPreflightSnapshot,
        AgentWorkspacePreflightSnapshot,
    )
    from core.agent_runtime import RuntimeCapabilities

    manifest = manifest or _manifest()

    def entry(
        kind,
        resource_id: str,
        *exports: tuple[AgentExportNamespace, str],
        dependencies=(),
    ):
        return AgentExtensionCatalogEntry(
            kind=kind,
            provider_id="builtin",
            resource_id=resource_id,
            version="1.0.0",
            content_sha256="a" * 64,
            exports=tuple(
                AgentExportedName(namespace=namespace, name=name)
                for namespace, name in exports
            ),
            dependencies=dependencies,
        )

    entries = (
        entry(
            AgentExtensionKind.SKILL,
            "research",
            (AgentExportNamespace.SKILL, "research"),
            dependencies=("tool:builtin:web-search",),
        ),
        entry(
            AgentExtensionKind.SKILL,
            "reply",
            (AgentExportNamespace.SKILL, "reply"),
            dependencies=("tool:builtin:reply",),
        ),
        entry(
            AgentExtensionKind.MCP_SERVER,
            "github",
            (AgentExportNamespace.MCP_SERVER, "github"),
            (AgentExportNamespace.TOOL, "github-search"),
        ),
        entry(
            AgentExtensionKind.TOOL,
            "web-search",
            (AgentExportNamespace.TOOL, "web-search"),
        ),
        entry(
            AgentExtensionKind.TOOL,
            "reply",
            (AgentExportNamespace.TOOL, "reply"),
        ),
        entry(
            AgentExtensionKind.HOOK,
            "audit",
            (AgentExportNamespace.HOOK, "audit"),
        ),
        entry(
            AgentExtensionKind.HOOK,
            "permission",
            (AgentExportNamespace.HOOK, "permission"),
        ),
    )
    resources = (
        AgentPinnedResourceSnapshot(
            kind=AgentPinnedResourceKind.PROMPT_BUNDLE,
            resource_id=manifest.prompt.bundle_id,
            version=manifest.prompt.version,
            content_sha256=manifest.prompt.content_sha256,
        ),
        AgentPinnedResourceSnapshot(
            kind=AgentPinnedResourceKind.OUTPUT_CONTRACT,
            resource_id=manifest.output.contract_id,
            version=manifest.output.version,
            content_sha256=manifest.output.schema_sha256,
        ),
    )
    if reverse:
        entries = tuple(reversed(entries))
        resources = tuple(reversed(resources))
    return AgentManifestCompilationEnvironment(
        revision="preflight:2026-08-03",
        runtime=RuntimeCapabilities(
            runtime_id=manifest.runtime.runtime_id,
            supported=manifest.runtime.required_capabilities,
        ),
        model_route=AgentModelRouteSnapshot(
            route_key=manifest.model.route_key,
            capabilities=frozenset(manifest.model.required_capabilities),
            revision="model-catalog:42",
        ),
        pinned_resources=resources,
        extensions=AgentExtensionCatalog(
            revision="extension-catalog:7",
            entries=entries,
        ),
        workspace=AgentWorkspacePreflightSnapshot(
            owner=manifest.identity.owner,
            workspace_profile_id=manifest.state.workspace_profile_id,
            memory_policy_ids=frozenset({manifest.state.memory_policy_id}),
            artifact_policy_ids=frozenset({manifest.state.artifact_policy_id}),
            sandbox_profile_ids=frozenset({manifest.state.sandbox_profile_id}),
            quota_bytes=2 * 1024 * 1024 * 1024,
            used_bytes=128 * 1024 * 1024,
            acl_allowed=True,
            quota_ready=True,
            sandbox_ready=True,
        ),
        security=AgentSecurityPreflightSnapshot(
            permission_policy_ids=frozenset({manifest.permissions.policy_id}),
            allowed_actions=frozenset(manifest.permissions.required_actions),
            secret_refs=manifest.permissions.secret_refs,
        ),
        budget_ceilings=AgentBudgetCeilings(
            token_limit=1_000_000,
            cost_limit_microunits=50_000_000,
            step_limit=1024,
            time_limit_ms=3_600_000,
            concurrency_limit=32,
        ),
    )


def test_manifest_covers_all_declared_agent_dimensions() -> None:
    manifest = _manifest()
    payload = manifest.to_dict()

    assert set(payload) == {
        "schema_version",
        "version",
        "identity",
        "source",
        "runtime",
        "model",
        "prompt",
        "output",
        "extensions",
        "state",
        "permissions",
        "budget",
        "evaluation",
        "rollout",
        "content_sha256",
    }
    assert payload["identity"]["owner"] == {
        "platform": "nanobot",
        "owner_type": "project",
        "owner_id": "main",
    }
    assert payload["runtime"]["required_capabilities"] == [
        "interrupt",
        "run",
        "run_event",
    ]
    assert len(payload["content_sha256"]) == 64


def test_manifest_is_frozen_and_hash_is_order_independent() -> None:
    first = _manifest()
    reordered = _manifest(reverse=True)

    assert first == reordered
    assert first.content_sha256 == reordered.content_sha256
    assert first.canonical_json() == reordered.canonical_json()
    with pytest.raises(FrozenInstanceError):
        first.version = "2.0.0"  # type: ignore[misc]


def test_manifest_rejects_declared_digest_mismatch() -> None:
    manifest = _manifest()

    with pytest.raises(ValueError, match="content_sha256 与声明内容不匹配"):
        replace(manifest, content_sha256="0" * 64)


def test_credentials_are_references_and_inline_secret_is_rejected() -> None:
    from core.agent_manifest import AgentSecretRef

    serialized = json.dumps(_manifest().to_dict(), ensure_ascii=False)

    assert "prod-model-credential" in serialized
    assert "api_key" not in serialized
    assert "secret_value" not in serialized
    with pytest.raises(ValueError, match="疑似凭据明文"):
        AgentSecretRef(
            binding="model.primary",
            provider_id="settings",
            secret_id="sk-" + "x" * 32,
        )


def test_manifest_rejects_duplicate_bindings_and_wrong_extension_kind() -> None:
    from core.agent_manifest import (
        AgentExtensionKind,
        AgentExtensionSet,
        AgentPermissionSpec,
        AgentSecretRef,
    )

    secret = AgentSecretRef(
        binding="model.primary",
        provider_id="settings",
        secret_id="prod-model-credential",
    )
    with pytest.raises(ValueError, match="binding 不能重复"):
        AgentPermissionSpec(
            policy_id="permission.default",
            secret_refs=(secret, secret),
        )
    with pytest.raises(ValueError, match="kind 不匹配"):
        AgentExtensionSet(
            skills=(_extension(AgentExtensionKind.TOOL, "reply"),),
        )


def test_manifest_requires_runnable_runtime_and_bounded_budgets() -> None:
    from core.agent_manifest import AgentBudget, AgentRolloutPolicy, AgentRuntimeSpec
    from core.agent_runtime import RuntimeCapability

    with pytest.raises(ValueError, match="必须包含 run"):
        AgentRuntimeSpec(
            runtime_id="native",
            required_capabilities=frozenset({RuntimeCapability.RUN_EVENT}),
        )
    with pytest.raises(ValueError, match="concurrency_limit"):
        AgentBudget(
            token_limit=1,
            cost_limit_microunits=1,
            step_limit=1,
            time_limit_ms=1,
            concurrency_limit=0,
        )
    with pytest.raises(ValueError, match="0 到 10000"):
        AgentRolloutPolicy(
            scope_ids=("global",),
            percentage_basis_points=10_001,
        )


def test_agent_manifest_contract_has_no_framework_dependencies() -> None:
    from pathlib import Path

    from scripts.check_architecture import BoundaryRule, check_rule

    rule = BoundaryRule(
        path=Path("core/agent_manifest"),
        forbidden_roots=frozenset({
            "api",
            "app",
            "clients",
            "creatures",
            "fastapi",
            "nanobot_kt",
            "sandboxd",
            "sqlalchemy",
        }),
        description="Agent Manifest 合同层不得依赖框架或 Adapter",
    )

    assert check_rule(rule) == []


def test_compiler_produces_immutable_auditable_runtime_snapshot() -> None:
    from core.agent_manifest import (
        AgentDiagnosticSeverity,
        compile_agent_manifest,
    )

    manifest = _manifest()
    environment = _compilation_environment(manifest)

    compiled = compile_agent_manifest(manifest, environment)

    assert compiled.manifest is manifest
    assert compiled.environment_sha256 == environment.snapshot_sha256
    assert len(compiled.extensions) == 7
    assert len(compiled.snapshot_sha256) == 64
    assert [item.code for item in compiled.diagnostics] == [
        "runtime.validated",
        "model.validated",
        "resources.validated",
        "extensions.validated",
        "workspace.validated",
        "security.validated",
        "budget.validated",
        "manifest.compiled",
    ]
    assert all(
        item.severity is AgentDiagnosticSeverity.INFO
        for item in compiled.diagnostics
    )
    with pytest.raises(FrozenInstanceError):
        compiled.environment_revision = "changed"  # type: ignore[misc]


def test_compilation_snapshot_is_independent_of_catalog_order() -> None:
    from core.agent_manifest import compile_agent_manifest

    manifest = _manifest()
    first = compile_agent_manifest(
        manifest,
        _compilation_environment(manifest),
    )
    reordered = compile_agent_manifest(
        manifest,
        _compilation_environment(manifest, reverse=True),
    )

    assert first.environment_sha256 == reordered.environment_sha256
    assert first.snapshot_sha256 == reordered.snapshot_sha256
    assert first.canonical_json() == reordered.canonical_json()


def test_compiler_fails_closed_on_runtime_and_model_capabilities() -> None:
    from core.agent_manifest import (
        AgentManifestCompilationError,
        AgentModelRouteSnapshot,
        compile_agent_manifest,
    )
    from core.agent_runtime import RuntimeCapabilities, RuntimeCapability

    manifest = _manifest()
    environment = _compilation_environment(manifest)
    environment = replace(
        environment,
        runtime=RuntimeCapabilities(
            runtime_id="kt",
            supported=frozenset({RuntimeCapability.RUN}),
        ),
        model_route=AgentModelRouteSnapshot(
            route_key=manifest.model.route_key,
            capabilities=frozenset(),
            revision="model-catalog:unavailable",
            available=False,
        ),
        snapshot_sha256="",
    )

    with pytest.raises(AgentManifestCompilationError) as caught:
        compile_agent_manifest(manifest, environment)

    codes = {item.code for item in caught.value.diagnostics}
    assert {
        "runtime.id_mismatch",
        "runtime.capability_missing",
        "model.route_unavailable",
        "model.capability_missing",
    }.issubset(codes)


def test_compiler_detects_missing_dependencies_and_export_conflicts() -> None:
    from core.agent_manifest import (
        AgentExportedName,
        AgentExportNamespace,
        AgentExtensionCatalog,
        AgentManifestCompilationError,
        AgentPinnedResourceKind,
        AgentPinnedResourceSnapshot,
        compile_agent_manifest,
    )

    manifest = _manifest()
    environment = _compilation_environment(manifest)
    entries = tuple(
        entry
        for entry in environment.extensions.entries
        if entry.binding_id != "tool:builtin:web-search"
    )
    github = next(
        entry
        for entry in entries
        if entry.binding_id == "mcp_server:builtin:github"
    )
    conflicting = replace(
        github,
        exports=github.exports
        + (
            AgentExportedName(
                namespace=AgentExportNamespace.TOOL,
                name="reply",
            ),
        ),
    )
    entries = tuple(
        conflicting if entry is github else entry
        for entry in entries
    )
    wrong_prompt = AgentPinnedResourceSnapshot(
        kind=AgentPinnedResourceKind.PROMPT_BUNDLE,
        resource_id=manifest.prompt.bundle_id,
        version=manifest.prompt.version,
        content_sha256="f" * 64,
    )
    resources = tuple(
        wrong_prompt
        if item.kind is AgentPinnedResourceKind.PROMPT_BUNDLE
        else item
        for item in environment.pinned_resources
    )
    environment = replace(
        environment,
        extensions=AgentExtensionCatalog(
            revision="extension-catalog:broken",
            entries=entries,
        ),
        pinned_resources=resources,
        snapshot_sha256="",
    )

    with pytest.raises(AgentManifestCompilationError) as caught:
        compile_agent_manifest(manifest, environment)

    codes = {item.code for item in caught.value.diagnostics}
    assert "prompt.digest_mismatch" in codes
    assert "extension.missing" in codes
    assert "extension.dependency_missing" in codes
    assert "extension.name_conflict" in codes


def test_compiler_checks_workspace_security_and_budget_without_secret_echo() -> None:
    from core.agent_manifest import (
        AgentBudgetCeilings,
        AgentManifestCompilationError,
        AgentSecurityPreflightSnapshot,
        compile_agent_manifest,
    )

    manifest = _manifest()
    environment = _compilation_environment(manifest)
    workspace = replace(
        environment.workspace,
        memory_policy_ids=frozenset(),
        artifact_policy_ids=frozenset(),
        sandbox_profile_ids=frozenset(),
        used_bytes=environment.workspace.quota_bytes,
        acl_allowed=False,
        quota_ready=False,
        sandbox_ready=False,
        snapshot_sha256="",
    )
    security = AgentSecurityPreflightSnapshot(
        permission_policy_ids=frozenset(),
        allowed_actions=frozenset(),
        secret_refs=(),
    )
    environment = replace(
        environment,
        workspace=workspace,
        security=security,
        budget_ceilings=AgentBudgetCeilings(
            token_limit=1,
            cost_limit_microunits=1,
            step_limit=1,
            time_limit_ms=1,
            concurrency_limit=1,
        ),
        snapshot_sha256="",
    )

    with pytest.raises(AgentManifestCompilationError) as caught:
        compile_agent_manifest(manifest, environment)

    codes = {item.code for item in caught.value.diagnostics}
    assert {
        "workspace.acl_denied",
        "memory.policy_missing",
        "artifact.policy_missing",
        "sandbox.profile_missing",
        "workspace.quota_unavailable",
        "sandbox.not_ready",
        "permission.policy_missing",
        "permission.action_denied",
        "secret.reference_missing",
        "budget.ceiling_exceeded",
    }.issubset(codes)
    error_text = str(caught.value)
    assert "prod-model-credential" not in error_text
    assert "github-app-installation" not in error_text
