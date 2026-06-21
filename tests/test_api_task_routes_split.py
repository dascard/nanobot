from __future__ import annotations

import inspect
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient


_TASK_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/tasks"),
    ("GET", "/api/v1/tasks"),
    ("PUT", "/api/v1/tasks/{task_id}"),
    ("POST", "/api/v1/tasks/{task_id}/toggle"),
    ("POST", "/api/v1/tasks/{task_id}/run"),
    ("DELETE", "/api/v1/tasks/{task_id}"),
)

_TASK_ROUTE_EXPORTS = (
    "ScheduledTaskCreate",
    "create_scheduled_task",
    "list_scheduled_tasks",
    "update_scheduled_task",
    "toggle_scheduled_task",
    "run_scheduled_task_now",
    "delete_scheduled_task",
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


def test_api_verify_token_is_shared_common_auth_object():
    from api import common_auth
    from api import routes

    assert routes.verify_token is common_auth.verify_token


def test_api_common_auth_uses_legacy_api_routes_token_monkeypatch(monkeypatch):
    from api import common_auth

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    assert common_auth.verify_token(authorization="Bearer split-token") is None
    for header in ("", "Bearer wrong"):
        try:
            common_auth.verify_token(authorization=header)
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("expected HTTPException")


def test_api_task_routes_are_registered_from_split_module():
    for method, path in _TASK_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.task_routes"}


def test_legacy_api_routes_task_imports_still_work():
    from api import routes
    from api import task_routes

    for name in _TASK_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(task_routes, name)

    body = routes.ScheduledTaskCreate(
        name="测试任务",
        target_id="u1",
        prompt_template="提醒我喝水",
    )
    assert body.cron_expr == "0 9 * * *"
    assert body.target_type == "private"


def test_split_task_routes_use_legacy_api_token_monkeypatch(db_session, monkeypatch):
    from core.database import get_db
    from server import app

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")
    try:
        with TestClient(app) as test_client:
            ok = test_client.get(
                "/api/v1/tasks",
                headers={"Authorization": "Bearer split-token"},
            )
            wrong = test_client.get(
                "/api/v1/tasks",
                headers={"Authorization": "Bearer wrong"},
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_task_routes_are_not_registered_twice():
    for method, path in _TASK_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_task_collection_routes_precede_dynamic_task_routes():
    ordered = [(path, route) for path, route in _api_route_entries()]
    collection_indexes = [
        idx
        for idx, (path, route) in enumerate(ordered)
        if path == "/api/v1/tasks" and {"GET", "POST"} & getattr(route, "methods", set())
    ]
    dynamic_indexes = [
        idx
        for idx, (path, route) in enumerate(ordered)
        if path.startswith("/api/v1/tasks/{task_id}")
    ]

    assert collection_indexes
    assert dynamic_indexes
    assert max(collection_indexes) < min(dynamic_indexes)


def test_api_task_async_boundaries_remain_coroutines():
    from api import routes
    from api import task_routes

    assert inspect.iscoroutinefunction(task_routes.run_scheduled_task_now)
    assert inspect.iscoroutinefunction(routes.run_scheduled_task_now)


def test_api_task_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/task_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_health_check_stays_in_parent_routes():
    routes = _api_routes_for("/api/v1/health", "GET")

    assert routes
    assert {route.endpoint.__module__ for route in routes} == {"api.routes"}
