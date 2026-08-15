"""定时任务 KT 生产适配器回归测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.orm import sessionmaker

from core.scheduled_task_outbound import (
    ScheduledOccurrence,
    ScheduledTaskSnapshot,
)
from core.scheduled_workflow import ScheduledWorkflowContext
from nanobot_kt.scheduled_workflow_adapter import (
    KtScheduledWorkflowCallbacks,
    _workflow_tool_output,
)
from tests.async_helpers import run_async


def _snapshot() -> ScheduledTaskSnapshot:
    return ScheduledTaskSnapshot(
        schema_version=3,
        task_id=1,
        name="测试任务",
        cron_expr="0 9 * * *",
        schedule_kind="cron",
        schedule_spec="",
        timezone="Asia/Shanghai",
        target_type="private",
        target_id="u1",
        prompt_template="旧提示",
        program={
            "version": 1,
            "steps": [
                {
                    "id": "model",
                    "op": "model",
                    "prompt": "检查",
                    "save_as": "model_output",
                    "max_attempts": 1,
                }
            ],
            "limits": {
                "max_steps": 100,
                "max_loop_iterations": 100,
                "max_duration_seconds": 600,
            },
        },
        program_sha256="a" * 64,
        owner_chat_stream_id="qq:u1:private",
        owner_platform="qq",
        owner_chat_type="private",
        owner_session_id="u1",
        created_by_actor_id="u1",
        definition_version=1,
        enabled=True,
    )


def _context() -> ScheduledWorkflowContext:
    return ScheduledWorkflowContext(
        execution_id=9,
        task_snapshot=_snapshot(),
        occurrence=ScheduledOccurrence(
            occurrence_key="manual:1",
            scheduled_for=datetime(2026, 7, 30, 8, 0, 0),
        ),
        trigger_type="manual",
        runtime_step_id="model",
        static_step_id="model",
    )


def test_workflow_tool_output_preserves_structured_content():
    result = SimpleNamespace(
        output='{"status":"success"}',
        metadata={
            "structured_content": {
                "status": "success",
                "data": {
                    "stdout": '{"action":"noop"}',
                    "items": ["甲", "乙"],
                },
            }
        },
    )

    assert _workflow_tool_output(result) == {
        "status": "success",
        "data": {
            "stdout": '{"action":"noop"}',
            "items": ["甲", "乙"],
        },
    }


def test_no_reply_is_successful_stop_and_keeps_trace(
    monkeypatch,
    db_session,
):
    from core.database import AgentRun
    from core import daily_digest

    factory = sessionmaker(
        bind=db_session.get_bind(),
        autocommit=False,
        autoflush=False,
    )
    captured = {}

    async def fake_generate(
        _task,
        *,
        trace_id="",
        workflow_idempotency_key="",
        task_run_id="",
    ):
        captured.update(
            {
                "trace_id": trace_id,
                "workflow_idempotency_key": workflow_idempotency_key,
                "task_run_id": task_run_id,
            }
        )
        db = factory()
        try:
            db.add(AgentRun(
                run_id="run-no-reply",
                trace_id=trace_id,
                session_id="u1",
                status="no_reply",
            ))
            db.commit()
        finally:
            db.close()
        return None

    monkeypatch.setattr(
        daily_digest,
        "_generate_task_message",
        fake_generate,
    )
    callbacks = KtScheduledWorkflowCallbacks(
        session_factory=factory,
    )

    result = run_async(callbacks.execute_model(
        _context(),
        prompt="没有变化时静默",
        idempotency_key="workflow-key",
    ))

    assert result.success
    assert result.stop is True
    assert result.output == {"status": "no_reply"}
    assert result.agent_run_id == "run-no-reply"
    assert result.model_trace_id == captured["trace_id"]
    assert captured["workflow_idempotency_key"] == "workflow-key"
    assert captured["task_run_id"] == "9"


def test_model_step_passes_frozen_typed_trigger_constraint(
    monkeypatch,
    db_session,
):
    from core import daily_digest
    from core.agent_runtime.contracts import RuntimePrincipal
    from core.database import AgentRun
    from core.trigger_runtime import TriggerKind, build_trigger_envelope

    factory = sessionmaker(
        bind=db_session.get_bind(),
        autocommit=False,
        autoflush=False,
    )
    envelope = build_trigger_envelope(
        kind=TriggerKind.MANUAL,
        source_type="scheduled_task",
        source_ref="manual:1",
        idempotency_key="manual:1",
        principal=RuntimePrincipal("qq", "user", "u1"),
        allowed_tools=("reply",),
        max_model_calls=1,
        max_steps=2,
        timeout_seconds=60,
        occurred_at=datetime.now(timezone.utc),
    )
    context = replace(
        _context(),
        trigger_envelope=envelope,
        model_tool_names=("reply",),
    )
    captured = {}

    async def fake_generate(
        _task,
        *,
        trace_id="",
        workflow_idempotency_key="",
        task_run_id="",
        trigger_constraint=None,
    ):
        captured["constraint"] = trigger_constraint
        db = factory()
        try:
            db.add(AgentRun(
                run_id="run-trigger-model",
                trace_id=trace_id,
                session_id="u1",
                status="success",
            ))
            db.commit()
        finally:
            db.close()
        return "完成"

    monkeypatch.setattr(daily_digest, "_generate_task_message", fake_generate)
    callbacks = KtScheduledWorkflowCallbacks(session_factory=factory)

    result = run_async(callbacks.execute_model(
        context,
        prompt="执行冻结任务",
        idempotency_key="workflow-trigger-key",
    ))

    assert result.success is True
    assert captured["constraint"].binding == envelope.run_binding()
    assert captured["constraint"].principal == envelope.principal
    assert captured["constraint"].allowed_tool_names == frozenset({"reply"})


def test_native_runtime_tool_step_does_not_require_kt_registry(monkeypatch):
    from core.agent_runtime import AgentRuntimeKind

    calls: list[dict[str, object]] = []

    class NativeBridge:
        runtime_kind = AgentRuntimeKind.NATIVE
        agent = None

        async def start(self) -> None:
            calls.append({"phase": "started"})

        async def stop(self) -> None:
            calls.append({"phase": "stopped"})

        async def execute_registered_tool(self, tool_name, args, **kwargs):
            calls.append({
                "phase": "executed",
                "tool_name": tool_name,
                "args": args,
                "kwargs": kwargs,
            })
            return SimpleNamespace(
                success=True,
                output="完成",
                error="",
                metadata={},
                tool_call_id="tool-native",
                run_id="run-native",
            )

    bridge = NativeBridge()
    monkeypatch.setattr(
        "core.agent_runtime.gateway.create_isolated_agent_gateway",
        lambda: bridge,
    )
    callbacks = KtScheduledWorkflowCallbacks(session_factory=lambda: None)

    result = run_async(callbacks.execute_tool(
        _context(),
        tool_name="sandbox_exec",
        args={"command": "true"},
        idempotency_key="native-tool-step",
    ))

    assert result.success is True
    assert result.output == "完成"
    assert result.tool_call_id == "tool-native"
    assert result.agent_run_id == "run-native"
    assert [item["phase"] for item in calls] == [
        "started",
        "executed",
        "stopped",
    ]
