"""新闻检索 Provider 的进程级框架无关 Port。"""

from __future__ import annotations

from threading import RLock
from typing import Protocol, runtime_checkable


class NewsSearchRuntimeUnavailableError(RuntimeError):
    """新闻检索 Provider 尚未绑定或已经停止。"""


@runtime_checkable
class NewsSearchProviderPort(Protocol):
    def search_and_extract(
        self,
        query: str,
        max_results: int = 3,
        *,
        persist: bool = False,
        user_id: str = "",
        session_id: str = "",
    ) -> str: ...


_lock = RLock()
_provider: NewsSearchProviderPort | None = None


def bind_news_search_provider(provider: NewsSearchProviderPort) -> None:
    if not isinstance(provider, NewsSearchProviderPort):
        raise TypeError("provider 未实现 NewsSearchProviderPort")
    global _provider
    with _lock:
        if _provider is not None:
            raise RuntimeError("News Search Runtime 已绑定")
        _provider = provider


def clear_news_search_provider() -> None:
    global _provider
    with _lock:
        _provider = None


def get_news_search_provider() -> NewsSearchProviderPort:
    with _lock:
        provider = _provider
    if provider is None:
        raise NewsSearchRuntimeUnavailableError("新闻检索 Provider 当前不可用")
    return provider


__all__ = [
    "NewsSearchProviderPort",
    "NewsSearchRuntimeUnavailableError",
    "bind_news_search_provider",
    "clear_news_search_provider",
    "get_news_search_provider",
]
