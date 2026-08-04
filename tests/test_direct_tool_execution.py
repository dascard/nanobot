from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_direct_tool_execution_uses_port_and_preserves_runtime_identity(
    monkeypatch,
):
    from core.agent_runtime.request_scope import require_current_runtime_context
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
        ensure_executable=executable_names.append,
    )
    finish_runs = []
    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    monkeypatch.setattr("core.tool_plan.build_tool_plan", lambda **_kwargs: tool_plan)
    monkeypatch.setattr("core.tracing.new_trace_id", lambda: "trace-direct")
    monkeypatch.setattr(
        "core.tracing.RunTracer.start_run",
        lambda **_kwargs: SimpleNamespace(run_id="run-direct"),
    )
    monkeypatch.setattr(
        "core.tracing.RunTracer.finish_run",
        lambda run_id, **kwargs: finish_runs.append((run_id, kwargs)),
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
        metadata={"platform": "qq", "request_id": "request-direct"},
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
    assert captured["trace_reset"] == "trace-token"
    assert captured["correlation_reset"] == "correlation-token"
