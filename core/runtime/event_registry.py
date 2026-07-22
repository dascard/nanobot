"""Nanobot 核心运行时事件的唯一 Descriptor Registry。"""

from __future__ import annotations

from core.runtime.events import (
    RuntimeEventDescriptor,
    RuntimeEventField,
    RuntimeEventRegistry,
)


def _field(
    name: str,
    kind: str,
    *,
    required: bool = False,
    max_chars: int = 128,
) -> RuntimeEventField:
    return RuntimeEventField(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        required=required,
        max_chars=max_chars,
    )


def build_runtime_event_registry() -> RuntimeEventRegistry:
    descriptors = (
        RuntimeEventDescriptor(
            name="agent.lifecycle",
            domain="agent",
            phases=("state_changed",),
            fields=(
                _field("runtime_id", "identifier", required=True),
                _field("previous_state", "label", required=True),
                _field("current_state", "label", required=True),
                _field("sequence", "count", required=True),
                _field("reason_type", "label"),
            ),
        ),
        RuntimeEventDescriptor(
            name="prompt.compile",
            domain="prompt",
            phases=("started", "succeeded", "failed"),
            fields=(
                _field("platform", "label"),
                _field("chat_type", "label"),
                _field("message_count", "count"),
                _field("section_count", "count"),
                _field("tool_count", "count"),
                _field("latency_ms", "duration_ms"),
                _field("request_sha256", "digest"),
                _field("error_type", "label"),
            ),
        ),
        RuntimeEventDescriptor(
            name="model.request",
            domain="model",
            phases=("started", "succeeded", "failed"),
            fields=(
                _field("provider", "label"),
                _field("model", "identifier", max_chars=192),
                _field("source", "label"),
                _field("candidate_index", "count"),
                _field("latency_ms", "duration_ms"),
                _field("request_sha256", "digest"),
                _field("response_sha256", "digest"),
                _field("error_type", "label"),
            ),
        ),
        RuntimeEventDescriptor(
            name="tool.execute",
            domain="tool",
            phases=("started", "succeeded", "failed"),
            fields=(
                _field("tool_name", "identifier", required=True),
                _field("latency_ms", "duration_ms"),
                _field("args_sha256", "digest"),
                _field("result_sha256", "digest"),
                _field("args_bytes", "count"),
                _field("result_bytes", "count"),
                _field("error_type", "label"),
            ),
        ),
        RuntimeEventDescriptor(
            name="memory.retrieve",
            domain="memory",
            phases=("started", "succeeded", "failed"),
            fields=(
                _field("provider", "identifier", required=True),
                _field("query_sha256", "digest"),
                _field("query_bytes", "count"),
                _field("selected_count", "count"),
                _field("latency_ms", "duration_ms"),
                _field("error_type", "label"),
            ),
        ),
        RuntimeEventDescriptor(
            name="delivery.attempt",
            domain="delivery",
            phases=("started", "succeeded", "failed"),
            fields=(
                _field("channel", "label", required=True),
                _field("target_type", "label"),
                _field("attempt_no", "count"),
                _field("payload_sha256", "digest"),
                _field("payload_bytes", "count"),
                _field("latency_ms", "duration_ms"),
                _field("error_type", "label"),
            ),
        ),
    )
    return RuntimeEventRegistry(descriptors).freeze()


RUNTIME_EVENT_REGISTRY = build_runtime_event_registry()
