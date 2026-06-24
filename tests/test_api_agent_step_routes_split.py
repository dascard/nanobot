from __future__ import annotations

import inspect
import json
from pathlib import Path

from fastapi.testclient import TestClient


_AGENT_STEP_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/render"),
    ("POST", "/api/v1/chat-step"),
)

_AGENT_STEP_ROUTE_EXPORTS = (
    "AgentStepRequest",
    "agent_step_event_payload",
    "run_agent_step",
    "run_agent_step_stream",
    "agent_step_sse_data",
    "render_markdown",
    "chat_step",
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


def _invalid_step_request(*, stream: bool = False) -> dict:
    return {
        "protocol": "bad-protocol",
        "run_id": "split-run",
        "input": {"user_message": "hello"},
        "tools": [],
        "tool_results": [],
        "stream": stream,
    }


def _sse_events(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        if not block.startswith("data: "):
            continue
        events.append(json.loads(block.removeprefix("data: ")))
    return events


def test_api_agent_step_routes_are_registered_from_split_module():
    for method, path in _AGENT_STEP_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.agent_step_routes"}


def test_legacy_api_routes_agent_step_imports_still_work():
    from api import agent_step_routes
    from api import routes

    for name in _AGENT_STEP_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(agent_step_routes, name)

    body = routes.AgentStepRequest(run_id="run-1")
    assert body.run_id == "run-1"
    assert body.protocol == "agent-step.v1"


def test_split_agent_step_routes_use_legacy_api_token_monkeypatch(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        ok = test_client.post(
            "/api/v1/chat-step",
            json=_invalid_step_request(),
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.post(
            "/api/v1/chat-step",
            json=_invalid_step_request(),
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert ok.json()["status"] == "error"
    assert ok.json()["error"]["code"] == "invalid_protocol"
    assert wrong.status_code == 401


def test_api_agent_step_routes_are_not_registered_twice():
    for method, path in _AGENT_STEP_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_agent_step_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/agent_step_routes.py").read_text(encoding="utf-8")

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
        "api/chat_runtime_route_context.py",
        "api/chat_guardrail_facade.py",
        "api/chat_media_precache.py",
        "api/chat_persona_context.py",
        "api/chat_persona_lookup.py",
        "api/chat_streaming_helpers.py",
        "api/chat_sse_loop.py",
        "api/chat_streaming_result.py",
        "api/chat_non_streaming_result.py",
        "api/chat_pre_bridge_decision.py",
        "api/chat_pre_bridge_route_result.py",
        "api/chat_private_buffer.py",
        "api/chat_push_envelope.py",
        "api/chat_route_runner.py",
    ):
        source = Path(path).read_text(encoding="utf-8")

        assert "from api.routes" not in source
        assert "import api.routes" not in source
        assert "asyncio.run" not in source
        assert "run_awaitable_sync" not in source


def test_agent_step_routes_keep_order_before_chat():
    render_index = _route_index("/api/v1/render", "GET")
    chat_step_index = _route_index("/api/v1/chat-step", "POST")
    chat_index = _route_index("/api/v1/chat", "POST")

    assert render_index < chat_step_index < chat_index


def test_render_route_stays_public_and_deprecated(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/render?text=hello")

    assert response.status_code == 200
    assert response.json() == {"status": "deprecated"}


def test_chat_step_accept_header_triggers_sse_without_stream_flag(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        with test_client.stream(
            "POST",
            "/api/v1/chat-step",
            json=_invalid_step_request(stream=False),
            headers={
                "Authorization": "Bearer split-token",
                "Accept": "text/event-stream",
            },
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(body)
    assert events[0] == {"status": "progress", "text": "正在判断需要的业务工具..."}
    assert events[-1]["status"] == "error"
    assert events[-1]["error"]["code"] == "invalid_protocol"


def test_chat_step_stream_flag_triggers_sse_without_accept_header(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        with test_client.stream(
            "POST",
            "/api/v1/chat-step",
            json=_invalid_step_request(stream=True),
            headers={"Authorization": "Bearer split-token"},
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [event["status"] for event in _sse_events(body)] == ["progress", "error"]


def test_api_agent_step_async_boundaries_remain_coroutines():
    from api import agent_step_routes
    from api import routes

    assert inspect.iscoroutinefunction(agent_step_routes.chat_step)
    assert inspect.iscoroutinefunction(routes.chat_step)


def test_chat_boundaries_stay_in_parent_routes_after_group_message_split():
    from api import group_message_routes
    from api import group_utility_routes
    from api import routes

    assert routes.proxy_chat.__module__ == "api.routes"
    assert routes._persist_chat_turn.__module__ == "api.routes"
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes.group_message is group_message_routes.group_message
    assert routes.group_timing_timer is group_utility_routes.group_timing_timer
