from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from core.database import (
    OutboundDeliveryAttempt,
    OutboundDeliveryCircuit,
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
)
from core.outbound_delivery import (
    OutboundConflictError,
    OutboundFencingError,
    claim_due_outbox,
    claim_outbound_run,
    commit_generated_outbox,
    endpoint_circuit_fingerprint,
    settle_delivery_attempt,
    start_generation_attempt,
)
from core.outbound_delivery_service import (
    OutboundDeliveryWorkResult,
    OutboundTransportRequest,
    OutboundWorkerConfig,
    deliver_outbound_once,
)
from core.outbound_transport import DeliveryOutcome
from tests.sqlite_test_utils import install_base_schema
from workers.outbound_delivery_worker import run_once_async


NOW = datetime(2026, 7, 15, 12, 0, 0)
SOURCE_TYPE = "scheduled_task"
ENDPOINT_KEY = "qq_push"
CONFIG_REVISION = "qq-config-r1"
PAYLOAD_CONTRACT = "qq-envelope-v1"


class SimulatedProcessCrash(BaseException):
    pass


class ScriptedTransport:
    def __init__(self, *results: DeliveryOutcome | BaseException):
        self.results = list(results)
        self.calls: list[OutboundTransportRequest] = []

    async def __call__(
        self,
        request: OutboundTransportRequest,
    ) -> DeliveryOutcome:
        self.calls.append(request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _outcome(
    category: str,
    status_code: int | None,
    *,
    error_type: str = "",
    retry_after_seconds: int | None = None,
    transport_phase: str = "response_received",
) -> DeliveryOutcome:
    return DeliveryOutcome(
        category=category,
        error_type=error_type,
        status_code=status_code,
        retry_after_seconds=retry_after_seconds,
        duration_ms=7,
        safe_summary="",
        transport_phase=transport_phase,
    )


def _config(**overrides: Any) -> OutboundWorkerConfig:
    values = {
        "push_url": "http://qq.test/nanobot/push",
        "push_token": "push-token-config-sentinel",
        "push_timeout_seconds": 1.0,
        "endpoint_config_revision": CONFIG_REVISION,
        "batch_size": 20,
        "lease_seconds": 60.0,
        "poll_interval_seconds": 0.01,
    }
    values.update(overrides)
    return OutboundWorkerConfig(**values)


def _work_result(
    outbox_id: int,
    *,
    outbox_status: str = "delivered",
) -> OutboundDeliveryWorkResult:
    return OutboundDeliveryWorkResult(
        outbox_id=outbox_id,
        attempt_id=outbox_id,
        attempt_no=1,
        payload_sha256=f"{outbox_id:064x}",
        outbox_status=outbox_status,
        run_status=("succeeded" if outbox_status == "delivered" else "queued"),
    )


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("success", ""),
        ("transient", "delivery.transient"),
        ("ambiguous", "delivery.ambiguous"),
        ("endpoint", "delivery.permanent_failure"),
        ("destination", "delivery.permanent_failure"),
        ("payload", "delivery.permanent_failure"),
        ("payload_contract", "delivery.permanent_failure"),
    ],
)
def test_delivery_outcome_has_stable_telemetry_failure_code(
    category,
    expected,
):
    from core.outbound_delivery_service import _delivery_failure_code

    assert _delivery_failure_code(category) == expected


@pytest.mark.asyncio
async def test_worker_runs_normal_and_both_legacy_lanes_with_shared_dependencies(
    monkeypatch,
):
    from core import proactive_outreach, scheduled_task_outbound
    from workers import outbound_delivery_worker as worker

    config = _config(batch_size=2)
    calls = []
    takeovers = {}
    normal_results = [_work_result(1), None]

    async def shared_transport(_request):
        raise AssertionError("编排测试不得真正调用 transport")

    async def fake_normal(**kwargs):
        calls.append(("normal", kwargs["transport"], kwargs["config"]))
        return normal_results.pop(0)

    async def fake_scheduled(**kwargs):
        calls.append(("scheduled", kwargs["transport"], kwargs["worker_config"]))
        takeovers["scheduled"] = kwargs["takeover_writer"]
        assert kwargs["limit"] == 2
        return [_work_result(2)]

    async def fake_proactive(**kwargs):
        calls.append(("proactive", kwargs["transport"], kwargs["worker_config"]))
        takeovers["proactive"] = kwargs["takeover_writer"]
        assert kwargs["limit"] == 2
        return [_work_result(3)]

    monkeypatch.setattr(worker, "deliver_outbound_once", fake_normal)
    monkeypatch.setattr(
        scheduled_task_outbound,
        "drain_due_legacy_scheduled_task_outboxes",
        fake_scheduled,
    )
    monkeypatch.setattr(
        proactive_outreach,
        "drain_due_legacy_proactive_outboxes",
        fake_proactive,
    )

    stats = await worker.run_once_async(
        transport=shared_transport,
        session_factory=lambda: None,
        config=config,
        owner="worker-lanes",
        now=NOW,
        limit=2,
    )

    assert [call[0] for call in calls] == [
        "normal",
        "normal",
        "scheduled",
        "proactive",
    ]
    assert all(call[1] is shared_transport for call in calls)
    assert all(call[2] is config for call in calls)
    assert takeovers["scheduled"].writer_owner == (
        "worker-lanes:scheduled_legacy"
    )
    assert takeovers["proactive"].writer_owner == (
        "worker-lanes:proactive_legacy"
    )
    assert takeovers["scheduled"].writer_token == (
        takeovers["proactive"].writer_token
    )
    assert config.push_token not in repr(takeovers)
    assert stats["processed"] == 3
    assert stats["delivered"] == 3


@pytest.mark.asyncio
async def test_worker_legacy_lane_failure_does_not_starve_next_lane(
    monkeypatch,
    caplog,
):
    from core import proactive_outreach, scheduled_task_outbound
    from workers import outbound_delivery_worker as worker

    calls = []

    async def transport(_request):
        raise AssertionError("编排测试不得真正调用 transport")

    async def no_normal(**_kwargs):
        return None

    async def failed_scheduled(**_kwargs):
        calls.append("scheduled")
        raise RuntimeError("legacy-lane-secret")

    async def successful_proactive(**_kwargs):
        calls.append("proactive")
        return [_work_result(4)]

    monkeypatch.setattr(worker, "deliver_outbound_once", no_normal)
    monkeypatch.setattr(
        scheduled_task_outbound,
        "drain_due_legacy_scheduled_task_outboxes",
        failed_scheduled,
    )
    monkeypatch.setattr(
        proactive_outreach,
        "drain_due_legacy_proactive_outboxes",
        successful_proactive,
    )

    stats = await worker.run_once_async(
        transport=transport,
        session_factory=lambda: None,
        config=_config(batch_size=1),
        owner="worker-lane-isolation",
        now=NOW,
    )

    assert calls == ["scheduled", "proactive"]
    assert stats["processed"] == 1
    assert "legacy-lane-secret" not in caplog.text


@pytest.mark.asyncio
async def test_worker_stop_after_legacy_slice_prevents_next_lane_claim(
    monkeypatch,
):
    from core import proactive_outreach, scheduled_task_outbound
    from workers import outbound_delivery_worker as worker

    stop_event = asyncio.Event()
    calls = []

    async def transport(_request):
        raise AssertionError("编排测试不得真正调用 transport")

    async def no_normal(**_kwargs):
        return None

    async def stop_in_scheduled(**_kwargs):
        calls.append("scheduled")
        stop_event.set()
        return [_work_result(5)]

    async def forbidden_proactive(**_kwargs):
        calls.append("proactive")
        return [_work_result(6)]

    monkeypatch.setattr(worker, "deliver_outbound_once", no_normal)
    monkeypatch.setattr(
        scheduled_task_outbound,
        "drain_due_legacy_scheduled_task_outboxes",
        stop_in_scheduled,
    )
    monkeypatch.setattr(
        proactive_outreach,
        "drain_due_legacy_proactive_outboxes",
        forbidden_proactive,
    )

    stats = await worker.run_once_async(
        transport=transport,
        session_factory=lambda: None,
        config=_config(batch_size=1),
        owner="worker-stop-between-lanes",
        stop_event=stop_event,
        now=NOW,
    )

    assert calls == ["scheduled"]
    assert stats["processed"] == 1


def _worker_environ(**overrides: str) -> dict[str, str]:
    values = {
        "QQBOT_PUSH_URL": "http://qq.test/nanobot/push",
        "QQBOT_PUSH_TIMEOUT": "180",
        "NANOBOT_QQ_PUSH_CONFIG_REVISION": CONFIG_REVISION,
        "NANOBOT_OUTBOUND_LEASE_SECONDS": "240",
    }
    values.update(overrides)
    return values


@pytest.fixture
def outbound_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'outbound-worker.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=5000")

    install_base_schema(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        engine.dispose()


def _seed_control(
    db,
    *,
    mode: str = "outbox_active",
    epoch: int = 7,
) -> None:
    db.add(
        OutboundDeliveryControl(
            source_type=SOURCE_TYPE,
            mode=mode,
            cutover_epoch=epoch,
            effective_from=NOW - timedelta(hours=1),
            protocol_version=2,
            writer_version=0,
            created_at=NOW - timedelta(days=1),
            updated_at=NOW - timedelta(days=1),
        )
    )
    db.flush()


def _seed_queued_outbox(
    factory,
    *,
    suffix: str = "1",
    mode: str = "outbox_active",
    epoch: int = 7,
    max_attempts: int = 3,
    retry_deadline_at: datetime | None = None,
    payload: dict[str, Any] | None = None,
    endpoint_key: str = ENDPOINT_KEY,
) -> int:
    with factory() as db:
        if db.get(OutboundDeliveryControl, SOURCE_TYPE) is None:
            _seed_control(db, mode=mode, epoch=epoch)
        target_id = f"opaque-user-{suffix}"
        destination = {
            "target_type": "private",
            "target_id": target_id,
        }
        claim = claim_outbound_run(
            db,
            source_type=SOURCE_TYPE,
            source_id=f"task-{suffix}",
            occurrence_key=f"slot-{suffix}",
            source_revision="revision-1",
            source_snapshot={
                "target_type": "private",
                "target_id": target_id,
                "prompt": "生成日报",
            },
            destination_snapshot=destination,
            target_type="private",
            task_kind="ai_digest",
            scheduled_for=NOW,
            trigger_type="cron",
            owner="producer-a",
            claim_lease_seconds=60,
            writer_owner="producer-a",
            writer_token="writer-a",
            writer_protocol_version=2,
            writer_lease_seconds=60,
            endpoint_key=endpoint_key,
            destination_fingerprint=f"destination-{suffix}",
            endpoint_config_revision=CONFIG_REVISION,
            payload_contract_fingerprint=PAYLOAD_CONTRACT,
            now=NOW,
        )
        generation = start_generation_attempt(
            db,
            run_id=claim.run_id,
            owner=claim.owner,
            claim_token=claim.claim_token,
            writer_owner="producer-a",
            writer_token="writer-a",
            writer_protocol_version=2,
            endpoint_key=endpoint_key,
            destination_fingerprint=f"destination-{suffix}",
            endpoint_config_revision=CONFIG_REVISION,
            payload_contract_fingerprint=PAYLOAD_CONTRACT,
            now=NOW,
        )
        envelope = payload or {
            "reply": f"已生成正文-{suffix}",
            "messages": [
                {"type": "text", "text": f"已生成正文-{suffix}"},
            ],
        }
        committed = commit_generated_outbox(
            db,
            run_id=claim.run_id,
            generation_attempt_id=generation.attempt_id,
            owner=claim.owner,
            claim_token=claim.claim_token,
            idempotency_key=f"delivery-{suffix}",
            destination_snapshot=destination,
            destination_fingerprint=f"destination-{suffix}",
            target_type="private",
            endpoint_key=endpoint_key,
            payload=envelope,
            max_attempts=max_attempts,
            retry_deadline_at=retry_deadline_at or NOW + timedelta(hours=1),
            endpoint_config_revision=CONFIG_REVISION,
            payload_contract_fingerprint=PAYLOAD_CONTRACT,
            model_trace_id=f"trace-{suffix}",
            now=NOW,
        )
        db.commit()
        assert committed.outbox_id is not None
        return committed.outbox_id


def _load_state(factory, outbox_id: int):
    with factory() as db:
        outbox = db.get(OutboundDeliveryOutbox, outbox_id)
        assert outbox is not None
        run = db.get(OutboundRun, outbox.run_id)
        attempts = (
            db.query(OutboundDeliveryAttempt)
            .filter(OutboundDeliveryAttempt.outbox_id == outbox_id)
            .order_by(OutboundDeliveryAttempt.attempt_no)
            .all()
        )
        generation_count = (
            db.query(OutboundGenerationAttempt)
            .filter(OutboundGenerationAttempt.run_id == outbox.run_id)
            .count()
        )
        db.expunge(outbox)
        if run is not None:
            db.expunge(run)
        for attempt in attempts:
            db.expunge(attempt)
        return outbox, run, attempts, generation_count


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"endpoint_config_revision": ""}, "NANOBOT_QQ_PUSH_CONFIG_REVISION"),
        ({"push_timeout_seconds": float("nan")}, "QQBOT_PUSH_TIMEOUT"),
        ({"batch_size": 0}, "NANOBOT_OUTBOUND_BATCH_SIZE"),
        ({"poll_interval_seconds": float("inf")}, "NANOBOT_OUTBOUND_POLL_INTERVAL"),
        ({"lease_seconds": 1.0}, "NANOBOT_OUTBOUND_LEASE_SECONDS"),
    ],
)
def test_worker_config_rejects_invalid_values(overrides, message):
    with pytest.raises(ValueError, match=message):
        _config(**overrides)


def test_worker_config_requires_dedicated_push_token():
    with pytest.raises(ValueError) as exc_info:
        OutboundWorkerConfig.from_env(_worker_environ())

    assert str(exc_info.value) == "NANOBOT_PUSH_TOKEN 未配置"


@pytest.mark.parametrize("codepoint", [*range(0x20), 0x7F])
def test_worker_config_rejects_control_char_push_token(codepoint):
    token = f"push-secret-{chr(codepoint)}-sentinel"

    with pytest.raises(ValueError) as exc_info:
        OutboundWorkerConfig.from_env(
            _worker_environ(NANOBOT_PUSH_TOKEN=token)
        )

    assert str(exc_info.value) == "NANOBOT_PUSH_TOKEN 包含非法控制字符"
    assert "push-secret" not in str(exc_info.value)


def test_worker_config_constructor_rejects_control_char_push_token():
    with pytest.raises(ValueError, match="NANOBOT_PUSH_TOKEN 包含非法控制字符"):
        OutboundWorkerConfig(
            push_url="http://qq.test/nanobot/push",
            push_token="push-secret-\x00-sentinel",
            push_timeout_seconds=180,
            endpoint_config_revision=CONFIG_REVISION,
            lease_seconds=240,
        )


def test_worker_config_stores_stripped_token_without_revealing_repr():
    token = "push-token-repr-sentinel"
    config = OutboundWorkerConfig.from_env(
        _worker_environ(NANOBOT_PUSH_TOKEN=f"  {token}  ")
    )

    assert getattr(config, "push_token", None) == token
    assert token not in repr(config)


@pytest.mark.asyncio
async def test_default_transport_receives_configured_push_token(monkeypatch):
    import workers.outbound_delivery_worker as worker_module

    token = "push-token-worker-sentinel"
    observed = {}

    class FakeClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    session = FakeClientSession()

    async def fake_deliver(session_arg, **kwargs):
        observed["session"] = session_arg
        observed.update(kwargs)
        return _outcome("success", 200)

    monkeypatch.setattr(worker_module.aiohttp, "ClientSession", lambda: session)
    monkeypatch.setattr(
        worker_module,
        "deliver_qq_push_with_session",
        fake_deliver,
    )
    request = OutboundTransportRequest(
        push_url="http://qq.test/nanobot/push",
        target_type="private",
        target_id="target-sentinel",
        message="测试消息",
        timeout_seconds=1,
        payload_sha256="a" * 64,
        outbox_id=1,
        attempt_no=1,
        now=NOW,
    )

    async with worker_module._transport_scope(
        None,
        push_token=token,
    ) as transport:
        outcome = await transport(request)

    assert outcome.category == "success"
    assert observed["session"] is session
    assert observed["push_token"] == token


@pytest.mark.asyncio
async def test_request_boundary_is_committed_before_transport(outbound_factory):
    outbox_id = _seed_queued_outbox(outbound_factory)
    observed = {}

    async def transport(request: OutboundTransportRequest) -> DeliveryOutcome:
        with outbound_factory() as db:
            outbox = db.get(OutboundDeliveryOutbox, outbox_id)
            attempt = db.query(OutboundDeliveryAttempt).one()
            run = db.get(OutboundRun, outbox.run_id)
            observed.update(
                outbox_status=outbox.status,
                request_started_count=outbox.request_started_count,
                attempt_status=attempt.status,
                attempt_phase=attempt.transport_phase,
                request_started=attempt.request_started,
                run_status=run.status,
                message=request.message,
            )
        return _outcome("success", 200)

    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-a",
        now=NOW + timedelta(seconds=1),
    )

    assert observed == {
        "outbox_status": "leased",
        "request_started_count": 1,
        "attempt_status": "started",
        "attempt_phase": "request_started",
        "request_started": True,
        "run_status": "delivering",
        "message": "已生成正文-1",
    }
    assert result is not None and result.outbox_status == "delivered"


@pytest.mark.asyncio
async def test_503_retry_reuses_exact_persisted_payload(outbound_factory):
    outbox_id = _seed_queued_outbox(outbound_factory)
    transport = ScriptedTransport(
        _outcome("transient", 503, error_type="service_unavailable"),
        _outcome("success", 200),
    )

    first = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-a",
        now=NOW + timedelta(seconds=1),
        jitter=lambda _maximum: 1.0,
    )
    second = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-b",
        now=NOW + timedelta(seconds=2),
        jitter=lambda _maximum: 1.0,
    )

    assert first is not None and first.outbox_status == "retry_wait"
    assert second is not None and second.outbox_status == "delivered"
    assert [call.payload_sha256 for call in transport.calls] == [
        transport.calls[0].payload_sha256,
        transport.calls[0].payload_sha256,
    ]
    assert [call.message for call in transport.calls] == [
        "已生成正文-1",
        "已生成正文-1",
    ]
    outbox, run, attempts, generation_count = _load_state(
        outbound_factory,
        outbox_id,
    )
    assert outbox.status == "delivered"
    assert outbox.allocated_attempt_count == 2
    assert outbox.request_started_count == 2
    assert [attempt.attempt_no for attempt in attempts] == [1, 2]
    assert [attempt.status for attempt in attempts] == [
        "transient_failure",
        "succeeded",
    ]
    assert run.status == "succeeded"
    assert generation_count == 1


@pytest.mark.asyncio
async def test_crash_before_request_boundary_is_safely_recovered(
    outbound_factory,
    monkeypatch,
):
    from core import outbound_delivery_service as service

    outbox_id = _seed_queued_outbox(outbound_factory, max_attempts=1)
    original = service.mark_delivery_request_started

    def crash_before_boundary(*_args, **_kwargs):
        raise SimulatedProcessCrash()

    monkeypatch.setattr(service, "mark_delivery_request_started", crash_before_boundary)
    with pytest.raises(SimulatedProcessCrash):
        await deliver_outbound_once(
            session_factory=outbound_factory,
            transport=ScriptedTransport(_outcome("success", 200)),
            config=_config(),
            worker_owner="worker-a",
            now=NOW + timedelta(seconds=1),
        )
    monkeypatch.setattr(service, "mark_delivery_request_started", original)

    transport = ScriptedTransport(_outcome("success", 200))
    recovered = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-b",
        now=NOW + timedelta(seconds=62),
    )

    assert recovered is not None and recovered.outbox_status == "delivered"
    outbox, _run, attempts, _generation_count = _load_state(
        outbound_factory,
        outbox_id,
    )
    assert outbox.allocated_attempt_count == 2
    assert outbox.request_started_count == 1
    assert [(row.attempt_no, row.status) for row in attempts] == [
        (1, "abandoned_before_send"),
        (2, "succeeded"),
    ]
    assert len(transport.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_during", ["transport", "settlement"])
async def test_crash_after_request_boundary_becomes_ambiguous(
    outbound_factory,
    monkeypatch,
    crash_during,
):
    from core import outbound_delivery_service as service

    outbox_id = _seed_queued_outbox(outbound_factory)
    transport = ScriptedTransport(
        SimulatedProcessCrash()
        if crash_during == "transport"
        else _outcome("success", 200)
    )
    if crash_during == "settlement":
        monkeypatch.setattr(
            service,
            "settle_delivery_attempt",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(SimulatedProcessCrash()),
        )

    with pytest.raises(SimulatedProcessCrash):
        await deliver_outbound_once(
            session_factory=outbound_factory,
            transport=transport,
            config=_config(),
            worker_owner="worker-a",
            now=NOW + timedelta(seconds=1),
        )

    no_second_send = ScriptedTransport(_outcome("success", 200))
    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=no_second_send,
        config=_config(),
        worker_owner="worker-b",
        now=NOW + timedelta(seconds=62),
    )

    assert result is None
    assert no_second_send.calls == []
    outbox, run, attempts, _generation_count = _load_state(
        outbound_factory,
        outbox_id,
    )
    assert outbox.status == "ambiguous"
    assert outbox.request_started_count == 1
    assert [row.status for row in attempts] == ["ambiguous"]
    assert run.status == "ambiguous"
    assert run.has_ambiguous_ancestor is True


@pytest.mark.asyncio
async def test_settlement_db_retry_never_repeats_http(
    outbound_factory,
    monkeypatch,
):
    from core import outbound_delivery_service as service

    _seed_queued_outbox(outbound_factory)
    transport = ScriptedTransport(_outcome("success", 200))
    original = service.settle_delivery_attempt
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("settlement", {}, RuntimeError("db down"))
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "settle_delivery_attempt", fail_once)
    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-a",
        now=NOW + timedelta(seconds=1),
        settlement_retry_sleep=lambda _seconds: asyncio.sleep(0),
    )

    assert result is not None and result.outbox_status == "delivered"
    assert calls == 2
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_two_workers_only_one_calls_transport(outbound_factory):
    _seed_queued_outbox(outbound_factory)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def blocking_transport(request: OutboundTransportRequest):
        calls.append(request)
        entered.set()
        await release.wait()
        return _outcome("success", 200)

    first_task = asyncio.create_task(
        deliver_outbound_once(
            session_factory=outbound_factory,
            transport=blocking_transport,
            config=_config(),
            worker_owner="worker-a",
            now=NOW + timedelta(seconds=1),
        )
    )
    await entered.wait()
    second = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=blocking_transport,
        config=_config(),
        worker_owner="worker-b",
        now=NOW + timedelta(seconds=1),
    )
    release.set()
    first = await first_task

    assert first is not None and first.outbox_status == "delivered"
    assert second is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_max_request_attempts_end_in_retry_exhausted(outbound_factory):
    outbox_id = _seed_queued_outbox(outbound_factory, max_attempts=2)
    transport = ScriptedTransport(
        _outcome("transient", 503, error_type="service_unavailable"),
        _outcome("transient", 503, error_type="service_unavailable"),
    )

    for offset in (1, 2):
        await deliver_outbound_once(
            session_factory=outbound_factory,
            transport=transport,
            config=_config(),
            worker_owner=f"worker-{offset}",
            now=NOW + timedelta(seconds=offset),
            jitter=lambda _maximum: 1.0,
        )
    third = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-3",
        now=NOW + timedelta(seconds=3),
    )

    assert third is None
    assert len(transport.calls) == 2
    outbox, run, attempts, _generation_count = _load_state(
        outbound_factory,
        outbox_id,
    )
    assert outbox.status == "failed"
    assert outbox.last_error_type == "retry_exhausted"
    assert outbox.request_started_count == 2
    assert run.status == "failed"
    assert [row.status for row in attempts] == [
        "transient_failure",
        "transient_failure",
    ]


@pytest.mark.asyncio
async def test_retry_at_deadline_exhausts_without_second_http(outbound_factory):
    outbox_id = _seed_queued_outbox(
        outbound_factory,
        retry_deadline_at=NOW + timedelta(seconds=2),
    )
    transport = ScriptedTransport(
        _outcome("transient", 503, error_type="service_unavailable"),
    )

    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-a",
        now=NOW + timedelta(seconds=1),
        jitter=lambda _maximum: 1.0,
    )

    assert result is not None and result.outbox_status == "failed"
    assert len(transport.calls) == 1
    outbox, run, attempts, _ = _load_state(outbound_factory, outbox_id)
    assert outbox.last_error_type == "retry_exhausted"
    assert outbox.next_attempt_at is None
    assert run.status == "failed"
    assert [row.status for row in attempts] == ["transient_failure"]


@pytest.mark.asyncio
async def test_abandoned_attempt_number_does_not_inflate_network_backoff(
    outbound_factory,
    monkeypatch,
):
    from core import outbound_delivery_service as service

    outbox_id = _seed_queued_outbox(outbound_factory)
    original_mark = service.mark_delivery_request_started
    claims = []
    original_claim = service.claim_due_outbox

    def record_claim(*args, **kwargs):
        claim = original_claim(*args, **kwargs)
        if claim is not None:
            claims.append(claim)
        return claim

    monkeypatch.setattr(service, "claim_due_outbox", record_claim)
    monkeypatch.setattr(
        service,
        "mark_delivery_request_started",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SimulatedProcessCrash()),
    )
    with pytest.raises(SimulatedProcessCrash):
        await deliver_outbound_once(
            session_factory=outbound_factory,
            transport=ScriptedTransport(_outcome("success", 200)),
            config=_config(),
            worker_owner="worker-a",
            now=NOW + timedelta(seconds=1),
        )
    stale_claim = claims[0]
    monkeypatch.setattr(service, "mark_delivery_request_started", original_mark)

    maxima = []
    transient = ScriptedTransport(
        _outcome("transient", 503, error_type="service_unavailable"),
    )
    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transient,
        config=_config(),
        worker_owner="worker-b",
        now=NOW + timedelta(seconds=62),
        jitter=lambda maximum: maxima.append(maximum) or 1.0,
    )

    assert result is not None and result.outbox_status == "retry_wait"
    assert maxima == [1.0]
    with outbound_factory() as db:
        with pytest.raises((OutboundFencingError, OutboundConflictError)):
            settle_delivery_attempt(
                db,
                outbox_id=stale_claim.outbox_id,
                attempt_id=stale_claim.attempt_id,
                worker_owner=stale_claim.worker_owner,
                lease_token=stale_claim.lease_token,
                outcome="permanent_failure",
                transport_phase="allocated",
                http_status=400,
                result_category="payload",
                error_type="stale_owner",
                safe_summary="旧 owner 不能结算",
                duration_ms=0,
                now=NOW + timedelta(seconds=63),
            )
        db.rollback()
    outbox, _run, attempts, _ = _load_state(outbound_factory, outbox_id)
    assert outbox.status == "retry_wait"
    assert outbox.request_started_count == 1
    assert [(row.attempt_no, row.status) for row in attempts] == [
        (1, "abandoned_before_send"),
        (2, "transient_failure"),
    ]


@pytest.mark.asyncio
async def test_empty_claim_commits_deadline_and_circuit_transitions(outbound_factory):
    expired_id = _seed_queued_outbox(
        outbound_factory,
        suffix="expired",
        retry_deadline_at=NOW + timedelta(seconds=1),
    )
    blocked_id = _seed_queued_outbox(outbound_factory, suffix="blocked")
    with outbound_factory() as db:
        db.add(
            OutboundDeliveryCircuit(
                scope_type="endpoint",
                scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
                config_revision=CONFIG_REVISION,
                status="open",
                reason_type="unauthorized",
                opened_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        db.commit()
    transport = ScriptedTransport(_outcome("success", 200))

    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-a",
        now=NOW + timedelta(seconds=2),
    )

    assert result is None
    assert transport.calls == []
    expired, expired_run, expired_attempts, _ = _load_state(
        outbound_factory,
        expired_id,
    )
    blocked, blocked_run, blocked_attempts, generation_count = _load_state(
        outbound_factory,
        blocked_id,
    )
    assert expired.status == "failed"
    assert expired.last_error_type == "retry_exhausted"
    assert expired_run.status == "failed"
    assert expired_attempts == []
    assert blocked.status == "blocked"
    assert blocked.last_error_type == "circuit_open"
    assert blocked_run.status == "blocked"
    assert blocked_attempts == []
    assert generation_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "epoch_delta", "expected_calls"),
    [
        ("outbox_hold", 0, 0),
        ("outbox_active", 1, 0),
        ("outbox_draining", 0, 1),
        ("outbox_draining", 1, 0),
    ],
)
async def test_control_mode_and_epoch_gate_transport(
    outbound_factory,
    mode,
    epoch_delta,
    expected_calls,
):
    outbox_id = _seed_queued_outbox(outbound_factory)
    with outbound_factory() as db:
        control = db.get(OutboundDeliveryControl, SOURCE_TYPE)
        control.mode = mode
        control.cutover_epoch += epoch_delta
        control.updated_at = NOW + timedelta(milliseconds=1)
        db.commit()
    transport = ScriptedTransport(_outcome("success", 200))

    await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-a",
        now=NOW + timedelta(seconds=1),
    )

    assert len(transport.calls) == expected_calls
    outbox, _run, attempts, _ = _load_state(outbound_factory, outbox_id)
    if expected_calls:
        assert outbox.status == "delivered"
        assert len(attempts) == 1
    else:
        assert outbox.status == "pending"
        assert attempts == []


@pytest.mark.asyncio
async def test_independent_worker_never_claims_legacy_direct_leaf(
    outbound_factory,
):
    outbox_id = _seed_queued_outbox(
        outbound_factory,
        mode="legacy_direct",
        epoch=0,
    )
    transport = ScriptedTransport(_outcome("success", 200))

    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-a",
        now=NOW + timedelta(seconds=1),
    )

    assert result is None
    assert transport.calls == []
    outbox, run, attempts, _ = _load_state(outbound_factory, outbox_id)
    assert outbox.status == "pending"
    assert run.status == "queued"
    assert attempts == []


@pytest.mark.asyncio
async def test_invalid_persisted_envelope_opens_contract_circuit_before_send(
    outbound_factory,
    monkeypatch,
):
    from core import outbound_delivery_service

    emitted = []

    def capture_event(name, phase, **kwargs):
        emitted.append((name, phase, kwargs))

    monkeypatch.setattr(
        outbound_delivery_service,
        "emit_runtime_event",
        capture_event,
    )
    outbox_id = _seed_queued_outbox(
        outbound_factory,
        payload={"content": "旧版未声明格式"},
    )
    transport = ScriptedTransport(_outcome("success", 200))

    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-a",
        now=NOW + timedelta(seconds=1),
    )

    assert result is not None and result.outbox_status == "failed"
    assert transport.calls == []
    outbox, run, attempts, _ = _load_state(outbound_factory, outbox_id)
    assert outbox.request_started_count == 0
    assert outbox.last_error_type == "delivery_contract_invalid"
    assert run.status == "blocked"
    assert [row.status for row in attempts] == ["permanent_failure"]
    assert emitted[-1][0:2] == ("delivery.attempt", "failed")
    assert emitted[-1][2]["attributes"]["failure_code"] == (
        "delivery.contract_invalid"
    )
    with outbound_factory() as db:
        circuit = db.query(OutboundDeliveryCircuit).one()
        assert circuit.scope_type == "payload_contract"


@pytest.mark.asyncio
async def test_qq_worker_never_claims_another_endpoint(outbound_factory):
    outbox_id = _seed_queued_outbox(
        outbound_factory,
        endpoint_key="webhook",
    )
    transport = ScriptedTransport(_outcome("success", 200))

    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="worker-a",
        now=NOW + timedelta(seconds=1),
    )

    assert result is None
    assert transport.calls == []
    outbox, run, attempts, _ = _load_state(outbound_factory, outbox_id)
    assert outbox.status == "pending"
    assert run.status == "queued"
    assert attempts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "retry_wait"])
async def test_qq_worker_never_terminalizes_expired_other_endpoint(
    outbound_factory,
    status,
):
    outbox_id = _seed_queued_outbox(
        outbound_factory,
        endpoint_key="webhook",
        retry_deadline_at=NOW + timedelta(seconds=2),
    )
    if status == "retry_wait":
        with outbound_factory() as db:
            outbox = db.get(OutboundDeliveryOutbox, outbox_id)
            outbox.status = "retry_wait"
            outbox.next_attempt_at = NOW + timedelta(seconds=1)
            db.commit()
    transport = ScriptedTransport(_outcome("success", 200))

    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        worker_owner="qq-worker",
        now=NOW + timedelta(seconds=3),
    )

    assert result is None
    assert transport.calls == []
    outbox, run, attempts, _ = _load_state(outbound_factory, outbox_id)
    assert outbox.status == status
    assert run.status == "queued"
    assert attempts == []


@pytest.mark.asyncio
async def test_qq_worker_never_expires_other_endpoint_lease(outbound_factory):
    outbox_id = _seed_queued_outbox(
        outbound_factory,
        endpoint_key="webhook",
    )
    with outbound_factory() as db:
        claim = claim_due_outbox(
            db,
            worker_owner="webhook-worker",
            lease_seconds=10,
            endpoint_config_revision="webhook-r1",
            endpoint_key="webhook",
            now=NOW + timedelta(seconds=1),
        )
        assert claim is not None
        db.commit()

    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=ScriptedTransport(_outcome("success", 200)),
        config=_config(),
        worker_owner="qq-worker",
        now=NOW + timedelta(seconds=12),
    )

    assert result is None
    outbox, run, attempts, _ = _load_state(outbound_factory, outbox_id)
    assert outbox.status == "leased"
    assert run.status == "delivering"
    assert [(row.status, row.request_started) for row in attempts] == [
        ("started", False),
    ]


@pytest.mark.asyncio
async def test_retry_and_settlement_use_http_completion_time(outbound_factory):
    outbox_id = _seed_queued_outbox(
        outbound_factory,
        retry_deadline_at=NOW + timedelta(seconds=5),
    )
    clock_values = iter(
        [
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=6),
        ]
    )

    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=ScriptedTransport(
            _outcome("transient", 503, error_type="service_unavailable"),
        ),
        config=_config(),
        worker_owner="worker-a",
        clock=lambda: next(clock_values),
        jitter=lambda _maximum: 1.0,
    )

    assert result is not None and result.outbox_status == "failed"
    outbox, run, attempts, _ = _load_state(outbound_factory, outbox_id)
    assert outbox.last_error_type == "retry_exhausted"
    assert outbox.next_attempt_at is None
    assert run.status == "failed"
    assert attempts[0].completed_at == NOW + timedelta(seconds=6)


@pytest.mark.asyncio
async def test_retry_delay_starts_after_http_completion(outbound_factory):
    outbox_id = _seed_queued_outbox(outbound_factory)
    clock_values = iter(
        [
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=10),
        ]
    )

    result = await deliver_outbound_once(
        session_factory=outbound_factory,
        transport=ScriptedTransport(
            _outcome("transient", 503, error_type="service_unavailable"),
        ),
        config=_config(),
        worker_owner="worker-a",
        clock=lambda: next(clock_values),
        jitter=lambda _maximum: 1.0,
    )

    assert result is not None and result.outbox_status == "retry_wait"
    outbox, _run, attempts, _ = _load_state(outbound_factory, outbox_id)
    assert outbox.next_attempt_at == NOW + timedelta(seconds=11)
    assert attempts[0].completed_at == NOW + timedelta(seconds=10)


@pytest.mark.asyncio
async def test_stop_event_finishes_current_and_prevents_next_claim(outbound_factory):
    first_id = _seed_queued_outbox(outbound_factory, suffix="first")
    second_id = _seed_queued_outbox(outbound_factory, suffix="second")
    stop_event = asyncio.Event()
    calls = []

    async def stop_during_transport(request: OutboundTransportRequest):
        calls.append(request)
        stop_event.set()
        return _outcome("success", 200)

    stats = await run_once_async(
        session_factory=outbound_factory,
        transport=stop_during_transport,
        config=_config(batch_size=2),
        owner="stable-worker",
        stop_event=stop_event,
        now=NOW + timedelta(seconds=1),
    )

    assert stats["processed"] == 1
    assert len(calls) == 1
    states = {
        _load_state(outbound_factory, first_id)[0].status,
        _load_state(outbound_factory, second_id)[0].status,
    }
    assert states == {"delivered", "pending"}


@pytest.mark.asyncio
async def test_pre_set_stop_event_claims_nothing(outbound_factory):
    outbox_id = _seed_queued_outbox(outbound_factory)
    stop_event = asyncio.Event()
    stop_event.set()
    transport = ScriptedTransport(_outcome("success", 200))

    stats = await run_once_async(
        session_factory=outbound_factory,
        transport=transport,
        config=_config(),
        owner="stable-worker",
        stop_event=stop_event,
        now=NOW + timedelta(seconds=1),
    )

    assert stats["processed"] == 0
    assert transport.calls == []
    outbox, run, attempts, _ = _load_state(outbound_factory, outbox_id)
    assert outbox.status == "pending"
    assert run.status == "queued"
    assert attempts == []


@pytest.mark.asyncio
async def test_database_sessions_run_outside_event_loop_thread(outbound_factory):
    _seed_queued_outbox(outbound_factory)
    session_threads = []

    def recording_factory():
        session_threads.append(threading.get_ident())
        return outbound_factory()

    event_loop_thread = threading.get_ident()
    result = await deliver_outbound_once(
        session_factory=recording_factory,
        transport=ScriptedTransport(_outcome("success", 200)),
        config=_config(),
        worker_owner="worker-a",
        now=NOW + timedelta(seconds=1),
    )

    assert result is not None and result.outbox_status == "delivered"
    assert session_threads
    assert all(thread_id != event_loop_thread for thread_id in session_threads)


def test_worker_import_graph_excludes_model_runtime():
    script = """
import os
import sys
os.environ.setdefault('NANOBOT_ADMIN_TOKEN', 'configured-for-import-test')
import workers.outbound_delivery_worker
forbidden = sorted(
    name for name in sys.modules
    if name == 'nanobot_kt.bridge'
    or name.startswith('clients.new_api_client')
    or 'model_registry' in name
)
print('|'.join(forbidden))
"""
    env = dict(os.environ)
    env["NANOBOT_ADMIN_TOKEN"] = "configured-for-import-test"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert completed.stdout.strip() == ""
