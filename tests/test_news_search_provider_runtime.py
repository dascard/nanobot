import pytest

from core.news_search_runtime import (
    NewsSearchRuntimeUnavailableError,
    bind_news_search_provider,
    clear_news_search_provider,
    get_news_search_provider,
)


class _FakeNewsSearchProvider:
    def search_and_extract(
        self,
        query: str,
        max_results: int = 3,
        *,
        persist: bool = False,
        user_id: str = "",
        session_id: str = "",
    ) -> str:
        return ":".join(
            (
                query,
                str(max_results),
                str(persist),
                user_id,
                session_id,
            )
        )


@pytest.fixture(autouse=True)
def _reset_news_search_runtime():
    clear_news_search_provider()
    yield
    clear_news_search_provider()


def test_news_search_runtime_requires_explicit_binding():
    with pytest.raises(
        NewsSearchRuntimeUnavailableError,
        match="新闻检索 Provider 当前不可用",
    ):
        get_news_search_provider()


def test_news_search_runtime_delegates_to_bound_provider():
    provider = _FakeNewsSearchProvider()
    bind_news_search_provider(provider)

    result = get_news_search_provider().search_and_extract(
        "模型更新",
        max_results=5,
        persist=True,
        user_id="u1",
        session_id="s1",
    )

    assert result == "模型更新:5:True:u1:s1"


def test_news_search_runtime_rejects_duplicate_binding():
    bind_news_search_provider(_FakeNewsSearchProvider())

    with pytest.raises(RuntimeError, match="News Search Runtime 已绑定"):
        bind_news_search_provider(_FakeNewsSearchProvider())
