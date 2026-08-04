from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_metric_registry_freezes_stable_names_and_label_allowlists():
    from core.telemetry import (
        TELEMETRY_METRIC_REGISTRY,
        MetricInstrument,
        TelemetryContractError,
        TelemetryMetricDescriptor,
    )

    snapshot = TELEMETRY_METRIC_REGISTRY.registry_snapshot
    assert snapshot.generation == 1
    assert set(snapshot.ordered_ids) == {
        "nanobot_runtime_event_duration_ms",
        "nanobot_runtime_events_total",
        "nanobot_runtime_jobs_active",
        "nanobot_runtime_jobs_total",
        "nanobot_runtime_telemetry_dropped_total",
    }
    descriptor = TELEMETRY_METRIC_REGISTRY.require(
        "nanobot_runtime_events_total"
    )
    assert descriptor.instrument is MetricInstrument.COUNTER
    assert descriptor.labels == (
        "domain",
        "event_name",
        "failure_code",
        "phase",
    )

    with pytest.raises(TelemetryContractError, match="指标名"):
        TelemetryMetricDescriptor(
            metric_name="Runtime.Events",
            instrument=MetricInstrument.COUNTER,
            unit="event",
            owner_module="runtime.agent",
        )
    with pytest.raises(TelemetryContractError, match="重复 label"):
        TelemetryMetricDescriptor(
            metric_name="nanobot_duplicate_labels_total",
            instrument=MetricInstrument.COUNTER,
            unit="event",
            owner_module="runtime.agent",
            labels=("phase", "phase"),
        )


def test_runtime_context_preserves_full_correlation_without_control_chars():
    from core.runtime.event_bus import current_runtime_event_context
    from core.tracing_context import (
        reset_runtime_correlation,
        set_runtime_correlation,
    )

    tokens = set_runtime_correlation(
        request_id="req-1",
        session_id="qq:private:u1",
        turn_id="turn-7",
        task_id="private_decision",
        task_run_id="taskrun-1",
        job_id="job-2",
        tool_call_id="tool-3",
        delivery_id="delivery-4",
        parent_job_id="job-parent",
    )
    try:
        context = current_runtime_event_context()
    finally:
        reset_runtime_correlation(tokens)

    assert context.request_id == "req-1"
    assert context.session_id == "qq:private:u1"
    assert context.turn_id == "turn-7"
    assert context.task_id == "private_decision"
    assert context.task_run_id == "taskrun-1"
    assert context.job_id == "job-2"
    assert context.tool_call_id == "tool-3"
    assert context.delivery_id == "delivery-4"
    assert context.parent_job_id == "job-parent"

    invalid_tokens = set_runtime_correlation(request_id="bad\nrequest")
    try:
        assert current_runtime_event_context().request_id == ""
    finally:
        reset_runtime_correlation(invalid_tokens)


def test_sqlalchemy_sink_persists_safe_event_and_full_correlation(db_session):
    from core.db.models.observability import RuntimeTelemetryEvent
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import RuntimeEventContext, RuntimeEventEmitter
    from core.telemetry import SqlAlchemyRuntimeEventSink

    factory = sessionmaker(bind=db_session.get_bind())
    sink = SqlAlchemyRuntimeEventSink(factory)
    emitter = RuntimeEventEmitter(
        RUNTIME_EVENT_REGISTRY,
        (sink,),
        now=lambda: datetime(2026, 7, 23, tzinfo=timezone.utc),
        event_id_factory=lambda: "evt_persisted",
        artifact_revision="git-abc123",
    )
    context = RuntimeEventContext(
        request_id="req-1",
        session_id="qq:private:u1",
        turn_id="turn-7",
        trace_id="trace-1",
        run_id="run-1",
        task_id="private_decision",
        task_run_id="taskrun-1",
        job_id="job-2",
        tool_call_id="tool-3",
        delivery_id="delivery-4",
        parent_job_id="job-parent",
    )

    event = emitter.emit(
        "task.execute",
        "failed",
        context=context,
        attributes={
            "task_id": "private_decision",
            "route_key": "private_decision",
            "failure_code": "schema_invalid",
            "input_sha256": "a" * 64,
            "output_bytes": 21,
            "prompt_cache_hit_tokens": 13,
            "prompt_cache_miss_tokens": 7,
            "content": "不得写入数据库的正文",
        },
    )

    db_session.expire_all()
    row = db_session.get(RuntimeTelemetryEvent, "evt_persisted")
    assert row is not None
    assert row.request_id == context.request_id
    assert row.session_id == context.session_id
    assert row.turn_id == context.turn_id
    assert row.trace_id == context.trace_id
    assert row.run_id == context.run_id
    assert row.task_id == context.task_id
    assert row.task_run_id == context.task_run_id
    assert row.job_id == context.job_id
    assert row.tool_call_id == context.tool_call_id
    assert row.delivery_id == context.delivery_id
    assert row.parent_job_id == context.parent_job_id
    assert row.registry_generation == (
        RUNTIME_EVENT_REGISTRY.registry_snapshot.generation
    )
    assert row.registry_sha256 == (
        RUNTIME_EVENT_REGISTRY.registry_snapshot.sha256
    )
    assert row.module_id == "runtime.task"
    assert row.module_version == "1.0.0"
    assert row.artifact_revision == "git-abc123"
    assert row.failure_code == "schema_invalid"
    assert row.dropped_attribute_count == 1
    stored = json.loads(row.attributes_json)
    assert stored["input_sha256"] == "a" * 64
    assert stored["prompt_cache_hit_tokens"] == 13
    assert stored["prompt_cache_miss_tokens"] == 7
    assert "不得写入" not in row.attributes_json
    assert event.provenance.registry_sha256 == row.registry_sha256

    # Sink 重试同一事件必须幂等，不能把 Observer 故障传回业务链。
    sink.emit(event)
    assert (
        db_session.query(RuntimeTelemetryEvent)
        .filter_by(event_id="evt_persisted")
        .count()
        == 1
    )


def test_telemetry_persistence_only_allows_integer_token_counts():
    from core.telemetry.persistence import _safe_attributes

    safe, dropped = _safe_attributes({
        "prompt_cache_hit_tokens": 13,
        "prompt_cache_miss_tokens": "不得写入",
        "api_token": "secret",
    })

    assert safe == {"prompt_cache_hit_tokens": 13}
    assert dropped == 2


def test_job_telemetry_projects_lease_retry_and_typed_failure():
    from core.jobs import JobCorrelation, JobLease
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import InMemoryRuntimeEventSink, RuntimeEventEmitter
    from core.telemetry import JobTelemetryEmitter

    sink = InMemoryRuntimeEventSink()
    emitter = JobTelemetryEmitter(RuntimeEventEmitter(
        RUNTIME_EVENT_REGISTRY,
        (sink,),
    ))
    correlation = JobCorrelation(
        request_id="req-job",
        session_id="qq:private:u1",
        turn_id="turn-9",
        trace_id="trace-job",
        run_id="run-job",
        task_id="memory_digest",
        tool_call_id="tool-job",
        delivery_id="delivery-job",
        parent_job_id="parent-job",
    )
    lease = JobLease(
        job_id="job-1",
        worker_id="worker-1",
        owner_token="secret-fencing-token",
        generation=2,
        attempt_no=3,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    event = emitter.emit_transition(
        job_type="memory_digest",
        transition="retry_scheduled",
        status="retry_wait",
        correlation=correlation,
        lease=lease,
        failure_code="execution_timeout",
        retry_scheduled=True,
    )

    assert event.context.job_id == "job-1"
    assert event.context.request_id == "req-job"
    assert event.context.parent_job_id == "parent-job"
    assert event.attributes["generation"] == 2
    assert event.attributes["attempt_no"] == 3
    assert event.attributes["lease_active"] is True
    assert event.attributes["retry_scheduled"] is True
    assert event.attributes["failure_code"] == "execution_timeout"
    assert "secret-fencing-token" not in repr(event)


def test_http_middleware_assigns_request_id_and_emits_route_contract():
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import InMemoryRuntimeEventSink, RuntimeEventEmitter
    from api.telemetry_middleware import TelemetryHttpMiddleware

    sink = InMemoryRuntimeEventSink()
    emitter = RuntimeEventEmitter(RUNTIME_EVENT_REGISTRY, (sink,))
    app = FastAPI()
    app.add_middleware(
        TelemetryHttpMiddleware,
        event_emitter=emitter,
        request_id_factory=lambda: "req-generated",
    )

    @app.get("/items/{item_id}")
    async def get_item(item_id: str):
        return {"item_id": item_id}

    with TestClient(app) as client:
        response = client.get(
            "/items/secret-user-value",
            headers={"X-Request-ID": "req-client"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-client"
    assert [event.phase for event in sink.events] == [
        "started",
        "succeeded",
    ]
    finished = sink.events[-1]
    assert finished.context.request_id == "req-client"
    assert finished.attributes["route"] == "/items/{item_id}"
    assert "secret-user-value" not in repr(sink.events)


def test_buffered_sink_flushes_without_blocking_business_thread():
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import RuntimeEventEmitter
    from core.telemetry.runtime import BufferedRuntimeEventSink

    delivered = []
    delivered_event = threading.Event()

    class Delegate:
        def emit_many(self, events):
            delivered.extend(events)
            delivered_event.set()

    sink = BufferedRuntimeEventSink(
        Delegate(),
        capacity=8,
        batch_size=4,
    )
    sink.start()
    event = RuntimeEventEmitter(
        RUNTIME_EVENT_REGISTRY,
        (),
    ).emit(
        "http.request",
        "started",
        attributes={"method": "GET"},
    )

    sink.emit(event)
    assert delivered_event.wait(timeout=1)
    sink.stop(timeout_seconds=1)

    assert delivered == [event]
    assert sink.dropped_count == 0
    assert sink.running is False


def test_buffered_sink_does_not_report_stopped_while_writer_is_blocked():
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import RuntimeEventEmitter
    from core.telemetry.runtime import BufferedRuntimeEventSink

    delegate_started = threading.Event()
    release_delegate = threading.Event()

    class BlockingDelegate:
        def emit_many(self, events):
            assert events
            delegate_started.set()
            release_delegate.wait(timeout=2)

    sink = BufferedRuntimeEventSink(
        BlockingDelegate(),
        capacity=1,
        batch_size=1,
    )
    sink.start()
    emitter = RuntimeEventEmitter(RUNTIME_EVENT_REGISTRY, ())
    first = emitter.emit(
        "http.request",
        "started",
        attributes={"method": "GET"},
    )
    second = emitter.emit(
        "http.request",
        "started",
        attributes={"method": "POST"},
    )
    sink.emit(first)
    assert delegate_started.wait(timeout=1)
    sink.emit(second)

    assert sink.stop(timeout_seconds=0.01) is False
    assert sink.running is True

    release_delegate.set()
    deadline = time.monotonic() + 1
    while sink.running and time.monotonic() < deadline:
        time.sleep(0.01)

    assert sink.running is False
    assert sink.dropped_count == 1


def test_runtime_stop_uninstalls_job_observer_without_buffered_sink(
    monkeypatch,
):
    from core.telemetry import runtime

    calls = []

    class Observer:
        def uninstall(self):
            calls.append("uninstalled")

    handle = runtime.TelemetryRuntimeHandle(
        buffered_sink=None,
        job_observer=Observer(),
    )
    monkeypatch.setattr(runtime, "_RUNTIME_HANDLE", handle)
    monkeypatch.setattr(
        runtime,
        "install_runtime_event_sinks",
        lambda sinks: calls.append(("sinks", len(sinks))),
    )

    runtime.stop_telemetry_runtime(handle)

    assert calls == ["uninstalled", ("sinks", 1)]
    assert handle.installed is False
    assert runtime._RUNTIME_HANDLE is None


def test_production_runtime_flushes_to_database_and_restores_logging_sink(
    tmp_path,
):
    from core.db.models.observability import RuntimeTelemetryEvent
    from core.db.models.run_ledger import RunLedgerEventRow
    from core.runtime.event_bus import emit_runtime_event
    from core.runtime.events import RuntimeEventContext
    from core.schema_migrations import run_schema_migrations
    from core.telemetry.runtime import (
        start_telemetry_runtime,
        stop_telemetry_runtime,
    )

    engine = create_engine(
        f"sqlite:///{tmp_path / 'runtime-telemetry.db'}",
        connect_args={"check_same_thread": False},
    )
    run_schema_migrations(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from core.run_ledger.adapters import run_accepted_event
    from core.run_ledger.persistence import SqlAlchemyRunEventLedgerWriter

    SqlAlchemyRunEventLedgerWriter(factory).append(run_accepted_event(
        run_id="run-production-runtime",
        trace_id="",
        session_id="qq:private:u1",
        user_id="u1",
        chat_type="private",
        group_id="",
        run_type="http_test",
        prompt_mode="none",
        prompt_key="",
        prompt_sha256="",
        model="",
        input_value="",
    ))
    handle = None
    try:
        handle = start_telemetry_runtime(
            session_factory=factory,
            capacity=8,
            batch_size=2,
        )
        event = emit_runtime_event(
            "http.request",
            "succeeded",
            context=RuntimeEventContext(
                request_id="request-production-runtime",
                session_id="qq:private:u1",
                run_id="run-production-runtime",
            ),
            attributes={
                "method": "GET",
                "route": "/api/v1/ready",
                "status_code": 200,
                "latency_ms": 3,
            },
        )
        assert event is not None
    finally:
        stop_telemetry_runtime(handle)

    with factory() as db:
        rows = db.query(RuntimeTelemetryEvent).all()
        ledger_rows = db.query(RunLedgerEventRow).all()
    assert len(rows) == 1
    assert rows[0].event_id == event.event_id
    assert rows[0].request_id == "request-production-runtime"
    assert len(ledger_rows) == 2
    assert ledger_rows[1].event_type == "http.request.succeeded"
    assert ledger_rows[1].run_id == "run-production-runtime"
    assert "/api/v1/ready" in ledger_rows[1].payload_json
    assert handle is not None
    assert handle.buffered_sink is not None
    assert handle.buffered_sink.running is False
    assert handle.job_observer.installed is False

    emit_runtime_event(
        "http.request",
        "succeeded",
        attributes={
            "method": "GET",
            "route": "/after-stop",
            "status_code": 200,
        },
    )
    with factory() as db:
        assert db.query(RuntimeTelemetryEvent).count() == 1
    engine.dispose()


def test_job_observer_emits_only_committed_lease_and_retry_transitions(
    db_session,
):
    from core.db.models.session_memory import SessionSummaryJob
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import InMemoryRuntimeEventSink, RuntimeEventEmitter
    from core.telemetry.job_observer import install_job_telemetry_observer

    sink = InMemoryRuntimeEventSink()
    observer = install_job_telemetry_observer(
        RuntimeEventEmitter(RUNTIME_EVENT_REGISTRY, (sink,))
    )
    try:
        rolled_back = SessionSummaryJob(
            session_id="qq:private:rolled-back",
            covered_from_turn_id=1,
            covered_until_turn_id=2,
            source_turn_ids_json="[1,2]",
            status="pending",
        )
        db_session.add(rolled_back)
        db_session.flush()
        db_session.rollback()
        assert sink.events == ()

        job = SessionSummaryJob(
            session_id="qq:private:u1",
            covered_from_turn_id=7,
            covered_until_turn_id=9,
            source_turn_ids_json="[7,8,9]",
            status="pending",
        )
        db_session.add(job)
        db_session.commit()

        job.status = "running"
        job.locked_by = "worker-1"
        job.lease_token = "secret-lease-token"
        job.lease_expires_at = datetime.now() + timedelta(minutes=1)
        job.generation = 1
        job.attempt_count = 1
        db_session.commit()

        job.status = "pending"
        job.lease_token = ""
        job.lease_expires_at = None
        job.next_retry_at = datetime.now() + timedelta(minutes=2)
        job.retry_count = 1
        db_session.commit()
    finally:
        observer.uninstall()

    assert [
        event.attributes["transition"]
        for event in sink.events
    ] == ["enqueued", "lease_claimed", "retry_scheduled"]
    claimed = sink.events[1]
    assert claimed.context.job_id == str(job.id)
    assert claimed.context.session_id == "qq:private:u1"
    assert claimed.context.turn_id == "9"
    assert claimed.attributes["lease_active"] is True
    retry = sink.events[2]
    assert retry.attributes["retry_scheduled"] is True
    assert retry.attributes["failure_code"] == "job.retry_scheduled"
    serialized = repr(sink.events)
    assert "secret-lease-token" not in serialized
    assert "[7,8,9]" not in serialized
