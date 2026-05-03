"""SourceProvider 抽象接口。"""

from typing import Protocol, runtime_checkable
from ..schema import NewsItem


@runtime_checkable
class SourceProvider(Protocol):
    name: str

    def fetch(self, limit: int) -> list[NewsItem]:
        ...
