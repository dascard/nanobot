from __future__ import annotations

from pathlib import Path


_ADMIN_CHAT_CONFIG_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/block-rules"),
    ("POST", "/api/v1/admin/block-rules"),
    ("PUT", "/api/v1/admin/block-rules/{rule_id}"),
    ("DELETE", "/api/v1/admin/block-rules/{rule_id}"),
    ("GET", "/api/v1/admin/content-block-rules"),
    ("POST", "/api/v1/admin/content-block-rules"),
    ("PUT", "/api/v1/admin/content-block-rules/{rule_id}"),
    ("DELETE", "/api/v1/admin/content-block-rules/{rule_id}"),
    ("POST", "/api/v1/admin/content-block-rules/{rule_id}/toggle"),
    ("POST", "/api/v1/admin/block-rules/test"),
    ("GET", "/api/v1/admin/chat-streams"),
    ("GET", "/api/v1/admin/configs"),
    ("PUT", "/api/v1/admin/configs"),
    ("GET", "/api/v1/admin/configs/{chat_stream_id:path}"),
    ("PUT", "/api/v1/admin/configs/{chat_stream_id:path}"),
    ("DELETE", "/api/v1/admin/configs/{chat_stream_id:path}"),
)


_CHAT_CONFIG_ROUTE_EXPORTS = (
    "BlockRuleCreate",
    "BlockRuleUpdate",
    "ContentBlockRuleCreate",
    "ContentBlockRuleUpdate",
    "ContentBlockRuleTestRequest",
    "ConfigUpdate",
    "ConfigUpsert",
    "_block_dict",
    "_content_block_dict",
    "_config_dict",
    "_config_default",
    "_raw_group_id",
    "_group_stream_id",
    "list_block_rules",
    "create_block_rule",
    "update_block_rule",
    "delete_block_rule",
    "list_content_block_rules",
    "create_content_block_rule",
    "update_content_block_rule",
    "delete_content_block_rule",
    "toggle_content_block_rule",
    "test_block_rules",
    "list_chat_streams",
    "list_configs",
    "get_config",
    "upsert_config",
    "update_config",
    "delete_config",
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


def test_admin_chat_config_routes_are_registered_from_split_module():
    for method, path in _ADMIN_CHAT_CONFIG_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.chat_config_routes"}


def test_legacy_admin_routes_chat_config_imports_still_work():
    from api import admin_routes
    from api.admin import chat_config_routes

    for name in _CHAT_CONFIG_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(chat_config_routes, name)

    assert admin_routes.BlockRuleCreate(user_id="u1").user_id == "u1"
    assert admin_routes.ContentBlockRuleTestRequest(message="测试").message == "测试"
    assert admin_routes.ConfigUpdate(talk_value=0.7).talk_value == 0.7
    assert admin_routes.ConfigUpsert(
        platform="qq",
        chat_type="private",
        session_id="private_u1",
        session_guidance="简洁回答",
    ).session_guidance == "简洁回答"
    assert admin_routes._raw_group_id("group_123") == "123"
    assert admin_routes._group_stream_id("123") == "qq:123:group"


def test_split_chat_config_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/block-rules",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/block-rules",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_chat_config_routes_are_not_registered_twice():
    for method, path in _ADMIN_CHAT_CONFIG_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_chat_config_static_configs_route_precedes_dynamic_path_route():
    entries = _admin_route_entries()

    for method in ("GET", "PUT"):
        static_index = next(
            index
            for index, (path, route) in enumerate(entries)
            if path == "/api/v1/admin/configs"
            and method in getattr(route, "methods", set())
        )
        dynamic_index = next(
            index
            for index, (path, route) in enumerate(entries)
            if path == "/api/v1/admin/configs/{chat_stream_id:path}"
            and method in getattr(route, "methods", set())
        )

        assert static_index < dynamic_index


def test_admin_chat_config_block_rules_test_route_precedes_dynamic_rule_routes():
    route_paths = [path for path, _route in _admin_route_entries()]

    test_index = route_paths.index("/api/v1/admin/block-rules/test")
    dynamic_indices = [
        index
        for index, path in enumerate(route_paths)
        if path == "/api/v1/admin/block-rules/{rule_id}"
    ]

    assert dynamic_indices
    assert test_index < min(dynamic_indices)


def test_admin_chat_config_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    path = Path("api/admin/chat_config_routes.py")

    assert path.exists()
    source = path.read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
