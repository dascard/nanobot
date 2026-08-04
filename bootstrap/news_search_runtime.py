"""新闻检索 Provider 的 Composition Root Adapter。"""

from __future__ import annotations


class NewsSearchProviderAdapter:
    def search_and_extract(
        self,
        query: str,
        max_results: int = 3,
        *,
        persist: bool = False,
        user_id: str = "",
        session_id: str = "",
    ) -> str:
        from nanobot_kt.tools.ai_daily import search_and_extract_news

        return search_and_extract_news(
            query,
            max_results=max_results,
            persist=persist,
            user_id=user_id,
            session_id=session_id,
        )


def bind_news_search_runtime() -> None:
    from core.news_search_runtime import bind_news_search_provider

    bind_news_search_provider(NewsSearchProviderAdapter())


def clear_news_search_runtime() -> None:
    from core.news_search_runtime import clear_news_search_provider

    clear_news_search_provider()


__all__ = ["bind_news_search_runtime", "clear_news_search_runtime"]
