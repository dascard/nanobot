import importlib
import logging
from types import SimpleNamespace

import pytest
from tests.http_test_utils import open_test_client_without_lifespan


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
    async def fake_run_startup_network_check(logger, *, session):
        assert session is new_api_session
        calls.append("network_check")

    monkeypatch.setattr(
        bootstrap_lifespan,
        "run_startup_network_check",
        fake_run_startup_network_check,
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
        assert app.state.composition_root is not None

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
        "shutdown_bridge",
        "shutdown_new_api_session",
        "stop_schedulers",
    ]
    assert app.state.bridge is None
    assert app.state.new_api_session is None
    assert app.state.composition_root is None


@pytest.mark.asyncio
async def test_lifespan_startup_failure_releases_started_resources(monkeypatch):
    import bootstrap.lifespan as bootstrap_lifespan
    from core import daily_digest

    calls: list[str] = []
    maintenance = object()
    new_api_session = object()

    class Handles:
        def stop_all(self):
            calls.append("stop_schedulers")

    monkeypatch.setenv("NANOBOT_TESTING", "0")
    monkeypatch.setattr(bootstrap_lifespan, "init_db", lambda: calls.append("init_db"))
    monkeypatch.setattr(
        bootstrap_lifespan,
        "start_sqlite_maintenance",
        lambda: calls.append("start_sqlite_maintenance") or maintenance,
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "stop_sqlite_maintenance",
        lambda worker: calls.append(f"stop_sqlite_maintenance:{worker is maintenance}"),
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "validate_sandbox_asset_token_config",
        lambda: calls.append("validate_sandbox"),
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "run_provider_migration",
        lambda: calls.append("provider_migration"),
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "init_prompt_runtimes",
        lambda _logger: calls.append("prompt_runtime"),
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "start_schedulers",
        lambda *, testing, logger: calls.append("start_schedulers") or Handles(),
    )

    async def fake_init_new_api_session():
        calls.append("init_new_api_session")
        return new_api_session

    async def fake_shutdown_new_api_session(session):
        assert session is new_api_session
        calls.append("shutdown_new_api_session")

    async def fail_network_check(_logger, *, session):
        assert session is new_api_session
        calls.append("network_check")
        raise RuntimeError("startup failed")

    async def fail_if_bridge_initialized():
        raise AssertionError("network check 失败后不能初始化 Bridge")

    async def fake_close_push_session():
        calls.append("close_push_session")

    monkeypatch.setattr(
        bootstrap_lifespan,
        "init_new_api_session",
        fake_init_new_api_session,
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "shutdown_new_api_session",
        fake_shutdown_new_api_session,
    )
    monkeypatch.setattr(
        bootstrap_lifespan,
        "run_startup_network_check",
        fail_network_check,
    )
    monkeypatch.setattr(bootstrap_lifespan, "init_bridge", fail_if_bridge_initialized)
    monkeypatch.setattr(daily_digest, "close_push_session", fake_close_push_session)

    app = SimpleNamespace(state=SimpleNamespace())
    from core.modules import CompositionStartError

    with pytest.raises(
        CompositionStartError,
        match=r"runtime\.agent.*RuntimeError",
    ) as caught:
        async with bootstrap_lifespan.lifespan(app):
            raise AssertionError("启动失败时不能进入 lifespan body")

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert str(caught.value.__cause__) == "startup failed"
    assert calls == [
        "init_db",
        "start_sqlite_maintenance",
        "validate_sandbox",
        "provider_migration",
        "prompt_runtime",
        "start_schedulers",
        "init_new_api_session",
        "network_check",
        "shutdown_new_api_session",
        "stop_schedulers",
        "close_push_session",
        "stop_sqlite_maintenance:True",
    ]
    assert app.state.bridge is None
    assert app.state.new_api_session is None
    assert app.state.composition_root is None


@pytest.mark.asyncio
async def test_startup_network_check_uses_resolved_routes_and_redacts_sensitive_logs(
    monkeypatch,
    caplog,
):
    import config
    from bootstrap import network_check
    from clients import classifier_client
    from core import model_route_health

    admin_secret = "startup-admin-secret"
    api_secret = "startup-api-secret"
    route_secret = "startup-route-secret"
    credential_url = (
        "https://startup-user:startup-password@model.test/v1?token=startup-query-secret"
    )
    routes = {
        route_key: {
            "route_key": route_key,
            "base_url": credential_url,
            "api_key": route_secret,
            "model": f"{route_key}-model",
            "provider_enabled": True,
        }
        for route_key in ("reply", "timing_gate", "sticker_describe")
    }
    resolve_calls = []
    probe_calls = []

    def fake_resolve(route_key):
        resolve_calls.append(route_key)
        return dict(routes[route_key])

    async def fake_probe(route, session):
        probe_calls.append((dict(route), session))
        return SimpleNamespace(
            status="ready",
            reachable=True,
            usable=True,
            status_code=200,
            latency_ms=2,
        )

    class FakePublicResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    class FakeSession:
        def get(self, _url, **_kwargs):
            return FakePublicResponse()

    session = FakeSession()
    monkeypatch.setattr(config, "NANOBOT_ADMIN_TOKEN", admin_secret)
    monkeypatch.setattr(config, "NANOBOT_API_TOKEN", api_secret)
    monkeypatch.setenv("NANOBOT_GIT_COMMIT", "image-commit")
    monkeypatch.setenv("NANOBOT_GIT_BRANCH", "image-branch")
    monkeypatch.setenv("NANOBOT_GIT_COMMIT_DATE", "2026-07-14T00:00:00Z")
    monkeypatch.setenv("NANOBOT_GIT_DIRTY", "false")
    monkeypatch.setattr(classifier_client, "resolve_model_route", fake_resolve)
    monkeypatch.setattr(model_route_health, "probe_model_route", fake_probe)

    logger = logging.getLogger("nanobot.startup.test")
    with caplog.at_level(logging.INFO, logger=logger.name):
        await network_check.run_startup_network_check(logger, session=session)

    assert resolve_calls == ["reply", "timing_gate", "sticker_describe"]
    assert [route for route, _session in probe_calls] == [
        routes["reply"],
        routes["timing_gate"],
        routes["sticker_describe"],
    ]
    assert all(probe_session is session for _route, probe_session in probe_calls)
    assert "server version=image-commit" in caplog.text
    assert "configured=true" in caplog.text
    for secret in (
        admin_secret,
        api_secret,
        route_secret,
        "startup-user",
        "startup-password",
        "startup-query-secret",
    ):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_startup_route_resolution_failure_is_nonfatal_and_redacted(
    monkeypatch,
    caplog,
):
    from bootstrap import network_check
    from clients import classifier_client

    secret = "route-resolution-secret"

    def broken_resolve(route_key):
        if route_key == "timing_gate":
            raise RuntimeError(secret)
        return {
            "route_key": route_key,
            "base_url": "",
            "provider_enabled": True,
        }

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    class FakeSession:
        def get(self, _url, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(classifier_client, "resolve_model_route", broken_resolve)
    logger = logging.getLogger("nanobot.startup.resolve-failure")

    with caplog.at_level(logging.INFO, logger=logger.name):
        await network_check.run_startup_network_check(logger, session=FakeSession())

    assert secret not in caplog.text
    assert "route=timing_gate status=network_error" in caplog.text


def test_cors_default_allows_any_origin():
    import server

    with open_test_client_without_lifespan(server.app) as client:
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
    with open_test_client_without_lifespan(reloaded.app) as client:
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


def test_start_schedulers_starts_embedded_session_summary_worker(monkeypatch):
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

    monkeypatch.setenv("NANOBOT_SESSION_SUMMARY_WORKER_MODE", "embedded")
    monkeypatch.setattr(schedulers, "_start_thread", fake_start_thread)
    monkeypatch.setattr(schedulers, "_preload_sentinel", lambda logger: None)

    handles = schedulers.start_schedulers(testing=False, logger=Logger())

    assert "session-summary-worker" in calls
    assert handles.session_summary is not None


def test_start_schedulers_never_restores_retired_expression_writer(monkeypatch):
    """阶段 7B 启动面不得重新创建旧表达学习线程或 handle。"""
    import bootstrap.schedulers as schedulers

    calls: list[str] = []

    class Logger:
        def info(self, *_args, **_kwargs):
            pass

        def warning(self, *_args, **_kwargs):
            pass

    class Handle:
        stop_timeout = 5.0

        def stop(self):
            pass

    def fake_start_thread(*, name, target):
        del target
        calls.append(name)
        return Handle()

    monkeypatch.setenv(
        "NANOBOT_SESSION_SUMMARY_WORKER_MODE",
        "disabled",
    )
    monkeypatch.setattr(schedulers, "_start_thread", fake_start_thread)
    monkeypatch.setattr(
        schedulers,
        "_preload_sentinel",
        lambda _logger: None,
    )

    handles = schedulers.start_schedulers(
        testing=False,
        logger=Logger(),
    )

    assert "expression-learner" not in calls
    assert not hasattr(handles, "expression_learner")


def test_start_schedulers_wires_group_learning_without_implicit_whitelist(
    monkeypatch,
):
    import bootstrap.schedulers as schedulers

    calls: list[tuple[str, object]] = []

    class Logger:
        def info(self, *_args, **_kwargs):
            pass

        def warning(self, *_args, **_kwargs):
            pass

    class Handle:
        stop_timeout = 5.0

        def stop(self):
            pass

    def fake_start_thread(*, name, target):
        calls.append((name, target))
        return Handle()

    monkeypatch.setenv(
        "NANOBOT_SESSION_SUMMARY_WORKER_MODE",
        "disabled",
    )
    monkeypatch.setattr(schedulers, "_start_thread", fake_start_thread)
    monkeypatch.setattr(
        schedulers,
        "_preload_sentinel",
        lambda _logger: None,
    )

    handles = schedulers.start_schedulers(
        testing=False,
        logger=Logger(),
    )

    assert [name for name, _target in calls].count(
        "group-learning-scheduler"
    ) == 1
    target = next(
        target
        for name, target in calls
        if name == "group-learning-scheduler"
    )
    assert target is schedulers.group_learning_worker_scheduler
    assert handles.group_learning is not None


@pytest.mark.parametrize("mode", ["external", "disabled"])
def test_start_schedulers_skips_embedded_session_summary_worker(monkeypatch, mode):
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

    monkeypatch.setenv("NANOBOT_SESSION_SUMMARY_WORKER_MODE", mode)
    monkeypatch.setattr(schedulers, "_start_thread", fake_start_thread)
    monkeypatch.setattr(schedulers, "_preload_sentinel", lambda logger: None)

    handles = schedulers.start_schedulers(testing=False, logger=Logger())

    assert "session-summary-worker" not in calls
    assert handles.session_summary is None


def test_session_summary_worker_mode_rejects_unknown_value(monkeypatch):
    import config

    monkeypatch.setenv("NANOBOT_SESSION_SUMMARY_WORKER_MODE", "sidecar")
    resolver = getattr(config, "get_session_summary_worker_mode", None)

    assert resolver is not None
    with pytest.raises(ValueError, match="NANOBOT_SESSION_SUMMARY_WORKER_MODE"):
        resolver()


def test_invalid_session_summary_worker_mode_starts_no_threads(monkeypatch):
    import bootstrap.schedulers as schedulers

    calls: list[str] = []

    class Logger:
        def info(self, *_args, **_kwargs):
            pass

        def warning(self, *_args, **_kwargs):
            pass

    monkeypatch.setenv("NANOBOT_SESSION_SUMMARY_WORKER_MODE", "sidecar")
    monkeypatch.setattr(
        schedulers,
        "_start_thread",
        lambda **kwargs: calls.append(kwargs["name"]),
    )
    monkeypatch.setattr(schedulers, "_preload_sentinel", lambda logger: None)

    with pytest.raises(ValueError, match="NANOBOT_SESSION_SUMMARY_WORKER_MODE"):
        schedulers.start_schedulers(testing=False, logger=Logger())

    assert calls == []


def test_start_schedulers_keeps_memory_digest_thread_for_hot_reload(monkeypatch):
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

    monkeypatch.setattr(config, "DAILY_DIGEST_ENABLED", False, raising=False)
    monkeypatch.setenv("NANOBOT_SESSION_SUMMARY_WORKER_MODE", "disabled")
    monkeypatch.setattr(schedulers, "_start_thread", fake_start_thread)
    monkeypatch.setattr(schedulers, "_preload_sentinel", lambda logger: None)

    schedulers.start_schedulers(testing=False, logger=Logger())

    assert "daily-digest-scheduler" in calls


def test_start_schedulers_starts_chat_delivery_worker(monkeypatch):
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

    monkeypatch.setattr(schedulers, "_start_thread", fake_start_thread)
    monkeypatch.setattr(schedulers, "_preload_sentinel", lambda logger: None)

    handles = schedulers.start_schedulers(testing=False, logger=Logger())

    assert "chat-delivery-worker" in calls
    assert handles.chat_delivery is not None


def test_scheduler_handles_stop_chat_delivery_worker():
    import bootstrap.schedulers as schedulers

    calls = []

    class Handle:
        def stop(self):
            calls.append("chat_delivery")

    handles = schedulers.SchedulerHandles(chat_delivery=Handle())
    handles.stop_all()

    assert calls == ["chat_delivery"]


def test_start_schedulers_testing_mode_skips_chat_delivery_worker():
    import bootstrap.schedulers as schedulers

    class Logger:
        def info(self, *_args, **_kwargs):
            pass

    handles = schedulers.start_schedulers(testing=True, logger=Logger())

    assert handles.chat_delivery is None


def test_start_schedulers_keeps_proactive_recovery_when_generation_disabled(
    monkeypatch,
):
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

    monkeypatch.setattr(schedulers, "_start_thread", fake_start_thread)
    monkeypatch.setattr(schedulers, "_preload_sentinel", lambda logger: None)
    monkeypatch.setattr(settings, "get_bool", fake_get_bool)

    handles = schedulers.start_schedulers(testing=False, logger=Logger())

    assert "proactive-outreach-scheduler" in calls
    assert handles.proactive_outreach is not None


def test_start_schedulers_starts_proactive_outreach_when_enabled(monkeypatch):
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
        assert app.state.composition_root is not None

    assert calls == [
        "init_db",
        "provider_migration",
        "prompt_runtime",
        "start_schedulers_testing",
        "init_new_api_session",
        "legacy_memory",
        "inside",
        "shutdown_new_api_session",
        "stop_schedulers",
    ]
    assert app.state.bridge is None
    assert app.state.new_api_session is None
    assert app.state.composition_root is None
