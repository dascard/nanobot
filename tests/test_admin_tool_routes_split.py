from __future__ import annotations

from pathlib import Path


_ADMIN_TOOL_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/tools"),
    ("GET", "/api/v1/admin/tools/targets"),
    ("GET", "/api/v1/admin/tools/effective"),
    ("GET", "/api/v1/admin/tools/decisions"),
    ("GET", "/api/v1/admin/tools/{tool_name}/schema"),
    ("PUT", "/api/v1/admin/tools/{tool_name}/schema"),
    ("DELETE", "/api/v1/admin/tools/{tool_name}/schema"),
    ("PUT", "/api/v1/admin/tools/{tool_name}"),
    ("PUT", "/api/v1/admin/tools/{tool_name}/override"),
    ("DELETE", "/api/v1/admin/tools/{tool_name}/override"),
)


_TOOL_ROUTE_EXPORTS = (
    "ToolUpdateBody",
    "ToolOverrideBody",
    "ToolSchemaOverrideBody",
    "_TEMP_TOOL_TARGET_EXACT",
    "_TEMP_TOOL_TARGET_PREFIXES",
    "_is_temp_tool_target_id",
    "_tool_target_label",
    "list_tools",
    "list_tool_targets",
    "get_tool_schema_override",
    "save_tool_schema_override_api",
    "delete_tool_schema_override_api",
    "update_tool_defaults",
    "set_tool_override",
    "delete_tool_override",
    "get_effective_tools",
    "list_runtime_preset_decisions",
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


def test_admin_tool_routes_are_registered_from_split_module():
    for method, path in _ADMIN_TOOL_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.tool_routes"}


def test_legacy_admin_routes_tool_imports_still_work():
    from api import admin_routes
    from api.admin import tool_routes

    for name in _TOOL_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(tool_routes, name)

    assert admin_routes.ToolUpdateBody(group_default=True).group_default is True
    assert admin_routes._is_temp_tool_target_id("private_test")
    assert admin_routes._tool_target_label("测试群", "123", "群聊 123") == "测试群 (123)"


def test_split_tool_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/tools/effective",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/tools/effective",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_tool_routes_are_not_registered_twice():
    for method, path in _ADMIN_TOOL_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_tool_static_routes_before_dynamic_tool_name_routes():
    route_paths = [path for path, _route in _admin_route_entries()]

    targets_index = route_paths.index("/api/v1/admin/tools/targets")
    effective_index = route_paths.index("/api/v1/admin/tools/effective")
    decisions_index = route_paths.index("/api/v1/admin/tools/decisions")
    dynamic_indices = [
        index
        for index, path in enumerate(route_paths)
        if path.startswith("/api/v1/admin/tools/{tool_name}")
    ]

    assert dynamic_indices
    first_dynamic_index = min(dynamic_indices)
    assert targets_index < first_dynamic_index
    assert effective_index < first_dynamic_index
    assert decisions_index < first_dynamic_index


def test_admin_tool_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    source = Path("api/admin/tool_routes.py").read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_generic_tool_routes_cannot_mutate_sandbox_authorization(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")
    headers = {"Authorization": "Bearer split-token"}

    default_update = client.put(
        "/api/v1/admin/tools/workspace_read",
        headers=headers,
        json={"private_default": True},
    )
    override_update = client.put(
        "/api/v1/admin/tools/workspace_read/override",
        headers=headers,
        json={
            "scope_type": "user",
            "scope_id": "10001",
            "enabled": True,
            "reason": "不得生效",
        },
    )
    override_delete = client.delete(
        "/api/v1/admin/tools/workspace_read/override",
        headers=headers,
        params={"scope_type": "user", "scope_id": "10001"},
    )

    assert default_update.status_code == 409
    assert override_update.status_code == 409
    assert override_delete.status_code == 409
