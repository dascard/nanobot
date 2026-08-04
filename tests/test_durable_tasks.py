"""Agent Run Durable Task 的租约、取消、恢复和只读投影测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker


def _now() -> datetime:
    return datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


def _admit_ledger_run(db, run_id: str, *, now: datetime):
    from core.database import AgentRun
    from core.run_ledger.adapters import (
        run_accepted_event,
        run_status_changed_event,
    )
    from core.run_ledger.persistence import SqlAlchemyRunEventLedger

    accepted = run_accepted_event(
        run_id=run_id,
        trace_id=f"trace-{run_id}",
        session_id="private_10001",
        user_id="10001",
        chat_type="private",
        group_id="",
        run_type="chat",
        prompt_mode="prompt",
        prompt_key="chat_private",
        prompt_sha256="",
        model="test-model",
        input_value="测试",
        platform="qq",
        request_id=f"request-{run_id}",
        occurred_at=now,
    )
    ledger = SqlAlchemyRunEventLedger(db)
    ledger.append(accepted, expected_sequence=1)
    ledger.append(
        run_status_changed_event(
            accepted_event=accepted,
            status="running",
            previous_status="accepted",
        ),
        expected_sequence=2,
    )
    row = AgentRun(
        run_id=run_id,
        trace_id=f"trace-{run_id}",
        session_id="private_10001",
        user_id="10001",
        chat_type="private",
        run_type="chat",
        status="running",
        started_at=now.replace(tzinfo=None),
    )
    db.add(row)
    return row


def test_run_task_lease_heartbeat_fencing_and_terminal_settlement(db_session):
    from core.durable_tasks import (
        RunTaskKind,
        RunTaskLease,
        RunTaskLeaseLost,
        RunTaskStatus,
        SqlAlchemyRunTaskService,
    )

    service = SqlAlchemyRunTaskService(db_session)
    lease = service.admit_running(
        run_id="run-task-lease",
        task_kind=RunTaskKind.CHAT,
        source_type="inbound_message",
        source_id="message-1",
        request_id="request-1",
        idempotency_key="request-1",
        owner="worker-a",
        lease_seconds=30,
        timeout_seconds=120,
        now=_now(),
    )
    db_session.commit()

    heartbeat = service.heartbeat(
        lease,
        lease_seconds=30,
        now=_now() + timedelta(seconds=10),
    )
    assert heartbeat.renewed is True
    assert heartbeat.lease is not None
    renewed = heartbeat.lease
    stale = RunTaskLease(
        run_id=renewed.run_id,
        owner=renewed.owner,
        token="f" * 64,
        generation=renewed.generation,
        attempt_no=renewed.attempt_no,
        expires_at=renewed.expires_at,
    )
    with pytest.raises(RunTaskLeaseLost):
        service.settle(
            stale,
            status=RunTaskStatus.SUCCEEDED,
            now=_now() + timedelta(seconds=11),
        )

    view = service.settle(
        renewed,
        status=RunTaskStatus.SUCCEEDED,
        result_ref="run-ledger://run-task-lease",
        now=_now() + timedelta(seconds=12),
    )
    db_session.commit()
    assert view.status is RunTaskStatus.SUCCEEDED
    assert view.lease_owner == ""
    assert view.lease_expires_at is None
    assert "token" not in view.to_dict()["lease"]


def test_run_task_cancel_and_delivery_receipt_are_idempotent(db_session):
    from core.durable_tasks import (
        RunTaskConflict,
        RunTaskHeartbeatReason,
        RunTaskKind,
        RunTaskStatus,
        SqlAlchemyRunTaskService,
    )

    service = SqlAlchemyRunTaskService(db_session)
    lease = service.admit_running(
        run_id="run-task-cancel",
        task_kind=RunTaskKind.SCHEDULED,
        source_type="scheduled_execution",
        source_id="42",
        request_id="scheduled-42",
        idempotency_key="scheduled-42:step-1",
        owner="worker-a",
        now=_now(),
    )
    service.attach_delivery_receipt(
        lease.run_id,
        receipt_ref="outbox://scheduled/42/step-1",
        now=_now(),
    )
    repeated = service.attach_delivery_receipt(
        lease.run_id,
        receipt_ref="outbox://scheduled/42/step-1",
        now=_now(),
    )
    assert repeated.delivery_receipt_ref == "outbox://scheduled/42/step-1"
    with pytest.raises(RunTaskConflict):
        service.attach_delivery_receipt(
            lease.run_id,
            receipt_ref="outbox://scheduled/42/other",
        )

    first = service.request_cancel(
        lease.run_id,
        reason="管理员取消",
        now=_now() + timedelta(seconds=1),
    )
    second = service.request_cancel(
        lease.run_id,
        reason="管理员取消",
        now=_now() + timedelta(seconds=2),
    )
    assert first.cancel_requested_at == second.cancel_requested_at
    heartbeat = service.heartbeat(
        lease,
        now=_now() + timedelta(seconds=3),
    )
    assert heartbeat.reason is RunTaskHeartbeatReason.CANCEL_REQUESTED
    settled = service.settle(
        lease,
        status=RunTaskStatus.CANCELLED,
        terminal_reason="cancel_requested",
        now=_now() + timedelta(seconds=4),
    )
    db_session.commit()
    assert settled.status is RunTaskStatus.CANCELLED


def test_reconcile_timeout_writes_authoritative_terminal_fact(db_session):
    from core.durable_tasks import (
        RunTaskKind,
        RunTaskStatus,
        SqlAlchemyRunTaskService,
        reconcile_expired_run_tasks,
    )
    from core.run_ledger.read_model import load_authoritative_run_view
    from core.run_ledger.persistence import SqlAlchemyRunEventLedger

    now = _now()
    row = _admit_ledger_run(db_session, "run-task-timeout", now=now)
    SqlAlchemyRunTaskService(db_session).admit_running(
        run_id=row.run_id,
        task_kind=RunTaskKind.RESEARCH,
        source_type="research_request",
        source_id="research-1",
        request_id="research-1",
        idempotency_key="research-1",
        owner="worker-a",
        lease_seconds=30,
        timeout_seconds=5,
        now=now,
    )
    db_session.commit()

    assert reconcile_expired_run_tasks(
        db_session,
        now=now + timedelta(seconds=6),
    ) == 1
    view = SqlAlchemyRunTaskService(db_session).get(row.run_id)
    assert view is not None
    assert view.status is RunTaskStatus.TIMED_OUT
    authoritative = load_authoritative_run_view(
        SqlAlchemyRunEventLedger(db_session),
        row.run_id,
    )
    assert authoritative is not None
    assert authoritative.projection.status == "timed_out"
    db_session.refresh(row)
    assert row.status == "timed_out"


def test_reconcile_unknown_side_effect_is_ambiguous(db_session):
    from core.database import RunSideEffectReceipt
    from core.durable_tasks import (
        RunTaskKind,
        RunTaskStatus,
        SqlAlchemyRunTaskService,
        reconcile_expired_run_tasks,
    )

    now = _now()
    row = _admit_ledger_run(db_session, "run-task-ambiguous", now=now)
    SqlAlchemyRunTaskService(db_session).admit_running(
        run_id=row.run_id,
        task_kind=RunTaskKind.BACKGROUND,
        source_type="agent_run",
        source_id=row.run_id,
        request_id=row.run_id,
        idempotency_key=row.run_id,
        owner="worker-a",
        lease_seconds=1,
        timeout_seconds=120,
        now=now,
    )
    db_session.add(RunSideEffectReceipt(
        receipt_id="receipt-ambiguous",
        run_id=row.run_id,
        tool_call_id="tool-1",
        tool_name="external_tool",
        execution_port_id="tool:test",
        effect_class="external",
        state="prepared",
        idempotency_key_sha256="1" * 64,
        request_sha256="2" * 64,
        result_sha256="",
        result_size_bytes=0,
        error_code="",
        checkpoint_before_id="checkpoint-before",
        checkpoint_after_id="",
        file_proofs_json="[]",
        artifact_proofs_json="[]",
        prepared_ledger_sequence=2,
        terminal_ledger_sequence=None,
        prepared_at=now.replace(tzinfo=None),
        settled_at=None,
    ))
    db_session.commit()

    assert reconcile_expired_run_tasks(
        db_session,
        now=now + timedelta(seconds=2),
    ) == 1
    view = SqlAlchemyRunTaskService(db_session).get(row.run_id)
    assert view is not None
    assert view.status is RunTaskStatus.AMBIGUOUS
    assert view.terminal_reason == "lease_expired_with_unknown_effect"


def test_prepared_run_can_only_be_claimed_once_and_cancel_reconciles(db_session):
    from core.durable_tasks import (
        RunTaskConflict,
        RunTaskKind,
        RunTaskStatus,
        SqlAlchemyRunTaskService,
        reconcile_expired_run_tasks,
    )

    now = _now()
    row = _admit_ledger_run(db_session, "run-task-prepared", now=now)
    row.status = "accepted"
    service = SqlAlchemyRunTaskService(db_session)
    service.admit_prepared(
        run_id=row.run_id,
        task_kind=RunTaskKind.RECOVERY,
        source_type="recovery_operation",
        source_id="recovery-1",
        request_id="request-1",
        idempotency_key="recovery-1",
        now=now,
    )
    db_session.commit()
    lease = service.claim_prepared(
        row.run_id,
        owner="worker-a",
        now=now + timedelta(seconds=1),
    )
    db_session.commit()
    assert lease.generation == 1
    with pytest.raises(RunTaskConflict):
        service.claim_prepared(
            row.run_id,
            owner="worker-b",
            now=now + timedelta(seconds=2),
        )

    other = _admit_ledger_run(db_session, "run-task-prepared-cancel", now=now)
    other.status = "accepted"
    service.admit_prepared(
        run_id=other.run_id,
        task_kind=RunTaskKind.RECOVERY,
        source_type="recovery_operation",
        source_id="recovery-2",
        request_id="request-2",
        idempotency_key="recovery-2",
        now=now,
    )
    service.request_cancel(
        other.run_id,
        reason="不再恢复",
        now=now + timedelta(seconds=1),
    )
    db_session.commit()
    assert reconcile_expired_run_tasks(
        db_session,
        now=now + timedelta(seconds=2),
    ) == 1
    cancelled = service.get(other.run_id)
    assert cancelled is not None
    assert cancelled.status is RunTaskStatus.CANCELLED


def test_run_tracer_production_path_owns_and_settles_durable_task(db_session):
    from core.durable_tasks import RunTaskStatus, SqlAlchemyRunTaskService
    from core.tracing import RunTracer

    handle = RunTracer.start_run(
        trace_id="trace-production-task",
        session_id="private_10001",
        user_id="10001",
        chat_type="private",
        run_type="chat",
        input_preview="你好",
        meta={
            "platform": "qq",
            "message_id": "message-production-task",
        },
    )
    db_session.expire_all()
    running = SqlAlchemyRunTaskService(db_session).get(handle.run_id)
    assert running is not None
    assert running.status is RunTaskStatus.RUNNING
    assert running.source_type == "inbound_message"

    RunTracer.finish_run(
        handle.run_id,
        task_lease=handle.task_lease,
        status="success",
        output_preview="你好呀",
    )
    db_session.expire_all()
    settled = SqlAlchemyRunTaskService(db_session).get(handle.run_id)
    assert settled is not None
    assert settled.status is RunTaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_run_task_owner_observes_cancel_without_starting_second_task(
    db_session,
):
    from core.durable_tasks import (
        RunTaskKind,
        RunTaskOwner,
        SqlAlchemyRunTaskService,
    )

    service = SqlAlchemyRunTaskService(db_session)
    lease = service.admit_running(
        run_id="run-task-owner-cancel",
        task_kind=RunTaskKind.CHAT,
        source_type="inbound_message",
        source_id="message-owner-cancel",
        request_id="message-owner-cancel",
        idempotency_key="message-owner-cancel",
        owner="worker-a",
        lease_seconds=3,
        timeout_seconds=60,
        now=datetime.now(timezone.utc),
    )
    db_session.commit()
    factory = sessionmaker(
        bind=db_session.get_bind(),
        autocommit=False,
        autoflush=False,
    )
    started = asyncio.Event()

    async def execute() -> str:
        owner = RunTaskOwner(
            lease,
            session_factory=factory,
            lease_seconds=3,
            heartbeat_interval_seconds=0.05,
        )
        await owner.start()
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as exc:
            return str(exc)
        finally:
            await owner.stop()

    task = asyncio.create_task(execute())
    await started.wait()
    service.request_cancel(
        lease.run_id,
        reason="客户端取消",
        now=datetime.now(timezone.utc),
    )
    db_session.commit()
    assert await asyncio.wait_for(task, timeout=1) == "durable_task_cancelled"


@pytest.mark.asyncio
async def test_run_task_owner_enforces_deadline_before_heartbeat_interval(
    db_session,
):
    from core.durable_tasks import (
        RunTaskKind,
        RunTaskOwner,
        SqlAlchemyRunTaskService,
    )

    service = SqlAlchemyRunTaskService(db_session)
    lease = service.admit_running(
        run_id="run-task-owner-timeout",
        task_kind=RunTaskKind.BACKGROUND,
        source_type="agent_run",
        source_id="run-task-owner-timeout",
        request_id="run-task-owner-timeout",
        idempotency_key="run-task-owner-timeout",
        owner="worker-a",
        lease_seconds=90,
        timeout_seconds=0.1,
        now=datetime.now(timezone.utc),
    )
    db_session.commit()
    factory = sessionmaker(
        bind=db_session.get_bind(),
        autocommit=False,
        autoflush=False,
    )

    async def execute() -> str:
        owner = RunTaskOwner(
            lease,
            session_factory=factory,
            lease_seconds=90,
        )
        await owner.start()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as exc:
            return str(exc)
        finally:
            await owner.stop()

    assert await asyncio.wait_for(
        asyncio.create_task(execute()),
        timeout=1,
    ) == "durable_task_timed_out"
