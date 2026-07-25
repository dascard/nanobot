"""业务模块调用类型化消息 Gateway 的框架无关入口。"""

from __future__ import annotations

from collections.abc import Mapping
import inspect
from typing import Any

from foundation.message_contract import InboundMessageContract


async def dispatch_agent_message(
    gateway: Any,
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
    """优先调用类型化 Port；旧测试替身暂经显式兼容分支。"""

    if not isinstance(message, InboundMessageContract):
        raise TypeError("message 必须是 InboundMessageContract")
    missing = object()
    declared_typed_handler = inspect.getattr_static(
        gateway,
        "handle_message_contract",
        missing,
    )
    typed_handler = (
        getattr(gateway, "handle_message_contract", None)
        if declared_typed_handler is not missing
        else None
    )
    if callable(typed_handler):
        return await typed_handler(
            message,
            content=content,
            runtime_user_id=runtime_user_id,
            runtime_session_id=runtime_session_id,
            sender_name=sender_name,
            metadata=metadata,
            stream_queue=stream_queue,
            stream=stream,
        )

    legacy_handler = getattr(gateway, "handle_message", None)
    if not callable(legacy_handler):
        raise TypeError("Agent Gateway 未实现消息处理 Port")
    return await legacy_handler(
        content,
        user_id=runtime_user_id,
        session_id=runtime_session_id,
        sender_name=sender_name,
        metadata=dict(metadata or {}),
        stream_queue=stream_queue,
        stream=stream,
    )


__all__ = ["dispatch_agent_message"]
