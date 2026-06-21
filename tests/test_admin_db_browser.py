from sqlalchemy import text


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
            yield from _iter_routes(original_router.routes, prefix + include_prefix)

    return [
        route
        for route_path, route in _iter_routes(router.routes)
        if route_path == path
    ]


def test_db_browser_routes_are_registered_from_split_module():
    expected = {
        "/api/v1/admin/db/tables",
        "/api/v1/admin/db/tables/{table_name}",
        "/api/v1/admin/db/query",
    }

    for path in expected:
        routes = _admin_routes_for(path)
        assert routes, f"missing route: {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.db_browser_routes"}


def test_legacy_admin_routes_db_browser_imports_still_work():
    from api import admin_routes
    from api.admin import db_browser_routes

    names = [
        "DbQuery",
        "DB_TABLE_GROUPS",
        "READONLY_TABLES",
        "READONLY_TABLE_SET",
        "BLOCKED_DB_TABLES",
        "GLOBAL_REDACT_COLUMNS",
        "GLOBAL_PREVIEW_ONLY_COLUMNS",
        "DEFAULT_DB_TABLE_POLICY",
        "DB_TABLE_POLICIES",
        "_db_table_policy",
        "_db_table_meta",
        "_quote_identifier",
        "_table_columns",
        "_safe_serialize_cell",
        "_serialize_db_rows",
        "_extract_query_table_names",
        "_validate_query_tables_allowed",
        "_validate_readonly_query",
        "_available_readonly_tables",
        "_available_db_groups",
        "list_tables",
        "query_table",
        "execute_readonly_query",
    ]

    for name in names:
        assert getattr(admin_routes, name) is getattr(db_browser_routes, name)
    assert admin_routes.DbQuery(query="SELECT 1").query == "SELECT 1"


def test_split_db_browser_uses_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/db/tables",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/db/tables",
        headers=_auth_header(),
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_db_browser_routes_are_not_registered_twice():
    expected = {
        "/api/v1/admin/db/tables",
        "/api/v1/admin/db/tables/{table_name}",
        "/api/v1/admin/db/query",
    }

    for path in expected:
        assert len(_admin_routes_for(path)) == 1


def test_db_tables_returns_groups_meta_and_hides_sensitive_data(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.get("/api/v1/admin/db/tables", headers=_auth_header())

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["tables"], list)
    assert "groups" in payload
    assert "table_meta" in payload
    assert "semantic_index_items" in payload["tables"]
    assert "rolling_session_summaries" in payload["tables"]
    assert "content_block_rules" in payload["tables"]
    assert "sensitive_data" not in payload["tables"]


def test_db_table_rejects_sensitive_data(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.get("/api/v1/admin/db/tables/sensitive_data", headers=_auth_header())

    assert response.status_code == 400


def test_db_query_rejects_sensitive_and_sqlite_system_tables(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    sensitive = client.post(
        "/api/v1/admin/db/query",
        headers=_auth_header(),
        json={"query": "SELECT * FROM sensitive_data"},
    )
    sqlite_master = client.post(
        "/api/v1/admin/db/query",
        headers=_auth_header(),
        json={"query": "SELECT name FROM sqlite_master"},
    )

    assert sensitive.status_code == 400
    assert sqlite_master.status_code == 400


def test_db_query_rejects_non_whitelist_join_before_execution(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.post(
        "/api/v1/admin/db/query",
        headers=_auth_header(),
        json={"query": "SELECT * FROM users JOIN eval_candidates ON 1 = 1"},
    )

    assert response.status_code == 400
    assert "eval_candidates" in response.json()["detail"]


def test_db_query_keyword_guard_does_not_reject_updated_at(client, monkeypatch, db_session):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_session.execute(
        text("INSERT INTO content_block_rules(pattern, updated_at) VALUES ('x', '2026-05-27 12:00:00')")
    )
    db_session.commit()

    response = client.post(
        "/api/v1/admin/db/query",
        headers=_auth_header(),
        json={"query": "SELECT updated_at FROM content_block_rules"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["rows"][0]["updated_at"]


def test_db_table_serializes_binary_and_long_text_with_metadata(client, monkeypatch, db_session):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    long_content = "长文本" * 600
    db_session.execute(
        text(
            "INSERT INTO persona_facts(user_id, content, embedding, cluster_centroid) "
            "VALUES ('u1', :content, :embedding, :centroid)"
        ),
        {"content": long_content, "embedding": b"\x01\x02\x03", "centroid": b"\x04\x05"},
    )
    db_session.commit()

    response = client.get(
        "/api/v1/admin/db/tables/persona_facts",
        headers=_auth_header(),
        params={"limit": 5},
    )

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


def test_db_query_uses_safe_serializer(client, monkeypatch, db_session):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    long_content = "SQL预览" * 500
    db_session.execute(
        text("INSERT INTO persona_behaviors(user_id, pattern, embedding) VALUES ('u1', :pattern, :embedding)"),
        {"pattern": long_content, "embedding": b"\x09\x08\x07\x06"},
    )
    db_session.commit()

    response = client.post(
        "/api/v1/admin/db/query",
        headers=_auth_header(),
        json={"query": "SELECT pattern, embedding FROM persona_behaviors"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["rows"][0]["embedding"] == "<binary 4 bytes>"
    assert payload["cell_meta"][0]["embedding"]["kind"] == "binary"
    assert payload["cell_meta"][0]["pattern"]["truncated"] is True


def test_db_query_does_not_echo_internal_sql_errors(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.post(
        "/api/v1/admin/db/query",
        headers=_auth_header(),
        json={"query": "SELECT missing_internal_column FROM users"},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail == "内部错误"
    assert "missing_internal_column" not in detail
    assert "SELECT" not in detail.upper()


def test_db_table_clamps_pagination(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.get(
        "/api/v1/admin/db/tables/users",
        headers=_auth_header(),
        params={"page": 0, "limit": 999},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["page"] == 1
    assert payload["limit"] == 200
    assert "has_next" in payload


def test_llm_log_sensitive_columns_are_redacted_or_hidden(client, monkeypatch, db_session):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_session.execute(
        text(
            "INSERT INTO llm_api_request_logs(trace_id, request_json, response_json, headers_json, request_preview) "
            "VALUES ('trace-db', :request_json, :response_json, :headers_json, 'preview ok')"
        ),
        {
            "request_json": '{"messages":[{"content":"完整请求"}]}',
            "response_json": '{"choices":[{"message":{"content":"完整响应"}}]}',
            "headers_json": '{"Authorization":"Bearer secret"}',
        },
    )
    db_session.commit()

    response = client.get(
        "/api/v1/admin/db/tables/llm_api_request_logs",
        headers=_auth_header(),
        params={"limit": 5},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "headers_json" not in payload["columns"]
    assert "request_json" not in payload["columns"]
    assert "response_json" not in payload["columns"]
    assert payload["rows"][0]["request_preview"] == "preview ok"
