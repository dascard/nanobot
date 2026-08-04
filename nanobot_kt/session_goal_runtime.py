"""Bridge 使用的 Session Goal 解析与 ToolPlan 组合。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from core.agent_runtime import (
    RuntimeAttribute,
    RuntimePlanKind,
    RuntimePlanRef,
)
from core.session_goal import (
    SessionGoalMode,
    SessionGoalPrincipal,
    SessionGoalRuntimePolicy,
    SessionGoalService,
    join_session_goal_project_context,
)
from core.tool_plan import ToolPlan


@dataclass(frozen=True, slots=True)
class SessionGoalBridgeBinding:
    """一次请求冻结的目标、工具计划和无正文运行证明。"""

    policy: SessionGoalRuntimePolicy | None
    tool_plan: ToolPlan
    project_context: str
    run_meta_update: Mapping[str, object]

    @property
    def plan_ref(self) -> RuntimePlanRef | None:
        policy = self.policy
        if policy is None:
            return None
        return RuntimePlanRef(
            RuntimePlanKind.BUDGET,
            f"session-goal:{policy.goal_id}:v{policy.snapshot.version}",
            policy.snapshot.snapshot_sha256,
        )

    @property
    def runtime_attributes(self) -> tuple[RuntimeAttribute, ...]:
        policy = self.policy
        if policy is None:
            return ()
        plan = policy.plan
        return (
            RuntimeAttribute("session_goal_id", policy.goal_id),
            RuntimeAttribute("session_goal_status", policy.status.value),
            RuntimeAttribute("session_goal_mode", policy.mode.value),
            RuntimeAttribute(
                "session_goal_version",
                policy.snapshot.version,
            ),
            RuntimeAttribute(
                "session_plan_revision",
                plan.revision if plan is not None else 0,
            ),
            RuntimeAttribute(
                "session_plan_sha256",
                plan.content_sha256 if plan is not None else "",
            ),
        )


def build_session_goal_bridge_binding(
    *,
    db: Any,
    metadata: Mapping[str, Any],
    platform: str,
    runtime_chat_type: str,
    is_group: bool,
    group_id: str,
    user_id: str,
    session_id: str,
    runtime_preset: str,
    disabled_tool_names: object,
) -> SessionGoalBridgeBinding:
    """按服务端 owner 和状态冻结目标资料及最终 ToolPlan。"""

    # 保留 core.tool_plan 的动态替换点，避免在模块加载时缓存实现。
    from core import tool_plan as tool_plan_module

    goal_id = str(metadata.get("session_goal_id") or "").strip()
    policy: SessionGoalRuntimePolicy | None = None
    run_meta_update: dict[str, object] = {}
    project_context = str(metadata.get("project_context") or "")
    if goal_id:
        policy = SessionGoalService(db).runtime_policy(
            goal_id=goal_id,
            principal=SessionGoalPrincipal(
                platform,
                "group" if is_group else "user",
                group_id if is_group else user_id,
                session_id,
            ),
        )
        project_context = join_session_goal_project_context(
            project_context,
            policy,
        )
        run_meta_update = {
            "session_goal_id": policy.goal_id,
            "session_goal_status": policy.status.value,
            "session_goal_mode": policy.mode.value,
            "session_goal_version": policy.snapshot.version,
            "session_goal_sha256": policy.snapshot.snapshot_sha256,
        }

    raw_disabled_names = (
        disabled_tool_names
        if isinstance(disabled_tool_names, (list, tuple, set, frozenset))
        else ()
    )
    source_disabled = {
        str(name).strip(): "来源上下文禁用(防递归)"
        for name in raw_disabled_names
        if str(name or "").strip()
    }
    tool_plan = tool_plan_module.build_tool_plan(
        chat_type=runtime_chat_type,
        group_id=group_id,
        user_id=user_id,
        platform=platform,
        session_id=session_id,
        runtime_preset=runtime_preset,
        db=db,
        extra_disabled=source_disabled or None,
        session_goal_mode=policy.mode.value if policy is not None else "",
        session_plan_writable=(
            policy.plan_writable if policy is not None else False
        ),
    )
    if policy is not None and policy.mode is SessionGoalMode.PLAN:
        plan_mode_tools = {
            "reply",
            "no_reply",
            "session_plan_read",
        }
        if policy.plan_writable:
            plan_mode_tools.add("session_plan_write")
        tool_plan = tool_plan_module.restrict_tool_plan(
            tool_plan,
            plan_mode_tools,
            disabled_reason="服务端 Plan Mode 只允许计划资产读写和最终回复",
        )

    return SessionGoalBridgeBinding(
        policy=policy,
        tool_plan=tool_plan,
        project_context=project_context,
        run_meta_update=MappingProxyType(run_meta_update),
    )


__all__ = [
    "SessionGoalBridgeBinding",
    "build_session_goal_bridge_binding",
]
