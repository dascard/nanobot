from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


_MODEL_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/models/list"),
    ("POST", "/api/v1/models/sync"),
)

_PARENT_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/evolution/trigger"),
    ("GET", "/api/v1/health"),
)

_MODEL_ROUTE_EXPORTS = (
    "ModelSyncRequest",
    "list_models",
    "sync_models",
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


def test_api_model_routes_are_registered_from_split_module():
    for method, path in _MODEL_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.model_routes"}


def test_legacy_api_routes_model_imports_still_work():
    from api import model_routes
    from api import routes

    for name in _MODEL_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(model_routes, name)

    body = routes.ModelSyncRequest(force=False)
    assert body.force is False


def test_split_model_routes_use_legacy_api_token_monkeypatch(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")
    with TestClient(app) as test_client:
        ok = test_client.get(
            "/api/v1/models/list",
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.get(
            "/api/v1/models/list",
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_model_routes_are_not_registered_twice():
    for method, path in _MODEL_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_model_async_boundaries_remain_coroutines():
    from api import model_routes
    from api import routes

    assert inspect.iscoroutinefunction(model_routes.sync_models)
    assert inspect.iscoroutinefunction(routes.sync_models)


def test_api_model_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/model_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_model_list_filters_provider_and_tier(client, monkeypatch):
    from api import model_routes

    class FakeRegistry:
        data = {"last_updated": "2026-06-21T00:00:00"}

        def get_models_by_provider(self, provider):
            assert provider == "new-api"
            return [
                {"id": "fast-model", "tier": "fast"},
                {"id": "smart-model", "tier": "smart"},
                {"id": "missing-tier"},
            ]

    monkeypatch.setattr(model_routes, "registry", FakeRegistry())

    response = client.get("/api/v1/models/list?provider=new-api&tier=fast")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["provider"] == "new-api"
    assert data["count"] == 1
    assert data["last_updated"] == "2026-06-21T00:00:00"
    assert data["models"] == [{"id": "fast-model", "tier": "fast"}]


def test_model_sync_rejects_missing_api_key(client, monkeypatch):
    monkeypatch.setattr("config.NEW_API_KEY", "")

    response = client.post("/api/v1/models/sync", json={"force": False})

    assert response.status_code == 400
    assert response.json()["detail"] == "NEW_API_KEY is missing"


def test_model_sync_uses_force_and_returns_updated_count(client, monkeypatch):
    from api import model_routes

    calls = []

    class FakeNewAPIClient:
        def __init__(self, *, api_key, base_url):
            calls.append(("init", api_key, base_url))

        async def sync_models_to_registry(self, *, force):
            calls.append(("sync", force))
            return 7

    monkeypatch.setattr("config.NEW_API_KEY", "test-key")
    monkeypatch.setattr("config.NEW_API_BASE_URL", "http://new-api")
    monkeypatch.setattr(model_routes, "NewAPIClient", FakeNewAPIClient)
    monkeypatch.setattr(
        model_routes,
        "registry",
        SimpleNamespace(data={"last_updated": "2026-06-21T01:02:03"}),
    )

    response = client.post("/api/v1/models/sync", json={"force": False})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "updated": 7,
        "last_updated": "2026-06-21T01:02:03",
    }
    assert calls == [("init", "test-key", "http://new-api"), ("sync", False)]


def test_non_model_tail_routes_stay_in_parent_routes():
    for method, path in _PARENT_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.routes"}
