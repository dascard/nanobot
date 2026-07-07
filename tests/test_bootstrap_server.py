import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def test_server_import_has_no_startup_side_effects(monkeypatch):
    import bootstrap.network_check as network_check
    import bootstrap.provider_migration as provider_migration
    import bootstrap.schedulers as schedulers

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("server import must not run startup side effects")

    monkeypatch.setattr(provider_migration, "run_provider_migration", fail_if_called)
    monkeypatch.setattr(network_check, "run_startup_network_check", fail_if_called)
    monkeypatch.setattr(schedulers, "start_schedulers", fail_if_called)

    import server

    reloaded = importlib.reload(server)

    assert reloaded.app is not None


@pytest.mark.asyncio
async def test_lifespan_calls_bootstrap_facades(monkeypatch):
    import bootstrap.lifespan as bootstrap_lifespan
    import server

    calls: list[str] = []
    bridge = object()
    new_api_session = object()

    class Handles:
        def stop_all(self):
            calls.append("stop_schedulers")

    monkeypatch.setenv("NANOBOT_TESTING", "0")
    monkeypatch.setattr(bootstrap_lifespan, "init_db", lambda: calls.append("init_db"))
    monkeypatch.setattr(
        bootstrap_lifespan,
        "run_provider_migration",
        lambda: calls.append("provider_migration"),
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "init_prompt_runtimes",
        lambda logger: calls.append("prompt_runtime"),
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "start_schedulers",
        lambda *, testing, logger: calls.append(f"start_schedulers:{testing}") or Handles(),
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "run_startup_network_check",
        lambda logger: calls.append("network_check"),
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "init_legacy_memory",
        lambda: calls.append("legacy_memory"),
    )

    async def fake_init_bridge():
        calls.append("init_bridge")
        return bridge

    async def fake_shutdown_bridge():
        calls.append("shutdown_bridge")

    async def fake_init_new_api_session():
        calls.append("init_new_api_session")
        return new_api_session

    async def fake_shutdown_new_api_session(session):
        assert session is new_api_session
        calls.append("shutdown_new_api_session")

    monkeypatch.setattr(bootstrap_lifespan, "init_bridge", fake_init_bridge)
    monkeypatch.setattr(bootstrap_lifespan, "shutdown_bridge", fake_shutdown_bridge)
    monkeypatch.setattr(bootstrap_lifespan, "init_new_api_session", fake_init_new_api_session)
    monkeypatch.setattr(bootstrap_lifespan, "shutdown_new_api_session", fake_shutdown_new_api_session)

    app = SimpleNamespace(state=SimpleNamespace())
    async with server.lifespan(app):
        calls.append("inside")
        assert app.state.bridge is bridge
        assert app.state.new_api_session is new_api_session

    assert calls == [
        "init_db",
        "provider_migration",
        "prompt_runtime",
        "start_schedulers:False",
        "init_new_api_session",
        "network_check",
        "init_bridge",
        "legacy_memory",
        "inside",
        "stop_schedulers",
        "shutdown_bridge",
        "shutdown_new_api_session",
    ]
    assert app.state.bridge is None
    assert app.state.new_api_session is None


def test_cors_default_allows_any_origin():
    import server

    with TestClient(server.app) as client:
        response = client.options(
            "/api/v1/admin/me",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] in (
        "*",
        "http://example.com",
    )


def test_cors_origins_can_be_configured(monkeypatch):
    monkeypatch.setenv("NANOBOT_CORS_ORIGINS", "http://a.com,http://b.com")

    import server

    reloaded = importlib.reload(server)
    with TestClient(reloaded.app) as client:
        allowed = client.options(
            "/api/v1/admin/me",
            headers={
                "Origin": "http://a.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = client.options(
            "/api/v1/admin/me",
            headers={
                "Origin": "http://evil.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://a.com"
    assert "access-control-allow-origin" not in denied.headers


def test_start_schedulers_starts_session_summary_worker(monkeypatch):
    import config
    import bootstrap.schedulers as schedulers

    calls: list[str] = []

    class Logger:
        def info(self, *_args, **_kwargs):
            pass

        def warning(self, *_args, **_kwargs):
            pass

    def fake_start_thread(*, name, target):
        calls.append(name)
        return object()

    monkeypatch.setattr(config, "DAILY_DIGEST_ENABLED", False)
    monkeypatch.setattr(schedulers, "_start_thread", fake_start_thread)
    monkeypatch.setattr(schedulers, "_preload_sentinel", lambda logger: None)

    handles = schedulers.start_schedulers(testing=False, logger=Logger())

    assert "session-summary-worker" in calls
    assert handles.session_summary is not None


def test_start_schedulers_skips_proactive_outreach_when_disabled(monkeypatch):
    import config
    import bootstrap.schedulers as schedulers
    from core.settings_service import settings

    calls: list[str] = []

    class Logger:
        def info(self, *_args, **_kwargs):
            pass

        def warning(self, *_args, **_kwargs):
            pass

    def fake_start_thread(*, name, target):
        calls.append(name)
        return object()

    def fake_get_bool(key, default=False):
        if key == "proactive_outreach.enabled":
            return False
        return default

    monkeypatch.setattr(config, "DAILY_DIGEST_ENABLED", False)
    monkeypatch.setattr(schedulers, "_start_thread", fake_start_thread)
    monkeypatch.setattr(schedulers, "_preload_sentinel", lambda logger: None)
    monkeypatch.setattr(settings, "get_bool", fake_get_bool)

    handles = schedulers.start_schedulers(testing=False, logger=Logger())

    assert "proactive-outreach-scheduler" not in calls
    assert handles.proactive_outreach is None


def test_start_schedulers_starts_proactive_outreach_when_enabled(monkeypatch):
    import config
    import bootstrap.schedulers as schedulers
    from core.settings_service import settings

    calls: list[str] = []

    class Logger:
        def info(self, *_args, **_kwargs):
            pass

        def warning(self, *_args, **_kwargs):
            pass

    def fake_start_thread(*, name, target):
        calls.append(name)
        return f"handle:{name}"

    def fake_get_bool(key, default=False):
        if key == "proactive_outreach.enabled":
            return True
        return default

    monkeypatch.setattr(config, "DAILY_DIGEST_ENABLED", False)
    monkeypatch.setattr(schedulers, "_start_thread", fake_start_thread)
    monkeypatch.setattr(schedulers, "_preload_sentinel", lambda logger: None)
    monkeypatch.setattr(settings, "get_bool", fake_get_bool)

    handles = schedulers.start_schedulers(testing=False, logger=Logger())

    assert "proactive-outreach-scheduler" in calls
    assert handles.proactive_outreach == "handle:proactive-outreach-scheduler"


@pytest.mark.asyncio
async def test_lifespan_testing_mode_skips_bridge_network_and_scheduler_work(monkeypatch):
    import bootstrap.lifespan as bootstrap_lifespan
    import server

    calls: list[str] = []
    new_api_session = object()

    class Handles:
        def stop_all(self):
            calls.append("stop_schedulers")

    def start_schedulers(*, testing, logger):
        assert testing is True
        calls.append("start_schedulers_testing")
        return Handles()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("testing mode must skip this startup action")

    async def fake_init_new_api_session():
        calls.append("init_new_api_session")
        return new_api_session

    async def fake_shutdown_new_api_session(session):
        assert session is new_api_session
        calls.append("shutdown_new_api_session")

    monkeypatch.setenv("NANOBOT_TESTING", "1")
    monkeypatch.setattr(bootstrap_lifespan, "init_db", lambda: calls.append("init_db"))
    monkeypatch.setattr(
        bootstrap_lifespan,
        "run_provider_migration",
        lambda: calls.append("provider_migration"),
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "init_prompt_runtimes",
        lambda logger: calls.append("prompt_runtime"),
    )
    monkeypatch.setattr(bootstrap_lifespan, "start_schedulers", start_schedulers)
    monkeypatch.setattr(bootstrap_lifespan, "run_startup_network_check", fail_if_called)
    monkeypatch.setattr(bootstrap_lifespan, "init_bridge", fail_if_called)
    monkeypatch.setattr(bootstrap_lifespan, "shutdown_bridge", fail_if_called)
    monkeypatch.setattr(bootstrap_lifespan, "init_new_api_session", fake_init_new_api_session)
    monkeypatch.setattr(bootstrap_lifespan, "shutdown_new_api_session", fake_shutdown_new_api_session)
    monkeypatch.setattr(
        bootstrap_lifespan,
        "init_legacy_memory",
        lambda: calls.append("legacy_memory"),
    )

    app = SimpleNamespace(state=SimpleNamespace())
    async with server.lifespan(app):
        calls.append("inside")
        assert app.state.bridge is None
        assert app.state.new_api_session is new_api_session

    assert calls == [
        "init_db",
        "provider_migration",
        "prompt_runtime",
        "start_schedulers_testing",
        "init_new_api_session",
        "legacy_memory",
        "inside",
        "stop_schedulers",
        "shutdown_new_api_session",
    ]
    assert app.state.bridge is None
    assert app.state.new_api_session is None
