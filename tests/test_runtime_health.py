from __future__ import annotations

import asyncio
import threading

import pytest


def test_session_factory_from_session_reuses_bind_without_reusing_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from core.database import session_factory_from_session

    engine = create_engine("sqlite:///:memory:")
    source_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    source = source_factory()
    derived = session_factory_from_session(source)()
    try:
        assert derived is not source
        assert derived.get_bind() is source.get_bind()
        assert derived.autoflush is False
        assert derived.expire_on_commit is False
    finally:
        derived.close()
        source.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_run_session_phase_async_keeps_session_on_one_worker_thread():
    from core.database import run_session_phase_async

    main_thread = threading.get_ident()
    calls: list[tuple[str, int]] = []

    class FakeSession:
        def rollback(self):
            calls.append(("rollback", threading.get_ident()))

        def close(self):
            calls.append(("close", threading.get_ident()))

    def factory():
        calls.append(("create", threading.get_ident()))
        return FakeSession()

    def operation(_db):
        calls.append(("operate", threading.get_ident()))
        return "ok"

    assert await run_session_phase_async(operation, session_factory=factory) == "ok"
    worker_threads = {thread_id for _, thread_id in calls}
    assert len(worker_threads) == 1
    assert main_thread not in worker_threads
    assert [name for name, _ in calls] == ["create", "operate", "close"]


@pytest.mark.asyncio
async def test_run_session_phase_async_does_not_block_event_loop():
    from core.database import run_session_phase_async

    entered = threading.Event()
    release = threading.Event()
    event_loop_progressed = asyncio.Event()

    class FakeSession:
        def rollback(self):
            pass

        def close(self):
            pass

    def operation(_db):
        entered.set()
        release.wait(timeout=1)

    task = asyncio.create_task(
        run_session_phase_async(operation, session_factory=FakeSession)
    )
    assert await asyncio.to_thread(entered.wait, 1)
    await asyncio.sleep(0)
    event_loop_progressed.set()
    assert event_loop_progressed.is_set()
    release.set()
    await task


def test_readiness_requires_startup_database_prompt_and_bridge(monkeypatch):
    from core import runtime_health

    runtime_health.mark_starting(testing=False)
    monkeypatch.setattr(runtime_health, "_database_ready", lambda: True)
    monkeypatch.setattr(
        runtime_health,
        "agent_runtime_binding_state",
        lambda: "stopped",
    )
    initial = runtime_health.readiness_snapshot()
    assert initial["ready"] is False
    assert set(initial["blocking_reasons"]) == {
        "startup_complete",
        "prompt_runtime",
        "bridge",
    }

    runtime_health.mark_prompt_runtime_ready()
    runtime_health.mark_startup_complete()
    monkeypatch.setattr(
        runtime_health,
        "agent_runtime_binding_state",
        lambda: "running",
    )
    ready = runtime_health.readiness_snapshot()
    assert ready["ready"] is True
    assert ready["status"] == "ready"

    runtime_health.mark_stopping()
    assert runtime_health.readiness_snapshot()["ready"] is False


def test_readiness_endpoint_returns_503_then_200(client, monkeypatch):
    from core import runtime_health

    runtime_health.mark_starting(testing=True)
    monkeypatch.setattr(runtime_health, "_database_ready", lambda: True)
    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"

    runtime_health.mark_prompt_runtime_ready()
    runtime_health.mark_startup_complete()
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    runtime_health.mark_stopping()


def test_database_healthcheck_cli_uses_local_database_probe(monkeypatch):
    from core import runtime_health

    monkeypatch.setattr(runtime_health, "_database_ready", lambda: True)
    assert runtime_health.database_healthcheck_main() == 0

    monkeypatch.setattr(runtime_health, "_database_ready", lambda: False)
    assert runtime_health.database_healthcheck_main() == 1
