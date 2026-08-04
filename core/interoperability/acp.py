"""ACP v1 到 Nanobot Runtime 的默认关闭薄 Adapter。

本模块锁定 ACP 稳定 wire version 1。它不启动监听器、不读取认证信息，也不把
ACP session 变成新的事实库；每个显式创建的 session 拥有一个全新的
``AgentRuntimePort``，流式输出直接由 Nanobot ``RuntimeRunEvent`` 投影。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
from typing import Any, Protocol
from urllib.parse import urlsplit
import uuid

from core.agent_runtime.contracts import (
    AgentRuntimePort,
    AgentTurnRequest,
    RequestRuntimeContext,
    RuntimeCapability,
    RuntimeLifecycleState,
    RuntimeRunEvent,
    RuntimeRunEventKind,
    RuntimeRunStatus,
    RuntimeToolCallStatus,
)
from core.agent_runtime.service_ports import (
    PermissionPort,
    RuntimePermissionDecision,
    RuntimePermissionOutcome,
    RuntimePermissionRequest,
)
from core.interoperability.contracts import (
    InteroperabilityError,
    require_interoperability_enabled,
)
from core.lifecycle import FeatureEnablementDecision


ACP_FEATURE_ID = "interoperability.acp_v1"
ACP_PROTOCOL_VERSION = 1
_JSONRPC_VERSION = "2.0"
_MAX_IDENTIFIER_CHARS = 256


class AcpProtocolError(InteroperabilityError):
    """可安全返回给 ACP transport 的协议错误。"""

    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        rpc_code: int = -32602,
    ) -> None:
        self.rpc_code = rpc_code
        super().__init__(code, safe_message)


class AcpRuntimeFactory(Protocol):
    def __call__(
        self, session_id: str
    ) -> AgentRuntimePort | Awaitable[AgentRuntimePort]: ...


class AcpContextFactory(Protocol):
    def __call__(
        self,
        session_id: str,
        turn_index: int,
    ) -> RequestRuntimeContext | Awaitable[RequestRuntimeContext]: ...


AcpNotificationSink = Callable[
    [Mapping[str, object]],
    Awaitable[None] | None,
]
AcpClientRequest = Callable[
    [Mapping[str, object]],
    Awaitable[Mapping[str, object]],
]


def _required_identifier(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise AcpProtocolError("INVALID_PARAMS", f"{name} 必须是字符串")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > _MAX_IDENTIFIER_CHARS
        or any(character in normalized for character in "\r\n\x00")
    ):
        raise AcpProtocolError("INVALID_PARAMS", f"{name} 非法")
    return normalized


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AcpProtocolError("INVALID_PARAMS", f"{name} 必须是对象")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise AcpProtocolError("INVALID_PARAMS", f"{name} 必须是数组")
    return value


async def _resolve_awaitable(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass(frozen=True, slots=True)
class AcpAdapterConfig:
    """只描述协议边界，不包含凭据、宿主路径或 MCP 配置。"""

    virtual_cwd: str = "/workspace"
    max_sessions: int = 4
    max_prompt_bytes: int = 1024 * 1024
    max_updates_per_turn: int = 4096
    context_window_tokens: int = 0
    allowed_resource_schemes: tuple[str, ...] = (
        "artifact",
        "asset",
        "https",
    )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.virtual_cwd, str)
            or not self.virtual_cwd.startswith("/")
            or self.virtual_cwd != self.virtual_cwd.strip()
            or ".." in self.virtual_cwd.split("/")
        ):
            raise ValueError("ACP virtual_cwd 必须是规范绝对虚拟路径")
        for name in (
            "max_sessions",
            "max_prompt_bytes",
            "max_updates_per_turn",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"ACP {name} 必须是正整数")
        if (
            type(self.context_window_tokens) is not int
            or self.context_window_tokens < 0
        ):
            raise ValueError("ACP context_window_tokens 必须是非负整数")
        schemes = tuple(
            dict.fromkeys(
                str(item or "").strip().lower()
                for item in self.allowed_resource_schemes
                if str(item or "").strip()
            )
        )
        if not schemes or any(not item.isascii() for item in schemes):
            raise ValueError("ACP allowed_resource_schemes 无效")
        object.__setattr__(self, "allowed_resource_schemes", schemes)


@dataclass(slots=True)
class _AcpSession:
    session_id: str
    runtime: AgentRuntimePort
    turn_index: int = 0
    active_task: asyncio.Task[Any] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _resource_link_text(
    block: Mapping[str, object],
    *,
    config: AcpAdapterConfig,
) -> str:
    name = _required_identifier(block.get("name"), "resource_link.name")
    uri = _required_identifier(block.get("uri"), "resource_link.uri")
    if len(uri) > 2048:
        raise AcpProtocolError("INVALID_RESOURCE_LINK", "资源 URI 过长")
    parsed = urlsplit(uri)
    if parsed.scheme.lower() not in config.allowed_resource_schemes:
        raise AcpProtocolError(
            "UNSUPPORTED_RESOURCE_LINK",
            "资源 URI scheme 未获允许",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AcpProtocolError(
            "UNSAFE_RESOURCE_LINK",
            "资源 URI 不得携带凭据、查询或片段",
        )
    return (
        "[ACP 资源引用；仅作为引用文本，不代表已授予读取权限]\n"
        f"名称：{name}\nURI：{uri}"
    )


def _prompt_text(params: Mapping[str, object], config: AcpAdapterConfig) -> str:
    blocks = _sequence(params.get("prompt"), "prompt")
    if not blocks:
        raise AcpProtocolError("INVALID_PROMPT", "prompt 不能为空")
    parts: list[str] = []
    for raw_block in blocks:
        block = _mapping(raw_block, "prompt block")
        block_type = str(block.get("type") or "").strip()
        if block_type == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise AcpProtocolError(
                    "INVALID_PROMPT",
                    "text block 缺少字符串 text",
                )
            parts.append(text)
        elif block_type == "resource_link":
            parts.append(_resource_link_text(block, config=config))
        else:
            raise AcpProtocolError(
                "UNSUPPORTED_CONTENT",
                "当前 ACP Adapter 仅接受 text 和 resource_link",
            )
    content = "\n\n".join(parts)
    if not content.strip():
        raise AcpProtocolError("INVALID_PROMPT", "prompt 文本不能为空")
    if len(content.encode("utf-8")) > config.max_prompt_bytes:
        raise AcpProtocolError("PROMPT_TOO_LARGE", "prompt 超过大小上限")
    return content


class AcpAgentAdapter:
    """由宿主 transport 显式调用的 ACP v1 Agent 方法集合。"""

    def __init__(
        self,
        *,
        enablement: FeatureEnablementDecision,
        runtime_factory: AcpRuntimeFactory,
        context_factory: AcpContextFactory,
        notification_sink: AcpNotificationSink,
        config: AcpAdapterConfig = AcpAdapterConfig(),
    ) -> None:
        require_interoperability_enabled(
            enablement,
            feature_id=ACP_FEATURE_ID,
        )
        if not callable(runtime_factory):
            raise TypeError("runtime_factory 必须可调用")
        if not callable(context_factory):
            raise TypeError("context_factory 必须可调用")
        if not callable(notification_sink):
            raise TypeError("notification_sink 必须可调用")
        if not isinstance(config, AcpAdapterConfig):
            raise TypeError("config 必须是 AcpAdapterConfig")
        self._runtime_factory = runtime_factory
        self._context_factory = context_factory
        self._notification_sink = notification_sink
        self._config = config
        self._initialized = False
        self._sessions: dict[str, _AcpSession] = {}
        self._runtime_instances: set[int] = set()
        self._lock = asyncio.Lock()

    @property
    def active_session_count(self) -> int:
        return len(self._sessions)

    async def handle_request(
        self,
        method: str,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        """执行一个已经由 transport 解码并认证的 ACP 方法。"""

        handlers = {
            "initialize": self.initialize,
            "session/new": self.new_session,
            "session/prompt": self.prompt,
            "session/cancel": self.cancel,
            "session/close": self.close_session,
        }
        handler = handlers.get(str(method or "").strip())
        if handler is None:
            raise AcpProtocolError(
                "METHOD_NOT_FOUND",
                "ACP 方法未实现或未开放",
                rpc_code=-32601,
            )
        return await handler(_mapping(params, "params"))

    async def initialize(
        self,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        version = params.get("protocolVersion")
        if type(version) is not int or version != ACP_PROTOCOL_VERSION:
            raise AcpProtocolError(
                "UNSUPPORTED_VERSION",
                "仅支持 ACP 稳定 wire version 1",
            )
        capabilities = params.get("clientCapabilities", {})
        if not isinstance(capabilities, Mapping):
            raise AcpProtocolError(
                "INVALID_PARAMS",
                "clientCapabilities 必须是对象",
            )
        self._initialized = True
        return {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": False,
                },
                "mcpCapabilities": {"http": False, "sse": False},
                "sessionCapabilities": {"close": {}},
                "auth": {},
            },
            "authMethods": [],
            "agentInfo": {
                "name": "Nanobot ACP Adapter",
                "version": "1.0.0-experimental",
            },
        }

    async def new_session(
        self,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        self._require_initialized()
        cwd = params.get("cwd")
        if cwd != self._config.virtual_cwd:
            raise AcpProtocolError(
                "WORKSPACE_NOT_ALLOWED",
                "ACP cwd 必须等于宿主分配的虚拟工作区",
            )
        mcp_servers = _sequence(params.get("mcpServers"), "mcpServers")
        if mcp_servers:
            raise AcpProtocolError(
                "MCP_OVERRIDE_FORBIDDEN",
                "ACP session 不得覆盖宿主 MCP 计划",
            )
        directories = params.get("additionalDirectories", [])
        if _sequence(directories, "additionalDirectories"):
            raise AcpProtocolError(
                "WORKSPACE_OVERRIDE_FORBIDDEN",
                "ACP session 不得扩展宿主工作区范围",
            )

        async with self._lock:
            if len(self._sessions) >= self._config.max_sessions:
                raise AcpProtocolError(
                    "SESSION_LIMIT",
                    "ACP 活跃 session 已达到上限",
                )
            session_id = f"acp-{uuid.uuid4().hex}"
            runtime = await _resolve_awaitable(self._runtime_factory(session_id))
            if not isinstance(runtime, AgentRuntimePort):
                raise AcpProtocolError(
                    "RUNTIME_CONTRACT_ERROR",
                    "Runtime factory 未返回 AgentRuntimePort",
                )
            if id(runtime) in self._runtime_instances:
                raise AcpProtocolError(
                    "RUNTIME_REUSE_FORBIDDEN",
                    "ACP session 必须使用独立 Runtime 实例",
                )
            if runtime.state is not RuntimeLifecycleState.NEW:
                raise AcpProtocolError(
                    "RUNTIME_OWNERSHIP_ERROR",
                    "ACP session 只能接管全新的 Runtime",
                )
            if not runtime.runtime_capabilities.supports(RuntimeCapability.RUN_EVENT):
                raise AcpProtocolError(
                    "RUNTIME_CAPABILITY_MISSING",
                    "Runtime 不支持事件化执行",
                )
            try:
                await runtime.start()
            except Exception as exc:
                raise AcpProtocolError(
                    "RUNTIME_START_FAILED",
                    "ACP Runtime 启动失败",
                ) from exc
            if runtime.state is not RuntimeLifecycleState.RUNNING:
                raise AcpProtocolError(
                    "RUNTIME_START_FAILED",
                    "ACP Runtime 启动后未进入 running",
                )
            self._runtime_instances.add(id(runtime))
            self._sessions[session_id] = _AcpSession(
                session_id=session_id,
                runtime=runtime,
            )
        return {"sessionId": session_id}

    async def prompt(
        self,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        self._require_initialized()
        session = self._require_session(params.get("sessionId"))
        content = _prompt_text(params, self._config)
        current_task = asyncio.current_task()
        if current_task is None:
            raise AcpProtocolError(
                "RUNTIME_CONTEXT_ERROR",
                "ACP prompt 必须在异步任务中执行",
            )
        async with session.lock:
            if session.active_task is not None:
                raise AcpProtocolError(
                    "SESSION_BUSY",
                    "同一 ACP session 只能执行一个 prompt",
                )
            session.turn_index += 1
            turn_index = session.turn_index
            session.active_task = current_task

        try:
            context = await _resolve_awaitable(
                self._context_factory(session.session_id, turn_index)
            )
        except Exception as exc:
            await self._clear_active_task(session, current_task)
            raise AcpProtocolError(
                "CONTEXT_FACTORY_FAILED",
                "ACP 请求上下文构造失败",
                rpc_code=-32603,
            ) from exc
        if not isinstance(context, RequestRuntimeContext):
            await self._clear_active_task(session, current_task)
            raise AcpProtocolError(
                "CONTEXT_CONTRACT_ERROR",
                "context factory 未返回 RequestRuntimeContext",
            )
        if context.session_id != session.session_id:
            await self._clear_active_task(session, current_task)
            raise AcpProtocolError(
                "CONTEXT_BINDING_ERROR",
                "宿主 context 与 ACP session 绑定不一致",
            )
        request = AgentTurnRequest(
            context=context,
            content=content,
            stream=True,
        )
        projection = _AcpTurnProjection(
            session_id=session.session_id,
            sink=self._notification_sink,
            config=self._config,
        )
        try:
            await session.runtime.run_event(request, projection.handle)
        except asyncio.CancelledError:
            return {"stopReason": "cancelled"}
        except AcpProtocolError:
            raise
        except Exception as exc:
            raise AcpProtocolError(
                "RUNTIME_EXECUTION_FAILED",
                "ACP prompt 执行失败",
                rpc_code=-32603,
            ) from exc
        finally:
            await self._clear_active_task(session, current_task)

        if projection.terminal_status is RuntimeRunStatus.SUCCEEDED:
            return {"stopReason": "end_turn"}
        if projection.terminal_status is RuntimeRunStatus.CANCELLED:
            return {"stopReason": "cancelled"}
        if projection.terminal_status is None:
            raise AcpProtocolError(
                "INCOMPLETE_EVENT_STREAM",
                "Runtime 没有产生终态事件",
                rpc_code=-32603,
            )
        raise AcpProtocolError(
            "RUNTIME_TERMINAL_FAILURE",
            "Runtime 以非成功终态结束",
            rpc_code=-32603,
        )

    async def cancel(
        self,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        self._require_initialized()
        session = self._require_session(params.get("sessionId"))
        async with session.lock:
            task = session.active_task
            if task is None:
                return {}
            try:
                session.runtime.interrupt(reason="acp_session_cancel")
            except Exception:
                pass
            task.cancel()
        return {}

    async def close_session(
        self,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        self._require_initialized()
        session_id = _required_identifier(
            params.get("sessionId"),
            "sessionId",
        )
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise AcpProtocolError("SESSION_NOT_FOUND", "ACP session 不存在")
        async with session.lock:
            task = session.active_task
            if task is not None:
                try:
                    session.runtime.interrupt(reason="acp_session_close")
                except Exception:
                    pass
                task.cancel()
        if task is not None and task is not asyncio.current_task():
            with suppress(asyncio.CancelledError, AcpProtocolError):
                await task
        try:
            await session.runtime.stop()
        except Exception as exc:
            raise AcpProtocolError(
                "RUNTIME_STOP_FAILED",
                "ACP Runtime 关闭失败",
            ) from exc
        finally:
            if session.runtime.state is RuntimeLifecycleState.STOPPED:
                async with self._lock:
                    if self._sessions.get(session_id) is session:
                        self._sessions.pop(session_id, None)
                self._runtime_instances.discard(id(session.runtime))
        return {}

    async def close_all(self) -> None:
        """供宿主 transport 关闭时显式释放所有 session。"""

        session_ids = tuple(self._sessions)
        failures = 0
        for session_id in session_ids:
            try:
                await self.close_session({"sessionId": session_id})
            except AcpProtocolError:
                failures += 1
        if failures:
            raise AcpProtocolError(
                "RUNTIME_STOP_FAILED",
                "部分 ACP Runtime 关闭失败",
            )

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise AcpProtocolError(
                "NOT_INITIALIZED",
                "必须先完成 ACP initialize",
            )

    def _require_session(self, value: object) -> _AcpSession:
        session_id = _required_identifier(value, "sessionId")
        session = self._sessions.get(session_id)
        if session is None:
            raise AcpProtocolError("SESSION_NOT_FOUND", "ACP session 不存在")
        return session

    @staticmethod
    async def _clear_active_task(
        session: _AcpSession,
        task: asyncio.Task[Any],
    ) -> None:
        async with session.lock:
            if session.active_task is task:
                session.active_task = None


class _AcpTurnProjection:
    """瞬时投影，不缓存正文，也不成为第二份事件日志。"""

    def __init__(
        self,
        *,
        session_id: str,
        sink: AcpNotificationSink,
        config: AcpAdapterConfig,
    ) -> None:
        self._session_id = session_id
        self._sink = sink
        self._config = config
        self._updates = 0
        self._tool_calls: set[str] = set()
        self._identity = None
        self.terminal_status: RuntimeRunStatus | None = None

    async def handle(self, event: RuntimeRunEvent) -> None:
        if not isinstance(event, RuntimeRunEvent):
            raise AcpProtocolError(
                "RUNTIME_EVENT_INVALID",
                "Runtime 产生了无效事件",
                rpc_code=-32603,
            )
        if self._identity is None:
            self._identity = event.identity
        elif event.identity != self._identity:
            raise AcpProtocolError(
                "RUNTIME_IDENTITY_CHANGED",
                "Runtime 在同一 prompt 中切换了身份",
                rpc_code=-32603,
            )

        if event.kind is RuntimeRunEventKind.TEXT_DELTA:
            await self._emit(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": event.text_delta},
                    "messageId": f"{event.run_id}:{event.turn_id}:assistant",
                }
            )
        elif event.kind is RuntimeRunEventKind.TOOL_ACTIVITY:
            await self._tool_activity(event)
        elif event.kind is RuntimeRunEventKind.USAGE:
            if self._config.context_window_tokens > 0 and event.usage is not None:
                await self._emit(
                    {
                        "sessionUpdate": "usage_update",
                        "used": min(
                            event.usage.total_tokens,
                            self._config.context_window_tokens,
                        ),
                        "size": self._config.context_window_tokens,
                    }
                )
        elif event.kind is RuntimeRunEventKind.ARTIFACT:
            if event.artifact is not None:
                await self._emit(
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {
                            "type": "resource_link",
                            "name": event.artifact.artifact_id,
                            "uri": event.artifact.uri,
                            "mimeType": event.artifact.media_type,
                            "size": event.artifact.size_bytes,
                        },
                        "messageId": f"{event.run_id}:{event.turn_id}:assistant",
                    }
                )
        elif event.kind is RuntimeRunEventKind.END:
            self.terminal_status = event.status

    async def _tool_activity(self, event: RuntimeRunEvent) -> None:
        call = event.tool_call
        if call is None:
            return
        status = {
            RuntimeToolCallStatus.REQUESTED: "pending",
            RuntimeToolCallStatus.RUNNING: "in_progress",
            RuntimeToolCallStatus.COMPLETED: "completed",
            RuntimeToolCallStatus.FAILED: "failed",
            RuntimeToolCallStatus.CANCELLED: "failed",
            RuntimeToolCallStatus.TIMED_OUT: "failed",
            RuntimeToolCallStatus.AMBIGUOUS: "failed",
        }[call.status]
        if call.call_id not in self._tool_calls:
            self._tool_calls.add(call.call_id)
            update: dict[str, object] = {
                "sessionUpdate": "tool_call",
                "toolCallId": call.call_id,
                "title": call.name,
                "kind": "other",
                "status": status,
            }
        else:
            update = {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call.call_id,
                "status": status,
            }
        await self._emit(update)

    async def _emit(self, update: Mapping[str, object]) -> None:
        self._updates += 1
        if self._updates > self._config.max_updates_per_turn:
            raise AcpProtocolError(
                "UPDATE_LIMIT",
                "ACP session/update 超过单轮上限",
                rpc_code=-32603,
            )
        notification = {
            "jsonrpc": _JSONRPC_VERSION,
            "method": "session/update",
            "params": {
                "sessionId": self._session_id,
                "update": dict(update),
            },
        }
        handled = self._sink(notification)
        if inspect.isawaitable(handled):
            await handled


class AcpPermissionPort:
    """把内部 ``ask`` 决策映射为有界 ACP pending interaction。

    应由宿主放在权威 ``LedgeredPermissionPort`` 内层；本 Adapter 不自行持久化
    决策，也不提供 ``allow_always``，避免在阶段 7.1 前制造隐式 session grant。
    """

    def __init__(
        self,
        *,
        enablement: FeatureEnablementDecision,
        session_id: str,
        policy: PermissionPort,
        client_request: AcpClientRequest,
        timeout_seconds: float = 60.0,
        max_pending: int = 8,
    ) -> None:
        require_interoperability_enabled(
            enablement,
            feature_id=ACP_FEATURE_ID,
        )
        self._session_id = _required_identifier(session_id, "sessionId")
        if not isinstance(policy, PermissionPort):
            raise TypeError("policy 必须是 PermissionPort")
        if not callable(client_request):
            raise TypeError("client_request 必须可调用")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or float(timeout_seconds) <= 0
        ):
            raise ValueError("timeout_seconds 必须是正数")
        if type(max_pending) is not int or max_pending <= 0:
            raise ValueError("max_pending 必须是正整数")
        self._policy = policy
        self._client_request = client_request
        self._timeout_seconds = float(timeout_seconds)
        self._max_pending = max_pending
        self._requests: dict[str, RuntimePermissionRequest] = {}
        self._decisions: dict[str, RuntimePermissionDecision] = {}
        self._pending: dict[
            str,
            asyncio.Task[RuntimePermissionDecision],
        ] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def evaluate(
        self,
        request: RuntimePermissionRequest,
    ) -> RuntimePermissionDecision:
        if not isinstance(request, RuntimePermissionRequest):
            raise TypeError("request 必须是 RuntimePermissionRequest")
        policy_decision = await self._policy.evaluate(request)
        if policy_decision.outcome is not RuntimePermissionOutcome.ASK:
            return policy_decision

        async with self._lock:
            existing_request = self._requests.get(request.request_id)
            if existing_request is not None and existing_request != request:
                raise ValueError("permission request_id 已绑定不同请求")
            cached = self._decisions.get(request.request_id)
            if cached is not None:
                return cached
            task = self._pending.get(request.request_id)
            if task is None:
                if len(self._pending) >= self._max_pending:
                    return self._deny(request, "acp_pending_limit")
                self._requests[request.request_id] = request
                self._cancel_events[request.request_id] = asyncio.Event()
                task = asyncio.create_task(self._resolve(request))
                self._pending[request.request_id] = task
        decision = await task
        async with self._lock:
            self._pending.pop(request.request_id, None)
            self._cancel_events.pop(request.request_id, None)
            self._decisions[request.request_id] = decision
        return decision

    async def cancel_pending(self) -> int:
        """取消当前 session 的所有交互；最终一律映射为 deny。"""

        async with self._lock:
            events = tuple(self._cancel_events.values())
            for event in events:
                event.set()
        return len(events)

    async def _resolve(
        self,
        request: RuntimePermissionRequest,
    ) -> RuntimePermissionDecision:
        rpc_id = f"permission-{uuid.uuid4().hex}"
        wire_request = {
            "jsonrpc": _JSONRPC_VERSION,
            "id": rpc_id,
            "method": "session/request_permission",
            "params": {
                "sessionId": self._session_id,
                "toolCall": {
                    "toolCallId": request.request_id,
                    "title": request.action,
                    "kind": "other",
                    "status": "pending",
                    "content": [
                        {
                            "type": "content",
                            "content": {
                                "type": "text",
                                "text": (
                                    f"资源：{request.resource[:1024]}\n"
                                    f"风险：{request.risk.value}"
                                ),
                            },
                        }
                    ],
                },
                "options": [
                    {
                        "optionId": "nanobot.allow_once",
                        "name": "仅允许本次",
                        "kind": "allow_once",
                    },
                    {
                        "optionId": "nanobot.reject_once",
                        "name": "拒绝本次",
                        "kind": "reject_once",
                    },
                ],
            },
        }
        cancel_event = self._cancel_events[request.request_id]

        async def invoke_client() -> Mapping[str, object]:
            response = self._client_request(wire_request)
            if inspect.isawaitable(response):
                return await response
            return response

        client_task = asyncio.create_task(invoke_client())
        cancel_task = asyncio.create_task(cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                {client_task, cancel_task},
                timeout=self._timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done:
                client_task.cancel()
                with suppress(asyncio.CancelledError):
                    await client_task
                return self._deny(request, "acp_client_cancelled")
            if client_task not in done:
                client_task.cancel()
                with suppress(asyncio.CancelledError):
                    await client_task
                return self._deny(request, "acp_client_timeout")
            try:
                response = client_task.result()
            except Exception:
                return self._deny(request, "acp_client_failed")
        except asyncio.CancelledError:
            client_task.cancel()
            with suppress(asyncio.CancelledError):
                await client_task
            raise
        finally:
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task
        try:
            response_map = _mapping(response, "permission response")
            if response_map.get("jsonrpc") != _JSONRPC_VERSION:
                raise AcpProtocolError("INVALID_RESPONSE", "JSON-RPC 版本无效")
            if response_map.get("id") != rpc_id or "error" in response_map:
                raise AcpProtocolError("INVALID_RESPONSE", "JSON-RPC 响应无效")
            result = _mapping(response_map.get("result"), "permission result")
            outcome = _mapping(result.get("outcome"), "permission outcome")
            outcome_type = outcome.get("outcome")
            if outcome_type == "cancelled":
                return self._deny(request, "acp_client_cancelled")
            if outcome_type != "selected":
                raise AcpProtocolError("INVALID_RESPONSE", "permission outcome 无效")
            option_id = outcome.get("optionId")
            if option_id == "nanobot.allow_once":
                return RuntimePermissionDecision(
                    decision_id=f"acp:{rpc_id}",
                    request_id=request.request_id,
                    outcome=RuntimePermissionOutcome.ALLOW_ONCE,
                    reason="acp_client_allow_once",
                    decided_at=datetime.now(timezone.utc),
                    grant_id=f"acp-once:{request.request_id}",
                )
            if option_id == "nanobot.reject_once":
                return self._deny(request, "acp_client_reject_once")
        except (AcpProtocolError, TypeError, ValueError):
            return self._deny(request, "acp_client_invalid")
        return self._deny(request, "acp_client_invalid")

    @staticmethod
    def _deny(
        request: RuntimePermissionRequest,
        reason: str,
    ) -> RuntimePermissionDecision:
        return RuntimePermissionDecision(
            decision_id=f"acp-deny:{request.request_id}:{reason}",
            request_id=request.request_id,
            outcome=RuntimePermissionOutcome.DENY,
            reason=reason,
            decided_at=datetime.now(timezone.utc),
        )


__all__ = [
    "ACP_FEATURE_ID",
    "ACP_PROTOCOL_VERSION",
    "AcpAdapterConfig",
    "AcpAgentAdapter",
    "AcpPermissionPort",
    "AcpProtocolError",
]
