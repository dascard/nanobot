"""Agent Link v1 会话、聊天幂等和前端工具反向调用运行时。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from config import (
    NANOBOT_AGENT_LINK_CHAT_TIMEOUT_SECONDS,
    NANOBOT_AGENT_LINK_MAX_ACTIVE_CHATS,
    NANOBOT_AGENT_LINK_MAX_INLINE_ATTACHMENT_BYTES,
    NANOBOT_AGENT_LINK_MAX_PENDING_TOOLS,
    NANOBOT_AGENT_LINK_MAX_TERMINAL_CHATS,
    NANOBOT_AGENT_LINK_MAX_TOOLS,
    NANOBOT_AGENT_LINK_TOOL_TIMEOUT_SECONDS,
)
from core.agent_link.protocol import (
    AgentLinkFrame,
    AgentLinkProtocolError,
    make_agent_link_frame,
)


logger = logging.getLogger("nanobot.agent_link")

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ALLOWED_IMAGE_MEDIA_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)

SendFrame = Callable[[Mapping[str, object]], Awaitable[None]]
CloseTransport = Callable[[int, str], Awaitable[None]]


class AgentLinkToolFailure(RuntimeError):
    """前端工具调用的稳定失败结果。"""

    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(safe_message)
        self.code = str(code or "EXECUTION_FAILED")
        self.safe_message = str(safe_message or "MeaPet 工具执行失败。")
        self.retryable = bool(retryable)

    def to_payload(self) -> dict[str, object]:
        return {
            "status": "failed",
            "code": self.code,
            "safe_message": self.safe_message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class AgentLinkSessionKey:
    """用设备和 Agent 会话共同隔离连接，避免跨桌面实例串线。"""

    device_id: str
    session_id: str

    @property
    def bridge_session_id(self) -> str:
        digest = hashlib.sha256(
            f"{self.device_id}\x00{self.session_id}".encode("utf-8")
        ).hexdigest()[:32]
        return f"agent_link:{digest}"

    @property
    def bridge_user_id(self) -> str:
        digest = hashlib.sha256(self.device_id.encode("utf-8")).hexdigest()[:24]
        return f"agent_link:{digest}"


@dataclass(frozen=True, slots=True)
class AgentLinkToolDefinition:
    """MeaPet 公布的一个动态工具定义。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    result_modalities: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: object) -> "AgentLinkToolDefinition":
        if not isinstance(raw, Mapping):
            raise AgentLinkProtocolError(
                "INVALID_TOOL_SNAPSHOT",
                "Agent Link 工具定义必须是对象",
            )
        name = str(raw.get("name") or "").strip()
        if (
            not name
            or len(name) > 128
            or _TOOL_NAME_RE.fullmatch(name) is None
        ):
            raise AgentLinkProtocolError(
                "INVALID_TOOL_SNAPSHOT",
                "Agent Link 工具名称非法",
            )
        description = str(raw.get("description") or "").strip()
        if not description or len(description) > 4000:
            raise AgentLinkProtocolError(
                "INVALID_TOOL_SNAPSHOT",
                f"Agent Link 工具 {name} 缺少有效说明",
            )
        input_schema = raw.get("input_schema")
        if not isinstance(input_schema, Mapping):
            raise AgentLinkProtocolError(
                "INVALID_TOOL_SNAPSHOT",
                f"Agent Link 工具 {name} 缺少 input_schema",
            )
        output_schema = raw.get("output_schema")
        if output_schema is not None and not isinstance(output_schema, Mapping):
            raise AgentLinkProtocolError(
                "INVALID_TOOL_SNAPSHOT",
                f"Agent Link 工具 {name} 的 output_schema 必须是对象",
            )
        modalities = raw.get("result_modalities") or []
        if not isinstance(modalities, list):
            raise AgentLinkProtocolError(
                "INVALID_TOOL_SNAPSHOT",
                f"Agent Link 工具 {name} 的 result_modalities 必须是数组",
            )
        try:
            normalized_input = json.loads(
                json.dumps(dict(input_schema), ensure_ascii=False)
            )
            normalized_output = (
                json.loads(json.dumps(dict(output_schema), ensure_ascii=False))
                if output_schema is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise AgentLinkProtocolError(
                "INVALID_TOOL_SNAPSHOT",
                f"Agent Link 工具 {name} 的 Schema 不是有效 JSON",
            ) from exc
        return cls(
            name=name,
            description=description,
            input_schema=normalized_input,
            output_schema=normalized_output,
            result_modalities=tuple(
                dict.fromkeys(
                    str(item or "").strip().lower()
                    for item in modalities
                    if str(item or "").strip()
                )
            ),
        )

    def wire_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": json.loads(
                    json.dumps(self.input_schema, ensure_ascii=False)
                ),
            },
        }


@dataclass(frozen=True, slots=True)
class AgentLinkChatRequest:
    """核心层交给具体 Agent Adapter 的聊天请求。"""

    key: AgentLinkSessionKey
    request_id: str
    content: str
    user_text: str
    history: tuple[dict[str, str], ...]
    frontend_context: dict[str, Any]
    files: tuple[str, ...]
    tools: tuple[AgentLinkToolDefinition, ...]


class AgentLinkToolCaller(Protocol):
    """具体 Agent Adapter 反向调用前端工具时使用的框架无关 Port。"""

    async def call_tool(
        self,
        key: AgentLinkSessionKey,
        name: str,
        arguments: Mapping[str, object],
    ) -> Any:
        ...


class AgentLinkChatPort(Protocol):
    """Agent Link 核心运行时到具体 Agent 框架的执行 Port。"""

    async def run_chat(
        self,
        request: AgentLinkChatRequest,
        tool_caller: AgentLinkToolCaller,
    ) -> str:
        ...


@dataclass(slots=True)
class AgentLinkPeer:
    """一条已完成握手的 WebSocket 连接。"""

    key: AgentLinkSessionKey
    send_frame: SendFrame = field(repr=False)
    close_transport: CloseTransport = field(repr=False)
    online: bool = True
    snapshot_revision: int | None = None
    _tools: dict[str, AgentLinkToolDefinition] = field(
        default_factory=dict,
        repr=False,
    )
    _pending_tools: dict[str, asyncio.Future[Any]] = field(
        default_factory=dict,
        repr=False,
    )

    def tool_definitions(self) -> tuple[AgentLinkToolDefinition, ...]:
        return tuple(self._tools.values())

    async def send(self, frame: Mapping[str, object]) -> None:
        if not self.online:
            raise AgentLinkToolFailure(
                "OFFLINE",
                "MeaPet 当前离线，操作未执行。",
                retryable=True,
            )
        await self.send_frame(frame)

    def replace_snapshot(
        self,
        revision: int,
        tools: Sequence[AgentLinkToolDefinition],
    ) -> None:
        if self.snapshot_revision is not None and revision < self.snapshot_revision:
            raise AgentLinkProtocolError(
                "STALE_TOOL_SNAPSHOT",
                "Agent Link 工具快照 revision 不能倒退",
            )
        self._tools = {tool.name: tool for tool in tools}
        self.snapshot_revision = revision

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
        *,
        timeout_seconds: float,
    ) -> Any:
        if not self.online:
            raise AgentLinkToolFailure(
                "OFFLINE",
                "MeaPet 当前离线，操作未执行。",
                retryable=True,
            )
        if name not in self._tools:
            raise AgentLinkToolFailure(
                "TOOL_UNAVAILABLE",
                "当前 MeaPet 没有提供该工具。",
                retryable=False,
            )
        if len(self._pending_tools) >= NANOBOT_AGENT_LINK_MAX_PENDING_TOOLS:
            raise AgentLinkToolFailure(
                "BUSY",
                "当前 MeaPet 前端工具并发调用过多，操作未执行。",
                retryable=True,
            )
        request_id = f"call-{uuid.uuid4().hex}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending_tools[request_id] = future
        try:
            await self.send(
                make_agent_link_frame(
                    "tool.call",
                    {
                        "name": name,
                        "arguments": dict(arguments),
                    },
                    message_id=request_id,
                    session_id=self.key.session_id,
                )
            )
            try:
                return await asyncio.wait_for(
                    future,
                    timeout=max(0.1, float(timeout_seconds)),
                )
            except asyncio.TimeoutError as exc:
                await self._send_tool_cancel(request_id)
                raise AgentLinkToolFailure(
                    "TIMEOUT",
                    "MeaPet 工具执行超时，操作已取消。",
                    retryable=True,
                ) from exc
            except asyncio.CancelledError:
                await self._send_tool_cancel(request_id)
                raise
        finally:
            self._pending_tools.pop(request_id, None)

    async def _send_tool_cancel(self, request_id: str) -> None:
        if not self.online:
            return
        try:
            await self.send(
                make_agent_link_frame(
                    "tool.cancel",
                    {"request_id": request_id},
                    session_id=self.key.session_id,
                    reply_to=request_id,
                )
            )
        except Exception:
            logger.debug(
                "Agent Link 工具取消帧发送失败",
                extra={"request_id": request_id},
            )

    def resolve_tool_frame(self, frame: AgentLinkFrame) -> None:
        request_id = frame.reply_to or str(
            frame.payload.get("request_id") or ""
        ).strip()
        future = self._pending_tools.get(request_id)
        if future is None or future.done():
            return
        if frame.type == "tool.result":
            future.set_result(frame.payload.get("result"))
            return
        if frame.type == "tool.error":
            future.set_exception(
                AgentLinkToolFailure(
                    str(frame.payload.get("code") or "EXECUTION_FAILED"),
                    str(
                        frame.payload.get("safe_message")
                        or "MeaPet 工具执行失败。"
                    ),
                    retryable=bool(frame.payload.get("retryable", False)),
                )
            )

    def mark_offline(self) -> None:
        if not self.online:
            return
        self.online = False
        failure = AgentLinkToolFailure(
            "OFFLINE",
            "MeaPet 当前离线，操作未执行。",
            retryable=True,
        )
        for future in tuple(self._pending_tools.values()):
            if not future.done():
                future.set_exception(failure)
        self._pending_tools.clear()


@dataclass(slots=True)
class _ChatState:
    key: AgentLinkSessionKey
    request_id: str
    payload_sha256: str
    payload: dict[str, Any] = field(repr=False)
    subscriber: AgentLinkPeer | None = field(default=None, repr=False)
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    terminal: dict[str, Any] | None = field(default=None, repr=False)


def _payload_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_protocol_display(text: str) -> str:
    """避免普通文本中的协议闭标签截断兜底分段。"""

    replacements = {
        "<MEAPET_SEGMENT": "＜MEAPET_SEGMENT",
        "</MEAPET_SEGMENT": "＜/MEAPET_SEGMENT",
        "<DISPLAY": "＜DISPLAY",
        "</DISPLAY": "＜/DISPLAY",
        "<META": "＜META",
        "</META": "＜/META",
        "<MEAPET_DONE": "＜MEAPET_DONE",
    }
    result = text
    for source, target in replacements.items():
        result = result.replace(source, target)
    return result


def ensure_meapet_response_format(answer: str) -> str:
    """保留有效的 MeaPet 输出；普通 Nanobot 回复则包装成单段协议。"""

    text = str(answer or "").strip()
    required_markers = (
        "<MEAPET_SEGMENT",
        "</MEAPET_SEGMENT",
        "<DISPLAY",
        "</DISPLAY",
        "<META",
        "</META",
        "<MEAPET_DONE",
    )
    if text and all(marker in text for marker in required_markers):
        return text
    display = _safe_protocol_display(text or "暂时无法生成回复。")
    metadata = json.dumps(
        {
            "voice_text": text or "暂时无法生成回复。",
            "voice_language": "zh-CN",
            "mood": "neutral",
            "tts_style": "",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "<MEAPET_SEGMENT>"
        f"<DISPLAY>{display}</DISPLAY>"
        f"<META>{metadata}</META>"
        "</MEAPET_SEGMENT>"
        "<MEAPET_DONE />"
    )


def _normalize_history(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise AgentLinkProtocolError(
            "INVALID_CHAT_REQUEST",
            "chat.submit history 必须是数组",
        )
    result: list[dict[str, str]] = []
    for raw in value[-20:]:
        if not isinstance(raw, Mapping):
            continue
        role = str(raw.get("role") or "").strip().lower()
        content = raw.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        result.append({"role": role, "content": content[:10000]})
    return result


def _normalize_attachments(value: object) -> list[str]:
    if not isinstance(value, list):
        raise AgentLinkProtocolError(
            "INVALID_CHAT_REQUEST",
            "chat.submit attachments 必须是数组",
        )
    files: list[str] = []
    total_bytes = 0
    for raw in value:
        if not isinstance(raw, Mapping) or raw.get("type") != "image":
            raise AgentLinkProtocolError(
                "INVALID_ATTACHMENT",
                "Agent Link v1 只接受内联图片附件",
            )
        media_type = str(raw.get("media_type") or "").strip().lower()
        data = raw.get("data")
        if media_type not in _ALLOWED_IMAGE_MEDIA_TYPES or not isinstance(
            data,
            str,
        ):
            raise AgentLinkProtocolError(
                "INVALID_ATTACHMENT",
                "Agent Link 图片附件格式不受支持",
            )
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, TypeError) as exc:
            raise AgentLinkProtocolError(
                "INVALID_ATTACHMENT",
                "Agent Link 图片附件不是有效 base64",
            ) from exc
        if len(decoded) > NANOBOT_AGENT_LINK_MAX_INLINE_ATTACHMENT_BYTES:
            raise AgentLinkProtocolError(
                "ATTACHMENT_TOO_LARGE",
                "Agent Link 单个图片附件超过大小限制",
            )
        total_bytes += len(decoded)
        if total_bytes > NANOBOT_AGENT_LINK_MAX_INLINE_ATTACHMENT_BYTES * 2:
            raise AgentLinkProtocolError(
                "ATTACHMENT_TOO_LARGE",
                "Agent Link 本轮图片附件总量超过大小限制",
            )
        files.append(f"data:{media_type};base64,{data}")
    return files


class AgentLinkRuntime:
    """进程内 Agent Link 会话管理器。"""

    def __init__(self) -> None:
        self._connections: dict[AgentLinkSessionKey, AgentLinkPeer] = {}
        self._chat_states: OrderedDict[
            tuple[AgentLinkSessionKey, str],
            _ChatState,
        ] = OrderedDict()
        self._lock = asyncio.Lock()
        self._closed = False
        self._chat_port: AgentLinkChatPort | None = None

    def bind_chat_port(self, port: AgentLinkChatPort) -> None:
        """由 Composition Root 注入具体 Agent Adapter。"""

        if self._closed:
            raise RuntimeError("Agent Link Runtime 已关闭")
        self._chat_port = port

    async def attach(self, peer: AgentLinkPeer) -> None:
        old: AgentLinkPeer | None = None
        async with self._lock:
            if self._closed:
                raise RuntimeError("Agent Link Runtime 已关闭")
            old = self._connections.get(peer.key)
            self._connections[peer.key] = peer
        if old is not None and old is not peer:
            old.mark_offline()
            try:
                await old.close_transport(1012, "同一会话已建立新连接")
            except Exception:
                logger.debug("关闭被替换的 Agent Link 连接失败")

    async def detach(self, peer: AgentLinkPeer) -> None:
        peer.mark_offline()
        async with self._lock:
            if self._connections.get(peer.key) is peer:
                self._connections.pop(peer.key, None)

    async def current_peer(
        self,
        key: AgentLinkSessionKey,
    ) -> AgentLinkPeer | None:
        async with self._lock:
            peer = self._connections.get(key)
            if peer is None or not peer.online:
                return None
            return peer

    async def call_tool(
        self,
        key: AgentLinkSessionKey,
        name: str,
        arguments: Mapping[str, object],
    ) -> Any:
        peer = await self.current_peer(key)
        if peer is None:
            raise AgentLinkToolFailure(
                "OFFLINE",
                "MeaPet 当前离线，操作未执行。",
                retryable=True,
            )
        return await peer.call_tool(
            name,
            arguments,
            timeout_seconds=NANOBOT_AGENT_LINK_TOOL_TIMEOUT_SECONDS,
        )

    async def handle_frame(
        self,
        peer: AgentLinkPeer,
        frame: AgentLinkFrame,
    ) -> None:
        if frame.session_id != peer.key.session_id:
            raise AgentLinkProtocolError(
                "SESSION_MISMATCH",
                "Agent Link 消息的会话 ID 与当前连接不匹配",
            )
        required_extensions = frame.payload.get("required_extensions") or []
        if not isinstance(required_extensions, list):
            raise AgentLinkProtocolError(
                "INVALID_EXTENSIONS",
                "Agent Link required_extensions 必须是数组",
            )
        if required_extensions:
            raise AgentLinkProtocolError(
                "UNSUPPORTED_EXTENSION",
                "Nanobot 不支持消息要求的必需扩展",
            )

        if frame.type == "tools.snapshot":
            self._replace_tool_snapshot(peer, frame)
            return
        if frame.type == "chat.submit":
            await self._accept_chat(peer, frame)
            return
        if frame.type == "chat.cancel":
            await self._cancel_chat(peer, frame)
            return
        if frame.type in {"tool.accepted", "tool.result", "tool.error"}:
            peer.resolve_tool_frame(frame)
            return
        if frame.type == "control.ping":
            await peer.send(
                make_agent_link_frame(
                    "control.pong",
                    {},
                    session_id=peer.key.session_id,
                    reply_to=frame.id,
                )
            )
            return
        if frame.type == "control.pong":
            return
        if bool(frame.payload.get("required", False)):
            await peer.send(
                make_agent_link_frame(
                    "control.error",
                    {
                        "category": "protocol",
                        "code": "UNSUPPORTED_MESSAGE",
                        "safe_message": "Nanobot 不支持该必需消息类型。",
                        "retryable": False,
                    },
                    session_id=peer.key.session_id,
                    reply_to=frame.id,
                )
            )

    def _replace_tool_snapshot(
        self,
        peer: AgentLinkPeer,
        frame: AgentLinkFrame,
    ) -> None:
        revision = frame.payload.get("revision")
        raw_tools = frame.payload.get("tools")
        if not isinstance(revision, int) or revision < 0:
            raise AgentLinkProtocolError(
                "INVALID_TOOL_SNAPSHOT",
                "Agent Link 工具快照 revision 必须是非负整数",
            )
        if not isinstance(raw_tools, list):
            raise AgentLinkProtocolError(
                "INVALID_TOOL_SNAPSHOT",
                "Agent Link 工具快照 tools 必须是数组",
            )
        if len(raw_tools) > NANOBOT_AGENT_LINK_MAX_TOOLS:
            raise AgentLinkProtocolError(
                "TOO_MANY_TOOLS",
                "Agent Link 工具数量超过服务端限制",
            )
        tools = tuple(AgentLinkToolDefinition.parse(item) for item in raw_tools)
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise AgentLinkProtocolError(
                "DUPLICATE_TOOL",
                "Agent Link 工具快照包含重复名称",
            )

        from core.tool_registration import list_tool_registrations

        reserved = {
            registration.name for registration in list_tool_registrations()
        }
        collision = sorted(set(names) & reserved)
        if collision:
            raise AgentLinkProtocolError(
                "TOOL_NAME_CONFLICT",
                "Agent Link 工具名称与 Nanobot 内置工具冲突",
            )
        peer.replace_snapshot(revision, tools)

    async def _accept_chat(
        self,
        peer: AgentLinkPeer,
        frame: AgentLinkFrame,
    ) -> None:
        if peer.snapshot_revision is None:
            raise AgentLinkProtocolError(
                "TOOLS_NOT_SYNCED",
                "Agent Link 必须先发送 tools.snapshot",
            )
        payload = self._validate_chat_payload(frame.payload)
        digest = _payload_sha256(payload)
        state_key = (peer.key, frame.id)
        terminal: dict[str, Any] | None = None
        duplicate = False
        async with self._lock:
            state = self._chat_states.get(state_key)
            if state is not None:
                if state.payload_sha256 != digest:
                    terminal = self._chat_error_frame(
                        peer.key.session_id,
                        frame.id,
                        category="protocol",
                        code="IDEMPOTENCY_CONFLICT",
                        safe_message="相同 chat.submit.id 对应了不同请求内容。",
                        retryable=False,
                    )
                else:
                    duplicate = True
                    state.subscriber = peer
                    terminal = state.terminal
                    self._chat_states.move_to_end(state_key)
            else:
                active_count = sum(
                    1
                    for item in self._chat_states.values()
                    if item.key == peer.key and item.terminal is None
                )
                if active_count >= NANOBOT_AGENT_LINK_MAX_ACTIVE_CHATS:
                    terminal = self._chat_error_frame(
                        peer.key.session_id,
                        frame.id,
                        category="rate_limit",
                        code="TOO_MANY_ACTIVE_CHATS",
                        safe_message="当前 Agent Link 会话的并发请求过多。",
                        retryable=True,
                    )
                else:
                    state = _ChatState(
                        key=peer.key,
                        request_id=frame.id,
                        payload_sha256=digest,
                        payload=payload,
                        subscriber=peer,
                    )
                    self._chat_states[state_key] = state
                    state.task = asyncio.create_task(
                        self._run_chat(state),
                        name=f"agent-link-chat:{frame.id[:48]}",
                    )

        if terminal is not None:
            await peer.send(terminal)
            return
        await peer.send(
            make_agent_link_frame(
                "chat.accepted",
                {
                    "status": "accepted",
                    "duplicate": duplicate,
                },
                session_id=peer.key.session_id,
                reply_to=frame.id,
            )
        )

    @staticmethod
    def _validate_chat_payload(payload: Mapping[str, object]) -> dict[str, Any]:
        content = payload.get("content")
        user_text = payload.get("user_text")
        response_format = payload.get("response_format")
        if not isinstance(content, str) or not content.strip():
            raise AgentLinkProtocolError(
                "INVALID_CHAT_REQUEST",
                "chat.submit content 不能为空",
            )
        if len(content) > 200000:
            raise AgentLinkProtocolError(
                "CHAT_REQUEST_TOO_LARGE",
                "chat.submit content 超过长度限制",
            )
        if not isinstance(user_text, str):
            raise AgentLinkProtocolError(
                "INVALID_CHAT_REQUEST",
                "chat.submit user_text 必须是字符串",
            )
        if response_format != "meapet-segments-v1":
            raise AgentLinkProtocolError(
                "UNSUPPORTED_RESPONSE_FORMAT",
                "Nanobot 仅支持 meapet-segments-v1 回复格式",
            )
        if payload.get("idempotent") is not True:
            raise AgentLinkProtocolError(
                "IDEMPOTENCY_REQUIRED",
                "Agent Link v1 chat.submit 必须声明 idempotent=true",
            )
        frontend_context = payload.get("frontend_context")
        if not isinstance(frontend_context, Mapping):
            raise AgentLinkProtocolError(
                "INVALID_CHAT_REQUEST",
                "chat.submit frontend_context 必须是对象",
            )
        normalized = dict(payload)
        normalized["content"] = content
        normalized["user_text"] = user_text
        normalized["history"] = _normalize_history(payload.get("history"))
        normalized["attachments"] = list(payload.get("attachments") or [])
        return normalized

    async def _run_chat(self, state: _ChatState) -> None:
        terminal: dict[str, Any]
        try:
            peer = await self.current_peer(state.key)
            if peer is None:
                raise RuntimeError("MeaPet 在聊天开始前已经离线")
            definitions = peer.tool_definitions()
            files = _normalize_attachments(
                state.payload.get("attachments")
            )
            chat_port = self._chat_port
            if chat_port is None:
                raise RuntimeError("Agent Link 尚未绑定 Agent 执行 Adapter")
            frontend_context = state.payload.get("frontend_context")
            request = AgentLinkChatRequest(
                key=state.key,
                request_id=state.request_id,
                content=str(state.payload.get("content") or ""),
                user_text=str(state.payload.get("user_text") or ""),
                history=tuple(state.payload.get("history") or ()),
                frontend_context=(
                    dict(frontend_context)
                    if isinstance(frontend_context, Mapping)
                    else {}
                ),
                files=tuple(files),
                tools=definitions,
            )
            answer = await asyncio.wait_for(
                chat_port.run_chat(request, self),
                timeout=max(
                    1.0,
                    float(NANOBOT_AGENT_LINK_CHAT_TIMEOUT_SECONDS),
                ),
            )
            terminal = make_agent_link_frame(
                "chat.final",
                {
                    "text": ensure_meapet_response_format(str(answer or "")),
                    "replace": True,
                },
                session_id=state.key.session_id,
                reply_to=state.request_id,
            )
        except asyncio.CancelledError:
            terminal = make_agent_link_frame(
                "chat.cancelled",
                {
                    "status": "cancelled",
                    "request_id": state.request_id,
                },
                session_id=state.key.session_id,
                reply_to=state.request_id,
            )
        except asyncio.TimeoutError:
            terminal = self._chat_error_frame(
                state.key.session_id,
                state.request_id,
                category="timeout",
                code="AGENT_TIMEOUT",
                safe_message="Nanobot 处理本轮消息超时。",
                retryable=True,
            )
        except AgentLinkProtocolError as exc:
            terminal = self._chat_error_frame(
                state.key.session_id,
                state.request_id,
                category="protocol",
                code=exc.code,
                safe_message=exc.safe_message,
                retryable=False,
            )
        except Exception:
            logger.exception(
                "Agent Link 聊天执行失败",
                extra={"request_id": state.request_id},
            )
            terminal = self._chat_error_frame(
                state.key.session_id,
                state.request_id,
                category="backend_unavailable",
                code="AGENT_UNAVAILABLE",
                safe_message="Nanobot 暂时无法生成回复。",
                retryable=True,
            )
        await self._finish_chat(state, terminal)

    async def _finish_chat(
        self,
        state: _ChatState,
        terminal: dict[str, Any],
    ) -> None:
        state_key = (state.key, state.request_id)
        subscriber: AgentLinkPeer | None
        async with self._lock:
            current = self._chat_states.get(state_key)
            if current is not state:
                return
            state.terminal = terminal
            state.task = None
            subscriber = state.subscriber
            self._chat_states.move_to_end(state_key)
            self._evict_terminal_chats_locked()
            active_peer = self._connections.get(state.key)
            if subscriber is not active_peer:
                subscriber = None
        if subscriber is not None and subscriber.online:
            try:
                await subscriber.send(terminal)
            except Exception:
                logger.debug(
                    "Agent Link 聊天终态暂存等待重连重放",
                    extra={"request_id": state.request_id},
                )

    def _evict_terminal_chats_locked(self) -> None:
        terminal_keys = [
            key
            for key, state in self._chat_states.items()
            if state.terminal is not None
        ]
        overflow = len(terminal_keys) - NANOBOT_AGENT_LINK_MAX_TERMINAL_CHATS
        for key in terminal_keys[: max(0, overflow)]:
            self._chat_states.pop(key, None)

    async def _cancel_chat(
        self,
        peer: AgentLinkPeer,
        frame: AgentLinkFrame,
    ) -> None:
        request_id = frame.reply_to or str(
            frame.payload.get("request_id") or ""
        ).strip()
        if not request_id:
            raise AgentLinkProtocolError(
                "INVALID_CHAT_CANCEL",
                "chat.cancel 缺少 request_id",
            )
        terminal: dict[str, Any] | None = None
        async with self._lock:
            state = self._chat_states.get((peer.key, request_id))
            if state is not None:
                state.subscriber = peer
                terminal = state.terminal
                task = state.task
                if terminal is None and task is not None and not task.done():
                    task.cancel()
            else:
                terminal = make_agent_link_frame(
                    "chat.cancelled",
                    {
                        "status": "cancelled",
                        "request_id": request_id,
                    },
                    session_id=peer.key.session_id,
                    reply_to=request_id,
                )
        if terminal is not None:
            await peer.send(terminal)

    @staticmethod
    def _chat_error_frame(
        session_id: str,
        request_id: str,
        *,
        category: str,
        code: str,
        safe_message: str,
        retryable: bool,
    ) -> dict[str, Any]:
        return make_agent_link_frame(
            "chat.error",
            {
                "category": category,
                "code": code,
                "safe_message": safe_message,
                "retryable": bool(retryable),
            },
            session_id=session_id,
            reply_to=request_id,
        )

    async def shutdown(self) -> None:
        async with self._lock:
            self._closed = True
            peers = tuple(self._connections.values())
            self._connections.clear()
            tasks = tuple(
                state.task
                for state in self._chat_states.values()
                if state.task is not None and not state.task.done()
            )
            self._chat_states.clear()
        for peer in peers:
            peer.mark_offline()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(
            *(
                peer.close_transport(1001, "Nanobot 服务正在关闭")
                for peer in peers
            ),
            return_exceptions=True,
        )


_runtime: AgentLinkRuntime | None = None


def get_agent_link_runtime() -> AgentLinkRuntime:
    global _runtime
    if _runtime is None or _runtime._closed:
        _runtime = AgentLinkRuntime()
    return _runtime


async def shutdown_agent_link_runtime() -> None:
    global _runtime
    runtime = _runtime
    _runtime = None
    if runtime is not None:
        await runtime.shutdown()
