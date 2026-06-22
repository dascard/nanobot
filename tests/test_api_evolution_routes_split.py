from __future__ import annotations

import inspect
from pathlib import Path

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient


_EVOLUTION_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/evolution/trigger"),
)

_EVOLUTION_ROUTE_EXPORTS = (
    "EvolutionTriggerRequest",
    "trigger_evolution",
)


def _api_route_entries():
    from server import app

    def _iter_routes(routes, prefix: str = ""):
        for route in routes:
            endpoint = getattr(route, "endpoint", None)
            route_path = getattr(route, "path", None)
            if endpoint is not None and route_path is not None:
                yield prefix + route_path, route
                continue

            original_router = getattr(route, "original_router", None)
            if original_router is None:
                continue
            include_context = getattr(route, "include_context", None)
            include_prefix = getattr(include_context, "prefix", "")
            yield from _iter_routes(original_router.routes, prefix + include_prefix)

    return list(_iter_routes(app.routes))


def _api_routes_for(path: str, method: str | None = None):
    return [
        route
        for route_path, route in _api_route_entries()
        if route_path == path and (method is None or method in getattr(route, "methods", set()))
    ]


def test_api_evolution_routes_are_registered_from_split_module():
    for method, path in _EVOLUTION_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.evolution_routes"}


def test_legacy_api_routes_evolution_imports_still_work():
    from api import evolution_routes
    from api import routes

    for name in _EVOLUTION_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(evolution_routes, name)

    body = routes.EvolutionTriggerRequest(user_id="u1")
    assert body.user_id == "u1"


def test_split_evolution_routes_use_legacy_api_token_monkeypatch(monkeypatch):
    from api import evolution_routes
    from server import app

    calls = []

    def fake_evolution_task(user_id: str):
        calls.append(user_id)

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")
    monkeypatch.setattr(evolution_routes, "evolution_task", fake_evolution_task)
    with TestClient(app) as test_client:
        ok = test_client.post(
            "/api/v1/evolution/trigger",
            json={"user_id": "u1"},
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.post(
            "/api/v1/evolution/trigger",
            json={"user_id": "u2"},
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert ok.json() == {
        "status": "ok",
        "message": "Evolution task queued for u1",
    }
    assert wrong.status_code == 401
    assert calls == ["u1"]


def test_api_evolution_routes_are_not_registered_twice():
    for method, path in _EVOLUTION_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_evolution_trigger_keeps_sync_background_boundary():
    from api import evolution_routes
    from api import routes

    assert not inspect.iscoroutinefunction(evolution_routes.trigger_evolution)
    assert not inspect.iscoroutinefunction(routes.trigger_evolution)

    background_tasks = BackgroundTasks()
    body = evolution_routes.EvolutionTriggerRequest(user_id="u1")
    response = evolution_routes.trigger_evolution(body, background_tasks)

    assert response == {
        "status": "ok",
        "message": "Evolution task queued for u1",
    }
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is evolution_routes.evolution_task
    assert task.args == ("u1",)
    assert task.kwargs == {}


def test_api_evolution_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/evolution_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_health_check_stays_in_parent_routes():
    routes = _api_routes_for("/api/v1/health", "GET")

    assert routes
    assert {route.endpoint.__module__ for route in routes} == {"api.routes"}
