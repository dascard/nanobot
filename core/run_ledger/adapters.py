"""现有 Runtime／Trace 合同到隐私安全 Ledger Event 的 Adapter。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from core.agent_runtime.contracts import (
    RuntimeRunEvent,
    RuntimeRunEventKind,
)
from core.run_ledger.contracts import (
    RunLedgerEventDraft,
    RunLedgerIdentity,
    RunLedgerScalar,
    canonical_run_status,
)
from core.runtime.events import RuntimeEvent
from core.telemetry.contracts import TelemetryCorrelation


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_event_id(*parts: object) -> str:
    raw = ":".join(str(part or "").strip() for part in parts)
    if len(raw) <= 160 and raw:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    namespace = str(parts[0] or "ledger").strip()[:32] or "ledger"
    return f"{namespace}:sha256:{digest}"


def _payload_fingerprint(value: object) -> tuple[int, int, str]:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except Exception:
        text = repr(type(value))
    encoded = text.encode("utf-8", errors="replace")
    return len(encoded), len(text), hashlib.sha256(encoded).hexdigest()


def _text_fingerprint(value: object) -> tuple[int, int, str]:
    text = str(value or "")
    encoded = text.encode("utf-8", errors="replace")
    return len(encoded), len(text), hashlib.sha256(encoded).hexdigest()


def _identity_from_runtime_event(event: RuntimeRunEvent) -> RunLedgerIdentity:
    return RunLedgerIdentity(
        actor_type=event.actor.actor_type.value,
        actor_id=event.actor.actor_id,
        parent_actor_id=event.actor.parent_actor_id,
        owner_platform=event.owner.platform,
        owner_type=event.owner.owner_type.value,
        owner_id=event.owner.owner_id,
    )


def _runtime_run_event_payload(
    event: RuntimeRunEvent,
) -> tuple[str, dict[str, RunLedgerScalar], int]:
    payload: dict[str, RunLedgerScalar] = {
        "correlation_id": event.correlation_id,
    }
    dropped = len(event.attributes)
    if event.kind is RuntimeRunEventKind.STATUS:
        event_type = "runtime.invocation_status_changed"
    elif event.kind is RuntimeRunEventKind.TEXT_DELTA:
        event_type = "runtime.output_delta_recorded"
        size_bytes, chars, digest = _text_fingerprint(event.text_delta)
        payload.update({
            "text_bytes": size_bytes,
            "text_chars": chars,
            "text_sha256": digest,
        })
    elif event.kind is RuntimeRunEventKind.TOOL_ACTIVITY:
        event_type = "tool.activity_recorded"
        tool_call = event.tool_call
        assert tool_call is not None
        args_bytes, args_chars, args_sha256 = _payload_fingerprint(
            tool_call.arguments
        )
        payload.update({
            "tool_call_id": tool_call.call_id,
            "tool_name": tool_call.name,
            "tool_status": tool_call.status.value,
            "args_bytes": args_bytes,
            "args_chars": args_chars,
            "args_sha256": args_sha256,
        })
        if tool_call.result is not None:
            result_bytes, result_chars, result_sha256 = _payload_fingerprint(
                tool_call.result
            )
            payload.update({
                "result_bytes": result_bytes,
                "result_chars": result_chars,
                "result_sha256": result_sha256,
            })
    elif event.kind is RuntimeRunEventKind.USAGE:
        event_type = "usage.recorded"
        usage = event.usage
        assert usage is not None
        payload.update({
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "total_tokens": usage.total_tokens,
            "cost_microunits": usage.cost_microunits,
        })
    elif event.kind is RuntimeRunEventKind.ARTIFACT:
        event_type = "artifact.recorded"
        artifact = event.artifact
        assert artifact is not None
        payload.update({
            "artifact_id": artifact.artifact_id,
            "artifact_sha256": artifact.sha256,
            "media_type": artifact.media_type,
            "size_bytes": artifact.size_bytes,
        })
    elif event.kind is RuntimeRunEventKind.ERROR:
        event_type = "runtime.error_recorded"
        error = event.error
        assert error is not None
        message_bytes, message_chars, message_sha256 = _text_fingerprint(
            error.message
        )
        payload.update({
            "error_code": error.code,
            "error_type": error.code,
            "retryable": error.retryable,
            "message_bytes": message_bytes,
            "message_chars": message_chars,
            "message_sha256": message_sha256,
        })
    else:
        event_type = "runtime.invocation_ended"
    return event_type, payload, dropped


def runtime_run_event_to_ledger(
    event: RuntimeRunEvent,
) -> RunLedgerEventDraft:
    """投影 AgentRuntime 的瞬时事件；不把 invocation end 当整体 Run 终态。"""

    if not isinstance(event, RuntimeRunEvent):
        raise TypeError("event 必须是 RuntimeRunEvent")
    event_type, payload, dropped = _runtime_run_event_payload(event)
    return RunLedgerEventDraft(
        event_id=_bounded_event_id("runtime-run", event.event_id),
        run_id=event.run_id,
        event_type=event_type,
        occurred_at=event.occurred_at,
        source="agent_runtime.event",
        correlation=TelemetryCorrelation(
            turn_id=event.turn_id,
            trace_id=event.correlation_id,
            run_id=event.run_id,
            tool_call_id=(
                event.tool_call.call_id
                if event.tool_call is not None
                else ""
            ),
        ),
        identity=_identity_from_runtime_event(event),
        status=event.status.value,
        payload=payload,
        source_event_id=event.event_id,
        source_sequence=event.sequence,
        dropped_field_count=dropped,
    )


def runtime_event_to_ledger(
    event: RuntimeEvent,
) -> RunLedgerEventDraft | None:
    """把已由 Descriptor 双重净化的 RuntimeEvent 同步投影到 Ledger。"""

    if not isinstance(event, RuntimeEvent):
        raise TypeError("event 必须是 RuntimeEvent")
    if not event.context.run_id:
        return None
    payload: dict[str, RunLedgerScalar] = {}
    additionally_dropped = 0
    for key, value in event.attributes.items():
        if type(value) in {str, int, float, bool, type(None)}:
            payload[str(key)] = value  # RuntimeEvent Descriptor 已完成字段白名单。
        else:
            additionally_dropped += 1
    return RunLedgerEventDraft(
        event_id=_bounded_event_id("runtime-event", event.event_id),
        run_id=event.context.run_id,
        event_type=f"{event.name}.{event.phase}",
        occurred_at=event.occurred_at,
        source="runtime.event_bus",
        correlation=event.context,
        status=event.phase,
        payload=payload,
        source_event_id=event.event_id,
        dropped_field_count=(
            event.dropped_attribute_count + additionally_dropped
        ),
    )


def runtime_event_admission_events(
    event: RuntimeEvent,
) -> tuple[RunLedgerEventDraft, RunLedgerEventDraft]:
    """为独立领域 Attempt Run 生成确定性的接纳与运行事实。"""

    if not isinstance(event, RuntimeEvent):
        raise TypeError("event 必须是 RuntimeEvent")
    run_id = str(event.context.run_id or "").strip()
    if not run_id:
        raise ValueError("领域 Run 必须声明 run_id")
    empty_bytes, empty_chars, empty_sha256 = _text_fingerprint("")
    accepted = RunLedgerEventDraft(
        event_id=_bounded_event_id("run", run_id, "accepted"),
        run_id=run_id,
        event_type="run.accepted",
        occurred_at=event.occurred_at,
        source="runtime.event_bus",
        correlation=event.context,
        status="accepted",
        payload={
            "run_type": event.domain,
            "event_name": event.name,
            "input_bytes": empty_bytes,
            "input_chars": empty_chars,
            "input_sha256": empty_sha256,
        },
        source_event_id=event.event_id,
    )
    running = run_status_changed_event(
        accepted_event=accepted,
        status="running",
        previous_status="accepted",
    )
    return accepted, running


def runtime_event_terminal_event(
    event: RuntimeEvent,
) -> RunLedgerEventDraft | None:
    """把一次独立领域 Attempt 的结束投影为该 Run 的唯一终态。"""

    if event.name != "delivery.attempt" or event.phase not in {
        "succeeded",
        "failed",
    }:
        return None
    failure_type = str(event.attributes.get("error_type") or "").lower()
    failure_code = str(event.attributes.get("failure_code") or "").lower()
    if event.phase == "succeeded":
        status = "succeeded"
    elif "cancel" in failure_type or "cancel" in failure_code:
        status = "cancelled"
    elif "ambiguous" in failure_type or "ambiguous" in failure_code:
        status = "ambiguous"
    else:
        status = "failed"
    latency_ms = event.attributes.get("latency_ms")
    return RunLedgerEventDraft(
        event_id=_bounded_event_id("run", event.context.run_id, "terminated"),
        run_id=event.context.run_id,
        event_type="run.terminated",
        occurred_at=event.occurred_at,
        source="runtime.event_bus",
        correlation=event.context,
        status=status,
        payload={
            "event_name": event.name,
            "latency_ms": (
                max(0, int(latency_ms))
                if type(latency_ms) in {int, float}
                else 0
            ),
            "failure_code": failure_code,
            "error_type": failure_type,
        },
        source_event_id=event.event_id,
    )


def artifact_published_event(
    *,
    correlation: TelemetryCorrelation,
    artifact_id: str,
    version: int,
    source_run_id: str,
    workspace_id: str,
    sha256: str,
    size_bytes: int,
    media_type: str,
    occurred_at: datetime | None = None,
) -> RunLedgerEventDraft:
    """记录不可变资产版本，不保存逻辑文件名、宿主路径或下载 URI。"""

    if not correlation.run_id:
        raise ValueError("Artifact Ledger 事件必须关联 run_id")
    return RunLedgerEventDraft(
        event_id=_bounded_event_id(
            "artifact",
            correlation.run_id,
            artifact_id,
        ),
        run_id=correlation.run_id,
        event_type="artifact.recorded",
        occurred_at=occurred_at or _now(),
        source="asset.service",
        correlation=correlation,
        status="published",
        payload={
            "artifact_id": str(artifact_id or ""),
            "artifact_sha256": sha256,
            "artifact_version": max(1, int(version)),
            "source_run_id": str(source_run_id or ""),
            "workspace_id": str(workspace_id or ""),
            "media_type": str(media_type or "application/octet-stream"),
            "size_bytes": max(0, int(size_bytes or 0)),
        },
    )


def sandbox_permission_decision_event(
    *,
    correlation: TelemetryCorrelation,
    tool_name: str,
    allowed: bool,
    reason_code: str,
    capability: str,
    occurred_at: datetime | None = None,
) -> RunLedgerEventDraft:
    """把生产 Sandbox Policy 决定写入 Ledger，不记录 session 或资源正文。"""

    if not correlation.run_id:
        raise ValueError("Permission Ledger 事件必须关联 run_id")
    outcome = "allow" if allowed else "deny"
    return RunLedgerEventDraft(
        event_id=_bounded_event_id(
            "permission",
            correlation.run_id,
            correlation.tool_call_id or correlation.request_id,
            tool_name,
            outcome,
            reason_code,
        ),
        run_id=correlation.run_id,
        event_type="permission.decided",
        occurred_at=occurred_at or _now(),
        source="sandbox.access_policy",
        correlation=correlation,
        status=outcome,
        payload={
            "action": str(tool_name or ""),
            "outcome": outcome,
            "reason_code": str(reason_code or "unknown"),
            "capability": str(capability or "off"),
        },
    )


def run_accepted_event(
    *,
    run_id: str,
    trace_id: str,
    session_id: str,
    user_id: str,
    chat_type: str,
    group_id: str,
    run_type: str,
    prompt_mode: str,
    prompt_key: str,
    prompt_sha256: str,
    model: str,
    input_value: object,
    platform: str = "",
    request_id: str = "",
    occurred_at: datetime | None = None,
) -> RunLedgerEventDraft:
    input_bytes, input_chars, input_sha256 = _text_fingerprint(input_value)
    owner_type = "group" if chat_type == "group" and group_id else "user"
    owner_id = group_id if owner_type == "group" else user_id
    identity = RunLedgerIdentity()
    if platform and owner_id:
        identity = RunLedgerIdentity(
            actor_type="user" if user_id else "",
            actor_id=user_id,
            owner_platform=platform,
            owner_type=owner_type,
            owner_id=owner_id,
        )
    return RunLedgerEventDraft(
        event_id=_bounded_event_id("run", run_id, "accepted"),
        run_id=run_id,
        event_type="run.accepted",
        occurred_at=occurred_at or _now(),
        source="trace.run",
        correlation=TelemetryCorrelation(
            request_id=request_id or run_id,
            session_id=session_id,
            trace_id=trace_id,
            run_id=run_id,
        ),
        identity=identity,
        status="accepted",
        payload={
            "run_type": str(run_type or ""),
            "chat_type": str(chat_type or ""),
            "prompt_mode": str(prompt_mode or ""),
            "prompt_key": str(prompt_key or ""),
            "prompt_sha256": str(prompt_sha256 or ""),
            "model": str(model or ""),
            "input_bytes": input_bytes,
            "input_chars": input_chars,
            "input_sha256": input_sha256,
        },
    )


def run_terminated_event(
    *,
    run_id: str,
    trace_id: str,
    session_id: str,
    status: str,
    output_value: object,
    error_value: object,
    latency_ms: int | None,
    model: str,
    occurred_at: datetime | None = None,
) -> RunLedgerEventDraft:
    output_bytes, output_chars, output_sha256 = _text_fingerprint(output_value)
    error_bytes, error_chars, error_sha256 = _text_fingerprint(error_value)
    legacy_status = str(status or "unknown").strip().lower()
    canonical_status = canonical_run_status(legacy_status)
    return RunLedgerEventDraft(
        event_id=_bounded_event_id("run", run_id, "terminated"),
        run_id=run_id,
        event_type="run.terminated",
        occurred_at=occurred_at or _now(),
        source="trace.run",
        correlation=TelemetryCorrelation(
            session_id=session_id,
            trace_id=trace_id,
            run_id=run_id,
        ),
        status=canonical_status,
        payload={
            "legacy_status": legacy_status,
            "model": str(model or ""),
            "latency_ms": max(0, int(latency_ms or 0)),
            "output_bytes": output_bytes,
            "output_chars": output_chars,
            "output_sha256": output_sha256,
            "error_bytes": error_bytes,
            "error_chars": error_chars,
            "error_sha256": error_sha256,
        },
    )


def run_prompt_resolved_event(
    *,
    run_id: str,
    trace_id: str,
    session_id: str,
    prompt_mode: str,
    prompt_key: str,
    prompt_source: str,
    prompt_sha256: str,
    resolution_manifest_json: str,
    resolution_count: int,
    context_manifest_sha256: str = "",
    context_manifest_entry_count: int = 0,
    context_manifest_token_estimate: int = 0,
    context_manifest_policy_id: str = "",
    occurred_at: datetime | None = None,
) -> RunLedgerEventDraft:
    """记录 Prompt 版本证明，不保存模板、路径或解析清单正文。"""

    source_bytes, source_chars, source_sha256 = _text_fingerprint(
        prompt_source
    )
    manifest_bytes, manifest_chars, manifest_sha256 = _text_fingerprint(
        resolution_manifest_json
    )
    normalized_source = str(prompt_source or "").strip().lower()
    source_type = (
        normalized_source
        if normalized_source in {"built_in", "default", "mixed", "runtime"}
        else "custom"
    )
    identity_digest = hashlib.sha256(
        "\x00".join((
            str(prompt_mode or ""),
            str(prompt_key or ""),
            str(prompt_sha256 or ""),
            source_sha256,
            manifest_sha256,
            str(context_manifest_sha256 or ""),
        )).encode("utf-8")
    ).hexdigest()
    return RunLedgerEventDraft(
        event_id=_bounded_event_id(
            "run",
            run_id,
            "prompt-resolved",
            identity_digest,
        ),
        run_id=run_id,
        event_type="run.prompt_resolved",
        occurred_at=occurred_at or _now(),
        source="trace.run",
        correlation=TelemetryCorrelation(
            session_id=session_id,
            trace_id=trace_id,
            run_id=run_id,
        ),
        payload={
            "prompt_mode": str(prompt_mode or ""),
            "prompt_key": str(prompt_key or ""),
            "prompt_sha256": str(prompt_sha256 or ""),
            "prompt_source_type": source_type,
            "prompt_source_bytes": source_bytes,
            "prompt_source_chars": source_chars,
            "prompt_source_sha256": source_sha256,
            "prompt_resolution_count": max(0, int(resolution_count or 0)),
            "prompt_resolution_bytes": manifest_bytes,
            "prompt_resolution_chars": manifest_chars,
            "prompt_resolution_sha256": manifest_sha256,
            "context_manifest_sha256": str(context_manifest_sha256 or ""),
            "context_manifest_entry_count": max(
                0,
                int(context_manifest_entry_count or 0),
            ),
            "context_manifest_tokens": max(
                0,
                int(context_manifest_token_estimate or 0),
            ),
            "context_manifest_policy_id": str(
                context_manifest_policy_id or ""
            ),
        },
    )


def run_status_changed_event(
    *,
    accepted_event: RunLedgerEventDraft,
    status: str,
    previous_status: str,
) -> RunLedgerEventDraft:
    """由已验证接纳事件派生同一身份和关联范围内的状态迁移。"""

    return RunLedgerEventDraft(
        event_id=_bounded_event_id(
            "run",
            accepted_event.run_id,
            "status",
            status,
        ),
        run_id=accepted_event.run_id,
        event_type="run.status_changed",
        occurred_at=accepted_event.occurred_at,
        source=accepted_event.source,
        correlation=accepted_event.correlation,
        identity=accepted_event.identity,
        status=str(status or "unknown"),
        payload={"previous_status": str(previous_status or "unknown")},
    )


def usage_recorded_event(
    *,
    event_id: str,
    run_id: str,
    trace_id: str,
    source_event_id: str,
    status: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    reasoning_tokens: int,
    cache_miss_tokens: int = 0,
    cache_write_tokens: int = 0,
    occurred_at: datetime | None = None,
) -> RunLedgerEventDraft:
    return RunLedgerEventDraft(
        event_id=_bounded_event_id("usage", event_id),
        run_id=run_id,
        event_type="usage.recorded",
        occurred_at=occurred_at or _now(),
        source="trace.llm_request",
        correlation=TelemetryCorrelation(
            trace_id=trace_id,
            run_id=run_id,
        ),
        status=str(status or "unknown"),
        payload={
            "source_event_id": source_event_id,
            "provider": str(provider or ""),
            "model": str(model or ""),
            "input_tokens": max(0, int(input_tokens or 0)),
            "output_tokens": max(0, int(output_tokens or 0)),
            "cached_input_tokens": max(0, int(cached_input_tokens or 0)),
            "reasoning_tokens": max(0, int(reasoning_tokens or 0)),
            "total_tokens": max(
                0,
                int(input_tokens or 0) + int(output_tokens or 0),
            ),
            "cache_miss_tokens": max(0, int(cache_miss_tokens or 0)),
            "cache_write_tokens": max(0, int(cache_write_tokens or 0)),
        },
        source_event_id=source_event_id,
    )


def permission_decision_event(
    request: Any,
    decision: Any,
) -> RunLedgerEventDraft:
    """PermissionPort 装饰器使用；正文资源仅保存摘要。"""

    identity = request.identity
    resource_bytes, resource_chars, resource_sha256 = _text_fingerprint(
        request.resource
    )
    reason_type = str(decision.reason or "").partition(":")[0]
    return RunLedgerEventDraft(
        event_id=_bounded_event_id("permission", decision.decision_id),
        run_id=identity.run_id,
        event_type="permission.decided",
        occurred_at=decision.decided_at,
        source="permission.port",
        correlation=TelemetryCorrelation(
            request_id=request.request_id,
            turn_id=identity.turn_id,
            trace_id=identity.correlation_id,
            run_id=identity.run_id,
        ),
        identity=RunLedgerIdentity(
            actor_type=identity.actor.actor_type.value,
            actor_id=identity.actor.actor_id,
            parent_actor_id=identity.actor.parent_actor_id,
            owner_platform=identity.owner.platform,
            owner_type=identity.owner.owner_type.value,
            owner_id=identity.owner.owner_id,
        ),
        status=decision.outcome.value,
        payload={
            "permission_request_id": request.request_id,
            "decision_id": decision.decision_id,
            "action": request.action,
            "risk": request.risk.value,
            "outcome": decision.outcome.value,
            "reason_type": reason_type,
            "grant_id": decision.grant_id,
            "resource_bytes": resource_bytes,
            "resource_chars": resource_chars,
            "resource_sha256": resource_sha256,
        },
    )


__all__ = [
    "artifact_published_event",
    "permission_decision_event",
    "run_accepted_event",
    "run_prompt_resolved_event",
    "run_status_changed_event",
    "run_terminated_event",
    "runtime_event_to_ledger",
    "runtime_event_admission_events",
    "runtime_event_terminal_event",
    "runtime_run_event_to_ledger",
    "sandbox_permission_decision_event",
    "usage_recorded_event",
]
