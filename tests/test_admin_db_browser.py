from __future__ import annotations

from sqlalchemy import text

from core.registry import RegistrySnapshot


def _auth_header():
    return {"Authorization": "Bearer test-token"}


def _admin_routes_for(path: str):
    from api.admin_routes import router

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
            yield from _iter_routes(
                original_router.routes,
                prefix + include_prefix,
            )

    return [
        route
        for route_path, route in _iter_routes(router.routes)
        if route_path == path
    ]


def _query_view(
    client,
    view_id: str,
    *,
    filters: dict[str, object] | None = None,
    cursor: str | None = None,
    limit: int = 50,
):
    return client.post(
        f"/api/v1/admin/db/views/{view_id}/rows",
        headers=_auth_header(),
        json={
            "filters": filters or {},
            "cursor": cursor,
            "limit": limit,
        },
    )


def test_db_browser_routes_are_registered_from_split_module():
    expected = {
        "/api/v1/admin/db/views",
        "/api/v1/admin/db/views/{view_id}/rows",
    }

    for path in expected:
        routes = _admin_routes_for(path)
        assert routes, f"missing route: {path}"
        assert {route.endpoint.__module__ for route in routes} == {
            "api.admin.db_browser_routes"
        }
    assert not _admin_routes_for("/api/v1/admin/db/query")


def test_legacy_admin_routes_exports_only_structured_db_browser_contracts():
    from api import admin_routes
    from api.admin import db_browser_routes

    names = [
        "AdminTableViewQuery",
        "list_views",
        "query_view_rows",
    ]
    for name in names:
        assert getattr(admin_routes, name) is getattr(db_browser_routes, name)

    retired_names = [
        "DbQuery",
        "_extract_query_table_names",
        "_validate_query_tables_allowed",
        "_validate_readonly_query",
        "execute_readonly_query",
    ]
    for name in retired_names:
        assert not hasattr(admin_routes, name)
        assert not hasattr(db_browser_routes, name)


def test_split_db_browser_uses_legacy_admin_token_monkeypatch(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "split-token",
    )

    ok = client.get(
        "/api/v1/admin/db/views",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/db/views",
        headers=_auth_header(),
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_db_browser_routes_are_not_registered_twice():
    expected = {
        "/api/v1/admin/db/views",
        "/api/v1/admin/db/views/{view_id}/rows",
    }

    for path in expected:
        assert len(_admin_routes_for(path)) == 1


def test_db_views_returns_registry_snapshot_and_hides_sensitive_table(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )

    response = client.get(
        "/api/v1/admin/db/views",
        headers=_auth_header(),
    )

    assert response.status_code == 200
    payload = response.json()
    view_ids = {item["view_id"] for item in payload["views"]}
    assert "semantic_index_items" in view_ids
    assert "rolling_session_summaries" in view_ids
    assert "content_block_rules" in view_ids
    assert "sensitive_data" not in view_ids
    assert payload["registry"]["namespace"] == "admin_table_view"
    assert payload["registry"]["generation"] == 1
    assert len(payload["registry"]["sha256"]) == 64
    assert payload["groups"]

    users = next(
        item for item in payload["views"] if item["view_id"] == "users"
    )
    assert users["owner"] == "core.chat"
    assert users["default_sort"]["column"] == "id"
    assert users["default_sort"]["direction"] == "desc"
    assert users["max_limit"] == 200
    assert users["lifecycle"] == "active"
    assert any(item["filter_id"] == "id" for item in users["filters"])


def test_admin_table_view_registry_uses_shared_kernel():
    from core.admin.table_views import ADMIN_TABLE_VIEW_REGISTRY

    assert isinstance(ADMIN_TABLE_VIEW_REGISTRY, RegistrySnapshot)
    assert ADMIN_TABLE_VIEW_REGISTRY.namespace == "admin_table_view"
    assert ADMIN_TABLE_VIEW_REGISTRY.generation == 1
    assert ADMIN_TABLE_VIEW_REGISTRY.require("users").table_name == "users"


def test_db_view_rejects_unknown_or_sensitive_view_with_stable_code(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )

    response = _query_view(client, "sensitive_data")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "admin_view_not_found"


def test_retired_db_query_route_is_absent(client, monkeypatch):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )

    response = client.post(
        "/api/v1/admin/db/query",
        headers=_auth_header(),
        json={"query": "SELECT * FROM users"},
    )

    # Admin router 下仍有 /db/backup 等固定方法；Starlette 对同前缀的
    # 未登记 POST 可能返回 404 或 405，两者都证明任意 SQL endpoint 不存在。
    assert response.status_code in {404, 405}


def test_db_view_filter_returns_only_registered_matches(
    client,
    monkeypatch,
    db_session,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )
    db_session.execute(
        text(
            "INSERT INTO content_block_rules(pattern, category, updated_at) "
            "VALUES ('x', 'target', '2026-05-27 12:00:00')"
        )
    )
    db_session.execute(
        text(
            "INSERT INTO content_block_rules(pattern, category, updated_at) "
            "VALUES ('y', 'other', '2026-05-28 12:00:00')"
        )
    )
    db_session.commit()

    response = _query_view(
        client,
        "content_block_rules",
        filters={"category": "target"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["rows"]
    assert {row["category"] for row in payload["rows"]} == {"target"}
    assert payload["rows"][0]["updated_at"]


def test_db_view_rejects_unknown_filter_with_stable_code(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )

    response = _query_view(
        client,
        "users",
        filters={"name LIKE": "%admin%"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "admin_view_filter_invalid"


def test_db_view_request_rejects_sql_and_sort_fragments(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )

    response = client.post(
        "/api/v1/admin/db/views/users/rows",
        headers=_auth_header(),
        json={
            "filters": {},
            "cursor": None,
            "limit": 10,
            "query": "SELECT * FROM sensitive_data",
            "order_by": "id; DROP TABLE users",
        },
    )

    assert response.status_code == 422


def test_db_view_serializes_binary_and_long_text_with_metadata(
    client,
    monkeypatch,
    db_session,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )
    long_content = "长文本" * 600
    db_session.execute(
        text(
            "INSERT INTO persona_facts"
            "(user_id, content, embedding, cluster_centroid) "
            "VALUES ('u1', :content, :embedding, :centroid)"
        ),
        {
            "content": long_content,
            "embedding": b"\x01\x02\x03",
            "centroid": b"\x04\x05",
        },
    )
    db_session.commit()

    response = _query_view(client, "persona_facts", limit=5)

    assert response.status_code == 200, response.text
    payload = response.json()
    row = payload["rows"][0]
    meta = payload["cell_meta"][0]
    assert row["embedding"] == "<binary 3 bytes>"
    assert meta["embedding"]["kind"] == "binary"
    assert row["cluster_centroid"] == "<binary 2 bytes>"
    assert meta["content"]["truncated"] is True
    assert meta["content"]["full_length"] == len(long_content)
    assert len(row["content"]) < len(long_content)


def test_db_view_uses_same_safe_serializer_for_behavior_rows(
    client,
    monkeypatch,
    db_session,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )
    long_content = "结构化预览" * 500
    db_session.execute(
        text(
            "INSERT INTO persona_behaviors"
            "(user_id, pattern, embedding) "
            "VALUES ('u1', :pattern, :embedding)"
        ),
        {
            "pattern": long_content,
            "embedding": b"\x09\x08\x07\x06",
        },
    )
    db_session.commit()

    response = _query_view(client, "persona_behaviors")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["rows"][0]["embedding"] == "<binary 4 bytes>"
    assert payload["cell_meta"][0]["embedding"]["kind"] == "binary"
    assert payload["cell_meta"][0]["pattern"]["truncated"] is True


def test_db_view_returns_safe_internal_error(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )

    def fail_query(*_args, **_kwargs):
        raise RuntimeError("SELECT secret FROM hidden_table")

    monkeypatch.setattr(
        "api.admin.db_browser_routes.TABLE_VIEW_SERVICE.query",
        fail_query,
    )

    response = _query_view(client, "users")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "admin_view_internal_error"
    assert "secret" not in detail["message"]
    assert "SELECT" not in detail["message"].upper()


def test_db_view_uses_opaque_cursor_pagination(
    client,
    monkeypatch,
    db_session,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )
    for user_id in ("cursor-a", "cursor-b", "cursor-c"):
        db_session.execute(
            text(
                "INSERT INTO users(id, name) VALUES (:id, :name)"
            ),
            {"id": user_id, "name": user_id},
        )
    db_session.commit()

    first = _query_view(client, "users", limit=2)
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["limit"] == 2
    assert first_payload["has_next"] is True
    assert first_payload["next_cursor"]
    assert "SELECT" not in first_payload["next_cursor"]

    second = _query_view(
        client,
        "users",
        cursor=first_payload["next_cursor"],
        limit=2,
    )
    assert second.status_code == 200, second.text
    first_ids = {row["id"] for row in first_payload["rows"]}
    second_ids = {row["id"] for row in second.json()["rows"]}
    assert first_ids.isdisjoint(second_ids)


def test_db_view_rejects_cursor_reuse_with_other_filter(
    client,
    monkeypatch,
    db_session,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )
    for user_id in ("filter-a", "filter-b"):
        db_session.execute(
            text("INSERT INTO users(id, name) VALUES (:id, 'same')"),
            {"id": user_id},
        )
    db_session.commit()

    first = _query_view(
        client,
        "users",
        filters={"name": "same"},
        limit=1,
    )
    assert first.status_code == 200, first.text
    cursor = first.json()["next_cursor"]
    assert cursor

    response = _query_view(
        client,
        "users",
        filters={"name": "changed"},
        cursor=cursor,
        limit=1,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "admin_view_cursor_invalid"


def test_db_view_rejects_limit_above_descriptor_max(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )

    response = _query_view(client, "users", limit=201)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "admin_view_limit_invalid"


def test_llm_log_sensitive_columns_are_hidden(
    client,
    monkeypatch,
    db_session,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "test-token",
    )
    db_session.execute(
        text(
            "INSERT INTO llm_api_request_logs"
            "(trace_id, request_json, response_json, headers_json, "
            "request_preview) "
            "VALUES ('trace-db', :request_json, :response_json, "
            ":headers_json, 'preview ok')"
        ),
        {
            "request_json": (
                '{"messages":[{"content":"完整请求"}]}'
            ),
            "response_json": (
                '{"choices":[{"message":{"content":"完整响应"}}]}'
            ),
            "headers_json": '{"Authorization":"Bearer secret"}',
        },
    )
    db_session.commit()

    response = _query_view(client, "llm_api_request_logs", limit=5)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "headers_json" not in payload["columns"]
    assert "request_json" not in payload["columns"]
    assert "response_json" not in payload["columns"]
    assert payload["rows"][0]["request_preview"] == "preview ok"
