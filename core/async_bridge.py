"""同步代码调用异步协程的集中桥接工具。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def _run_in_manual_loop(coro: Coroutine[Any, Any, T]) -> T:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
            shutdown_default_executor = getattr(loop, "shutdown_default_executor", None)
            if shutdown_default_executor is not None:
                loop.run_until_complete(shutdown_default_executor())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def _run_in_fresh_loop(coro: Coroutine[Any, Any, T]) -> T:
    runner_cls = getattr(asyncio, "Runner", None)
    if runner_cls is None:
        return _run_in_manual_loop(coro)
    with runner_cls() as runner:
        return runner.run(coro)


def run_awaitable_sync(coro: Coroutine[Any, Any, T]) -> T:
    """在同步入口执行协程；若当前线程已有事件循环，则切到隔离线程。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        context = contextvars.copy_context()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(context.run, _run_in_fresh_loop, coro).result()
    return _run_in_fresh_loop(coro)
