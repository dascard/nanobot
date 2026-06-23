from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from core.database import ChatLog, ConversationTurn, User, get_db


_HISTORY_LOG_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/chat/mark-clear"),
    ("GET", "/api/v1/chat/history-summary"),
    ("POST", "/api/v1/chat/compact-history"),
    ("GET", "/api/v1/context"),
    ("POST", "/api/v1/log"),
    ("POST", "/api/v1/log_ambient"),
    ("GET", "/api/v1/search_logs"),
)

_HISTORY_LOG_ROUTE_EXPORTS = (
    "LogRequest",
    "AmbientLogRequest",
    "mark_clear",
    "get_history_summary",
    "compact_history",
    "get_context",
    "submit_log",
    "submit_ambient_log",
    "search_history_logs",
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


@pytest.fixture()
def client_with_db(db_session):
    from server import app

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)


def test_api_history_log_routes_are_registered_from_split_module():
    for method, path in _HISTORY_LOG_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.history_log_routes"}


def test_legacy_api_routes_history_log_imports_still_work():
    from api import history_log_routes
    from api import routes

    for name in _HISTORY_LOG_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(history_log_routes, name)

    log_body = routes.LogRequest(user_id="u1", role="user", content="hello")
    ambient_body = routes.AmbientLogRequest(group_id="42", sender_name="alice", content="hi")
    assert log_body.user_id == "u1"
    assert ambient_body.group_id == "42"


def test_split_history_log_routes_use_legacy_api_token_monkeypatch(client_with_db, monkeypatch):
    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    ok = client_with_db.get(
        "/api/v1/chat/history-summary?user_id=u1",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client_with_db.get(
        "/api/v1/chat/history-summary?user_id=u1",
        headers={"Authorization": "Bearer wrong"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_history_log_routes_are_not_registered_twice():
    for method, path in _HISTORY_LOG_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_history_log_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/history_log_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable():
    for path in (
        "api/chat_content_helpers.py",
        "api/chat_response_contract.py",
        "api/chat_persistence.py",
        "api/chat_request_contract.py",
        "api/chat_runtime_facade.py",
        "api/chat_guardrail_facade.py",
        "api/chat_streaming_helpers.py",
        "api/chat_sse_loop.py",
        "api/chat_streaming_result.py",
        "api/chat_private_buffer.py",
        "api/chat_push_envelope.py",
    ):
        source = Path(path).read_text(encoding="utf-8")

        assert "from api.routes" not in source
        assert "import api.routes" not in source
        assert "asyncio.run" not in source
        assert "run_awaitable_sync" not in source


def test_chat_persistence_helpers_stay_in_parent_routes():
    from api import routes

    assert routes._persist_chat_turn.__module__ == "api.routes"
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes.init_legacy_memory.__module__ == "api.routes"


def test_submit_log_keeps_background_evolution_boundary(db_session, monkeypatch):
    from api import history_log_routes

    calls = []

    def fake_evolution_task(user_id: str):
        calls.append(user_id)

    monkeypatch.setattr(history_log_routes, "EVOLUTION_THRESHOLD", 1)
    monkeypatch.setattr(history_log_routes, "evolution_task", fake_evolution_task)

    background_tasks = BackgroundTasks()
    response = history_log_routes.submit_log(
        history_log_routes.LogRequest(user_id="u-log", role="user", content="日志"),
        background_tasks,
        db_session,
        _auth=True,
    )

    assert response == {"status": "ok", "unprocessed_logs": 1}
    assert db_session.query(User).filter_by(id="u-log").count() == 1
    rows = db_session.query(ChatLog).filter_by(user_id="u-log").all()
    assert len(rows) == 1
    assert rows[0].content == "日志"
    assert rows[0].processed == 0
    assert db_session.query(ConversationTurn).filter_by(user_id="u-log").count() == 0
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is history_log_routes.evolution_task
    assert task.args == ("u-log",)
    assert calls == []


def test_ambient_log_keeps_group_session_and_processed_contract(db_session):
    from api import history_log_routes

    response = history_log_routes.submit_ambient_log(
        history_log_routes.AmbientLogRequest(
            group_id="123",
            session_name="测试群",
            sender_name="alice",
            content="环境消息",
            message_id="m1",
        ),
        db_session,
        _auth=True,
    )

    assert response == {"status": "ok", "message": "ambient log saved [deprecated]"}
    user = db_session.query(User).filter_by(id="group_123").one()
    assert user.name == "测试群"
    row = db_session.query(ChatLog).filter_by(user_id="group_123").one()
    assert row.session_id == "group_123"
    assert row.sender_name == "alice"
    assert row.session_name == "测试群"
    assert row.role == "ambient"
    assert row.content == "[alice]: 环境消息"
    assert row.processed == 1
    assert row.message_id == "m1"


def test_health_check_stays_in_parent_routes():
    routes = _api_routes_for("/api/v1/health", "GET")

    assert routes
    assert {route.endpoint.__module__ for route in routes} == {"api.routes"}
