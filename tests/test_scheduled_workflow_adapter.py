"""定时任务 KT 生产适配器回归测试。"""

from __future__ import annotations

from datetime import datetime
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
