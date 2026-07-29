"""Agent Link v1 WebSocket 握手、聊天和动态前端工具测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.agent_link.protocol import (
    AgentLinkFrame,
    AgentLinkProtocolError,
    make_agent_link_frame,
)
from core.agent_link.runtime import (
    AgentLinkChatRequest,
    AgentLinkPeer,
    AgentLinkRuntime,
    AgentLinkSessionKey,
    AgentLinkToolDefinition,
)


def _hello(*, token: str = "secret") -> dict:
    return make_agent_link_frame(
        "control.hello",
        {
            "client": {"name": "MeaPet", "version": "1.0.0"},
            "device": {"id": "device-test"},
            "auth": {"scheme": "bearer", "token": token},
            "resume": {"session_id": "session-test"},
            "capabilities": {
                "chat": {
                    "submit": True,
                    "streaming": True,
                    "cancel": True,
                },
                "tools": {
                    "dynamic": True,
                    "call": True,
                    "cancel": True,
                    "list_changed": True,
                },
            },
            "required_extensions": [],
        },
        message_id="hello-test",
        session_id="session-test",
    )


def _tool_snapshot() -> dict:
    return make_agent_link_frame(
        "tools.snapshot",
        {
            "revision": 1,
            "tools": [
                {
                    "name": "meapet.echo",
                    "description": "在 MeaPet 前端回显一个值。",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                        },
                        "required": ["value"],
                    },
                    "output_schema": {"type": "object"},
                }
            ],
        },
        message_id="tools-1",
        session_id="session-test",
    )


def _chat_submit(request_id: str = "turn-test") -> dict:
    return make_agent_link_frame(
        "chat.submit",
        {
            "content": "请调用 meapet.echo，然后回复用户。",
            "user_text": "回显测试",
            "history": [],
            "frontend_context": {},
            "attachments": [],
            "response_format": "meapet-segments-v1",
            "idempotent": True,
        },
        message_id=request_id,
        session_id="session-test",
    )


def _test_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    runtime: AgentLinkRuntime,
) -> FastAPI:
    from api import agent_link_routes

    monkeypatch.setattr(
        agent_link_routes,
        "NANOBOT_AGENT_LINK_TOKEN",
        "secret",
    )
    monkeypatch.setattr(
        agent_link_routes,
        "get_agent_link_runtime",
        lambda: runtime,
    )
    app = FastAPI()
    app.include_router(agent_link_routes.router)
    return app


def test_agent_link_envelope_rejects_incompatible_version() -> None:
    frame = make_agent_link_frame("control.ping")
    with pytest.raises(AgentLinkProtocolError, match="主版本"):
        AgentLinkFrame.parse({**frame, "version": "2.0"})


def test_agent_link_rejects_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AgentLinkRuntime()
    app = _test_app(monkeypatch, runtime=runtime)

    with TestClient(app) as client:
        with client.websocket_connect("/agent-link") as websocket:
            websocket.send_json(_hello(token="wrong"))
            response = websocket.receive_json()

    assert response["type"] == "control.error"
    assert response["reply_to"] == "hello-test"
    assert response["payload"]["category"] == "authentication"
    assert response["payload"]["code"] == "INVALID_TOKEN"
    assert "wrong" not in str(response)


def test_agent_link_chat_calls_frontend_tool_and_replays_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AgentLinkRuntime()
    app = _test_app(monkeypatch, runtime=runtime)
    captured: dict[str, object] = {"executions": 0}

    class _ChatPort:
        async def run_chat(self, request, tool_caller):
            from nanobot_kt.agent_link_tools import build_agent_link_tools

            captured["executions"] = int(captured["executions"]) + 1
            captured["request"] = request
            tools = build_agent_link_tools(
                request.key,
                request.tools,
                runtime=tool_caller,
            )
            tool = next(item for item in tools if item.tool_name == "meapet.echo")
            result = await tool.execute({"value": "ok"})
            assert result.success
            return f"前端返回：{result.get_text_output()}"

    runtime.bind_chat_port(_ChatPort())

    with TestClient(app) as client:
        with client.websocket_connect("/agent-link") as websocket:
            websocket.send_json(_hello())
            ready = websocket.receive_json()
            assert ready["type"] == "control.ready"
            assert ready["reply_to"] == "hello-test"
            assert ready["payload"]["capabilities"]["tools"]["dynamic"] is True
            assert ready["payload"]["capabilities"]["chat"]["streaming"] is False

            websocket.send_json(_tool_snapshot())
            websocket.send_json(_chat_submit())

            terminal = None
            received_types: list[str] = []
            while terminal is None:
                frame = websocket.receive_json()
                received_types.append(frame["type"])
                if frame["type"] == "tool.call":
                    assert frame["payload"] == {
                        "name": "meapet.echo",
                        "arguments": {"value": "ok"},
                    }
                    websocket.send_json(
                        make_agent_link_frame(
                            "tool.accepted",
                            {"status": "accepted", "duplicate": False},
                            session_id="session-test",
                            reply_to=frame["id"],
                        )
                    )
                    websocket.send_json(
                        make_agent_link_frame(
                            "tool.result",
                            {
                                "status": "succeeded",
                                "result": {"value": "ok"},
                            },
                            session_id="session-test",
                            reply_to=frame["id"],
                        )
                    )
                elif frame["type"] == "chat.final":
                    terminal = frame

            assert "chat.accepted" in received_types
            assert "tool.call" in received_types
            assert terminal["reply_to"] == "turn-test"
            assert "<MEAPET_SEGMENT>" in terminal["payload"]["text"]
            assert '"value":"ok"' in terminal["payload"]["text"]

            websocket.send_json(_chat_submit())
            replay = websocket.receive_json()
            assert replay["type"] == "chat.final"
            assert replay["reply_to"] == "turn-test"

    assert captured["executions"] == 1
    request = captured["request"]
    assert isinstance(request, AgentLinkChatRequest)
    assert request.tools[0].wire_schema()["function"]["name"] == "meapet.echo"


def test_agent_link_chat_cancel_propagates_to_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AgentLinkRuntime()
    app = _test_app(monkeypatch, runtime=runtime)
    cancelled = {"value": False}

    class _ChatPort:
        async def run_chat(self, _request, _tool_caller):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled["value"] = True
                raise

    runtime.bind_chat_port(_ChatPort())

    with TestClient(app) as client:
        with client.websocket_connect("/agent-link") as websocket:
            websocket.send_json(_hello())
            assert websocket.receive_json()["type"] == "control.ready"
            websocket.send_json(_tool_snapshot())
            websocket.send_json(_chat_submit("turn-cancel"))
            assert websocket.receive_json()["type"] == "chat.accepted"
            websocket.send_json(
                make_agent_link_frame(
                    "chat.cancel",
                    {"request_id": "turn-cancel"},
                    session_id="session-test",
                    reply_to="turn-cancel",
                )
            )
            response = websocket.receive_json()

    assert response["type"] == "chat.cancelled"
    assert response["reply_to"] == "turn-cancel"
    assert cancelled["value"] is True


@pytest.mark.asyncio
async def test_agent_link_tool_returns_offline_without_queueing() -> None:
    from nanobot_kt.agent_link_tools import AgentLinkProxyTool

    runtime = AgentLinkRuntime()
    key = AgentLinkSessionKey("device-test", "session-test")
    definition = AgentLinkToolDefinition(
        name="meapet.echo",
        description="回显。",
        input_schema={"type": "object", "properties": {}},
    )
    tool = AgentLinkProxyTool(key, definition, runtime)

    result = await tool.execute({})

    assert result.success is False
    assert result.metadata["code"] == "OFFLINE"
    assert '"code":"OFFLINE"' in str(result.error)


@pytest.mark.asyncio
async def test_agent_link_runtime_can_call_frontend_without_active_chat() -> None:
    runtime = AgentLinkRuntime()
    key = AgentLinkSessionKey("device-test", "session-test")
    definition = AgentLinkToolDefinition(
        name="meapet.echo",
        description="回显。",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
        },
    )
    sent_frames: list[dict] = []
    peer: AgentLinkPeer

    async def send_frame(frame) -> None:
        sent_frames.append(dict(frame))
        if frame["type"] != "tool.call":
            return
        peer.resolve_tool_frame(
            AgentLinkFrame.parse(
                make_agent_link_frame(
                    "tool.result",
                    {
                        "status": "succeeded",
                        "result": {"value": frame["payload"]["arguments"]["value"]},
                    },
                    session_id=key.session_id,
                    reply_to=frame["id"],
                )
            )
        )

    async def close_transport(_code: int, _reason: str) -> None:
        return None

    peer = AgentLinkPeer(
        key=key,
        send_frame=send_frame,
        close_transport=close_transport,
    )
    peer.replace_snapshot(1, (definition,))
    await runtime.attach(peer)

    result = await runtime.call_tool(
        key,
        "meapet.echo",
        {"value": "主动调用"},
    )

    assert result == {"value": "主动调用"}
    assert [frame["type"] for frame in sent_frames] == ["tool.call"]
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_agent_link_dynamic_tool_enters_registry_and_tool_plan() -> None:
    from kohakuterrarium.core.executor import Executor
    from kohakuterrarium.core.registry import Registry

    from core.tool_plan import (
        ToolPlan,
        build_tool_plan,
        extend_tool_plan,
    )
    from nanobot_kt.agent_link_adapter import KtAgentLinkChatAdapter

    runtime = AgentLinkRuntime()
    key = AgentLinkSessionKey("device-test", "session-test")
    definition = AgentLinkToolDefinition(
        name="meapet.echo",
        description="回显。",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
        },
    )
    registry = Registry()
    executor = Executor()
    captured: dict[str, object] = {}

    class _Bridge:
        def __init__(self):
            self.agent = SimpleNamespace(
                registry=registry,
                executor=executor,
            )

        async def handle_message(self, query, **kwargs):
            captured["query"] = query
            captured["kwargs"] = kwargs
            captured["plan"] = build_tool_plan(
                chat_type="private",
                platform="meapet",
                session_id=key.bridge_session_id,
                runtime_preset="full",
                db=None,
            )
            tool = registry.get_tool("meapet.echo")
            result = await tool.execute({"value": "ok"})
            captured["tool_result"] = result
            return "完成"

    bridge = _Bridge()

    class _Pool:
        def _session_key(self, *, user_id, session_id):
            return session_id or user_id

        async def _acquire_bridge(self, _key):
            return bridge

        async def _release_bridge(self, released_key):
            captured["released_key"] = released_key

    class _ToolCaller:
        async def call_tool(self, _key, name, arguments):
            captured["tool_call"] = (name, dict(arguments))
            return {"value": arguments["value"]}

    request = AgentLinkChatRequest(
        key=key,
        request_id="turn-adapter",
        content="执行动态工具",
        user_text="执行动态工具",
        history=(),
        frontend_context={},
        files=(),
        tools=(definition,),
    )
    answer = await KtAgentLinkChatAdapter(_Pool()).run_chat(
        request,
        _ToolCaller(),
    )

    assert answer == "完成"
    assert registry.get_tool("meapet.echo") is not None
    assert executor.get_tool("meapet.echo") is registry.get_tool("meapet.echo")
    plan = captured["plan"]
    assert plan.can_execute("meapet.echo") is True
    assert captured["tool_call"] == ("meapet.echo", {"value": "ok"})
    assert captured["tool_result"].success is True
    assert captured["released_key"] == key.bridge_session_id

    base_schema = {
        "type": "function",
        "function": {
            "name": "reply",
            "description": "回复。",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    base_plan = ToolPlan.from_effective_tools(
        enabled={"reply": True},
        tool_schemas=[base_schema],
        chat_type="private",
    )
    extended = extend_tool_plan(
        base_plan,
        [definition.wire_schema()],
        chat_type="private",
        platform="meapet",
        session_id=key.bridge_session_id,
        db=None,
    )

    assert extended.can_execute("reply") is True
    assert extended.can_execute("meapet.echo") is True
    assert {
        schema["function"]["name"]
        for schema in extended.sent_tool_schemas
    } == {"reply", "meapet.echo"}
