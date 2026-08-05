from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError

from core.agent_orchestration import (
    MULTI_AGENT_FEATURE_ID,
    AgentDagOrchestrator,
    AgentOrchestrationApproval,
    AgentOrchestrationBudget,
    AgentOrchestrationError,
    AgentOrchestrationFreeze,
    AgentOrchestrationPlan,
    AgentOrchestrationRequest,
    AgentOrchestrationState,
    AgentPlanEventKind,
    AgentPlanGovernanceService,
    AgentPlanRevisionState,
    AgentRoleDefinition,
    AgentRoleKind,
    AgentTaskCompletionCondition,
    AgentTaskDefinition,
    AgentTaskInputBinding,
    AgentTaskOutput,
    AgentTaskOutputStatus,
    AgentTaskRetryPolicy,
    GovernedAgentDagOrchestrator,
    InMemoryAgentOrchestrationCheckpointStore,
    InMemoryAgentPlanRepository,
    JsonObjectContract,
    SqlAlchemyAgentOrchestrationCheckpointStore,
    SqlAlchemyAgentPlanRepository,
)
from core.agent_orchestration.serialization import (
    decode_agent_orchestration_checkpoint,
    decode_agent_orchestration_plan,
    encode_agent_orchestration_checkpoint,
    encode_agent_orchestration_plan,
)
from core.agent_runtime import (
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
)
from core.db.session import session_factory_from_session
from core.lifecycle.feature_registry import (
    FEATURE_LIFECYCLE_REGISTRY,
    FeatureScope,
    evaluate_feature_enablement,
)
from core.schema_migrations import _agent_orchestration_governance_v1


def _identity(
    run_id: str = "run-plan-governance",
    owner_id: str = "u-plan",
) -> RuntimeRunIdentity:
    return RuntimeRunIdentity(
        run_id=run_id,
        turn_id=f"{run_id}:turn",
        correlation_id=f"{run_id}:trace",
        actor=RuntimeActor(RuntimeActorType.AGENT, "coordinator"),
        owner=RuntimePrincipal("qq", RuntimeOwnerType.USER, owner_id),
    )


def _plan(
    *,
    revision: int = 1,
    worker_description: str = "调查主题",
    retry: AgentTaskRetryPolicy | None = None,
    max_tokens: int = 1_000,
) -> AgentOrchestrationPlan:
    worker = AgentTaskDefinition(
        task_id="research",
        role_id="worker",
        description=worker_description,
        dependencies=(),
        input_contract=JsonObjectContract(required_keys=("topic",)),
        input_bindings=(
            AgentTaskInputBinding("topic", "topic", required=True),
        ),
        output_contract=JsonObjectContract(required_keys=("finding",)),
        completion=AgentTaskCompletionCondition(
            required_data_keys=("finding",),
        ),
        retry_policy=retry or AgentTaskRetryPolicy(),
    )
    aggregate = AgentTaskDefinition(
        task_id="aggregate",
        role_id="aggregator",
        description="汇总调查结果",
        dependencies=("research",),
        input_contract=JsonObjectContract(required_keys=("finding",)),
        input_bindings=(
            AgentTaskInputBinding(
                "finding",
                "finding",
                source_task_id="research",
                required=True,
            ),
        ),
        output_contract=JsonObjectContract(required_keys=("answer",)),
        completion=AgentTaskCompletionCondition(
            required_data_keys=("answer",),
        ),
    )
    return AgentOrchestrationPlan(
        plan_id="plan-governed",
        revision=revision,
        roles=(
            AgentRoleDefinition(
                "coordinator",
                AgentRoleKind.COORDINATOR,
                "冻结计划并调度",
            ),
            AgentRoleDefinition(
                "worker",
                AgentRoleKind.WORKER,
                "执行单个调查任务",
            ),
            AgentRoleDefinition(
                "aggregator",
                AgentRoleKind.AGGREGATOR,
                "聚合结构化结果",
            ),
        ),
        tasks=(worker, aggregate),
        root_input_contract=JsonObjectContract(required_keys=("topic",)),
        aggregation_task_id="aggregate",
        budget=AgentOrchestrationBudget(
            max_tasks=2,
            max_concurrency=1,
            max_model_calls=10,
            max_tokens=max_tokens,
            max_cost_microunits=100_000,
            max_elapsed_ms=10_000,
            max_output_bytes=256_000,
            max_checkpoints=2,
            max_tool_calls=0,
        ),
    )


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}-{self.value}"


def _service(repository=None) -> AgentPlanGovernanceService:
    return AgentPlanGovernanceService(
        repository or InMemoryAgentPlanRepository(),
        now=lambda: datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
        id_factory=_Ids(),
    )


def _approve_and_freeze(
    service: AgentPlanGovernanceService,
    plan: AgentOrchestrationPlan,
    identity: RuntimeRunIdentity,
    *,
    repair_reason_code: str = "",
):
    preview = service.preview(
        plan,
        identity=identity,
        proposed_by="coordinator",
        repair_reason_code=repair_reason_code,
    )
    approved = service.approve(
        plan_id=plan.plan_id,
        revision=plan.revision,
        plan_sha256=plan.content_sha256,
        owner=identity.owner,
        approved_by="human-reviewer",
        expected_event_sequence=preview.latest_event_sequence,
    )
    assert approved is not None and approved.approval is not None
    frozen = service.freeze(
        plan_id=plan.plan_id,
        revision=plan.revision,
        plan_sha256=plan.content_sha256,
        approval_id=approved.approval.approval_id,
        owner=identity.owner,
        frozen_by="orchestration-host",
        expected_event_sequence=approved.latest_event_sequence,
    )
    assert frozen is not None
    return frozen


def _feature_decision():
    descriptor = FEATURE_LIFECYCLE_REGISTRY.require(MULTI_AGENT_FEATURE_ID)
    return evaluate_feature_enablement(
        MULTI_AGENT_FEATURE_ID,
        requested=True,
        scope=FeatureScope.PRIVATE_SESSION,
        satisfied_gates=frozenset(descriptor.enablement_gates),
    )


def _limit(
    scope: RuntimeBudgetScope,
    *,
    steps: int,
    concurrency: int,
) -> RuntimeBudgetLimit:
    return RuntimeBudgetLimit(
        scope=scope,
        model_call_limit=20,
        token_limit=10_000,
        cost_limit_microunits=1_000_000,
        step_limit=steps,
        time_limit_ms=30_000,
        concurrency_limit=concurrency,
    )


def _account(identity: RuntimeRunIdentity) -> RuntimeBudgetAccount:
    return RuntimeBudgetAccount(
        identity,
        RuntimeGovernanceEnvelope(
            budgets=RuntimeBudgetEnvelope(
                run=_limit(RuntimeBudgetScope.RUN, steps=20, concurrency=4),
                turn=_limit(RuntimeBudgetScope.TURN, steps=20, concurrency=4),
                tool=_limit(RuntimeBudgetScope.TOOL, steps=0, concurrency=0),
                subagent=_limit(
                    RuntimeBudgetScope.SUBAGENT,
                    steps=4,
                    concurrency=1,
                ),
            )
        ),
    )


def _manual_request(
    plan: AgentOrchestrationPlan,
    identity: RuntimeRunIdentity,
    orchestration_id: str,
) -> AgentOrchestrationRequest:
    now = datetime.now(timezone.utc)
    approval = AgentOrchestrationApproval(
        approval_id=f"approval-{orchestration_id}",
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        plan_sha256=plan.content_sha256,
        approved_by="human",
        approved_at=now,
    )
    return AgentOrchestrationRequest(
        orchestration_id=orchestration_id,
        identity=identity,
        plan=plan,
        approval=approval,
        freeze=AgentOrchestrationFreeze(
            freeze_id=f"freeze-{orchestration_id}",
            approval_id=approval.approval_id,
            plan_id=plan.plan_id,
            plan_revision=plan.revision,
            plan_sha256=plan.content_sha256,
            frozen_by="host",
            frozen_at=now,
        ),
        root_input={"topic": "有界编排"},
    )


def test_plan_preview_approve_freeze_requires_exact_latest_proof():
    identity = _identity()
    plan = _plan()
    service = _service()
    preview = service.preview(
        plan,
        identity=identity,
        proposed_by="coordinator",
    )
    assert preview.state is AgentPlanRevisionState.PREVIEWED

    with pytest.raises(AgentOrchestrationError, match="plan_state_conflict"):
        service.freeze(
            plan_id=plan.plan_id,
            revision=1,
            plan_sha256=plan.content_sha256,
            approval_id="not-approved",
            owner=identity.owner,
            frozen_by="host",
            expected_event_sequence=preview.latest_event_sequence,
        )
    with pytest.raises(AgentOrchestrationError, match="plan_digest_conflict"):
        service.approve(
            plan_id=plan.plan_id,
            revision=1,
            plan_sha256="0" * 64,
            owner=identity.owner,
            approved_by="human",
            expected_event_sequence=preview.latest_event_sequence,
        )

    approved = service.approve(
        plan_id=plan.plan_id,
        revision=1,
        plan_sha256=plan.content_sha256,
        owner=identity.owner,
        approved_by="human",
        expected_event_sequence=preview.latest_event_sequence,
    )
    assert approved is not None and approved.approval is not None
    frozen = service.freeze(
        plan_id=plan.plan_id,
        revision=1,
        plan_sha256=plan.content_sha256,
        approval_id=approved.approval.approval_id,
        owner=identity.owner,
        frozen_by="host",
        expected_event_sequence=approved.latest_event_sequence,
    )
    assert frozen is not None
    assert frozen.state is AgentPlanRevisionState.FROZEN
    assert frozen.freeze is not None
    request = AgentOrchestrationRequest(
        orchestration_id="orch-governed",
        identity=identity,
        plan=plan,
        approval=frozen.approval,
        freeze=frozen.freeze,
        root_input={"topic": "计划治理"},
    )
    assert service.require_frozen(request) == frozen


def test_approved_plan_change_creates_append_only_revision_and_audit_event():
    identity = _identity()
    repository = InMemoryAgentPlanRepository()
    service = _service(repository)
    first = _approve_and_freeze(service, _plan(), identity)
    second_plan = _plan(revision=2, worker_description="修复后的调查任务")
    second = _approve_and_freeze(
        service,
        second_plan,
        identity,
        repair_reason_code="review_failed",
    )

    assert first.record.plan.content_sha256 != second.record.plan.content_sha256
    assert second.record.repair.changed_task_ids == ("research",)
    assert second.state is AgentPlanRevisionState.FROZEN
    old = service.get_revision("plan-governed", 1, owner=identity.owner)
    assert old is not None
    assert old.state is AgentPlanRevisionState.SUPERSEDED
    events = repository.list_events(
        "plan-governed",
        owner_id=identity.owner.canonical_id,
    )
    assert [event.kind for event in events] == [
        AgentPlanEventKind.PREVIEWED,
        AgentPlanEventKind.APPROVED,
        AgentPlanEventKind.FROZEN,
        AgentPlanEventKind.PREVIEWED,
        AgentPlanEventKind.APPROVED,
        AgentPlanEventKind.REVISION_SUPERSEDED,
        AgentPlanEventKind.FROZEN,
    ]
    assert [event.sequence for event in events] == list(range(1, 8))
    assert all(
        event.previous_event_sha256 == events[index - 1].event_sha256
        for index, event in enumerate(events[1:], start=1)
    )


def test_approved_but_not_frozen_revision_is_superseded_by_new_freeze():
    identity = _identity("run-approved-repair")
    repository = InMemoryAgentPlanRepository()
    service = _service(repository)
    first_plan = _plan()
    preview = service.preview(
        first_plan,
        identity=identity,
        proposed_by="coordinator",
    )
    first = service.approve(
        plan_id=first_plan.plan_id,
        revision=1,
        plan_sha256=first_plan.content_sha256,
        owner=identity.owner,
        approved_by="human",
        expected_event_sequence=preview.latest_event_sequence,
    )
    assert first.state is AgentPlanRevisionState.APPROVED

    _approve_and_freeze(
        service,
        _plan(revision=2, worker_description="批准后修订"),
        identity,
        repair_reason_code="approval_review_changed",
    )

    superseded = service.get_revision(
        first_plan.plan_id,
        1,
        owner=identity.owner,
    )
    assert superseded is not None
    assert superseded.state is AgentPlanRevisionState.SUPERSEDED
    assert superseded.approval is not None
    assert superseded.freeze is None
    assert [
        event.kind
        for event in repository.list_events(
            first_plan.plan_id,
            owner_id=identity.owner.canonical_id,
        )
    ] == [
        AgentPlanEventKind.PREVIEWED,
        AgentPlanEventKind.APPROVED,
        AgentPlanEventKind.PREVIEWED,
        AgentPlanEventKind.APPROVED,
        AgentPlanEventKind.REVISION_SUPERSEDED,
        AgentPlanEventKind.FROZEN,
    ]


def test_plan_repair_cannot_expand_budget_or_approve_stale_revision():
    identity = _identity()
    service = _service()
    first = _approve_and_freeze(service, _plan(max_tokens=100), identity)
    expanded = _plan(
        revision=2,
        worker_description="修改",
        max_tokens=101,
    )
    with pytest.raises(
        AgentOrchestrationError,
        match="plan_repair_budget_expanded",
    ):
        service.preview(
            expanded,
            identity=identity,
            proposed_by="coordinator",
            repair_reason_code="retry",
        )

    with pytest.raises(AgentOrchestrationError, match="plan_repair_no_change"):
        service.preview(
            _plan(revision=2, max_tokens=100),
            identity=identity,
            proposed_by="coordinator",
            repair_reason_code="retry",
        )

    second = service.preview(
        _plan(revision=2, worker_description="修改", max_tokens=100),
        identity=identity,
        proposed_by="coordinator",
        repair_reason_code="retry",
    )
    with pytest.raises(AgentOrchestrationError, match="plan_revision_stale"):
        service.approve(
            plan_id="plan-governed",
            revision=1,
            plan_sha256=first.record.plan.content_sha256,
            owner=identity.owner,
            approved_by="human",
            expected_event_sequence=second.latest_event_sequence,
        )


def test_plan_governance_is_owner_isolated():
    service = _service()
    identity = _identity(owner_id="owner-a")
    _approve_and_freeze(service, _plan(), identity)
    assert service.get_revision(
        "plan-governed",
        1,
        owner=_identity(owner_id="owner-b").owner,
    ) is None


def test_retry_policy_rejects_implicit_or_governance_retries():
    for error_code in (
        "runtime_budget_exceeded",
        "child_model_scope_denied",
        "child_runtime_stop_unconfirmed",
    ):
        with pytest.raises(ValueError, match="不可重试"):
            AgentTaskRetryPolicy(
                max_attempts=2,
                retryable_error_codes=(error_code,),
                backoff_ms=(0,),
                idempotency_key="invalid-retry",
            )
    with pytest.raises(ValueError, match="idempotency_key"):
        AgentTaskRetryPolicy(
            max_attempts=2,
            retryable_error_codes=("task_executor_failed",),
            backoff_ms=(0,),
        )


@pytest.mark.asyncio
async def test_local_retry_is_explicit_idempotent_and_barrier_checkpointed():
    retry = AgentTaskRetryPolicy(
        max_attempts=2,
        retryable_error_codes=("task_executor_failed",),
        backoff_ms=(0,),
        idempotency_key="research-retry-key",
    )
    plan = _plan(retry=retry)
    assert [barrier.task_ids for barrier in plan.execution_barriers()] == [
        ("research",),
        ("aggregate",),
    ]
    assert plan.execution_barriers() == plan.execution_barriers()

    class Executor:
        def __init__(self) -> None:
            self.contexts = []

        async def execute(self, context):
            self.contexts.append(context)
            if context.task.task_id == "research" and context.attempt_no == 1:
                raise RuntimeError("第一次失败")
            if context.task.task_id == "research":
                return AgentTaskOutput(
                    AgentTaskOutputStatus.SUCCESS,
                    "重试成功",
                    data={"finding": "证据"},
                )
            return AgentTaskOutput(
                AgentTaskOutputStatus.SUCCESS,
                "聚合完成",
                data={"answer": context.inputs["finding"]},
            )

    identity = _identity("run-local-retry")
    store = InMemoryAgentOrchestrationCheckpointStore()
    executor = Executor()
    result = await AgentDagOrchestrator(
        executor=executor,
        checkpoint_store=store,
        budget_account=_account(identity),
    ).execute(
        _manual_request(plan, identity, "orch-local-retry"),
        feature_decision=_feature_decision(),
    )

    assert result.state is AgentOrchestrationState.SUCCEEDED
    assert [
        (receipt.task_id, receipt.attempt_no, receipt.state.value)
        for receipt in result.receipts
    ] == [
        ("research", 1, "failed"),
        ("research", 2, "succeeded"),
        ("aggregate", 1, "succeeded"),
    ]
    retry_context = executor.contexts[1]
    assert retry_context.attempt_no == 2
    assert [item.error_code for item in retry_context.previous_attempts] == [
        "task_executor_failed"
    ]
    latest = await store.load_latest(
        "orch-local-retry",
        owner_id=identity.owner.canonical_id,
    )
    assert latest is not None
    assert latest.sequence == 2
    assert latest.plan_id == plan.plan_id
    assert latest.plan_revision == plan.revision
    assert latest.freeze_id == "freeze-orch-local-retry"
    assert latest.cumulative_usage.task_attempts == 3
    assert latest.receipt_sha256s == tuple(
        receipt.receipt_sha256 for receipt in result.receipts
    )


@pytest.mark.asyncio
async def test_interrupted_barrier_requires_append_only_plan_repair():
    class Store:
        def __init__(self) -> None:
            self.inner = InMemoryAgentOrchestrationCheckpointStore()

        async def save(self, checkpoint):
            if checkpoint.sequence == 2:
                raise RuntimeError("模拟第二个屏障提交失败")
            return await self.inner.save(checkpoint)

        async def load_latest(self, orchestration_id, *, owner_id):
            return await self.inner.load_latest(
                orchestration_id,
                owner_id=owner_id,
            )

    class Executor:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, context):
            self.calls += 1
            if context.task.task_id == "research":
                return AgentTaskOutput(
                    AgentTaskOutputStatus.SUCCESS,
                    "调查完成",
                    data={"finding": "已执行但只持久化第一屏障"},
                )
            return AgentTaskOutput(
                AgentTaskOutputStatus.SUCCESS,
                "聚合完成",
                data={"answer": context.inputs["finding"]},
            )

    identity = _identity("run-interrupted-barrier")
    store = Store()
    executor = Executor()
    orchestrator = AgentDagOrchestrator(
        executor=executor,
        checkpoint_store=store,
        budget_account=_account(identity),
    )
    request = _manual_request(
        _plan(),
        identity,
        "orch-interrupted-barrier",
    )

    first = await orchestrator.execute(
        request,
        feature_decision=_feature_decision(),
    )
    assert first.state is AgentOrchestrationState.FAILED
    assert first.failure_code == "checkpoint_store_failed"
    assert executor.calls == 2
    with pytest.raises(AgentOrchestrationError, match="plan_repair_required"):
        await orchestrator.execute(
            request,
            feature_decision=_feature_decision(),
        )
    assert executor.calls == 2


@pytest.mark.asyncio
async def test_governed_orchestrator_rejects_unpersisted_freeze_before_execution():
    class Executor:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, context):
            self.calls += 1
            if context.task.task_id == "research":
                return AgentTaskOutput(
                    AgentTaskOutputStatus.SUCCESS,
                    "调查完成",
                    data={"finding": "持久冻结计划"},
                )
            return AgentTaskOutput(
                AgentTaskOutputStatus.SUCCESS,
                "聚合完成",
                data={"answer": context.inputs["finding"]},
            )

    identity = _identity("run-governed-execute")
    plan = _plan()
    service = _service()
    frozen = _approve_and_freeze(service, plan, identity)
    executor = Executor()
    governed = GovernedAgentDagOrchestrator(
        orchestrator=AgentDagOrchestrator(
            executor=executor,
            checkpoint_store=InMemoryAgentOrchestrationCheckpointStore(),
            budget_account=_account(identity),
        ),
        governance=service,
    )
    with pytest.raises(AgentOrchestrationError, match="plan_not_frozen"):
        await governed.execute(
            _manual_request(plan, identity, "orch-governed-unpersisted"),
            feature_decision=_feature_decision(),
        )
    assert executor.calls == 0

    request = AgentOrchestrationRequest(
        orchestration_id="orch-governed-persisted",
        identity=identity,
        plan=plan,
        approval=frozen.approval,
        freeze=frozen.freeze,
        root_input={"topic": "持久治理入口"},
    )
    result = await governed.execute(
        request,
        feature_decision=_feature_decision(),
    )
    assert result.state is AgentOrchestrationState.SUCCEEDED
    assert executor.calls == 2


def test_plan_and_checkpoint_serialization_are_strict_and_reversible():
    plan = _plan()
    restored = decode_agent_orchestration_plan(
        encode_agent_orchestration_plan(plan)
    )
    assert restored == plan
    payload = plan.to_dict()
    payload["unknown"] = True
    with pytest.raises(ValueError, match="unknown"):
        decode_agent_orchestration_plan(
            json.dumps(payload, ensure_ascii=False)
        )


def test_sql_plan_repository_survives_new_session(db_session):
    factory = session_factory_from_session(db_session)
    identity = _identity("run-sql-plan")
    service = _service(SqlAlchemyAgentPlanRepository(db_session))
    frozen = _approve_and_freeze(service, _plan(), identity)
    db_session.commit()

    fresh = factory()
    try:
        loaded = _service(
            SqlAlchemyAgentPlanRepository(fresh)
        ).get_revision("plan-governed", 1, owner=identity.owner)
        assert loaded is not None
        assert loaded.state is AgentPlanRevisionState.FROZEN
        assert loaded.record.plan == frozen.record.plan
        assert loaded.approval == frozen.approval
        assert loaded.freeze == frozen.freeze
    finally:
        fresh.close()


def test_sql_plan_event_conflict_rolls_back_only_failed_append(db_session):
    identity = _identity("run-sql-event-conflict")
    service = AgentPlanGovernanceService(
        SqlAlchemyAgentPlanRepository(db_session),
        now=lambda: datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
        id_factory=lambda _prefix: "deliberately-duplicate-id",
    )
    plan = _plan()
    preview = service.preview(
        plan,
        identity=identity,
        proposed_by="coordinator",
    )

    with pytest.raises(AgentOrchestrationError, match="plan_event_conflict"):
        service.approve(
            plan_id=plan.plan_id,
            revision=plan.revision,
            plan_sha256=plan.content_sha256,
            owner=identity.owner,
            approved_by="human",
            expected_event_sequence=preview.latest_event_sequence,
        )

    loaded = service.get_revision(
        plan.plan_id,
        plan.revision,
        owner=identity.owner,
    )
    assert loaded is not None
    assert loaded.state is AgentPlanRevisionState.PREVIEWED


@pytest.mark.asyncio
async def test_sql_checkpoint_store_replays_only_fully_persisted_terminal_result(
    db_session,
):
    identity = _identity("run-sql-checkpoint")
    factory = session_factory_from_session(db_session)
    store = SqlAlchemyAgentOrchestrationCheckpointStore(factory)

    class Executor:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, context):
            self.calls += 1
            if context.task.task_id == "research":
                return AgentTaskOutput(
                    AgentTaskOutputStatus.SUCCESS,
                    "调查完成",
                    data={"finding": "持久结果"},
                )
            return AgentTaskOutput(
                AgentTaskOutputStatus.SUCCESS,
                "聚合完成",
                data={"answer": context.inputs["finding"]},
            )

    executor = Executor()
    request = _manual_request(_plan(), identity, "orch-sql-checkpoint")
    orchestrator = AgentDagOrchestrator(
        executor=executor,
        checkpoint_store=store,
        budget_account=_account(identity),
    )
    first = await orchestrator.execute(
        request,
        feature_decision=_feature_decision(),
    )
    second = await orchestrator.execute(
        request,
        feature_decision=_feature_decision(),
    )
    assert first == second
    assert executor.calls == 2
    encoded = encode_agent_orchestration_checkpoint(
        await store.load_latest(
            request.orchestration_id,
            owner_id=identity.owner.canonical_id,
        )
    )
    assert decode_agent_orchestration_checkpoint(encoded).sequence == 2
    tampered = json.loads(encoded)
    tampered["outputs"]["aggregate"]["summary"] = "篡改后的汇总"
    with pytest.raises(ValueError, match="receipt|state_sha256"):
        decode_agent_orchestration_checkpoint(
            json.dumps(tampered, ensure_ascii=False)
        )
    with pytest.raises(
        AgentOrchestrationError,
        match="checkpoint_owner_conflict",
    ):
        await store.load_latest(
            request.orchestration_id,
            owner_id="qq:user:foreign",
        )


def test_sqlite_migration_makes_plan_facts_append_only(tmp_path):
    database_path = tmp_path / "orchestration.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        _agent_orchestration_governance_v1(connection, engine, str(database_path))
        trigger_names = tuple(connection.execute(text(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'trigger' "
            "AND name LIKE 'trg_agent_orchestration_%' "
            "ORDER BY name"
        )).scalars())
        assert trigger_names == (
            "trg_agent_orchestration_checkpoints_no_delete",
            "trg_agent_orchestration_checkpoints_no_update",
            "trg_agent_orchestration_plan_events_no_delete",
            "trg_agent_orchestration_plan_events_no_update",
            "trg_agent_orchestration_plan_revisions_no_delete",
            "trg_agent_orchestration_plan_revisions_no_update",
        )
        connection.execute(text(
            "INSERT INTO agent_orchestration_plan_revisions ("
            "preview_id, owner_platform, owner_type, owner_id, plan_id, "
            "revision, plan_sha256, plan_json, size_bytes, source_run_id, "
            "source_turn_id, proposed_by, proposed_at"
            ") VALUES ("
            "'preview-1', 'qq', 'user', 'u1', 'plan-1', 1, :sha, '{}', "
            "2, 'run-1', 'turn-1', 'actor-1', CURRENT_TIMESTAMP"
            ")"
        ), {"sha": "a" * 64})
    with engine.begin() as connection, pytest.raises(
        DatabaseError,
        match="append_only",
    ):
        connection.execute(text(
            "UPDATE agent_orchestration_plan_revisions "
            "SET proposed_by = 'other' WHERE preview_id = 'preview-1'"
        ))
    with engine.begin() as connection, pytest.raises(
        DatabaseError,
        match="append_only",
    ):
        connection.execute(text(
            "DELETE FROM agent_orchestration_plan_revisions "
            "WHERE preview_id = 'preview-1'"
        ))
    engine.dispose()
