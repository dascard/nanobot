"""私聊缓冲状态辅助模块。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PrivateBufferConfig:
    max_messages: int
    window_seconds: float
    window_with_files_seconds: float
    follower_timeout_seconds: float


@dataclass(frozen=True)
class PrivateBufferOwnerStarted:
    buffer: dict[str, Any]


@dataclass(frozen=True)
class PrivateBufferFollowerJoined:
    done_event: asyncio.Event


@dataclass(frozen=True)
class PrivateBufferSnapshot:
    messages: list[str]
    files: list[str]
    guardrail_task: asyncio.Task[Any]


def join_buffered_messages(messages: Sequence[str]) -> str:
    return "\n---\n".join(message for message in messages if message)


def merge_buffered_files(existing: Sequence[str], incoming: Sequence[str] | None) -> list[str]:
    merged = list(existing)
    for file in incoming or []:
        if file and file not in merged:
            merged.append(file)
    return merged


def private_buffer_window_seconds(files: Sequence[str] | None, config: PrivateBufferConfig) -> float:
    return config.window_with_files_seconds if list(files or []) else config.window_seconds


class PrivateBufferStore:
    def __init__(self, buffers: dict[str, dict[str, Any]], lock: asyncio.Lock) -> None:
        self.buffers = buffers
        self._lock = lock

    async def begin_or_append(
        self,
        user_id: str,
        *,
        merged_query: str,
        files: Sequence[str],
        guardrail_task_factory: Callable[[], asyncio.Task[Any]],
        now: float,
        config: PrivateBufferConfig,
    ) -> PrivateBufferOwnerStarted | PrivateBufferFollowerJoined:
        incoming_files = list(files)
        async with self._lock:
            buf = self.buffers.get(user_id)
            if buf is None or buf["done"].is_set():
                if buf is not None:
                    self.buffers.pop(user_id, None)
                window_seconds = private_buffer_window_seconds(incoming_files, config)
                buf = self.buffers[user_id] = {
                    "queries": [merged_query],
                    "files": incoming_files,
                    "qwen_task": guardrail_task_factory(),
                    "done": asyncio.Event(),
                    "result": None,
                    "answer": None,
                    "deadline": now + window_seconds,
                    "window_seconds": window_seconds,
                    "deadline_changed": asyncio.Event(),
                }
                return PrivateBufferOwnerStarted(buf)

            if len(buf["queries"]) < config.max_messages:
                buf["queries"].append(merged_query)
            else:
                buf["queries"][-1] = join_buffered_messages(
                    [buf["queries"][-1], merged_query]
                )
            buf["files"] = merge_buffered_files(buf.get("files", []), incoming_files)
            window_seconds = private_buffer_window_seconds(incoming_files, config)
            buf["window_seconds"] = window_seconds
            buf["deadline"] = now + window_seconds
            changed = buf.get("deadline_changed")
            if isinstance(changed, asyncio.Event):
                changed.set()
            return PrivateBufferFollowerJoined(buf["done"])

    async def deadline(self, user_id: str) -> float | None:
        async with self._lock:
            buf = self.buffers.get(user_id)
            if buf is None:
                return None
            return float(buf["deadline"])

    async def wait_until_deadline(
        self,
        user_id: str,
        *,
        now: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> bool:
        while True:
            async with self._lock:
                buf = self.buffers.get(user_id)
                if buf is None or buf["done"].is_set():
                    return False

                deadline = float(buf["deadline"])
                remaining = deadline - now()
                if remaining <= 0:
                    return True

                changed = buf.get("deadline_changed")
                if not isinstance(changed, asyncio.Event):
                    changed = asyncio.Event()
                    buf["deadline_changed"] = changed

            sleep_task = asyncio.create_task(sleep(remaining))
            changed_task = asyncio.create_task(changed.wait())
            try:
                done, pending = await asyncio.wait(
                    {sleep_task, changed_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

                if sleep_task in done:
                    sleep_task.result()

                if changed_task in done:
                    changed_task.result()
                    async with self._lock:
                        buf = self.buffers.get(user_id)
                        if buf is not None and buf.get("deadline_changed") is changed:
                            buf["deadline_changed"] = asyncio.Event()
            finally:
                pending_tasks = [
                    task for task in (sleep_task, changed_task) if not task.done()
                ]
                for task in pending_tasks:
                    task.cancel()
                if pending_tasks:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)

    async def snapshot(self, user_id: str) -> PrivateBufferSnapshot | None:
        async with self._lock:
            buf = self.buffers.get(user_id)
            if buf is None:
                return None
            return PrivateBufferSnapshot(
                messages=list(buf["queries"]),
                files=list(buf.get("files", [])),
                guardrail_task=buf["qwen_task"],
            )

    async def store_guardrail_result(self, user_id: str, result: dict[str, Any]) -> None:
        async with self._lock:
            buf = self.buffers.get(user_id)
            if buf is not None:
                buf["result"] = result

    async def finalize(
        self,
        user_id: str,
        answer: str | None = None,
        *,
        clear_window: bool = True,
    ) -> None:
        async with self._lock:
            buf = self.buffers.get(user_id)
            if not buf:
                return
            if answer is not None:
                buf["answer"] = answer
            changed = buf.get("deadline_changed")
            if isinstance(changed, asyncio.Event):
                changed.set()
            if not buf["done"].is_set():
                buf["done"].set()
            if clear_window:
                self.buffers.pop(user_id, None)
