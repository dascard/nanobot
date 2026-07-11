from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _normalize(raw: Any) -> dict[str, Any] | None:
    if raw == "skip":
        return None
    if isinstance(raw, dict):
        return dict(raw)
    return None


def test_chat_sse_loop_module_does_not_import_parent_routes_or_runtime_side_effects():
    source = _source("api/chat_sse_loop.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "StreamingResponse" not in source
    assert "APIRouter" not in source
    assert "BackgroundTasks" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source
    assert "_persist_chat_turn(" not in source
    assert "push_envelope_to_qq" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


@pytest.mark.asyncio
async def test_iter_chat_stream_events_emits_heartbeat_when_idle():
    from api.chat_sse_loop import ChatSseLoopCallbacks, iter_chat_stream_events
    from api.chat_streaming_helpers import StreamEventCoalescer

    queue: asyncio.Queue[Any] = asyncio.Queue()
    done = asyncio.Event()
    iterator = iter_chat_stream_events(
        queue,
        done,
        heartbeat_interval=0.001,
        coalescer=StreamEventCoalescer(),
        callbacks=ChatSseLoopCallbacks(normalize_event=_normalize),
    )

    event = await asyncio.wait_for(anext(iterator), timeout=1)
    done.set()
    await iterator.aclose()

    assert event == {"status": "heartbeat"}


@pytest.mark.asyncio
async def test_iter_chat_stream_events_stops_after_done_without_waiting_for_heartbeat():
    from api.chat_sse_loop import ChatSseLoopCallbacks, iter_chat_stream_events
    from api.chat_streaming_helpers import StreamEventCoalescer

    queue: asyncio.Queue[Any] = asyncio.Queue()
    done = asyncio.Event()
    done.set()

    started = time.perf_counter()
    events = [
        event
        async for event in iter_chat_stream_events(
            queue,
            done,
            heartbeat_interval=5.0,
            coalescer=StreamEventCoalescer(),
            callbacks=ChatSseLoopCallbacks(normalize_event=_normalize),
        )
    ]
    elapsed = time.perf_counter() - started

    assert events == []
    assert elapsed < 0.2


@pytest.mark.asyncio
async def test_iter_chat_stream_events_coalesces_delta_before_progress_and_flushes_tail():
    from api.chat_sse_loop import ChatSseLoopCallbacks, iter_chat_stream_events
    from api.chat_streaming_helpers import StreamEventCoalescer

    queue: asyncio.Queue[Any] = asyncio.Queue()
    queue.put_nowait({"status": "delta", "text": "你"})
    queue.put_nowait({"status": "delta", "text": "好"})
    queue.put_nowait("skip")
    queue.put_nowait({"status": "progress", "text": "工具"})
    queue.put_nowait({"status": "delta", "text": "！"})
    done = asyncio.Event()
    done.set()

    events = [
        event
        async for event in iter_chat_stream_events(
            queue,
            done,
            heartbeat_interval=5.0,
            coalescer=StreamEventCoalescer(),
            callbacks=ChatSseLoopCallbacks(normalize_event=_normalize),
        )
    ]

    assert events == [
        {"status": "delta", "text": "你好"},
        {"status": "progress", "text": "工具"},
        {"status": "delta", "text": "！"},
    ]


def test_parent_stream_chat_delegates_cold_body_and_keeps_streaming_response_boundary():
    source = _source("api/routes.py")

    assert "chat_route_runner" in source
    assert "chat_route_runner.ColdChatStreamingBody" in source
    assert "chat_route_runner.iter_streaming_chat_response" not in source
    assert "StreamingResponse(" in source
    assert "async def _stream_chat" not in source
    assert "chat_sse_loop.iter_chat_stream_events(" not in source
    assert "asyncio.wait(\n                        {get_task, done_task}," not in source
