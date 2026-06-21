from __future__ import annotations

from pathlib import Path


_ADMIN_MODEL_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/models/status"),
    ("POST", "/api/v1/admin/models/chat-test"),
    ("GET", "/api/v1/admin/model-catalog"),
    ("PATCH", "/api/v1/admin/model-catalog/{model_id}"),
    ("GET", "/api/v1/admin/models/providers"),
    ("PUT", "/api/v1/admin/models/providers/{provider_id}"),
    ("GET", "/api/v1/admin/models/catalog"),
    ("GET", "/api/v1/admin/models/route-references"),
    ("POST", "/api/v1/admin/models/catalog/refresh"),
    ("GET", "/api/v1/admin/model-routes"),
    ("PATCH", "/api/v1/admin/model-routes/{stage}"),
    ("PUT", "/api/v1/admin/models/routes/{route_key}"),
    ("POST", "/api/v1/admin/models/routes/{route_key}/test"),
    ("GET", "/api/v1/admin/models/routes/{route_key}/resolved"),
    ("GET", "/api/v1/admin/models/available"),
    ("POST", "/api/v1/admin/models/local/{component}/test"),
    ("POST", "/api/v1/admin/models/local/{component}/warmup"),
    ("POST", "/api/v1/admin/models/timing-gate-stability-test"),
    ("POST", "/api/v1/admin/models/health-check"),
)


_MODEL_ROUTE_EXPORTS = (
    "ChatModelTestRequest",
    "ProviderUpdateBody",
    "ModelCatalogPatch",
    "ModelRoutePatch",
    "ModelRouteEditBody",
    "TimingGateStabilityRequest",
    "_ALLOWED_TIERS",
    "_STAGE_META",
    "_ROUTE_SETTING_MAP",
    "_CLASSIFIER_ROUTE_KEYS",
    "_ROUTE_ALIAS",
    "_CHAT_ROUTES",
    "_TINY_TEST_PNG",
    "_resolve_route_value",
    "_resolve_route_key",
    "_redact",
    "_test_nli_contradiction",
    "models_status",
    "chat_model_test",
    "get_model_catalog",
    "patch_model_catalog",
    "list_model_providers",
    "update_model_provider",
    "get_model_catalog_v2",
    "get_route_references",
    "refresh_model_catalog",
    "get_model_routes",
    "patch_model_route",
    "edit_model_route",
    "test_model_route",
    "get_resolved_route",
    "list_available_models",
    "test_local_component",
    "warmup_local_component",
    "timing_gate_stability_test",
    "model_health_check",
)


def _admin_route_entries():
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


def _admin_routes_for(path: str, method: str | None = None):
    return [
        route
        for route_path, route in _admin_route_entries()
        if route_path == path and (method is None or method in getattr(route, "methods", set()))
    ]


def test_admin_model_routes_are_registered_from_split_module():
    for method, path in _ADMIN_MODEL_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.model_routes"}


def test_model_replies_stays_in_parent_admin_routes():
    routes = _admin_routes_for("/api/v1/admin/model-replies", "GET")

    assert routes
    assert {route.endpoint.__module__ for route in routes} == {"api.admin_routes"}


def test_legacy_admin_routes_model_imports_still_work():
    from api import admin_routes
    from api.admin import model_routes

    for name in _MODEL_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(model_routes, name)

    assert admin_routes.ChatModelTestRequest(model="x").model == "x"
    assert admin_routes.ProviderUpdateBody(enabled=True).enabled is True
    assert admin_routes._resolve_route_key("vision")[1] == "sticker_describe"
    assert admin_routes._redact({"x.api_key": "secret", "x.model": "m"}) == {
        "x.api_key": "***",
        "x.model": "m",
    }


def test_split_model_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/model-catalog",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/model-catalog",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_model_routes_are_not_registered_twice():
    for method, path in _ADMIN_MODEL_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_model_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    path = Path("api/admin/model_routes.py")

    assert path.exists()
    source = path.read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
