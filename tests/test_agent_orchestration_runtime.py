from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json

import pytest

from core.agent_orchestration import (
    MULTI_AGENT_FEATURE_ID,
    AgentDagOrchestrator,
    AgentModelClass,
    AgentOrchestrationApproval,
    AgentOrchestrationBudget,
    AgentOrchestrationError,
    AgentOrchestrationFreeze,
    AgentOrchestrationPlan,
    AgentOrchestrationRequest,
    AgentOrchestrationState,
    AgentRoleDefinition,
    AgentRoleKind,
    AgentRuntimeTaskExecutor,
    AgentTaskAccessRequirement,
    AgentTaskAuthority,
    AgentTaskCompletionCondition,
    AgentTaskDefinition,
    AgentTaskExecutionContext,
    AgentTaskInputBinding,
    AgentTaskOutputStatus,
    AgentTaskPurpose,
    AgentTaskRetryPolicy,
    AgentTaskRuntimeBudget,
    AgentTaskRuntimeEnvironment,
    AgentTaskRuntimePolicy,
    AgentTaskState,
    ChildAgentRuntimeLease,
    InMemoryAgentOrchestrationCheckpointStore,
    JsonObjectContract,
)
from core.agent_runtime import (
    AgentTurnResult,
    FakeAgentRuntime,
    RuntimeAccessEnvelope,
    RuntimeAccessGrant,
    RuntimeAccessKind,
    RuntimeActor,
    RuntimeActorType,
    RuntimeBudgetAccount,
    RuntimeBudgetEnvelope,
    RuntimeBudgetLimit,
    RuntimeBudgetScope,
    RuntimeChatType,
    RuntimeGovernanceEnvelope,
    RuntimeLifecycleState,
    RuntimeMcpSnapshot,
    RuntimeMcpToolDescriptor,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimeOwnerType,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
    RequestRuntimeContext,
    RuntimeSkillContent,
    RuntimeSkillDescriptor,
    RuntimeSkillScope,
    RuntimeSkillSnapshot,
    RuntimeUsage,
    runtime_model_route_sha256,
)
from core.lifecycle.feature_registry import (
    FEATURE_LIFECYCLE_REGISTRY,
    FeatureScope,
    evaluate_feature_enablement,
)
from core.tool_plan import ToolPlan


def _route(route_id: str, model_id: str) -> RuntimeModelRoute:
    return RuntimeModelRoute(
        route_id=route_id,
        model_id=model_id,
        provider_id="newapi",
        temperature=0,
        max_tokens=200,
        timeout_seconds=10,
        enable_thinking="false",
    )


ROUTE_ECONOMY = _route("subagent/economy", "economy-model")
ROUTE_REVIEW = _route("subagent/review", "quality-review-model")
ROUTE_AGGREGATE = _route("subagent/aggregate", "quality-aggregate-model")


def _task_budget(*, tool_calls: int = 0) -> AgentTaskRuntimeBudget:
    return AgentTaskRuntimeBudget(
        model_call_limit=1,
        token_limit=100,
        cost_limit_microunits=100,
        tool_call_limit=tool_calls,
        time_limit_ms=2_000,
    )


def _policy(
    purpose: AgentTaskPurpose,
    model_class: AgentModelClass,
    route: RuntimeModelRoute,
    *,
    authority: AgentTaskAuthority | None = None,
    tool_calls: int = 0,
) -> AgentTaskRuntimePolicy:
    return AgentTaskRuntimePolicy(
        purpose=purpose,
        model_class=model_class,
        model_route_id=route.route_id,
        model_route_sha256=runtime_model_route_sha256(route),
        authority=authority or AgentTaskAuthority(),
        budget=_task_budget(tool_calls=tool_calls),
    )


def _plan(
    *,
    worker_authority: AgentTaskAuthority | None = None,
    worker_tool_calls: int = 0,
    review_route: RuntimeModelRoute = ROUTE_REVIEW,
) -> AgentOrchestrationPlan:
    roles = (
        AgentRoleDefinition(
            "coordinator",
            AgentRoleKind.COORDINATOR,
            "冻结计划并集中派发",
        ),
        AgentRoleDefinition(
            "researcher",
            AgentRoleKind.WORKER,
            "执行受限探索",
        ),
        AgentRoleDefinition(
            "reviewer",
            AgentRoleKind.REVIEWER,
            "独立验证探索结果",
        ),
        AgentRoleDefinition(
            "aggregator",
            AgentRoleKind.AGGREGATOR,
            "汇总已验证结果",
        ),
    )
    worker = AgentTaskDefinition(
        task_id="research",
        role_id="researcher",
        description="调查给定主题",
        dependencies=(),
        input_contract=JsonObjectContract(required_keys=("topic",)),
        input_bindings=(AgentTaskInputBinding("topic", "topic"),),
        output_contract=JsonObjectContract(required_keys=("finding",)),
        completion=AgentTaskCompletionCondition(
            required_data_keys=("finding",),
        ),
        timeout_ms=2_000,
        runtime_policy=_policy(
            AgentTaskPurpose.EXPLORE,
            AgentModelClass.ECONOMY,
            ROUTE_ECONOMY,
            authority=worker_authority,
            tool_calls=worker_tool_calls,
        ),
    )
    review = AgentTaskDefinition(
        task_id="review",
        role_id="reviewer",
        description="独立核验调查结果",
        dependencies=("research",),
        input_contract=JsonObjectContract(required_keys=("finding",)),
        input_bindings=(
            AgentTaskInputBinding(
                "finding",
                "finding",
                source_task_id="research",
            ),
        ),
        output_contract=JsonObjectContract(required_keys=("verdict",)),
        completion=AgentTaskCompletionCondition(
            required_data_keys=("verdict",),
        ),
        timeout_ms=2_000,
        runtime_policy=_policy(
            AgentTaskPurpose.VERIFY,
            AgentModelClass.QUALITY,
            review_route,
        ),
    )
    aggregate = AgentTaskDefinition(
        task_id="aggregate",
        role_id="aggregator",
        description="汇总调查与核验结果",
        dependencies=("research", "review"),
        input_contract=JsonObjectContract(
            required_keys=("finding", "verdict"),
        ),
        input_bindings=(
            AgentTaskInputBinding(
                "finding",
                "finding",
                source_task_id="research",
            ),
            AgentTaskInputBinding(
                "verdict",
                "verdict",
                source_task_id="review",
            ),
        ),
        output_contract=JsonObjectContract(required_keys=("answer",)),
        completion=AgentTaskCompletionCondition(
            required_data_keys=("answer",),
        ),
        timeout_ms=2_000,
        runtime_policy=_policy(
            AgentTaskPurpose.AGGREGATE,
            AgentModelClass.QUALITY,
            ROUTE_AGGREGATE,
        ),
    )
    return AgentOrchestrationPlan(
        plan_id="runtime-plan",
        revision=1,
        roles=roles,
        tasks=(worker, review, aggregate),
        root_input_contract=JsonObjectContract(required_keys=("topic",)),
        aggregation_task_id="aggregate",
        budget=AgentOrchestrationBudget(
            max_tasks=3,
            max_concurrency=1,
            max_model_calls=3,
            max_tokens=300,
            max_cost_microunits=300,
            max_elapsed_ms=10_000,
            max_output_bytes=512 * 1024,
            max_checkpoints=3,
            max_tool_calls=worker_tool_calls,
        ),
    )


def _limit(
    scope: RuntimeBudgetScope,
    *,
    model_calls: int,
    tokens: int,
    cost: int,
    steps: int,
    time_ms: int,
    concurrency: int,
    models: tuple[str, ...] = (),
) -> RuntimeBudgetLimit:
    return RuntimeBudgetLimit(
        scope,
        model_call_limit=model_calls,
        token_limit=tokens,
        cost_limit_microunits=cost,
        step_limit=steps,
        time_limit_ms=time_ms,
        concurrency_limit=concurrency,
        allowed_model_ids=models,
    )


def _governance(
    *,
    access: tuple[RuntimeAccessGrant, ...] = (),
    models: tuple[str, ...] = (
        "economy-model",
        "quality-review-model",
        "quality-aggregate-model",
    ),
) -> RuntimeGovernanceEnvelope:
    return RuntimeGovernanceEnvelope(
        budgets=RuntimeBudgetEnvelope(
            run=_limit(
                RuntimeBudgetScope.RUN,
                model_calls=20,
                tokens=10_000,
                cost=10_000,
                steps=30,
                time_ms=60_000,
                concurrency=8,
                models=models,
            ),
            turn=_limit(
                RuntimeBudgetScope.TURN,
                model_calls=20,
                tokens=10_000,
                cost=10_000,
                steps=30,
                time_ms=60_000,
                concurrency=8,
                models=models,
            ),
            tool=_limit(
                RuntimeBudgetScope.TOOL,
                model_calls=0,
                tokens=0,
                cost=0,
                steps=10,
                time_ms=30_000,
                concurrency=4,
            ),
            subagent=_limit(
                RuntimeBudgetScope.SUBAGENT,
                model_calls=10,
                tokens=2_000,
                cost=2_000,
                steps=10,
                time_ms=30_000,
                concurrency=2,
                models=models,
            ),
        ),
        access=RuntimeAccessEnvelope(access),
    )


def _parent_context(
    *,
    governance: RuntimeGovernanceEnvelope | None = None,
    tool_plan: ToolPlan | None = None,
    plans: tuple[RuntimePlanRef, ...] = (),
) -> RequestRuntimeContext:
    resolved_plans = list(plans)
    if tool_plan is not None:
        resolved_plans.append(RuntimePlanRef(
            RuntimePlanKind.TOOL,
            "tool-plan:parent",
            tool_plan.sha256,
        ))
    return RequestRuntimeContext(
        request_id="parent-request",
        agent_id="nanobot",
        principal=RuntimePrincipal("qq", RuntimeOwnerType.USER, "u1"),
        session_id="private-u1",
        chat_type=RuntimeChatType.PRIVATE,
        trace_id="trace-parent",
        run_id="run-parent",
        turn_id="turn-parent",
        correlation_id="correlation-parent",
        actor=RuntimeActor(RuntimeActorType.AGENT, "coordinator"),
        capabilities=frozenset({"parent-secret-capability"}),
        plans=tuple(resolved_plans),
        governance=governance or _governance(),
    )


class _FailingRuntime(FakeAgentRuntime):
    async def run_event(self, request, handler):
        del request, handler
        raise RuntimeError("不应泄漏的底层错误")


class _Factory:
    def __init__(
        self,
        *,
        raw_output: str = "",
        usage: RuntimeUsage | None = None,
        model_calls: int = 1,
        execution_error: bool = False,
    ) -> None:
        self.bindings = []
        self.runtimes = []
        self.raw_output = raw_output
        self.usage = usage or RuntimeUsage(
            input_tokens=1,
            output_tokens=1,
            cost_microunits=1,
        )
        self.model_calls = model_calls
        self.execution_error = execution_error

    async def create(self, binding):
        self.bindings.append(binding)
        runtime_type = _FailingRuntime if self.execution_error else FakeAgentRuntime
        runtime = runtime_type(runtime_id=f"fake:{binding.task_id}")
        payload = json.loads(binding.request_content)
        if self.raw_output:
            output = self.raw_output
        elif binding.task_id == "research":
            output = json.dumps({
                "status": "success",
                "summary": "调查完成",
                "next_actions": [],
                "artifacts": [],
                "data": {"finding": f"发现：{payload['inputs']['topic']}"},
            }, ensure_ascii=False)
        elif binding.task_id == "review":
            output = json.dumps({
                "status": "success",
                "summary": "核验完成",
                "next_actions": [],
                "artifacts": [],
                "data": {"verdict": "证据一致"},
            }, ensure_ascii=False)
        else:
            output = json.dumps({
                "status": "success",
                "summary": "汇总完成",
                "next_actions": [],
                "artifacts": [],
                "data": {"answer": "最终答案"},
            }, ensure_ascii=False)
        runtime.queue_result(AgentTurnResult(
            raw_result={"provider": "fake"},
            messages=(RuntimeMessage("assistant", output),),
            usage=self.usage,
            model_calls=self.model_calls,
        ))
        self.runtimes.append(runtime)
        return ChildAgentRuntimeLease(runtime)


def _feature_decision():
    descriptor = FEATURE_LIFECYCLE_REGISTRY.require(MULTI_AGENT_FEATURE_ID)
    return evaluate_feature_enablement(
        MULTI_AGENT_FEATURE_ID,
        requested=True,
        scope=FeatureScope.PRIVATE_SESSION,
        satisfied_gates=frozenset(descriptor.enablement_gates),
    )


@pytest.mark.asyncio
async def test_runtime_executor_runs_dag_with_isolated_models_and_parent_budget():
    plan = _plan()
    parent = _parent_context()
    factory = _Factory()
    executor = AgentRuntimeTaskExecutor(
        plan=plan,
        environment=AgentTaskRuntimeEnvironment(
            parent_context=parent,
            parent_tool_plan=None,
            model_routes=(ROUTE_AGGREGATE, ROUTE_ECONOMY, ROUTE_REVIEW),
        ),
        runtime_factory=factory,
    )
    account = RuntimeBudgetAccount(
        parent.execution_identity(),
        parent.governance,
    )
    orchestrator = AgentDagOrchestrator(
        executor=executor,
        checkpoint_store=InMemoryAgentOrchestrationCheckpointStore(),
        budget_account=account,
    )
    approval = AgentOrchestrationApproval(
        approval_id="approval-runtime",
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        plan_sha256=plan.content_sha256,
        approved_by="operator",
        approved_at=datetime.now(timezone.utc),
    )
    freeze = AgentOrchestrationFreeze(
        freeze_id="freeze-runtime",
        approval_id=approval.approval_id,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        plan_sha256=plan.content_sha256,
        frozen_by="scheduler",
        frozen_at=approval.approved_at,
    )
    request = AgentOrchestrationRequest(
        orchestration_id="orchestration-runtime",
        identity=parent.execution_identity(),
        plan=plan,
        approval=approval,
        freeze=freeze,
        root_input={"topic": "Agent Harness"},
    )

    result = await orchestrator.execute(
        request,
        feature_decision=_feature_decision(),
    )

    assert result.state is AgentOrchestrationState.SUCCEEDED
    assert result.aggregate_output.data == {"answer": "最终答案"}
    assert [item.model_route.model_id for item in factory.bindings] == [
        "economy-model",
        "quality-review-model",
        "quality-aggregate-model",
    ]
    assert all(item.context.principal == parent.principal for item in factory.bindings)
    assert all(item.context.session_id != parent.session_id for item in factory.bindings)
    assert all(item.context.chat_type is RuntimeChatType.TASK for item in factory.bindings)
    assert all(
        item.context.capabilities == frozenset({"bounded_subagent"})
        for item in factory.bindings
    )
    assert all(
        item.context.governance.budgets.subagent.model_call_limit == 0
        for item in factory.bindings
    )
    assert all(
        runtime.state is RuntimeLifecycleState.STOPPED
        for runtime in factory.runtimes
    )
    consumption = account.consumption(RuntimeBudgetScope.SUBAGENT)
    assert consumption.model_calls == 3
    assert consumption.tokens == 6
    assert consumption.cost_microunits == 3
    assert consumption.steps == 3


@pytest.mark.asyncio
async def test_runtime_retry_keeps_frozen_idempotency_and_isolates_attempt_runs():
    base = _plan()
    research = replace(
        base.task_by_id["research"],
        retry_policy=AgentTaskRetryPolicy(
            max_attempts=2,
            retryable_error_codes=("child_reported_error",),
            backoff_ms=(0,),
            idempotency_key="research-stable-operation",
        ),
    )
    plan = AgentOrchestrationPlan(
        plan_id="runtime-retry-plan",
        revision=1,
        roles=base.roles,
        tasks=(
            research,
            base.task_by_id["review"],
            base.task_by_id["aggregate"],
        ),
        root_input_contract=base.root_input_contract,
        aggregation_task_id=base.aggregation_task_id,
        budget=replace(
            base.budget,
            max_model_calls=4,
            max_tokens=400,
            max_cost_microunits=400,
        ),
    )

    class RetryFactory(_Factory):
        async def create(self, binding):
            first_research = (
                binding.task_id == "research"
                and not any(
                    item.task_id == "research" for item in self.bindings
                )
            )
            if first_research:
                self.raw_output = json.dumps({
                    "status": "error",
                    "summary": "上游暂时不可用",
                    "next_actions": ["按冻结策略局部重试"],
                    "artifacts": [],
                    "data": {},
                }, ensure_ascii=False)
            try:
                return await super().create(binding)
            finally:
                self.raw_output = ""

    parent = _parent_context()
    factory = RetryFactory()
    account = RuntimeBudgetAccount(
        parent.execution_identity(),
        parent.governance,
    )
    orchestrator = AgentDagOrchestrator(
        executor=AgentRuntimeTaskExecutor(
            plan=plan,
            environment=AgentTaskRuntimeEnvironment(
                parent_context=parent,
                parent_tool_plan=None,
                model_routes=(
                    ROUTE_AGGREGATE,
                    ROUTE_ECONOMY,
                    ROUTE_REVIEW,
                ),
            ),
            runtime_factory=factory,
        ),
        checkpoint_store=InMemoryAgentOrchestrationCheckpointStore(),
        budget_account=account,
    )
    approved_at = datetime.now(timezone.utc)
    approval = AgentOrchestrationApproval(
        approval_id="approval-runtime-retry",
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        plan_sha256=plan.content_sha256,
        approved_by="operator",
        approved_at=approved_at,
    )
    request = AgentOrchestrationRequest(
        orchestration_id="orchestration-runtime-retry",
        identity=parent.execution_identity(),
        plan=plan,
        approval=approval,
        freeze=AgentOrchestrationFreeze(
            freeze_id="freeze-runtime-retry",
            approval_id=approval.approval_id,
            plan_id=plan.plan_id,
            plan_revision=plan.revision,
            plan_sha256=plan.content_sha256,
            frozen_by="scheduler",
            frozen_at=approved_at,
        ),
        root_input={"topic": "Agent Harness 重试"},
    )

    result = await orchestrator.execute(
        request,
        feature_decision=_feature_decision(),
    )

    assert result.state is AgentOrchestrationState.SUCCEEDED
    assert [item.task_id for item in factory.bindings] == [
        "research",
        "research",
        "review",
        "aggregate",
    ]
    retry_payloads = [
        json.loads(item.request_content)
        for item in factory.bindings[:2]
    ]
    assert [item["attempt_no"] for item in retry_payloads] == [1, 2]
    assert {
        item["idempotency_key"] for item in retry_payloads
    } == {"research-stable-operation"}
    assert (
        factory.bindings[0].context.run_id
        != factory.bindings[1].context.run_id
    )
    assert (
        factory.bindings[0].context.session_id
        != factory.bindings[1].context.session_id
    )
    assert [
        (receipt.task_id, receipt.attempt_no, receipt.state)
        for receipt in result.receipts[:2]
    ] == [
        ("research", 1, AgentTaskState.FAILED),
        ("research", 2, AgentTaskState.SUCCEEDED),
    ]


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} 测试工具",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }


def _tool_environment():
    mcp_descriptor = RuntimeMcpToolDescriptor(
        provider_id="mcp",
        server_id="search-server",
        tool_name="search",
        input_schema_json=b'{"type":"object","properties":{}}',
        execution_port_id="mcp/search-server/search",
    )
    mcp_wire_name = mcp_descriptor.wire_name
    tool_plan = ToolPlan.from_effective_tools(
        enabled={
            "workspace_read": True,
            "workspace_write": True,
            mcp_wire_name: True,
        },
        tool_schemas=[
            _schema("workspace_read"),
            _schema("workspace_write"),
            _schema(mcp_wire_name),
        ],
    )
    skill_document = "# 受限检索\n只读取工作区。\n".encode()
    skill_descriptor = RuntimeSkillDescriptor(
        provider_id="skills",
        skill_id="bounded-research",
        scope=RuntimeSkillScope.USER,
        version="1.0.0",
        description="受限检索 Skill",
        content_sha256=hashlib.sha256(skill_document).hexdigest(),
        required_permissions=("workspace:read",),
        allowed_tools=("workspace_read",),
    )
    skill_content = RuntimeSkillContent(skill_descriptor, skill_document)
    access = (
        RuntimeAccessGrant(
            RuntimeAccessKind.FILE,
            "workspace:current",
            ("read", "write"),
            "workspace_policy",
        ),
        RuntimeAccessGrant(
            RuntimeAccessKind.TOOL,
            "tool-plan:resolved",
            ("execute",),
            "request_tool_plan",
        ),
        RuntimeAccessGrant(
            RuntimeAccessKind.SKILL,
            "skill-lock:parent",
            ("load",),
            "skill_lock",
        ),
        RuntimeAccessGrant(
            RuntimeAccessKind.MCP,
            "mcp-server:search-server",
            ("call",),
            "mcp_snapshot",
        ),
    )
    parent = _parent_context(
        governance=_governance(access=access),
        tool_plan=tool_plan,
        plans=(RuntimePlanRef(
            RuntimePlanKind.WORKSPACE,
            "workspace:current",
            "a" * 64,
        ),),
    )
    authority = AgentTaskAuthority(
        access=(
            AgentTaskAccessRequirement(
                RuntimeAccessKind.FILE,
                "workspace:current",
                ("read",),
            ),
            AgentTaskAccessRequirement(
                RuntimeAccessKind.TOOL,
                "tool:workspace_read",
                ("execute",),
            ),
            AgentTaskAccessRequirement(
                RuntimeAccessKind.TOOL,
                f"tool:{mcp_wire_name}",
                ("execute",),
            ),
            AgentTaskAccessRequirement(
                RuntimeAccessKind.SKILL,
                "skill-lock:parent",
                ("load",),
            ),
            AgentTaskAccessRequirement(
                RuntimeAccessKind.MCP,
                "mcp-server:search-server",
                ("call",),
            ),
        ),
        skill_ids=(skill_descriptor.qualified_id,),
        mcp_tool_names=(mcp_wire_name,),
    )
    environment = AgentTaskRuntimeEnvironment(
        parent_context=parent,
        parent_tool_plan=tool_plan,
        model_routes=(ROUTE_AGGREGATE, ROUTE_ECONOMY, ROUTE_REVIEW),
        skill_snapshots=(RuntimeSkillSnapshot(
            provider_id="skills",
            revision="parent-skill-revision",
            skills=(skill_descriptor,),
        ),),
        skill_contents=(skill_content,),
        mcp_snapshots=(RuntimeMcpSnapshot(
            provider_id="mcp",
            revision="parent-mcp-revision",
            tools=(mcp_descriptor,),
        ),),
    )
    return parent, authority, environment, mcp_wire_name, skill_document


@pytest.mark.asyncio
async def test_runtime_executor_compiles_exact_tool_skill_mcp_and_workspace_subset(
    monkeypatch,
):
    runtime_events = []

    def capture_runtime_event(name, phase, *, attributes=None, context=None):
        runtime_events.append((
            name,
            phase,
            dict(attributes or {}),
            context,
        ))
        return None

    monkeypatch.setattr(
        "core.runtime.event_bus.emit_runtime_event",
        capture_runtime_event,
    )
    parent, authority, environment, mcp_wire_name, skill_document = _tool_environment()
    plan = _plan(worker_authority=authority, worker_tool_calls=2)
    factory = _Factory()
    executor = AgentRuntimeTaskExecutor(
        plan=plan,
        environment=environment,
        runtime_factory=factory,
    )
    task = plan.task_by_id["research"]
    context = AgentTaskExecutionContext(
        orchestration_id="orchestration-subset",
        identity=parent.execution_identity(),
        task=task,
        role=plan.role_by_id[task.role_id],
        inputs={"topic": "最小权限"},
        dependencies=(),
    )

    output = await executor.execute(context)

    assert output.data == {"finding": "发现：最小权限"}
    binding = factory.bindings[0]
    assert binding.tool_plan.executable_tool_names == frozenset({
        "workspace_read",
        mcp_wire_name,
    })
    assert len(binding.skill_snapshots) == 1
    assert [item.skill_id for item in binding.skill_snapshots[0].skills] == [
        "bounded-research",
    ]
    assert binding.skill_contents[0].document == skill_document
    assert len(binding.mcp_snapshots) == 1
    assert [item.wire_name for item in binding.mcp_snapshots[0].tools] == [
        mcp_wire_name,
    ]
    child_grants = binding.context.governance.access.grants
    assert all(item.resource != "tool-plan:resolved" for item in child_grants)
    assert next(
        item for item in child_grants
        if item.kind is RuntimeAccessKind.FILE
    ).operations == ("read",)
    assert binding.context.plan(RuntimePlanKind.SKILL) is not None
    assert binding.context.plan(RuntimePlanKind.MCP) is not None
    assert binding.context.plan(RuntimePlanKind.WORKSPACE) == (
        parent.plan(RuntimePlanKind.WORKSPACE)
    )
    subagent_events = [
        item for item in runtime_events if item[0] == "subagent.execute"
    ]
    assert [item[1] for item in subagent_events] == ["started", "succeeded"]
    assert subagent_events[0][3].run_id == parent.run_id
    assert subagent_events[0][3].task_run_id == binding.context.run_id
    assert subagent_events[1][2]["status"] == "success"
    assert subagent_events[1][2]["model_call_count"] == 1
    assert subagent_events[1][2]["input_tokens"] > 0
    serialized_events = json.dumps(subagent_events, ensure_ascii=False, default=str)
    assert "最小权限" not in serialized_events
    assert skill_document.decode("utf-8") not in serialized_events
    payload = json.loads(binding.request_content)
    assert payload["skills"][0]["document"].startswith("# 受限检索")
    assert "parent-secret-capability" not in binding.context.capabilities


def test_runtime_executor_rejects_same_physical_model_for_independent_review():
    same_model_review = _route("subagent/review-alias", "economy-model")
    plan = _plan(review_route=same_model_review)
    parent = _parent_context(
        governance=_governance(models=(
            "economy-model",
            "quality-aggregate-model",
        )),
    )

    with pytest.raises(
        AgentOrchestrationError,
        match="验证或裁判模型必须与其直接证据生产模型不同",
    ):
        AgentRuntimeTaskExecutor(
            plan=plan,
            environment=AgentTaskRuntimeEnvironment(
                parent_context=parent,
                parent_tool_plan=None,
                model_routes=(
                    ROUTE_AGGREGATE,
                    ROUTE_ECONOMY,
                    same_model_review,
                ),
            ),
            runtime_factory=_Factory(),
        )


def test_runtime_executor_rejects_plan_larger_than_parent_budget_directly():
    plan = _plan()
    base = _governance()
    parent_budget = base.budgets.subagent
    governance = RuntimeGovernanceEnvelope(
        policy_id=base.policy_id,
        budgets=replace(
            base.budgets,
            subagent=replace(
                parent_budget,
                model_call_limit=2,
                allowed_model_ids=(
                    "economy-model",
                    "quality-review-model",
                ),
            ),
        ),
        access=base.access,
    )
    parent = _parent_context(governance=governance)

    with pytest.raises(AgentOrchestrationError, match="超过父级显式预算"):
        AgentRuntimeTaskExecutor(
            plan=plan,
            environment=AgentTaskRuntimeEnvironment(
                parent_context=parent,
                parent_tool_plan=None,
                model_routes=(
                    ROUTE_AGGREGATE,
                    ROUTE_ECONOMY,
                    ROUTE_REVIEW,
                ),
            ),
            runtime_factory=_Factory(),
        )


@pytest.mark.asyncio
async def test_runtime_executor_fails_closed_on_parent_scope_expansion():
    authority = AgentTaskAuthority(access=(
        AgentTaskAccessRequirement(
            RuntimeAccessKind.FILE,
            "workspace:other-owner",
            ("read",),
        ),
    ))
    plan = _plan(worker_authority=authority)
    parent = _parent_context(governance=_governance(access=(
        RuntimeAccessGrant(
            RuntimeAccessKind.FILE,
            "workspace:current",
            ("read",),
            "workspace_policy",
        ),
    )))
    executor = AgentRuntimeTaskExecutor(
        plan=plan,
        environment=AgentTaskRuntimeEnvironment(
            parent_context=parent,
            parent_tool_plan=None,
            model_routes=(ROUTE_AGGREGATE, ROUTE_ECONOMY, ROUTE_REVIEW),
        ),
        runtime_factory=_Factory(),
    )
    task = plan.task_by_id["research"]

    with pytest.raises(AgentOrchestrationError, match="不在父权限信封内"):
        await executor.execute(AgentTaskExecutionContext(
            orchestration_id="orchestration-denied",
            identity=parent.execution_identity(),
            task=task,
            role=plan.role_by_id[task.role_id],
            inputs={"topic": "越权"},
            dependencies=(),
        ))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_output", "error_code"),
    [
        (
            "```json\n{\"status\":\"success\"}\n```",
            "child_output_contract_invalid",
        ),
        (
            json.dumps({
                "status": "success",
                "summary": "伪造资产",
                "next_actions": [],
                "artifacts": ["artifact-not-published"],
                "data": {"finding": "不可信"},
            }, ensure_ascii=False),
            "child_artifact_unpublished",
        ),
    ],
)
async def test_runtime_executor_rejects_repaired_json_and_unpublished_artifact(
    raw_output,
    error_code,
):
    plan = _plan()
    parent = _parent_context()
    executor = AgentRuntimeTaskExecutor(
        plan=plan,
        environment=AgentTaskRuntimeEnvironment(
            parent_context=parent,
            parent_tool_plan=None,
            model_routes=(ROUTE_AGGREGATE, ROUTE_ECONOMY, ROUTE_REVIEW),
        ),
        runtime_factory=_Factory(raw_output=raw_output),
    )
    task = plan.task_by_id["research"]

    output = await executor.execute(AgentTaskExecutionContext(
        orchestration_id="orchestration-output-denied",
        identity=parent.execution_identity(),
        task=task,
        role=plan.role_by_id[task.role_id],
        inputs={"topic": "输出合同"},
        dependencies=(),
    ))

    assert output.status is AgentTaskOutputStatus.ERROR
    assert output.data == {"error_code": error_code}
    assert output.model_calls == 1
    assert output.usage.total_tokens == 2


@pytest.mark.asyncio
async def test_runtime_failure_is_structured_and_charged_to_parent_budget():
    plan = _plan()
    parent = _parent_context()
    factory = _Factory(raw_output="not-json")
    account = RuntimeBudgetAccount(
        parent.execution_identity(),
        parent.governance,
    )
    orchestrator = AgentDagOrchestrator(
        executor=AgentRuntimeTaskExecutor(
            plan=plan,
            environment=AgentTaskRuntimeEnvironment(
                parent_context=parent,
                parent_tool_plan=None,
                model_routes=(
                    ROUTE_AGGREGATE,
                    ROUTE_ECONOMY,
                    ROUTE_REVIEW,
                ),
            ),
            runtime_factory=factory,
        ),
        checkpoint_store=InMemoryAgentOrchestrationCheckpointStore(),
        budget_account=account,
    )
    approval = AgentOrchestrationApproval(
        approval_id="approval-runtime-failure",
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        plan_sha256=plan.content_sha256,
        approved_by="operator",
        approved_at=datetime.now(timezone.utc),
    )
    freeze = AgentOrchestrationFreeze(
        freeze_id="freeze-runtime-failure",
        approval_id=approval.approval_id,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        plan_sha256=plan.content_sha256,
        frozen_by="scheduler",
        frozen_at=approval.approved_at,
    )

    result = await orchestrator.execute(
        AgentOrchestrationRequest(
            orchestration_id="orchestration-runtime-failure",
            identity=parent.execution_identity(),
            plan=plan,
            approval=approval,
            freeze=freeze,
            root_input={"topic": "失败计费"},
        ),
        feature_decision=_feature_decision(),
    )

    assert result.state is AgentOrchestrationState.FAILED
    receipts = {item.task_id: item for item in result.receipts}
    assert receipts["research"].error_code == "child_output_contract_invalid"
    assert receipts["review"].state is AgentTaskState.BLOCKED
    assert receipts["aggregate"].state is AgentTaskState.BLOCKED
    consumption = account.consumption(RuntimeBudgetScope.SUBAGENT)
    assert consumption.model_calls == 1
    assert consumption.tokens == 2
    assert consumption.cost_microunits == 1
    assert consumption.steps == 1


@pytest.mark.asyncio
async def test_runtime_model_can_report_error_without_fabricating_required_data():
    raw_output = json.dumps({
        "status": "error",
        "summary": "当前权限不足",
        "next_actions": ["申请只读工具"],
        "artifacts": [],
        "data": {},
    }, ensure_ascii=False)
    plan = _plan()
    parent = _parent_context()
    executor = AgentRuntimeTaskExecutor(
        plan=plan,
        environment=AgentTaskRuntimeEnvironment(
            parent_context=parent,
            parent_tool_plan=None,
            model_routes=(ROUTE_AGGREGATE, ROUTE_ECONOMY, ROUTE_REVIEW),
        ),
        runtime_factory=_Factory(raw_output=raw_output),
    )
    task = plan.task_by_id["research"]

    output = await executor.execute(AgentTaskExecutionContext(
        orchestration_id="orchestration-reported-error",
        identity=parent.execution_identity(),
        task=task,
        role=plan.role_by_id[task.role_id],
        inputs={"topic": "权限不足"},
        dependencies=(),
    ))

    assert output.status is AgentTaskOutputStatus.ERROR
    assert output.data == {"error_code": "child_reported_error"}


@pytest.mark.asyncio
async def test_invalid_usage_is_charged_at_approved_task_ceiling():
    plan = _plan()
    parent = _parent_context()
    executor = AgentRuntimeTaskExecutor(
        plan=plan,
        environment=AgentTaskRuntimeEnvironment(
            parent_context=parent,
            parent_tool_plan=None,
            model_routes=(ROUTE_AGGREGATE, ROUTE_ECONOMY, ROUTE_REVIEW),
        ),
        runtime_factory=_Factory(
            raw_output="not-json",
            usage=RuntimeUsage(),
            model_calls=0,
        ),
    )
    task = plan.task_by_id["research"]

    output = await executor.execute(AgentTaskExecutionContext(
        orchestration_id="orchestration-invalid-usage",
        identity=parent.execution_identity(),
        task=task,
        role=plan.role_by_id[task.role_id],
        inputs={"topic": "失真用量"},
        dependencies=(),
    ))

    assert output.status is AgentTaskOutputStatus.ERROR
    assert output.data == {"error_code": "child_output_contract_invalid"}
    assert output.model_calls == task.runtime_policy.budget.model_call_limit
    assert output.usage.total_tokens == task.runtime_policy.budget.token_limit
    assert output.usage.cost_microunits == (
        task.runtime_policy.budget.cost_limit_microunits
    )


@pytest.mark.asyncio
async def test_runtime_exception_is_redacted_and_charged_at_task_ceiling():
    plan = _plan()
    parent = _parent_context()
    executor = AgentRuntimeTaskExecutor(
        plan=plan,
        environment=AgentTaskRuntimeEnvironment(
            parent_context=parent,
            parent_tool_plan=None,
            model_routes=(ROUTE_AGGREGATE, ROUTE_ECONOMY, ROUTE_REVIEW),
        ),
        runtime_factory=_Factory(execution_error=True),
    )
    task = plan.task_by_id["research"]

    output = await executor.execute(AgentTaskExecutionContext(
        orchestration_id="orchestration-runtime-error",
        identity=parent.execution_identity(),
        task=task,
        role=plan.role_by_id[task.role_id],
        inputs={"topic": "底层异常"},
        dependencies=(),
    ))

    assert output.data == {"error_code": "child_runtime_execution_failed"}
    assert "不应泄漏" not in output.summary
    assert output.model_calls == task.runtime_policy.budget.model_call_limit
    assert output.usage.total_tokens == task.runtime_policy.budget.token_limit


def test_runtime_policy_rejects_economy_judge_and_is_part_of_plan_hash():
    with pytest.raises(ValueError, match="低成本模型只允许探索或检索"):
        _policy(
            AgentTaskPurpose.JUDGE,
            AgentModelClass.ECONOMY,
            ROUTE_REVIEW,
        )

    plan = _plan()
    mutated = replace(
        plan.task_by_id["research"],
        runtime_policy=replace(
            plan.task_by_id["research"].runtime_policy,
            budget=replace(
                plan.task_by_id["research"].runtime_policy.budget,
                token_limit=99,
            ),
        ),
    )
    changed = replace(
        plan,
        tasks=tuple(
            mutated if item.task_id == "research" else item
            for item in plan.tasks
        ),
        content_sha256="",
    )

    assert changed.content_sha256 != plan.content_sha256
