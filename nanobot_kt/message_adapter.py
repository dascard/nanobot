"""MessageContract 到 KT 现有 Bridge 调用面的显式适配器。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from typing import Any

from core.agent_runtime.contracts import (
    AgentTurnRequest,
    RequestRuntimeContext,
    RuntimeChatType,
    RuntimeOwnerType,
    RuntimePrincipal,
)
from foundation.message_contract import InboundMessageContract


@dataclass(frozen=True, slots=True)
class KTMessageInvocation:
    runtime_request: AgentTurnRequest
    content: str
    user_id: str
    session_id: str
    sender_name: str
    metadata: dict[str, Any]
    stream: bool


class MessageContractBridgeMixin:
    """让 KT Bridge 只经本 Adapter 接收类型化消息合同。"""

    async def handle_message_contract(
        self,
        message: InboundMessageContract,
        *,
        content: str,
        runtime_user_id: str,
        runtime_session_id: str,
        sender_name: str,
        metadata: Mapping[str, Any] | None = None,
        stream_queue: Any = None,
        stream: bool = False,
    ) -> Any:
        invocation = build_kt_message_invocation(
            message,
            content=content,
            runtime_user_id=runtime_user_id,
            runtime_session_id=runtime_session_id,
            sender_name=sender_name,
            metadata=metadata,
            stream=stream,
        )
        runtime_request = invocation.runtime_request
        return await self.handle_message(
            runtime_request.content,
            user_id=invocation.user_id,
            session_id=invocation.session_id,
            sender_name=invocation.sender_name,
            metadata=invocation.metadata,
            stream_queue=stream_queue,
            stream=runtime_request.stream,
        )


def _runtime_request_id(
    message: InboundMessageContract,
    *,
    content: str,
) -> str:
    if message.trace.request_id:
        return message.trace.request_id
    if message.message_id:
        return message.message_id
    material = "\0".join(
        (
            str(message.schema_version),
            message.chat_stream.chat_stream_id,
            message.actor.canonical_id,
            content,
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"message-{digest}"


def _build_runtime_request(
    message: InboundMessageContract,
    *,
    content: str,
    stream: bool,
) -> AgentTurnRequest:
    chat_type = (
        RuntimeChatType.GROUP
        if message.chat_stream.chat_type == "group"
        else RuntimeChatType.PRIVATE
    )
    context = RequestRuntimeContext(
        request_id=_runtime_request_id(message, content=content),
        principal=RuntimePrincipal(
            platform=str(message.principal.platform),
            owner_type=RuntimeOwnerType(message.principal.owner_type),
            owner_id=message.principal.owner_id,
        ),
        session_id=message.chat_stream.chat_stream_id,
        chat_type=chat_type,
        trace_id=message.trace.trace_id,
        message_id=message.message_id,
    )
    return AgentTurnRequest(
        context=context,
        content=content,
        stream=bool(stream),
    )


def build_kt_message_invocation(
    message: InboundMessageContract,
    *,
    content: str,
    runtime_user_id: str,
    runtime_session_id: str,
    sender_name: str,
    metadata: Mapping[str, Any] | None = None,
    stream: bool = False,
) -> KTMessageInvocation:
    """用受信合同覆盖所有身份字段，兼容元数据不能反向改写身份。"""

    if not isinstance(message, InboundMessageContract):
        raise TypeError("message 必须是 InboundMessageContract")
    if type(content) is not str:
        raise TypeError("content 必须是字符串")
    user_id = str(runtime_user_id or "").strip()
    session_id = str(runtime_session_id or "").strip()
    if not user_id:
        raise ValueError("runtime_user_id 不能为空")
    if not session_id:
        raise ValueError("runtime_session_id 不能为空")

    normalized_meta = dict(metadata or {})
    is_group = message.chat_stream.chat_type == "group"
    normalized_meta.update(
        {
            "platform": message.chat_stream.platform,
            "chat_type": message.chat_stream.chat_type,
            "user_id": user_id,
            "session_id": session_id,
            "sender_id": message.actor.actor_id,
            "message_id": message.message_id,
            "is_group": is_group,
            "chat_stream_id": message.chat_stream.chat_stream_id,
            "principal_id": message.principal.canonical_id,
            "recipient_id": message.recipient.canonical_id,
            "message_contract_version": message.schema_version,
            "stream": bool(stream),
        }
    )
    if is_group:
        normalized_meta["group_id"] = (
            message.chat_stream.external_session_id
        )
    else:
        normalized_meta.pop("group_id", None)
    if message.trace.request_id:
        normalized_meta["request_id"] = message.trace.request_id
    if message.trace.trace_id:
        normalized_meta["trace_id"] = message.trace.trace_id
    if message.trace.correlation_id:
        normalized_meta["correlation_id"] = (
            message.trace.correlation_id
        )

    return KTMessageInvocation(
        runtime_request=_build_runtime_request(
            message,
            content=content,
            stream=stream,
        ),
        content=content,
        user_id=user_id,
        session_id=session_id,
        sender_name=str(sender_name or ""),
        metadata=normalized_meta,
        stream=bool(stream),
    )


__all__ = [
    "KTMessageInvocation",
    "MessageContractBridgeMixin",
    "build_kt_message_invocation",
]
