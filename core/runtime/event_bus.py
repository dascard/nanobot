"""RuntimeEvent 的进程内组合根与追踪上下文 Adapter。"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping

from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
from core.runtime.events import (
    RuntimeEvent,
    RuntimeEventContext,
    RuntimeEventEmitter,
    RuntimeEventPhase,
    RuntimeEventSink,
)
from core.runtime.extensions import (
    RuntimeExtensionKind,
    RuntimeFailurePolicy,
    RuntimeHookDescriptor,
    RuntimeObserverBinding,
    RuntimeObserverDispatcher,
)


logger = logging.getLogger("nanobot.runtime.events")


class LoggingRuntimeEventSink:
    def emit(self, event: RuntimeEvent) -> None:
        logger.info(
            "runtime_event name=%s phase=%s domain=%s dropped=%d attributes=%s",
            event.name,
            event.phase,
            event.domain,
            event.dropped_attribute_count,
            dict(event.attributes),
            extra={
                "request_id": event.context.request_id,
                "session_id": event.context.session_id,
                "turn_id": event.context.turn_id,
                "trace_id": event.context.trace_id,
                "run_id": event.context.run_id,
                "task_id": event.context.task_id,
                "task_run_id": event.context.task_run_id,
                "job_id": event.context.job_id,
                "tool_call_id": event.context.tool_call_id,
                "delivery_id": event.context.delivery_id,
                "parent_job_id": event.context.parent_job_id,
                "runtime_event_id": event.event_id,
                "runtime_event_registry_generation": (
                    event.provenance.registry_generation
                ),
                "runtime_event_registry_sha256": (
                    event.provenance.registry_sha256
                ),
                "runtime_event_module_id": event.provenance.module_id,
                "runtime_event_module_version": (
                    event.provenance.module_version
                ),
                "runtime_artifact_revision": (
                    event.provenance.artifact_revision
                ),
            },
        )


class _LoggingRuntimeEventObserver:
    def __init__(self) -> None:
        self._sink = LoggingRuntimeEventSink()

    def observe(self, event: object) -> None:
        if not isinstance(event, RuntimeEvent):
            raise TypeError("Logging Observer 只接受 RuntimeEvent")
        self._sink.emit(event)


LOGGING_RUNTIME_EVENT_OBSERVER_DESCRIPTOR = RuntimeHookDescriptor(
    hook_id="runtime.logging",
    kind=RuntimeExtensionKind.OBSERVER,
    owner_module="runtime.agent",
    domain="runtime",
    input_contract="runtime.event.v1",
    output_contract="none",
    priority=100,
    failure_policy=RuntimeFailurePolicy.FAIL_OPEN,
    trusted_builtin=True,
)


_LOCK = threading.Lock()
_AUTHORITATIVE_SINKS: tuple[RuntimeEventSink, ...] = ()
_EMITTER = RuntimeEventEmitter(
    RUNTIME_EVENT_REGISTRY,
    observer_dispatcher=RuntimeObserverDispatcher((
        RuntimeObserverBinding(
            LOGGING_RUNTIME_EVENT_OBSERVER_DESCRIPTOR,
            _LoggingRuntimeEventObserver(),
        ),
    )),
)


def install_runtime_event_sinks(
    sinks: tuple[RuntimeEventSink, ...],
    *,
    authoritative_sinks: tuple[RuntimeEventSink, ...] = (),
) -> RuntimeEventEmitter:
    """由组合根原子替换观测与权威 Sink。"""

    global _AUTHORITATIVE_SINKS, _EMITTER
    emitter = RuntimeEventEmitter(RUNTIME_EVENT_REGISTRY, sinks)
    with _LOCK:
        _EMITTER = emitter
        _AUTHORITATIVE_SINKS = tuple(authoritative_sinks)
    return emitter


def get_runtime_event_emitter() -> RuntimeEventEmitter:
    with _LOCK:
        return _EMITTER


def get_authoritative_runtime_event_sinks() -> tuple[RuntimeEventSink, ...]:
    with _LOCK:
        return _AUTHORITATIVE_SINKS


def current_runtime_event_context() -> RuntimeEventContext:
    from core.tracing_context import get_runtime_correlation

    return get_runtime_correlation()


def emit_runtime_event(
    name: str,
    phase: RuntimeEventPhase,
    *,
    attributes: Mapping[str, object] | None = None,
    context: RuntimeEventContext | None = None,
) -> RuntimeEvent | None:
    """无 Run 事件可降级；关联 Run 的权威入账失败必须中止业务。"""

    resolved_context = context or current_runtime_event_context()
    try:
        event = get_runtime_event_emitter().emit(
            name,
            phase,
            context=resolved_context,
            attributes=attributes,
        )
    except Exception as exc:
        if resolved_context.run_id:
            from core.run_ledger.contracts import RunLedgerAuthorityError

            raise RunLedgerAuthorityError(
                "关联 Run 的 RuntimeEvent 合同或观测投递失败",
                run_id=resolved_context.run_id,
                event_type=f"{name}.{phase}",
                code="runtime_event_invalid",
            ) from exc
        logger.exception("RuntimeEvent 投递失败", extra={"event_name": name})
        return None
    authoritative_sinks = get_authoritative_runtime_event_sinks()
    if (
        resolved_context.run_id
        and not authoritative_sinks
        and os.environ.get("NANOBOT_TESTING") != "1"
    ):
        from core.run_ledger.contracts import RunLedgerAuthorityError

        raise RunLedgerAuthorityError(
            "关联 Run 的权威 RuntimeEvent Sink 未安装",
            run_id=resolved_context.run_id,
            event_type=f"{name}.{phase}",
            code="ledger_sink_unavailable",
        )
    if resolved_context.run_id:
        for sink in authoritative_sinks:
            try:
                sink.emit(event)
            except Exception as exc:
                from core.run_ledger.contracts import RunLedgerAuthorityError

                if isinstance(exc, RunLedgerAuthorityError):
                    raise
                raise RunLedgerAuthorityError(
                    "关联 Run 的 RuntimeEvent 权威入账失败",
                    run_id=resolved_context.run_id,
                    event_type=f"{name}.{phase}",
                ) from exc
    return event


def emit_agent_lifecycle_event(event: object) -> None:
    """把 AgentRuntime 合同事件映射到统一事件总线，避免反向耦合合同包。"""

    previous = getattr(event, "previous_state", "")
    current = getattr(event, "current_state", "")
    emit_runtime_event(
        "agent.lifecycle",
        "state_changed",
        attributes={
            "runtime_id": str(getattr(event, "runtime_id", "") or ""),
            "previous_state": str(getattr(previous, "value", previous) or ""),
            "current_state": str(getattr(current, "value", current) or ""),
            "sequence": int(getattr(event, "sequence", 0) or 0),
            "reason_type": str(getattr(event, "reason", "") or ""),
        },
    )
