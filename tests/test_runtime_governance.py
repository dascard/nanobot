from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from kohakuterrarium.modules.plugin.base import PluginBlockError
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from core.agent_runtime import (
    AgentRuntimeBudgetExceededError,
    AgentTurnRequest,
    RequestRuntimeContext,
    RuntimeAccessEnvelope,
    RuntimeAccessGrant,
    RuntimeAccessKind,
    RuntimeActor,
    RuntimeActorType,
    RuntimeBudgetDecisionOutcome,
    RuntimeBudgetEnvelope,
    RuntimeBudgetLimit,
    RuntimeBudgetManager,
    RuntimeBudgetScope,
    RuntimeChatType,
    RuntimeGovernanceEnvelope,
    RuntimeModelRoute,
    RuntimeOwnerType,
    RuntimePermissionDecision,
    RuntimePermissionOutcome,
    RuntimePermissionRequest,
    RuntimePermissionRisk,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
    RuntimeRunIdentity,
    RuntimeUsage,
    estimate_model_input_tokens,
)
from core.db.models.permission import PermissionSessionGrantRow
from core.permissions import (
    RuntimePermissionRevocationRequest,
    SqlAlchemySessionPermissionPort,
)
from core.run_ledger.contracts import (
    RunLedgerAuthorityError,
    RunLedgerEventDraft,
    RunLedgerIdentity,
)
from core.run_ledger.persistence import (
    SqlAlchemyRunEventLedger,
    SqlAlchemyRunEventLedgerWriter,
)
from core.run_ledger.sinks import SqlAlchemyRuntimeBudgetDecisionSink
from core.schema_migrations import run_schema_migrations
from core.telemetry.contracts import TelemetryCorrelation
from nanobot_kt.runtime_context_adapter import (
    build_request_runtime_context,
    build_request_runtime_governance,
)
from nanobot_kt.tool_runtime import (
    PermissionGuardPlugin,
    RuntimeBudgetGuardPlugin,
)


def _identity(
    *,
    run_id: str = "run-governance",
    turn_id: str = "turn-governance",
    owner_id: str = "owner-1",
) -> RuntimeRunIdentity:
    owner = RuntimePrincipal("qq", RuntimeOwnerType.USER, owner_id)
    return RuntimeRunIdentity(
        run_id=run_id,
        turn_id=turn_id,
        correlation_id=f"trace:{run_id}",
        actor=RuntimeActor(RuntimeActorType.USER, owner_id),
        owner=owner,
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
        scope=scope,
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
    model_calls: int = 2,
    tokens: int = 20,
    cost: int = 100,
    run_steps: int = 4,
    tool_steps: int = 2,
    tool_name: str = "reply",
) -> RuntimeGovernanceEnvelope:
    return RuntimeGovernanceEnvelope(
        budgets=RuntimeBudgetEnvelope(
            run=_limit(
                RuntimeBudgetScope.RUN,
                model_calls=model_calls,
                tokens=tokens,
                cost=cost,
                steps=run_steps,
                time_ms=30_000,
                concurrency=1,
                models=("model-a",),
            ),
            turn=_limit(
                RuntimeBudgetScope.TURN,
                model_calls=model_calls,
                tokens=tokens,
                cost=cost,
                steps=run_steps,
                time_ms=20_000,
                concurrency=1,
                models=("model-a",),
            ),
            tool=_limit(
                RuntimeBudgetScope.TOOL,
                model_calls=0,
                tokens=0,
                cost=0,
                steps=tool_steps,
                time_ms=5_000,
                concurrency=1,
            ),
            subagent=_limit(
                RuntimeBudgetScope.SUBAGENT,
                model_calls=0,
                tokens=0,
                cost=0,
                steps=0,
                time_ms=0,
                concurrency=0,
            ),
        ),
        access=RuntimeAccessEnvelope((
            RuntimeAccessGrant(
                RuntimeAccessKind.TOOL,
                f"tool:{tool_name}",
                ("execute",),
                "tool_plan",
            ),
        )),
    )


def _admit(factory, identity: RuntimeRunIdentity) -> None:
    SqlAlchemyRunEventLedgerWriter(factory).append(RunLedgerEventDraft(
        event_id=f"accepted:{identity.run_id}",
        run_id=identity.run_id,
        event_type="run.accepted",
        occurred_at=datetime.now(timezone.utc),
        source="test.runtime_governance",
        correlation=TelemetryCorrelation(
            request_id=identity.turn_id,
            session_id="session-1",
            turn_id=identity.turn_id,
            trace_id=identity.correlation_id,
            run_id=identity.run_id,
        ),
        identity=RunLedgerIdentity(
            actor_type=identity.actor.actor_type.value,
            actor_id=identity.actor.actor_id,
            owner_platform=identity.owner.platform,
            owner_type=identity.owner.owner_type.value,
            owner_id=identity.owner.owner_id,
        ),
        status="accepted",
    ))


def test_runtime_governance_contract_is_exact_hashed_and_monotonic():
    governance = _governance()

    assert len(governance.content_sha256) == 64
    assert governance.bind_model("model-a").content_sha256 == (
        governance.content_sha256
    )
    assert governance.access.find(
        RuntimeAccessKind.TOOL,
        "tool:reply",
        "execute",
    ) is not None
    assert governance.access.find(
        RuntimeAccessKind.TOOL,
        "tool:unknown",
        "execute",
    ) is None
    with pytest.raises(ValueError, match="通配符"):
        RuntimeAccessGrant(
            RuntimeAccessKind.FILE,
            "workspace:*",
            ("read",),
            "policy",
        )
    with pytest.raises(ValueError, match="标识符序列"):
        _limit(
            RuntimeBudgetScope.RUN,
            model_calls=1,
            tokens=1,
            cost=1,
            steps=1,
            time_ms=1,
            concurrency=1,
            models="model-a",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="不能超过 run 上限"):
        RuntimeBudgetEnvelope(
            run=_limit(
                RuntimeBudgetScope.RUN,
                model_calls=1,
                tokens=1,
                cost=1,
                steps=1,
                time_ms=1,
                concurrency=1,
            ),
            turn=_limit(
                RuntimeBudgetScope.TURN,
                model_calls=2,
                tokens=1,
                cost=1,
                steps=1,
                time_ms=1,
                concurrency=1,
            ),
        )


class _ToolPlan:
    sha256 = "b" * 64
    executable_tool_names = frozenset({
        "memory_query",
        "web_search",
        "workspace_read",
    })


def test_production_request_context_declares_every_access_scope_exactly():
    skill_plan = RuntimePlanRef(
        RuntimePlanKind.SKILL,
        "skill-lock:research",
        "c" * 64,
    )
    context = build_request_runtime_context(
        request_id="request-1",
        agent_id="nanobot",
        platform="qq",
        user_id="user-1",
        group_id="",
        session_id="session-1",
        is_group=False,
        is_super_user=False,
        trace_id="trace-1",
        run_id="run-1",
        turn_id="turn-1",
        correlation_id="correlation-1",
        message_id="message-1",
        capabilities={"supports_stream": True},
        prompt_key="chat.default",
        prompt_sha256="a" * 64,
        tool_plan=_ToolPlan(),
        skill_plan=skill_plan,
    )
    access = context.governance.access

    assert access.find(
        RuntimeAccessKind.TOOL,
        "tool:workspace_read",
        "execute",
    ).authorization == "sandbox_session_grant"
    assert access.find(
        RuntimeAccessKind.FILE,
        "workspace:current",
        "read",
    ).authorization == "sandbox_session_grant"
    assert access.find(
        RuntimeAccessKind.NETWORK,
        "controlled-provider:web_search",
        "request",
    ).authorization == "service_policy"
    assert access.find(
        RuntimeAccessKind.MEMORY,
        "memory:session",
        "read",
    ).authorization == "memory_policy"
    assert access.find(
        RuntimeAccessKind.SKILL,
        skill_plan.identity,
        "load",
    ).authorization == "skill_lock"
    assert all(grant.resource != "tool-plan:resolved" for grant in access.grants)
    assert all("*" not in grant.resource for grant in access.grants)


def test_mcp_snapshot_declares_exact_tool_and_server_access(monkeypatch):
    descriptor = SimpleNamespace(
        wire_name="mcp.docs.search",
        server_id="docs",
    )
    disabled_descriptor = SimpleNamespace(
        wire_name="mcp.admin.write",
        server_id="admin",
    )
    monkeypatch.setattr(
        "core.mcp.get_current_mcp_runtime",
        lambda: SimpleNamespace(
            snapshot=SimpleNamespace(
                tools=(descriptor, disabled_descriptor),
            ),
        ),
    )
    tool_plan = SimpleNamespace(
        executable_tool_names=frozenset({descriptor.wire_name}),
    )

    governance = build_request_runtime_governance(
        tool_plan=tool_plan,
        skill_plan=None,
    )

    assert governance.access.find(
        RuntimeAccessKind.TOOL,
        "tool:mcp.docs.search",
        "execute",
    ).authorization == "mcp_snapshot"
    assert governance.access.find(
        RuntimeAccessKind.MCP,
        "mcp-server:docs",
        "call",
    ).authorization == "mcp_snapshot"
    assert governance.access.find(
        RuntimeAccessKind.TOOL,
        "tool:mcp.admin.write",
        "execute",
    ) is None
    assert governance.access.find(
        RuntimeAccessKind.MCP,
        "mcp-server:admin",
        "call",
    ) is None


def test_budget_manager_enforces_attempt_usage_access_concurrency_and_subagent():
    decisions = []

    class Sink:
        def emit(self, decision):
            decisions.append(decision)

    account = RuntimeBudgetManager(sink=Sink()).bind(
        _identity(),
        _governance(),
    )
    account.reserve_model("model-a")
    account.record_usage(RuntimeUsage(
        input_tokens=4,
        output_tokens=3,
        cost_microunits=20,
    ))
    reservation = account.reserve_tool("reply")
    with pytest.raises(
        AgentRuntimeBudgetExceededError,
        match="concurrency_limit",
    ):
        account.reserve_tool("reply")
    account.release(reservation)
    with pytest.raises(
        AgentRuntimeBudgetExceededError,
        match="tool_access_scope_denied",
    ):
        account.reserve_tool("unknown")
    account.reserve_model("model-a")
    with pytest.raises(
        AgentRuntimeBudgetExceededError,
        match="model_call_limit",
    ):
        account.reserve_model("model-a")
    with pytest.raises(
        AgentRuntimeBudgetExceededError,
        match="step_limit",
    ):
        account.reserve_subagent("worker")

    assert decisions[0].operation == "declared"
    assert any(
        item.outcome is RuntimeBudgetDecisionOutcome.DENY
        for item in decisions
    )


@pytest.mark.parametrize(
    ("usage", "reason"),
    (
        (RuntimeUsage(input_tokens=21), "token_limit"),
        (RuntimeUsage(cost_microunits=101), "cost_limit_microunits"),
    ),
)
def test_budget_manager_denies_actual_token_and_cost_overrun(usage, reason):
    account = RuntimeBudgetManager().bind(_identity(), _governance())
    account.reserve_model("model-a")

    with pytest.raises(AgentRuntimeBudgetExceededError, match=reason):
        account.record_usage(usage)


def test_budget_manager_reserves_pessimistic_model_usage_and_refunds_actual():
    account = RuntimeBudgetManager().bind(
        _identity(),
        _governance(tokens=20, cost=100),
    )

    reservation = account.reserve_model_attempt(
        "model-a",
        input_tokens=4,
        max_output_tokens=10,
        cost_input_1m=2.0,
        cost_output_1m=5.0,
    )

    assert reservation.max_output_tokens == 10
    assert reservation.reserved_tokens == 14
    assert reservation.reserved_cost_microunits == 58
    reserved = account.consumption(RuntimeBudgetScope.TURN)
    assert (reserved.model_calls, reserved.tokens, reserved.cost_microunits) == (
        1,
        14,
        58,
    )

    settled = account.settle_model_attempt(
        reservation,
        RuntimeUsage(input_tokens=4, output_tokens=3),
    )

    assert settled == RuntimeUsage(
        input_tokens=4,
        output_tokens=3,
        cost_microunits=23,
    )
    actual = account.consumption(RuntimeBudgetScope.TURN)
    assert (actual.model_calls, actual.tokens, actual.cost_microunits) == (
        1,
        7,
        23,
    )


def test_budget_manager_clamps_output_to_remaining_token_and_cost_budget():
    account = RuntimeBudgetManager().bind(
        _identity(),
        _governance(tokens=20, cost=30),
    )

    reservation = account.reserve_model_attempt(
        "model-a",
        input_tokens=4,
        max_output_tokens=10,
        cost_input_1m=2.0,
        cost_output_1m=5.0,
    )

    assert reservation.max_output_tokens == 4
    assert reservation.reserved_tokens == 8
    assert reservation.reserved_cost_microunits == 28


def test_budget_manager_denies_unknown_model_pricing_before_attempt():
    account = RuntimeBudgetManager().bind(_identity(), _governance())

    with pytest.raises(
        AgentRuntimeBudgetExceededError,
        match="model_pricing_unknown",
    ):
        account.reserve_model_attempt(
            "model-a",
            input_tokens=4,
            max_output_tokens=10,
            cost_input_1m=None,
            cost_output_1m=None,
        )

    assert account.consumption(RuntimeBudgetScope.TURN).model_calls == 0


def test_budget_manager_keeps_pessimistic_charge_when_outcome_is_unknown():
    decisions = []

    class Sink:
        def emit(self, decision):
            decisions.append(decision)

    account = RuntimeBudgetManager(sink=Sink()).bind(
        _identity(),
        _governance(tokens=20, cost=100),
    )
    reservation = account.reserve_model_attempt(
        "model-a",
        input_tokens=4,
        max_output_tokens=10,
        cost_input_1m=2.0,
        cost_output_1m=5.0,
    )

    account.charge_model_attempt_unknown(
        reservation,
        reason="provider_timeout",
    )

    charged = account.consumption(RuntimeBudgetScope.TURN)
    assert (charged.tokens, charged.cost_microunits) == (14, 58)
    assert [item.reason for item in decisions[-2:]] == [
        "charged_unknown",
        "charged_unknown",
    ]


def test_model_input_estimate_uses_conservative_utf8_upper_bound():
    messages = [{"role": "user", "content": "测试 ascii"}]
    tools = [{"type": "function", "function": {"name": "lookup"}}]

    estimated = estimate_model_input_tokens(messages, tools)

    assert estimated >= len(
        json.dumps(
            {"messages": messages, "tools": tools},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def test_budget_manager_denies_model_scope_and_elapsed_time():
    clock = [0.0]
    manager = RuntimeBudgetManager(monotonic=lambda: clock[0])
    account = manager.bind(_identity(), _governance())

    with pytest.raises(AgentRuntimeBudgetExceededError, match="model_scope_denied"):
        account.reserve_model("model-b")
    clock[0] = 30.001
    with pytest.raises(AgentRuntimeBudgetExceededError, match="time_limit_exhausted"):
        account.remaining_time_seconds()


def test_budget_manager_rejects_access_expansion_within_same_run():
    identity = _identity()
    manager = RuntimeBudgetManager()
    manager.bind(identity, _governance(tool_name="reply"))

    with pytest.raises(AgentRuntimeBudgetExceededError, match="access_scope_changed"):
        manager.bind(identity, _governance(tool_name="workspace_write"))


@pytest.mark.asyncio
async def test_kt_guards_block_and_defer_original_authority_or_budget_failure():
    identity = _identity()
    governance = _governance(model_calls=1)
    context = RequestRuntimeContext(
        request_id="request-kt-governance",
        agent_id="test.agent",
        principal=identity.owner,
        session_id="session-1",
        chat_type=RuntimeChatType.PRIVATE,
        trace_id=identity.correlation_id,
        run_id=identity.run_id,
        turn_id=identity.turn_id,
        correlation_id=identity.correlation_id,
        actor=identity.actor,
        governance=governance,
    )
    request = AgentTurnRequest(context, "执行")

    class FailingPermissionPort:
        async def evaluate(self, _request):
            raise RunLedgerAuthorityError(
                "账本不可写",
                run_id=identity.run_id,
                event_type="permission.decided",
            )

    permission = PermissionGuardPlugin(
        FailingPermissionPort(),
        lambda: request,
    )
    permission_token = permission.begin_turn()
    with pytest.raises(PluginBlockError, match="runtime_permission_denied"):
        await asyncio.create_task(
            permission.pre_tool_execute(
                {},
                tool_name="reply",
                job_id="call-1",
            )
        )
    with pytest.raises(RunLedgerAuthorityError, match="账本不可写"):
        permission.raise_deferred_failure()
    permission.end_turn(permission_token)

    account = RuntimeBudgetManager().bind(identity, governance)
    budget = RuntimeBudgetGuardPlugin(
        lambda: account,
        lambda: request,
        route_provider=lambda: RuntimeModelRoute(
            route_id="test/free",
            model_id="model-a",
            provider_id="test",
            max_tokens=16,
            cost_input_1m=0.0,
            cost_output_1m=0.0,
        ),
    )
    budget_token = budget.begin_turn()
    await asyncio.create_task(budget.pre_llm_call([], model="model-a"))
    with pytest.raises(PluginBlockError, match="runtime_model_budget_denied"):
        await asyncio.create_task(budget.pre_llm_call([], model="model-a"))
    with pytest.raises(AgentRuntimeBudgetExceededError, match="model_call_limit"):
        budget.raise_deferred_failure()
    budget.end_turn(budget_token)

    tool_account = RuntimeBudgetManager().bind(identity, _governance())
    tool_budget = RuntimeBudgetGuardPlugin(
        lambda: tool_account,
        lambda: request,
    )
    tool_token = tool_budget.begin_turn()
    await tool_budget.pre_tool_execute({}, tool_name="reply", job_id="job-1")
    with pytest.raises(PluginBlockError, match="runtime_tool_budget_denied"):
        await tool_budget.pre_tool_execute(
            {},
            tool_name="reply",
            job_id="job-2",
        )
    with pytest.raises(AgentRuntimeBudgetExceededError, match="concurrency_limit"):
        tool_budget.raise_deferred_failure()
    await tool_budget.post_tool_execute(
        "完成",
        tool_name="reply",
        job_id="job-1",
    )
    tool_budget.end_turn(tool_token)


@pytest.mark.asyncio
async def test_kt_budget_guard_clamps_before_call_and_refunds_after_usage():
    messages = [{"role": "user", "content": "执行"}]
    input_tokens = estimate_model_input_tokens(messages, ())
    governance = _governance(
        tokens=input_tokens + 4,
        cost=1_000,
    )
    identity = _identity()
    context = RequestRuntimeContext(
        request_id="request-kt-hard-budget",
        agent_id="test.agent",
        principal=identity.owner,
        session_id="session-1",
        chat_type=RuntimeChatType.PRIVATE,
        trace_id=identity.correlation_id,
        run_id=identity.run_id,
        turn_id=identity.turn_id,
        correlation_id=identity.correlation_id,
        actor=identity.actor,
        governance=governance,
    )
    account = RuntimeBudgetManager().bind(identity, governance)
    applied_max_tokens = []
    guard = RuntimeBudgetGuardPlugin(
        lambda: account,
        lambda: AgentTurnRequest(context, "执行"),
        route_provider=lambda: RuntimeModelRoute(
            route_id="reply/kt-budget",
            model_id="model-a",
            provider_id="new-api",
            max_tokens=10,
            cost_input_1m=2.0,
            cost_output_1m=5.0,
        ),
        max_tokens_applier=applied_max_tokens.append,
    )
    token = guard.begin_turn()

    await guard.pre_llm_call(messages, model="model-a", tools=[])

    assert applied_max_tokens == [4]
    reserved = account.consumption(RuntimeBudgetScope.TURN)
    assert reserved.tokens == input_tokens + 4

    await guard.post_llm_call(
        messages,
        "完成",
        {"prompt_tokens": input_tokens, "completion_tokens": 2},
        model="model-a",
    )

    settled = account.consumption(RuntimeBudgetScope.TURN)
    assert settled.tokens == input_tokens + 2
    assert settled.cost_microunits == input_tokens * 2 + 10
    guard.end_turn(token)


def test_budget_decisions_are_authoritative_and_do_not_store_resource_text(
    db_session,
):
    factory = sessionmaker(bind=db_session.get_bind())
    identity = _identity(run_id="run-budget-ledger")
    _admit(factory, identity)
    account = RuntimeBudgetManager(
        sink=SqlAlchemyRuntimeBudgetDecisionSink(factory)
    ).bind(identity, _governance())
    account.reserve_model("model-a")

    with factory() as db:
        records = SqlAlchemyRunEventLedger(db).read(identity.run_id)
    assert [record.event_type for record in records] == [
        "run.accepted",
        "budget.declared",
        "budget.declared",
        "budget.declared",
        "budget.declared",
        "budget.decision_recorded",
        "budget.decision_recorded",
    ]
    assert records[-1].payload["scope"] == "turn"
    assert [
        record.payload["scope"]
        for record in records
        if record.event_type == "budget.declared"
    ] == ["run", "turn", "tool", "subagent"]
    serialized = str([dict(record.payload) for record in records])
    assert "model-a" not in serialized
    assert records[-1].payload["resource_sha256"]


class _GrantThenAskPort:
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(
        self,
        request: RuntimePermissionRequest,
    ) -> RuntimePermissionDecision:
        self.calls += 1
        decided_at = datetime.now(timezone.utc)
        if self.calls == 1:
            return RuntimePermissionDecision(
                decision_id=f"decision:{request.request_id}",
                request_id=request.request_id,
                outcome=RuntimePermissionOutcome.SESSION_GRANT,
                reason="test_session_grant",
                decided_at=decided_at,
                grant_id=f"grant:{request.request_id}",
                grant_expires_at=decided_at + timedelta(minutes=10),
            )
        return RuntimePermissionDecision(
            decision_id=f"decision:{request.request_id}",
            request_id=request.request_id,
            outcome=RuntimePermissionOutcome.ASK,
            reason="test_ask",
            decided_at=decided_at,
        )


@pytest.mark.asyncio
async def test_session_grant_is_exact_replayable_ledgered_and_revocable(db_session):
    factory = sessionmaker(bind=db_session.get_bind())
    identity = _identity(run_id="run-permission-session")
    _admit(factory, identity)
    delegate = _GrantThenAskPort()
    port = SqlAlchemySessionPermissionPort(delegate, factory)
    requested_at = datetime.now(timezone.utc)
    first_request = RuntimePermissionRequest(
        request_id="permission-first",
        identity=identity,
        action="tool.execute",
        resource="tool:workspace_write",
        risk=RuntimePermissionRisk.HIGH,
        requested_at=requested_at,
        session_id="session-1",
    )

    issued = await port.evaluate(first_request)
    assert issued.outcome is RuntimePermissionOutcome.SESSION_GRANT
    assert delegate.calls == 1
    with factory() as db:
        before_replay = len(SqlAlchemyRunEventLedger(db).read(identity.run_id))

    replay_delegate = _GrantThenAskPort()
    replay = await SqlAlchemySessionPermissionPort(
        replay_delegate,
        factory,
    ).evaluate(first_request)
    assert replay.outcome is RuntimePermissionOutcome.SESSION_GRANT
    assert replay.grant_id == issued.grant_id
    assert replay_delegate.calls == 0
    with factory() as db:
        assert len(SqlAlchemyRunEventLedger(db).read(identity.run_id)) == before_replay

    reused = await port.evaluate(RuntimePermissionRequest(
        request_id="permission-reused",
        identity=identity,
        action="tool.execute",
        resource="tool:workspace_write",
        risk=RuntimePermissionRisk.HIGH,
        requested_at=requested_at,
        session_id="session-1",
    ))
    assert reused.outcome is RuntimePermissionOutcome.ALLOW
    assert reused.grant_id == issued.grant_id
    assert delegate.calls == 1

    different_resource = await port.evaluate(RuntimePermissionRequest(
        request_id="permission-other-resource",
        identity=identity,
        action="tool.execute",
        resource="tool:workspace_delete",
        risk=RuntimePermissionRisk.HIGH,
        requested_at=requested_at,
        session_id="session-1",
    ))
    assert different_resource.outcome is RuntimePermissionOutcome.ASK
    assert delegate.calls == 2

    with factory() as db:
        row = db.get(PermissionSessionGrantRow, issued.grant_id)
        assert row is not None
        row.issued_at = datetime.now() - timedelta(hours=2)
        row.expires_at = datetime.now() - timedelta(hours=1)
        db.commit()
    after_expiry = await port.evaluate(RuntimePermissionRequest(
        request_id="permission-after-expiry",
        identity=identity,
        action="tool.execute",
        resource="tool:workspace_write",
        risk=RuntimePermissionRisk.HIGH,
        requested_at=requested_at,
        session_id="session-1",
    ))
    assert after_expiry.outcome is RuntimePermissionOutcome.ASK
    assert delegate.calls == 3

    revoked = await port.revoke(RuntimePermissionRevocationRequest(
        revocation_id="revoke-1",
        grant_id=issued.grant_id,
        identity=identity,
        session_id="session-1",
        revoked_by="admin-1",
        reason="用户撤销",
        revoked_at=datetime.now(timezone.utc),
    ))
    assert revoked.grant_id == issued.grant_id
    after_revoke = await port.evaluate(RuntimePermissionRequest(
        request_id="permission-after-revoke",
        identity=identity,
        action="tool.execute",
        resource="tool:workspace_write",
        risk=RuntimePermissionRisk.HIGH,
        requested_at=requested_at,
        session_id="session-1",
    ))
    assert after_revoke.outcome is RuntimePermissionOutcome.ASK
    assert delegate.calls == 4

    with factory() as db:
        row = db.get(PermissionSessionGrantRow, issued.grant_id)
        assert row is not None
        assert row.active_binding_key is None
        assert row.revocation_id == "revoke-1"
        records = SqlAlchemyRunEventLedger(db).read(identity.run_id)
    event_types = [record.event_type for record in records]
    assert "permission.grant_issued" in event_types
    assert "permission.grant_revoked" in event_types
    serialized = str([dict(record.payload) for record in records])
    assert "tool:workspace_write" not in serialized


def test_permission_grant_schema_migration_is_idempotent_and_indexed():
    from core.schema_migrations import (
        _AGENT_COLLABORATION_V1_VERSION,
        _GATEWAY_SESSION_CONTROL_V1_VERSION,
        _AGENT_ORCHESTRATION_GOVERNANCE_V1_VERSION,
        MIGRATIONS,
        _RUNTIME_PERMISSION_GOVERNANCE_V1_VERSION,
    )

    engine = create_engine("sqlite:///:memory:")
    run_schema_migrations(engine)
    run_schema_migrations(engine)
    inspector = inspect(engine)

    assert "permission_session_grants" in inspector.get_table_names()
    assert {
        "active_binding_key",
        "owner_platform",
        "owner_type",
        "owner_id",
        "session_id",
        "action",
        "resource_sha256",
        "expires_at",
        "revoked_at",
        "revocation_id",
    } <= {
        column["name"]
        for column in inspector.get_columns("permission_session_grants")
    }
    assert {
        "ix_permission_session_grant_scope",
        "ix_permission_session_grant_active_expiry",
    } <= {
        index["name"]
        for index in inspector.get_indexes("permission_session_grants")
    }
    migration_versions = [version for version, _name, _migration in MIGRATIONS]
    assert migration_versions.index(
        _RUNTIME_PERMISSION_GOVERNANCE_V1_VERSION
    ) < migration_versions.index(
        _AGENT_ORCHESTRATION_GOVERNANCE_V1_VERSION
    ) < migration_versions.index(
        _AGENT_COLLABORATION_V1_VERSION
    ) < migration_versions.index(
        _GATEWAY_SESSION_CONTROL_V1_VERSION
    )
