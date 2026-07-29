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
            owner_module="runtime.agent",
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
            owner_module="prompt.runtime",
            phases=("started", "succeeded", "failed"),
            fields=(
                _field("prompt_key", "identifier", max_chars=192),
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
            owner_module="model.provider",
            phases=("started", "succeeded", "failed"),
            fields=(
                _field("route_key", "identifier"),
                _field("provider", "label"),
                _field("model", "identifier", max_chars=192),
                _field("source", "label"),
                _field("candidate_index", "count"),
                _field("latency_ms", "duration_ms"),
                _field("request_sha256", "digest"),
                _field("response_sha256", "digest"),
                _field("response_bytes", "count"),
                _field("response_truncated", "boolean"),
                _field("failure_code", "label"),
                _field("error_type", "label"),
            ),
        ),
        RuntimeEventDescriptor(
            name="task.execute",
            domain="task",
            owner_module="runtime.task",
            phases=("started", "succeeded", "failed"),
            fields=(
                _field("task_id", "identifier", required=True),
                _field("route_key", "identifier", required=True),
                _field("contract_version", "label"),
                _field("slo_id", "identifier"),
                _field("slo_version", "label"),
                _field("slo_status", "label"),
                _field("attempt_count", "count"),
                _field("latency_ms", "duration_ms"),
                _field("input_sha256", "digest"),
                _field("input_bytes", "count"),
                _field("input_chars", "count"),
                _field("input_token_estimate", "count"),
                _field("input_tokens", "count"),
                _field("prompt_cache_hit_tokens", "count"),
                _field("prompt_cache_miss_tokens", "count"),
                _field("output_sha256", "digest"),
                _field("output_bytes", "count"),
                _field("output_tokens", "count"),
                _field("total_tokens", "count"),
                _field("output_truncated", "boolean"),
                _field("failure_code", "label"),
                _field("provider", "label"),
                _field("model", "identifier", max_chars=192),
                _field("terminal_action", "label"),
            ),
        ),
        RuntimeEventDescriptor(
            name="tool.execute",
            domain="tool",
            owner_module="tool.runtime",
            phases=("started", "succeeded", "failed"),
            fields=(
                _field("tool_name", "identifier", required=True),
                _field("latency_ms", "duration_ms"),
                _field("args_sha256", "digest"),
                _field("result_sha256", "digest"),
                _field("args_bytes", "count"),
                _field("result_bytes", "count"),
                _field("result_truncated", "boolean"),
                _field("failure_code", "label"),
                _field("error_type", "label"),
            ),
        ),
        RuntimeEventDescriptor(
            name="memory.retrieve",
            domain="memory",
            owner_module="memory.runtime",
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
            owner_module="delivery.outbound",
            phases=("started", "succeeded", "failed"),
            fields=(
                _field("channel", "label", required=True),
                _field("target_type", "label"),
                _field("attempt_no", "count"),
                _field("payload_sha256", "digest"),
                _field("payload_bytes", "count"),
                _field("latency_ms", "duration_ms"),
                _field("failure_code", "label"),
                _field("error_type", "label"),
            ),
        ),
        RuntimeEventDescriptor(
            name="http.request",
            domain="http",
            owner_module="runtime.telemetry",
            phases=("started", "succeeded", "failed"),
            fields=(
                _field("method", "label", required=True),
                _field("route", "identifier", max_chars=256),
                _field("status_code", "count"),
                _field("latency_ms", "duration_ms"),
                _field("failure_code", "label"),
                _field("error_type", "label"),
            ),
        ),
        RuntimeEventDescriptor(
            name="job.lifecycle",
            domain="job",
            owner_module="runtime.telemetry",
            phases=("state_changed",),
            fields=(
                _field("job_type", "identifier", required=True),
                _field("transition", "label", required=True),
                _field("status", "label", required=True),
                _field("generation", "count"),
                _field("attempt_no", "count"),
                _field("lease_active", "boolean"),
                _field("retry_scheduled", "boolean"),
                _field("failure_code", "label"),
            ),
        ),
        RuntimeEventDescriptor(
            name="compatibility.alias_used",
            domain="compatibility",
            owner_module="runtime.agent",
            phases=("state_changed",),
            fields=(
                _field(
                    "compatibility_id",
                    "identifier",
                    required=True,
                ),
                _field("kind", "label", required=True),
                _field("warning_policy", "label", required=True),
                _field("usage_count", "count", required=True),
            ),
        ),
    )
    return RuntimeEventRegistry(descriptors).freeze()


RUNTIME_EVENT_REGISTRY = build_runtime_event_registry()
