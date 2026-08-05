"""只执行已由持久控制面冻结的多 Agent 计划。"""

from __future__ import annotations

from core.agent_orchestration.contracts import (
    AgentOrchestrationRequest,
    AgentOrchestrationResult,
)
from core.agent_orchestration.plan_governance import (
    AgentPlanGovernanceService,
)
from core.agent_orchestration.scheduler import (
    AgentDagOrchestrator,
    AgentOrchestrationCancellation,
)
from core.lifecycle.feature_registry import FeatureEnablementDecision


class GovernedAgentDagOrchestrator:
    """先核验持久批准／冻结事实，再进入底层确定性调度器。"""

    def __init__(
        self,
        *,
        orchestrator: AgentDagOrchestrator,
        governance: AgentPlanGovernanceService,
    ) -> None:
        if not isinstance(orchestrator, AgentDagOrchestrator):
            raise TypeError("orchestrator 必须是 AgentDagOrchestrator")
        if not isinstance(governance, AgentPlanGovernanceService):
            raise TypeError("governance 必须是 AgentPlanGovernanceService")
        self._orchestrator = orchestrator
        self._governance = governance

    async def execute(
        self,
        request: AgentOrchestrationRequest,
        *,
        feature_decision: FeatureEnablementDecision,
        cancellation: AgentOrchestrationCancellation | None = None,
    ) -> AgentOrchestrationResult:
        self._governance.require_frozen(request)
        return await self._orchestrator.execute(
            request,
            feature_decision=feature_decision,
            cancellation=cancellation,
        )


__all__ = ["GovernedAgentDagOrchestrator"]
