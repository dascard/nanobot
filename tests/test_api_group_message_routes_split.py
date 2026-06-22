from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


_GROUP_MESSAGE_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/group/message"),
)

_GROUP_MESSAGE_ROUTE_EXPORTS = (
    "OneBotMessageSegmentPayload",
    "GroupMessageRequest",
    "group_message",
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


def test_api_group_message_route_is_registered_from_split_module():
    for method, path in _GROUP_MESSAGE_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.group_message_routes"}


def test_legacy_api_routes_group_message_imports_still_work():
    from api import group_message_routes
    from api import routes

    for name in _GROUP_MESSAGE_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(group_message_routes, name)

    body = routes.GroupMessageRequest(group_id="123", sender_id="456", message="你好")
    assert body.group_id == "123"
    assert body.sender_id == "456"
    assert body.message == "你好"
    assert body.bot_aliases == []
    assert body.segments == []

    segment = routes.OneBotMessageSegmentPayload(type="text", data={"text": "hi"})
    assert segment.type == "text"
    assert segment.data == {"text": "hi"}


def test_split_group_message_route_uses_legacy_api_token_monkeypatch(monkeypatch):
    from server import app

    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        async def handle(self, req):
            return {"action": "no_reply", "reason": "fake"}

    monkeypatch.setattr(
        "app.group_ingress.service.GroupIngressService",
        FakeService,
    )
    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        ok = test_client.post(
            "/api/v1/group/message",
            json={"group_id": "123", "message": "hi"},
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.post(
            "/api/v1/group/message",
            json={"group_id": "123", "message": "hi"},
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert ok.json() == {"action": "no_reply", "reason": "fake"}
    assert wrong.status_code == 401


def test_api_group_message_route_is_not_registered_twice():
    for method, path in _GROUP_MESSAGE_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_group_message_route_does_not_import_parent_routes_or_sync_awaitable():
    path = Path("api/group_message_routes.py")
    assert path.exists()
    source = path.read_text(encoding="utf-8")

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
    ):
        source = Path(path).read_text(encoding="utf-8")

        assert "from api.routes" not in source
        assert "import api.routes" not in source
        assert "asyncio.run" not in source
        assert "run_awaitable_sync" not in source


def test_group_message_route_keeps_order_before_group_utility_and_chat():
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


def test_api_group_message_async_boundary_remains_coroutine():
    from api import group_message_routes
    from api import routes

    assert inspect.iscoroutinefunction(group_message_routes.group_message)
    assert inspect.iscoroutinefunction(routes.group_message)


@pytest.mark.asyncio
async def test_group_message_uses_legacy_routes_get_bridge_monkeypatch(monkeypatch, db_session):
    from api import group_message_routes
    from api import routes

    calls = []

    class FakeService:
        def __init__(self, *, db, background_tasks, bridge_provider):
            self.db = db
            self.background_tasks = background_tasks
            self.bridge_provider = bridge_provider

        async def handle(self, req):
            bridge = self.bridge_provider()
            calls.append(
                {
                    "bridge": bridge,
                    "group_id": req.group_id,
                    "client_meta": req.client_meta,
                }
            )
            return {"action": "no_reply", "reason": "fake"}

    class FakeBridge:
        pass

    monkeypatch.setattr(
        "app.group_ingress.service.GroupIngressService",
        FakeService,
    )
    monkeypatch.setattr(routes, "get_bridge", lambda: FakeBridge())

    result = await group_message_routes.group_message(
        group_message_routes.GroupMessageRequest(
            group_id="123",
            message="你好",
            client_meta={"platform": "web"},
        ),
        db=db_session,
        background_tasks=None,
        _auth=None,
    )

    assert result == {"action": "no_reply", "reason": "fake"}
    assert isinstance(calls[0]["bridge"], FakeBridge)
    assert calls[0]["group_id"] == "123"
    assert calls[0]["client_meta"]["platform"] == "web"
    assert calls[0]["client_meta"]["chat_type"] == "group"


@pytest.mark.asyncio
async def test_group_message_rejects_conflicting_client_meta_before_service(
    monkeypatch,
    db_session,
):
    from api import group_message_routes

    class FakeService:
        def __init__(self, *args, **kwargs):
            raise AssertionError("invalid client_meta must not enter service")

    monkeypatch.setattr(
        "app.group_ingress.service.GroupIngressService",
        FakeService,
    )

    with pytest.raises(HTTPException) as exc:
        await group_message_routes.group_message(
            group_message_routes.GroupMessageRequest(
                group_id="123",
                message="你好",
                client_meta={"chat_type": "private"},
            ),
            db=db_session,
            background_tasks=None,
            _auth=None,
        )

    assert exc.value.status_code == 400
    assert "client_meta" in str(exc.value.detail)


def test_chat_and_health_boundaries_stay_in_parent_routes_after_group_message_split():
    from api import routes

    assert routes.proxy_chat.__module__ == "api.routes"
    assert routes._persist_chat_turn.__module__ == "api.routes"
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes._build_multimodal_user_input_text.__module__ == "api.routes"

    health_routes = _api_routes_for("/api/v1/health", "GET")
    assert health_routes
    assert {route.endpoint.__module__ for route in health_routes} == {"api.routes"}


def test_group_ingress_helper_facades_stay_in_parent_routes():
    from api import routes
    from app.group_ingress import helpers

    assert routes._normalize_onebot_segments is helpers.normalize_onebot_segments
    assert routes._build_group_message_text is helpers.build_group_message_text
    assert routes._persist_group_bridge_reply is helpers.persist_group_bridge_reply
    assert routes._derive_group_trigger_reason is helpers.derive_group_trigger_reason
