from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from tests.http_test_utils import open_test_client_without_lifespan

from core.database import User


_GROUP_UTILITY_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/update_group_name"),
    ("POST", "/api/v1/group_timing"),
    ("POST", "/api/v1/group_timing/timer"),
)

_GROUP_UTILITY_ROUTE_EXPORTS = (
    "UpdateGroupNameRequest",
    "GroupTimingRequest",
    "GroupTimingTimerRequest",
    "_build_group_timing_context",
    "update_group_name",
    "group_timing_deprecated",
    "group_timing_timer",
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


def _route_index(path: str, method: str) -> int:
    for index, (route_path, route) in enumerate(_api_route_entries()):
        if route_path == path and method in getattr(route, "methods", set()):
            return index
    raise AssertionError(f"missing route: {method} {path}")


def test_api_group_utility_routes_are_registered_from_split_module():
    for method, path in _GROUP_UTILITY_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.group_utility_routes"}


def test_legacy_api_routes_group_utility_imports_still_work():
    from api import group_utility_routes
    from api import routes

    for name in _GROUP_UTILITY_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(group_utility_routes, name)

    body = routes.GroupTimingTimerRequest(group_id="123", generation=7)
    assert body.group_id == "123"
    assert body.generation == 7
    assert body.timer_fired is True


def test_split_group_utility_routes_use_legacy_api_token_monkeypatch(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with open_test_client_without_lifespan(app) as test_client:
        ok = test_client.post(
            "/api/v1/update_group_name",
            json={"group_id": "123", "group_name": "群工具测试"},
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.post(
            "/api/v1/update_group_name",
            json={"group_id": "123", "group_name": "群工具测试"},
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_group_utility_routes_are_not_registered_twice():
    for method, path in _GROUP_UTILITY_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_group_utility_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/group_utility_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_group_utility_routes_keep_order_between_group_message_and_agent_step():
    group_message_index = _route_index("/api/v1/group/message", "POST")
    update_index = _route_index("/api/v1/update_group_name", "POST")
    group_timing_index = _route_index("/api/v1/group_timing", "POST")
    timer_index = _route_index("/api/v1/group_timing/timer", "POST")
    render_index = _route_index("/api/v1/render", "GET")
    chat_step_index = _route_index("/api/v1/chat-step", "POST")
    chat_index = _route_index("/api/v1/chat", "POST")

    assert group_message_index < update_index
    assert update_index < group_timing_index
    assert group_timing_index < timer_index
    assert timer_index < render_index
    assert render_index < chat_step_index
    assert chat_step_index < chat_index


def test_group_utility_async_boundaries_remain_explicit():
    from api import group_utility_routes
    from api import routes

    assert not inspect.iscoroutinefunction(group_utility_routes.update_group_name)
    assert inspect.iscoroutinefunction(group_utility_routes.group_timing_deprecated)
    assert inspect.iscoroutinefunction(group_utility_routes.group_timing_timer)
    assert not inspect.iscoroutinefunction(routes.update_group_name)
    assert inspect.iscoroutinefunction(routes.group_timing_deprecated)
    assert inspect.iscoroutinefunction(routes.group_timing_timer)


def test_update_group_name_keeps_group_user_id_normalization(db_session):
    from api import group_utility_routes

    group_utility_routes.update_group_name(
        group_utility_routes.UpdateGroupNameRequest(group_id="123", group_name="旧群名"),
        db=db_session,
        _auth=None,
    )
    created = db_session.query(User).filter(User.id == "group_123").one()
    assert created.name == "旧群名"

    group_utility_routes.update_group_name(
        group_utility_routes.UpdateGroupNameRequest(group_id="123", group_name="新群名"),
        db=db_session,
        _auth=None,
    )
    assert db_session.query(User).filter(User.id == "group_123").one().name == "新群名"

    group_utility_routes.update_group_name(
        group_utility_routes.UpdateGroupNameRequest(group_id="group_123", group_name="带前缀群名"),
        db=db_session,
        _auth=None,
    )
    assert db_session.query(User).filter(User.id == "group_123").one().name == "带前缀群名"
    assert db_session.query(User).filter(User.id == "group_group_123").first() is None


@pytest.mark.asyncio
async def test_group_timing_timer_uses_legacy_routes_get_bridge_monkeypatch(monkeypatch, db_session):
    from api import group_utility_routes
    from api import routes

    class FakeRuntime:
        _states = {}

        async def handle_timer_fired(self, *args, **kwargs):
            return {"action": "continue", "pending_text": "你好"}

        def note_bot_replied(self, group_id):
            raise AssertionError("empty fake bridge reply should not mark bot replied")

    class FakeBridge:
        async def handle_message(self, message, *, session_id, user_id, metadata):
            assert message == "<user_input>\n你好\n</user_input>"
            assert session_id == "group_123"
            assert user_id == "group_123"
            assert metadata["group_id"] == "123"
            assert metadata["is_superuser"] is False
            assert type(metadata["is_superuser"]) is bool
            return ""

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: FakeRuntime())
    monkeypatch.setattr(routes, "get_bridge", lambda: FakeBridge())

    result = await group_utility_routes.group_timing_timer(
        group_utility_routes.GroupTimingTimerRequest(group_id="123", generation=1),
        db=db_session,
        _auth=None,
    )

    assert result["action"] == "continue"
    assert result["reply"] == ""
    assert result["reply_meta"] is None
    assert result["group_id"] == "123"
