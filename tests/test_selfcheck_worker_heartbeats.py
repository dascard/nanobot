from __future__ import annotations

import asyncio
import json
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.sqlite_test_utils import install_base_schema


@pytest.fixture
def heartbeat_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'selfcheck-worker-heartbeats.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    install_base_schema(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _heartbeat(factory, worker_id: str):
    from core.db.models.selfcheck import WorkerHeartbeat

    with factory() as db:
        return db.get(WorkerHeartbeat, worker_id)


@pytest.mark.asyncio
async def test_chat_delivery_loop_records_real_cycle_heartbeat(
    heartbeat_session_factory,
    monkeypatch,
):
    from workers import chat_delivery_worker as worker

    stop_event = threading.Event()

    async def fake_cycle(**_kwargs):
        stop_event.set()
        return {
            "processed": 2,
            "delivered": 1,
            "failed": 1,
            "ambiguous": 0,
        }

    monkeypatch.setattr(worker, "_run_once_with_publisher", fake_cycle)
    await worker.run_forever_async(
        stop_event,
        publisher=lambda *_args: True,
        session_factory=heartbeat_session_factory,
        owner="chat-heartbeat-test",
        interval=0.05,
    )

    row = _heartbeat(heartbeat_session_factory, "chat-delivery-worker")
    assert row is not None
    assert row.instance_id == "chat-heartbeat-test"
    assert row.mode == "embedded"
    assert (row.cycle_count, row.success_count, row.failure_count) == (1, 1, 0)
    assert json.loads(row.metadata_json) == {
        "ambiguous": 0,
        "delivered": 1,
        "failed": 1,
        "processed": 2,
    }


@pytest.mark.asyncio
async def test_outbound_delivery_loop_records_real_cycle_heartbeat(
    heartbeat_session_factory,
    monkeypatch,
):
    from core.outbound_delivery_service import OutboundWorkerConfig
    from workers import outbound_delivery_worker as worker

    stop_event = asyncio.Event()

    async def fake_cycle(**_kwargs):
        stop_event.set()
        return {
            "processed": 1,
            "delivered": 1,
            "retried": 0,
            "failed": 0,
            "ambiguous": 0,
        }

    async def fake_transport(_request):
        raise AssertionError("心跳测试不得调用外部投递")

    monkeypatch.setattr(worker, "_run_worker_cycle", fake_cycle)
    await worker.run_forever_async(
        stop_event,
        transport=fake_transport,
        session_factory=heartbeat_session_factory,
        config=OutboundWorkerConfig(
            push_url="http://qq.invalid/push",
            push_token="selfcheck-heartbeat-test-token",
            push_timeout_seconds=1,
            endpoint_config_revision="test",
            batch_size=1,
            lease_seconds=120,
            poll_interval_seconds=0.05,
        ),
        owner="outbound-heartbeat-test",
    )

    row = _heartbeat(heartbeat_session_factory, "outbound-delivery-worker")
    assert row is not None
    assert row.instance_id == "outbound-heartbeat-test"
    assert row.mode == "external"
    assert (row.cycle_count, row.success_count, row.failure_count) == (1, 1, 0)


@pytest.mark.asyncio
async def test_session_summary_loop_records_real_cycle_heartbeat(
    heartbeat_session_factory,
    monkeypatch,
):
    from workers import session_summary_worker as worker

    stop_event = threading.Event()

    async def fake_run_once(**_kwargs):
        stop_event.set()
        return {"processed": 1, "done": 1, "failed": 0, "recovered": 0}

    monkeypatch.setattr(worker, "SessionLocal", heartbeat_session_factory)
    monkeypatch.setattr(worker, "run_once_async", fake_run_once)
    await worker.run_forever_async(
        owner="summary-heartbeat-test",
        interval=0.05,
        stop_event=stop_event,
    )

    row = _heartbeat(heartbeat_session_factory, "session-summary-worker")
    assert row is not None
    assert row.instance_id == "summary-heartbeat-test"
    assert row.mode == "embedded"
    assert (row.cycle_count, row.success_count, row.failure_count) == (1, 1, 0)


def test_semantic_index_loop_records_successful_cycle_heartbeat(
    heartbeat_session_factory,
    monkeypatch,
):
    from workers import semantic_index_worker as worker

    class StopLoop(RuntimeError):
        pass

    monkeypatch.setattr(worker, "SessionLocal", heartbeat_session_factory)
    monkeypatch.setattr(worker, "run_once", lambda **_kwargs: False)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: (_ for _ in ()).throw(StopLoop()))

    with pytest.raises(StopLoop):
        worker.run_forever(worker_id="semantic-heartbeat-test", interval_seconds=0.1)

    row = _heartbeat(heartbeat_session_factory, "semantic-index-worker")
    assert row is not None
    assert row.instance_id == "semantic-heartbeat-test"
    assert row.mode == "external"
    assert (row.cycle_count, row.success_count, row.failure_count) == (1, 1, 0)


def test_semantic_index_loop_records_failed_cycle_without_secret(
    heartbeat_session_factory,
    monkeypatch,
):
    from workers import semantic_index_worker as worker

    monkeypatch.setattr(worker, "SessionLocal", heartbeat_session_factory)

    def fail_cycle(**_kwargs):
        raise RuntimeError("不应进入心跳或日志的敏感正文")

    monkeypatch.setattr(worker, "run_once", fail_cycle)
    with pytest.raises(RuntimeError):
        worker.run_forever(worker_id="semantic-failed-test", interval_seconds=0.1)

    row = _heartbeat(heartbeat_session_factory, "semantic-index-worker")
    assert row is not None
    assert row.instance_id == "semantic-failed-test"
    assert (row.cycle_count, row.success_count, row.failure_count) == (1, 0, 1)
    assert row.last_error_code == "semantic_index_cycle_failed"
    assert "敏感正文" not in row.metadata_json
