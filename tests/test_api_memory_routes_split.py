from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


_MEMORY_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/memory/digests"),
    ("POST", "/api/v1/memory/digests/run"),
    ("GET", "/api/v1/memory/recall"),
)

_PARENT_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/evolution/trigger"),
    ("GET", "/api/v1/models/list"),
    ("POST", "/api/v1/models/sync"),
    ("GET", "/api/v1/health"),
)

_MEMORY_ROUTE_EXPORTS = (
    "MemoryDigestRunRequest",
    "_validate_memory_digest_date_filters",
    "_short_text",
    "_calc_recall_confidence",
    "_build_expand_chain",
    "get_memory_digests",
    "run_memory_digests",
    "recall_memory",
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


def test_api_memory_routes_are_registered_from_split_module():
    for method, path in _MEMORY_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.memory_routes"}


def test_legacy_api_routes_memory_imports_still_work():
    from api import memory_routes
    from api import routes

    for name in _MEMORY_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(memory_routes, name)

    body = routes.MemoryDigestRunRequest(target_date="2026-06-20", user_id="u1", force=True)
    assert body.target_date == "2026-06-20"
    assert body.user_id == "u1"
    assert body.force is True


def test_split_memory_routes_use_legacy_api_token_monkeypatch(db_session, monkeypatch):
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
                "/api/v1/memory/digests",
                headers={"Authorization": "Bearer split-token"},
            )
            wrong = test_client.get(
                "/api/v1/memory/digests",
                headers={"Authorization": "Bearer wrong"},
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_memory_routes_are_not_registered_twice():
    for method, path in _MEMORY_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_memory_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/memory_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_non_memory_tail_routes_stay_in_parent_routes():
    for method, path in _PARENT_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.routes"}


def test_safe_meta_stays_in_parent_routes():
    from api import routes

    assert routes._safe_meta.__module__ == "api.routes"
