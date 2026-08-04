"""受信请求字段到框架无关 Runtime context 的薄适配。"""

from __future__ import annotations

from typing import Any

from core.agent_runtime import (
    RequestRuntimeContext,
    RuntimeActor,
    RuntimeActorType,
    RuntimeChatType,
    RuntimeFeature,
    RuntimeOwnerType,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
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
]
