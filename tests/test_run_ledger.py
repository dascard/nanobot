from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

from core.agent_runtime.contracts import (
    RuntimeActor,
    RuntimeActorType,
    RuntimePrincipal,
    RuntimeRunEvent,
    RuntimeRunEventKind,
    RuntimeRunIdentity,
    RuntimeRunStatus,
    RuntimeUsage,
    RuntimeOwnerType,
)
from core.agent_runtime.service_ports import (
    RuntimePermissionOutcome,
    RuntimePermissionRequest,
    RuntimePermissionRisk,
    StaticPermissionPort,
)
from core.run_ledger.adapters import (
    run_prompt_resolved_event,
    runtime_run_event_to_ledger,
)
from core.run_ledger.contracts import (
    RUN_LEDGER_SCHEMA_VERSION,
    RunLedgerAuthorityError,
    RunLedgerConflictError,
    RunLedgerContractError,
    RunLedgerEventDraft,
    RunLedgerIdentity,
    RunLedgerIntegrityError,
    UnsupportedRunLedgerSchemaError,
    decode_run_ledger_payload,
)
from core.run_ledger.persistence import (
    SqlAlchemyRunEventLedger,
    SqlAlchemyRunEventLedgerWriter,
)
from core.run_ledger.projection import (
    assess_run_ledger_readiness,
    project_run_ledger,
)
from core.run_ledger.read_model import load_authoritative_run_view
from core.run_ledger.sinks import (
    LedgeredPermissionPort,
    SqlAlchemyRuntimeEventLedgerSink,
    SqlAlchemyRuntimeRunEventSink,
)
from core.telemetry.contracts import TelemetryCorrelation
from tests.sqlite_test_utils import install_base_schema


def _event(
    event_id: str,
    event_type: str,
    *,
    status: str = "",
    payload: dict[str, object] | None = None,
    owner_id: str = "",
    correction_of_event_id: str = "",
) -> RunLedgerEventDraft:
    identity = RunLedgerIdentity()
    if owner_id:
        identity = RunLedgerIdentity(
            actor_type="user",
            actor_id=owner_id,
            owner_platform="qq",
            owner_type="user",
            owner_id=owner_id,
        )
    return RunLedgerEventDraft(
        event_id=event_id,
        run_id="run-1",
        event_type=event_type,
        occurred_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        source="test.run_ledger",
        correlation=TelemetryCorrelation(
            request_id="request-1",
            session_id="session-1",
            trace_id="trace-1",
            run_id="run-1",
        ),
        identity=identity,
        status=status,
        payload=payload or {},
        correction_of_event_id=correction_of_event_id,
    )


def test_run_ledger_contract_rejects_raw_sensitive_payload_and_future_schema():
    with pytest.raises(RunLedgerContractError, match="敏感正文"):
        _event(
            "event-secret",
            "model.request.started",
            payload={"prompt_content": "不要保存我"},
        )
    for unsafe_key in (
        "raw_data",
        "request_body",
        "response_detail",
        "tool_result",
        "workspace_path",
    ):
        with pytest.raises(RunLedgerContractError, match="敏感正文"):
            _event(
                f"event-secret-{unsafe_key}",
                "tool.execute.started",
                payload={unsafe_key: "不要保存我"},
            )

    safe = _event(
        "event-safe",
        "model.request.started",
        payload={
            "prompt_sha256": "a" * 64,
            "input_bytes": 12,
            "input_tokens": 3,
        },
    )
    assert safe.payload["prompt_sha256"] == "a" * 64
    assert safe.correlation.run_id == "run-1"

    with pytest.raises(UnsupportedRunLedgerSchemaError):
        replace(safe, schema_version=RUN_LEDGER_SCHEMA_VERSION + 1)
    with pytest.raises(UnsupportedRunLedgerSchemaError):
        decode_run_ledger_payload(
            "{}",
            schema_version=RUN_LEDGER_SCHEMA_VERSION + 1,
        )


def test_run_ledger_contract_has_no_database_or_framework_imports():
    path = Path("core/run_ledger/contracts.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    assert imported_roots.isdisjoint({
        "fastapi",
        "kohakuterrarium",
        "nanobot_kt",
        "sqlalchemy",
    })


def test_sqlalchemy_run_ledger_is_ordered_idempotent_and_terminal(db_session):
    ledger = SqlAlchemyRunEventLedger(db_session)
    accepted = _event("event-1", "run.accepted", status="accepted")
    first = ledger.append(accepted, expected_sequence=1)
    duplicate = ledger.append(accepted, expected_sequence=1)
    assert duplicate == first

    model_started = _event(
        "event-2",
        "model.request.started",
        status="started",
        payload={"model": "test-model", "request_sha256": "b" * 64},
    )
    second = ledger.append(model_started, expected_sequence=2)
    terminal = ledger.append(
        _event("event-3", "run.terminated", status="succeeded"),
        expected_sequence=3,
    )
    db_session.commit()

    assert first.sequence == 1
    assert second.previous_event_sha256 == first.event_sha256
    assert terminal.previous_event_sha256 == second.event_sha256
    assert ledger.head("run-1").terminal_sequence == 3
    records = ledger.read("run-1")
    assert [record.sequence for record in records] == [1, 2, 3]

    with pytest.raises(RunLedgerConflictError, match="已终止"):
        ledger.append(_event("event-4", "tool.execute.started"))

    correction = ledger.append(
        _event(
            "event-4",
            "run.event_corrected",
            payload={"replacement_status": "failed"},
            correction_of_event_id="event-3",
        ),
        expected_sequence=4,
    )
    db_session.commit()
    projection = project_run_ledger(ledger.read("run-1"))
    assert correction.sequence == 4
    assert projection is not None
    assert projection.status == "failed"
    assert projection.terminal is True
    assert projection.correction_count == 1


def test_run_ledger_rejects_conflicting_id_sequence_owner_and_correction(db_session):
    ledger = SqlAlchemyRunEventLedger(db_session)
    with pytest.raises(RunLedgerConflictError, match="首条事实"):
        ledger.append(replace(
            _event("orphan-event", "model.request.started"),
            run_id="orphan-run",
            correlation=TelemetryCorrelation(run_id="orphan-run"),
        ))
    ledger.append(
        _event(
            "event-1",
            "run.accepted",
            status="accepted",
            owner_id="user-1",
        )
    )

    with pytest.raises(RunLedgerConflictError, match="不同事实"):
        ledger.append(
            _event(
                "event-1",
                "run.accepted",
                status="accepted",
                payload={"model": "changed"},
                owner_id="user-1",
            )
        )
    with pytest.raises(RunLedgerConflictError, match="期望 sequence"):
        ledger.append(
            _event("event-2", "model.request.started"),
            expected_sequence=3,
        )
    with pytest.raises(RunLedgerConflictError, match="切换 owner"):
        ledger.append(
            _event(
                "event-2",
                "model.request.started",
                owner_id="user-2",
            )
        )
    with pytest.raises(RunLedgerConflictError, match="同一 Run"):
        ledger.append(
            _event(
                "event-2",
                "run.event_corrected",
                correction_of_event_id="not-found",
            )
        )


def test_runtime_run_event_adapter_hashes_text_and_projects_usage(db_session):
    identity = RuntimeRunIdentity(
        run_id="run-1",
        turn_id="turn-1",
        correlation_id="trace-1",
        actor=RuntimeActor(RuntimeActorType.USER, "user-1"),
        owner=RuntimePrincipal(
            platform="qq",
            owner_type=RuntimeOwnerType.USER,
            owner_id="user-1",
        ),
    )
    text_event = RuntimeRunEvent(
        event_id="runtime-text-1",
        identity=identity,
        sequence=1,
        kind=RuntimeRunEventKind.TEXT_DELTA,
        status=RuntimeRunStatus.RUNNING,
        occurred_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        text_delta="不得进入账本的正文",
    )
    usage_event = RuntimeRunEvent(
        event_id="runtime-usage-1",
        identity=identity,
        sequence=2,
        kind=RuntimeRunEventKind.USAGE,
        status=RuntimeRunStatus.RUNNING,
        occurred_at=datetime(2026, 8, 4, 12, 1, tzinfo=timezone.utc),
        usage=RuntimeUsage(
            input_tokens=10,
            output_tokens=4,
            cached_input_tokens=6,
            reasoning_tokens=2,
            cost_microunits=30,
        ),
    )

    text_draft = runtime_run_event_to_ledger(text_event)
    usage_draft = runtime_run_event_to_ledger(usage_event)
    assert "不得进入账本的正文" not in str(dict(text_draft.payload))
    assert text_draft.payload["text_chars"] == len("不得进入账本的正文")

    ledger = SqlAlchemyRunEventLedger(db_session)
    ledger.append(_event("event-accepted", "run.accepted", status="accepted"))
    ledger.append(text_draft)
    ledger.append(usage_draft)
    db_session.commit()
    projection = project_run_ledger(ledger.read("run-1"))
    assert projection is not None
    assert projection.input_tokens == 10
    assert projection.output_tokens == 4
    assert projection.cached_input_tokens == 6
    assert projection.reasoning_tokens == 2
    assert projection.cost_microunits == 30


def test_prompt_resolution_projection_and_legacy_readiness_are_body_free(
    db_session,
):
    ledger = SqlAlchemyRunEventLedger(db_session)
    ledger.append(_event(
        "event-accepted",
        "run.accepted",
        status="accepted",
        payload={
            "prompt_mode": "prompt",
            "prompt_key": "chat_private",
            "prompt_sha256": "a" * 64,
            "model": "model-a",
        },
    ))
    prompt_event = run_prompt_resolved_event(
        run_id="run-1",
        trace_id="trace-1",
        session_id="session-1",
        prompt_mode="prompt",
        prompt_key="chat_private",
        prompt_source="runtime:/private/prompt.md",
        prompt_sha256="b" * 64,
        resolution_manifest_json=(
            '{"base":{"runtime_path":"/private/prompt.md"}}'
        ),
        resolution_count=1,
        context_manifest_sha256="c" * 64,
        context_manifest_entry_count=7,
        context_manifest_token_estimate=321,
        context_manifest_policy_id="prompt-context-v1-private",
        occurred_at=datetime(2026, 8, 4, 12, 1, tzinfo=timezone.utc),
    )
    ledger.append(prompt_event)
    ledger.append(_event(
        "event-terminal",
        "run.terminated",
        status="succeeded",
        payload={"model": "model-b"},
    ))
    db_session.commit()

    stored_payload = str(dict(prompt_event.payload))
    assert "/private/prompt.md" not in stored_payload
    projection = project_run_ledger(ledger.read("run-1"))
    assert projection is not None
    assert projection.prompt_mode == "prompt"
    assert projection.prompt_key == "chat_private"
    assert projection.prompt_sha256 == "b" * 64
    assert projection.prompt_resolution_sha256
    assert projection.model_ids == ("model-a", "model-b")
    assert projection.to_dict()["context_manifest"]["prompt_sha256"] == (
        "b" * 64
    )
    assert projection.context_manifest_sha256 == "c" * 64
    assert projection.context_manifest_entry_count == 7
    assert projection.context_manifest_token_estimate == 321
    assert projection.context_manifest_policy_id == "prompt-context-v1-private"

    records = ledger.read("run-1")
    ready = assess_run_ledger_readiness(
        records,
        legacy_status="success",
        legacy_finished_at=datetime(2026, 8, 4, 12, 2),
    )
    assert ready.projection_consistent is True
    assert ready.reason_codes == ()
    assert ready.legacy_status == "succeeded"
    assert ready.ledger_status == "succeeded"

    drifted = assess_run_ledger_readiness(
        records,
        legacy_status="error",
        legacy_finished_at=datetime(2026, 8, 4, 12, 2),
        projection_complete=False,
    )
    assert drifted.projection_consistent is False
    assert drifted.reason_codes == (
        "projection_incomplete",
        "status_mismatch",
    )


def test_writer_recovers_successful_commit_with_unknown_outcome(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger-commit.db'}")
    install_base_schema(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    factory_calls = 0

    def uncertain_factory():
        nonlocal factory_calls
        factory_calls += 1
        db = session_factory()
        if factory_calls == 1:
            commit = db.commit

            def commit_then_raise():
                commit()
                raise RuntimeError("commit outcome unknown")

            db.commit = commit_then_raise  # type: ignore[method-assign]
        return db

    writer = SqlAlchemyRunEventLedgerWriter(uncertain_factory)
    record = writer.append(
        _event("event-commit", "run.accepted", status="accepted")
    )
    assert record.event_id == "event-commit"
    assert factory_calls >= 2
    with session_factory() as db:
        assert SqlAlchemyRunEventLedger(db).head("run-1").last_sequence == 1
    engine.dispose()


def test_writer_append_many_is_atomic_and_assigns_contiguous_sequences(
    db_session,
):
    factory = sessionmaker(bind=db_session.get_bind())
    writer = SqlAlchemyRunEventLedgerWriter(factory)
    accepted = _event("batch-accepted", "run.accepted", status="accepted")
    running = _event(
        "batch-running",
        "run.status_changed",
        status="running",
    )

    with pytest.raises(RunLedgerConflictError, match="期望 sequence"):
        writer.append_many(
            (accepted, running),
            expected_sequences=(1, 3),
        )
    assert writer.head("run-1") is None

    records = writer.append_many(
        (accepted, running),
        expected_sequences=(1, 2),
    )
    assert [record.sequence for record in records] == [1, 2]
    assert records[1].previous_event_sha256 == records[0].event_sha256


def test_authoritative_read_model_pages_to_fixed_head_and_rejects_drift(
    db_session,
):
    ledger = SqlAlchemyRunEventLedger(db_session)
    ledger.append(_event("view-accepted", "run.accepted", status="accepted"))
    ledger.append(_event(
        "view-running",
        "run.status_changed",
        status="running",
    ))
    ledger.append(_event(
        "view-terminal",
        "run.terminated",
        status="succeeded",
    ))
    ledger.append(_event(
        "view-correction",
        "run.event_corrected",
        payload={"replacement_status": "failed"},
        correction_of_event_id="view-terminal",
    ))
    db_session.commit()

    view = load_authoritative_run_view(ledger, "run-1", page_size=1)
    assert view is not None
    assert view.head.last_sequence == 4
    assert view.head.terminal_sequence == 3
    assert view.projection.status == "failed"
    assert [record.sequence for record in view.records] == [1, 2, 3, 4]

    db_session.execute(text(
        "UPDATE run_ledger_stream_heads "
        "SET last_event_id='drifted-head' WHERE run_id='run-1'"
    ))
    db_session.commit()
    with pytest.raises(RunLedgerIntegrityError, match="last_event_id"):
        load_authoritative_run_view(ledger, "run-1", page_size=2)


def test_delivery_attempt_sink_owns_independent_terminal_run(db_session):
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import RuntimeEventEmitter

    factory = sessionmaker(bind=db_session.get_bind())
    sink = SqlAlchemyRuntimeEventLedgerSink(factory)
    emitter = RuntimeEventEmitter(
        RUNTIME_EVENT_REGISTRY,
        now=lambda: datetime(2026, 8, 4, 12, 2, tzinfo=timezone.utc),
        event_id_factory=iter(("delivery-start", "delivery-finish")).__next__,
    )
    context = TelemetryCorrelation(
        run_id="delivery:test:42",
        task_run_id="source-run-1",
        job_id="outbox-7",
        delivery_id="attempt-42",
    )

    sink.emit(emitter.emit(
        "delivery.attempt",
        "started",
        context=context,
        attributes={"channel": "qq", "attempt_no": 1},
    ))
    sink.emit(emitter.emit(
        "delivery.attempt",
        "failed",
        context=context,
        attributes={
            "channel": "qq",
            "attempt_no": 1,
            "error_type": "ambiguous_transport",
        },
    ))

    with factory() as db:
        records = SqlAlchemyRunEventLedger(db).read("delivery:test:42")
    assert [record.event_type for record in records] == [
        "run.accepted",
        "run.status_changed",
        "delivery.attempt.started",
        "delivery.attempt.failed",
        "run.terminated",
    ]
    projection = project_run_ledger(records)
    assert projection is not None
    assert projection.status == "ambiguous"
    assert projection.terminal is True
    assert records[0].event.correlation.task_run_id == "source-run-1"


@pytest.mark.asyncio
async def test_runtime_sinks_reject_events_for_unadmitted_business_run(
    db_session,
):
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import RuntimeEventEmitter

    factory = sessionmaker(bind=db_session.get_bind())
    runtime_sink = SqlAlchemyRuntimeEventLedgerSink(factory)
    emitter = RuntimeEventEmitter(
        RUNTIME_EVENT_REGISTRY,
        event_id_factory=lambda: "unadmitted-model-event",
    )
    event = emitter.emit(
        "model.request",
        "started",
        context=TelemetryCorrelation(run_id="unadmitted-main-run"),
        attributes={
            "route_key": "reply",
            "provider": "newapi",
            "model": "model-1",
            "source": "replyer",
        },
    )
    with pytest.raises(RunLedgerAuthorityError) as runtime_failure:
        runtime_sink.emit(event)
    assert runtime_failure.value.code == "run_not_admitted"

    identity = RuntimeRunIdentity(
        run_id="unadmitted-main-run",
        turn_id="turn-1",
        correlation_id="trace-1",
        actor=RuntimeActor(RuntimeActorType.USER, "user-1"),
        owner=RuntimePrincipal(
            platform="qq",
            owner_type=RuntimeOwnerType.USER,
            owner_id="user-1",
        ),
    )
    typed_event = RuntimeRunEvent(
        event_id="typed-unadmitted",
        identity=identity,
        sequence=1,
        kind=RuntimeRunEventKind.STATUS,
        status=RuntimeRunStatus.RUNNING,
        occurred_at=datetime.now(timezone.utc),
    )
    typed_sink = SqlAlchemyRuntimeRunEventSink(factory)
    with pytest.raises(RunLedgerAuthorityError) as typed_failure:
        await typed_sink.append(typed_event)
    assert typed_failure.value.code == "run_not_admitted"



def test_runtime_event_sink_records_model_tool_and_delivery_projection(db_session):
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import RuntimeEventEmitter

    factory = sessionmaker(bind=db_session.get_bind())
    writer = SqlAlchemyRunEventLedgerWriter(factory)
    writer.append(_event("event-accepted", "run.accepted", status="accepted"))
    sink = SqlAlchemyRuntimeEventLedgerSink(factory)
    emitter = RuntimeEventEmitter(
        RUNTIME_EVENT_REGISTRY,
        (sink,),
        now=lambda: datetime(2026, 8, 4, 12, 2, tzinfo=timezone.utc),
        event_id_factory=iter([
            "runtime-model-start",
            "runtime-model-finish",
            "runtime-tool-start",
            "runtime-tool-finish",
            "runtime-delivery-start",
            "runtime-delivery-finish",
        ]).__next__,
    )
    context = TelemetryCorrelation(
        request_id="request-1",
        session_id="session-1",
        trace_id="trace-1",
        run_id="run-1",
        tool_call_id="tool-call-1",
        delivery_id="delivery-1",
    )
    emitter.emit(
        "model.request",
        "started",
        context=context,
        attributes={
            "route_key": "reply",
            "provider": "newapi",
            "model": "model-1",
            "source": "replyer",
            "request_sha256": "a" * 64,
            "content": "不得入账",
        },
    )
    emitter.emit(
        "model.request",
        "succeeded",
        context=context,
        attributes={
            "route_key": "reply",
            "provider": "newapi",
            "model": "model-1",
            "source": "replyer",
            "response_sha256": "b" * 64,
            "response_bytes": 32,
        },
    )
    emitter.emit(
        "tool.execute",
        "started",
        context=context,
        attributes={
            "tool_name": "workspace_read",
            "args_sha256": "c" * 64,
            "args_bytes": 20,
        },
    )
    emitter.emit(
        "tool.execute",
        "succeeded",
        context=context,
        attributes={
            "tool_name": "workspace_read",
            "result_sha256": "d" * 64,
            "result_bytes": 10,
            "result_truncated": False,
        },
    )
    emitter.emit(
        "delivery.attempt",
        "started",
        context=context,
        attributes={
            "channel": "qq",
            "payload_sha256": "e" * 64,
            "payload_bytes": 8,
        },
    )
    emitter.emit(
        "delivery.attempt",
        "succeeded",
        context=context,
        attributes={
            "channel": "qq",
            "payload_sha256": "e" * 64,
            "payload_bytes": 8,
            "latency_ms": 5,
        },
    )
    writer.append(_event("event-terminal", "run.terminated", status="succeeded"))

    with factory() as db:
        records = SqlAlchemyRunEventLedger(db).read("run-1")
    projection = project_run_ledger(records)
    assert projection is not None
    assert projection.model_request_count == 1
    assert projection.tool_call_count == 1
    assert projection.delivery_attempt_count == 1
    stored = "\n".join(str(dict(record.payload)) for record in records)
    assert "不得入账" not in stored
    assert [record.sequence for record in records] == list(
        range(1, len(records) + 1)
    )


@pytest.mark.asyncio
async def test_permission_decorator_persists_decision_before_return(db_session):
    factory = sessionmaker(bind=db_session.get_bind())
    writer = SqlAlchemyRunEventLedgerWriter(factory)
    writer.append(
        _event(
            "event-accepted",
            "run.accepted",
            status="accepted",
            owner_id="user-1",
        )
    )
    port = LedgeredPermissionPort(
        StaticPermissionPort({
            "workspace.write": RuntimePermissionOutcome.ALLOW_ONCE,
        }),
        factory,
    )
    identity = RuntimeRunIdentity(
        run_id="run-1",
        turn_id="turn-1",
        correlation_id="trace-1",
        actor=RuntimeActor(RuntimeActorType.USER, "user-1"),
        owner=RuntimePrincipal(
            platform="qq",
            owner_type=RuntimeOwnerType.USER,
            owner_id="user-1",
        ),
    )
    decision = await port.evaluate(RuntimePermissionRequest(
        request_id="permission-request-1",
        identity=identity,
        action="workspace.write",
        resource="private/path/不得入账.txt",
        risk=RuntimePermissionRisk.HIGH,
        requested_at=datetime(2026, 8, 4, 12, 3, tzinfo=timezone.utc),
    ))
    assert decision.outcome is RuntimePermissionOutcome.ALLOW_ONCE
    with factory() as db:
        records = SqlAlchemyRunEventLedger(db).read("run-1")
    assert records[-1].event_type == "permission.decided"
    assert records[-1].status == "allow_once"
    assert "private/path" not in str(dict(records[-1].payload))
    assert records[-1].payload["resource_sha256"]


def test_run_ledger_migration_is_idempotent_and_database_append_only():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    run_schema_migrations(engine)
    run_schema_migrations(engine)

    inspector = inspect(engine)
    assert {
        "run_ledger_events",
        "run_ledger_stream_heads",
    } <= set(inspector.get_table_names())
    indexes = {
        index["name"]
        for index in inspector.get_indexes("run_ledger_events")
    }
    assert {
        "ix_run_ledger_run_time",
        "ix_run_ledger_type_time",
    } <= indexes

    local_factory = sessionmaker(bind=engine)
    writer = SqlAlchemyRunEventLedgerWriter(local_factory)
    writer.append(_event("event-immutable", "run.accepted", status="accepted"))
    with engine.begin() as conn:
        with pytest.raises(DatabaseError, match="append_only"):
            conn.execute(text(
                "UPDATE run_ledger_events SET status='changed' "
                "WHERE event_id='event-immutable'"
            ))
    with engine.begin() as conn:
        with pytest.raises(DatabaseError, match="append_only"):
            conn.execute(text(
                "DELETE FROM run_ledger_events "
                "WHERE event_id='event-immutable'"
            ))
    with engine.connect() as conn:
        version_count = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260804_run_event_ledger_v1'"
        )).scalar_one()
    assert version_count == 1
    engine.dispose()
