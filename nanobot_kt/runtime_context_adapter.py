"""受信请求字段到框架无关 Runtime context 的薄适配。"""

from __future__ import annotations

from typing import Any

from core.agent_runtime import (
    RequestRuntimeContext,
    RuntimeAccessEnvelope,
    RuntimeAccessGrant,
    RuntimeAccessKind,
    RuntimeActor,
    RuntimeActorType,
    RuntimeChatType,
    RuntimeFeature,
    RuntimeGovernanceEnvelope,
    RuntimeOwnerType,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
)


_FILE_TOOL_OPERATIONS = {
    "workspace_read": ("read",),
    "workspace_search": ("search",),
    "workspace_write": ("write",),
    "workspace_edit": ("edit",),
    "sandbox_exec": ("execute",),
    "sandbox_poll": ("read_process",),
    "sandbox_write_stdin": ("write_process",),
    "sandbox_terminate": ("terminate_process",),
}
_ASSET_TOOL_OPERATIONS = {
    "asset_import": ("import",),
    "asset_publish": ("publish",),
}
_MEMORY_TOOL_SCOPES = {
    "memory_query": ("memory:session", "read"),
    "knowledge_query": ("knowledge:authorized", "read"),
    "sql_analysis": ("memory:archive", "read"),
    "persona_update": ("memory:persona", "write"),
}
_CONTROLLED_NETWORK_TOOLS = {
    "ai_daily",
    "group_analysis",
    "image_generation",
    "image_summary",
    "sticker_search",
    "web_search",
}


def _merge_access_grant(
    grants: dict[tuple[RuntimeAccessKind, str], tuple[set[str], str]],
    *,
    kind: RuntimeAccessKind,
    resource: str,
    operations: tuple[str, ...],
    authorization: str,
) -> None:
    key = (kind, resource)
    existing = grants.get(key)
    if existing is None:
        grants[key] = (set(operations), authorization)
        return
    existing[0].update(operations)
    if existing[1] != authorization:
        raise ValueError(f"资源 {resource} 存在冲突的授权来源")


def build_request_runtime_governance(
    *,
    tool_plan: Any,
    skill_plan: RuntimePlanRef | None,
) -> RuntimeGovernanceEnvelope:
    """从本轮已冻结计划生成精确访问范围，不读取模型参数。"""

    from core.tool_registration import get_tool_registration
    from core.tool_registry import SANDBOX_TOOL_NAMES

    grants: dict[
        tuple[RuntimeAccessKind, str],
        tuple[set[str], str],
    ] = {}
    tool_names = tuple(
        sorted(
            str(name)
            for name in getattr(tool_plan, "executable_tool_names", ())
        )
    )
    try:
        from core.mcp import get_current_mcp_runtime

        mcp_runtime = get_current_mcp_runtime()
    except Exception:
        mcp_runtime = None
    mcp_tools = {
        descriptor.wire_name: descriptor
        for descriptor in (
            mcp_runtime.snapshot.tools if mcp_runtime is not None else ()
        )
    }
    for tool_name in tool_names:
        if tool_name in mcp_tools:
            authorization = "mcp_snapshot"
        elif tool_name in SANDBOX_TOOL_NAMES:
            authorization = "sandbox_session_grant"
        else:
            authorization = "tool_plan"
        _merge_access_grant(
            grants,
            kind=RuntimeAccessKind.TOOL,
            resource=f"tool:{tool_name}",
            operations=("execute",),
            authorization=authorization,
        )
        file_operations = _FILE_TOOL_OPERATIONS.get(tool_name)
        if file_operations:
            _merge_access_grant(
                grants,
                kind=RuntimeAccessKind.FILE,
                resource="workspace:current",
                operations=file_operations,
                authorization="sandbox_session_grant",
            )
        asset_operations = _ASSET_TOOL_OPERATIONS.get(tool_name)
        if asset_operations:
            _merge_access_grant(
                grants,
                kind=RuntimeAccessKind.FILE,
                resource="assets:authorized",
                operations=asset_operations,
                authorization="sandbox_session_grant",
            )
        memory_scope = _MEMORY_TOOL_SCOPES.get(tool_name)
        if memory_scope:
            _merge_access_grant(
                grants,
                kind=RuntimeAccessKind.MEMORY,
                resource=memory_scope[0],
                operations=(memory_scope[1],),
                authorization="memory_policy",
            )
        registration = get_tool_registration(tool_name)
        if registration is not None and tool_name in _CONTROLLED_NETWORK_TOOLS:
            _merge_access_grant(
                grants,
                kind=RuntimeAccessKind.NETWORK,
                resource=f"controlled-provider:{tool_name}",
                operations=("request",),
                authorization="service_policy",
            )
    if skill_plan is not None:
        _merge_access_grant(
            grants,
            kind=RuntimeAccessKind.SKILL,
            resource=skill_plan.identity,
            operations=("load",),
            authorization="skill_lock",
        )
    enabled_mcp_descriptors = tuple(
        mcp_tools[tool_name]
        for tool_name in tool_names
        if tool_name in mcp_tools
    )
    for descriptor in enabled_mcp_descriptors:
        _merge_access_grant(
            grants,
            kind=RuntimeAccessKind.MCP,
            resource=f"mcp-server:{descriptor.server_id}",
            operations=("call",),
            authorization="mcp_snapshot",
        )
    return RuntimeGovernanceEnvelope(
        policy_id="runtime-governance-v1",
        access=RuntimeAccessEnvelope(tuple(
            RuntimeAccessGrant(
                kind=kind,
                resource=resource,
                operations=tuple(sorted(operations)),
                authorization=authorization,
            )
            for (kind, resource), (operations, authorization) in grants.items()
        )),
    )


def build_request_runtime_context(
    *,
    request_id: str,
    platform: str,
    user_id: str,
    group_id: str,
    session_id: str,
    is_group: bool,
    is_super_user: bool,
    trace_id: str,
    run_id: str,
    turn_id: str,
    correlation_id: str,
    message_id: str,
    capabilities: dict[str, bool],
    prompt_key: str,
    prompt_sha256: str,
    tool_plan: Any,
    recovery_plans: tuple[RuntimePlanRef, ...] = (),
    session_goal_plan: RuntimePlanRef | None = None,
    skill_plan: RuntimePlanRef | None = None,
) -> RequestRuntimeContext:
    owner_id = group_id if is_group else user_id
    if not owner_id:
        owner_id = session_id
    plans: list[RuntimePlanRef] = []
    prompt_digest = str(prompt_sha256 or "").strip().lower()
    if len(prompt_digest) == 64:
        plans.append(
            RuntimePlanRef(
                RuntimePlanKind.PROMPT,
                f"prompt:{prompt_key}",
                prompt_digest,
            )
        )
    tool_digest = str(getattr(tool_plan, "sha256", "") or "").strip().lower()
    if len(tool_digest) == 64:
        plans.append(
            RuntimePlanRef(
                RuntimePlanKind.TOOL,
                "tool-plan:current",
                tool_digest,
            )
        )
    for reference in recovery_plans:
        if not isinstance(reference, RuntimePlanRef):
            raise TypeError("recovery_plans 必须只包含 RuntimePlanRef")
        if reference.kind in {item.kind for item in plans}:
            raise ValueError(f"重复 RuntimePlan：{reference.kind.value}")
        plans.append(reference)
    if session_goal_plan is not None:
        if not isinstance(session_goal_plan, RuntimePlanRef):
            raise TypeError("session_goal_plan 必须是 RuntimePlanRef")
        if session_goal_plan.kind in {item.kind for item in plans}:
            raise ValueError(
                f"重复 RuntimePlan：{session_goal_plan.kind.value}"
            )
        plans.append(session_goal_plan)
    if skill_plan is not None:
        if not isinstance(skill_plan, RuntimePlanRef):
            raise TypeError("skill_plan 必须是 RuntimePlanRef")
        if skill_plan.kind in {item.kind for item in plans}:
            raise ValueError(f"重复 RuntimePlan：{skill_plan.kind.value}")
        plans.append(skill_plan)
    return RequestRuntimeContext(
        request_id=request_id or run_id,
        principal=RuntimePrincipal(
            platform=platform,
            owner_type=(RuntimeOwnerType.GROUP if is_group else RuntimeOwnerType.USER),
            owner_id=owner_id,
        ),
        session_id=session_id,
        chat_type=(RuntimeChatType.GROUP if is_group else RuntimeChatType.PRIVATE),
        trace_id=trace_id,
        run_id=run_id,
        turn_id=turn_id,
        correlation_id=correlation_id,
        actor=RuntimeActor(RuntimeActorType.USER, user_id or owner_id),
        message_id=message_id,
        capabilities=frozenset(
            name for name, enabled in capabilities.items() if enabled
        ),
        features=(
            RuntimeFeature("super_user", is_super_user, "request"),
            RuntimeFeature(
                "stream",
                capabilities.get("supports_stream", False),
                "request",
            ),
        ),
        plans=tuple(plans),
        governance=build_request_runtime_governance(
            tool_plan=tool_plan,
            skill_plan=skill_plan,
        ),
    )


def build_fallback_request_runtime_context(
    *,
    session_id: str,
    trace_id: str,
    run_id: str,
) -> RequestRuntimeContext:
    """仅供旧单元夹具使用；生产请求必须使用完整受信上下文。"""

    return RequestRuntimeContext(
        request_id=run_id or trace_id or session_id,
        principal=RuntimePrincipal(
            platform="qq",
            owner_type=RuntimeOwnerType.USER,
            owner_id=session_id,
        ),
        session_id=session_id,
        chat_type=RuntimeChatType.PRIVATE,
        trace_id=trace_id,
        run_id=run_id,
    )


__all__ = [
    "build_fallback_request_runtime_context",
    "build_request_runtime_context",
    "build_request_runtime_governance",
]
