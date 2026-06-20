"""测试中的同步协程桥接。"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any, TypeVar

from core.async_bridge import run_awaitable_sync

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    return run_awaitable_sync(coro)
