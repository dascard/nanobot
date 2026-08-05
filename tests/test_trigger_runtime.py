"""Trigger envelope、预算和权威 Run 事件回归。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core.agent_runtime.contracts import RuntimePrincipal
from core.agent_runtime.errors import AgentRuntimeBudgetExceededError
from core.database import (
    AgentRun,
    OutboundDeliveryControl,
    ProactiveOutreachLog,
    RunLedgerEventRow,
)
from core.trigger_runtime import (
    TriggerContractError,
    TriggerEnvelope,
    TriggerKind,
    TriggerRunContext,
    build_trigger_envelope,
)
from core.tracing import RunTracer


def _envelope(
    *,
    kind: TriggerKind = TriggerKind.EVENT,
    occurred_at: datetime | None = None,
):
    return build_trigger_envelope(
        kind=kind,
        source_type="proactive_outreach",
        source_ref="trusted-source:user-1:slot-1",
        idempotency_key="trusted-idempotency:user-1:slot-1",
        principal=RuntimePrincipal("qq", "user", "user-1"),
        allowed_tools=("inspect_image",),
        delivery_endpoints=("qq_push",),
        allowed_subagents=("proactive_research",),
        max_model_calls=3,
        max_steps=8,
        timeout_seconds=180,
        max_subagents=1,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        ttl_seconds=180,
    )


def test_trigger_envelope_round_trip_is_redacted_and_cannot_expand_access():
    envelope = _envelope(kind=TriggerKind.HEARTBEAT)

    restored = TriggerEnvelope.from_mapping(envelope.to_dict())

    assert restored == envelope
    assert restored.kind is TriggerKind.HEARTBEAT
    assert "trusted-source:user-1" not in str(restored.to_dict())
    restored.assert_tool("inspect_image")
    restored.assert_delivery("qq_push")
    restored.assert_subagent("proactive_research")
    restored.assert_owner(RuntimePrincipal("qq", "user", "user-1"))
    with pytest.raises(TriggerContractError, match="未授权工具"):
        restored.assert_tool("schedule_task")
    with pytest.raises(TriggerContractError, match="未授权投递"):
        restored.assert_delivery("arbitrary_webhook")
    with pytest.raises(TriggerContractError, match="不得切换"):
        restored.assert_owner(RuntimePrincipal("qq", "user", "other-user"))
    constraint = restored.tool_constraint(("inspect_image",))
    assert constraint.allowed_tool_names == frozenset({"inspect_image"})
    constraint.assert_owner(RuntimePrincipal("qq", "user", "user-1"))
    with pytest.raises(TriggerContractError, match="未授权工具"):
        restored.tool_constraint(("schedule_task",))


def test_trigger_envelope_rejects_wildcards_and_expiration():
    with pytest.raises((TriggerContractError, ValueError), match="通配符"):
        build_trigger_envelope(
            kind=TriggerKind.MANUAL,
            source_type="scheduled_task",
            source_ref="task:1",
            idempotency_key="task:1:manual",
            principal=RuntimePrincipal("qq", "user", "user-1"),
            allowed_tools=("workspace_*",),
            max_model_calls=1,
            max_steps=2,
            timeout_seconds=60,
        )

    occurred_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    expired = _envelope(occurred_at=occurred_at)
    with pytest.raises(TriggerContractError, match="已过期"):
        expired.assert_active(now=datetime.now(timezone.utc))


def test_plain_trace_metadata_cannot_forge_trigger_binding(db_session):
    handle = RunTracer.start_run(
        session_id="untrusted-trigger-fields",
        user_id="user-1",
        meta={
            "trigger_id": "trigger-forged",
            "trigger_type": "event",
            "trigger_sha256": "a" * 64,
            "governance_sha256": "b" * 64,
        },
    )
    RunTracer.finish_run(
        handle.run_id,
        task_lease=handle.task_lease,
        status="success",
    )

    event_types = [
        row.event_type
        for row in (
            db_session.query(RunLedgerEventRow)
            .filter(RunLedgerEventRow.run_id == handle.run_id)
            .order_by(RunLedgerEventRow.sequence.asc())
            .all()
        )
    ]
    assert event_types == [
        "run.accepted",
        "run.status_changed",
        "run.terminated",
    ]


@pytest.mark.asyncio
async def test_trigger_run_is_ledgered_and_enforces_hard_budget(db_session):
    from core import database

    envelope = _envelope(kind=TriggerKind.EVENT)
    runtime = await TriggerRunContext.start(
        envelope,
        session_factory=database.SessionLocal,
        evaluated_status="due",
    )
    runtime.reserve_model("proactive_judge")
    runtime.reserve_model("proactive_generate")
    runtime.reserve_model("proactive_quality")
    with pytest.raises(AgentRuntimeBudgetExceededError):
        runtime.reserve_model("proactive_unplanned_retry")
    delivery = runtime.reserve_delivery("qq_push")
    runtime.release(delivery)
    runtime.mark_delivery_committed("queued")
    await runtime.finish(
        status="success",
        output={"status": "queued", "log_id": 7},
    )

    db_session.expire_all()
    run = (
        db_session.query(AgentRun)
        .filter(AgentRun.run_id == runtime.run_id)
        .one()
    )
    events = (
        db_session.query(RunLedgerEventRow)
        .filter(RunLedgerEventRow.run_id == runtime.run_id)
        .order_by(RunLedgerEventRow.sequence.asc())
        .all()
    )
    event_types = [event.event_type for event in events]
    assert run.status == "success"
    assert event_types[:3] == [
        "run.accepted",
        "run.status_changed",
        "trigger.bound",
    ]
    assert "trigger.phase_changed" in event_types
    assert "budget.decision_recorded" in event_types
    assert event_types[-1] == "run.terminated"
    assert all("trusted-source:user-1" not in event.payload_json for event in events)


@pytest.mark.asyncio
async def test_trigger_run_finish_retries_without_duplicate_terminal_phase(
    db_session,
    monkeypatch,
):
    from core import database

    runtime = await TriggerRunContext.start(
        _envelope(),
        session_factory=database.SessionLocal,
    )
    real_flush = runtime.ledger.flush
    attempts = 0

    def flaky_flush() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient ledger failure")
        real_flush()

    monkeypatch.setattr(runtime.ledger, "flush", flaky_flush)
    with pytest.raises(RuntimeError, match="transient ledger failure"):
        await runtime.finish(status="success")
    with pytest.raises(TriggerContractError, match="不得改变终态"):
        await runtime.finish(status="failed")

    await runtime.finish(status="success")

    db_session.expire_all()
    phases = [
        json.loads(row.payload_json)["phase"]
        for row in (
            db_session.query(RunLedgerEventRow)
            .filter(
                RunLedgerEventRow.run_id == runtime.run_id,
                RunLedgerEventRow.event_type == "trigger.phase_changed",
            )
            .order_by(RunLedgerEventRow.sequence.asc())
            .all()
        )
    ]
    assert attempts == 2
    assert phases.count("completed") == 1
    assert db_session.query(AgentRun).filter(
        AgentRun.run_id == runtime.run_id,
    ).one().status == "success"


@pytest.mark.asyncio
async def test_proactive_event_uses_trigger_run_and_keeps_runtime_metadata_out_of_prompt(
    db_session,
):
    from core.proactive.serialization import grounding_json_for_model
    from core.proactive_outreach import run_outreach_once

    db_session.add(OutboundDeliveryControl(
        source_type="proactive_outreach",
        mode="legacy_direct",
        cutover_epoch=0,
        effective_from=datetime(1970, 1, 1),
        protocol_version=2,
        writer_version=0,
    ))
    db_session.commit()
    now = datetime(2026, 8, 5, 12, 0, 0)

    result = await run_outreach_once(
        "trigger-user",
        db=db_session,
        now=now,
        judge_fn=lambda _grounding, *, now, **_kwargs: {
            "should_reach_out": False,
            "reason": "本轮无需外呼",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "topic_type": "none",
            "topic": "",
            "evidence_ids": [],
            "error_type": None,
        },
    )

    row = (
        db_session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == "trigger-user")
        .one()
    )
    grounding = json.loads(row.grounding_json)
    assert result["status"] == "pending"
    trigger_run_id = grounding["_trigger_runtime"]["run_id"]
    assert trigger_run_id
    assert db_session.query(AgentRun).filter(
        AgentRun.run_id == trigger_run_id,
    ).one().status == "success"
    assert "trigger-user" not in str(grounding["_trigger_runtime"])
    assert "_trigger_runtime" not in grounding_json_for_model(grounding)


@pytest.mark.asyncio
async def test_proactive_daily_quota_blocks_before_judge(db_session):
    from core.proactive_outreach import run_outreach_once

    db_session.add(OutboundDeliveryControl(
        source_type="proactive_outreach",
        mode="legacy_direct",
        cutover_epoch=0,
        effective_from=datetime(1970, 1, 1),
        protocol_version=2,
        writer_version=0,
    ))
    now = datetime(2026, 8, 5, 12, 0, 0)
    db_session.add_all([
        ProactiveOutreachLog(
            user_id="quota-user",
            idempotency_key=f"quota-{index}",
            status="sent",
            message="已发送",
            created_at=now - timedelta(hours=index),
        )
        for index in (1, 2)
    ])
    db_session.commit()

    run_count_before = db_session.query(AgentRun).count()
    result = await run_outreach_once(
        "quota-user",
        db=db_session,
        now=now,
        daily_delivery_quota=2,
        min_interval_min=1,
        judge_fn=lambda *_args, **_kwargs: pytest.fail("配额门禁后不得调用 Judge"),
    )

    assert result["status"] == "skipped_daily_quota"
    assert result["daily_delivery_count"] == 2
    assert db_session.query(AgentRun).count() == run_count_before + 1
