from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _descriptor():
    from core.runtime.events import RuntimeEventDescriptor, RuntimeEventField

    return RuntimeEventDescriptor(
        name="test.operation",
        domain="test",
        phases=("started", "succeeded", "failed"),
        fields=(
            RuntimeEventField("operation", "identifier", required=True),
            RuntimeEventField("latency_ms", "duration_ms"),
            RuntimeEventField("payload_sha256", "digest"),
        ),
    )


def test_runtime_event_registry_rejects_collision_and_mutation_after_freeze():
    from core.runtime.events import RuntimeEventRegistry, RuntimeEventRegistryError

    registry = RuntimeEventRegistry((_descriptor(),))
    with pytest.raises(RuntimeEventRegistryError, match="重复注册"):
        registry.register(_descriptor())

    registry.freeze()
    with pytest.raises(RuntimeEventRegistryError, match="已冻结"):
        registry.register(
            type(_descriptor())(
                name="test.other",
                domain="test",
                phases=("started",),
            )
        )


def test_runtime_event_emitter_whitelists_fields_and_drops_sensitive_payloads():
    from core.runtime.events import (
        InMemoryRuntimeEventSink,
        RuntimeEventContext,
        RuntimeEventEmitter,
        RuntimeEventRegistry,
    )

    registry = RuntimeEventRegistry((_descriptor(),)).freeze()
    sink = InMemoryRuntimeEventSink()
    emitter = RuntimeEventEmitter(
        registry,
        (sink,),
        now=lambda: datetime(2026, 7, 21, tzinfo=timezone.utc),
        event_id_factory=lambda: "evt_test",
    )

    event = emitter.emit(
        "test.operation",
        "succeeded",
        context=RuntimeEventContext(trace_id="trace_1", run_id="run_1"),
        attributes={
            "operation": "compile",
            "latency_ms": 12.9,
            "payload_sha256": "a" * 64,
            "content": "不得进入事件",
            "authorization": "Bearer secret",
            "unknown": "also omitted",
        },
    )

    assert event.event_id == "evt_test"
    assert dict(event.attributes) == {
        "operation": "compile",
        "latency_ms": 12,
        "payload_sha256": "a" * 64,
    }
    assert event.dropped_attribute_count == 3
    assert event.context.trace_id == "trace_1"
    assert sink.events == (event,)
    with pytest.raises(TypeError):
        event.attributes["operation"] = "tampered"


def test_runtime_event_required_field_and_phase_are_fail_closed():
    from core.runtime.events import RuntimeEventEmitter, RuntimeEventRegistry

    emitter = RuntimeEventEmitter(RuntimeEventRegistry((_descriptor(),)).freeze())
    with pytest.raises(ValueError, match="缺少合法必填字段"):
        emitter.emit("test.operation", "started", attributes={})
    with pytest.raises(ValueError, match="不允许 phase"):
        emitter.emit(
            "test.operation",
            "state_changed",
            attributes={"operation": "compile"},
        )


def test_runtime_event_sink_failure_isolated_by_default():
    from core.runtime.events import RuntimeEventEmitter, RuntimeEventRegistry

    class BrokenSink:
        def emit(self, event):
            raise RuntimeError("sink unavailable")

    emitter = RuntimeEventEmitter(
        RuntimeEventRegistry((_descriptor(),)).freeze(),
        (BrokenSink(),),
    )
    event = emitter.emit(
        "test.operation",
        "started",
        attributes={"operation": "compile"},
    )
    assert event.name == "test.operation"


def test_runtime_event_authority_failure_only_blocks_correlated_run():
    from core.run_ledger.contracts import RunLedgerAuthorityError
    from core.runtime.event_bus import (
        LoggingRuntimeEventSink,
        emit_runtime_event,
        install_runtime_event_sinks,
    )
    from core.runtime.events import RuntimeEventContext

    calls = []

    class BrokenAuthoritySink:
        def emit(self, event):
            calls.append(event.context.run_id)
            raise RuntimeError("ledger unavailable")

    install_runtime_event_sinks(
        (),
        authoritative_sinks=(BrokenAuthoritySink(),),
    )
    try:
        event = emit_runtime_event(
            "http.request",
            "started",
            attributes={"method": "GET", "route": "/health"},
        )
        assert event is not None
        assert calls == []

        with pytest.raises(RunLedgerAuthorityError) as raised:
            emit_runtime_event(
                "http.request",
                "started",
                context=RuntimeEventContext(run_id="run-authoritative"),
                attributes={"method": "GET", "route": "/runs/1"},
            )
        assert raised.value.run_id == "run-authoritative"
        assert raised.value.event_type == "http.request.started"
        assert calls == ["run-authoritative"]
    finally:
        install_runtime_event_sinks((LoggingRuntimeEventSink(),))


def test_default_runtime_event_registry_covers_cross_cutting_boundaries():
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY

    assert {descriptor.name for descriptor in RUNTIME_EVENT_REGISTRY.list()} == {
        "agent.lifecycle",
        "agent.runtime_selection",
        "compatibility.alias_used",
        "delivery.attempt",
        "http.request",
        "job.lifecycle",
        "memory.retrieve",
        "model.request",
        "prompt.compile",
        "task.execute",
        "tool.execute",
    }
