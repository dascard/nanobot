from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_direct_tool_execution_uses_port_and_preserves_runtime_identity(
    monkeypatch,
):
    from core.agent_runtime.contracts import RuntimePrincipal
    from core.agent_runtime.request_scope import require_current_runtime_context
    from core.trigger_runtime import TriggerKind, build_trigger_envelope
    from nanobot_kt.direct_tool_execution import execute_registered_tool

    captured = {}

    class PublicTool:
        config = SimpleNamespace(max_output=1024)

        async def execute(self, args, context=None):
            captured["args"] = args
            captured["tool_context"] = context
            captured["runtime_context"] = require_current_runtime_context()
            return SimpleNamespace(
                output="查询完成",
                error=None,
                exit_code=0,
                metadata={"structured_content": {"status": "success"}},
            )

    class ForbiddenExecutor:
        def __getattribute__(self, name):
            raise AssertionError(f"不应访问 KT executor.{name}")

    agent = SimpleNamespace(
        registry=SimpleNamespace(
            get_tool=lambda name: PublicTool() if name == "memory_query" else None,
        ),
        executor=ForbiddenExecutor(),
        session_store=None,
    )
    bridge = SimpleNamespace(_agent=agent)

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = None
            return self

        def __exit__(self, *_exc):
            return False

    executable_names = []
    tool_plan = SimpleNamespace(
        sha256="a" * 64,
        executable_tool_names=frozenset({"memory_query"}),
        ensure_executable=executable_names.append,
    )
    finish_runs = []
    started_runs = []
    task_lease = object()

    trigger_binding = build_trigger_envelope(
        kind=TriggerKind.SCHEDULE,
        source_type="scheduled_task",
        source_ref="direct-tool:test",
        idempotency_key="direct-tool:test:1",
        principal=RuntimePrincipal("qq", "user", "user-1"),
        allowed_tools=("memory_query",),
        max_model_calls=0,
        max_steps=1,
        timeout_seconds=5,
    ).run_binding()

    class FakeRunTaskOwner:
        def __init__(self, lease):
            assert lease is task_lease
            self.lease = lease

        async def start(self):
            captured["task_owner_started"] = True

        async def stop(self):
            captured["task_owner_stopped"] = True

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    monkeypatch.setattr("core.tool_plan.build_tool_plan", lambda **_kwargs: tool_plan)
    monkeypatch.setattr("core.tracing.new_trace_id", lambda: "trace-direct")
    def fake_start_run(**kwargs):
        started_runs.append(kwargs)
        return SimpleNamespace(
            run_id="run-direct",
            task_lease=task_lease,
        )

    monkeypatch.setattr("core.tracing.RunTracer.start_run", fake_start_run)
    monkeypatch.setattr(
        "core.tracing.RunTracer.finish_run",
        lambda run_id, **kwargs: finish_runs.append((run_id, kwargs)),
    )
    monkeypatch.setattr(
        "core.durable_tasks.RunTaskOwner",
        FakeRunTaskOwner,
    )
    from core.agent_runtime import (
        RuntimePermissionOutcome,
        StaticPermissionPort,
    )

    monkeypatch.setattr(
        "core.permissions.default_session_permission_port",
        lambda: StaticPermissionPort({
            "tool.execute": RuntimePermissionOutcome.ALLOW,
        }),
    )

    class FakeBudgetSink:
        def __init__(self, _factory):
            pass

        def emit(self, decision):
            captured.setdefault("budget_decisions", []).append(decision)

    monkeypatch.setattr(
        "core.run_ledger.sinks.SqlAlchemyRuntimeBudgetDecisionSink",
        FakeBudgetSink,
    )
    monkeypatch.setattr(
        "core.tracing_context.set_trace_context",
        lambda *_args: "trace-token",
    )
    monkeypatch.setattr(
        "core.tracing_context.reset_trace_context",
        lambda token: captured.setdefault("trace_reset", token),
    )
    monkeypatch.setattr(
        "core.tracing_context.set_runtime_correlation",
        lambda **_kwargs: "correlation-token",
    )
    monkeypatch.setattr(
        "core.tracing_context.reset_runtime_correlation",
        lambda token: captured.setdefault("correlation_reset", token),
    )
    monkeypatch.setattr(
        "nanobot_kt.tool_execution_adapter.begin_tool_trace",
        lambda _name, _args, *, tool_call_id="": (tool_call_id, 1.0),
    )
    monkeypatch.setattr(
        "nanobot_kt.tool_execution_adapter.finish_tool_trace",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "nanobot_kt.tool_execution_adapter.set_tool_trace_context",
        lambda tool_call_id: f"tool-token:{tool_call_id}",
    )
    monkeypatch.setattr(
        "nanobot_kt.tool_execution_adapter.reset_tool_trace_context",
        lambda token: captured.setdefault("tool_reset", token),
    )

    result = await execute_registered_tool(
        bridge,
        "memory_query",
        {"query": "运行时"},
        user_id="user-1",
        session_id="private_user-1",
        metadata={
            "platform": "qq",
            "request_id": "request-direct",
            "_trigger_run_binding": trigger_binding,
        },
        idempotency_key="idem-direct",
        trace_id="trace-direct",
        timeout_seconds=5,
    )

    assert result.success is True
    assert result.output == "查询完成"
    assert result.trace_id == "trace-direct"
    assert result.run_id == "run-direct"
    assert result.tool_call_id.startswith("tool-")
    assert executable_names == ["memory_query"]
    assert captured["args"] == {"query": "运行时"}
    assert captured["tool_context"] is None
    assert captured["runtime_context"] == {
        "chat_type": "private",
        "runtime_chat_type": "private",
        "is_group": False,
        "is_super_user": False,
        "session_id": "private_user-1",
        "group_id": "",
        "user_id": "user-1",
        "platform": "qq",
        "sender_name": "定时任务",
        "trace_id": "trace-direct",
        "run_id": "run-direct",
        "turn_id": "request-direct",
        "correlation_id": "request-direct",
        "actor_type": "system",
        "actor_id": "scheduled-task",
        "actor_parent_id": "user-1",
        "owner_type": "user",
        "owner_id": "user-1",
        "message_id": "request-direct",
        "workflow_idempotency_key": "idem-direct",
    }
    assert finish_runs[0][0] == "run-direct"
    assert finish_runs[0][1]["status"] == "success"
    assert finish_runs[0][1]["task_lease"] is task_lease
    assert started_runs[0]["meta"]["_trigger_run_binding"] == trigger_binding
    assert finish_runs[0][1]["meta"]["trigger_id"] == (
        trigger_binding.trigger_id
    )
    assert finish_runs[0][1]["meta"]["trigger_sha256"] == (
        trigger_binding.trigger_sha256
    )
    assert captured["task_owner_started"] is True
    assert captured["task_owner_stopped"] is True
    assert any(
        decision.operation == "tool_reservation"
        for decision in captured["budget_decisions"]
    )
    assert captured["trace_reset"] == "trace-token"
    assert captured["correlation_reset"] == "correlation-token"
