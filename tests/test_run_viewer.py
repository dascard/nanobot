"""统一离线 Run Viewer 的纯投影与隐私边界测试。"""

from __future__ import annotations

import json

from core.context_engine import (
    ContextLayer,
    ContextLayerBudget,
    ContextManifest,
)
from core.observability.run_view import RunViewSource, build_run_view


def _manifest() -> dict[str, object]:
    return ContextManifest(
        policy_id="prompt-context-v1-private",
        request_prompt_sha256="a" * 64,
        entries=(),
        layer_budgets=tuple(
            ContextLayerBudget(layer, 1_000, 0)
            for layer in ContextLayer
        ),
    ).to_dict()


def _runtime_event(
    event_id: str,
    *,
    name: str,
    domain: str,
    phase: str,
    occurred_at: str,
    attributes: dict[str, object],
    tool_call_id: str = "",
    task_id: str = "",
    task_run_id: str = "",
    delivery_id: str = "",
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "name": name,
        "domain": domain,
        "phase": phase,
        "occurred_at": occurred_at,
        "turn_id": "turn-1",
        "trace_id": "trace-1",
        "run_id": "run-1",
        "task_id": task_id,
        "task_run_id": task_run_id,
        "job_id": "",
        "tool_call_id": tool_call_id,
        "delivery_id": delivery_id,
        "parent_job_id": "",
        "registry_generation": 7,
        "registry_sha256": "b" * 64,
        "module_id": f"{domain}.runtime",
        "module_version": "2.1.0",
        "artifact_revision": "release-10.1",
        "failure_code": "",
        "attributes": json.dumps(attributes, ensure_ascii=False),
    }


def test_run_view_links_all_persisted_span_kinds_without_payload_bodies():
    runtime_events = []
    event_specs = (
        (
            "memory.retrieve",
            "memory",
            {"provider": "hybrid", "query_sha256": "c" * 64},
            {},
        ),
        (
            "mcp.call",
            "mcp",
            {
                "server_id": "research",
                "tool_name": "search",
                "input_schema_sha256": "d" * 64,
            },
            {"tool_call_id": "tool-1"},
        ),
        (
            "subagent.execute",
            "subagent",
            {
                "child_run_id": "run-1:subagent:one",
                "task_id": "research-task",
                "role_id": "researcher",
                "attempt_no": 2,
                "model": "model-child",
            },
            {
                "task_id": "research-task",
                "task_run_id": "run-1:subagent:one",
            },
        ),
        (
            "delivery.attempt",
            "delivery",
            {"channel": "qq", "target_type": "private", "attempt_no": 1},
            {"delivery_id": "delivery-1"},
        ),
    )
    for index, (name, domain, attributes, correlation) in enumerate(
        event_specs,
        start=1,
    ):
        runtime_events.extend((
            _runtime_event(
                f"event-{index}-start",
                name=name,
                domain=domain,
                phase="started",
                occurred_at=f"2026-08-09T00:00:0{index}+00:00",
                attributes=attributes,
                **correlation,
            ),
            _runtime_event(
                f"event-{index}-finish",
                name=name,
                domain=domain,
                phase="succeeded",
                occurred_at=f"2026-08-09T00:00:1{index}+00:00",
                attributes={**attributes, "latency_ms": index * 10},
                **correlation,
            ),
        ))

    view = build_run_view(RunViewSource(
        run_id="run-1",
        run={
            "run_id": "run-1",
            "trace_id": "trace-1",
            "run_type": "chat",
            "status": "success",
            "prompt_key": "chat_private",
            "prompt_mode": "managed",
            "prompt_sha256": "a" * 64,
            "started_at": "2026-08-09T00:00:00+00:00",
            "finished_at": "2026-08-09T00:01:00+00:00",
        },
        ledger_records=({"trace_id": "trace-1", "turn_id": "turn-1"},),
        tool_calls=({
            "tool_call_id": "tool-1",
            "trace_id": "trace-1",
            "tool_name": "remote__search",
            "status": "success",
            "started_at": "2026-08-09T00:00:02+00:00",
            "finished_at": "2026-08-09T00:00:14+00:00",
        },),
        prompt_logs=({
            "id": 1,
            "trace_id": "trace-1",
            "prompt_key": "chat_private",
            "mode": "managed",
            "prompt_source": "runtime",
            "prompt_sha256": "a" * 64,
            "token_estimate": 80,
            "created_at": "2026-08-09T00:00:01+00:00",
        },),
        llm_requests=(
            {
                "id": 1,
                "trace_id": "trace-1",
                "source": "replyer",
                "phase": "agent_loop",
                "round_index": 0,
                "route_attempt_index": 0,
                "provider": "provider-a",
                "model": "model-a",
                "status": "failed",
                "error_category": "timeout",
                "cache_status": "error",
                "input_tokens": 100,
                "output_tokens": 0,
                "cost_microusd": 0,
                "created_at": "2026-08-09T00:00:02+00:00",
                "finished_at": "2026-08-09T00:00:03+00:00",
            },
            {
                "id": 2,
                "trace_id": "trace-1",
                "source": "replyer",
                "phase": "agent_loop",
                "round_index": 0,
                "route_attempt_index": 1,
                "provider": "provider-b",
                "model": "model-b",
                "status": "success",
                "cache_status": "hit",
                "cache_hit": True,
                "cache_hit_tokens": 80,
                "cache_miss_tokens": 20,
                "input_tokens": 100,
                "output_tokens": 30,
                "cost_microusd": 42,
                "created_at": "2026-08-09T00:00:04+00:00",
                "finished_at": "2026-08-09T00:00:05+00:00",
            },
        ),
        runtime_events=tuple(runtime_events),
        sandbox_runs=({
            "run_id": "sandbox-1",
            "trace_id": "trace-1",
            "tool_call_id": "tool-1",
            "profile_id": "restricted",
            "execution_mode": "oneshot",
            "image_digest": "sha256:sandbox-v1",
            "status": "completed",
            "cpu_time_ms": 11,
            "peak_memory_bytes": 1024,
            "stdout_bytes": 8,
            "stderr_bytes": 0,
            "started_at": "2026-08-09T00:00:06+00:00",
            "finished_at": "2026-08-09T00:00:07+00:00",
        },),
        artifacts=({
            "artifact_id": "artifact-1",
            "asset_sha256": "e" * 64,
            "version": 2,
            "source_kind": "tool",
            "size_bytes": 321,
            "created_at": "2026-08-09T00:00:08+00:00",
        },),
        checkpoints=({
            "checkpoint_id": "checkpoint-1",
            "sequence": 1,
            "schema_version": 1,
            "boundary": "restored",
            "turn_id": "turn-1",
            "runtime_id": "native",
            "runtime_protocol_version": "2.0",
            "resumable": True,
            "created_at": "2026-08-09T00:00:09+00:00",
        },),
        side_effects=({
            "receipt_id": "receipt-1",
            "tool_call_id": "tool-1",
            "tool_name": "remote__search",
            "effect_class": "external",
            "state": "ambiguous",
            "request_sha256": "f" * 64,
            "error_code": "result_unknown",
            "prepared_at": "2026-08-09T00:00:10+00:00",
            "settled_at": "2026-08-09T00:00:11+00:00",
        },),
        recovery_operations=({
            "operation_id": "recovery-1",
            "operation_kind": "resume",
            "restored_checkpoint_id": "checkpoint-1",
            "source_head_sequence": 8,
            "status": "succeeded",
            "prepared_at": "2026-08-09T00:00:12+00:00",
            "finished_at": "2026-08-09T00:00:13+00:00",
        },),
        context_manifest={
            "available": True,
            "source": "prompt_render_log",
            "manifest": _manifest(),
            "fingerprint": {},
        },
    ))

    kinds = {span["kind"] for span in view["spans"]}
    assert {
        "artifact",
        "cache",
        "delivery",
        "llm",
        "mcp",
        "memory",
        "prompt",
        "run",
        "sandbox",
        "subagent",
        "tool",
    } <= kinds
    mcp_span = next(span for span in view["spans"] if span["kind"] == "mcp")
    assert mcp_span["parent_span_id"] == "tool:tool-1"
    assert view["turn_ids"] == ["turn-1"]
    assert view["waterfall"]["totals"] == {
        "input_tokens": 200,
        "output_tokens": 30,
        "cache_hit_tokens": 80,
        "cache_miss_tokens": 20,
        "cost_microusd": 42,
    }
    assert view["retries"][0]["from_span_id"] == "llm:1"
    assert view["retries"][0]["to_span_id"] == "llm:2"
    assert view["context_manifest"]["manifest"]["sha256"]
    assert view["redaction"]["hidden_reasoning"] == "omitted"
    serialized = json.dumps(view, ensure_ascii=False)
    for forbidden in (
        "模型隐藏推理正文",
        "原始工具参数",
        "原始工具结果",
        "sandbox command --secret",
        "credential-value",
    ):
        assert forbidden not in serialized


def test_run_view_does_not_admit_unknown_runtime_attributes():
    view = build_run_view(RunViewSource(
        run_id="run-safe",
        run={"status": "success"},
        runtime_events=(
            _runtime_event(
                "unsafe-event",
                name="memory.retrieve",
                domain="memory",
                phase="succeeded",
                occurred_at="2026-08-09T00:00:00+00:00",
                attributes={
                    "provider": "hybrid",
                    "query": "不能展示的查询正文",
                    "reasoning_content": "不能展示的隐藏推理",
                    "secret": "credential-value",
                },
            ),
        ),
    ))

    serialized = json.dumps(view, ensure_ascii=False)
    assert "不能展示的查询正文" not in serialized
    assert "不能展示的隐藏推理" not in serialized
    assert "credential-value" not in serialized


def test_run_view_keeps_unique_fallback_ids_and_cancelled_terminal_status():
    view = build_run_view(RunViewSource(
        run_id="run-fallback",
        run={"status": "running"},
        tool_calls=(
            {"tool_name": "first", "status": "success"},
            {"tool_name": "second", "status": "success"},
        ),
        runtime_events=(
            _runtime_event(
                "cancelled-start",
                name="subagent.execute",
                domain="subagent",
                phase="started",
                occurred_at="2026-08-09T00:00:00+00:00",
                task_id="task-cancelled",
                task_run_id="child-cancelled",
                attributes={
                    "child_run_id": "child-cancelled",
                    "task_id": "task-cancelled",
                    "role_id": "worker",
                    "attempt_no": 1,
                    "model": "model-a",
                },
            ),
            _runtime_event(
                "cancelled-finish",
                name="subagent.execute",
                domain="subagent",
                phase="failed",
                occurred_at="2026-08-09T00:00:01+00:00",
                task_id="task-cancelled",
                task_run_id="child-cancelled",
                attributes={
                    "child_run_id": "child-cancelled",
                    "task_id": "task-cancelled",
                    "role_id": "worker",
                    "attempt_no": 1,
                    "model": "model-a",
                    "status": "cancelled",
                    "failure_code": "subagent_execution_interrupted",
                },
            ),
        ),
    ))

    tool_ids = [
        span["span_id"] for span in view["spans"] if span["kind"] == "tool"
    ]
    assert tool_ids == ["tool:1", "tool:2"]
    subagent = next(
        span for span in view["spans"] if span["kind"] == "subagent"
    )
    assert subagent["status"] == "cancelled"
    assert subagent["failure"]["code"] == "subagent_execution_interrupted"
