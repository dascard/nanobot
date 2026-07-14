"""KT Agent 适配层。

集中放 Nanobot 主链路对 KT conversation/event API 的直接调用。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from kohakuterrarium.core.events import create_user_input_event


logger = logging.getLogger("nanobot.kt.adapter")


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def _sync_conversation_metadata(conversation: Any) -> None:
    messages = getattr(conversation, "_messages", None)
    metadata = getattr(conversation, "_metadata", None)
    if not isinstance(messages, list) or metadata is None:
        return

    from kohakuterrarium.core.conversation import _get_content_text_length

    if hasattr(metadata, "message_count"):
        metadata.message_count = len(messages)
    if hasattr(metadata, "total_chars"):
        metadata.total_chars = sum(
            _get_content_text_length(_message_content(message))
            for message in messages
        )
    if hasattr(metadata, "updated_at"):
        metadata.updated_at = datetime.now()


def _install_emergency_drop_order_guard(agent: Any) -> bool:
    controller = getattr(agent, "controller", None)
    llm = getattr(controller, "llm", None)
    register = getattr(llm, "on_emergency_drop", None)
    if not callable(register):
        return False

    providers = getattr(agent, "_nanobot_order_guard_providers", None)
    if not isinstance(providers, list):
        providers = []
        try:
            agent._nanobot_order_guard_providers = providers
        except Exception:
            return False
    if any(provider is llm for provider in providers):
        return False

    def _reinstall_after_emergency_drop(_messages: list[dict[str, Any]]) -> None:
        install_conversation_order_guard(agent)

    register(_reinstall_after_emergency_drop)
    providers.append(llm)
    return True


def install_conversation_order_guard(agent: Any) -> bool:
    """保持 Prompt Runtime 消息顺序，并覆盖 KT 的 Conversation 替换路径。"""

    _install_emergency_drop_order_guard(agent)
    conversation = getattr(getattr(agent, "controller", None), "conversation", None)
    if conversation is None or not hasattr(conversation, "_maybe_truncate"):
        return False
    if getattr(conversation, "_nanobot_order_guard_installed", False):
        return False

    original_maybe_truncate = conversation._maybe_truncate

    def _maybe_truncate_preserving_order() -> None:
        config = getattr(conversation, "config", None)
        messages = getattr(conversation, "_messages", None)
        if config is None or not isinstance(messages, list):
            original_maybe_truncate()
            return

        max_messages = int(getattr(config, "max_messages", 0) or 0)
        if max_messages <= 0 or len(messages) <= max_messages:
            return

        if bool(getattr(config, "keep_system", True)):
            system_indexes = {
                index
                for index, message in enumerate(messages)
                if message_role(message) == "system"
            }
            other_indexes = [
                index
                for index, message in enumerate(messages)
                if message_role(message) != "system"
            ]
            max_other = max(0, max_messages - len(system_indexes))
            kept_indexes = set(system_indexes)
            if max_other:
                kept_indexes.update(other_indexes[-max_other:])
            conversation._messages = [
                message
                for index, message in enumerate(messages)
                if index in kept_indexes
            ]
        else:
            conversation._messages = messages[-max_messages:]

        _sync_conversation_metadata(conversation)
        logger.debug(
            "Nanobot conversation truncated without role reordering",
            extra={
                "dropped": len(messages) - len(conversation._messages),
                "kept": len(conversation._messages),
            },
        )

    conversation._nanobot_original_maybe_truncate = original_maybe_truncate
    conversation._maybe_truncate = _maybe_truncate_preserving_order
    conversation._nanobot_order_guard_installed = True
    return True


def message_role(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("role", ""))
    return str(getattr(msg, "role", ""))


def reset_conversation_to_system(agent: Any) -> tuple[int, int]:
    conv = getattr(getattr(agent, "controller", None), "conversation", None)
    if conv is None or not hasattr(conv, "_messages"):
        return (0, 0)
    all_msgs = list(getattr(conv, "_messages", []))
    before_len = len(all_msgs)
    clear = getattr(conv, "clear", None)
    if callable(clear):
        clear(keep_system=True)
    else:
        conv._messages = [m for m in all_msgs if message_role(m) == "system"]
    _sync_conversation_metadata(conv)
    return before_len, len(conv._messages)


def apply_prompt_messages(agent: Any, messages: list[dict[str, Any]]) -> int:
    conv = getattr(getattr(agent, "controller", None), "conversation", None)
    if conv is None or not hasattr(conv, "_messages"):
        return 0
    clear = getattr(conv, "clear", None)
    if callable(clear):
        clear(keep_system=False)
    else:
        conv._messages = []
        _sync_conversation_metadata(conv)
    for msg in messages or []:
        conv.append(str(msg.get("role") or "system"), msg.get("content") or "")
    _sync_conversation_metadata(conv)
    return len(messages or [])


def create_user_event(content: Any, **context: Any) -> Any:
    return create_user_input_event(content, **context)


async def process_event(agent: Any, event: Any) -> Any:
    return await agent._process_event(event)
