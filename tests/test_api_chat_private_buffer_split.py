from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_private_buffer_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_private_buffer.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source
    assert "_detect_guardrail(" not in source
    assert "_persist_chat_turn(" not in source


def test_private_buffer_helpers_preserve_message_file_and_window_contracts():
    from api.chat_private_buffer import (
        PrivateBufferConfig,
        join_buffered_messages,
        merge_buffered_files,
        private_buffer_window_seconds,
    )

    config = PrivateBufferConfig(
        max_messages=10,
        window_seconds=5.0,
        window_with_files_seconds=10.0,
        follower_timeout_seconds=900.0,
    )

    assert join_buffered_messages(["第一句", "", "第二句"]) == "第一句\n---\n第二句"
    assert merge_buffered_files(["a.png", "b.png"], ["", "b.png", "c.png"]) == [
        "a.png",
        "b.png",
        "c.png",
    ]
    assert private_buffer_window_seconds([], config) == 5.0
    assert private_buffer_window_seconds(["a.png"], config) == 10.0


def test_parent_private_buffer_wrappers_remain_in_routes_and_patchable(monkeypatch):
    from api import routes

    assert routes._join_buffered_messages.__module__ == "api.routes"
    assert routes._merge_buffered_files.__module__ == "api.routes"
    assert routes._private_buffer_window_seconds.__module__ == "api.routes"
    assert routes._finalize_private_buffer.__module__ == "api.routes"

    monkeypatch.setattr(routes, "PRIVATE_BUFFER_WINDOW_SECONDS", 0.25)
    monkeypatch.setattr(routes, "PRIVATE_BUFFER_WINDOW_WITH_FILES_SECONDS", 0.75)

    assert routes._private_buffer_window_seconds(None) == 0.25
    assert routes._private_buffer_window_seconds(["https://example.com/a.png"]) == 0.75


@pytest.mark.asyncio
async def test_private_buffer_store_creates_appends_snapshots_and_finalizes():
    from api.chat_private_buffer import (
        PrivateBufferConfig,
        PrivateBufferFollowerJoined,
        PrivateBufferOwnerStarted,
        PrivateBufferStore,
    )

    buffers: dict[str, dict] = {}
    store = PrivateBufferStore(buffers, asyncio.Lock())
    config = PrivateBufferConfig(
        max_messages=3,
        window_seconds=5.0,
        window_with_files_seconds=10.0,
        follower_timeout_seconds=900.0,
    )
    created_tasks: list[asyncio.Task[dict[str, str]]] = []

    def task_factory() -> asyncio.Task[dict[str, str]]:
        task = asyncio.create_task(asyncio.sleep(0, result={"status": "reply"}))
        created_tasks.append(task)
        return task

    owner = await store.begin_or_append(
        "u1",
        merged_query="第一句",
        files=[],
        guardrail_task_factory=task_factory,
        now=1.0,
        config=config,
    )
    assert isinstance(owner, PrivateBufferOwnerStarted)
    assert buffers["u1"]["queries"] == ["第一句"]
    assert buffers["u1"]["files"] == []
    assert buffers["u1"]["deadline"] == 6.0
    assert buffers["u1"]["window_seconds"] == 5.0
    assert len(created_tasks) == 1

    follower = await store.begin_or_append(
        "u1",
        merged_query="第二句",
        files=["a.png"],
        guardrail_task_factory=task_factory,
        now=3.0,
        config=config,
    )
    assert isinstance(follower, PrivateBufferFollowerJoined)
    assert follower.done_event is buffers["u1"]["done"]
    assert buffers["u1"]["queries"] == ["第一句", "第二句"]
    assert buffers["u1"]["files"] == ["a.png"]
    assert buffers["u1"]["deadline"] == 13.0
    assert buffers["u1"]["window_seconds"] == 10.0
    assert len(created_tasks) == 1

    snapshot = await store.snapshot("u1")
    assert snapshot is not None
    assert snapshot.messages == ["第一句", "第二句"]
    assert snapshot.files == ["a.png"]
    assert snapshot.guardrail_task is created_tasks[0]
    snapshot.messages.append("不会写回")
    assert buffers["u1"]["queries"] == ["第一句", "第二句"]

    assert await store.deadline("u1") == 13.0
    await store.store_guardrail_result("u1", {"status": "reply"})
    assert buffers["u1"]["result"] == {"status": "reply"}

    await store.finalize("u1", "答案", clear_window=False)
    assert buffers["u1"]["answer"] == "答案"
    assert buffers["u1"]["done"].is_set()

    await store.finalize("u1")
    assert "u1" not in buffers
    await asyncio.gather(*created_tasks)


@pytest.mark.asyncio
async def test_private_buffer_store_overflow_coalesces_latest_message():
    from api.chat_private_buffer import PrivateBufferConfig, PrivateBufferStore

    buffers: dict[str, dict] = {}
    store = PrivateBufferStore(buffers, asyncio.Lock())
    config = PrivateBufferConfig(
        max_messages=2,
        window_seconds=5.0,
        window_with_files_seconds=10.0,
        follower_timeout_seconds=900.0,
    )

    def task_factory() -> asyncio.Task[dict[str, str]]:
        return asyncio.create_task(asyncio.sleep(0, result={"status": "reply"}))

    await store.begin_or_append(
        "u-overflow",
        merged_query="第一句",
        files=[],
        guardrail_task_factory=task_factory,
        now=0.0,
        config=config,
    )
    await store.begin_or_append(
        "u-overflow",
        merged_query="第二句",
        files=[],
        guardrail_task_factory=task_factory,
        now=1.0,
        config=config,
    )
    await store.begin_or_append(
        "u-overflow",
        merged_query="第三句",
        files=[],
        guardrail_task_factory=task_factory,
        now=2.0,
        config=config,
    )

    assert buffers["u-overflow"]["queries"] == ["第一句", "第二句\n---\n第三句"]
    await store.finalize("u-overflow")
