"""测试中的同步协程桥接。"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    with asyncio.Runner() as runner:
        return runner.run(coro)
