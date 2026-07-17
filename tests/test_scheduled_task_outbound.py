from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta

import pytest

from core import database, outbound_delivery
from core.database import (
    OutboundDeliveryCircuit,
    OutboundDeliveryAttempt,
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
    ScheduledTask,
)
from core.outbound_delivery_service import (
    OutboundTransportRequest,
    OutboundWorkerConfig,
)
from core.outbound_transport import DeliveryOutcome
from core.outbound_delivery import OutboundFencingError
from core.outbound_delivery import (
    destination_circuit_fingerprint,
    endpoint_circuit_fingerprint,
)
from tests.async_helpers import run_async


NOW = datetime(2026, 7, 15, 4, 0, 0)
SOURCE_TYPE = "scheduled_task"
ENDPOINT_KEY = "qq_push"
CONFIG_REVISION = "qq-config-r1"
PAYLOAD_CONTRACT = "qq-envelope-v1"


def _seed_control(db, *, mode: str = "outbox_active") -> None:
    db.add(
        OutboundDeliveryControl(
            source_type=SOURCE_TYPE,
            mode=mode,
            cutover_epoch=7 if mode != "legacy_direct" else 0,
            effective_from=NOW - timedelta(hours=1),
            protocol_version=2,
            writer_version=0,
            created_at=NOW - timedelta(days=1),
            updated_at=NOW - timedelta(days=1),
        )
    )
    db.flush()


def _seed_task(db, *, target_id: str = "opaque-user") -> ScheduledTask:
    task = ScheduledTask(
        name="AI 日报",
        cron_expr="0 12 * * *",
        target_type="private",
        target_id=target_id,
        prompt_template="生成今日 AI 日报",
        enabled=1,
        delivery_status="idle",
    )
    db.add(task)
    db.flush()
    return task


def _destination(target_id: str) -> dict[str, str]:
    return {"target_type": "private", "target_id": target_id}


def _destination_fingerprint(target_id: str) -> str:
    return hashlib.sha256(
        f"{ENDPOINT_KEY}\0private\0{target_id}".encode()
    ).hexdigest()


def _worker_config() -> OutboundWorkerConfig:
    return OutboundWorkerConfig(
        push_url="http://qq.test/nanobot/push",
        push_token="push-token-scheduled-helper-sentinel",
        push_timeout_seconds=1.0,
        endpoint_config_revision=CONFIG_REVISION,
        batch_size=1,
        lease_seconds=60.0,
        poll_interval_seconds=0.01,
    )


def test_legacy_producer_without_explicit_transport_only_queues(
    db_session,
    monkeypatch,
):
    from core import scheduled_task_outbound

    _seed_control(db_session, mode="legacy_direct")
    task = _seed_task(db_session)
    db_session.commit()

    def forbidden_worker_config(*_args, **_kwargs):
        raise AssertionError("scheduled producer 不得加载 worker 凭据")

    monkeypatch.delenv("NANOBOT_PUSH_TOKEN", raising=False)
    monkeypatch.setattr(
        scheduled_task_outbound.OutboundWorkerConfig,
        "from_env",
        classmethod(forbidden_worker_config),
    )

    result = run_async(
        scheduled_task_outbound.enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="queue-without-worker-token",
            config=scheduled_task_outbound.ScheduledTaskProducerConfig.for_tests(
                endpoint_config_revision=CONFIG_REVISION,
            ),
            generator=lambda _snapshot: "只持久化的日报",
            session_factory=database.SessionLocal,
            now=NOW,
        )
    )

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, result.outbox_id)
    assert result.status == "queued"
    assert outbox is not None and outbox.status == "pending"
    assert db_session.query(OutboundDeliveryAttempt).count() == 0


def _claim(db, task: ScheduledTask, *, mode: str = "cron"):
    destination = _destination(str(task.target_id))
    return outbound_delivery.claim_outbound_run(
        db,
        source_type=SOURCE_TYPE,
        source_id=str(task.id),
        occurrence_key=f"scheduled-task:{task.id}:{mode}:20260715T040000Z",
        source_revision="task-revision-r1",
        source_snapshot={
            "task_id": task.id,
            "name": task.name,
            "cron_expr": task.cron_expr,
            "target_type": task.target_type,
            "target_id": task.target_id,
            "prompt_template": task.prompt_template,
            "enabled": bool(task.enabled),
        },
        destination_snapshot=destination,
        target_type="private",
        task_kind="scheduled_task",
        scheduled_for=NOW,
        trigger_type=mode,
        owner="scheduled-producer-a",
        claim_lease_seconds=900,
        writer_owner="scheduled-producer-a",
        writer_token="scheduled-writer-token",
        writer_protocol_version=2,
        writer_lease_seconds=900,
        endpoint_key=ENDPOINT_KEY,
        destination_fingerprint=_destination_fingerprint(str(task.target_id)),
        endpoint_config_revision=CONFIG_REVISION,
        payload_contract_fingerprint=PAYLOAD_CONTRACT,
        now=NOW,
    )


def _start(db, claim, task: ScheduledTask):
    return outbound_delivery.start_generation_attempt(
        db,
        run_id=claim.run_id,
        owner=claim.owner,
        claim_token=claim.claim_token,
        writer_owner="scheduled-producer-a",
        writer_token="scheduled-writer-token",
        writer_protocol_version=2,
        endpoint_key=ENDPOINT_KEY,
        destination_fingerprint=_destination_fingerprint(str(task.target_id)),
        endpoint_config_revision=CONFIG_REVISION,
        payload_contract_fingerprint=PAYLOAD_CONTRACT,
        now=NOW + timedelta(seconds=1),
    )


def _commit(db, claim, generation, task: ScheduledTask):
    return outbound_delivery.commit_generated_outbox(
        db,
        run_id=claim.run_id,
        generation_attempt_id=generation.attempt_id,
        owner=claim.owner,
        claim_token=claim.claim_token,
        idempotency_key=f"scheduled-task:{task.id}:delivery:slot-1",
        destination_snapshot=_destination(str(task.target_id)),
        destination_fingerprint=_destination_fingerprint(str(task.target_id)),
        target_type="private",
        endpoint_key=ENDPOINT_KEY,
        payload={
            "reply": "已生成日报",
            "messages": [{"type": "text", "text": "已生成日报"}],
        },
        max_attempts=3,
        retry_deadline_at=NOW + timedelta(hours=1),
        endpoint_config_revision=CONFIG_REVISION,
        payload_contract_fingerprint=PAYLOAD_CONTRACT,
        now=NOW + timedelta(seconds=2),
    )


def test_scheduled_task_projection_is_atomic_from_generation_to_delivery(db_session):
    _seed_control(db_session)
    task = _seed_task(db_session)

    claim = _claim(db_session, task)
    db_session.expire_all()
    projected = db_session.get(ScheduledTask, task.id)
    assert projected.last_run_id == claim.run_id
    assert projected.delivery_status == "claimed"
    assert projected.last_attempt_at is None
    assert projected.last_success_at is None

    generation = _start(db_session, claim, projected)
    db_session.expire_all()
    projected = db_session.get(ScheduledTask, task.id)
    assert projected.delivery_status == "generating"
    assert projected.last_attempt_at == NOW + timedelta(seconds=1)
    assert projected.last_run_at == projected.last_attempt_at
    assert projected.last_success_at is None

    queued = _commit(db_session, claim, generation, projected)
    db_session.expire_all()
    projected = db_session.get(ScheduledTask, task.id)
    assert projected.delivery_status == "queued"
    assert projected.last_success_at is None

    delivery = outbound_delivery.claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=60,
        endpoint_config_revision=CONFIG_REVISION,
        now=NOW + timedelta(seconds=3),
    )
    assert delivery is not None
    outbound_delivery.mark_delivery_request_started(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=4),
    )
    outbound_delivery.settle_delivery_attempt(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        outcome="succeeded",
        transport_phase="response_received",
        http_status=200,
        result_category="success",
        error_type="",
        safe_summary="",
        duration_ms=8,
        now=NOW + timedelta(seconds=5),
    )

    db_session.expire_all()
    projected = db_session.get(ScheduledTask, task.id)
    assert queued.outbox_id == delivery.outbox_id
    assert projected.delivery_status == "delivered"
    assert projected.last_success_at == NOW + timedelta(seconds=5)
    assert projected.last_error_summary == ""


def test_generation_failure_updates_attempt_but_never_success(db_session):
    _seed_control(db_session)
    task = _seed_task(db_session)
    claim = _claim(db_session, task)
    generation = _start(db_session, claim, task)

    outbound_delivery.fail_outbound_generation(
        db_session,
        run_id=claim.run_id,
        generation_attempt_id=generation.attempt_id,
        owner=claim.owner,
        claim_token=claim.claim_token,
        error_type="empty_generation",
        error_summary="模型没有生成可投递内容",
        now=NOW + timedelta(seconds=2),
    )

    db_session.expire_all()
    projected = db_session.get(ScheduledTask, task.id)
    assert projected.last_attempt_at == NOW + timedelta(seconds=1)
    assert projected.last_run_at == projected.last_attempt_at
    assert projected.last_success_at is None
    assert projected.delivery_status == "failed"
    assert projected.last_error_summary == "模型没有生成可投递内容"
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_legacy_direct_persists_payload_before_a_specific_delivery_claim(db_session):
    _seed_control(db_session, mode="legacy_direct")
    task = _seed_task(db_session)
    claim = _claim(db_session, task, mode="manual")
    assert claim.delivery_mode == "legacy_direct"
    generation = _start(db_session, claim, task)

    queued = _commit(db_session, claim, generation, task)
    legacy_claim = outbound_delivery.claim_legacy_direct_outbox(
        db_session,
        outbox_id=queued.outbox_id,
        worker_owner="scheduled-producer-a",
        lease_seconds=60,
        writer_owner="scheduled-producer-a",
        writer_token="scheduled-writer-token",
        writer_protocol_version=2,
        writer_lease_seconds=900,
        endpoint_key=ENDPOINT_KEY,
        endpoint_config_revision=CONFIG_REVISION,
        now=NOW + timedelta(seconds=3),
    )

    assert queued.created is True
    assert legacy_claim is not None
    assert legacy_claim.outbox_id == queued.outbox_id
    assert db_session.get(OutboundDeliveryOutbox, queued.outbox_id).status == "leased"


@pytest.mark.parametrize(
    "stale_fact",
    ["owner_token", "protocol", "version", "expired"],
)
def test_legacy_worker_rejects_stale_live_writer_snapshot_without_sending(
    db_session,
    stale_fact,
):
    from core.outbound_delivery_service import deliver_legacy_outbound_once

    _seed_control(db_session, mode="legacy_direct")
    task = _seed_task(db_session)
    claim = _claim(db_session, task, mode="manual")
    generation = _start(db_session, claim, task)
    queued = _commit(db_session, claim, generation, task)
    db_session.commit()

    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    writer_snapshot = {
        "owner": str(control.writer_owner),
        "token": str(control.writer_token),
        "protocol": int(control.protocol_version),
        "version": int(control.writer_version),
    }
    if stale_fact == "owner_token":
        control.writer_owner = "replacement-writer"
        control.writer_token = "replacement-token"
    elif stale_fact == "protocol":
        control.protocol_version += 1
    elif stale_fact == "version":
        control.writer_version += 1
    else:
        control.writer_lease_expires_at = NOW + timedelta(seconds=2)
    db_session.commit()
    transport_calls = []

    async def transport(request):
        transport_calls.append(request.outbox_id)
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=1,
            safe_summary="",
            transport_phase="response_received",
        )

    result = run_async(
        deliver_legacy_outbound_once(
            session_factory=database.SessionLocal,
            transport=transport,
            config=_worker_config(),
            outbox_id=queued.outbox_id,
            worker_owner="outbound-worker",
            writer_owner=writer_snapshot["owner"],
            writer_token=writer_snapshot["token"],
            writer_protocol_version=writer_snapshot["protocol"],
            writer_lease_seconds=900,
            expected_writer_version=writer_snapshot["version"],
            now=NOW + timedelta(seconds=3),
        )
    )

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    assert result is None
    assert transport_calls == []
    assert outbox.status == "pending"
    assert db_session.query(OutboundDeliveryAttempt).count() == 0


def test_safe_source_cancellation_never_steals_a_delivery_lease(db_session):
    _seed_control(db_session)
    task = _seed_task(db_session)
    claim = _claim(db_session, task)
    generation = _start(db_session, claim, task)
    queued = _commit(db_session, claim, generation, task)
    delivery = outbound_delivery.claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=60,
        endpoint_config_revision=CONFIG_REVISION,
        now=NOW + timedelta(seconds=3),
    )
    assert delivery is not None

    summary = outbound_delivery.cancel_safe_deliveries_for_source(
        db_session,
        source_type=SOURCE_TYPE,
        source_id=str(task.id),
        expected_source_revision="task-revision-r1",
        reason_type="task_disabled",
        safe_summary="任务已禁用",
        now=NOW + timedelta(seconds=4),
    )

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    assert summary.cancelled == 0
    assert summary.unsafe == 1
    assert outbox.status == "leased"


def test_source_cancellation_terminalizes_live_generation_without_outbox(
    db_session,
):
    _seed_control(db_session)
    task = _seed_task(db_session)
    claim = _claim(db_session, task)
    generation = _start(db_session, claim, task)

    summary = outbound_delivery.cancel_safe_deliveries_for_source(
        db_session,
        source_type=SOURCE_TYPE,
        source_id=str(task.id),
        expected_source_revision="task-revision-r1",
        reason_type="task_updated",
        safe_summary="任务定义已修改",
        now=NOW + timedelta(seconds=2),
    )

    run = db_session.get(OutboundRun, claim.run_id)
    attempt = db_session.get(
        OutboundGenerationAttempt,
        generation.attempt_id,
    )
    assert summary.cancelled == 1
    assert summary.unsafe == 0
    assert run.status == "failed"
    assert run.failure_type == "task_updated"
    assert run.claim_owner is None
    assert run.claim_token is None
    assert run.claim_expires_at is None
    assert run.active_outbox_id is None
    assert attempt.status == "abandoned"
    assert attempt.error_type == "task_updated"
    assert attempt.completed_at == NOW + timedelta(seconds=2)
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_cron_occurrence_key_uses_task_and_normalized_shanghai_slot_only():
    from core.scheduled_task_outbound import scheduled_cron_occurrence

    first = scheduled_cron_occurrence(
        task_id=7,
        local_time=datetime(2026, 7, 15, 12, 0, 1),
    )
    same_slot = scheduled_cron_occurrence(
        task_id=7,
        local_time=datetime(2026, 7, 15, 12, 0, 59),
    )
    next_slot = scheduled_cron_occurrence(
        task_id=7,
        local_time=datetime(2026, 7, 15, 12, 1, 0),
    )

    assert first == same_slot
    assert first.occurrence_key == "scheduled-task:7:cron:20260715T040000Z"
    assert first.scheduled_for == NOW
    assert next_slot.occurrence_key != first.occurrence_key


def test_same_cron_slot_generates_once_and_keeps_first_snapshot(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session)
    task = _seed_task(db_session, target_id="first-target")
    db_session.commit()
    calls = []

    async def generator(snapshot):
        calls.append((snapshot.target_id, snapshot.prompt_template))
        return "首次快照生成的日报"

    config = ScheduledTaskProducerConfig.for_tests(
        endpoint_config_revision=CONFIG_REVISION,
    )
    first_result = run_async(enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="cron",
            scheduled_for=NOW,
            config=config,
            generator=generator,
            now=NOW,
        ))
    task.target_id = "edited-target"
    task.prompt_template = "同一槽内修改后的模板"
    db_session.commit()
    second_result = run_async(enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="cron",
            scheduled_for=NOW,
            config=config,
            generator=generator,
            now=NOW + timedelta(seconds=30),
        ))
    assert first_result.run_id == second_result.run_id
    assert first_result.outbox_id == second_result.outbox_id
    assert second_result.deduplicated is True
    assert calls == [("first-target", "生成今日 AI 日报")]
    assert db_session.query(OutboundRun).count() == 1
    assert db_session.query(OutboundGenerationAttempt).count() == 1
    assert db_session.query(OutboundDeliveryOutbox).count() == 1


@pytest.mark.parametrize(
    ("exception_type", "expected_failure_type"),
    [
        (TimeoutError, "generation_timeout"),
        (RuntimeError, "generation_error"),
    ],
)
def test_generation_exception_is_classified_without_persisting_exception_text(
    db_session,
    exception_type,
    expected_failure_type,
):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session)
    task = _seed_task(db_session)
    db_session.commit()
    secret = "generation-exception-secret"

    async def failing_generator(_snapshot):
        raise exception_type(secret)

    result = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key=f"failure-{expected_failure_type}",
            config=ScheduledTaskProducerConfig.for_tests(
                endpoint_config_revision=CONFIG_REVISION,
            ),
            generator=failing_generator,
            now=NOW,
        )
    )

    db_session.expire_all()
    run = db_session.get(OutboundRun, result.run_id)
    attempt = (
        db_session.query(OutboundGenerationAttempt)
        .filter(OutboundGenerationAttempt.run_id == result.run_id)
        .one()
    )
    projected = db_session.get(ScheduledTask, task.id)

    assert result.status == "failed"
    assert run.failure_type == expected_failure_type
    assert attempt.status == "failed"
    assert attempt.error_type == expected_failure_type
    assert secret not in attempt.error_summary
    assert secret not in run.failure_summary
    assert secret not in projected.last_error_summary
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_new_occurrence_reads_task_after_shared_source_lock(
    db_session,
    monkeypatch,
):
    from core import scheduled_task_outbound
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session)
    task = _seed_task(db_session, target_id="old-target")
    task.prompt_template = "旧任务定义"
    db_session.commit()
    generated = []

    def update_wins_before_producer_read(db, *, source_type, now=None):
        assert source_type == SOURCE_TYPE
        live = db.get(ScheduledTask, task.id)
        live.target_id = "new-target"
        live.prompt_template = "新任务定义"
        db.flush()
        db.expire_all()
        return now

    monkeypatch.setattr(
        scheduled_task_outbound,
        "lock_outbound_source_control",
        update_wins_before_producer_read,
        raising=False,
    )

    result = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="source-lock-order",
            config=ScheduledTaskProducerConfig.for_tests(
                endpoint_config_revision=CONFIG_REVISION,
            ),
            generator=lambda snapshot: generated.append(
                (snapshot.target_id, snapshot.prompt_template)
            ) or "新定义正文",
            now=NOW,
        )
    )

    outbox = db_session.get(OutboundDeliveryOutbox, result.outbox_id)
    assert generated == [("new-target", "新任务定义")]
    assert json.loads(outbox.destination_snapshot_json)["target_id"] == "new-target"


def test_cron_occurrence_rechecks_latest_schedule_after_shared_source_lock(
    db_session,
    monkeypatch,
):
    from core import scheduled_task_outbound
    from core.scheduled_task_outbound import (
        ScheduledTaskOutboundError,
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session)
    task = _seed_task(db_session)
    db_session.commit()
    generated = []

    def update_cron_before_producer_read(db, *, source_type, now=None):
        assert source_type == SOURCE_TYPE
        live = db.get(ScheduledTask, task.id)
        live.cron_expr = "0 0 1 1 *"
        live.prompt_template = "更新后不应在当前槽执行"
        db.flush()
        db.expire_all()
        return now

    async def generator(snapshot):
        generated.append(snapshot.prompt_template)
        return "不应生成"

    monkeypatch.setattr(
        scheduled_task_outbound,
        "lock_outbound_source_control",
        update_cron_before_producer_read,
    )

    with pytest.raises(ScheduledTaskOutboundError, match="当前槽"):
        run_async(
            enqueue_scheduled_task_occurrence(
                db_session,
                task_id=task.id,
                trigger_type="cron",
                scheduled_for=NOW,
                config=ScheduledTaskProducerConfig.for_tests(
                    endpoint_config_revision=CONFIG_REVISION,
                ),
                generator=generator,
                now=NOW,
            )
        )
    db_session.rollback()

    assert generated == []
    assert db_session.query(OutboundRun).count() == 0
    assert db_session.query(OutboundGenerationAttempt).count() == 0
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_legacy_producer_persists_request_boundary_before_http(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session, mode="legacy_direct")
    task = _seed_task(db_session)
    db_session.commit()
    observed = {}

    async def transport(request: OutboundTransportRequest) -> DeliveryOutcome:
        observed["producer_transaction_open"] = db_session.in_transaction()
        with database.SessionLocal() as probe:
            outbox = probe.get(OutboundDeliveryOutbox, request.outbox_id)
            attempt = (
                probe.query(OutboundDeliveryAttempt)
                .filter(OutboundDeliveryAttempt.outbox_id == request.outbox_id)
                .one()
            )
            run = probe.get(OutboundRun, outbox.run_id)
            observed.update(
                outbox_status=outbox.status,
                request_started_count=outbox.request_started_count,
                attempt_status=attempt.status,
                request_started=attempt.request_started,
                run_status=run.status,
            )
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=7,
            safe_summary="",
            transport_phase="response_received",
        )

    result = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="manual-request-one",
            config=ScheduledTaskProducerConfig.for_tests(
                endpoint_config_revision=CONFIG_REVISION,
            ),
            generator=lambda _snapshot: "已生成日报",
            session_factory=database.SessionLocal,
            legacy_transport=transport,
            legacy_worker_config=_worker_config(),
            now=NOW,
        )
    )

    assert observed == {
        "producer_transaction_open": False,
        "outbox_status": "leased",
        "request_started_count": 1,
        "attempt_status": "started",
        "request_started": True,
        "run_status": "delivering",
    }
    assert result.status == "delivered"
    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, result.outbox_id)
    projected = db_session.get(ScheduledTask, task.id)
    assert outbox.status == "delivered"
    assert projected.delivery_status == "delivered"
    assert projected.last_success_at == NOW


def test_explicit_legacy_transport_does_not_require_worker_credentials(
    db_session,
    monkeypatch,
):
    from core import scheduled_task_outbound

    _seed_control(db_session, mode="legacy_direct")
    task = _seed_task(db_session)
    db_session.commit()

    def forbidden_worker_config(*_args, **_kwargs):
        raise AssertionError("显式 transport 不得加载 outbound worker 凭据")

    async def transport(_request: OutboundTransportRequest) -> DeliveryOutcome:
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=1,
            safe_summary="",
            transport_phase="response_received",
        )

    monkeypatch.delenv("NANOBOT_PUSH_TOKEN", raising=False)
    monkeypatch.setattr(
        scheduled_task_outbound.OutboundWorkerConfig,
        "from_env",
        classmethod(forbidden_worker_config),
    )

    result = run_async(
        scheduled_task_outbound.enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="explicit-transport-without-worker-secret",
            config=scheduled_task_outbound.ScheduledTaskProducerConfig.for_tests(
                endpoint_config_revision=CONFIG_REVISION,
            ),
            generator=lambda _snapshot: "显式 transport 测试日报",
            session_factory=database.SessionLocal,
            legacy_transport=transport,
            now=NOW,
        )
    )

    assert result.status == "delivered"
    assert db_session.query(OutboundDeliveryAttempt).count() == 1


def test_legacy_deduplicated_retry_reuses_persisted_leaf(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session, mode="legacy_direct")
    task = _seed_task(db_session)
    db_session.commit()
    generation_calls = []
    transport_calls = []

    async def generator(snapshot):
        generation_calls.append(snapshot.task_id)
        return "只生成一次的日报"

    async def transient_transport(
        request: OutboundTransportRequest,
    ) -> DeliveryOutcome:
        transport_calls.append(request.outbox_id)
        return DeliveryOutcome(
            category="transient",
            error_type="upstream_unavailable",
            status_code=503,
            retry_after_seconds=1,
            duration_ms=7,
            safe_summary="上游暂时不可用",
            transport_phase="response_received",
        )

    async def success_transport(
        request: OutboundTransportRequest,
    ) -> DeliveryOutcome:
        transport_calls.append(request.outbox_id)
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=5,
            safe_summary="",
            transport_phase="response_received",
        )

    config = ScheduledTaskProducerConfig.for_tests(
        endpoint_config_revision=CONFIG_REVISION,
    )
    first = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="same-manual-request",
            config=config,
            generator=generator,
            session_factory=database.SessionLocal,
            legacy_transport=transient_transport,
            legacy_worker_config=_worker_config(),
            now=NOW,
        )
    )
    second = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="same-manual-request",
            config=config,
            generator=generator,
            session_factory=database.SessionLocal,
            legacy_transport=success_transport,
            legacy_worker_config=_worker_config(),
            now=NOW + timedelta(seconds=2),
        )
    )

    assert first.status == "retry_wait"
    assert second.status == "delivered"
    assert second.deduplicated is True
    assert second.outbox_id == first.outbox_id
    assert generation_calls == [task.id]
    assert transport_calls == [first.outbox_id, first.outbox_id]
    assert db_session.query(OutboundGenerationAttempt).count() == 1
    assert db_session.query(OutboundDeliveryAttempt).count() == 2


def test_legacy_process_restart_takes_over_same_safe_leaf(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session, mode="legacy_direct")
    task = _seed_task(db_session)
    db_session.commit()
    generation_calls = []

    async def generator(snapshot):
        generation_calls.append(snapshot.task_id)
        return "重启后仍复用的日报"

    async def transient_transport(_request):
        return DeliveryOutcome(
            category="transient",
            error_type="upstream_unavailable",
            status_code=503,
            retry_after_seconds=1,
            duration_ms=7,
            safe_summary="上游暂时不可用",
            transport_phase="response_received",
        )

    success_calls = []

    async def success_transport(request):
        success_calls.append(request.outbox_id)
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=5,
            safe_summary="",
            transport_phase="response_received",
        )

    producer_a = ScheduledTaskProducerConfig(
        endpoint_config_revision=CONFIG_REVISION,
        producer_owner="scheduled-producer-a",
        writer_token="scheduled-writer-token-a",
        writer_lease_seconds=900,
    )
    producer_b = ScheduledTaskProducerConfig(
        endpoint_config_revision=CONFIG_REVISION,
        producer_owner="scheduled-producer-b",
        writer_token="scheduled-writer-token-b",
        writer_lease_seconds=900,
    )
    first = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="restart-safe-request",
            config=producer_a,
            generator=generator,
            session_factory=database.SessionLocal,
            legacy_transport=transient_transport,
            legacy_worker_config=_worker_config(),
            now=NOW,
        )
    )
    second = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="restart-safe-request",
            config=producer_b,
            generator=generator,
            session_factory=database.SessionLocal,
            legacy_transport=success_transport,
            legacy_worker_config=_worker_config(),
            now=NOW + timedelta(seconds=901),
        )
    )

    assert first.status == "retry_wait"
    assert second.status == "delivered"
    assert second.outbox_id == first.outbox_id
    assert generation_calls == [task.id]
    assert success_calls == [first.outbox_id]
    run = db_session.get(OutboundRun, second.run_id)
    assert run.writer_owner == producer_b.producer_owner
    assert run.writer_token == producer_b.writer_token


def test_legacy_drain_recovers_pending_leaf_without_occurrence_reentry(
    db_session,
):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        drain_due_legacy_scheduled_task_outboxes,
    )

    _seed_control(db_session, mode="legacy_direct")
    task = _seed_task(db_session)
    claim = _claim(db_session, task, mode="manual")
    generation = _start(db_session, claim, task)
    queued = _commit(db_session, claim, generation, task)
    db_session.commit()
    transport_calls = []

    async def success_transport(request):
        transport_calls.append(request.outbox_id)
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=5,
            safe_summary="",
            transport_phase="response_received",
        )

    results = run_async(
        drain_due_legacy_scheduled_task_outboxes(
            session_factory=database.SessionLocal,
            producer_config=ScheduledTaskProducerConfig(
                endpoint_config_revision=CONFIG_REVISION,
                producer_owner="scheduled-producer-b",
                writer_token="scheduled-writer-token-b",
                writer_lease_seconds=60,
            ),
            worker_config=_worker_config(),
            transport=success_transport,
            now=NOW + timedelta(seconds=901),
        )
    )

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    assert len(results) == 1
    assert results[0].outbox_status == "delivered"
    assert transport_calls == [queued.outbox_id]
    assert outbox.status == "delivered"
    assert db_session.query(OutboundGenerationAttempt).count() == 1


def test_worker_scheduled_drain_reuses_live_writer_and_is_idempotent(
    db_session,
):
    from core.scheduled_task_outbound import (
        drain_due_legacy_scheduled_task_outboxes,
    )

    _seed_control(db_session, mode="legacy_direct")
    task = _seed_task(db_session)
    claim = _claim(db_session, task, mode="manual")
    generation = _start(db_session, claim, task)
    queued = _commit(db_session, claim, generation, task)
    db_session.commit()
    transport_calls = []

    async def success_transport(request):
        transport_calls.append(request.outbox_id)
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=5,
            safe_summary="",
            transport_phase="response_received",
        )

    first = run_async(
        drain_due_legacy_scheduled_task_outboxes(
            session_factory=database.SessionLocal,
            worker_config=_worker_config(),
            transport=success_transport,
            now=NOW + timedelta(seconds=3),
        )
    )
    second = run_async(
        drain_due_legacy_scheduled_task_outboxes(
            session_factory=database.SessionLocal,
            worker_config=_worker_config(),
            transport=success_transport,
            now=NOW + timedelta(seconds=4),
        )
    )

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    assert len(first) == 1
    assert second == []
    assert transport_calls == [queued.outbox_id]
    assert outbox.status == "delivered"
    assert db_session.query(OutboundDeliveryAttempt).count() == 1


@pytest.mark.parametrize("initial_status", ["pending", "retry_wait"])
def test_worker_scheduled_drain_takes_over_expired_writer(
    db_session,
    initial_status,
):
    from core.outbound_delivery_service import LegacyWriterTakeover
    from core.scheduled_task_outbound import (
        drain_due_legacy_scheduled_task_outboxes,
    )

    _seed_control(db_session, mode="legacy_direct")
    task = _seed_task(db_session)
    claim = _claim(db_session, task, mode="manual")
    generation = _start(db_session, claim, task)
    queued = _commit(db_session, claim, generation, task)
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    run = db_session.get(OutboundRun, queued.run_id)
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    outbox.status = initial_status
    run.status = "queued"
    if initial_status == "retry_wait":
        outbox.next_attempt_at = NOW + timedelta(seconds=2)
    control.writer_lease_expires_at = NOW + timedelta(seconds=1)
    db_session.commit()
    transport_calls = []

    async def success_transport(request):
        transport_calls.append(request.outbox_id)
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=5,
            safe_summary="",
            transport_phase="response_received",
        )

    results = run_async(
        drain_due_legacy_scheduled_task_outboxes(
            session_factory=database.SessionLocal,
            worker_config=_worker_config(),
            transport=success_transport,
            worker_owner="outbound-worker",
            takeover_writer=LegacyWriterTakeover(
                writer_owner="outbound-worker:scheduled-legacy",
                writer_token="scheduled-takeover-token",
            ),
            now=NOW + timedelta(seconds=3),
        )
    )

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    run = db_session.get(OutboundRun, queued.run_id)
    assert len(results) == 1
    assert transport_calls == [queued.outbox_id]
    assert outbox.status == "delivered"
    assert run.status == "succeeded"
    assert run.writer_owner == "outbound-worker:scheduled-legacy"
    assert db_session.query(OutboundDeliveryAttempt).count() == 1


def test_worker_scheduled_drain_stop_prevents_next_legacy_claim(db_session):
    from core.scheduled_task_outbound import (
        drain_due_legacy_scheduled_task_outboxes,
    )

    _seed_control(db_session, mode="legacy_direct")
    queued_ids = []
    for suffix in ("first", "second"):
        task = _seed_task(db_session, target_id=f"opaque-{suffix}")
        claim = _claim(db_session, task, mode="manual")
        generation = _start(db_session, claim, task)
        queued_ids.append(_commit(db_session, claim, generation, task).outbox_id)
    db_session.commit()

    class StopEvent:
        stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

    stop_event = StopEvent()
    transport_calls = []

    async def stop_after_success(request):
        transport_calls.append(request.outbox_id)
        stop_event.set()
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=5,
            safe_summary="",
            transport_phase="response_received",
        )

    results = run_async(
        drain_due_legacy_scheduled_task_outboxes(
            session_factory=database.SessionLocal,
            worker_config=_worker_config(),
            transport=stop_after_success,
            stop_event=stop_event,
            now=NOW + timedelta(seconds=3),
            limit=2,
        )
    )

    db_session.expire_all()
    statuses = {
        db_session.get(OutboundDeliveryOutbox, outbox_id).status
        for outbox_id in queued_ids
    }
    assert len(results) == 1
    assert len(transport_calls) == 1
    assert statuses == {"delivered", "pending"}
    assert db_session.query(OutboundDeliveryAttempt).count() == 1


def test_legacy_drain_retries_transient_leaf_after_original_cron_slot(
    db_session,
    monkeypatch,
):
    from core import outbound_transport
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        drain_due_legacy_scheduled_task_outboxes,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session, mode="legacy_direct")
    task = _seed_task(db_session)
    db_session.commit()
    generation_calls = []
    producer_config = ScheduledTaskProducerConfig(
        endpoint_config_revision=CONFIG_REVISION,
        producer_owner="scheduled-producer-a",
        writer_token="scheduled-writer-token-a",
        writer_lease_seconds=900,
    )

    async def generator(snapshot):
        generation_calls.append(snapshot.task_id)
        return "只生成一次的 cron 正文"

    async def transient_transport(_request):
        return DeliveryOutcome(
            category="transient",
            error_type="upstream_unavailable",
            status_code=503,
            retry_after_seconds=1,
            duration_ms=7,
            safe_summary="上游暂时不可用",
            transport_phase="response_received",
        )

    first = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="cron",
            scheduled_for=NOW,
            config=producer_config,
            generator=generator,
            session_factory=database.SessionLocal,
            legacy_transport=transient_transport,
            legacy_worker_config=_worker_config(),
            now=NOW,
        )
    )
    success_calls = []
    observed_tokens = []

    async def success_transport(_session, **kwargs):
        success_calls.append(True)
        observed_tokens.append(kwargs["push_token"])
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=5,
            safe_summary="",
            transport_phase="response_received",
        )

    monkeypatch.setattr(
        outbound_transport,
        "deliver_qq_push_with_session",
        success_transport,
    )

    results = run_async(
        drain_due_legacy_scheduled_task_outboxes(
            session_factory=database.SessionLocal,
            producer_config=producer_config,
            worker_config=_worker_config(),
            now=NOW + timedelta(minutes=1),
        )
    )

    assert first.status == "retry_wait"
    assert len(results) == 1
    assert results[0].outbox_status == "delivered"
    assert success_calls == [True]
    assert observed_tokens == ["push-token-scheduled-helper-sentinel"]
    assert generation_calls == [task.id]
    assert db_session.query(OutboundGenerationAttempt).count() == 1
    assert db_session.query(OutboundDeliveryAttempt).count() == 2


def test_legacy_drain_isolates_one_leaf_failure_and_continues_batch(
    db_session,
    monkeypatch,
    caplog,
):
    from core import scheduled_task_outbound
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        drain_due_legacy_scheduled_task_outboxes,
    )

    _seed_control(db_session, mode="legacy_direct")
    first_task = _seed_task(db_session, target_id="first-target")
    second_task = _seed_task(db_session, target_id="second-target")
    first_claim = _claim(db_session, first_task, mode="manual")
    first_outbox = _commit(
        db_session,
        first_claim,
        _start(db_session, first_claim, first_task),
        first_task,
    )
    second_claim = _claim(db_session, second_task, mode="manual")
    second_outbox = _commit(
        db_session,
        second_claim,
        _start(db_session, second_claim, second_task),
        second_task,
    )
    db_session.commit()
    calls = []

    async def flaky_delivery(**kwargs):
        outbox_id = kwargs["outbox_id"]
        calls.append(outbox_id)
        if outbox_id == first_outbox.outbox_id:
            raise RuntimeError("legacy-leaf-secret")
        return None

    monkeypatch.setattr(
        scheduled_task_outbound,
        "deliver_legacy_outbound_once",
        flaky_delivery,
    )
    caplog.set_level(
        logging.ERROR,
        logger="nanobot.scheduled_task_outbound",
    )

    results = run_async(
        drain_due_legacy_scheduled_task_outboxes(
            session_factory=database.SessionLocal,
            producer_config=ScheduledTaskProducerConfig(
                endpoint_config_revision=CONFIG_REVISION,
                producer_owner="scheduled-producer-a",
                writer_token="scheduled-writer-token",
            ),
            worker_config=_worker_config(),
            transport=lambda _request: None,
            now=NOW + timedelta(seconds=3),
            limit=2,
        )
    )

    assert results == []
    assert calls == [first_outbox.outbox_id, second_outbox.outbox_id]
    assert f"outbox_id={first_outbox.outbox_id}" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "legacy-leaf-secret" not in caplog.text


def test_long_generation_cannot_commit_after_claim_lease_expires(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session)
    task = _seed_task(db_session)
    db_session.commit()
    current = [NOW]

    def clock():
        return current[0]

    async def slow_generator(_snapshot):
        current[0] = NOW + timedelta(seconds=2)
        return "已经越过租约的正文"

    config = ScheduledTaskProducerConfig(
        endpoint_config_revision=CONFIG_REVISION,
        producer_owner="scheduled-producer-a",
        writer_token="scheduled-writer-token-a",
        claim_lease_seconds=1,
        writer_lease_seconds=1,
    )

    with pytest.raises(OutboundFencingError, match="claim|writer"):
        run_async(
            enqueue_scheduled_task_occurrence(
                db_session,
                task_id=task.id,
                trigger_type="manual",
                manual_idempotency_key="expired-generation-request",
                config=config,
                generator=slow_generator,
                clock=clock,
            )
        )

    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_endpoint_circuit_blocks_before_generation(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session)
    task = _seed_task(db_session)
    db_session.add(OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
        config_revision=CONFIG_REVISION,
        status="open",
        reason_type="unauthorized",
        opened_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    ))
    db_session.commit()
    calls = []

    async def generator(_snapshot):
        calls.append("generated")
        return "不应生成"

    result = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="endpoint-circuit-request",
            config=ScheduledTaskProducerConfig.for_tests(
                endpoint_config_revision=CONFIG_REVISION,
            ),
            generator=generator,
            now=NOW,
        )
    )

    assert result.status == "blocked"
    assert result.outbox_id is None
    assert calls == []
    assert db_session.query(OutboundGenerationAttempt).count() == 0


def test_task_update_terminalizes_blocked_run_before_circuit_reset(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        cancel_scheduled_task_deliveries,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session)
    task = _seed_task(db_session)
    circuit = OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
        config_revision=CONFIG_REVISION,
        status="open",
        reason_type="unauthorized",
        opened_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(circuit)
    db_session.commit()
    config = ScheduledTaskProducerConfig.for_tests(
        endpoint_config_revision=CONFIG_REVISION,
    )
    first = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="blocked-before-update",
            config=config,
            generator=lambda _snapshot: "不应生成",
            now=NOW,
        )
    )
    assert first.status == "blocked"
    assert first.outbox_id is None

    cancellation = cancel_scheduled_task_deliveries(
        db_session,
        task=task,
        reason_type="task_updated",
        safe_summary="任务定义已修改",
        now=NOW + timedelta(seconds=1),
    )
    task.prompt_template = "更新后的任务定义"
    db_session.commit()
    reset = outbound_delivery.reset_delivery_circuit(
        db_session,
        scope_type="endpoint",
        scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
        config_revision=CONFIG_REVISION,
        expected_updated_at=NOW,
        now=NOW + timedelta(seconds=2),
    )
    db_session.commit()
    generated = []
    repeated = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="blocked-before-update",
            config=config,
            generator=lambda snapshot: generated.append(
                snapshot.prompt_template
            ) or "旧定义正文",
            now=NOW + timedelta(seconds=3),
        )
    )

    db_session.expire_all()
    run = db_session.get(OutboundRun, first.run_id)
    assert cancellation.cancelled == 1
    assert cancellation.unsafe == 0
    assert reset.applied is True
    assert repeated.status == "failed"
    assert generated == []
    assert run.status == "failed"
    assert run.failure_type == "task_updated"
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_destination_circuit_does_not_block_another_target(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
        scheduled_task_destination_fingerprint,
        snapshot_scheduled_task,
    )

    _seed_control(db_session)
    blocked_task = _seed_task(db_session, target_id="blocked-target")
    allowed_task = _seed_task(db_session, target_id="allowed-target")
    blocked_fingerprint = scheduled_task_destination_fingerprint(
        snapshot_scheduled_task(blocked_task)
    )
    db_session.add(OutboundDeliveryCircuit(
        scope_type="destination",
        scope_fingerprint=destination_circuit_fingerprint(
            ENDPOINT_KEY,
            blocked_fingerprint,
        ),
        config_revision=CONFIG_REVISION,
        status="open",
        reason_type="destination_not_found",
        opened_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    ))
    db_session.commit()
    generated = []

    async def generator(snapshot):
        generated.append(snapshot.target_id)
        return "允许目标的正文"

    config = ScheduledTaskProducerConfig.for_tests(
        endpoint_config_revision=CONFIG_REVISION,
    )
    blocked = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=blocked_task.id,
            trigger_type="manual",
            manual_idempotency_key="blocked-destination-request",
            config=config,
            generator=generator,
            now=NOW,
        )
    )
    allowed = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=allowed_task.id,
            trigger_type="manual",
            manual_idempotency_key="allowed-destination-request",
            config=config,
            generator=generator,
            now=NOW,
        )
    )

    assert blocked.status == "blocked"
    assert allowed.status == "queued"
    assert generated == ["allowed-target"]


def test_manual_idempotency_key_raw_value_is_never_persisted(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )

    _seed_control(db_session)
    task = _seed_task(db_session)
    db_session.commit()
    raw_key = "raw-sensitive-manual-request-value"

    result = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key=raw_key,
            config=ScheduledTaskProducerConfig.for_tests(
                endpoint_config_revision=CONFIG_REVISION,
            ),
            generator=lambda _snapshot: "幂等测试正文",
            now=NOW,
        )
    )

    run = db_session.get(OutboundRun, result.run_id)
    outbox = db_session.get(OutboundDeliveryOutbox, result.outbox_id)
    persisted = "\n".join((
        run.occurrence_key,
        run.source_snapshot_json,
        run.delivery_contract_json,
        outbox.idempotency_key,
        outbox.destination_snapshot_json,
        outbox.payload_json,
    ))
    assert raw_key not in persisted
    assert hashlib.sha256(raw_key.encode()).hexdigest() in run.occurrence_key


def test_expired_generation_is_recovered_from_frozen_run_snapshot(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
        recover_expired_scheduled_task_occurrences,
    )

    class SimulatedProducerCrash(BaseException):
        pass

    _seed_control(db_session)
    task = _seed_task(db_session, target_id="frozen-target")
    db_session.commit()
    first_config = ScheduledTaskProducerConfig(
        endpoint_config_revision=CONFIG_REVISION,
        producer_owner="scheduled-producer-a",
        writer_token="scheduled-writer-token-a",
        claim_lease_seconds=1,
        writer_lease_seconds=1,
    )

    async def crash_after_attempt_started(_snapshot):
        raise SimulatedProducerCrash()

    with pytest.raises(SimulatedProducerCrash):
        run_async(
            enqueue_scheduled_task_occurrence(
                db_session,
                task_id=task.id,
                trigger_type="manual",
                manual_idempotency_key="recovery-without-raw-key",
                config=first_config,
                generator=crash_after_attempt_started,
                now=NOW,
            )
        )

    crashed_run = db_session.query(OutboundRun).one()
    task.target_id = "edited-after-crash"
    task.prompt_template = "崩溃后被外部修改的定义"
    db_session.commit()
    recovered_snapshots = []

    async def recovered_generator(snapshot):
        recovered_snapshots.append((snapshot.target_id, snapshot.prompt_template))
        return "按冻结快照恢复的正文"

    recovered = run_async(
        recover_expired_scheduled_task_occurrences(
            session_factory=database.SessionLocal,
            config=ScheduledTaskProducerConfig(
                endpoint_config_revision=CONFIG_REVISION,
                producer_owner="scheduled-producer-b",
                writer_token="scheduled-writer-token-b",
                claim_lease_seconds=60,
                writer_lease_seconds=60,
            ),
            generator=recovered_generator,
            now=NOW + timedelta(seconds=2),
        )
    )

    assert len(recovered) == 1
    assert recovered[0].run_id == crashed_run.id
    assert recovered[0].status == "queued"
    assert recovered_snapshots == [("frozen-target", "生成今日 AI 日报")]
    db_session.expire_all()
    attempts = (
        db_session.query(OutboundGenerationAttempt)
        .order_by(OutboundGenerationAttempt.attempt_no)
        .all()
    )
    assert [row.status for row in attempts] == ["abandoned", "succeeded"]
    assert db_session.query(OutboundRun).count() == 1
    assert db_session.query(OutboundDeliveryOutbox).count() == 1


def test_recovery_skips_run_that_already_has_historical_outbox(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
        recover_expired_scheduled_task_occurrences,
    )

    _seed_control(db_session)
    task = _seed_task(db_session)
    db_session.commit()
    queued = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="historical-leaf",
            config=ScheduledTaskProducerConfig(
                endpoint_config_revision=CONFIG_REVISION,
                producer_owner="scheduled-producer-a",
                writer_token="scheduled-writer-token-a",
                claim_lease_seconds=1,
                writer_lease_seconds=1,
            ),
            generator=lambda _snapshot: "已经形成过 outbox 的正文",
            now=NOW,
        )
    )
    run = db_session.get(OutboundRun, queued.run_id)
    run.status = "generating"
    run.active_outbox_id = None
    run.claim_owner = "crashed-producer"
    run.claim_token = "crashed-claim-token"
    run.claim_expires_at = NOW + timedelta(seconds=1)
    db_session.commit()
    generated = []

    recovered = run_async(
        recover_expired_scheduled_task_occurrences(
            session_factory=database.SessionLocal,
            config=ScheduledTaskProducerConfig(
                endpoint_config_revision=CONFIG_REVISION,
                producer_owner="scheduled-producer-b",
                writer_token="scheduled-writer-token-b",
                claim_lease_seconds=60,
                writer_lease_seconds=60,
            ),
            generator=lambda snapshot: generated.append(snapshot.task_id),
            now=NOW + timedelta(seconds=2),
        )
    )

    assert recovered == []
    assert generated == []
    assert db_session.query(OutboundDeliveryOutbox).count() == 1


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("task_kind", "proactive_outreach"),
        ("scheduled_for", None),
        ("trigger_type", "invalid"),
    ),
)
def test_recovery_quarantines_invalid_metadata_once(
    db_session,
    field_name,
    invalid_value,
):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
        recover_expired_scheduled_task_occurrences,
    )

    class SimulatedProducerCrash(BaseException):
        pass

    _seed_control(db_session)
    task = _seed_task(db_session)
    db_session.commit()

    async def crash_after_attempt_started(_snapshot):
        raise SimulatedProducerCrash()

    with pytest.raises(SimulatedProducerCrash):
        run_async(
            enqueue_scheduled_task_occurrence(
                db_session,
                task_id=task.id,
                trigger_type="manual",
                manual_idempotency_key="wrong-task-kind",
                config=ScheduledTaskProducerConfig(
                    endpoint_config_revision=CONFIG_REVISION,
                    producer_owner="scheduled-producer-a",
                    writer_token="scheduled-writer-token-a",
                    claim_lease_seconds=1,
                    writer_lease_seconds=1,
                ),
                generator=crash_after_attempt_started,
                now=NOW,
            )
    )
    run = db_session.query(OutboundRun).one()
    setattr(run, field_name, invalid_value)
    db_session.commit()
    generated = []
    recovery_config = ScheduledTaskProducerConfig(
        endpoint_config_revision=CONFIG_REVISION,
        producer_owner="scheduled-producer-b",
        writer_token="scheduled-writer-token-b",
        claim_lease_seconds=60,
        writer_lease_seconds=60,
    )

    first = run_async(
        recover_expired_scheduled_task_occurrences(
            session_factory=database.SessionLocal,
            config=recovery_config,
            generator=lambda snapshot: generated.append(snapshot.task_id),
            now=NOW + timedelta(seconds=2),
        )
    )
    second = run_async(
        recover_expired_scheduled_task_occurrences(
            session_factory=database.SessionLocal,
            config=recovery_config,
            generator=lambda snapshot: generated.append(snapshot.task_id),
            now=NOW + timedelta(seconds=3),
        )
    )

    db_session.expire_all()
    run = db_session.get(OutboundRun, run.id)
    attempt = db_session.query(OutboundGenerationAttempt).one()
    assert first == []
    assert second == []
    assert generated == []
    assert run.status == "failed"
    assert run.failure_type == "recovery_state_invalid"
    assert attempt.status == "abandoned"


def test_recovery_quarantines_corrupt_snapshot_once(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
        recover_expired_scheduled_task_occurrences,
    )

    class SimulatedProducerCrash(BaseException):
        pass

    _seed_control(db_session)
    task = _seed_task(db_session)
    db_session.commit()

    async def crash_after_attempt_started(_snapshot):
        raise SimulatedProducerCrash()

    with pytest.raises(SimulatedProducerCrash):
        run_async(
            enqueue_scheduled_task_occurrence(
                db_session,
                task_id=task.id,
                trigger_type="manual",
                manual_idempotency_key="corrupt-recovery-snapshot",
                config=ScheduledTaskProducerConfig(
                    endpoint_config_revision=CONFIG_REVISION,
                    producer_owner="scheduled-producer-a",
                    writer_token="scheduled-writer-token-a",
                    claim_lease_seconds=1,
                    writer_lease_seconds=1,
                ),
                generator=crash_after_attempt_started,
                now=NOW,
            )
        )
    run = db_session.query(OutboundRun).one()
    run.source_snapshot_json = '{"corrupt":true}'
    db_session.commit()
    generated = []
    recovery_config = ScheduledTaskProducerConfig(
        endpoint_config_revision=CONFIG_REVISION,
        producer_owner="scheduled-producer-b",
        writer_token="scheduled-writer-token-b",
        claim_lease_seconds=60,
        writer_lease_seconds=60,
    )

    first = run_async(
        recover_expired_scheduled_task_occurrences(
            session_factory=database.SessionLocal,
            config=recovery_config,
            generator=lambda snapshot: generated.append(snapshot.task_id),
            now=NOW + timedelta(seconds=2),
        )
    )
    second = run_async(
        recover_expired_scheduled_task_occurrences(
            session_factory=database.SessionLocal,
            config=recovery_config,
            generator=lambda snapshot: generated.append(snapshot.task_id),
            now=NOW + timedelta(seconds=3),
        )
    )

    db_session.expire_all()
    run = db_session.get(OutboundRun, run.id)
    attempt = db_session.query(OutboundGenerationAttempt).one()
    assert first == []
    assert second == []
    assert generated == []
    assert run.status == "failed"
    assert run.failure_type == "recovery_state_invalid"
    assert attempt.status == "abandoned"


def test_recovery_quarantines_semantically_forged_delivery_contract(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
        recover_expired_scheduled_task_occurrences,
    )

    class SimulatedProducerCrash(BaseException):
        pass

    _seed_control(db_session)
    task = _seed_task(db_session)
    db_session.commit()

    async def crash_after_attempt_started(_snapshot):
        raise SimulatedProducerCrash()

    with pytest.raises(SimulatedProducerCrash):
        run_async(
            enqueue_scheduled_task_occurrence(
                db_session,
                task_id=task.id,
                trigger_type="manual",
                manual_idempotency_key="forged-recovery-contract",
                config=ScheduledTaskProducerConfig(
                    endpoint_config_revision=CONFIG_REVISION,
                    producer_owner="scheduled-producer-a",
                    writer_token="scheduled-writer-token-a",
                    claim_lease_seconds=1,
                    writer_lease_seconds=1,
                ),
                generator=crash_after_attempt_started,
                now=NOW,
            )
        )
    run = db_session.query(OutboundRun).one()
    contract = json.loads(run.delivery_contract_json)
    contract["destination_fingerprint"] = "0" * 64
    encoded = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    run.delivery_contract_json = encoded
    run.delivery_contract_sha256 = hashlib.sha256(encoded.encode()).hexdigest()
    db_session.commit()
    generated = []

    recovered = run_async(
        recover_expired_scheduled_task_occurrences(
            session_factory=database.SessionLocal,
            config=ScheduledTaskProducerConfig(
                endpoint_config_revision=CONFIG_REVISION,
                producer_owner="scheduled-producer-b",
                writer_token="scheduled-writer-token-b",
                claim_lease_seconds=60,
                writer_lease_seconds=60,
            ),
            generator=lambda snapshot: generated.append(snapshot.task_id),
            now=NOW + timedelta(seconds=2),
        )
    )

    db_session.expire_all()
    run = db_session.get(OutboundRun, run.id)
    assert recovered == []
    assert generated == []
    assert run.status == "failed"
    assert run.failure_type == "recovery_state_invalid"


def test_recovery_blocks_expired_generation_when_circuit_opened(db_session):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
        recover_expired_scheduled_task_occurrences,
    )

    class SimulatedProducerCrash(BaseException):
        pass

    _seed_control(db_session)
    task = _seed_task(db_session)
    db_session.commit()

    async def crash_after_attempt_started(_snapshot):
        raise SimulatedProducerCrash()

    with pytest.raises(SimulatedProducerCrash):
        run_async(
            enqueue_scheduled_task_occurrence(
                db_session,
                task_id=task.id,
                trigger_type="manual",
                manual_idempotency_key="circuit-opened-after-crash",
                config=ScheduledTaskProducerConfig(
                    endpoint_config_revision=CONFIG_REVISION,
                    producer_owner="scheduled-producer-a",
                    writer_token="scheduled-writer-token-a",
                    claim_lease_seconds=1,
                    writer_lease_seconds=1,
                ),
                generator=crash_after_attempt_started,
                now=NOW,
            )
        )
    run = db_session.query(OutboundRun).one()
    db_session.add(OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
        config_revision=CONFIG_REVISION,
        status="open",
        reason_type="unauthorized",
        opened_at=NOW + timedelta(seconds=1),
        created_at=NOW + timedelta(seconds=1),
        updated_at=NOW + timedelta(seconds=1),
    ))
    db_session.commit()
    generated = []
    recovery_config = ScheduledTaskProducerConfig(
        endpoint_config_revision=CONFIG_REVISION,
        producer_owner="scheduled-producer-b",
        writer_token="scheduled-writer-token-b",
        claim_lease_seconds=60,
        writer_lease_seconds=60,
    )

    first = run_async(
        recover_expired_scheduled_task_occurrences(
            session_factory=database.SessionLocal,
            config=recovery_config,
            generator=lambda snapshot: generated.append(snapshot.task_id),
            now=NOW + timedelta(seconds=2),
        )
    )
    second = run_async(
        recover_expired_scheduled_task_occurrences(
            session_factory=database.SessionLocal,
            config=recovery_config,
            generator=lambda snapshot: generated.append(snapshot.task_id),
            now=NOW + timedelta(seconds=3),
        )
    )

    db_session.expire_all()
    run = db_session.get(OutboundRun, run.id)
    attempt = db_session.query(OutboundGenerationAttempt).one()
    assert [result.status for result in first] == ["blocked"]
    assert second == []
    assert generated == []
    assert run.status == "blocked"
    assert run.failure_type == "circuit_open"
    assert attempt.status == "abandoned"
