"""周期自检 Watchdog 的热开关、运行接线与失败心跳。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.sqlite_test_utils import install_base_schema


@pytest.fixture
def watchdog_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'selfcheck-watchdog.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    install_base_schema(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_watchdog_disabled_does_not_open_database_or_create_run(monkeypatch):
    from workers import selfcheck_watchdog as watchdog

    monkeypatch.setattr(
        watchdog.settings,
        "get_bool",
        lambda key, default=False: False
        if key == "selfcheck.watchdog_enabled"
        else default,
    )

    def forbidden_session():
        raise AssertionError("关闭 Watchdog 时不得打开数据库")

    result = watchdog.run_watchdog_once(
        app=SimpleNamespace(state=SimpleNamespace()),
        session_factory=forbidden_session,
        engine_factory=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("关闭 Watchdog 时不得构造自检引擎")
        ),
    )

    assert result is None


def test_watchdog_reads_hot_settings_and_runs_with_runtime_registry(monkeypatch):
    from workers import selfcheck_watchdog as watchdog

    calls: dict[str, object] = {}

    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    class FakeRegistry:
        def descriptors(self):
            return ("nanobot", "pabot")

    class FakeEngine:
        def __init__(self, **kwargs):
            calls["engine_kwargs"] = kwargs

        def run(self, **kwargs):
            calls["run_kwargs"] = kwargs
            return SimpleNamespace(
                run_id="selfcheck-watchdog-test",
                status="passed",
                summary={"failed": 0},
            )

    db = FakeSession()

    def fake_get_bool(key, default=False):
        return {
            "selfcheck.watchdog_enabled": True,
            "selfcheck.model_canary_enabled": True,
        }.get(key, default)

    monkeypatch.setattr(watchdog.settings, "get_bool", fake_get_bool)
    monkeypatch.setattr(
        watchdog,
        "_agent_runtime_registry",
        lambda: FakeRegistry(),
    )
    monkeypatch.setattr(
        watchdog,
        "_endpoint_contracts",
        lambda: ("contract",),
    )

    report = watchdog.run_watchdog_once(
        app=SimpleNamespace(state=SimpleNamespace()),
        session_factory=lambda: db,
        engine_factory=FakeEngine,
    )

    assert report.status == "passed"
    assert calls["engine_kwargs"]["allow_model_checks"] is True
    assert calls["engine_kwargs"]["agent_descriptors"] == (
        "nanobot",
        "pabot",
    )
    assert calls["engine_kwargs"]["endpoint_contracts"] == ("contract",)
    assert calls["run_kwargs"] == {
        "trigger": "watchdog",
        "requested_by": "selfcheck-watchdog",
    }
    assert db.closed is True


def test_watchdog_cycle_failure_is_recorded_without_exception_body(
    watchdog_session_factory,
    monkeypatch,
):
    from core.db.models.selfcheck import WorkerHeartbeat
    from workers import selfcheck_watchdog as watchdog

    class OneCycleStopEvent:
        def __init__(self):
            self.wait_calls = 0

        def is_set(self):
            return False

        def wait(self, _seconds):
            self.wait_calls += 1
            return self.wait_calls >= 2

    monkeypatch.setattr(
        watchdog.settings,
        "get_bool",
        lambda key, default=False: True
        if key == "selfcheck.watchdog_enabled"
        else default,
    )
    monkeypatch.setattr(
        watchdog.settings,
        "get_int",
        lambda _key, _default=900: 60,
    )
    monkeypatch.setattr(
        watchdog,
        "run_watchdog_once",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("不得写入心跳的异常正文")
        ),
    )

    watchdog.run_until_stopped(
        OneCycleStopEvent(),
        app=SimpleNamespace(state=SimpleNamespace()),
        session_factory=watchdog_session_factory,
        initial_delay_seconds=0,
    )

    with watchdog_session_factory() as db:
        row = db.get(WorkerHeartbeat, watchdog.WORKER_ID)
        assert row is not None
        assert (row.cycle_count, row.success_count, row.failure_count) == (
            1,
            0,
            1,
        )
        assert row.last_error_code == "selfcheck_watchdog_cycle_failed"
        assert "异常正文" not in row.metadata_json
