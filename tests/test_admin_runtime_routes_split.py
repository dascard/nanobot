from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError


_ADMIN_RUNTIME_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/overview"),
    ("GET", "/api/v1/admin/groups"),
    ("GET", "/api/v1/admin/groups/{group_id:path}"),
    ("GET", "/api/v1/admin/timing-gate/events"),
    ("POST", "/api/v1/admin/timing-gate/test"),
)


_RUNTIME_ROUTE_EXPORTS = (
    "TimingGateTestRequest",
    "_timing_meta",
    "_timing_event_dict",
    "_timing_stats",
    "_runtime_snapshot",
    "overview",
    "list_groups",
    "group_detail",
    "timing_gate_events",
    "timing_gate_test",
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


def test_admin_runtime_routes_are_registered_from_split_module():
    for method, path in _ADMIN_RUNTIME_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.runtime_routes"}


def test_legacy_admin_routes_runtime_imports_still_work():
    from api import admin_routes
    from api.admin import runtime_routes

    for name in _RUNTIME_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(runtime_routes, name)

    assert admin_routes.TimingGateTestRequest(context="测试", repeats=1).repeats == 1


def test_split_runtime_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_runtime_routes_are_not_registered_twice():
    for method, path in _ADMIN_RUNTIME_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_group_memory_routes_still_precede_group_detail_catchall():
    route_paths = [path for path, _route in _admin_route_entries()]

    detail_index = route_paths.index("/api/v1/admin/groups/{group_id:path}")
    list_index = route_paths.index("/api/v1/admin/groups/{group_id:path}/memories")
    extract_index = route_paths.index("/api/v1/admin/groups/{group_id:path}/memories/extract")

    assert list_index < detail_index
    assert extract_index < detail_index


def test_admin_runtime_static_group_routes_precede_group_detail_catchall():
    route_paths = [path for path, _route in _admin_route_entries()]

    groups_index = route_paths.index("/api/v1/admin/groups")
    detail_index = route_paths.index("/api/v1/admin/groups/{group_id:path}")

    assert groups_index < detail_index


def test_admin_runtime_async_boundaries_remain_coroutines():
    from api.admin import runtime_routes

    assert inspect.iscoroutinefunction(runtime_routes.timing_gate_test)


def test_timing_gate_test_request_repeats_cap_is_preserved_via_split_import():
    from api.admin.runtime_routes import TimingGateTestRequest

    with pytest.raises(ValidationError):
        TimingGateTestRequest(context="测试", repeats=6)


def test_admin_runtime_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    path = Path("api/admin/runtime_routes.py")

    assert path.exists()
    source = path.read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
