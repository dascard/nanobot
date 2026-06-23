from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_streaming_helpers_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_streaming_helpers.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source
    assert "_persist_chat_turn(" not in source


def test_stream_event_coalescer_merges_delta_until_non_delta():
    from api.chat_streaming_helpers import StreamEventCoalescer

    coalescer = StreamEventCoalescer()

    assert coalescer.feed({"status": "delta", "text": "你"}) == []
    assert coalescer.feed({"status": "delta", "text": "好"}) == []
    assert coalescer.feed({"status": "progress", "text": "工具"}) == [
        {"status": "delta", "text": "你好"},
        {"status": "progress", "text": "工具"},
    ]


def test_stream_event_coalescer_flushes_tail_delta_once():
    from api.chat_streaming_helpers import StreamEventCoalescer

    coalescer = StreamEventCoalescer()
    coalescer.feed({"status": "delta", "text": "最"})
    coalescer.feed({"status": "delta", "text": "后"})

    assert coalescer.flush() == {"status": "delta", "text": "最后"}
    assert coalescer.flush() is None


def test_collect_ready_stream_events_drains_current_queue_for_delta_first_event():
    from api.chat_streaming_helpers import (
        StreamEventCoalescer,
        collect_ready_stream_events,
    )

    queue: asyncio.Queue[object] = asyncio.Queue()
    queue.put_nowait({"status": "delta", "text": "好"})
    queue.put_nowait("skip")
    queue.put_nowait({"status": "progress", "text": "工具"})
    queue.put_nowait({"status": "delta", "text": "！"})

    def normalize(raw: object) -> dict[str, object] | None:
        if raw == "skip":
            return None
        assert isinstance(raw, dict)
        return raw

    events = collect_ready_stream_events(
        {"status": "delta", "text": "你"},
        queue,
        normalize_event=normalize,
        coalescer=StreamEventCoalescer(),
    )

    assert events == [
        {"status": "delta", "text": "你好"},
        {"status": "progress", "text": "工具"},
        {"status": "delta", "text": "！"},
    ]
    assert queue.empty()


def test_collect_ready_stream_events_does_not_drain_for_non_delta_first_event():
    from api.chat_streaming_helpers import (
        StreamEventCoalescer,
        collect_ready_stream_events,
    )

    queue: asyncio.Queue[object] = asyncio.Queue()
    queue.put_nowait({"status": "delta", "text": "later"})

    events = collect_ready_stream_events(
        {"status": "progress", "text": "先发"},
        queue,
        normalize_event=lambda raw: raw if isinstance(raw, dict) else None,
        coalescer=StreamEventCoalescer(),
    )

    assert events == [{"status": "progress", "text": "先发"}]
    assert not queue.empty()


@pytest.mark.asyncio
async def test_drain_stream_queue_until_task_done_drains_bounded_queue():
    from api.chat_streaming_helpers import drain_stream_queue_until_task_done

    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)

    async def producer() -> None:
        await queue.put({"status": "delta", "text": "A"})
        await queue.put({"status": "delta", "text": "B"})
        await queue.put({"status": "final", "text": "AB"})

    runner_task = asyncio.create_task(producer())

    await drain_stream_queue_until_task_done(
        queue,
        runner_task,
        poll_timeout=0.001,
    )

    assert runner_task.done()
    assert queue.empty()
