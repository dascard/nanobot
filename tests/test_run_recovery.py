from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

from core.agent_runtime import (
    AgentRuntimeAmbiguousError,
    AgentTurnRequest,
    NativeAgentRuntime,
    RegisteredToolExecutionPort,
    RequestRuntimeContext,
    RuntimeActor,
    RuntimeActorType,
    RuntimeCheckpointBoundary,
    RuntimeCheckpointCapture,
    RuntimeChatType,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimeOwnerType,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
    RuntimeRecoveryOperationKind,
    RuntimeRunIdentity,
    RuntimeSideEffectState,
    RuntimeToolCall,
    RuntimeToolCallStatus,
    RuntimeToolEffectClass,
    RuntimeToolExecutionRequest,
    RuntimeToolExecutionResult,
    RuntimeTurnKind,
    runtime_model_route_sha256,
)
from core.database import (
    AgentRun,
    Asset,
    RunCheckpointRow,
    RunRecoveryOperation,
    RunSideEffectReceipt,
    Workspace,
    WorkspaceAsset,
)
from core.db.base import Base
from core.model_provider.chat_runtime import ChatCompletionRequest
from core.run_ledger import (
    RunLedgerEventDraft,
    RunLedgerIdentity,
    load_authoritative_run_view,
)
from core.run_ledger.adapters import (
    run_status_changed_event,
    run_terminated_event,
)
from core.run_ledger.persistence import SqlAlchemyRunEventLedger
from core.run_recovery import (
    RunRecoveryIntegrityError,
    RunRecoveryPreflightDenied,
    RunRecoveryFileProof,
    SqlAlchemyRunRecoveryService,
    SqlAlchemyRuntimeRecoveryCoordinator,
    build_live_recovery_plans,
)
from core.telemetry.contracts import TelemetryCorrelation
from core.tool_plan import ToolPlan


class _ScriptedCompletionPort:
    def __init__(self, responses: tuple[Mapping[str, Any], ...]) -> None:
        self.responses = deque(dict(item) for item in responses)
        self.requests: list[ChatCompletionRequest] = []

    @property
    def adapter_id(self) -> str:
        return "test:run-recovery"

    async def complete_chat(
        self,
        request: ChatCompletionRequest,
    ) -> Mapping[str, Any]:
        self.requests.append(request)
        return self.responses.popleft()

    async def stream_chat(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[Mapping[str, Any]]:
        self.requests.append(request)
        response = self.responses.popleft()
        message = response["choices"][0]["message"]
        yield {"choices": [{"delta": message}]}


class _FileVerifier:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.proofs: list[RunRecoveryFileProof] = []

    def verify(self, proof: RunRecoveryFileProof) -> bool:
        self.proofs.append(proof)
        return self.valid


def _principal() -> RuntimePrincipal:
    return RuntimePrincipal(
        platform="qq",
        owner_type=RuntimeOwnerType.USER,
        owner_id="10001",
    )


def _identity(run_id: str, *, suffix: str = "source") -> RuntimeRunIdentity:
    return RuntimeRunIdentity(
        run_id=run_id,
        turn_id=f"turn-{suffix}",
        correlation_id=f"trace-{suffix}",
        actor=RuntimeActor(RuntimeActorType.USER, "10001"),
        owner=_principal(),
    )


def _route(model: str = "test-model") -> RuntimeModelRoute:
    return RuntimeModelRoute(
        route_id="reply/current",
        model_id=model,
        provider_id="test-provider",
        profile_id="test-profile",
        temperature=0.2,
        max_tokens=2048,
        timeout_seconds=30.0,
        enable_thinking="false",
    )


def _tool_schema(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"执行 {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _tool_plan(*names: str) -> ToolPlan:
    return ToolPlan.from_effective_tools(
        enabled={name: True for name in names},
        chat_type="private",
        tool_schemas=[_tool_schema(name) for name in names],
    )


def _plans(route: RuntimeModelRoute, plan: ToolPlan) -> tuple[RuntimePlanRef, ...]:
    return (
        RuntimePlanRef(RuntimePlanKind.MANIFEST, "manifest:test", "1" * 64),
        RuntimePlanRef(RuntimePlanKind.PROMPT, "prompt:test", "2" * 64),
        RuntimePlanRef(
            RuntimePlanKind.MODEL,
            "model-route:reply/current",
            runtime_model_route_sha256(route),
        ),
        RuntimePlanRef(RuntimePlanKind.TOOL, "tool-plan:test", plan.sha256),
        RuntimePlanRef(RuntimePlanKind.WORKSPACE, "workspace:none", "3" * 64),
        RuntimePlanRef(RuntimePlanKind.ARTIFACT, "artifact-set:none", "4" * 64),
        RuntimePlanRef(RuntimePlanKind.SECURITY, "security:test", "5" * 64),
    )


def _context(
    run_id: str,
    *,
    route: RuntimeModelRoute,
    plan: ToolPlan,
    suffix: str = "source",
) -> RequestRuntimeContext:
    identity = _identity(run_id, suffix=suffix)
    return RequestRuntimeContext(
        request_id=f"request-{suffix}",
        principal=identity.owner,
        session_id="private_10001",
        chat_type=RuntimeChatType.PRIVATE,
        trace_id=identity.correlation_id,
        run_id=identity.run_id,
        turn_id=identity.turn_id,
        correlation_id=identity.correlation_id,
        actor=identity.actor,
        plans=_plans(route, plan),
    )


def _accepted_event(identity: RuntimeRunIdentity) -> RunLedgerEventDraft:
    return RunLedgerEventDraft(
        event_id=f"accepted:{identity.run_id}",
        run_id=identity.run_id,
        event_type="run.accepted",
        occurred_at=datetime.now(timezone.utc),
        source="test.run_recovery",
        correlation=TelemetryCorrelation(
            request_id=identity.run_id,
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
        payload={
            "run_type": "chat",
            "input_bytes": 0,
            "input_chars": 0,
            "input_sha256": "0" * 64,
        },
    )


def _seed_active_run(db, identity: RuntimeRunIdentity) -> None:
    accepted = _accepted_event(identity)
    ledger = SqlAlchemyRunEventLedger(db)
    ledger.append(accepted, expected_sequence=1)
    ledger.append(run_status_changed_event(
        accepted_event=accepted,
        status="running",
        previous_status="accepted",
    ), expected_sequence=2)
    db.add(AgentRun(
        run_id=identity.run_id,
        trace_id=identity.correlation_id,
        session_id="private_10001",
        user_id="10001",
        chat_type="private",
        group_id="",
        run_type="chat",
        prompt_mode="v2",
        prompt_key="prompt:test",
        prompt_sha256="2" * 64,
        model="test-model",
        status="running",
        started_at=datetime.now(),
    ))
    db.commit()


def _terminate_run(db, identity: RuntimeRunIdentity, status: str = "succeeded") -> None:
    SqlAlchemyRunEventLedger(db).append(run_terminated_event(
        run_id=identity.run_id,
        trace_id=identity.correlation_id,
        session_id="private_10001",
        status=status,
        output_value="",
        error_value="",
        latency_ms=1,
        model="test-model",
    ))
    row = db.get(AgentRun, identity.run_id)
    row.status = status
    row.finished_at = datetime.now()
    db.commit()


def _session_factory(db):
    return sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        expire_on_commit=False,
    )


@pytest.mark.asyncio
async def test_checkpoint_is_authoritative_versioned_and_secret_safe(db_session):
    identity = _identity("run-checkpoint")
    route = _route()
    plan = _tool_plan("reply")
    _seed_active_run(db_session, identity)
    coordinator = SqlAlchemyRuntimeRecoveryCoordinator(
        _session_factory(db_session)
    )

    reference = await coordinator.save_checkpoint(RuntimeCheckpointCapture(
        identity=identity,
        boundary=RuntimeCheckpointBoundary.TURN_STARTED,
        runtime_id="native:test",
        runtime_protocol_version="1.0",
        messages=(RuntimeMessage(
            "user",
            {
                "authorization": "Bearer secret-token-123456789",
                "text": "使用 sk-abcdefghijklmnop 连接",
            },
        ),),
        plans=_plans(route, plan),
        model_route=route,
    ))

    service = SqlAlchemyRunRecoveryService(db_session)
    state = service.load_checkpoint(reference.checkpoint_id, _principal())
    serialized = json.dumps(
        state.messages[0].content,
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "secret-token" not in serialized
    assert "sk-abcdefghijklmnop" not in serialized
    assert "redacted" in serialized
    assert state.reference.resumable is True
    assert state.plan(RuntimePlanKind.MANIFEST).sha256 == "1" * 64
    row = db_session.get(RunCheckpointRow, reference.checkpoint_id)
    record = SqlAlchemyRunEventLedger(db_session).get(
        f"checkpoint:{reference.checkpoint_id}"
    )
    assert row.ledger_event_sha256 == record.event_sha256
    assert record.event_type == "run.checkpoint_saved"
    assert "secret" not in json.dumps(dict(record.payload), ensure_ascii=False)


@pytest.mark.asyncio
async def test_native_runtime_persists_safe_boundaries_and_prepared_receipt_first(
    db_session,
):
    identity = _identity("run-native-recovery")
    route = _route()
    plan = _tool_plan("reply")
    _seed_active_run(db_session, identity)
    factory = _session_factory(db_session)
    coordinator = SqlAlchemyRuntimeRecoveryCoordinator(factory)
    completion = _ScriptedCompletionPort(({
        "choices": [{"message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-reply",
                "type": "function",
                "function": {
                    "name": "reply",
                    "arguments": json.dumps({"text": "完成"}),
                },
            }],
        }}],
    },))
    observed_states: list[str] = []

    async def execute(request: RuntimeToolExecutionRequest):
        check_db = factory()
        try:
            receipt = (
                check_db.query(RunSideEffectReceipt)
                .filter(RunSideEffectReceipt.tool_call_id == "call-reply")
                .one()
            )
            observed_states.append(str(receipt.state))
        finally:
            check_db.close()
        return RuntimeToolExecutionResult(
            tool_call=RuntimeToolCall(
                call_id=request.tool_call.call_id,
                name=request.tool_call.name,
                arguments=request.arguments,
                status=RuntimeToolCallStatus.COMPLETED,
                result={"status": "success", "text": "完成"},
            )
        )

    runtime = NativeAgentRuntime(
        completion,
        RegisteredToolExecutionPort({"tool.reply.execute": execute}),
        runtime_id="native:test",
        tool_plan_resolver=lambda: plan,
        tool_binding_resolver=lambda _name: "tool.reply.execute",
        tool_effect_resolver=lambda _name: RuntimeToolEffectClass.LOCAL_WRITE,
        recovery_port=coordinator,
        available_tool_names=("reply",),
    )
    await runtime.start()
    runtime.set_model_route(route)
    await runtime.run(AgentTurnRequest(
        _context(identity.run_id, route=route, plan=plan),
        "请回复",
    ))

    assert observed_states == ["prepared"]
    checkpoints = (
        db_session.query(RunCheckpointRow)
        .filter(RunCheckpointRow.run_id == identity.run_id)
        .order_by(RunCheckpointRow.sequence.asc())
        .all()
    )
    assert [row.boundary for row in checkpoints] == [
        "turn_started",
        "plan_resolved",
        "tool_ready",
        "tool_completed",
        "turn_completed",
    ]
    assert checkpoints[-1].resumable is True
    receipt = db_session.query(RunSideEffectReceipt).one()
    assert receipt.state == "completed"
    event_types = [
        record.event_type
        for record in load_authoritative_run_view(
            SqlAlchemyRunEventLedger(db_session),
            identity.run_id,
        ).records
    ]
    assert event_types.index("tool.side_effect_prepared") < event_types.index(
        "tool.side_effect_completed"
    )


@pytest.mark.asyncio
async def test_unknown_external_effect_becomes_ambiguous_without_replay(db_session):
    identity = _identity("run-external-ambiguous")
    route = _route()
    plan = _tool_plan("image_generation")
    _seed_active_run(db_session, identity)
    completion = _ScriptedCompletionPort(({
        "choices": [{"message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-image",
                "type": "function",
                "function": {
                    "name": "image_generation",
                    "arguments": json.dumps({"prompt": "一只猫"}),
                },
            }],
        }}],
    }, {
        "choices": [{"message": {"role": "assistant", "content": "不应重试"}}],
    }))
    tool_calls = 0

    async def timeout_tool(_request: RuntimeToolExecutionRequest):
        nonlocal tool_calls
        tool_calls += 1
        raise TimeoutError("外部服务结果未知")

    runtime = NativeAgentRuntime(
        completion,
        RegisteredToolExecutionPort({"tool.image.execute": timeout_tool}),
        runtime_id="native:test",
        tool_plan_resolver=lambda: plan,
        tool_binding_resolver=lambda _name: "tool.image.execute",
        tool_effect_resolver=lambda _name: RuntimeToolEffectClass.EXTERNAL,
        recovery_port=SqlAlchemyRuntimeRecoveryCoordinator(
            _session_factory(db_session)
        ),
        available_tool_names=("image_generation",),
    )
    await runtime.start()
    runtime.set_model_route(route)

    with pytest.raises(AgentRuntimeAmbiguousError):
        await runtime.run_event(
            AgentTurnRequest(
                _context(identity.run_id, route=route, plan=plan),
                "生成图片",
            ),
            lambda _event: None,
        )

    assert len(completion.requests) == 1
    assert tool_calls == 1
    receipt = db_session.query(RunSideEffectReceipt).one()
    assert receipt.state == "ambiguous"
    latest = (
        db_session.query(RunCheckpointRow)
        .order_by(RunCheckpointRow.sequence.desc())
        .first()
    )
    assert latest.boundary == "tool_ambiguous"
    assert latest.resumable is False
    _terminate_run(db_session, identity, status="ambiguous")
    preview = SqlAlchemyRunRecoveryService(db_session).preflight(
        source_run_id=identity.run_id,
        checkpoint_id=str(latest.checkpoint_id),
        operation_kind=RuntimeRecoveryOperationKind.FORK,
        principal=_principal(),
        current_runtime_id="native:test",
        current_runtime_protocol_version="1.0",
        current_plans=_plans(route, plan),
        current_model_route=route,
    )
    assert preview.allowed is False
    assert "checkpoint_not_resumable" in preview.blockers
    assert "side_effect_ambiguous" in preview.blockers


@pytest.mark.asyncio
async def test_invalid_side_effect_result_contract_is_ambiguous(db_session):
    identity = _identity("run-invalid-effect-result")
    route = _route()
    plan = _tool_plan("schedule_task")
    _seed_active_run(db_session, identity)
    completion = _ScriptedCompletionPort(({
        "choices": [{"message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-invalid-result",
                "type": "function",
                "function": {
                    "name": "schedule_task",
                    "arguments": json.dumps({"action": "create"}),
                },
            }],
        }}],
    },))
    tool_calls = 0

    async def invalid_result(_request: RuntimeToolExecutionRequest):
        nonlocal tool_calls
        tool_calls += 1
        return {"status": "success"}

    runtime = NativeAgentRuntime(
        completion,
        RegisteredToolExecutionPort({
            "tool.schedule.execute": invalid_result,
        }),
        runtime_id="native:test",
        tool_plan_resolver=lambda: plan,
        tool_binding_resolver=lambda _name: "tool.schedule.execute",
        tool_effect_resolver=lambda _name: RuntimeToolEffectClass.LOCAL_WRITE,
        recovery_port=SqlAlchemyRuntimeRecoveryCoordinator(
            _session_factory(db_session)
        ),
        available_tool_names=("schedule_task",),
    )
    await runtime.start()
    runtime.set_model_route(route)

    with pytest.raises(AgentRuntimeAmbiguousError):
        await runtime.run_event(
            AgentTurnRequest(
                _context(identity.run_id, route=route, plan=plan),
                "创建任务",
            ),
            lambda _event: None,
        )

    assert len(completion.requests) == 1
    assert tool_calls == 1
    assert db_session.query(RunSideEffectReceipt).one().state == "ambiguous"


@pytest.mark.asyncio
async def test_fork_creates_new_lineage_and_executes_real_continue(db_session):
    source = _identity("run-fork-source")
    route = _route()
    plan = _tool_plan()
    _seed_active_run(db_session, source)
    coordinator = SqlAlchemyRuntimeRecoveryCoordinator(
        _session_factory(db_session)
    )
    source_checkpoint = await coordinator.save_checkpoint(
        RuntimeCheckpointCapture(
            identity=source,
            boundary=RuntimeCheckpointBoundary.TURN_COMPLETED,
            runtime_id="native:test",
            runtime_protocol_version="1.0",
            messages=(
                RuntimeMessage("system", "系统规则"),
                RuntimeMessage("user", "原问题"),
                RuntimeMessage("assistant", "原回答"),
            ),
            plans=_plans(route, plan),
            model_route=route,
            model_step=1,
        )
    )
    _terminate_run(db_session, source)
    source_before = load_authoritative_run_view(
        SqlAlchemyRunEventLedger(db_session),
        source.run_id,
    )
    service = SqlAlchemyRunRecoveryService(db_session)
    preview = service.preflight(
        source_run_id=source.run_id,
        checkpoint_id=source_checkpoint.checkpoint_id,
        operation_kind=RuntimeRecoveryOperationKind.FORK,
        principal=_principal(),
        current_runtime_id="native:test",
        current_runtime_protocol_version="1.0",
        current_plans=_plans(route, plan),
        current_model_route=route,
    )
    assert preview.allowed is True
    child = _identity("run-fork-child", suffix="child")
    prepared = service.prepare_operation(
        request_id="fork-request-1",
        confirm_checkpoint_id=source_checkpoint.checkpoint_id,
        source_run_id=source.run_id,
        checkpoint_id=source_checkpoint.checkpoint_id,
        operation_kind=RuntimeRecoveryOperationKind.FORK,
        principal=_principal(),
        child_identity=child,
        current_runtime_id="native:test",
        current_runtime_protocol_version="1.0",
        current_plans=_plans(route, plan),
        current_model_route=route,
    )
    replay = service.prepare_operation(
        request_id="fork-request-1",
        confirm_checkpoint_id=source_checkpoint.checkpoint_id,
        source_run_id=source.run_id,
        checkpoint_id=source_checkpoint.checkpoint_id,
        operation_kind=RuntimeRecoveryOperationKind.FORK,
        principal=_principal(),
        child_identity=child,
        current_runtime_id="native:test",
        current_runtime_protocol_version="1.0",
        current_plans=_plans(route, plan),
        current_model_route=route,
    )
    assert replay.idempotent_replay is True
    assert replay.child_run_id == prepared.child_run_id
    source_after_prepare = load_authoritative_run_view(
        SqlAlchemyRunEventLedger(db_session),
        source.run_id,
    )
    assert source_after_prepare.head == source_before.head
    assert source_after_prepare.records == source_before.records

    completion = _ScriptedCompletionPort(({
        "choices": [{"message": {
            "role": "assistant",
            "content": "子运行继续完成",
        }}],
    },))
    runtime = NativeAgentRuntime(
        completion,
        RegisteredToolExecutionPort({}),
        runtime_id="native:test",
        tool_plan_resolver=lambda: plan,
        recovery_port=coordinator,
        available_tool_names=(),
    )
    await runtime.start()
    child_context = _context(
        child.run_id,
        route=route,
        plan=plan,
        suffix="child",
    )
    result = await service.execute_prepared(
        operation_id=prepared.operation_id,
        principal=_principal(),
        runtime=runtime,
        request=AgentTurnRequest(
            child_context,
            "",
            kind=RuntimeTurnKind.CONTINUE,
        ),
    )
    assert result.messages[-1].content == "子运行继续完成"
    operation = db_session.get(RunRecoveryOperation, prepared.operation_id)
    assert operation.status == "succeeded"
    child_view = load_authoritative_run_view(
        SqlAlchemyRunEventLedger(db_session),
        child.run_id,
    )
    assert child_view.projection.terminal is True
    assert child_view.projection.lineage_operation_kind == "fork"
    assert child_view.projection.parent_run_sha256
    source_after_execute = load_authoritative_run_view(
        SqlAlchemyRunEventLedger(db_session),
        source.run_id,
    )
    assert source_after_execute.head == source_before.head


@pytest.mark.asyncio
async def test_preflight_rejects_version_file_and_side_effect_drift(db_session):
    identity = _identity("run-recovery-drift")
    route = _route()
    plan = _tool_plan("workspace_write")
    plans = list(_plans(route, plan))
    plans[4] = RuntimePlanRef(
        RuntimePlanKind.WORKSPACE,
        "workspace:workspace-test",
        "3" * 64,
    )
    _seed_active_run(db_session, identity)
    coordinator = SqlAlchemyRuntimeRecoveryCoordinator(
        _session_factory(db_session)
    )
    result = RuntimeToolExecutionResult(
        tool_call=RuntimeToolCall(
            call_id="call-write",
            name="workspace_write",
            arguments={"path": "notes/a.txt", "content": "固定内容"},
            status=RuntimeToolCallStatus.COMPLETED,
            result={"status": "success", "data": {"path": "notes/a.txt"}},
        )
    )
    checkpoint = await coordinator.save_checkpoint(RuntimeCheckpointCapture(
        identity=identity,
        boundary=RuntimeCheckpointBoundary.TOOL_COMPLETED,
        runtime_id="native:test",
        runtime_protocol_version="1.0",
        messages=(RuntimeMessage("tool", "ok", tool_call_id="call-write"),),
        plans=tuple(plans),
        model_route=route,
        last_tool_result=result,
    ))
    _terminate_run(db_session, identity)
    verifier = _FileVerifier(valid=False)
    service = SqlAlchemyRunRecoveryService(
        db_session,
        file_verifier=verifier,
    )
    drifted_route = _route("other-model")
    preview = service.preflight(
        source_run_id=identity.run_id,
        checkpoint_id=checkpoint.checkpoint_id,
        operation_kind=RuntimeRecoveryOperationKind.FORK,
        principal=_principal(),
        current_runtime_id="native:test",
        current_runtime_protocol_version="1.0",
        current_plans=tuple(plans),
        current_model_route=drifted_route,
    )
    assert "model_route_drift" in preview.blockers
    assert any(item.startswith("file_drift:") for item in preview.blockers)
    assert verifier.proofs[0].virtual_path == "notes/a.txt"


def test_artifact_plan_pins_policy_without_freezing_unrelated_inventory(db_session):
    workspace = Workspace(
        id="workspace-proof-policy",
        platform="qq",
        owner_type="user",
        owner_id="10001",
        name="default",
        status="active",
        quota_bytes=1024 * 1024,
    )
    db_session.add(workspace)
    db_session.commit()
    plan = _tool_plan("reply")
    first = build_live_recovery_plans(
        db_session,
        principal=_principal(),
        session_id="private_10001",
        chat_type="private",
        runtime_id="native:test",
        prompt_key="prompt:test",
        prompt_sha256="2" * 64,
        tool_plan=plan,
    )
    digest = "a" * 64
    db_session.add_all([
        Asset(
            sha256=digest,
            size_bytes=4,
            media_type="text/plain",
            storage_key=f"sha256/{digest}",
        ),
        WorkspaceAsset(
            workspace_id=workspace.id,
            asset_sha256=digest,
            logical_name="unrelated.txt",
        ),
    ])
    db_session.commit()
    second = build_live_recovery_plans(
        db_session,
        principal=_principal(),
        session_id="private_10001",
        chat_type="private",
        runtime_id="native:test",
        prompt_key="prompt:test",
        prompt_sha256="2" * 64,
        tool_plan=plan,
    )

    assert next(
        item for item in first if item.kind is RuntimePlanKind.ARTIFACT
    ) == next(item for item in second if item.kind is RuntimePlanKind.ARTIFACT)


@pytest.mark.asyncio
async def test_preflight_rejects_artifact_acl_drift(db_session):
    workspace = Workspace(
        id="workspace-artifact-proof",
        platform="qq",
        owner_type="user",
        owner_id="10001",
        name="default",
        status="active",
        quota_bytes=1024 * 1024,
    )
    digest = "b" * 64
    asset = Asset(
        sha256=digest,
        size_bytes=4,
        media_type="text/plain",
        storage_key=f"sha256/{digest}",
    )
    link = WorkspaceAsset(
        workspace_id=workspace.id,
        asset_sha256=digest,
        logical_name="report.txt",
    )
    db_session.add_all([workspace, asset, link])
    db_session.commit()
    identity = _identity("run-artifact-drift")
    route = _route()
    plan = _tool_plan("asset_publish")
    plans = list(_plans(route, plan))
    plans[4] = RuntimePlanRef(
        RuntimePlanKind.WORKSPACE,
        f"workspace:{workspace.id}",
        "3" * 64,
    )
    _seed_active_run(db_session, identity)
    result = RuntimeToolExecutionResult(
        tool_call=RuntimeToolCall(
            call_id="call-publish",
            name="asset_publish",
            arguments={"path": "report.txt"},
            status=RuntimeToolCallStatus.COMPLETED,
            result={
                "status": "success",
                "data": {
                    "ref": f"asset://sha256/{digest}",
                    "size_bytes": 4,
                    "media_type": "text/plain",
                },
            },
        )
    )
    checkpoint = await SqlAlchemyRuntimeRecoveryCoordinator(
        _session_factory(db_session)
    ).save_checkpoint(RuntimeCheckpointCapture(
        identity=identity,
        boundary=RuntimeCheckpointBoundary.TOOL_COMPLETED,
        runtime_id="native:test",
        runtime_protocol_version="1.0",
        messages=(RuntimeMessage("tool", "published"),),
        plans=tuple(plans),
        model_route=route,
        last_tool_result=result,
    ))
    _terminate_run(db_session, identity)
    db_session.delete(link)
    db_session.commit()

    preview = SqlAlchemyRunRecoveryService(db_session).preflight(
        source_run_id=identity.run_id,
        checkpoint_id=checkpoint.checkpoint_id,
        operation_kind=RuntimeRecoveryOperationKind.FORK,
        principal=_principal(),
        current_runtime_id="native:test",
        current_runtime_protocol_version="1.0",
        current_plans=tuple(plans),
        current_model_route=route,
    )
    assert preview.allowed is False
    assert f"artifact_drift:{digest[:12]}" in preview.blockers


@pytest.mark.asyncio
async def test_preflight_verifies_receipt_against_ledger_facts(db_session):
    identity = _identity("run-receipt-integrity")
    route = _route()
    plan = _tool_plan("schedule_task")
    plans = _plans(route, plan)
    _seed_active_run(db_session, identity)
    coordinator = SqlAlchemyRuntimeRecoveryCoordinator(
        _session_factory(db_session)
    )
    call = RuntimeToolCall(
        call_id="call-schedule",
        name="schedule_task",
        arguments={"action": "create"},
    )
    before = await coordinator.save_checkpoint(RuntimeCheckpointCapture(
        identity=identity,
        boundary=RuntimeCheckpointBoundary.TOOL_READY,
        runtime_id="native:test",
        runtime_protocol_version="1.0",
        messages=(RuntimeMessage("user", "创建任务"),),
        plans=plans,
        model_route=route,
        pending_tool=call,
        resumable=False,
    ))
    guard = await coordinator.prepare_tool_effect(
        identity=identity,
        tool_call=call,
        execution_port_id="tool.schedule_task.execute",
        idempotency_key="request:call-schedule",
        effect_class=RuntimeToolEffectClass.LOCAL_WRITE,
        checkpoint=before,
    )
    assert guard is not None
    result = RuntimeToolExecutionResult(
        tool_call=RuntimeToolCall(
            call_id=call.call_id,
            name=call.name,
            arguments=call.arguments,
            status=RuntimeToolCallStatus.COMPLETED,
            result={"status": "success", "task_id": "task-1"},
        )
    )
    await coordinator.settle_tool_effect(
        guard,
        state=RuntimeSideEffectState.COMPLETED,
        result=result,
    )
    after = await coordinator.save_checkpoint(RuntimeCheckpointCapture(
        identity=identity,
        boundary=RuntimeCheckpointBoundary.TOOL_COMPLETED,
        runtime_id="native:test",
        runtime_protocol_version="1.0",
        messages=(RuntimeMessage("tool", "created"),),
        plans=plans,
        model_route=route,
        last_tool_result=result,
        side_effect_receipt_ids=(guard.receipt_id,),
    ))
    _terminate_run(db_session, identity)
    receipt = db_session.get(RunSideEffectReceipt, guard.receipt_id)
    receipt.result_sha256 = "f" * 64
    db_session.commit()

    with pytest.raises(RunRecoveryIntegrityError, match="终态回执"):
        SqlAlchemyRunRecoveryService(db_session).preflight(
            source_run_id=identity.run_id,
            checkpoint_id=after.checkpoint_id,
            operation_kind=RuntimeRecoveryOperationKind.FORK,
            principal=_principal(),
            current_runtime_id="native:test",
            current_runtime_protocol_version="1.0",
            current_plans=plans,
            current_model_route=route,
        )


@pytest.mark.asyncio
async def test_resume_requires_latest_but_rewind_accepts_older_safe_checkpoint(
    db_session,
):
    identity = _identity("run-rewind-source")
    route = _route()
    plan = _tool_plan()
    plans = _plans(route, plan)
    _seed_active_run(db_session, identity)
    coordinator = SqlAlchemyRuntimeRecoveryCoordinator(
        _session_factory(db_session)
    )
    older = await coordinator.save_checkpoint(RuntimeCheckpointCapture(
        identity=identity,
        boundary=RuntimeCheckpointBoundary.TURN_STARTED,
        runtime_id="native:test",
        runtime_protocol_version="1.0",
        messages=(RuntimeMessage("user", "第一版"),),
        plans=plans,
        model_route=route,
    ))
    latest = await coordinator.save_checkpoint(RuntimeCheckpointCapture(
        identity=identity,
        boundary=RuntimeCheckpointBoundary.TURN_COMPLETED,
        runtime_id="native:test",
        runtime_protocol_version="1.0",
        messages=(
            RuntimeMessage("user", "第一版"),
            RuntimeMessage("assistant", "第二版"),
        ),
        plans=plans,
        model_route=route,
        model_step=1,
    ))
    _terminate_run(db_session, identity)
    service = SqlAlchemyRunRecoveryService(db_session)
    rejected = service.preflight(
        source_run_id=identity.run_id,
        checkpoint_id=older.checkpoint_id,
        operation_kind=RuntimeRecoveryOperationKind.RESUME,
        principal=_principal(),
        current_runtime_id="native:test",
        current_runtime_protocol_version="1.0",
        current_plans=plans,
        current_model_route=route,
    )
    resumed = service.preflight(
        source_run_id=identity.run_id,
        checkpoint_id=latest.checkpoint_id,
        operation_kind=RuntimeRecoveryOperationKind.RESUME,
        principal=_principal(),
        current_runtime_id="native:test",
        current_runtime_protocol_version="1.0",
        current_plans=plans,
        current_model_route=route,
    )
    rewound = service.preflight(
        source_run_id=identity.run_id,
        checkpoint_id=older.checkpoint_id,
        operation_kind=RuntimeRecoveryOperationKind.REWIND,
        principal=_principal(),
        current_runtime_id="native:test",
        current_runtime_protocol_version="1.0",
        current_plans=plans,
        current_model_route=route,
    )

    assert rejected.allowed is False
    assert "resume_requires_latest_checkpoint" in rejected.blockers
    assert resumed.allowed is True
    assert rewound.allowed is True


@pytest.mark.asyncio
async def test_restore_rechecks_file_state_after_operation_was_prepared(db_session):
    source = _identity("run-restore-toctou")
    route = _route()
    plan = _tool_plan("workspace_write")
    plans = list(_plans(route, plan))
    plans[4] = RuntimePlanRef(
        RuntimePlanKind.WORKSPACE,
        "workspace:workspace-toctou",
        "3" * 64,
    )
    frozen_plans = tuple(plans)
    _seed_active_run(db_session, source)
    result = RuntimeToolExecutionResult(
        tool_call=RuntimeToolCall(
            call_id="call-toctou",
            name="workspace_write",
            arguments={"path": "state.txt", "content": "v1"},
            status=RuntimeToolCallStatus.COMPLETED,
            result={"status": "success", "data": {"path": "state.txt"}},
        )
    )
    coordinator = SqlAlchemyRuntimeRecoveryCoordinator(
        _session_factory(db_session)
    )
    checkpoint = await coordinator.save_checkpoint(RuntimeCheckpointCapture(
        identity=source,
        boundary=RuntimeCheckpointBoundary.TOOL_COMPLETED,
        runtime_id="native:test",
        runtime_protocol_version="1.0",
        messages=(RuntimeMessage("tool", "written"),),
        plans=frozen_plans,
        model_route=route,
        last_tool_result=result,
    ))
    _terminate_run(db_session, source)
    verifier = _FileVerifier(valid=True)
    service = SqlAlchemyRunRecoveryService(
        db_session,
        file_verifier=verifier,
    )
    child = _identity("run-restore-toctou-child", suffix="child")
    prepared = service.prepare_operation(
        request_id="restore-toctou-request",
        confirm_checkpoint_id=checkpoint.checkpoint_id,
        source_run_id=source.run_id,
        checkpoint_id=checkpoint.checkpoint_id,
        operation_kind=RuntimeRecoveryOperationKind.FORK,
        principal=_principal(),
        child_identity=child,
        current_runtime_id="native:test",
        current_runtime_protocol_version="1.0",
        current_plans=frozen_plans,
        current_model_route=route,
    )
    verifier.valid = False
    runtime = NativeAgentRuntime(
        _ScriptedCompletionPort(()),
        RegisteredToolExecutionPort({}),
        runtime_id="native:test",
        tool_plan_resolver=lambda: plan,
        recovery_port=coordinator,
        available_tool_names=("workspace_write",),
    )
    await runtime.start()

    with pytest.raises(RunRecoveryPreflightDenied, match="状态漂移"):
        service.restore_into_runtime(
            operation_id=prepared.operation_id,
            principal=_principal(),
            runtime=runtime,
        )

    operation = db_session.get(RunRecoveryOperation, prepared.operation_id)
    assert operation.status == "prepared"


@pytest.mark.asyncio
async def test_recovery_migration_enforces_checkpoint_and_erasure_guards():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    run_schema_migrations(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    identity = _identity("run-recovery-trigger")
    route = _route()
    plan = _tool_plan()
    with factory() as db:
        _seed_active_run(db, identity)
    checkpoint = await SqlAlchemyRuntimeRecoveryCoordinator(
        factory
    ).save_checkpoint(RuntimeCheckpointCapture(
        identity=identity,
        boundary=RuntimeCheckpointBoundary.TURN_STARTED,
        runtime_id="native:test",
        runtime_protocol_version="1.0",
        messages=(RuntimeMessage("user", "trigger"),),
        plans=_plans(route, plan),
        model_route=route,
    ))
    with factory() as db:
        row = db.get(RunCheckpointRow, checkpoint.checkpoint_id)
        db.add_all([
            RunSideEffectReceipt(
                receipt_id="effect-trigger",
                run_id=identity.run_id,
                tool_call_id="call-trigger",
                tool_name="schedule_task",
                execution_port_id="tool.schedule_task.execute",
                effect_class="local_write",
                state="prepared",
                idempotency_key_sha256="6" * 64,
                request_sha256="7" * 64,
                checkpoint_before_id=checkpoint.checkpoint_id,
                prepared_ledger_sequence=int(row.ledger_sequence) + 1,
            ),
            RunRecoveryOperation(
                operation_id="recovery-trigger",
                request_id_sha256="8" * 64,
                request_fingerprint_sha256="9" * 64,
                operation_kind="fork",
                run_id=identity.run_id,
                restored_checkpoint_id=checkpoint.checkpoint_id,
                source_run_id_sha256="a" * 64,
                source_checkpoint_id_sha256="b" * 64,
                source_checkpoint_sha256=checkpoint.payload_sha256,
                source_head_sequence=int(row.ledger_sequence),
                source_head_sha256=str(row.ledger_event_sha256),
                owner_platform="qq",
                owner_type="user",
                owner_id="10001",
                status="prepared",
            ),
        ])
        db.commit()

    with engine.begin() as connection:
        with pytest.raises(DatabaseError, match="run_checkpoints_immutable"):
            connection.execute(
                text(
                    "UPDATE run_checkpoints SET resumable = 0 "
                    "WHERE checkpoint_id = :checkpoint_id"
                ),
                {"checkpoint_id": checkpoint.checkpoint_id},
            )
    for table_name in (
        "run_checkpoints",
        "run_side_effect_receipts",
        "run_recovery_operations",
    ):
        with engine.begin() as connection:
            with pytest.raises(DatabaseError, match="run_recovery_erasure_guard"):
                connection.execute(text(
                    f"DELETE FROM {table_name} "
                    "WHERE run_id = 'run-recovery-trigger'"
                ))
    with engine.connect() as connection:
        version_count = connection.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260804_run_checkpoint_recovery_v1'"
        )).scalar_one()
    assert version_count == 1
    engine.dispose()
