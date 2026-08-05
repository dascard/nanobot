from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from core.agent_orchestration import (
    MULTI_AGENT_FEATURE_ID,
    AgentDagOrchestrator,
    AgentOrchestrationApproval,
    AgentOrchestrationBudget,
    AgentOrchestrationCancellation,
    AgentOrchestrationError,
    AgentOrchestrationPlan,
    AgentOrchestrationRequest,
    AgentOrchestrationState,
    AgentRoleDefinition,
    AgentRoleKind,
    AgentTaskCompletionCondition,
    AgentTaskDefinition,
    AgentTaskInputBinding,
    AgentTaskOutput,
    AgentTaskOutputStatus,
    AgentTaskState,
    InMemoryAgentOrchestrationCheckpointStore,
    JsonObjectContract,
    current_orchestration_depth,
)
from core.agent_orchestration.scope import orchestration_worker_scope
from core.agent_runtime import (
    AgentRuntimeBudgetExceededError,
    RuntimeActor,
    RuntimeActorType,
    RuntimeBudgetAccount,
    RuntimeBudgetEnvelope,
    RuntimeBudgetLimit,
    RuntimeBudgetScope,
    RuntimeGovernanceEnvelope,
    RuntimeOwnerType,
    RuntimePrincipal,
    RuntimeRunIdentity,
    RuntimeUsage,
)
from core.lifecycle.feature_registry import (
    FEATURE_LIFECYCLE_REGISTRY,
    FeatureScope,
    evaluate_feature_enablement,
)


def _identity(run_id: str = "run-orchestration") -> RuntimeRunIdentity:
    return RuntimeRunIdentity(
        run_id=run_id,
        turn_id="turn-orchestration",
        correlation_id="trace-orchestration",
        actor=RuntimeActor(RuntimeActorType.AGENT, "coordinator"),
        owner=RuntimePrincipal("qq", RuntimeOwnerType.USER, "u1"),
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
) -> RuntimeBudgetLimit:
    return RuntimeBudgetLimit(
        scope=scope,
        model_call_limit=model_calls,
        token_limit=tokens,
        cost_limit_microunits=cost,
        step_limit=steps,
        time_limit_ms=time_ms,
        concurrency_limit=concurrency,
    )


def _governance(
    *,
    subagent_steps: int = 3,
    subagent_concurrency: int = 2,
    subagent_model_calls: int = 6,
    subagent_tokens: int = 1_000,
) -> RuntimeGovernanceEnvelope:
    return RuntimeGovernanceEnvelope(
        budgets=RuntimeBudgetEnvelope(
            run=_limit(
                RuntimeBudgetScope.RUN,
                model_calls=20,
                tokens=10_000,
                cost=100_000,
                steps=30,
                time_ms=120_000,
                concurrency=8,
            ),
            turn=_limit(
                RuntimeBudgetScope.TURN,
                model_calls=20,
                tokens=10_000,
                cost=100_000,
                steps=30,
                time_ms=120_000,
                concurrency=8,
            ),
            tool=_limit(
                RuntimeBudgetScope.TOOL,
                model_calls=0,
                tokens=0,
                cost=0,
                steps=8,
                time_ms=60_000,
                concurrency=4,
            ),
            subagent=_limit(
                RuntimeBudgetScope.SUBAGENT,
                model_calls=subagent_model_calls,
                tokens=subagent_tokens,
                cost=10_000,
                steps=subagent_steps,
                time_ms=60_000,
                concurrency=subagent_concurrency,
            ),
        )
    )


def _feature_decision(*, requested: bool = True):
    descriptor = FEATURE_LIFECYCLE_REGISTRY.require(
        MULTI_AGENT_FEATURE_ID
    )
    return evaluate_feature_enablement(
        MULTI_AGENT_FEATURE_ID,
        requested=requested,
        scope=FeatureScope.PRIVATE_SESSION,
        satisfied_gates=frozenset(descriptor.enablement_gates),
    )


def _worker_task(task_id: str, role_id: str) -> AgentTaskDefinition:
    return AgentTaskDefinition(
        task_id=task_id,
        role_id=role_id,
        description=f"调查 {task_id}",
        dependencies=(),
        input_contract=JsonObjectContract(required_keys=("topic",)),
        input_bindings=(
            AgentTaskInputBinding("topic", "topic", required=True),
        ),
        output_contract=JsonObjectContract(required_keys=("finding",)),
        completion=AgentTaskCompletionCondition(
            required_data_keys=("finding",),
        ),
        timeout_ms=2_000,
    )


def _plan(
    *,
    plan_id: str = "plan-orchestration",
    worker_ids: tuple[str, ...] = ("research_a", "research_b"),
    max_tokens: int = 100,
    worker_timeout_ms: int = 2_000,
) -> AgentOrchestrationPlan:
    roles = (
        AgentRoleDefinition(
            "coordinator",
            AgentRoleKind.COORDINATOR,
            "只负责冻结计划、集中派发和收集回执",
        ),
        AgentRoleDefinition(
            "researcher",
            AgentRoleKind.WORKER,
            "执行受限调查任务",
            ("research",),
        ),
        AgentRoleDefinition(
            "aggregator",
            AgentRoleKind.AGGREGATOR,
            "根据结构化依赖输出形成最终汇总",
            ("aggregate",),
        ),
    )
    workers = tuple(
        replace(
            _worker_task(task_id, "researcher"),
            timeout_ms=worker_timeout_ms,
        )
        for task_id in worker_ids
    )
    aggregate_keys = tuple(f"finding_{index}" for index in range(len(workers)))
    aggregate = AgentTaskDefinition(
        task_id="aggregate",
        role_id="aggregator",
        description="汇总全部调查任务",
        dependencies=worker_ids,
        input_contract=JsonObjectContract(required_keys=aggregate_keys),
        input_bindings=tuple(
            AgentTaskInputBinding(
                target_key=aggregate_keys[index],
                source_key="finding",
                source_task_id=task_id,
                required=True,
            )
            for index, task_id in enumerate(worker_ids)
        ),
        output_contract=JsonObjectContract(required_keys=("answer",)),
        completion=AgentTaskCompletionCondition(
            required_data_keys=("answer",),
        ),
        timeout_ms=2_000,
    )
    task_count = len(workers) + 1
    return AgentOrchestrationPlan(
        plan_id=plan_id,
        revision=1,
        roles=roles,
        tasks=(*workers, aggregate),
        root_input_contract=JsonObjectContract(required_keys=("topic",)),
        aggregation_task_id="aggregate",
        budget=AgentOrchestrationBudget(
            max_tasks=task_count,
            max_concurrency=min(2, task_count),
            max_model_calls=task_count,
            max_tokens=max_tokens,
            max_cost_microunits=1_000,
            max_elapsed_ms=10_000,
            max_output_bytes=512 * 1024,
            max_checkpoints=task_count,
        ),
    )


def _request(
    plan: AgentOrchestrationPlan,
    *,
    identity: RuntimeRunIdentity | None = None,
    orchestration_id: str = "orch-1",
) -> AgentOrchestrationRequest:
    run_identity = identity or _identity()
    return AgentOrchestrationRequest(
        orchestration_id=orchestration_id,
        identity=run_identity,
        plan=plan,
        approval=AgentOrchestrationApproval(
            approval_id=f"approval-{orchestration_id}",
            plan_id=plan.plan_id,
            plan_revision=plan.revision,
            plan_sha256=plan.content_sha256,
            approved_by="operator-1",
            approved_at=datetime.now(timezone.utc),
        ),
        root_input={"topic": "中文 Agent Harness"},
    )


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.contexts = []
        self.running = 0
        self.max_running = 0

    async def execute(self, context):
        self.calls.append(context.task.task_id)
        self.contexts.append(context)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await asyncio.sleep(0.01)
            assert current_orchestration_depth() == 1
            if context.task.task_id == "aggregate":
                return AgentTaskOutput(
                    AgentTaskOutputStatus.SUCCESS,
                    "汇总完成",
                    next_actions=("交付协调者",),
                    data={"answer": " + ".join(context.inputs.values())},
                    usage=RuntimeUsage(input_tokens=1, output_tokens=1),
                )
            return AgentTaskOutput(
                AgentTaskOutputStatus.SUCCESS,
                f"{context.task.task_id} 完成",
                next_actions=("等待协调者汇总",),
                data={"finding": f"发现-{context.task.task_id}"},
                usage=RuntimeUsage(input_tokens=1, output_tokens=1),
            )
        finally:
            self.running -= 1


def _orchestrator(
    executor,
    *,
    identity: RuntimeRunIdentity | None = None,
    governance: RuntimeGovernanceEnvelope | None = None,
    store=None,
    sink=None,
):
    run_identity = identity or _identity()
    account = RuntimeBudgetAccount(
        run_identity,
        governance or _governance(),
        sink=sink,
    )
    checkpoint_store = store or InMemoryAgentOrchestrationCheckpointStore()
    return (
        AgentDagOrchestrator(
            executor=executor,
            checkpoint_store=checkpoint_store,
            budget_account=account,
        ),
        checkpoint_store,
        account,
    )


def test_plan_defines_roles_contracts_completion_and_deterministic_batches():
    plan = _plan()

    assert plan.execution_batches() == (
        ("research_a", "research_b"),
        ("aggregate",),
    )
    assert plan.to_dict()["communication_mode"] == "coordinator_mediated"
    assert len(plan.content_sha256) == 64
    assert plan.budget.max_spawn_depth == 1
    assert plan.task_by_id["aggregate"].completion.required_data_keys == (
        "answer",
    )


def test_plan_rejects_cycle_unknown_binding_and_incomplete_aggregation():
    task_a = AgentTaskDefinition(
        task_id="a",
        role_id="worker",
        description="任务 A",
        dependencies=("b",),
        input_contract=JsonObjectContract(required_keys=("value",)),
        input_bindings=(
            AgentTaskInputBinding("value", "value", "b", True),
        ),
        output_contract=JsonObjectContract(required_keys=("value",)),
        completion=AgentTaskCompletionCondition(required_data_keys=("value",)),
    )
    task_b = AgentTaskDefinition(
        task_id="b",
        role_id="aggregator",
        description="任务 B",
        dependencies=("a",),
        input_contract=JsonObjectContract(required_keys=("value",)),
        input_bindings=(
            AgentTaskInputBinding("value", "value", "a", True),
        ),
        output_contract=JsonObjectContract(required_keys=("value",)),
        completion=AgentTaskCompletionCondition(required_data_keys=("value",)),
    )
    roles = (
        AgentRoleDefinition("coordinator", AgentRoleKind.COORDINATOR, "协调"),
        AgentRoleDefinition("worker", AgentRoleKind.WORKER, "执行"),
        AgentRoleDefinition("aggregator", AgentRoleKind.AGGREGATOR, "汇总"),
    )
    with pytest.raises(ValueError, match="循环依赖|其他任务不能依赖"):
        AgentOrchestrationPlan(
            "cycle",
            1,
            roles,
            (task_a, task_b),
            JsonObjectContract(optional_keys=("value",)),
            "b",
            AgentOrchestrationBudget(2, 1, 2, 100, 100, 1_000, 10_000, 2),
        )

    good = _plan()
    aggregate = good.task_by_id["aggregate"]
    with pytest.raises(ValueError, match="必须直接依赖所有其他任务"):
        AgentOrchestrationPlan(
            plan_id="incomplete",
            revision=1,
            roles=good.roles,
            tasks=(
                good.task_by_id["research_a"],
                good.task_by_id["research_b"],
                AgentTaskDefinition(
                    task_id=aggregate.task_id,
                    role_id=aggregate.role_id,
                    description=aggregate.description,
                    dependencies=("research_a",),
                    input_contract=JsonObjectContract(
                        required_keys=("finding_0",)
                    ),
                    input_bindings=(
                        AgentTaskInputBinding(
                            "finding_0",
                            "finding",
                            "research_a",
                            True,
                        ),
                    ),
                    output_contract=aggregate.output_contract,
                    completion=aggregate.completion,
                ),
            ),
            root_input_contract=good.root_input_contract,
            aggregation_task_id="aggregate",
            budget=good.budget,
        )


@pytest.mark.asyncio
async def test_orchestrator_executes_real_dag_with_budget_and_checkpoints():
    decisions = []

    class Sink:
        def emit(self, decision):
            decisions.append(decision)

    executor = _RecordingExecutor()
    orchestrator, store, _ = _orchestrator(executor, sink=Sink())
    request = _request(_plan())

    result = await orchestrator.execute(
        request,
        feature_decision=_feature_decision(),
    )

    assert result.state is AgentOrchestrationState.SUCCEEDED
    assert result.aggregate_output is not None
    assert result.aggregate_output.data["answer"] == (
        "发现-research_a + 发现-research_b"
    )
    assert executor.calls[:2] == ["research_a", "research_b"]
    assert executor.calls[-1] == "aggregate"
    assert executor.max_running == 2
    assert [item.task_id for item in result.receipts] == [
        "research_a",
        "research_b",
        "aggregate",
    ]
    assert all(item.state is AgentTaskState.SUCCEEDED for item in result.receipts)
    latest = await store.load_latest(
        request.orchestration_id,
        owner_id=request.identity.owner.canonical_id,
    )
    assert latest is not None
    assert latest.sequence == 3
    assert latest.checkpoint_id == result.latest_checkpoint_id
    assert len(latest.receipt_sha256s) == 3
    assert all(context.nesting_depth == 1 for context in executor.contexts)
    assert all(context.spawn_allowed is False for context in executor.contexts)
    assert not hasattr(executor.contexts[0], "send")
    assert not hasattr(executor.contexts[0], "orchestrator")
    operations = [item.operation for item in decisions]
    assert operations.count("subagent_reservation") == 9
    assert operations.count("subagent_usage_recorded") == 9


@pytest.mark.asyncio
async def test_multi_agent_default_off_keeps_single_agent_path_untouched():
    executor = _RecordingExecutor()
    orchestrator, store, _ = _orchestrator(executor)
    request = _request(_plan())

    with pytest.raises(AgentOrchestrationError, match="multi_agent_disabled"):
        await orchestrator.execute(
            request,
            feature_decision=_feature_decision(requested=False),
        )

    assert executor.calls == []
    assert await store.load_latest(
        request.orchestration_id,
        owner_id=request.identity.owner.canonical_id,
    ) is None
    descriptor = FEATURE_LIFECYCLE_REGISTRY.require(MULTI_AGENT_FEATURE_ID)
    assert descriptor.default_enabled is False


@pytest.mark.asyncio
async def test_orchestrator_rejects_zero_parent_budget_and_recursive_spawn():
    executor = _RecordingExecutor()
    identity = _identity("run-zero-budget")
    orchestrator, _, _ = _orchestrator(
        executor,
        identity=identity,
        governance=RuntimeGovernanceEnvelope(),
    )
    request = _request(_plan(), identity=identity, orchestration_id="zero")
    with pytest.raises(AgentOrchestrationError, match="subagent_budget_denied"):
        await orchestrator.execute(
            request,
            feature_decision=_feature_decision(),
        )

    identity_nested = _identity("run-nested")
    nested, _, _ = _orchestrator(executor, identity=identity_nested)
    nested_request = _request(
        _plan(plan_id="nested-plan"),
        identity=identity_nested,
        orchestration_id="nested",
    )
    with orchestration_worker_scope():
        with pytest.raises(AgentOrchestrationError, match="recursive_spawn_denied"):
            await nested.execute(
                nested_request,
                feature_decision=_feature_decision(),
            )

    assert executor.calls == []


@pytest.mark.asyncio
async def test_orchestrator_rejects_identity_switch_with_same_run_id():
    executor = _RecordingExecutor()
    identity = _identity("run-owner-bound")
    orchestrator, _, _ = _orchestrator(executor, identity=identity)
    foreign_identity = replace(
        identity,
        owner=RuntimePrincipal("qq", RuntimeOwnerType.USER, "u2"),
    )

    with pytest.raises(AgentOrchestrationError, match="budget_identity_mismatch"):
        await orchestrator.execute(
            _request(
                _plan(plan_id="foreign-owner-plan"),
                identity=foreign_identity,
                orchestration_id="foreign-owner",
            ),
            feature_decision=_feature_decision(),
        )

    assert executor.calls == []


@pytest.mark.asyncio
async def test_cancellation_stops_workers_and_checkpoints_terminal_states():
    started = asyncio.Event()

    class BlockingExecutor:
        async def execute(self, context):
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("取消后不应继续")

    orchestrator, store, _ = _orchestrator(BlockingExecutor())
    request = _request(_plan())
    cancellation = AgentOrchestrationCancellation()
    running = asyncio.create_task(orchestrator.execute(
        request,
        feature_decision=_feature_decision(),
        cancellation=cancellation,
    ))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert cancellation.request("operator_cancelled") is True
    assert cancellation.request("duplicate") is False

    result = await asyncio.wait_for(running, timeout=2)

    assert result.state is AgentOrchestrationState.CANCELLED
    assert result.failure_code == "operator_cancelled"
    assert all(
        item.state in {AgentTaskState.CANCELLED, AgentTaskState.BLOCKED}
        for item in result.receipts
    )
    latest = await store.load_latest(
        request.orchestration_id,
        owner_id=request.identity.owner.canonical_id,
    )
    assert latest is not None
    assert latest.sequence == len(request.plan.tasks)


@pytest.mark.asyncio
async def test_task_failure_blocks_aggregator_without_hidden_retry():
    class FailingExecutor(_RecordingExecutor):
        async def execute(self, context):
            self.calls.append(context.task.task_id)
            if context.task.task_id == "research_a":
                raise RuntimeError("sensitive failure body")
            return await super().execute(context)

    executor = FailingExecutor()
    orchestrator, _, _ = _orchestrator(executor)
    result = await orchestrator.execute(
        _request(_plan()),
        feature_decision=_feature_decision(),
    )

    assert result.state is AgentOrchestrationState.FAILED
    receipt_by_id = {item.task_id: item for item in result.receipts}
    assert receipt_by_id["research_a"].error_code == "task_executor_failed"
    assert receipt_by_id["aggregate"].state is AgentTaskState.BLOCKED
    assert executor.calls.count("research_a") == 1
    assert "aggregate" not in executor.calls
    assert "sensitive failure body" not in str(result.outputs)
    error_output = result.outputs["research_a"]
    assert error_output.status is AgentTaskOutputStatus.ERROR
    assert error_output.summary
    assert error_output.next_actions
    assert error_output.data["error_code"] == "task_executor_failed"


@pytest.mark.asyncio
async def test_task_timeout_is_terminal_and_does_not_run_aggregator():
    class SlowExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, context):
            self.calls.append(context.task.task_id)
            await asyncio.sleep(1)
            return AgentTaskOutput(
                AgentTaskOutputStatus.SUCCESS,
                "迟到结果",
                data={"finding": "迟到"},
            )

    executor = SlowExecutor()
    plan = _plan(worker_ids=("research_a",), worker_timeout_ms=10)
    identity = _identity("run-timeout")
    orchestrator, _, _ = _orchestrator(executor, identity=identity)
    result = await orchestrator.execute(
        _request(plan, identity=identity, orchestration_id="timeout"),
        feature_decision=_feature_decision(),
    )

    assert result.state is AgentOrchestrationState.FAILED
    assert result.receipts[0].state is AgentTaskState.TIMED_OUT
    assert result.receipts[0].error_code == "task_timeout"
    assert executor.calls == ["research_a"]


@pytest.mark.asyncio
async def test_actual_usage_over_plan_budget_fails_closed():
    class ExpensiveExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, context):
            self.calls.append(context.task.task_id)
            return AgentTaskOutput(
                AgentTaskOutputStatus.SUCCESS,
                "完成但超预算",
                data={"finding": "结果"},
                usage=RuntimeUsage(input_tokens=3, output_tokens=3),
            )

    executor = ExpensiveExecutor()
    plan = _plan(worker_ids=("research_a",), max_tokens=5)
    identity = _identity("run-usage")
    orchestrator, _, _ = _orchestrator(
        executor,
        identity=identity,
        governance=_governance(
            subagent_steps=2,
            subagent_concurrency=2,
            subagent_tokens=20,
        ),
    )
    result = await orchestrator.execute(
        _request(plan, identity=identity, orchestration_id="usage"),
        feature_decision=_feature_decision(),
    )

    assert result.state is AgentOrchestrationState.FAILED
    assert result.receipts[0].error_code == "orchestration_budget_exceeded"
    assert result.receipts[-1].state is AgentTaskState.BLOCKED
    assert executor.calls == ["research_a"]


@pytest.mark.asyncio
async def test_parent_runtime_budget_denial_keeps_stable_failure_code():
    executor = _RecordingExecutor()
    identity = _identity("run-parent-budget")
    plan = _plan(
        plan_id="parent-budget-plan",
        worker_ids=("research_a",),
        max_tokens=10,
    )
    orchestrator, _, account = _orchestrator(
        executor,
        identity=identity,
        governance=_governance(
            subagent_steps=2,
            subagent_concurrency=2,
            subagent_tokens=10,
        ),
    )
    account.record_subagent_usage(
        RuntimeUsage(input_tokens=9),
        model_calls=0,
    )

    result = await orchestrator.execute(
        _request(plan, identity=identity, orchestration_id="parent-budget"),
        feature_decision=_feature_decision(),
    )

    assert result.state is AgentOrchestrationState.FAILED
    assert result.receipts[0].error_code == "runtime_budget_exceeded"
    assert result.outputs["research_a"].data["error_code"] == (
        "runtime_budget_exceeded"
    )
    assert result.receipts[-1].state is AgentTaskState.BLOCKED
    assert executor.calls == ["research_a"]


@pytest.mark.asyncio
async def test_checkpoint_failure_cannot_be_reported_as_success():
    class FailingStore:
        async def save(self, checkpoint):
            raise RuntimeError("database unavailable")

        async def load_latest(self, orchestration_id, *, owner_id):
            return None

    executor = _RecordingExecutor()
    orchestrator, _, _ = _orchestrator(executor, store=FailingStore())
    result = await orchestrator.execute(
        _request(_plan()),
        feature_decision=_feature_decision(),
    )

    assert result.state is AgentOrchestrationState.FAILED
    assert result.failure_code == "checkpoint_store_failed"
    assert result.aggregate_output is None
    assert result.latest_checkpoint_id == ""


@pytest.mark.asyncio
async def test_checkpoint_store_is_owner_isolated_and_monotonic():
    executor = _RecordingExecutor()
    orchestrator, store, _ = _orchestrator(executor)
    request = _request(_plan())
    result = await orchestrator.execute(
        request,
        feature_decision=_feature_decision(),
    )
    assert result.state is AgentOrchestrationState.SUCCEEDED

    assert await store.load_latest(
        request.orchestration_id,
        owner_id="qq:user:other",
    ) is None
    latest = await store.load_latest(
        request.orchestration_id,
        owner_id=request.identity.owner.canonical_id,
    )
    assert latest is not None
    with pytest.raises(ValueError, match="sequence|引用"):
        await store.save(type(latest)(
            checkpoint_id="forged-checkpoint",
            orchestration_id=latest.orchestration_id,
            identity=latest.identity,
            plan_sha256=latest.plan_sha256,
            sequence=latest.sequence,
            parent_checkpoint_id=latest.parent_checkpoint_id,
            task_states=latest.task_states,
            outputs=latest.outputs,
            receipt_sha256s=latest.receipt_sha256s,
            created_at=datetime.now(timezone.utc),
        ))


def test_runtime_budget_account_reserves_releases_and_records_subagent_usage():
    decisions = []

    class Sink:
        def emit(self, decision):
            decisions.append(decision)

    account = RuntimeBudgetAccount(_identity(), _governance(), sink=Sink())
    first = account.reserve_subagent("worker-a")
    second = account.reserve_subagent("worker-b")
    with pytest.raises(
        AgentRuntimeBudgetExceededError,
        match="concurrency_limit",
    ):
        account.reserve_subagent("worker-c")
    account.record_subagent_usage(
        RuntimeUsage(input_tokens=3, output_tokens=2, cost_microunits=10),
        model_calls=1,
    )
    account.release(first)
    account.release(second)
    third = account.reserve_subagent("worker-c")
    account.release(third)
    with pytest.raises(ValueError, match="已释放"):
        account.release(third)

    assert any(
        item.operation == "subagent_usage_recorded"
        and item.scope is RuntimeBudgetScope.SUBAGENT
        for item in decisions
    )
