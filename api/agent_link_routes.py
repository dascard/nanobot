"""Agent Link v1 WebSocket 服务端入口。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from hmac import compare_digest
from typing import Mapping

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import (
    NANOBOT_AGENT_LINK_HANDSHAKE_TIMEOUT_SECONDS,
    NANOBOT_AGENT_LINK_MAX_FRAME_BYTES,
    NANOBOT_AGENT_LINK_OUTGOING_QUEUE_SIZE,
    NANOBOT_AGENT_LINK_SEND_TIMEOUT_SECONDS,
    NANOBOT_AGENT_LINK_TOKEN,
    NANOBOT_CHARACTER_NAME,
)
from core.agent_link.protocol import (
    AgentLinkFrame,
    AgentLinkProtocolError,
    make_agent_link_frame,
)
from core.agent_link.runtime import (
    AgentLinkClientIdentity,
    AgentLinkPeer,
    AgentLinkSessionKey,
    get_agent_link_runtime,
)
from core.prompt_v2.policy_profiles import (
    DEFAULT_EXTERNAL_POLICY_PROFILE,
    PromptPolicyError,
    resolve_prompt_policy_profile,
)


logger = logging.getLogger("nanobot.agent_link.routes")
router = APIRouter(tags=["agent-link"])


@dataclass(slots=True)
class _OutgoingItem:
    text: str
    completed: asyncio.Future[None]


class _WebSocketChannel:
    """有界单写者通道，避免聊天与工具结果并发写同一 WebSocket。"""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._queue: asyncio.Queue[_OutgoingItem | None] = asyncio.Queue(
            maxsize=max(1, NANOBOT_AGENT_LINK_OUTGOING_QUEUE_SIZE)
        )
        self._writer_task: asyncio.Task[None] | None = None
        self._close_lock = asyncio.Lock()
        self._closed = False

    def start(self) -> None:
        if self._writer_task is None:
            self._writer_task = asyncio.create_task(
                self._writer(),
                name="agent-link-websocket-writer",
            )

    async def send_frame(self, frame: Mapping[str, object]) -> None:
        if self._closed:
            raise ConnectionError("Agent Link WebSocket 已关闭")
        text = json.dumps(
            dict(frame),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(text.encode("utf-8")) > NANOBOT_AGENT_LINK_MAX_FRAME_BYTES:
            raise AgentLinkProtocolError(
                "FRAME_TOO_LARGE",
                "Agent Link 出站消息超过大小限制",
            )
        loop = asyncio.get_running_loop()
        completed: asyncio.Future[None] = loop.create_future()
        item = _OutgoingItem(text=text, completed=completed)
        timeout = max(0.1, NANOBOT_AGENT_LINK_SEND_TIMEOUT_SECONDS)
        await asyncio.wait_for(self._queue.put(item), timeout=timeout)
        await asyncio.wait_for(asyncio.shield(completed), timeout=timeout)

    async def _writer(self) -> None:
        primary_error: BaseException | None = None
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                try:
                    await self._websocket.send_text(item.text)
                except BaseException as exc:
                    primary_error = exc
                    if not item.completed.done():
                        item.completed.set_exception(exc)
                    raise
                else:
                    if not item.completed.done():
                        item.completed.set_result(None)
        except asyncio.CancelledError as exc:
            primary_error = exc
            raise
        except BaseException as exc:
            primary_error = exc
        finally:
            self._closed = True
            failure = primary_error or ConnectionError(
                "Agent Link WebSocket 写入通道已关闭"
            )
            while True:
                try:
                    queued = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if queued is not None and not queued.completed.done():
                    queued.completed.set_exception(failure)

    async def close(self, code: int, reason: str) -> None:
        async with self._close_lock:
            if self._closed and (
                self._writer_task is None or self._writer_task.done()
            ):
                return
            self._closed = True
            task = self._writer_task
            self._writer_task = None
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            try:
                await self._websocket.close(
                    code=int(code),
                    reason=str(reason or "")[:120],
                )
            except Exception:
                pass


async def _receive_frame(
    websocket: WebSocket,
    *,
    timeout: float | None = None,
) -> AgentLinkFrame:
    receive = websocket.receive()
    message = (
        await asyncio.wait_for(receive, timeout=max(0.1, timeout))
        if timeout is not None
        else await receive
    )
    message_type = str(message.get("type") or "")
    if message_type == "websocket.disconnect":
        raise WebSocketDisconnect(
            code=int(message.get("code") or 1000),
            reason=str(message.get("reason") or ""),
        )
    text = message.get("text")
    if not isinstance(text, str):
        raise AgentLinkProtocolError(
            "BINARY_FRAME_UNSUPPORTED",
            "Agent Link 只接受 UTF-8 JSON 文本帧",
        )
    if len(text.encode("utf-8")) > NANOBOT_AGENT_LINK_MAX_FRAME_BYTES:
        raise AgentLinkProtocolError(
            "FRAME_TOO_LARGE",
            "Agent Link 入站消息超过大小限制",
        )
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentLinkProtocolError(
            "INVALID_JSON",
            "Agent Link 消息不是有效 JSON",
        ) from exc
    return AgentLinkFrame.parse(raw)


def _safe_device_id(frame: AgentLinkFrame) -> str:
    device = frame.payload.get("device")
    device = device if isinstance(device, Mapping) else {}
    device_id = str(device.get("id") or "").strip()
    if (
        not device_id
        or len(device_id) > 256
        or any(char in device_id for char in "\r\n\x00")
    ):
        raise AgentLinkProtocolError(
            "INVALID_HANDSHAKE",
            "control.hello 缺少有效 device.id",
        )
    return device_id


def _client_identity(frame: AgentLinkFrame) -> AgentLinkClientIdentity:
    return AgentLinkClientIdentity.parse(frame.payload.get("client"))


async def _preflight_prompt_policy(
    identity: AgentLinkClientIdentity,
) -> str:
    """在 ready 前确认服务端分配的 Prompt 策略存在可执行分支。"""

    try:
        policy_profile = resolve_prompt_policy_profile(
            identity.platform_id,
            DEFAULT_EXTERNAL_POLICY_PROFILE,
        )
        from core.prompt_v2.compiler import compile_prompt_plan
        from core.prompt_v2.schema import PromptCompileRequest

        await compile_prompt_plan(
            PromptCompileRequest(
                chat_type="private",
                platform=identity.platform_id,
                policy_profile=policy_profile,
                session_id="agent_link_preflight",
                user_id="agent_link_preflight",
                sender_name=identity.name,
                user_input="Agent Link 连接预检",
            ),
            strict_audit=True,
        )
        return policy_profile
    except (PromptPolicyError, OSError, ValueError, RuntimeError) as exc:
        logger.error(
            "Agent Link Prompt 策略预检失败",
            extra={
                "platform_id": identity.platform_id,
                "error_type": type(exc).__name__,
            },
        )
        raise AgentLinkProtocolError(
            "PROMPT_POLICY_UNAVAILABLE",
            "Nanobot 当前没有可用的外部私聊 Prompt 策略。",
        ) from exc


def _validate_client_capabilities(frame: AgentLinkFrame) -> None:
    capabilities = frame.payload.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, Mapping) else {}
    chat = capabilities.get("chat")
    chat = chat if isinstance(chat, Mapping) else {}
    tools = capabilities.get("tools")
    tools = tools if isinstance(tools, Mapping) else {}
    if not bool(chat.get("submit", False)):
        raise AgentLinkProtocolError(
            "CHAT_UNAVAILABLE",
            "MeaPet 没有声明聊天提交能力",
        )
    if not bool(tools.get("dynamic", False)) or not bool(
        tools.get("call", False)
    ):
        raise AgentLinkProtocolError(
            "TOOLS_UNAVAILABLE",
            "MeaPet 没有声明动态工具与调用能力",
        )
    required_extensions = frame.payload.get("required_extensions") or []
    if not isinstance(required_extensions, list):
        raise AgentLinkProtocolError(
            "INVALID_HANDSHAKE",
            "control.hello required_extensions 必须是数组",
        )
    if required_extensions:
        raise AgentLinkProtocolError(
            "UNSUPPORTED_EXTENSION",
            "Nanobot 不支持 control.hello 要求的必需扩展",
        )


def _authenticate_hello(frame: AgentLinkFrame) -> None:
    configured = str(NANOBOT_AGENT_LINK_TOKEN or "")
    if not configured:
        raise AgentLinkProtocolError(
            "TOKEN_NOT_CONFIGURED",
            "Nanobot 尚未配置 Agent Link 访问令牌。",
        )
    auth = frame.payload.get("auth")
    auth = auth if isinstance(auth, Mapping) else {}
    scheme = str(auth.get("scheme") or "").strip().lower()
    token = auth.get("token")
    if (
        scheme != "bearer"
        or not isinstance(token, str)
        or not compare_digest(token, configured)
    ):
        raise AgentLinkProtocolError(
            "INVALID_TOKEN",
            "Agent Link 访问令牌无效。",
        )


async def _send_control_error(
    channel: _WebSocketChannel,
    *,
    session_id: str,
    reply_to: str,
    error: AgentLinkProtocolError,
) -> None:
    category = (
        "authentication"
        if error.code in {"INVALID_TOKEN", "TOKEN_NOT_CONFIGURED"}
        else (
            "configuration"
            if error.code == "PROMPT_POLICY_UNAVAILABLE"
            else "protocol"
        )
    )
    try:
        await channel.send_frame(
            make_agent_link_frame(
                "control.error",
                {
                    "category": category,
                    "code": error.code,
                    "safe_message": error.safe_message,
                    "retryable": False,
                },
                session_id=session_id,
                reply_to=reply_to,
            )
        )
    except Exception:
        pass


@router.websocket("/agent-link")
async def agent_link_websocket(websocket: WebSocket) -> None:
    """接收第三方客户端主动建立的 Agent Link v1 双向长连接。"""

    await websocket.accept()
    channel = _WebSocketChannel(websocket)
    channel.start()
    runtime = get_agent_link_runtime()
    peer: AgentLinkPeer | None = None
    last_session_id = ""
    last_message_id = ""
    close_code = 1000
    close_reason = "连接已关闭"
    try:
        hello = await _receive_frame(
            websocket,
            timeout=NANOBOT_AGENT_LINK_HANDSHAKE_TIMEOUT_SECONDS,
        )
        last_session_id = hello.session_id
        last_message_id = hello.id
        if hello.type != "control.hello":
            raise AgentLinkProtocolError(
                "INVALID_HANDSHAKE",
                "Agent Link 第一条消息必须是 control.hello",
            )
        if not hello.session_id:
            raise AgentLinkProtocolError(
                "INVALID_HANDSHAKE",
                "control.hello 缺少 session_id",
            )
        _authenticate_hello(hello)
        _validate_client_capabilities(hello)
        client_identity = _client_identity(hello)
        policy_profile = await _preflight_prompt_policy(client_identity)
        device_id = _safe_device_id(hello)
        peer = AgentLinkPeer(
            key=AgentLinkSessionKey(
                device_id=device_id,
                session_id=hello.session_id,
            ),
            send_frame=channel.send_frame,
            close_transport=channel.close,
            client=client_identity,
            policy_profile=policy_profile,
        )
        await runtime.attach(peer)
        await peer.send(
            make_agent_link_frame(
                "control.ready",
                {
                    "version": "1.0",
                    "authenticated": True,
                    "agent_name": NANOBOT_CHARACTER_NAME or "Nanobot",
                    "server_version": "1.0.0",
                    "client_context": {
                        "platform_id": client_identity.platform_id,
                        "policy_profile": policy_profile,
                        "chat_type": "private",
                    },
                    "capabilities": {
                        "chat": {
                            "submit": True,
                            "streaming": False,
                            "cancel": True,
                        },
                        "tools": {
                            "dynamic": True,
                            "call": True,
                            "cancel": True,
                        },
                    },
                    "required_extensions": [],
                },
                session_id=hello.session_id,
                reply_to=hello.id,
            )
        )

        while True:
            frame = await _receive_frame(websocket)
            last_session_id = frame.session_id
            last_message_id = frame.id
            await runtime.handle_frame(peer, frame)
    except WebSocketDisconnect:
        close_reason = "客户端已断开"
    except asyncio.TimeoutError:
        close_code = 1008
        close_reason = "Agent Link 握手超时"
        error = AgentLinkProtocolError(
            "HANDSHAKE_TIMEOUT",
            "Agent Link 握手超时。",
        )
        await _send_control_error(
            channel,
            session_id=last_session_id,
            reply_to=last_message_id,
            error=error,
        )
    except AgentLinkProtocolError as exc:
        if exc.code in {"INVALID_TOKEN", "TOKEN_NOT_CONFIGURED"}:
            close_code = 1008
        elif exc.code == "PROMPT_POLICY_UNAVAILABLE":
            close_code = 1011
        else:
            close_code = 1002
        close_reason = exc.safe_message
        await _send_control_error(
            channel,
            session_id=last_session_id,
            reply_to=last_message_id,
            error=exc,
        )
    except Exception:
        close_code = 1011
        close_reason = "Nanobot Agent Link 内部错误"
        logger.exception("Agent Link WebSocket 未预期失败")
    finally:
        if peer is not None:
            await runtime.detach(peer)
        await channel.close(close_code, close_reason)


__all__ = ["agent_link_websocket", "router"]
