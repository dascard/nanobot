"""ACP、A2A 与 Headless 薄 Adapter 的兼容性和安全回归。"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.agent_runtime import (
    AgentTurnRequest,
    AgentTurnResult,
    FakeAgentRuntime,
    RequestRuntimeContext,
    RuntimeActor,
    RuntimeActorType,
    RuntimeArtifactRef,
    RuntimeChatType,
    RuntimeMessage,
    RuntimeOwnerType,
    RuntimePrincipal,
    RuntimeRunIdentity,
    RuntimeRunStatus,
    RuntimeToolCall,
    RuntimeToolCallStatus,
    RuntimeUsage,
)
from core.agent_runtime.event_stream import RuntimeRunEventEmitter
from core.agent_runtime.service_ports import (
    RuntimePermissionOutcome,
    RuntimePermissionRequest,
    RuntimePermissionRisk,
    StaticPermissionPort,
)
from core.interoperability import (
    A2A_FEATURE_ID,
    ACP_FEATURE_ID,
    HEADLESS_FEATURE_ID,
    A2AClientAdapter,
    A2AClientRequest,
    A2AInterface,
    A2APartKind,
    A2AProtocolError,
    A2ATaskState,
    A2ATransportError,
    A2ATransportLimits,
    AcpAdapterConfig,
    AcpAgentAdapter,
    AcpPermissionPort,
    AcpProtocolError,
    HeadlessExecutionError,
    HeadlessLimits,
    HeadlessRuntimeAdapter,
    HttpsA2AJsonRpcTransport,
    InteroperabilityDisabledError,
)
from core.lifecycle import (
    FEATURE_LIFECYCLE_REGISTRY,
    FeatureLifecycleState,
    FeatureScope,
    evaluate_feature_enablement,
)


def _enablement(feature_id: str):
    descriptor = FEATURE_LIFECYCLE_REGISTRY.require(feature_id)
    return evaluate_feature_enablement(
        feature_id,
        requested=True,
        scope=FeatureScope.ADMIN,
        satisfied_gates=frozenset(descriptor.enablement_gates),
    )


def _disabled(feature_id: str):
    return evaluate_feature_enablement(
        feature_id,
        requested=False,
        scope=FeatureScope.ADMIN,
    )


def _context(
    session_id: str = "session-1",
    *,
    turn: int = 1,
    owner_id: str = "10001",
) -> RequestRuntimeContext:
    return RequestRuntimeContext(
        request_id=f"request-{turn}",
        principal=RuntimePrincipal(
            platform="qq",
            owner_type=RuntimeOwnerType.USER,
            owner_id=owner_id,
        ),
        session_id=session_id,
        chat_type=RuntimeChatType.PRIVATE,
        trace_id=f"trace-{turn}",
        run_id=f"run-{turn}",
        turn_id=f"turn-{turn}",
        correlation_id=f"correlation-{turn}",
        actor=RuntimeActor(RuntimeActorType.USER, owner_id),
    )


def _identity(*, owner_id: str = "10001") -> RuntimeRunIdentity:
    return _context(owner_id=owner_id).execution_identity()


def test_interoperability_features_are_experimental_default_off_and_admin_only():
    for feature_id in (
        ACP_FEATURE_ID,
        A2A_FEATURE_ID,
        HEADLESS_FEATURE_ID,
    ):
        descriptor = FEATURE_LIFECYCLE_REGISTRY.require(feature_id)
        assert descriptor.state is FeatureLifecycleState.EXPERIMENTAL
        assert descriptor.default_enabled is False
        assert descriptor.supported_scopes == (FeatureScope.ADMIN,)
        assert descriptor.enablement_gates


def test_interoperability_adapter_rejects_disabled_or_mismatched_feature():
    async def runtime_factory(_session_id: str):
        return FakeAgentRuntime()

    async def context_factory(session_id: str, turn: int):
        return _context(session_id, turn=turn)

    with pytest.raises(InteroperabilityDisabledError):
        AcpAgentAdapter(
            enablement=_disabled(ACP_FEATURE_ID),
            runtime_factory=runtime_factory,
            context_factory=context_factory,
            notification_sink=lambda _value: None,
        )
    with pytest.raises(InteroperabilityDisabledError):
        AcpAgentAdapter(
            enablement=_enablement(HEADLESS_FEATURE_ID),
            runtime_factory=runtime_factory,
            context_factory=context_factory,
            notification_sink=lambda _value: None,
        )


@pytest.mark.asyncio
async def test_acp_v1_executes_real_runtime_and_projects_stream_without_tool_payloads():
    notifications: list[dict[str, object]] = []
    runtimes: list[FakeAgentRuntime] = []

    async def runtime_factory(_session_id: str):
        runtime = FakeAgentRuntime()
        runtime.queue_text_deltas("你", "好")
        runtime.queue_result(
            AgentTurnResult(
                raw_result={"secret": "raw-result"},
                messages=(RuntimeMessage("assistant", "你好"),),
                tool_calls=(
                    RuntimeToolCall(
                        call_id="call-1",
                        name="news_search",
                        arguments={"token": "never-project"},
                        status=RuntimeToolCallStatus.COMPLETED,
                        result={"secret": "never-project"},
                    ),
                ),
            )
        )
        runtimes.append(runtime)
        return runtime

    async def context_factory(session_id: str, turn: int):
        return _context(session_id, turn=turn)

    async def sink(value):
        notifications.append(dict(value))

    adapter = AcpAgentAdapter(
        enablement=_enablement(ACP_FEATURE_ID),
        runtime_factory=runtime_factory,
        context_factory=context_factory,
        notification_sink=sink,
        config=AcpAdapterConfig(context_window_tokens=8192),
    )
    initialized = await adapter.handle_request(
        "initialize",
        {
            "protocolVersion": 1,
            "clientCapabilities": {
                "terminal": True,
                "fs": {"readTextFile": True, "writeTextFile": True},
            },
        },
    )
    assert initialized["protocolVersion"] == 1
    assert initialized["agentCapabilities"]["mcpCapabilities"] == {
        "http": False,
        "sse": False,
    }
    created = await adapter.handle_request(
        "session/new",
        {"cwd": "/workspace", "mcpServers": []},
    )
    session_id = created["sessionId"]
    response = await adapter.handle_request(
        "session/prompt",
        {
            "sessionId": session_id,
            "prompt": [
                {"type": "text", "text": "总结资料"},
                {
                    "type": "resource_link",
                    "name": "输入资料",
                    "uri": "artifact://input-1",
                },
            ],
        },
    )

    assert response == {"stopReason": "end_turn"}
    assert runtimes[0].requests[0].stream is True
    assert "artifact://input-1" in runtimes[0].requests[0].content
    assert [item["params"]["update"]["sessionUpdate"] for item in notifications] == [
        "agent_message_chunk",
        "agent_message_chunk",
        "tool_call",
    ]
    serialized = json.dumps(notifications, ensure_ascii=False)
    assert "never-project" not in serialized
    assert "raw-result" not in serialized
    assert all(item["jsonrpc"] == "2.0" for item in notifications)
    await adapter.handle_request("session/close", {"sessionId": session_id})
    assert runtimes[0].state.value == "stopped"


@pytest.mark.asyncio
async def test_acp_rejects_v2_workspace_mcp_and_unsafe_resource_overrides():
    factory_calls = 0

    async def runtime_factory(_session_id: str):
        nonlocal factory_calls
        factory_calls += 1
        return FakeAgentRuntime()

    adapter = AcpAgentAdapter(
        enablement=_enablement(ACP_FEATURE_ID),
        runtime_factory=runtime_factory,
        context_factory=lambda session_id, turn: _context(
            session_id,
            turn=turn,
        ),
        notification_sink=lambda _value: None,
    )
    with pytest.raises(AcpProtocolError, match="version 1"):
        await adapter.initialize({"protocolVersion": 2})
    await adapter.initialize({"protocolVersion": 1})
    with pytest.raises(AcpProtocolError, match="虚拟工作区"):
        await adapter.new_session({"cwd": "/etc", "mcpServers": []})
    with pytest.raises(AcpProtocolError, match="MCP"):
        await adapter.new_session(
            {
                "cwd": "/workspace",
                "mcpServers": [{"name": "override"}],
            }
        )
    with pytest.raises(AcpProtocolError, match="扩展"):
        await adapter.new_session(
            {
                "cwd": "/workspace",
                "mcpServers": [],
                "additionalDirectories": ["/host"],
            }
        )
    assert factory_calls == 0

    created = await adapter.new_session({"cwd": "/workspace", "mcpServers": []})
    with pytest.raises(AcpProtocolError, match="凭据"):
        await adapter.prompt(
            {
                "sessionId": created["sessionId"],
                "prompt": [
                    {
                        "type": "resource_link",
                        "name": "泄漏",
                        "uri": "https://user:secret@example.com/file",
                    }
                ],
            }
        )
    await adapter.close_all()


@pytest.mark.asyncio
async def test_acp_context_factory_failure_is_sanitized_and_releases_session():
    attempts = 0

    async def context_factory(session_id: str, turn: int):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("credential=never-leak")
        return _context(session_id, turn=turn)

    adapter = AcpAgentAdapter(
        enablement=_enablement(ACP_FEATURE_ID),
        runtime_factory=lambda _session_id: FakeAgentRuntime(),
        context_factory=context_factory,
        notification_sink=lambda _value: None,
    )
    await adapter.initialize({"protocolVersion": 1})
    created = await adapter.new_session({"cwd": "/workspace", "mcpServers": []})
    params = {
        "sessionId": created["sessionId"],
        "prompt": [{"type": "text", "text": "执行"}],
    }
    with pytest.raises(AcpProtocolError) as error:
        await adapter.prompt(params)
    assert "never-leak" not in str(error.value)
    assert await adapter.prompt(params) == {"stopReason": "end_turn"}
    await adapter.close_all()


class _RichEventRuntime(FakeAgentRuntime):
    async def run_event(self, request, handler):
        emitter = RuntimeRunEventEmitter(
            request.context.execution_identity(),
            handler,
        )
        self.requests.append(request)
        await emitter.status_changed(RuntimeRunStatus.ACCEPTED)
        await emitter.status_changed(RuntimeRunStatus.RUNNING)
        await emitter.tool_activity(
            RuntimeToolCall(
                "call-rich",
                "workspace_read",
                {"secret": "argument"},
                RuntimeToolCallStatus.REQUESTED,
            )
        )
        await emitter.tool_activity(
            RuntimeToolCall(
                "call-rich",
                "workspace_read",
                {"secret": "argument"},
                RuntimeToolCallStatus.COMPLETED,
                {"secret": "result"},
            )
        )
        await emitter.usage(RuntimeUsage(input_tokens=20, output_tokens=5))
        await emitter.artifact(
            RuntimeArtifactRef(
                artifact_id="artifact-rich",
                uri="artifact://artifact-rich",
                sha256="a" * 64,
                media_type="text/plain",
                size_bytes=12,
                source_run_id=request.context.run_id,
            )
        )
        await emitter.end(RuntimeRunStatus.SUCCEEDED)
        return AgentTurnResult(raw_result=None, messages=())


@pytest.mark.asyncio
async def test_acp_maps_tool_update_usage_and_artifact_from_runtime_events():
    notifications: list[dict[str, Any]] = []
    adapter = AcpAgentAdapter(
        enablement=_enablement(ACP_FEATURE_ID),
        runtime_factory=lambda _session_id: _RichEventRuntime(),
        context_factory=lambda session_id, turn: _context(
            session_id,
            turn=turn,
        ),
        notification_sink=lambda value: notifications.append(dict(value)),
        config=AcpAdapterConfig(context_window_tokens=100),
    )
    await adapter.initialize({"protocolVersion": 1})
    created = await adapter.new_session({"cwd": "/workspace", "mcpServers": []})
    await adapter.prompt(
        {
            "sessionId": created["sessionId"],
            "prompt": [{"type": "text", "text": "执行"}],
        }
    )
    updates = [item["params"]["update"] for item in notifications]
    assert [item["sessionUpdate"] for item in updates] == [
        "tool_call",
        "tool_call_update",
        "usage_update",
        "agent_message_chunk",
    ]
    assert updates[1]["status"] == "completed"
    assert updates[2] == {
        "sessionUpdate": "usage_update",
        "used": 25,
        "size": 100,
    }
    assert updates[3]["content"]["type"] == "resource_link"
    assert "secret" not in json.dumps(updates, ensure_ascii=False)
    await adapter.close_all()


class _BlockingRuntime(FakeAgentRuntime):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()

    async def run_event(self, request, handler):
        emitter = RuntimeRunEventEmitter(
            request.context.execution_identity(),
            handler,
        )
        self.requests.append(request)
        await emitter.status_changed(RuntimeRunStatus.ACCEPTED)
        await emitter.status_changed(RuntimeRunStatus.RUNNING)
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await emitter.end(RuntimeRunStatus.CANCELLED)
            raise


@pytest.mark.asyncio
async def test_acp_cancel_interrupts_real_runtime_and_returns_cancelled_stop_reason():
    runtime = _BlockingRuntime()
    adapter = AcpAgentAdapter(
        enablement=_enablement(ACP_FEATURE_ID),
        runtime_factory=lambda _session_id: runtime,
        context_factory=lambda session_id, turn: _context(
            session_id,
            turn=turn,
        ),
        notification_sink=lambda _value: None,
    )
    await adapter.initialize({"protocolVersion": 1})
    created = await adapter.new_session({"cwd": "/workspace", "mcpServers": []})
    task = asyncio.create_task(
        adapter.prompt(
            {
                "sessionId": created["sessionId"],
                "prompt": [{"type": "text", "text": "等待"}],
            }
        )
    )
    await runtime.started.wait()
    await adapter.cancel({"sessionId": created["sessionId"]})
    assert await task == {"stopReason": "cancelled"}
    assert runtime.interrupt_reasons == ["acp_session_cancel"]
    await adapter.close_all()


@pytest.mark.asyncio
async def test_acp_pending_permission_offers_bounded_session_grant():
    wire_requests: list[dict[str, object]] = []

    async def client_request(request):
        wire_requests.append(dict(request))
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "outcome": {
                    "outcome": "selected",
                    "optionId": "nanobot.allow_once",
                }
            },
        }

    port = AcpPermissionPort(
        enablement=_enablement(ACP_FEATURE_ID),
        session_id="acp-session",
        policy=StaticPermissionPort({"workspace.write": RuntimePermissionOutcome.ASK}),
        client_request=client_request,
    )
    request = RuntimePermissionRequest(
        request_id="permission-1",
        identity=_identity(),
        action="workspace.write",
        resource="/workspace/result.md",
        risk=RuntimePermissionRisk.MEDIUM,
        requested_at=datetime.now(timezone.utc),
    )
    decision = await port.evaluate(request)

    assert decision.outcome is RuntimePermissionOutcome.ALLOW_ONCE
    assert decision.grant_id == "acp-once:permission-1"
    options = wire_requests[0]["params"]["options"]
    assert {item["kind"] for item in options} == {
        "allow_always",
        "allow_once",
        "reject_once",
    }
    serialized = json.dumps(wire_requests, ensure_ascii=False)
    assert "allow_always" in serialized
    assert "owner_id" not in serialized


@pytest.mark.asyncio
async def test_acp_session_grant_is_scoped_and_has_bounded_expiry():
    async def client_request(request):
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "outcome": {
                    "outcome": "selected",
                    "optionId": "nanobot.allow_session",
                }
            },
        }

    port = AcpPermissionPort(
        enablement=_enablement(ACP_FEATURE_ID),
        session_id="acp-session",
        policy=StaticPermissionPort({
            "workspace.write": RuntimePermissionOutcome.ASK,
        }),
        client_request=client_request,
        session_grant_ttl_seconds=120,
    )
    requested_at = datetime.now(timezone.utc)
    decision = await port.evaluate(RuntimePermissionRequest(
        request_id="permission-session",
        identity=_identity(),
        action="workspace.write",
        resource="/workspace/result.md",
        risk=RuntimePermissionRisk.MEDIUM,
        requested_at=requested_at,
        session_id="acp-session",
    ))

    assert decision.outcome is RuntimePermissionOutcome.SESSION_GRANT
    assert decision.grant_id.startswith("acp-session:")
    assert decision.grant_expires_at is not None
    assert 0 < (
        decision.grant_expires_at - decision.decided_at
    ).total_seconds() <= 120
    with pytest.raises(ValueError, match="不属于当前 ACP session"):
        await port.evaluate(RuntimePermissionRequest(
            request_id="permission-other-session",
            identity=_identity(),
            action="workspace.write",
            resource="/workspace/result.md",
            risk=RuntimePermissionRisk.MEDIUM,
            requested_at=requested_at,
            session_id="other-session",
        ))


@pytest.mark.asyncio
async def test_acp_permission_malformed_or_cancelled_response_fails_closed():
    responses = [
        {"jsonrpc": "2.0", "id": "wrong", "result": {}},
        None,
    ]

    async def client_request(request):
        response = responses.pop(0)
        if response is None:
            await asyncio.Future()
        return response

    port = AcpPermissionPort(
        enablement=_enablement(ACP_FEATURE_ID),
        session_id="acp-session",
        policy=StaticPermissionPort(
            {
                "one": RuntimePermissionOutcome.ASK,
                "two": RuntimePermissionOutcome.ASK,
            }
        ),
        client_request=client_request,
    )
    first = RuntimePermissionRequest(
        request_id="permission-invalid",
        identity=_identity(),
        action="one",
        resource="resource",
        risk=RuntimePermissionRisk.HIGH,
        requested_at=datetime.now(timezone.utc),
    )
    assert (await port.evaluate(first)).outcome is RuntimePermissionOutcome.DENY
    second = RuntimePermissionRequest(
        request_id="permission-cancel",
        identity=_identity(),
        action="two",
        resource="resource",
        risk=RuntimePermissionRisk.HIGH,
        requested_at=datetime.now(timezone.utc),
    )
    pending = asyncio.create_task(port.evaluate(second))
    await asyncio.sleep(0)
    assert await port.cancel_pending() == 1
    assert (await pending).outcome is RuntimePermissionOutcome.DENY


def test_a2a_interface_requires_https_exact_allowlist_and_stable_v1():
    with pytest.raises(ValueError, match="HTTPS"):
        A2AInterface(
            url="http://agent.example/rpc",
            allowed_origins=("https://agent.example",),
        )
    with pytest.raises(ValueError, match="allowlist"):
        A2AInterface(
            url="https://agent.example/rpc",
            allowed_origins=("https://other.example",),
        )
    with pytest.raises(ValueError, match="非公网"):
        A2AInterface(
            url="https://127.0.0.1/rpc",
            allowed_origins=("https://127.0.0.1",),
        )
    with pytest.raises(ValueError, match="1.0"):
        A2AInterface(
            url="https://agent.example/rpc",
            allowed_origins=("https://agent.example",),
            protocol_version="0.3",
        )


def test_a2a_experiment_rejects_unbound_remote_task_continuation():
    with pytest.raises(ValueError, match="仅创建新任务"):
        A2AClientRequest(
            identity=_identity(),
            message_id="message-1",
            text="继续",
            task_id="remote-task-not-bound",
        )


class _A2ATransport:
    def __init__(self, interface: A2AInterface, result_factory):
        self._interface = interface
        self.result_factory = result_factory
        self.requests: list[dict[str, object]] = []

    @property
    def interface(self):
        return self._interface

    async def send(self, envelope):
        request = dict(envelope)
        self.requests.append(request)
        return self.result_factory(request)


@pytest.mark.asyncio
async def test_a2a_client_dispatches_fixed_v1_request_and_parses_task_artifacts():
    interface = A2AInterface(
        url="https://agent.example/rpc",
        allowed_origins=("https://agent.example",),
        tenant="trusted-route",
    )

    def response(request):
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "task": {
                    "id": "remote-task-1",
                    "contextId": "remote-context-1",
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "artifacts": [
                        {
                            "artifactId": "remote-artifact-1",
                            "name": "结果",
                            "parts": [
                                {
                                    "text": "完成",
                                    "mediaType": "text/plain",
                                },
                                {
                                    "raw": base64.b64encode(b"raw").decode(),
                                    "mediaType": "application/octet-stream",
                                },
                                {"data": {"ok": True}},
                            ],
                            "metadata": {
                                "owner_id": "attacker",
                                "authorization": "secret",
                            },
                        }
                    ],
                    "metadata": {"run_id": "remote-override"},
                }
            },
        }

    transport = _A2ATransport(interface, response)
    adapter = A2AClientAdapter(
        enablement=_enablement(A2A_FEATURE_ID),
        interface=interface,
        transport=transport,
    )
    identity = _identity(owner_id="trusted-owner")
    exchange = await adapter.send_message(
        A2AClientRequest(
            identity=identity,
            message_id="message-1",
            text="执行远程任务",
        )
    )

    assert exchange.source_identity is identity
    assert exchange.task is not None
    assert exchange.task.task_id == "remote-task-1"
    assert exchange.task.state is A2ATaskState.COMPLETED
    assert [part.kind for part in exchange.task.artifacts[0].parts] == [
        A2APartKind.TEXT,
        A2APartKind.RAW,
        A2APartKind.DATA,
    ]
    assert exchange.task.artifacts[0].parts[1].value == b"raw"
    request = transport.requests[0]
    assert request["method"] == "SendMessage"
    assert request["params"]["tenant"] == "trusted-route"
    assert request["params"]["configuration"] == {
        "acceptedOutputModes": ["text/plain", "application/json"],
        "historyLength": 0,
        "returnImmediately": False,
    }
    serialized = json.dumps(request, ensure_ascii=False)
    assert "trusted-owner" not in serialized
    assert "run-1" not in serialized
    assert "metadata" not in serialized


@pytest.mark.asyncio
async def test_a2a_client_parses_direct_message_without_creating_local_task():
    interface = A2AInterface(
        url="https://agent.example/rpc",
        allowed_origins=("https://agent.example",),
    )
    transport = _A2ATransport(
        interface,
        lambda request: {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "message": {
                    "messageId": "remote-message",
                    "contextId": "remote-context",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": "响应"}],
                }
            },
        },
    )
    exchange = await A2AClientAdapter(
        enablement=_enablement(A2A_FEATURE_ID),
        interface=interface,
        transport=transport,
    ).send_message(
        A2AClientRequest(
            identity=_identity(),
            message_id="message-1",
            text="你好",
        )
    )
    assert exchange.task is None
    assert exchange.message is not None
    assert exchange.message.parts[0].value == "响应"


@pytest.mark.asyncio
async def test_a2a_rejects_remote_errors_identity_override_and_unsafe_artifact_url():
    interface = A2AInterface(
        url="https://agent.example/rpc",
        allowed_origins=("https://agent.example",),
    )
    responses: list[dict[str, object]] = []

    def response(request):
        value = responses.pop(0)
        value["id"] = request["id"]
        return value

    transport = _A2ATransport(interface, response)
    adapter = A2AClientAdapter(
        enablement=_enablement(A2A_FEATURE_ID),
        interface=interface,
        transport=transport,
    )
    request = A2AClientRequest(
        identity=_identity(),
        message_id="message-1",
        text="执行",
    )
    responses.append(
        {
            "jsonrpc": "2.0",
            "error": {"code": -32000, "message": "token=remote-secret"},
        }
    )
    with pytest.raises(A2AProtocolError) as error:
        await adapter.send_message(request)
    assert "remote-secret" not in str(error.value)

    responses.append(
        {
            "jsonrpc": "2.0",
            "result": {
                "task": {
                    "id": "task",
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "artifacts": [
                        {
                            "artifactId": "artifact",
                            "parts": [
                                {"url": ("https://files.example/result?token=secret")}
                            ],
                        }
                    ],
                }
            },
        }
    )
    with pytest.raises(A2AProtocolError, match="查询"):
        await adapter.send_message(request)

    responses.append(
        {
            "jsonrpc": "2.0",
            "result": {
                "task": {
                    "id": "task",
                    "status": {"state": "TASK_STATE_WORKING"},
                }
            },
        }
    )
    with pytest.raises(A2AProtocolError, match="非终态"):
        await adapter.send_message(request)


@pytest.mark.asyncio
async def test_https_a2a_transport_sends_auth_once_without_leaking_it():
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request):
        calls.append(request)
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"message": {}},
            },
        )

    interface = A2AInterface(
        url="https://agent.example/rpc",
        allowed_origins=("https://agent.example",),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpsA2AJsonRpcTransport(
        interface=interface,
        authorization_provider=lambda: "Bearer top-secret-token",
        client=client,
    )
    response = await transport.send(
        {"jsonrpc": "2.0", "id": "rpc-1", "method": "SendMessage"}
    )
    assert response["id"] == "rpc-1"
    assert len(calls) == 1
    assert calls[0].headers["a2a-version"] == "1.0"
    assert calls[0].headers["authorization"] == "Bearer top-secret-token"
    await client.aclose()


@pytest.mark.asyncio
async def test_https_a2a_transport_forbids_redirect_and_never_retries():
    calls = 0

    async def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            307,
            headers={"Location": "https://attacker.example/steal"},
        )

    interface = A2AInterface(
        url="https://agent.example/rpc",
        allowed_origins=("https://agent.example",),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpsA2AJsonRpcTransport(
        interface=interface,
        authorization_provider=lambda: "Bearer do-not-leak",
        client=client,
    )
    with pytest.raises(A2ATransportError) as error:
        await transport.send({"jsonrpc": "2.0", "id": "1"})
    assert error.value.code == "REDIRECT_FORBIDDEN"
    assert "do-not-leak" not in str(error.value)
    assert calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_https_a2a_transport_sanitizes_credential_provider_failure():
    calls = 0

    async def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    def credential_provider():
        raise RuntimeError("secret-provider-token")

    interface = A2AInterface(
        url="https://agent.example/rpc",
        allowed_origins=("https://agent.example",),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpsA2AJsonRpcTransport(
        interface=interface,
        authorization_provider=credential_provider,
        client=client,
    )
    with pytest.raises(A2ATransportError) as error:
        await transport.send({"jsonrpc": "2.0", "id": "1"})
    assert error.value.code == "CREDENTIAL_UNAVAILABLE"
    assert "secret-provider-token" not in str(error.value)
    assert calls == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_https_a2a_transport_rejects_duplicate_json_and_bounded_response():
    responses = [
        b'{"jsonrpc":"2.0","id":"1","id":"2"}',
        b"x" * 65,
    ]

    async def handler(_request: httpx.Request):
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=responses.pop(0),
        )

    interface = A2AInterface(
        url="https://agent.example/rpc",
        allowed_origins=("https://agent.example",),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpsA2AJsonRpcTransport(
        interface=interface,
        limits=A2ATransportLimits(max_response_bytes=64),
        client=client,
    )
    with pytest.raises(A2ATransportError) as duplicate:
        await transport.send({"jsonrpc": "2.0", "id": "1"})
    assert duplicate.value.code == "RESPONSE_JSON_INVALID"
    with pytest.raises(A2ATransportError) as oversized:
        await transport.send({"jsonrpc": "2.0", "id": "2"})
    assert oversized.value.code == "RESPONSE_TOO_LARGE"
    await client.aclose()


@pytest.mark.asyncio
async def test_headless_executes_real_runtime_after_authoritative_handler():
    runtime = FakeAgentRuntime()
    runtime.queue_text_deltas("真实", "输出")
    runtime.queue_result(
        AgentTurnResult(
            raw_result={"secret": "internal-only"},
            messages=(RuntimeMessage("assistant", "真实输出"),),
        )
    )
    await runtime.start()
    committed = []

    async def authoritative_handler(event):
        committed.append(event)

    adapter = HeadlessRuntimeAdapter(
        enablement=_enablement(HEADLESS_FEATURE_ID),
        runtime=runtime,
        authoritative_event_handler=authoritative_handler,
    )
    request = AgentTurnRequest(context=_context(), content="执行")
    output = await adapter.execute(request)

    assert runtime.requests == [request]
    assert output.events == tuple(committed)
    assert output.result.raw_result == {"secret": "internal-only"}
    assert output.evidence.terminal_status is RuntimeRunStatus.SUCCEEDED
    assert output.evidence.event_count == len(committed)
    assert output.evidence.text_bytes == len("真实输出".encode())
    assert len(output.evidence.event_sha256) == 64
    assert "internal-only" not in json.dumps(asdict(output.evidence), default=str)
    await runtime.stop()


@pytest.mark.asyncio
async def test_headless_requires_running_runtime_and_enabled_feature():
    runtime = FakeAgentRuntime()
    with pytest.raises(InteroperabilityDisabledError):
        HeadlessRuntimeAdapter(
            enablement=_disabled(HEADLESS_FEATURE_ID),
            runtime=runtime,
            authoritative_event_handler=lambda _event: None,
        )
    adapter = HeadlessRuntimeAdapter(
        enablement=_enablement(HEADLESS_FEATURE_ID),
        runtime=runtime,
        authoritative_event_handler=lambda _event: None,
    )
    with pytest.raises(HeadlessExecutionError, match="running"):
        await adapter.execute(AgentTurnRequest(context=_context(), content="执行"))


@pytest.mark.asyncio
async def test_headless_limit_fails_after_authoritative_event_and_interrupts_runtime():
    runtime = FakeAgentRuntime()
    runtime.queue_text_deltas("a", "b")
    await runtime.start()
    committed = []
    adapter = HeadlessRuntimeAdapter(
        enablement=_enablement(HEADLESS_FEATURE_ID),
        runtime=runtime,
        authoritative_event_handler=lambda event: committed.append(event),
        limits=HeadlessLimits(max_events=2, max_text_bytes=10),
    )
    with pytest.raises(HeadlessExecutionError, match="事件超过"):
        await adapter.execute(AgentTurnRequest(context=_context(), content="执行"))
    assert len(committed) == 3
    assert runtime.interrupt_reasons == ["headless_contract_failure"]
    await runtime.stop()


@pytest.mark.asyncio
async def test_headless_cancel_targets_only_current_bound_run():
    runtime = _BlockingRuntime()
    await runtime.start()
    adapter = HeadlessRuntimeAdapter(
        enablement=_enablement(HEADLESS_FEATURE_ID),
        runtime=runtime,
        authoritative_event_handler=lambda _event: None,
    )
    task = asyncio.create_task(
        adapter.execute(AgentTurnRequest(context=_context(), content="等待"))
    )
    await runtime.started.wait()
    assert await adapter.cancel("wrong-run") is False
    assert await adapter.cancel("run-1") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.interrupt_reasons == ["headless_cancel"]
    await runtime.stop()


def test_interoperability_package_stays_outside_agent_link_and_has_no_server_route():
    root = Path(__file__).resolve().parents[1] / "core" / "interoperability"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.glob("*.py"))
    )
    assert "core.agent_link" not in source
    assert "FastAPI" not in source
    assert "APIRouter" not in source
    assert "pushNotification" not in source
    assert "SendStreamingMessage" not in source
