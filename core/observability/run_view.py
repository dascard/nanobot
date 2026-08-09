"""从持久证据离线构建统一 Run 时间线、DAG 与用量瀑布。

本模块只接受已经脱敏的标量和 Context Manifest，不依赖数据库或 Web 框架。
调用方不得把 Prompt、消息、工具参数、工具结果、命令或模型隐藏推理传入。
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


RUN_VIEW_SCHEMA_VERSION = "1.0"

_SUCCEEDED_STATUSES = frozenset({
    "completed",
    "delivered",
    "ok",
    "success",
    "succeeded",
    "stream_success",
})
_FAILED_STATUSES = frozenset({
    "ambiguous",
    "cancelled",
    "error",
    "failed",
    "failure",
    "stream_error",
    "timed_out",
    "timeout",
})
_RUNNING_STATUSES = frozenset({
    "created",
    "pending",
    "prepared",
    "running",
    "started",
    "stream_created",
    "waiting_approval",
    "waiting_input",
})
_SAFE_ATTRIBUTE_NAMES = frozenset({
    "ambiguous",
    "artifact_revision",
    "attempt_count",
    "attempt_no",
    "boundary",
    "bucket",
    "cache_hit",
    "cache_status",
    "candidate_index",
    "channel",
    "changed",
    "child_run_id",
    "contract_version",
    "cost_source",
    "current_state",
    "effect_class",
    "error_category",
    "error_type",
    "execution_mode",
    "failure_code",
    "failure_policy",
    "image_digest",
    "is_error",
    "job_type",
    "lease_active",
    "model",
    "module_id",
    "module_version",
    "operation_kind",
    "phase",
    "platform",
    "policy_id",
    "policy_profile",
    "previous_state",
    "profile_id",
    "prompt_key",
    "prompt_mode",
    "provider",
    "read_only",
    "registry_generation",
    "resumable",
    "retry_scheduled",
    "retryable",
    "role_id",
    "route_key",
    "schema_version",
    "scope",
    "selected_runtime",
    "selection_reason",
    "server_id",
    "source",
    "source_kind",
    "state",
    "status",
    "stop",
    "target_type",
    "task_id",
    "terminal_action",
    "termination_reason",
    "tool_name",
    "transition",
    "version",
})
_SAFE_ATTRIBUTE_SUFFIXES = (
    "_bytes",
    "_chars",
    "_count",
    "_id",
    "_index",
    "_ms",
    "_sha256",
    "_tokens",
    "_truncated",
)


@dataclass(frozen=True, slots=True)
class RunViewSource:
    """统一 Viewer 的已脱敏输入快照。"""

    run_id: str
    run: Mapping[str, Any]
    ledger_projection: Mapping[str, Any] | None = None
    ledger_records: Sequence[Mapping[str, Any]] = ()
    tool_calls: Sequence[Mapping[str, Any]] = ()
    prompt_logs: Sequence[Mapping[str, Any]] = ()
    llm_requests: Sequence[Mapping[str, Any]] = ()
    runtime_events: Sequence[Mapping[str, Any]] = ()
    sandbox_runs: Sequence[Mapping[str, Any]] = ()
    artifacts: Sequence[Mapping[str, Any]] = ()
    checkpoints: Sequence[Mapping[str, Any]] = ()
    side_effects: Sequence[Mapping[str, Any]] = ()
    recovery_operations: Sequence[Mapping[str, Any]] = ()
    context_manifest: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        run_id = str(self.run_id or "").strip()
        if not run_id:
            raise ValueError("run_id 不能为空")
        object.__setattr__(self, "run_id", run_id)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, parsed)


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _duration_ms(
    started_at: datetime | None,
    finished_at: datetime | None,
    declared: object = 0,
) -> int:
    declared_ms = _nonnegative_int(declared)
    if declared_ms:
        return declared_ms
    if started_at is None or finished_at is None:
        return 0
    return max(0, int((finished_at - started_at).total_seconds() * 1000))


def _status(value: object) -> str:
    normalized = str(value or "unknown").strip().lower() or "unknown"
    if normalized in _SUCCEEDED_STATUSES:
        return "succeeded"
    if normalized in _FAILED_STATUSES:
        return normalized if normalized in {"ambiguous", "cancelled", "timed_out"} else "failed"
    if normalized in _RUNNING_STATUSES:
        return "running"
    return normalized[:32]


def _failure(
    status: str,
    *,
    code: object = "",
    error_type: object = "",
    retryable: object = False,
) -> dict[str, Any] | None:
    if status not in {"ambiguous", "cancelled", "failed", "timed_out"}:
        return None
    normalized_code = str(code or status).strip()[:128] or status
    return {
        "code": normalized_code,
        "error_type": str(error_type or "").strip()[:128],
        "retryable": retryable is True,
    }


def _safe_attributes(value: object) -> dict[str, Any]:
    attributes = _json_mapping(value)
    safe: dict[str, Any] = {}
    for raw_key, raw_value in attributes.items():
        key = str(raw_key or "").strip()
        if (
            not key
            or len(key) > 64
            or (
                key not in _SAFE_ATTRIBUTE_NAMES
                and not key.endswith(_SAFE_ATTRIBUTE_SUFFIXES)
            )
            or type(raw_value) not in {bool, int, float, str}
        ):
            continue
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if len(text) > 256 or any(ord(char) < 32 for char in text):
                continue
            safe[key] = text
        else:
            safe[key] = raw_value
    return safe


def _span(
    *,
    span_id: str,
    parent_span_id: str,
    kind: str,
    name: str,
    status: object,
    run_id: str,
    trace_id: object = "",
    turn_id: object = "",
    started_at: object = None,
    finished_at: object = None,
    duration_ms: object = 0,
    attempt: object = 0,
    metrics: Mapping[str, Any] | None = None,
    attributes: Mapping[str, Any] | None = None,
    failure: Mapping[str, Any] | None = None,
    version: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    start = _timestamp(started_at)
    finish = _timestamp(finished_at)
    normalized_status = _status(status)
    return {
        "span_id": str(span_id),
        "parent_span_id": str(parent_span_id or ""),
        "kind": str(kind or "runtime")[:48],
        "name": str(name or kind or "runtime")[:192],
        "status": normalized_status,
        "run_id": run_id,
        "trace_id": str(trace_id or "")[:160],
        "turn_id": str(turn_id or "")[:160],
        "started_at": _iso(start),
        "finished_at": _iso(finish),
        "duration_ms": _duration_ms(start, finish, duration_ms),
        "offset_ms": 0,
        "attempt": _nonnegative_int(attempt),
        "metrics": {
            str(key): value
            for key, value in dict(metrics or {}).items()
            if type(value) in {bool, int, float, str}
        },
        "attributes": _safe_attributes(attributes or {}),
        "failure": dict(failure) if failure else None,
        "version": _safe_attributes(version or {}),
    }


def _runtime_event_identity(event: Mapping[str, Any]) -> tuple[str, ...]:
    attrs = _safe_attributes(event.get("attributes"))
    correlation = tuple(
        str(event.get(key) or "")
        for key in (
            "delivery_id",
            "tool_call_id",
            "task_id",
            "task_run_id",
            "job_id",
            "parent_job_id",
        )
    )
    discriminators = tuple(
        str(attrs.get(key) or "")
        for key in (
            "attempt_no",
            "prompt_key",
            "provider",
            "request_sha256",
            "server_id",
            "tool_name",
        )
    )
    return (str(event.get("name") or "runtime.event"), *correlation, *discriminators)


def _pair_runtime_events(
    events: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any] | None]]:
    ordered = sorted(
        (dict(item) for item in events),
        key=lambda item: _timestamp(item.get("occurred_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    pending: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    pairs: list[tuple[Mapping[str, Any], Mapping[str, Any] | None]] = []
    for event in ordered:
        phase = str(event.get("phase") or "")
        key = _runtime_event_identity(event)
        if phase == "started":
            pending[key].append(event)
            continue
        if phase in {"succeeded", "failed"} and pending[key]:
            pairs.append((pending[key].pop(0), event))
            continue
        pairs.append((event, None))
    for starts in pending.values():
        pairs.extend((event, None) for event in starts)
    return pairs


def _runtime_kind(event: Mapping[str, Any]) -> str:
    name = str(event.get("name") or "")
    if name == "subagent.execute":
        return "subagent"
    if name == "mcp.call":
        return "mcp"
    return str(event.get("domain") or "runtime")[:48]


def _runtime_name(event: Mapping[str, Any], attrs: Mapping[str, Any]) -> str:
    event_name = str(event.get("name") or "runtime.event")
    if event_name == "tool.execute":
        return str(attrs.get("tool_name") or event_name)
    if event_name == "memory.retrieve":
        return f"memory:{attrs.get('provider') or 'default'}"
    if event_name == "delivery.attempt":
        return f"delivery:{attrs.get('channel') or 'unknown'}"
    if event_name == "mcp.call":
        return (
            f"mcp:{attrs.get('server_id') or 'unknown'}/"
            f"{attrs.get('tool_name') or 'tool'}"
        )
    if event_name == "subagent.execute":
        return (
            f"subagent:{attrs.get('role_id') or 'worker'}/"
            f"{attrs.get('task_id') or 'task'}"
        )
    return event_name


def _latest_prompt_parent(
    prompt_spans: Sequence[Mapping[str, Any]],
    started_at: object,
) -> str:
    target = _timestamp(started_at)
    candidates = [
        span
        for span in prompt_spans
        if _timestamp(span.get("started_at")) is not None
        and (
            target is None
            or _timestamp(span.get("started_at")) <= target
        )
    ]
    if not candidates:
        return ""
    candidates.sort(
        key=lambda span: _timestamp(span.get("started_at"))
        or datetime.min.replace(tzinfo=timezone.utc)
    )
    return str(candidates[-1].get("span_id") or "")


def _build_base_spans(
    source: RunViewSource,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    run = _mapping(source.run)
    projection = _mapping(source.ledger_projection)
    root_id = f"run:{source.run_id}"
    root_started = projection.get("started_at") or run.get("started_at")
    root_finished = projection.get("finished_at") or run.get("finished_at")
    root_status = projection.get("status") or run.get("status")
    trace_id = run.get("trace_id") or next(
        (
            record.get("trace_id")
            for record in source.ledger_records
            if record.get("trace_id")
        ),
        "",
    )
    turn_id = next(
        (
            str(record.get("turn_id") or "")
            for record in source.ledger_records
            if record.get("turn_id")
        ),
        "",
    )
    spans = [_span(
        span_id=root_id,
        parent_span_id="",
        kind="run",
        name=str(run.get("run_type") or "agent_run"),
        status=root_status,
        run_id=source.run_id,
        trace_id=trace_id,
        turn_id=turn_id,
        started_at=root_started,
        finished_at=root_finished,
        duration_ms=run.get("latency_ms"),
        attributes={
            "prompt_mode": run.get("prompt_mode") or projection.get("prompt_mode"),
            "prompt_key": run.get("prompt_key") or projection.get("prompt_key"),
            "model": run.get("model"),
            "status": root_status,
        },
        failure=_failure(
            _status(root_status),
            code=(projection.get("error_codes") or [""])[0]
            if isinstance(projection.get("error_codes"), list)
            and projection.get("error_codes")
            else "",
        ),
        version={
            "prompt_sha256": run.get("prompt_sha256")
            or projection.get("prompt_sha256"),
        },
    )]
    tool_span_ids: dict[str, str] = {}
    for index, row in enumerate(source.tool_calls, start=1):
        item = _mapping(row)
        tool_call_id = str(item.get("tool_call_id") or "")
        span_id = f"tool:{tool_call_id or index}"
        if tool_call_id:
            tool_span_ids[tool_call_id] = span_id
        normalized = _status(item.get("status"))
        spans.append(_span(
            span_id=span_id,
            parent_span_id=root_id,
            kind="tool",
            name=str(item.get("tool_name") or "tool"),
            status=normalized,
            run_id=source.run_id,
            trace_id=item.get("trace_id") or trace_id,
            turn_id=item.get("turn_id") or turn_id,
            started_at=item.get("started_at"),
            finished_at=item.get("finished_at"),
            duration_ms=item.get("latency_ms"),
            metrics={},
            attributes={"tool_name": item.get("tool_name")},
            failure=_failure(
                normalized,
                code="tool_execution_failed",
                error_type="tool_error" if item.get("error_present") else "",
            ),
        ))

    prompt_spans: list[dict[str, Any]] = []
    for index, row in enumerate(source.prompt_logs, start=1):
        item = _mapping(row)
        span = _span(
            span_id=f"prompt:{item.get('id') or index}",
            parent_span_id=root_id,
            kind="prompt",
            name=str(item.get("prompt_key") or "prompt.render"),
            status="failed" if item.get("error_present") else "succeeded",
            run_id=source.run_id,
            trace_id=item.get("trace_id") or trace_id,
            turn_id=item.get("turn_id") or turn_id,
            started_at=item.get("created_at"),
            finished_at=item.get("created_at"),
            metrics={"token_estimate": _nonnegative_int(item.get("token_estimate"))},
            attributes={
                "prompt_key": item.get("prompt_key"),
                "prompt_mode": item.get("mode"),
                "source": item.get("prompt_source"),
            },
            failure=_failure(
                "failed" if item.get("error_present") else "succeeded",
                code="prompt_render_failed",
            ),
            version={"prompt_sha256": item.get("prompt_sha256")},
        )
        prompt_spans.append(span)
        spans.append(span)

    llm_spans: list[dict[str, Any]] = []
    for index, row in enumerate(source.llm_requests, start=1):
        item = _mapping(row)
        normalized = _status(item.get("status"))
        attempt = _nonnegative_int(item.get("route_attempt_index")) + 1
        parent_id = _latest_prompt_parent(prompt_spans, item.get("created_at"))
        span = _span(
            span_id=f"llm:{item.get('id') or index}",
            parent_span_id=parent_id or root_id,
            kind="llm",
            name=(
                f"{item.get('provider') or 'provider'}/"
                f"{item.get('model') or 'model'}"
            ),
            status=normalized,
            run_id=source.run_id,
            trace_id=item.get("trace_id") or trace_id,
            turn_id=item.get("turn_id") or turn_id,
            started_at=item.get("created_at"),
            finished_at=item.get("finished_at"),
            duration_ms=item.get("latency_ms"),
            attempt=attempt,
            metrics={
                "input_tokens": _nonnegative_int(item.get("input_tokens")),
                "output_tokens": _nonnegative_int(item.get("output_tokens")),
                "cache_hit_tokens": _nonnegative_int(item.get("cache_hit_tokens")),
                "cache_miss_tokens": _nonnegative_int(item.get("cache_miss_tokens")),
                "cache_write_tokens": _nonnegative_int(item.get("cache_write_tokens")),
                "first_token_latency_ms": _nonnegative_int(
                    item.get("first_token_latency_ms")
                ),
                "cost_microusd": _nonnegative_int(item.get("cost_microusd")),
            },
            attributes={
                "provider": item.get("provider"),
                "model": item.get("model"),
                "source": item.get("source"),
                "phase": item.get("phase"),
                "round_index": item.get("round_index"),
                "route_attempt_index": item.get("route_attempt_index"),
                "cache_status": item.get("cache_status"),
                "cache_hit": item.get("cache_hit"),
                "error_category": item.get("error_category"),
                "cost_source": item.get("cost_source"),
            },
            failure=_failure(
                normalized,
                code=item.get("error_category") or "model_request_failed",
                error_type=item.get("error_category"),
                retryable=(
                    str(item.get("error_category") or "")
                    in {"network", "rate_limit", "server", "timeout"}
                ),
            ),
            version={
                "provider": item.get("provider"),
                "model": item.get("model"),
            },
        )
        llm_spans.append(span)
        spans.append(span)
        cache_tokens = sum(
            _nonnegative_int(item.get(key))
            for key in (
                "cache_hit_tokens",
                "cache_miss_tokens",
                "cache_write_tokens",
            )
        )
        cache_status = str(item.get("cache_status") or "pending")
        if cache_tokens or cache_status not in {"", "pending"}:
            spans.append(_span(
                span_id=f"cache:{item.get('id') or index}",
                parent_span_id=span["span_id"],
                kind="cache",
                name=f"model_cache:{cache_status}",
                status=(
                    "failed" if cache_status == "error" else "succeeded"
                ),
                run_id=source.run_id,
                trace_id=item.get("trace_id") or trace_id,
                turn_id=item.get("turn_id") or turn_id,
                started_at=item.get("created_at"),
                finished_at=item.get("finished_at"),
                metrics={
                    "cache_hit_tokens": _nonnegative_int(
                        item.get("cache_hit_tokens")
                    ),
                    "cache_miss_tokens": _nonnegative_int(
                        item.get("cache_miss_tokens")
                    ),
                    "cache_write_tokens": _nonnegative_int(
                        item.get("cache_write_tokens")
                    ),
                },
                attributes={
                    "cache_status": cache_status,
                    "cache_hit": item.get("cache_hit"),
                    "provider": item.get("provider"),
                },
                failure=_failure(
                    "failed" if cache_status == "error" else "succeeded",
                    code="model_cache_error",
                ),
            ))
    return spans, tool_span_ids, llm_spans


def _append_runtime_spans(
    source: RunViewSource,
    spans: list[dict[str, Any]],
    tool_span_ids: Mapping[str, str],
) -> None:
    root_id = f"run:{source.run_id}"
    existing_llm = any(span["kind"] == "llm" for span in spans)
    for index, (started, terminal) in enumerate(
        _pair_runtime_events(source.runtime_events),
        start=1,
    ):
        event = terminal or started
        event_name = str(event.get("name") or "runtime.event")
        tool_call_id = str(event.get("tool_call_id") or "")
        if event_name == "tool.execute" and tool_call_id in tool_span_ids:
            continue
        if event_name == "model.request" and existing_llm:
            continue
        attrs = {
            **_safe_attributes(started.get("attributes")),
            **_safe_attributes(event.get("attributes")),
        }
        phase = str(event.get("phase") or "")
        if phase == "succeeded":
            normalized = "succeeded"
        elif phase == "failed":
            failure_status = _status(attrs.get("status") or "failed")
            normalized = (
                failure_status
                if failure_status in {
                    "ambiguous",
                    "cancelled",
                    "failed",
                    "timed_out",
                }
                else "failed"
            )
        else:
            normalized = attrs.get("status") or "running"
        parent_id = tool_span_ids.get(tool_call_id, root_id)
        span_id = (
            f"event:{started.get('event_id') or event.get('event_id') or index}"
        )
        spans.append(_span(
            span_id=span_id,
            parent_span_id=parent_id,
            kind=_runtime_kind(event),
            name=_runtime_name(event, attrs),
            status=normalized,
            run_id=source.run_id,
            trace_id=event.get("trace_id") or started.get("trace_id"),
            turn_id=event.get("turn_id") or started.get("turn_id"),
            started_at=started.get("occurred_at"),
            finished_at=(terminal or {}).get("occurred_at"),
            duration_ms=attrs.get("latency_ms"),
            attempt=attrs.get("attempt_no") or attrs.get("attempt_count"),
            metrics={
                key: value
                for key, value in attrs.items()
                if key.endswith(("_bytes", "_count", "_ms", "_tokens"))
            },
            attributes=attrs,
            failure=_failure(
                _status(normalized),
                code=event.get("failure_code") or attrs.get("failure_code"),
                error_type=attrs.get("error_type"),
                retryable=attrs.get("retryable"),
            ),
            version={
                "registry_generation": event.get("registry_generation"),
                "registry_sha256": event.get("registry_sha256"),
                "module_id": event.get("module_id"),
                "module_version": event.get("module_version"),
                "artifact_revision": event.get("artifact_revision"),
            },
        ))


def _append_persisted_runtime_spans(
    source: RunViewSource,
    spans: list[dict[str, Any]],
    tool_span_ids: Mapping[str, str],
) -> None:
    root_id = f"run:{source.run_id}"
    for index, row in enumerate(source.sandbox_runs, start=1):
        item = _mapping(row)
        normalized = _status(item.get("status"))
        tool_call_id = str(item.get("tool_call_id") or "")
        spans.append(_span(
            span_id=f"sandbox:{item.get('run_id') or index}",
            parent_span_id=tool_span_ids.get(tool_call_id, root_id),
            kind="sandbox",
            name=f"sandbox:{item.get('profile_id') or 'restricted'}",
            status=normalized,
            run_id=source.run_id,
            trace_id=item.get("trace_id"),
            started_at=item.get("started_at") or item.get("created_at"),
            finished_at=item.get("finished_at"),
            metrics={
                "cpu_time_ms": _nonnegative_int(item.get("cpu_time_ms")),
                "peak_memory_bytes": _nonnegative_int(
                    item.get("peak_memory_bytes")
                ),
                "stdout_bytes": _nonnegative_int(item.get("stdout_bytes")),
                "stderr_bytes": _nonnegative_int(item.get("stderr_bytes")),
            },
            attributes={
                "profile_id": item.get("profile_id"),
                "execution_mode": item.get("execution_mode"),
                "image_digest": item.get("image_digest"),
                "termination_reason": item.get("termination_reason"),
                "stdout_truncated": item.get("stdout_truncated"),
                "stderr_truncated": item.get("stderr_truncated"),
            },
            failure=_failure(
                normalized,
                code=item.get("termination_reason") or "sandbox_execution_failed",
                error_type=item.get("termination_reason"),
            ),
            version={"image_digest": item.get("image_digest")},
        ))

    for index, row in enumerate(source.artifacts, start=1):
        item = _mapping(row)
        spans.append(_span(
            span_id=f"artifact:{item.get('artifact_id') or index}",
            parent_span_id=root_id,
            kind="artifact",
            name=f"artifact:{item.get('source_kind') or 'runtime'}",
            status="succeeded",
            run_id=source.run_id,
            started_at=item.get("created_at"),
            finished_at=item.get("created_at"),
            metrics={"size_bytes": _nonnegative_int(item.get("size_bytes"))},
            attributes={
                "artifact_id": item.get("artifact_id"),
                "source_kind": item.get("source_kind"),
                "version": item.get("version"),
                "asset_sha256": item.get("asset_sha256"),
            },
            version={
                "artifact_id": item.get("artifact_id"),
                "asset_sha256": item.get("asset_sha256"),
                "version": item.get("version"),
            },
        ))

    for index, row in enumerate(source.checkpoints, start=1):
        item = _mapping(row)
        spans.append(_span(
            span_id=f"checkpoint:{item.get('checkpoint_id') or index}",
            parent_span_id=root_id,
            kind="checkpoint",
            name=f"checkpoint:{item.get('boundary') or 'unknown'}",
            status="succeeded",
            run_id=source.run_id,
            turn_id=item.get("turn_id"),
            started_at=item.get("created_at"),
            finished_at=item.get("created_at"),
            metrics={
                "sequence": _nonnegative_int(item.get("sequence")),
                "model_step": _nonnegative_int(item.get("model_step")),
                "tool_round": _nonnegative_int(item.get("tool_round")),
                "side_effect_frontier": _nonnegative_int(
                    item.get("side_effect_frontier")
                ),
            },
            attributes={
                "boundary": item.get("boundary"),
                "resumable": item.get("resumable"),
                "runtime_id": item.get("runtime_id"),
            },
            version={
                "schema_version": item.get("schema_version"),
                "module_version": item.get("runtime_protocol_version"),
                "version_proofs_sha256": item.get(
                    "version_proofs_sha256"
                ),
            },
        ))

    for index, row in enumerate(source.side_effects, start=1):
        item = _mapping(row)
        normalized = _status(item.get("state"))
        tool_call_id = str(item.get("tool_call_id") or "")
        spans.append(_span(
            span_id=f"side-effect:{item.get('receipt_id') or index}",
            parent_span_id=tool_span_ids.get(tool_call_id, root_id),
            kind="side_effect",
            name=str(item.get("tool_name") or "side_effect"),
            status=normalized,
            run_id=source.run_id,
            started_at=item.get("prepared_at"),
            finished_at=item.get("settled_at"),
            metrics={
                "result_size_bytes": _nonnegative_int(
                    item.get("result_size_bytes")
                ),
            },
            attributes={
                "effect_class": item.get("effect_class"),
                "state": item.get("state"),
                "tool_name": item.get("tool_name"),
                "request_sha256": item.get("request_sha256"),
                "result_sha256": item.get("result_sha256"),
            },
            failure=_failure(
                normalized,
                code=item.get("error_code") or "side_effect_failed",
                retryable=False,
            ),
        ))

    for index, row in enumerate(source.recovery_operations, start=1):
        item = _mapping(row)
        normalized = _status(item.get("status"))
        spans.append(_span(
            span_id=f"recovery:{item.get('operation_id') or index}",
            parent_span_id=root_id,
            kind="recovery",
            name=f"recovery:{item.get('operation_kind') or 'resume'}",
            status=normalized,
            run_id=source.run_id,
            started_at=item.get("prepared_at"),
            finished_at=item.get("finished_at") or item.get("updated_at"),
            metrics={
                "source_head_sequence": _nonnegative_int(
                    item.get("source_head_sequence")
                ),
            },
            attributes={
                "operation_kind": item.get("operation_kind"),
                "status": item.get("status"),
                "restored_checkpoint_id": item.get(
                    "restored_checkpoint_id"
                ),
            },
            failure=_failure(
                normalized,
                code=item.get("error_code") or "run_recovery_failed",
            ),
        ))


def _retry_items(
    source: RunViewSource,
    spans: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    llm_by_id = {
        str(span.get("span_id")): span
        for span in spans
        if span.get("kind") == "llm"
    }
    groups: dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in source.llm_requests:
        item = _mapping(row)
        groups[(
            str(item.get("source") or ""),
            str(item.get("phase") or ""),
            _nonnegative_int(item.get("round_index")),
        )].append(item)
    retries: list[dict[str, Any]] = []
    for attempts in groups.values():
        attempts.sort(key=lambda item: (
            _nonnegative_int(item.get("route_attempt_index")),
            _nonnegative_int(item.get("id")),
        ))
        previous: Mapping[str, Any] | None = None
        for item in attempts:
            attempt_index = _nonnegative_int(item.get("route_attempt_index"))
            current_id = f"llm:{item.get('id')}"
            if attempt_index > 0 or previous is not None:
                previous_id = f"llm:{previous.get('id')}" if previous else ""
                retries.append({
                    "kind": "llm_route",
                    "from_span_id": previous_id if previous_id in llm_by_id else "",
                    "to_span_id": current_id if current_id in llm_by_id else "",
                    "attempt": attempt_index + 1,
                    "reason_code": (
                        str(previous.get("error_category") or "route_fallback")
                        if previous
                        else "route_fallback"
                    ),
                })
            previous = item
    known = {(item["kind"], item["to_span_id"]) for item in retries}
    for span in spans:
        attempt = _nonnegative_int(span.get("attempt"))
        if attempt <= 1 or span.get("kind") == "llm":
            continue
        key = (str(span.get("kind")), str(span.get("span_id")))
        if key in known:
            continue
        retries.append({
            "kind": str(span.get("kind")),
            "from_span_id": "",
            "to_span_id": str(span.get("span_id")),
            "attempt": attempt,
            "reason_code": "runtime_retry",
        })
    return retries


def _versions(spans: Sequence[Mapping[str, Any]]) -> dict[str, list[Any]]:
    prompts: set[str] = set()
    models: set[str] = set()
    modules: set[str] = set()
    registries: set[str] = set()
    sandbox_images: set[str] = set()
    artifacts: set[str] = set()
    for span in spans:
        version = _mapping(span.get("version"))
        attributes = _mapping(span.get("attributes"))
        if version.get("prompt_sha256"):
            prompts.add(str(version["prompt_sha256"]))
        model = version.get("model") or attributes.get("model")
        provider = version.get("provider") or attributes.get("provider")
        if model:
            models.add(f"{provider or 'provider'}/{model}")
        if version.get("module_id"):
            modules.add(
                f"{version['module_id']}@{version.get('module_version') or 'unknown'}"
            )
        if version.get("registry_sha256"):
            registries.add(str(version["registry_sha256"]))
        image = version.get("image_digest") or attributes.get("image_digest")
        if image:
            sandbox_images.add(str(image))
        artifact_id = version.get("artifact_id")
        if artifact_id:
            artifacts.add(
                f"{artifact_id}@{version.get('version') or 1}:"
                f"{version.get('asset_sha256') or ''}"
            )
    return {
        "prompts": sorted(prompts),
        "models": sorted(models),
        "runtime_modules": sorted(modules),
        "registries": sorted(registries),
        "sandbox_images": sorted(sandbox_images),
        "artifacts": sorted(artifacts),
    }


def _finalize_spans(spans: list[dict[str, Any]]) -> None:
    timestamps = [
        parsed
        for span in spans
        if (parsed := _timestamp(span.get("started_at"))) is not None
    ]
    origin = min(timestamps) if timestamps else None
    root = spans[0]
    if root.get("started_at") is None and origin is not None:
        root["started_at"] = _iso(origin)
    finishes = [
        parsed
        for span in spans
        if (parsed := _timestamp(span.get("finished_at"))) is not None
    ]
    if root.get("finished_at") is None and finishes:
        root["finished_at"] = _iso(max(finishes))
        root["duration_ms"] = _duration_ms(
            _timestamp(root.get("started_at")),
            _timestamp(root.get("finished_at")),
        )
    origin = _timestamp(root.get("started_at")) or origin
    for span in spans:
        started = _timestamp(span.get("started_at"))
        span["offset_ms"] = (
            max(0, int((started - origin).total_seconds() * 1000))
            if origin is not None and started is not None
            else 0
        )
    spans.sort(key=lambda span: (
        _timestamp(span.get("started_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        str(span.get("span_id")),
    ))


def build_run_view(source: RunViewSource) -> dict[str, Any]:
    """生成可序列化、无正文的离线 Run Viewer 读模型。"""

    if not isinstance(source, RunViewSource):
        raise TypeError("source 必须是 RunViewSource")
    spans, tool_span_ids, _llm_spans = _build_base_spans(source)
    _append_runtime_spans(source, spans, tool_span_ids)
    _append_persisted_runtime_spans(source, spans, tool_span_ids)
    _finalize_spans(spans)

    span_ids = {str(span["span_id"]) for span in spans}
    dag_edges = [
        {
            "source": str(span["parent_span_id"]),
            "target": str(span["span_id"]),
            "relation": "contains",
        }
        for span in spans
        if span.get("parent_span_id") in span_ids
    ]
    retries = _retry_items(source, spans)
    dag_edges.extend(
        {
            "source": item["from_span_id"],
            "target": item["to_span_id"],
            "relation": "retry",
        }
        for item in retries
        if item["from_span_id"] in span_ids and item["to_span_id"] in span_ids
    )
    failures = [
        {
            "span_id": span["span_id"],
            "kind": span["kind"],
            "name": span["name"],
            "status": span["status"],
            **dict(span["failure"]),
        }
        for span in spans
        if span.get("failure")
    ]
    recoveries = [
        {
            "span_id": span["span_id"],
            "kind": span["kind"],
            "name": span["name"],
            "status": span["status"],
            "started_at": span["started_at"],
            "attributes": span["attributes"],
        }
        for span in spans
        if span.get("kind") in {"checkpoint", "recovery", "side_effect"}
    ]
    llm_spans = [span for span in spans if span.get("kind") == "llm"]
    waterfall_items = [
        {
            "span_id": span["span_id"],
            "name": span["name"],
            "attempt": span["attempt"],
            "duration_ms": span["duration_ms"],
            **dict(span["metrics"]),
        }
        for span in llm_spans
    ]
    context_manifest = _mapping(source.context_manifest)
    turn_ids = sorted({
        str(span.get("turn_id"))
        for span in spans
        if span.get("turn_id")
    })
    root = next(span for span in spans if span["kind"] == "run")
    return {
        "schema_version": RUN_VIEW_SCHEMA_VERSION,
        "source": "persisted_evidence",
        "offline": True,
        "run_id": source.run_id,
        "trace_id": root.get("trace_id") or "",
        "turn_ids": turn_ids,
        "summary": {
            "status": root["status"],
            "duration_ms": root["duration_ms"],
            "span_count": len(spans),
            "failed_span_count": len(failures),
            "retry_count": len(retries),
            "recovery_count": len(recoveries),
        },
        "spans": spans,
        "timeline": [
            {
                key: span[key]
                for key in (
                    "span_id",
                    "parent_span_id",
                    "kind",
                    "name",
                    "status",
                    "turn_id",
                    "started_at",
                    "finished_at",
                    "duration_ms",
                    "offset_ms",
                    "attempt",
                )
            }
            for span in spans
        ],
        "dag": {
            "nodes": [
                {
                    "id": span["span_id"],
                    "kind": span["kind"],
                    "name": span["name"],
                    "status": span["status"],
                }
                for span in spans
            ],
            "edges": dag_edges,
        },
        "waterfall": {
            "totals": {
                "input_tokens": sum(
                    _nonnegative_int(span["metrics"].get("input_tokens"))
                    for span in llm_spans
                ),
                "output_tokens": sum(
                    _nonnegative_int(span["metrics"].get("output_tokens"))
                    for span in llm_spans
                ),
                "cache_hit_tokens": sum(
                    _nonnegative_int(span["metrics"].get("cache_hit_tokens"))
                    for span in llm_spans
                ),
                "cache_miss_tokens": sum(
                    _nonnegative_int(span["metrics"].get("cache_miss_tokens"))
                    for span in llm_spans
                ),
                "cost_microusd": sum(
                    _nonnegative_int(span["metrics"].get("cost_microusd"))
                    for span in llm_spans
                ),
            },
            "items": waterfall_items,
        },
        "context_manifest": context_manifest or {
            "available": False,
            "source": "not_recorded",
            "manifest": {},
            "fingerprint": {},
        },
        "failures": failures,
        "retries": retries,
        "recoveries": recoveries,
        "versions": _versions(spans),
        "redaction": {
            "hidden_reasoning": "omitted",
            "prompt_and_messages": "omitted",
            "tool_arguments_and_results": "omitted",
            "sandbox_command_and_output": "omitted",
            "secrets_and_credentials": "omitted",
            "available_evidence": "hashes_counts_statuses_and_versions",
        },
    }


__all__ = [
    "RUN_VIEW_SCHEMA_VERSION",
    "RunViewSource",
    "build_run_view",
]
