"""从生产事实源构建 Checkpoint 的 Manifest／Workspace／安全版本证明。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from sqlalchemy.orm import Session

from core.agent_manifest import (
    AgentBudget,
    AgentEvaluationPolicy,
    AgentExtensionKind,
    AgentExtensionRef,
    AgentExtensionSet,
    AgentIdentity,
    AgentManifest,
    AgentModelSpec,
    AgentOutputContractRef,
    AgentPermissionSpec,
    AgentPromptBundleRef,
    AgentRolloutPolicy,
    AgentRuntimeSpec,
    AgentSourceKind,
    AgentSourceRef,
    AgentStatePolicy,
)
from core.agent_runtime import (
    RuntimeCapability,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
)
from core.chat_stream_identity import resolve_chat_stream_identity
from core.db.models import (
    SandboxAccessGrant,
    Workspace,
)
from core.run_recovery.contracts import canonical_sha256
from core.sandbox.workspace_acl import workspace_matches_sandbox_grant
from core.tool_registration import (
    TOOL_REGISTRATION_REGISTRY,
    get_tool_registration,
)


def _tool_names(tool_plan: object) -> tuple[str, ...]:
    values = getattr(tool_plan, "executable_tool_names", ())
    if not isinstance(values, (set, frozenset, tuple, list)):
        return ()
    return tuple(sorted(str(item) for item in values if str(item).strip()))


def _tool_extension(name: str) -> AgentExtensionRef:
    registration = get_tool_registration(name)
    if registration is None or registration.lifecycle != "active":
        raise ValueError(f"ToolPlan 引用了未激活工具：{name}")
    return AgentExtensionRef(
        kind=AgentExtensionKind.TOOL,
        provider_id="nanobot",
        resource_id=name,
        version="1.0.0",
        content_sha256=canonical_sha256(dict(registration.registry_payload())),
    )


def _build_manifest(
    *,
    agent_id: str,
    principal: RuntimePrincipal,
    runtime_id: str,
    prompt_key: str,
    prompt_sha256: str,
    tool_names: tuple[str, ...],
) -> AgentManifest:
    return AgentManifest(
        schema_version=1,
        version="1.0.0",
        identity=AgentIdentity(
            agent_id=agent_id,
            display_name="Nanobot 对话 Agent",
            description="使用 canonical Prompt Runtime 与冻结工具计划处理消息。",
            owner=principal,
        ),
        source=AgentSourceRef(
            kind=AgentSourceKind.BUILTIN,
            source_id="nanobot/runtime-chat",
            revision="runtime-chat.v1",
        ),
        runtime=AgentRuntimeSpec(
            runtime_id=runtime_id,
            required_capabilities=frozenset({
                RuntimeCapability.RUN,
                RuntimeCapability.RUN_EVENT,
                RuntimeCapability.CONVERSATION,
                RuntimeCapability.MODEL_ROUTE,
                RuntimeCapability.CHECKPOINT_RECOVERY,
            }),
        ),
        model=AgentModelSpec(
            route_key="reply.current",
            required_capabilities=("tools",),
        ),
        prompt=AgentPromptBundleRef(
            bundle_id=prompt_key or "chat.default",
            version="2.0.0",
            content_sha256=prompt_sha256,
        ),
        output=AgentOutputContractRef(
            contract_id="reply.rich",
            version="1.0.0",
            media_types=("text/html", "text/plain"),
        ),
        extensions=AgentExtensionSet(
            tools=tuple(_tool_extension(name) for name in tool_names),
        ),
        state=AgentStatePolicy(
            memory_policy_id="conversation.default",
            workspace_profile_id="workspace.owner-default",
            artifact_policy_id="artifact.immutable",
            sandbox_profile_id="sandbox.default-deny",
        ),
        permissions=AgentPermissionSpec(
            policy_id="permission.tool-plan",
            required_actions=tuple(f"tool.{name}" for name in tool_names),
        ),
        budget=AgentBudget(
            token_limit=1_000_000,
            cost_limit_microunits=100_000_000,
            step_limit=8,
            time_limit_ms=120_000,
            concurrency_limit=1,
        ),
        evaluation=AgentEvaluationPolicy(
            required_gate_ids=("runtime.contract", "reply.contract"),
        ),
        rollout=AgentRolloutPolicy(
            scope_ids=("runtime.production",),
            percentage_basis_points=10_000,
        ),
    )


def _sandbox_grant(
    db: Session,
    *,
    principal: RuntimePrincipal,
    session_id: str,
    chat_type: str,
) -> SandboxAccessGrant | None:
    identity = resolve_chat_stream_identity(
        platform=principal.platform,
        chat_type=chat_type,
        session_id=session_id,
    )
    return (
        db.query(SandboxAccessGrant)
        .filter(
            SandboxAccessGrant.chat_stream_id == identity.chat_stream_id,
            SandboxAccessGrant.platform == identity.platform,
            SandboxAccessGrant.chat_type == identity.chat_type,
            SandboxAccessGrant.external_session_id
            == identity.external_session_id,
        )
        .one_or_none()
    )


def _scope_workspace(
    db: Session,
    *,
    principal: RuntimePrincipal,
    grant: SandboxAccessGrant | None,
) -> Workspace | None:
    if grant is not None and str(grant.workspace_id or ""):
        return db.get(Workspace, str(grant.workspace_id))
    return (
        db.query(Workspace)
        .filter(
            Workspace.platform == principal.platform,
            Workspace.owner_type == principal.owner_type.value,
            Workspace.owner_id == principal.owner_id,
            Workspace.name == "default",
        )
        .one_or_none()
    )


def _grant_document(
    grant: SandboxAccessGrant | None,
    workspace: Workspace | None,
) -> dict[str, object]:
    if grant is None:
        return {"present": False}
    return {
        "present": True,
        "grant_id_sha256": hashlib.sha256(
            str(grant.id).encode("utf-8")
        ).hexdigest(),
        "workspace_id": str(grant.workspace_id or ""),
        "capability_level": str(grant.capability_level),
        "execution_profile": str(grant.execution_profile),
        "status": str(grant.status),
        "version": int(grant.version),
        "workspace_acl_bound": workspace_matches_sandbox_grant(
            workspace,
            grant,
        ),
    }


def build_runtime_scope_plans(
    db: Session,
    *,
    agent_id: str,
    principal: RuntimePrincipal,
    session_id: str,
    chat_type: str,
    tool_plan: object,
    memory_registry_generation: int,
    memory_registry_sha256: str,
) -> tuple[RuntimePlanRef, ...]:
    """构建不依赖 KT／Native 的 Agent 数据和授权范围证明。"""

    if not isinstance(db, Session):
        raise TypeError("db 必须是 SQLAlchemy Session")
    if not isinstance(principal, RuntimePrincipal):
        raise TypeError("principal 必须是 RuntimePrincipal")
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        raise ValueError("agent_id 不能为空")
    tool_digest = str(getattr(tool_plan, "sha256", "") or "").lower()
    if len(tool_digest) != 64 or any(
        character not in "0123456789abcdef" for character in tool_digest
    ):
        raise ValueError("ToolPlan 缺少稳定摘要")
    memory_digest = str(memory_registry_sha256 or "").strip().lower()
    if len(memory_digest) != 64 or any(
        character not in "0123456789abcdef" for character in memory_digest
    ):
        raise ValueError("Memory Provider Registry 缺少稳定摘要")
    if (
        type(memory_registry_generation) is not int
        or memory_registry_generation <= 0
    ):
        raise ValueError("Memory Provider Registry generation 必须是正整数")

    chat_identity = resolve_chat_stream_identity(
        platform=principal.platform,
        chat_type=chat_type,
        session_id=session_id,
    )
    grant = _sandbox_grant(
        db,
        principal=principal,
        session_id=chat_identity.chat_stream_id,
        chat_type=chat_identity.chat_type,
    )
    workspace = _scope_workspace(db, principal=principal, grant=grant)
    workspace_id = str(workspace.id) if workspace is not None else ""
    tools = _tool_names(tool_plan)
    workspace_document = (
        {
            "schema_version": 1,
            "present": True,
            "workspace_id": workspace_id,
            "platform": str(workspace.platform),
            "owner_type": str(workspace.owner_type),
            "owner_id_sha256": hashlib.sha256(
                str(workspace.owner_id).encode("utf-8")
            ).hexdigest(),
            "name": str(workspace.name),
            "status": str(workspace.status),
            "quota_bytes": int(workspace.quota_bytes),
            "sandbox_acl_bound": (
                workspace_matches_sandbox_grant(workspace, grant)
                if grant is not None
                else None
            ),
        }
        if workspace is not None
        else {"schema_version": 1, "present": False}
    )
    session_digest = hashlib.sha256(
        chat_identity.chat_stream_id.encode("utf-8")
    ).hexdigest()
    owner_digest = hashlib.sha256(
        principal.canonical_id.encode("utf-8")
    ).hexdigest()
    memory_document = {
        "schema_version": 1,
        "agent_id": normalized_agent_id,
        "policy_id": "conversation.default",
        "owner_sha256": owner_digest,
        "chat_stream_sha256": session_digest,
        "provider_registry_generation": memory_registry_generation,
        "provider_registry_sha256": memory_digest,
    }
    # Artifact Plan 固定发布合同和 owner workspace 边界，而不是把工作区内
    # 所有历史资产都纳入摘要。具体被本 Run 引用或生成的资产由 Checkpoint
    # 的 artifact_proofs 单独固定，否则新增一个无关资产也会永久阻断恢复。
    artifact_document = {
        "schema_version": 1,
        "policy_id": "artifact.immutable",
        "reference_contract": "asset://sha256/v1",
        "workspace_id": workspace_id or "none",
    }
    security_document = {
        "schema_version": 1,
        "agent_id": normalized_agent_id,
        "owner_sha256": owner_digest,
        "chat_stream_sha256": session_digest,
        "tool_plan_sha256": tool_digest,
        "tool_registry_sha256": (
            TOOL_REGISTRATION_REGISTRY.registry_snapshot.sha256
        ),
        "tools": [
            {
                "name": name,
                "effect_policy": str(
                    get_tool_registration(name).descriptor.effect_policy
                ),
            }
            for name in tools
        ],
        "sandbox_grant": _grant_document(grant, workspace),
    }
    return (
        RuntimePlanRef(
            RuntimePlanKind.MEMORY,
            f"memory-session:{session_digest[:16]}",
            canonical_sha256(memory_document),
        ),
        RuntimePlanRef(
            RuntimePlanKind.WORKSPACE,
            f"workspace:{workspace_id or 'none'}",
            canonical_sha256(workspace_document),
        ),
        RuntimePlanRef(
            RuntimePlanKind.ARTIFACT,
            f"artifact-set:{workspace_id or 'none'}",
            canonical_sha256(artifact_document),
        ),
        RuntimePlanRef(
            RuntimePlanKind.SECURITY,
            f"security-session:{session_digest[:16]}",
            canonical_sha256(security_document),
        ),
    )


def build_live_recovery_plans(
    db: Session,
    *,
    agent_id: str,
    principal: RuntimePrincipal,
    session_id: str,
    chat_type: str,
    runtime_id: str,
    prompt_key: str,
    prompt_sha256: str,
    tool_plan: object,
    memory_registry_generation: int,
    memory_registry_sha256: str,
) -> tuple[RuntimePlanRef, ...]:
    """构建 Native Checkpoint 证明，并复用 Runtime 无关作用域。"""

    normalized_agent_id = str(agent_id or "").strip()
    scope_plans = build_runtime_scope_plans(
        db,
        agent_id=normalized_agent_id,
        principal=principal,
        session_id=session_id,
        chat_type=chat_type,
        tool_plan=tool_plan,
        memory_registry_generation=memory_registry_generation,
        memory_registry_sha256=memory_registry_sha256,
    )
    tools = _tool_names(tool_plan)
    manifest = _build_manifest(
        agent_id=normalized_agent_id,
        principal=principal,
        runtime_id=runtime_id,
        prompt_key=prompt_key,
        prompt_sha256=prompt_sha256,
        tool_names=tools,
    )
    return (
        RuntimePlanRef(
            RuntimePlanKind.MANIFEST,
            f"agent-manifest:{normalized_agent_id}@1.0.0",
            manifest.content_sha256,
        ),
        *scope_plans,
    )


def replace_recovery_plan(
    plans: Sequence[RuntimePlanRef],
    reference: RuntimePlanRef,
) -> tuple[RuntimePlanRef, ...]:
    values = {item.kind: item for item in plans}
    values[reference.kind] = reference
    return tuple(values[kind] for kind in RuntimePlanKind if kind in values)


__all__ = [
    "build_live_recovery_plans",
    "build_runtime_scope_plans",
    "replace_recovery_plan",
]
