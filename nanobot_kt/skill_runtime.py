"""把受管 Agent Skills 精确版本锁接入请求级 ToolPlan。"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
from types import MappingProxyType
from typing import Any, Mapping

from core.agent_runtime import RuntimeAttribute, RuntimePlanKind, RuntimePlanRef
from core.skills import (
    RuntimeSkillLock,
    SkillResolutionContext,
    SqlAlchemySkillProvider,
    render_skill_catalog,
    runtime_skill_targets,
    select_skills_for_query,
)
from core.token_utils import estimate_tokens
from core.tool_plan import (
    SKILL_LOCK_PENDING_REASON,
    ToolPlan,
    enable_registered_tool,
)
from core.tool_schema_preview import build_tool_schema
from nanobot_kt.session_goal_runtime import build_session_goal_bridge_binding


@dataclass(frozen=True, slots=True)
class SkillBridgeBinding:
    """一次请求冻结的 Skill 目录、精确锁和动态工具计划。"""

    tool_plan: ToolPlan
    project_context: str
    lock: RuntimeSkillLock | None
    targets_json: str = ""
    agent_id: str = ""
    project_id: str = ""
    run_meta_update: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def plan_ref(self) -> RuntimePlanRef | None:
        if self.lock is None:
            return None
        return RuntimePlanRef(
            RuntimePlanKind.SKILL,
            f"skill-lock:{self.lock.sha256[:16]}",
            self.lock.sha256,
        )

    @property
    def runtime_attributes(self) -> tuple[RuntimeAttribute, ...]:
        if self.lock is None:
            return ()
        return (
            RuntimeAttribute("skill_lock_json", self.lock.to_runtime_json()),
            RuntimeAttribute("skill_lock_sha256", self.lock.sha256),
            RuntimeAttribute("skill_scope_targets_json", self.targets_json),
            RuntimeAttribute("skill_agent_id", self.agent_id),
            RuntimeAttribute("skill_project_id", self.project_id),
        )


@dataclass(frozen=True, slots=True)
class RequestExtensionBridgeBinding:
    """一次组合 Session Goal 与 Skill 后的请求级扩展投影。"""

    tool_plan: ToolPlan
    project_context: str
    run_meta_update: Mapping[str, object]
    session_goal_plan: RuntimePlanRef | None
    skill_plan: RuntimePlanRef | None
    runtime_attributes: tuple[RuntimeAttribute, ...]
    persistence_pending: bool = False


def _skill_schema(names: tuple[str, ...], *, db: Any) -> dict[str, Any]:
    schema = copy.deepcopy(
        build_tool_schema(
            "skill",
            db=db,
            include_template_overlay=False,
        )
    )
    function = schema.get("function")
    if not isinstance(function, dict):  # pragma: no cover - 注册表启动不变量
        raise RuntimeError("skill canonical schema 缺少 function")
    parameters = function.get("parameters")
    if not isinstance(parameters, dict):  # pragma: no cover - 注册表启动不变量
        raise RuntimeError("skill canonical schema 缺少 parameters")
    properties = parameters.get("properties")
    if not isinstance(properties, dict):  # pragma: no cover - 注册表启动不变量
        raise RuntimeError("skill canonical schema 缺少 properties")
    name_schema = properties.get("name")
    if not isinstance(name_schema, dict):  # pragma: no cover - 注册表启动不变量
        raise RuntimeError("skill canonical schema 缺少 name")
    name_schema["enum"] = list(names)
    return schema


def _join_project_context(current: str, catalog: str) -> str:
    parts = [str(current or "").strip(), str(catalog or "").strip()]
    return "\n\n".join(part for part in parts if part)


def build_skill_bridge_binding(
    *,
    db: Any,
    tool_plan: ToolPlan,
    project_context: str,
    platform: str,
    runtime_chat_type: str,
    is_group: bool,
    owner_id: str,
    agent_id: str,
    session_id: str,
    query: str = "",
    session_goal_mode: str = "",
    project_id: str = "",
) -> SkillBridgeBinding:
    """解析可见 Skill；Plan Mode 与空目录均保持原 ToolPlan 不变。"""

    if str(session_goal_mode or "").strip().lower() == "plan":
        return SkillBridgeBinding(
            tool_plan=tool_plan,
            project_context=project_context,
            lock=None,
            run_meta_update=MappingProxyType(
                {"skill_count": 0, "skill_disabled_reason": "plan_mode"}
            ),
        )
    disabled_reason = str(tool_plan.disabled.get("skill") or "")
    if disabled_reason and disabled_reason != SKILL_LOCK_PENDING_REASON:
        return SkillBridgeBinding(
            tool_plan=tool_plan,
            project_context=project_context,
            lock=None,
            run_meta_update=MappingProxyType(
                {"skill_count": 0, "skill_disabled_reason": "tool_plan"}
            ),
        )
    targets = runtime_skill_targets(
        platform=platform,
        is_group=is_group,
        owner_id=owner_id,
        agent_id=agent_id,
        project_id=project_id,
    )
    visible_lock = SqlAlchemySkillProvider(db).resolve_lock(
        SkillResolutionContext(
            targets=targets,
            executable_tool_names=tool_plan.executable_tool_names,
        )
    )
    if not visible_lock.entries:
        return SkillBridgeBinding(
            tool_plan=tool_plan,
            project_context=project_context,
            lock=None,
            run_meta_update=MappingProxyType(
                {
                    "skill_count": 0,
                    "skill_candidate_count": 0,
                    "skill_diagnostic_count": len(visible_lock.diagnostics),
                }
            ),
        )
    selection = select_skills_for_query(
        db,
        lock=visible_lock,
        query=query,
        runtime_chat_type=runtime_chat_type,
    )
    lock = selection.selected_lock
    if not lock.entries:
        return SkillBridgeBinding(
            tool_plan=tool_plan,
            project_context=project_context,
            lock=None,
            run_meta_update=MappingProxyType(
                {
                    "skill_count": 0,
                    "skill_candidate_count": len(visible_lock.entries),
                    "skill_registry_sha256": selection.registry.sha256,
                    "skill_selection_mode": selection.retrieval_mode,
                    "skill_indexed_count": selection.indexed_count,
                    "skill_diagnostic_count": len(visible_lock.diagnostics),
                }
            ),
        )
    names = tuple(entry.name for entry in lock.entries)
    schema = _skill_schema(names, db=db)
    catalog = render_skill_catalog(lock)
    skill_plan = enable_registered_tool(
        tool_plan,
        schema,
        chat_type=runtime_chat_type,
        platform=platform,
        session_id=session_id,
        db=db,
    )
    targets_json = json.dumps(
        [
            {"scope": target.scope.value, "scope_key": target.scope_key}
            for target in targets
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return SkillBridgeBinding(
        tool_plan=skill_plan,
        project_context=_join_project_context(
            project_context,
            catalog,
        ),
        lock=lock,
        targets_json=targets_json,
        agent_id=str(agent_id or "").strip(),
        project_id=str(project_id or "").strip(),
        run_meta_update=MappingProxyType(
            {
                "skill_count": len(lock.entries),
                "skill_candidate_count": len(visible_lock.entries),
                "skill_lock_sha256": lock.sha256,
                "skill_registry_sha256": selection.registry.sha256,
                "skill_selection_mode": selection.retrieval_mode,
                "skill_indexed_count": selection.indexed_count,
                "skill_catalog_prompt_tokens": estimate_tokens(catalog),
                "skill_body_prompt_tokens_if_loaded": sum(
                    entry.body_prompt_tokens for entry in lock.entries
                ),
                "skill_schema_prompt_tokens": estimate_tokens(
                    json.dumps(
                        schema,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                ),
                "skill_diagnostic_count": len(lock.diagnostics),
            }
        ),
    )


async def build_request_extension_binding(
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
    agent_id: str,
    query: str = "",
    agent: Any = None,
    cleanup_registrar: Any = None,
) -> RequestExtensionBridgeBinding:
    """按固定顺序组合目标策略与受管 Skill，不暴露平行授权路径。"""

    goal = build_session_goal_bridge_binding(
        db=db,
        metadata=metadata,
        platform=platform,
        runtime_chat_type=runtime_chat_type,
        is_group=is_group,
        group_id=group_id,
        user_id=user_id,
        session_id=session_id,
        runtime_preset=runtime_preset,
        disabled_tool_names=metadata.get("disabled_tool_names"),
    )
    skill = build_skill_bridge_binding(
        db=db,
        tool_plan=goal.tool_plan,
        project_context=goal.project_context,
        platform=platform,
        runtime_chat_type=runtime_chat_type,
        is_group=is_group,
        owner_id=group_id if is_group else user_id,
        agent_id=agent_id,
        session_id=session_id,
        query=query,
        session_goal_mode=(
            goal.policy.mode.value if goal.policy is not None else ""
        ),
    )
    from nanobot_kt.mcp_runtime import build_mcp_bridge_binding

    mcp = await build_mcp_bridge_binding(
        db=db,
        tool_plan=skill.tool_plan,
        runtime_chat_type=runtime_chat_type,
        platform=platform,
        session_id=session_id,
        session_goal_mode=(
            goal.policy.mode.value if goal.policy is not None else ""
        ),
        agent=agent,
        cleanup_registrar=cleanup_registrar,
    )
    return RequestExtensionBridgeBinding(
        tool_plan=mcp.tool_plan,
        project_context=skill.project_context,
        run_meta_update=MappingProxyType(
            {
                **goal.run_meta_update,
                **skill.run_meta_update,
                **mcp.run_meta_update,
            }
        ),
        session_goal_plan=goal.plan_ref,
        skill_plan=skill.plan_ref,
        runtime_attributes=(
            *goal.runtime_attributes,
            *skill.runtime_attributes,
        ),
        persistence_pending=mcp.persistence_pending,
    )


__all__ = [
    "RequestExtensionBridgeBinding",
    "SkillBridgeBinding",
    "build_request_extension_binding",
    "build_skill_bridge_binding",
]
