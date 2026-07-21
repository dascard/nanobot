"""HTTP 单元测试的轻量客户端辅助函数。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient


@contextmanager
def open_test_client_without_lifespan(app: Any) -> Iterator[TestClient]:
    """关闭客户端资源，但不运行与当前断言无关的应用 lifespan。"""

    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()


__all__ = ["open_test_client_without_lifespan"]
