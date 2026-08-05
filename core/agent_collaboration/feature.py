"""多 Agent 协作显式开关与生命周期门禁。"""

from __future__ import annotations

from core.agent_orchestration import MULTI_AGENT_FEATURE_ID
from core.lifecycle import (
    FEATURE_LIFECYCLE_REGISTRY,
    FeatureScope,
    evaluate_feature_enablement,
)
from core.settings_service import settings


def is_agent_collaboration_requested() -> bool:
    """只读取操作员开关；默认关闭且不改变单 Agent 路径。"""

    return settings.get_bool("agent.multi_agent.enabled", False)


def require_agent_collaboration_enabled(scope: FeatureScope) -> None:
    """在真实冻结计划和持久基础设施就绪的调用边界完成门禁。"""

    descriptor = FEATURE_LIFECYCLE_REGISTRY.require(MULTI_AGENT_FEATURE_ID)
    decision = evaluate_feature_enablement(
        MULTI_AGENT_FEATURE_ID,
        requested=is_agent_collaboration_requested(),
        scope=scope,
        satisfied_gates=frozenset(descriptor.enablement_gates),
    )
    if not decision.enabled:
        from core.agent_collaboration.contracts import AgentCollaborationError

        raise AgentCollaborationError(
            "agent_collaboration_disabled",
            "多 Agent 协作未启用或当前入口不受支持",
        )


__all__ = [
    "is_agent_collaboration_requested",
    "require_agent_collaboration_enabled",
]
