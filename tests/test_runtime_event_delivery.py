from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.agent_runtime import RuntimeRunEventKind
from core.tool_plan import ToolPlan, tool_plan_scope
from nanobot_kt.runtime_event_delivery import build_runtime_event_handler


def _text_event(text: str = "未验证正文") -> SimpleNamespace:
    return SimpleNamespace(
        kind=RuntimeRunEventKind.TEXT_DELTA,
        text_delta=text,
        error=None,
    )


@pytest.mark.asyncio
async def test_final_action_tool_plan_suppresses_unverified_text_delta():
    queue: asyncio.Queue[dict] = asyncio.Queue()
    handler = build_runtime_event_handler(
        SimpleNamespace(_run_event_sink=None),
        queue,
    )
    plan = ToolPlan.from_effective_tools(
        enabled={"reply": True},
        chat_type="private",
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "reply",
                    "description": "最终回复",
                    "parameters": {"type": "object"},
                },
            },
        ],
    )

    with tool_plan_scope(plan):
        await handler(_text_event())

    assert queue.empty()


@pytest.mark.asyncio
async def test_tool_plan_without_final_action_keeps_text_delta():
    queue: asyncio.Queue[dict] = asyncio.Queue()
    handler = build_runtime_event_handler(
        SimpleNamespace(_run_event_sink=None),
        queue,
    )
    plan = ToolPlan.from_effective_tools(
        enabled={"memory_query": True},
        chat_type="private",
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "memory_query",
                    "description": "查询记忆",
                    "parameters": {"type": "object"},
                },
            },
        ],
    )

    with tool_plan_scope(plan):
        await handler(_text_event("正常片段"))

    assert await queue.get() == {"status": "delta", "text": "正常片段"}
