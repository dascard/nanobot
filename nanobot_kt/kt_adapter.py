"""KT Agent 适配层。

集中放 Nanobot 主链路对 KT conversation/event API 的直接调用。
"""

from __future__ import annotations

from typing import Any

from kohakuterrarium.core.events import create_user_input_event


def message_role(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("role", ""))
    return str(getattr(msg, "role", ""))


def reset_conversation_to_system(agent: Any) -> tuple[int, int]:
    conv = getattr(getattr(agent, "controller", None), "conversation", None)
    if conv is None or not hasattr(conv, "_messages"):
        return (0, 0)
    all_msgs = getattr(conv, "_messages", [])
    before_len = len(all_msgs)
    conv._messages = [m for m in all_msgs if message_role(m) == "system"]
    return before_len, len(conv._messages)


def apply_prompt_messages(agent: Any, messages: list[dict[str, Any]]) -> int:
    conv = getattr(getattr(agent, "controller", None), "conversation", None)
    if conv is None or not hasattr(conv, "_messages"):
        return 0
    conv._messages = []
    for msg in messages or []:
        conv.append(str(msg.get("role") or "system"), msg.get("content") or "")
    return len(messages or [])


def create_user_event(content: Any, **context: Any) -> Any:
    return create_user_input_event(content, **context)


async def process_event(agent: Any, event: Any) -> Any:
    return await agent._process_event(event)
