from __future__ import annotations

import subprocess
import sys


def test_runtime_cache_imports_without_runtime_tool_dependencies():
    code = """
import sys
from creatures.nanobot.prompts.skills.news_search import runtime_cache
blocked = [
    "nanobot_kt.tools.ai_daily",
    "duckduckgo_search",
    "trafilatura",
    "kohakuterrarium.modules.tool.base",
]
loaded = [name for name in blocked if name in sys.modules]
assert not loaded, loaded
assert runtime_cache._NEWS_SEARCH_CACHE == {}
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_runtime_cache_key_preserves_daily_and_query_contract():
    from creatures.nanobot.prompts.skills.news_search import runtime_cache

    daily_key = runtime_cache._news_search_cache_key(
        "2026年5月1日 AI 日报",
        8,
        mode="quality",
        user_id="user-a",
        session_id="session-a",
    )
    query_key = runtime_cache._news_search_cache_key(
        "  GPT-5   NEWS  ",
        3,
        mode="fast",
        user_id="user-a",
        session_id="session-a",
    )

    assert daily_key == ("v2_20260503", "daily_ai", "2026-05-01", 8, "quality")
    assert query_key == ("v2_20260503", "query", "gpt-5 news", 3, "fast")


def test_tool_cache_facade_shares_runtime_state_and_honors_legacy_ttl(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search import runtime_cache
    import nanobot_kt.tools.ai_daily as news_tool

    key = runtime_cache._news_search_cache_key("AI news", 3)
    news_tool._NEWS_SEARCH_CACHE.clear()

    assert news_tool._NEWS_SEARCH_CACHE is runtime_cache._NEWS_SEARCH_CACHE
    assert news_tool._NEWS_SEARCH_CACHE_LOCK is runtime_cache._NEWS_SEARCH_CACHE_LOCK

    runtime_cache._store_cached_news_result(key, "<article>cached</article>")
    assert news_tool._get_cached_news_result(key) == "<article>cached</article>"

    monkeypatch.setattr(news_tool, "NEWS_SEARCH_CACHE_TTL_SECONDS", -1)
    assert news_tool._get_cached_news_result(key) is None


def test_runtime_cache_evicts_before_inserting_beyond_capacity():
    from creatures.nanobot.prompts.skills.news_search import runtime_cache

    cache = runtime_cache._NEWS_SEARCH_CACHE
    cache.clear()
    clock = iter(range(runtime_cache.NEWS_SEARCH_CACHE_MAX_ENTRIES + 1))
    try:
        for index in range(runtime_cache.NEWS_SEARCH_CACHE_MAX_ENTRIES):
            runtime_cache._store_cached_news_result(
                ("key", index),
                f"value-{index}",
                monotonic=lambda: next(clock),
            )

        runtime_cache._store_cached_news_result(
            ("key", "new"),
            "new-value",
            monotonic=lambda: next(clock),
        )

        assert len(cache) == runtime_cache.NEWS_SEARCH_CACHE_MAX_ENTRIES
        assert ("key", 0) not in cache
        assert cache[("key", "new")][1] == "new-value"
    finally:
        cache.clear()


def test_runtime_cache_updates_existing_key_without_evicting_other_entries():
    from creatures.nanobot.prompts.skills.news_search import runtime_cache

    cache = runtime_cache._NEWS_SEARCH_CACHE
    cache.clear()
    clock = iter(range(runtime_cache.NEWS_SEARCH_CACHE_MAX_ENTRIES + 1))
    try:
        for index in range(runtime_cache.NEWS_SEARCH_CACHE_MAX_ENTRIES):
            runtime_cache._store_cached_news_result(
                ("key", index),
                f"value-{index}",
                monotonic=lambda: next(clock),
            )

        runtime_cache._store_cached_news_result(
            ("key", 0),
            "updated-value",
            monotonic=lambda: next(clock),
        )

        assert len(cache) == runtime_cache.NEWS_SEARCH_CACHE_MAX_ENTRIES
        assert cache[("key", 0)][1] == "updated-value"
        assert ("key", 1) in cache
    finally:
        cache.clear()
