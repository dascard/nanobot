from __future__ import annotations


_GROUP_MEMORY_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/groups/{group_id:path}/memories"),
    ("GET", "/api/v1/admin/group-memories/overview"),
    ("GET", "/api/v1/admin/group-memories/{group_id:path}/items"),
    ("POST", "/api/v1/admin/group-memories/{group_id:path}/extract"),
    ("PUT", "/api/v1/admin/group-memories/{group_id:path}/injection-config"),
    ("POST", "/api/v1/admin/group-memories/{group_id:path}/injection-preview"),
    ("PATCH", "/api/v1/admin/group-memories/items/{memory_id}"),
    ("POST", "/api/v1/admin/groups/{group_id:path}/memories/extract"),
)


_GROUP_MEMORY_ROUTE_EXPORTS = (
    "GroupMemoryExtractRequest",
    "GroupMemoryInjectionConfigRequest",
    "GroupMemoryInjectionPreviewRequest",
    "GroupMemoryUpdateRequest",
    "_group_memory_row_dict",
    "_group_memories_payload",
    "_extract_group_memories_response",
    "group_memories_list",
    "group_memories_overview",
    "group_memory_items",
    "group_memory_extract_alias",
    "group_memory_injection_config",
    "group_memory_injection_preview",
    "group_memory_update_item",
    "group_memories_extract",
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


def test_admin_group_memory_routes_are_registered_from_split_module():
    for method, path in _GROUP_MEMORY_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.group_memory_routes"}


def test_legacy_admin_routes_group_memory_imports_still_work():
    from api import admin_routes
    from api.admin import group_memory_routes

    for name in _GROUP_MEMORY_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(group_memory_routes, name)

    assert admin_routes.GroupMemoryExtractRequest(window_hours=1).window_hours == 1


def test_split_group_memory_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/group-memories/overview",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/group-memories/overview",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_group_memory_routes_are_not_registered_twice():
    for method, path in _GROUP_MEMORY_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_group_memory_routes_are_registered_before_group_detail_catchall():
    route_paths = [path for path, _route in _admin_route_entries()]

    detail_index = route_paths.index("/api/v1/admin/groups/{group_id:path}")
    list_index = route_paths.index("/api/v1/admin/groups/{group_id:path}/memories")
    extract_index = route_paths.index("/api/v1/admin/groups/{group_id:path}/memories/extract")

    assert list_index < detail_index
    assert extract_index < detail_index
