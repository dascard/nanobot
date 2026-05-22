import asyncio
import json
from datetime import datetime
from pathlib import Path


def _news_item(title, source_name, group="core_provider", url=None, summary="摘要"):
    from creatures.nanobot.prompts.skills.news_search.news_daily.schema import NewsItem

    return NewsItem(
        title=title,
        url=url or f"https://example.com/{source_name}/{title}",
        summary=summary,
        source_name=source_name,
        source_group=group,
        domain="example.com",
        published_at="2026-05-22",
        trust=0.9,
        score=1.0,
    )


def test_ai_daily_is_primary_tool_name_and_news_search_is_legacy_alias(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search.tool import AiDailyTool, NewsSearchTool

    calls = []

    def fake_daily(query, mode="quality", limit=8):
        calls.append((query, mode, limit))
        return "<article>AI daily</article>"

    monkeypatch.setattr("creatures.nanobot.prompts.skills.news_search.tool._run_news_daily_pipeline", fake_daily)

    ai_daily = AiDailyTool()
    legacy = NewsSearchTool()

    assert ai_daily.tool_name == "ai_daily"
    assert legacy.tool_name == "news_search"
    assert "AI" in ai_daily.description
    assert "兼容" in legacy.description

    schema = ai_daily.get_parameters_schema()
    assert schema["properties"]["max_results"]["default"] == 8
    assert "freshness" in schema["properties"]
    assert "target_date" in schema["properties"]

    result = asyncio.run(ai_daily.execute({"query": "人工智能 科技 最新新闻", "max_results": 5}))
    assert result.success
    assert calls == [("人工智能 科技 最新新闻", "quality", 8)]


def test_ai_daily_is_registered_in_kt_config():
    config_text = Path("creatures/nanobot/config.yaml").read_text(encoding="utf-8")

    assert "name: ai_daily" in config_text
    assert "module: nanobot_kt.tools.ai_daily" in config_text
    assert "class: AiDailyTool" in config_text
    assert "name: news_search" in config_text


def test_source_registry_uses_source_specific_adapters():
    from creatures.nanobot.prompts.skills.news_search.news_daily.sources.official import (
        DEFAULT_SOURCES,
        create_provider_for_source,
    )
    from creatures.nanobot.prompts.skills.news_search.news_daily.sources.adapters import (
        AnthropicNewsProvider,
        CohereBlogProvider,
        DeepSeekUpdatesProvider,
        KimiBlogProvider,
        MetaAIBlogProvider,
        MistralNewsProvider,
        QwenArticleApiProvider,
        XAINewsProvider,
    )
    from creatures.nanobot.prompts.skills.news_search.news_daily.sources.rss import RSSProvider

    expected = {
        "anthropic_news": AnthropicNewsProvider,
        "mistral_news": MistralNewsProvider,
        "deepseek_news": DeepSeekUpdatesProvider,
        "qwen_blog": QwenArticleApiProvider,
        "kimi_blog": KimiBlogProvider,
        "xai_news": XAINewsProvider,
        "cohere_blog": CohereBlogProvider,
        "meta_ai_blog": MetaAIBlogProvider,
    }
    configs = {cfg.name: cfg for cfg in DEFAULT_SOURCES}

    for name, cls in expected.items():
        assert isinstance(create_provider_for_source(configs[name]), cls)

    deepmind = create_provider_for_source(configs["google_deepmind_news"])
    assert isinstance(deepmind, RSSProvider)
    assert deepmind.url.endswith("/rss.xml")
    assert configs["deepseek_news"].url.endswith("/updates")


def test_mistral_adapter_extracts_nextjs_posts():
    from creatures.nanobot.prompts.skills.news_search.news_daily.sources.adapters import MistralNewsProvider

    html = (
        '<script>self.__next_f.push([1,"{\\"posts\\":[{\\"slug\\":\\"vibe-remote\\",'
        '\\"date\\":\\"2026-04-29T12:00:00\\",\\"title\\":\\"Remote agents in Vibe\\",'
        '\\"description\\":\\"Introducing remote coding agents.\\"}]}"])</script>'
    )

    items = MistralNewsProvider("https://mistral.ai/news", "mistral_news", 0.94)._extract(html, 3)

    assert len(items) == 1
    assert items[0].title == "Remote agents in Vibe"
    assert items[0].url == "https://mistral.ai/news/vibe-remote"
    assert items[0].published_at == "2026-04-29"
    assert "remote coding" in items[0].summary


def test_qwen_adapter_parses_page_config_api_payload():
    from creatures.nanobot.prompts.skills.news_search.news_daily.sources.adapters import QwenArticleApiProvider

    payload = json.dumps([
        {
            "id": "qwen3-coder",
            "title": "Qwen3-Coder is available",
            "date": "2026-05-20T08:00:00.000Z",
            "description": "A coding model release.",
        }
    ])

    items = QwenArticleApiProvider("https://qwen.ai/api/page_config?code=news.news-list", "qwen_blog", 0.92)._parse(payload, 3)

    assert len(items) == 1
    assert items[0].title == "Qwen3-Coder is available"
    assert items[0].url == "https://qwen.ai/blog?id=qwen3-coder"
    assert items[0].published_at == "2026-05-20"


def test_kimi_adapter_extracts_card_dates_from_blog_index():
    from creatures.nanobot.prompts.skills.news_search.news_daily.sources.adapters import KimiBlogProvider

    html = """
    <a href="/blog/kimi-k2-6" class="menu-card">
      <h4 class="card-title">Kimi K2.6</h4>
      <p class="card-desc">Advancing Open-Source Coding</p>
      <p class="card-date">2026/04/20</p>
    </a>
    <a href="/blog/kimi-k2-thinking" class="menu-card">
      <h4 class="card-title">Kimi K2 Thinking</h4>
      <p class="card-desc">A thinking model release.</p>
      <p class="card-date">2025/11/06</p>
    </a>
    """

    items = KimiBlogProvider("https://www.kimi.com/blog", "kimi_blog", 0.92)._extract(html, 3)

    assert [item.title for item in items] == ["Kimi K2.6", "Kimi K2 Thinking"]
    assert items[0].url == "https://www.kimi.com/blog/kimi-k2-6"
    assert items[0].published_at == "2026-04-20"
    assert items[1].published_at == "2025-11-06"
    assert "Open-Source Coding" in items[0].summary


def test_filter_recent_drops_unknown_dates_by_default():
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.normalize import filter_recent

    dated = _news_item("今天新闻", "openai_news")
    dated.published_at = datetime.now().strftime("%Y-%m-%d")
    missing_date = _news_item("无日期旧内容", "kimi_blog")
    missing_date.published_at = ""
    invalid_date = _news_item("坏日期旧内容", "kimi_blog")
    invalid_date.published_at = "unknown"

    result = filter_recent([dated, missing_date, invalid_date], hours=72)

    assert result == [dated]


def test_filter_recent_can_keep_unknown_dates_when_requested():
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.normalize import filter_recent

    missing_date = _news_item("无日期但保留", "curated")
    missing_date.published_at = ""

    assert filter_recent([missing_date], hours=72, keep_unknown=True) == [missing_date]


def test_deepseek_adapter_extracts_changelog_sections():
    from creatures.nanobot.prompts.skills.news_search.news_daily.sources.adapters import DeepSeekUpdatesProvider

    html = """
    <div class="theme-doc-markdown markdown">
      <h2 id="date-2026-04-24">Date: 2026-04-24</h2>
      <h3 id="deepseek-v4">DeepSeek-V4</h3>
      <p>The DeepSeek API now supports V4-Pro and V4-Flash.</p>
      <h3 id="new-api">OpenAI-compatible API Update</h3>
      <p>New API options are available.</p>
    </div>
    """

    items = DeepSeekUpdatesProvider("https://api-docs.deepseek.com/updates", "deepseek_news", 0.92)._extract(html, 3)

    assert [item.title for item in items] == ["DeepSeek-V4", "OpenAI-compatible API Update"]
    assert all(item.published_at == "2026-04-24" for item in items)
    assert items[0].url == "https://api-docs.deepseek.com/updates#deepseek-v4"


def test_deterministic_digest_render_has_source_links_and_index():
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.digest import build_digest_deterministic
    from creatures.nanobot.prompts.skills.news_search.render import render_html

    digest = build_digest_deterministic([
        _news_item("OpenAI 发布 Codex 更新", "openai_news", url="https://openai.com/index/codex/"),
        _news_item("DeepSeek 更新 API 并发限制", "deepseek_news", url="https://api-docs.deepseek.com/updates#api"),
    ], query="今天 AI 日报", mode="quality_fallback")

    html = render_html(digest)

    assert "quality_fallback mode" in html
    assert "来源索引" in html
    assert "<table" in html
    assert 'href="https://openai.com/index/codex/"' in html
    assert "OpenAI 发布 Codex 更新" in html
    assert "DeepSeek 更新 API 并发限制" in html


def test_select_items_by_quota_limits_each_source_to_two_items():
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.select_ import select_items_by_quota

    items = [
        _news_item(f"NVIDIA {i}", "nvidia_blog", group="core_provider")
        for i in range(5)
    ] + [
        _news_item(f"OpenAI {i}", "openai_news", group="core_provider")
        for i in range(3)
    ] + [
        _news_item("MIT item", "mit_techreview_ai", group="ai_media")
    ]

    selected = select_items_by_quota(items, max_items=8)
    counts = {}
    for item in selected:
        counts[item.source_name] = counts.get(item.source_name, 0) + 1

    assert counts["nvidia_blog"] == 2
    assert counts["openai_news"] == 2
    assert all(count <= 2 for count in counts.values())


def test_enrich_skips_openai_index_pages_with_existing_summary(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline import enrich

    called = {"count": 0}

    def fake_fetch(url, timeout=8):
        called["count"] += 1
        return "detail"

    monkeypatch.setattr(enrich, "_fetch_detail", fake_fetch)

    item = _news_item(
        "OpenAI item",
        "openai_news",
        url="https://openai.com/index/model-disproves-discrete-geometry-conjecture/",
        summary="RSS 已有足够摘要",
    )
    qbitai = _news_item(
        "QbitAI item",
        "qbitai",
        url="https://www.qbitai.com/2026/05/422624.html",
        summary="RSS 已有足够摘要",
    )
    result = enrich.enrich_items([item, qbitai], max_fetch=12)

    assert result[0].detail_text == ""
    assert result[1].detail_text == ""
    assert called["count"] == 0
