"""统一定时任务程序与持久执行器回归测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from core.database import (
    ScheduledTask,
    ScheduledTaskExecution,
    ScheduledTaskStepAttempt,
)
from core.scheduled_task_contract import (
    ScheduledTaskContractError,
    apply_scheduled_task_owner,
    apply_scheduled_task_program,
    normalize_scheduled_task_program,
    scheduled_task_owner_from_target,
)
from core.scheduled_workflow import (
    ScheduledWorkflowStepOutcome,
    claim_scheduled_task_executions,
    enqueue_scheduled_task_execution,
    execute_claimed_scheduled_task,
)
from tests.async_helpers import run_async


NOW = datetime(2026, 7, 29, 4, 0, 0)


class _Callbacks:
    def __init__(self) -> None:
        self.tool_calls: list[tuple[str, dict, str]] = []
        self.model_calls: list[tuple[str, str]] = []
        self.emit_calls: list[tuple[str, str, str]] = []

    async def execute_tool(
        self,
        _context,
        *,
        tool_name,
        args,
        idempotency_key,
    ):
        self.tool_calls.append((tool_name, args, idempotency_key))
        return ScheduledWorkflowStepOutcome(
            output={"value": args.get("value")},
            tool_call_id=f"tool-{len(self.tool_calls)}",
        )

    async def execute_model(
        self,
        _context,
        *,
        prompt,
        idempotency_key,
    ):
        self.model_calls.append((prompt, idempotency_key))
        return ScheduledWorkflowStepOutcome(
            output=f"模型：{prompt}",
            model_trace_id="trace-model-1",
            agent_run_id="run-model-1",
        )

    async def emit(
        self,
        _context,
        *,
        content,
        idempotency_key,
        model_trace_id,
    ):
        self.emit_calls.append(
            (content, idempotency_key, model_trace_id)
        )
        return ScheduledWorkflowStepOutcome(
            output={"queued": True},
            outbound_run_id=42,
            model_trace_id=model_trace_id,
        )


def _seed_task(
    db,
    program: dict,
    *,
    owner_id: str = "workflow-user",
) -> ScheduledTask:
    task = ScheduledTask(
        name="统一任务",
        cron_expr="0 12 * * *",
        schedule_kind="cron",
        schedule_spec="",
        target_type="private",
        target_id=owner_id,
        prompt_template="",
        enabled=1,
        delivery_status="idle",
    )
    apply_scheduled_task_owner(
        task,
        scheduled_task_owner_from_target(
            target_type="private",
            target_id=owner_id,
            created_by_actor_id=owner_id,
        ),
    )
    apply_scheduled_task_program(
        task,
        name=task.name,
        program=program,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _factory(db):
    return sessionmaker(
        bind=db.get_bind(),
        autocommit=False,
        autoflush=False,
    )


def _enqueue_and_claim(db, task: ScheduledTask, *, key: str = "manual-1"):
    queued = enqueue_scheduled_task_execution(
        db,
        task_id=task.id,
        trigger_type="manual",
        manual_idempotency_key=key,
        now=NOW,
    )
    db.commit()
    claims = claim_scheduled_task_executions(
        db,
        owner="test-worker",
        limit=1,
        lease_seconds=60,
        now=NOW,
    )
    assert len(claims) == 1
    assert claims[0].execution_id == queued.execution_id
    return queued, claims[0]


def test_program_rejects_duplicate_step_id_and_recursive_task_tool():
    with pytest.raises(ScheduledTaskContractError, match="重复"):
        normalize_scheduled_task_program({
            "version": 1,
            "steps": [
                {"id": "same", "op": "set", "name": "a", "value": 1},
                {"id": "same", "op": "set", "name": "b", "value": 2},
            ],
        })

    with pytest.raises(ScheduledTaskContractError, match="不能直接调用"):
        normalize_scheduled_task_program({
            "version": 1,
            "steps": [
                {
                    "id": "recursive",
                    "op": "tool",
                    "tool": "schedule_task",
                    "args": {},
                }
            ],
        })

    with pytest.raises(ScheduledTaskContractError, match="引用未声明"):
        normalize_scheduled_task_program({
            "version": 1,
            "steps": [
                {
                    "id": "send",
                    "op": "emit",
                    "content": {"$ref": "variables.missing"},
                }
            ],
        })


def test_deterministic_program_uses_tools_branches_and_loops_without_model(
    db_session,
):
    program = {
        "version": 1,
        "steps": [
            {
                "id": "seed",
                "op": "set",
                "name": "items",
                "value": ["甲", "乙"],
            },
            {
                "id": "walk",
                "op": "loop",
                "items": {"$ref": "variables.items"},
                "item": "current",
                "index": "position",
                "max_iterations": 2,
                "steps": [
                    {
                        "id": "inspect",
                        "op": "tool",
                        "tool": "inspect_image",
                        "args": {
                            "value": {"$ref": "variables.current"}
                        },
                        "recovery": "safe_retry",
                        "max_attempts": 1,
                    }
                ],
            },
            {
                "id": "choose",
                "op": "branch",
                "condition": {
                    "$eq": [
                        {"$ref": "variables.position"},
                        1,
                    ]
                },
                "then": [
                    {
                        "id": "message",
                        "op": "set",
                        "name": "content",
                        "value": "确定性完成",
                    }
                ],
                "else": [
                    {
                        "id": "fallback",
                        "op": "set",
                        "name": "content",
                        "value": "未完成",
                    }
                ],
            },
            {
                "id": "send",
                "op": "emit",
                "content": {"$ref": "variables.content"},
            },
        ],
        "limits": {
            "max_steps": 20,
            "max_loop_iterations": 2,
            "max_duration_seconds": 60,
        },
    }
    task = _seed_task(db_session, program)
    queued, claim = _enqueue_and_claim(db_session, task)
    callbacks = _Callbacks()

    status = run_async(execute_claimed_scheduled_task(
        db_session,
        claim=claim,
        callbacks=callbacks,
        session_factory=_factory(db_session),
        clock=lambda: NOW,
    ))

    db_session.expire_all()
    execution = db_session.get(
        ScheduledTaskExecution,
        queued.execution_id,
    )
    state = json.loads(execution.state_json)
    assert status == "succeeded"
    assert execution.status == "succeeded"
    assert callbacks.model_calls == []
    assert [call[1]["value"] for call in callbacks.tool_calls] == [
        "甲",
        "乙",
    ]
    assert callbacks.emit_calls[0][0] == "确定性完成"
    assert state["variables"]["position"] == 1
    assert execution.outbound_run_id == 42


def test_model_trace_is_forwarded_only_to_emit(db_session):
    task = _seed_task(db_session, {
        "version": 1,
        "steps": [
            {
                "id": "generate",
                "op": "model",
                "prompt": "生成摘要",
                "save_as": "summary",
                "max_attempts": 1,
            },
            {
                "id": "send",
                "op": "emit",
                "content": {"$ref": "variables.summary"},
            },
        ],
    })
    _queued, claim = _enqueue_and_claim(db_session, task)
    callbacks = _Callbacks()

    status = run_async(execute_claimed_scheduled_task(
        db_session,
        claim=claim,
        callbacks=callbacks,
        session_factory=_factory(db_session),
        clock=lambda: NOW,
    ))

    assert status == "succeeded"
    assert callbacks.model_calls == [
        ("生成摘要", callbacks.model_calls[0][1])
    ]
    assert callbacks.emit_calls == [
        (
            "模型：生成摘要",
            callbacks.emit_calls[0][1],
            "trace-model-1",
        )
    ]


def test_wait_parks_and_resumes_from_checkpoint(db_session):
    task = _seed_task(db_session, {
        "version": 1,
        "steps": [
            {"id": "pause", "op": "wait", "seconds": 5},
            {"id": "send", "op": "emit", "content": "醒来"},
        ],
    })
    queued, claim = _enqueue_and_claim(db_session, task)
    callbacks = _Callbacks()
    current = [NOW]

    first = run_async(execute_claimed_scheduled_task(
        db_session,
        claim=claim,
        callbacks=callbacks,
        session_factory=_factory(db_session),
        clock=lambda: current[0],
    ))
    db_session.expire_all()
    waiting = db_session.get(ScheduledTaskExecution, queued.execution_id)
    assert first == "waiting"
    assert waiting.status == "waiting"
    assert callbacks.emit_calls == []

    current[0] = NOW + timedelta(seconds=5)
    claims = claim_scheduled_task_executions(
        db_session,
        owner="test-worker-2",
        limit=1,
        lease_seconds=60,
        now=current[0],
    )
    assert len(claims) == 1
    second = run_async(execute_claimed_scheduled_task(
        db_session,
        claim=claims[0],
        callbacks=callbacks,
        session_factory=_factory(db_session),
        clock=lambda: current[0],
    ))

    db_session.expire_all()
    assert second == "succeeded"
    assert db_session.get(
        ScheduledTaskExecution,
        queued.execution_id,
    ).status == "succeeded"
    assert len(callbacks.emit_calls) == 1
    assert (
        db_session.query(ScheduledTaskStepAttempt)
        .filter_by(execution_id=queued.execution_id, step_id="pause")
        .count()
        == 1
    )


class _SimulatedWorkerCrash(BaseException):
    pass


class _CrashCallbacks(_Callbacks):
    def __init__(self, *, crash: bool) -> None:
        super().__init__()
        self.crash = crash

    async def execute_tool(
        self,
        _context,
        *,
        tool_name,
        args,
        idempotency_key,
    ):
        self.tool_calls.append((tool_name, args, idempotency_key))
        if self.crash:
            raise _SimulatedWorkerCrash()
        return ScheduledWorkflowStepOutcome(output="完成")


def _tool_program(*, recovery: str) -> dict:
    return {
        "version": 1,
        "steps": [
            {
                "id": "effect",
                "op": "tool",
                "tool": "external_effect",
                "args": {},
                "recovery": recovery,
                "max_attempts": 1,
                "idempotency_arg": "idempotency_key",
            }
        ],
    }


def test_unknown_tool_result_stops_as_ambiguous_after_lease_takeover(
    db_session,
):
    task = _seed_task(db_session, _tool_program(recovery="ambiguous"))
    queued, first_claim = _enqueue_and_claim(db_session, task)
    crashing = _CrashCallbacks(crash=True)

    with pytest.raises(_SimulatedWorkerCrash):
        run_async(execute_claimed_scheduled_task(
            db_session,
            claim=first_claim,
            callbacks=crashing,
            session_factory=_factory(db_session),
            lease_seconds=1,
            clock=lambda: NOW,
        ))

    takeover_at = NOW + timedelta(seconds=61)
    claims = claim_scheduled_task_executions(
        db_session,
        owner="takeover-worker",
        limit=1,
        lease_seconds=60,
        now=takeover_at,
    )
    assert len(claims) == 1
    forbidden = _CrashCallbacks(crash=False)
    status = run_async(execute_claimed_scheduled_task(
        db_session,
        claim=claims[0],
        callbacks=forbidden,
        session_factory=_factory(db_session),
        clock=lambda: takeover_at,
    ))

    db_session.expire_all()
    execution = db_session.get(
        ScheduledTaskExecution,
        queued.execution_id,
    )
    assert status == "ambiguous"
    assert execution.status == "ambiguous"
    assert forbidden.tool_calls == []


def test_safe_retry_reuses_stable_step_idempotency_key_after_takeover(
    db_session,
):
    task = _seed_task(db_session, _tool_program(recovery="safe_retry"))
    queued, first_claim = _enqueue_and_claim(db_session, task)
    crashing = _CrashCallbacks(crash=True)

    with pytest.raises(_SimulatedWorkerCrash):
        run_async(execute_claimed_scheduled_task(
            db_session,
            claim=first_claim,
            callbacks=crashing,
            session_factory=_factory(db_session),
            lease_seconds=1,
            clock=lambda: NOW,
        ))

    takeover_at = NOW + timedelta(seconds=61)
    claims = claim_scheduled_task_executions(
        db_session,
        owner="takeover-worker",
        limit=1,
        lease_seconds=60,
        now=takeover_at,
    )
    recovered = _CrashCallbacks(crash=False)
    status = run_async(execute_claimed_scheduled_task(
        db_session,
        claim=claims[0],
        callbacks=recovered,
        session_factory=_factory(db_session),
        clock=lambda: takeover_at,
    ))

    db_session.expire_all()
    attempts = (
        db_session.query(ScheduledTaskStepAttempt)
        .filter_by(execution_id=queued.execution_id, step_id="effect")
        .order_by(ScheduledTaskStepAttempt.attempt_no.asc())
        .all()
    )
    assert status == "succeeded"
    assert [attempt.status for attempt in attempts] == [
        "failed",
        "succeeded",
    ]
    assert attempts[0].idempotency_key == attempts[1].idempotency_key
    assert crashing.tool_calls[0][2] == recovered.tool_calls[0][2]
    assert (
        recovered.tool_calls[0][1]["idempotency_key"]
        == recovered.tool_calls[0][2]
    )


def test_occurrence_enqueue_is_idempotent(db_session):
    task = _seed_task(db_session, {
        "version": 1,
        "steps": [{"id": "send", "op": "emit", "content": "固定消息"}],
    })

    first = enqueue_scheduled_task_execution(
        db_session,
        task_id=task.id,
        trigger_type="manual",
        manual_idempotency_key="same-request",
        now=NOW,
    )
    db_session.commit()
    second = enqueue_scheduled_task_execution(
        db_session,
        task_id=task.id,
        trigger_type="manual",
        manual_idempotency_key="same-request",
        now=NOW + timedelta(minutes=1),
    )

    assert not first.deduplicated
    assert second.deduplicated
    assert second.execution_id == first.execution_id
    assert db_session.query(ScheduledTaskExecution).count() == 1


def test_claims_serialize_same_owner_but_allow_different_owners(db_session):
    program = {
        "version": 1,
        "steps": [{"id": "send", "op": "emit", "content": "固定消息"}],
    }
    first = _seed_task(db_session, program, owner_id="same-owner")
    second = _seed_task(db_session, program, owner_id="same-owner")
    other = _seed_task(db_session, program, owner_id="other-owner")
    for task, key in (
        (first, "first"),
        (second, "second"),
        (other, "other"),
    ):
        enqueue_scheduled_task_execution(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key=key,
            now=NOW,
        )
    db_session.commit()

    claims = claim_scheduled_task_executions(
        db_session,
        owner="concurrent-worker",
        limit=3,
        lease_seconds=60,
        now=NOW,
    )

    assert len(claims) == 2
    assert {
        claim.owner_chat_stream_id for claim in claims
    } == {
        "qq:same-owner:private",
        "qq:other-owner:private",
    }
