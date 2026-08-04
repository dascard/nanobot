from tests.async_helpers import run_async
import json

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone

import nanobot_kt.tools.ai_daily as news_tool
from nanobot_kt.tools.ai_daily import (
    AiDailyTool,
    WebTools,
    search_and_extract_news,
    _parse_news_layout_payload,
    _merge_layout_with_fallback,
)

def test_web_search_mock(monkeypatch):
    """测试 WebSearchTool 是否能正确处理搜刮结果"""
    monkeypatch.setattr(news_tool, "NEWS_SEARCH_DDG_ENABLED", True)
    monkeypatch.setattr(news_tool, "_fetch_multi_rss", lambda query=None, max_results=3: [])
    monkeypatch.setattr(news_tool, "_fetch_juya_rss", lambda max_results=3, target_date=None: [])
    mock_results = [
        {"title": "AI News 1", "href": "http://test1.com", "body": "Snippet 1"},
        {"title": "AI News 2", "href": "http://test2.com", "body": "Snippet 2"},
    ]

    with patch("nanobot_kt.tools.ai_daily.DDGS") as mock_ddgs:
        # Mock DDGS context manager and text method
        mock_instance = mock_ddgs.return_value.__enter__.return_value
        mock_instance.text.return_value = mock_results
        
        results = WebTools.search("test query")
        
        assert len(results) == 2
        assert results[0]["title"] == "AI News 1"
        assert results[1]["href"] == "http://test2.com"

def test_web_extract_mock():
    """测试网页内容提取工具"""
    with patch("nanobot_kt.tools.ai_daily.trafilatura.fetch_url") as mock_fetch, \
         patch("nanobot_kt.tools.ai_daily.trafilatura.extract") as mock_extract:
        
        mock_fetch.return_value = "<html>content</html>"
        mock_extract.return_value = "Extracted Plain Text"
        
        content = WebTools.extract_web_content("http://example.com")
        
        assert content == "Extracted Plain Text"
        mock_fetch.assert_called_once_with("http://example.com", timeout=5)

def test_combined_news_tool():
    """测试组合出的新闻搜集工具逻辑"""
    with patch("nanobot_kt.tools.ai_daily.WebTools.search") as mock_search, \
         patch("nanobot_kt.tools.ai_daily.WebTools.extract_web_content") as mock_extract, \
         patch("nanobot_kt.tools.ai_daily._model_should_deepen") as mock_deepen, \
         patch("config.NEW_API_KEY", ""):
        
        mock_search.return_value = [
            {
                "title": "Title A",
                "href": "https://example.com/a",
                "body": "body_a",
                "search_strategy": "web_ddg",
            }
        ]
        mock_extract.return_value = "Long content from A"
        mock_deepen.return_value = (False, "test")
        
        final_report = search_and_extract_news("query")

        assert "<article" in final_report
        assert "class=\"news-brief\"" in final_report
        assert "今日结论" in final_report
        assert "重点速览" in final_report
        assert "来源索引" in final_report
        assert "<table" in final_report
        assert "Title A" in final_report
        assert "Title A" in final_report
        assert "Long content from A" in final_report
        assert "https://example.com/a" in final_report
        assert "query" in final_report


def test_combined_news_tool_output_matches_qqbot_markdown_render_patterns():
    """输出应包含 QQbot 复杂 Markdown 检测所需的标题和表格。"""
    with patch("nanobot_kt.tools.ai_daily.WebTools.search") as mock_search, \
         patch("nanobot_kt.tools.ai_daily.WebTools.extract_web_content") as mock_extract, \
         patch("nanobot_kt.tools.ai_daily._model_should_deepen") as mock_deepen, \
         patch("config.NEW_API_KEY", ""):

        mock_search.return_value = [
            {
                "title": "DeepSeek 新发布",
                "href": "https://example.com/deepseek",
                "body": "价格更新与免费额度",
                "search_strategy": "rss:test",
            }
        ]
        mock_extract.return_value = "DeepSeek 提供了新的免费额度和更低的 token 价格。"
        mock_deepen.return_value = (True, "planner")

        final_report = search_and_extract_news("deepseek 最新资讯")

        assert "<article" in final_report
        assert "今日结论" in final_report
        assert "机会关注" in final_report
        assert "<table" in final_report
        assert "DeepSeek 新发布" in final_report


def test_web_search_news_query_uses_daily_timelimit_and_merges_rss_with_web(monkeypatch):
    monkeypatch.setattr(news_tool, "NEWS_SEARCH_DDG_ENABLED", True)
    monkeypatch.setattr(news_tool, "_fetch_juya_rss", lambda max_results=3, target_date=None: [])
    fresh_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    rss_results = [
        {
            "title": "RSS AI Daily",
            "href": "https://rss.example.com/a",
            "body": "rss body",
            "date": fresh_date,
            "source_weight": 3,
            "search_strategy": "rss:test",
        }
    ]
    web_results = [
        {
            "title": "Web Breaking News",
            "href": "https://web.example.com/b",
            "body": "web body",
        }
    ]
    monkeypatch.setattr(news_tool, "_fetch_multi_rss", lambda query=None, max_results=3: rss_results)

    with patch("nanobot_kt.tools.ai_daily.DDGS") as mock_ddgs:
        mock_instance = mock_ddgs.return_value.__enter__.return_value
        mock_instance.text.return_value = web_results

        results = WebTools.search("AI 最新资讯", max_results=3, deep=False)

        assert len(results) == 2, f"Expected 2 results, got {len(results)}: {results}"
        assert any(item["href"] == "https://rss.example.com/a" for item in results)
        assert any(item["href"] == "https://web.example.com/b" for item in results)
        _, kwargs = mock_instance.text.call_args
        assert kwargs["timelimit"] == "d"


def test_parse_news_layout_payload_accepts_small_json_schema():
    raw = """
```json
{
  "title": "AI 今日速报",
  "subtitle": "价格、发布与开源动态",
  "summary": "今天最值得关注的是价格窗口和新模型发布。",
  "highlights": ["Qwen 新模型发布", "DeepSeek 价格继续下探"],
  "alerts": ["注意比较 API 定价变化"],
  "closing": "适合继续跟踪今天和近 24 小时内的发布。"
}
```
""".strip()

    parsed = _parse_news_layout_payload(raw)

    assert parsed["title"] == "AI 今日速报"
    assert parsed["subtitle"] == "价格、发布与开源动态"
    assert parsed["summary"].startswith("今天最值得关注")
    assert parsed["highlights"] == ["Qwen 新模型发布", "DeepSeek 价格继续下探"]
    assert parsed["alerts"] == ["注意比较 API 定价变化"]
    assert parsed["closing"].startswith("适合继续跟踪")


def test_combined_news_tool_renders_fixed_html_template_from_structured_layout():
    with patch("nanobot_kt.tools.ai_daily.WebTools.search") as mock_search, \
         patch("nanobot_kt.tools.ai_daily.WebTools.extract_web_content") as mock_extract, \
         patch("nanobot_kt.tools.ai_daily._model_should_deepen") as mock_deepen, \
         patch("nanobot_kt.tools.ai_daily._summarize_news_layout") as mock_layout:

        mock_search.return_value = [
            {
                "title": "Qwen 新模型发布",
                "href": "https://example.com/qwen",
                "body": "更强推理和更低价格",
                "search_strategy": "web_ddg",
            }
        ]
        mock_extract.return_value = "Qwen 发布了新模型，并给出了更低的 token 价格。"
        mock_deepen.return_value = (False, "test")
        mock_layout.return_value = {
            "title": "AI 今日速报",
            "subtitle": "发布、价格与模型能力更新",
            "summary": "今天的核心是模型发布和价格信号同时出现。",
            "highlights": ["Qwen 新模型发布", "价格出现下探信号"],
            "alerts": ["适合继续比较 API 成本"],
            "closing": "更细节可看下方来源索引。",
        }

        final_report = search_and_extract_news("qwen 最新资讯")

        assert "<article" in final_report
        assert "class=\"news-brief\"" in final_report
        assert "AI 今日速报" in final_report
        assert "发布、价格与模型能力更新" in final_report
        assert "今天的核心是模型发布和价格信号同时出现。" in final_report
        assert "Qwen 新模型发布" in final_report
        assert "适合继续比较 API 成本" in final_report
        assert "更细节可看下方来源索引。" in final_report
        assert "<table" in final_report


def test_web_search_news_query_prefers_ddgs_news_results(monkeypatch):
    monkeypatch.setattr(news_tool, "NEWS_SEARCH_DDG_ENABLED", True)
    monkeypatch.setattr(news_tool, "_fetch_multi_rss", lambda query=None, max_results=3: [])
    monkeypatch.setattr(news_tool, "_fetch_juya_rss", lambda max_results=3, target_date=None: [])
    fresh_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    news_results = [
        {
            "title": "Fresh AI Launch",
            "url": "https://news.example.com/fresh",
            "body": "fresh body",
            "date": fresh_date,
        }
    ]
    text_results = [
        {
            "title": "Generic Web Result",
            "href": "https://web.example.com/generic",
            "body": "generic body",
        }
    ]

    with patch("nanobot_kt.tools.ai_daily.DDGS") as mock_ddgs:
        mock_instance = mock_ddgs.return_value.__enter__.return_value
        mock_instance.news.return_value = news_results
        mock_instance.text.return_value = text_results

        results = WebTools.search("AI 最新资讯", max_results=3, deep=False)

        assert any(item["href"] == "https://news.example.com/fresh" for item in results)
        assert any(item["href"] == "https://web.example.com/generic" for item in results)
        assert mock_instance.news.called


def test_extract_date_accepts_chinese_date_and_today(monkeypatch):
    monkeypatch.setattr(
        news_tool,
        "db_now_naive",
        lambda: datetime(2026, 5, 1, tzinfo=timezone.utc).astimezone().replace(tzinfo=None),
    )
    monkeypatch.setattr(
        news_tool._runtime_cache,
        "db_now_naive",
        lambda: datetime(2026, 5, 1, tzinfo=timezone.utc).astimezone().replace(tzinfo=None),
    )

    assert news_tool._extract_date("2026年5月1日 人工智能 新闻") == "2026-05-01"
    assert news_tool._extract_date("今天 AI 日报") == "2026-05-01"


def test_juya_rss_preserves_pubdate_for_freshness_filter():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss><channel><item>
  <title>AI Daily 2026-05-01</title>
  <link>https://example.com/issue-1</link>
  <description>fresh issue</description>
  <pubDate>Fri, 01 May 2026 00:00:00 GMT</pubDate>
</item></channel></rss>"""

    mock_resp = MagicMock()
    mock_resp.read.return_value = xml
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("nanobot_kt.tools.ai_daily._urlopen", return_value=mock_resp):
        results = news_tool._fetch_juya_rss(max_results=3, target_date="2026-05-01")

    assert len(results) == 1
    assert results[0]["date"].startswith("2026-05-01T")


def test_ai_daily_tool_reuses_equivalent_daily_query_cache(monkeypatch):
    calls = {"count": 0}

    def fake_daily(query, mode="quality", limit=8):
        calls["count"] += 1
        return f"<article>{query}:{limit}</article>"

    monkeypatch.setattr(news_tool, "_run_news_daily_pipeline", fake_daily)
    news_tool._NEWS_SEARCH_CACHE.clear()

    tool = news_tool.AiDailyTool()
    q = "2026年5月1日 人工智能 新闻"
    first = run_async(tool.execute({"query": q, "max_results": 5}))
    second = run_async(tool.execute({"query": q, "max_results": 5}))

    assert first.success
    assert second.success
    assert calls["count"] == 1
    assert second.output == first.output


def test_web_search_latest_query_filters_out_obviously_stale_dated_results(monkeypatch):
    monkeypatch.setattr(news_tool, "NEWS_SEARCH_DDG_ENABLED", True)
    monkeypatch.setattr(news_tool, "_fetch_juya_rss", lambda max_results=3, target_date=None: [])
    now = datetime.now(timezone.utc)
    fresh_date = now.strftime("%Y-%m-%dT%H:%M:%S%z")
    old_date = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S%z")
    rss_results = [
        {
            "title": "Old RSS News",
            "href": "https://rss.example.com/old",
            "body": "old rss body",
            "date": old_date,
            "source_weight": 3,
            "search_strategy": "rss:test",
        },
        {
            "title": "Fresh RSS News",
            "href": "https://rss.example.com/fresh",
            "body": "fresh rss body",
            "date": fresh_date,
            "source_weight": 3,
            "search_strategy": "rss:test",
        },
    ]
    monkeypatch.setattr(news_tool, "_fetch_multi_rss", lambda query=None, max_results=3: rss_results)

    with patch("nanobot_kt.tools.ai_daily.DDGS") as mock_ddgs:
        mock_instance = mock_ddgs.return_value.__enter__.return_value
        mock_instance.news.return_value = []
        mock_instance.text.return_value = []

        results = WebTools.search("今天 AI 最新资讯", max_results=5, deep=False)

        assert any(item["href"] == "https://rss.example.com/fresh" for item in results)
        assert all(item["href"] != "https://rss.example.com/old" for item in results)


def test_merge_layout_with_fallback_backfills_specific_models_and_more_items():
    fallback = {
        "title": "AI 今日速报",
        "subtitle": "Qwen、DeepSeek 与价格动态",
        "summary": "Qwen 新模型发布，DeepSeek 同时调整 API 定价与免费额度。",
        "highlights": [
            "Qwen 新模型发布：推理能力提升，并给出更低价格策略",
            "DeepSeek API 定价调整：token 计费与免费额度同步更新",
            "两条动态都直接影响模型选型与调用成本",
        ],
        "alerts": [
            "DeepSeek 免费额度变化需要核对实际使用量",
            "价格调整可能影响现有项目预算",
        ],
        "closing": "详细数据可继续查看来源索引。",
    }
    parsed = {
        "title": "AI 动态",
        "subtitle": "行业持续变化",
        "summary": "今天行业继续发展，值得关注。",
        "highlights": ["行业持续升温"],
        "alerts": ["继续关注"],
        "closing": "后续继续观察。",
    }

    merged = _merge_layout_with_fallback(parsed, fallback)

    assert merged["title"] == "AI 动态"
    assert "Qwen" in "".join(merged["highlights"])
    assert "DeepSeek" in "".join(merged["highlights"])
    assert len(merged["highlights"]) >= 3
    assert any("价格" in item or "API" in item or "免费额度" in item for item in merged["alerts"])
    assert "Qwen" in merged["summary"] or "DeepSeek" in merged["summary"]


def test_combined_news_tool_returns_unavailable_html_when_search_backends_fail():
    with patch("nanobot_kt.tools.ai_daily.WebTools.search", return_value=[]), \
         patch("nanobot_kt.tools.ai_daily._model_should_deepen", return_value=(False, "backend-unavailable")):

        final_report = search_and_extract_news("今天 AI 新闻")

        assert "<article" in final_report
        assert "class=\"news-brief" in final_report
        assert "暂时不可用" in final_report or "稍后再试" in final_report
        assert "不要继续重试" in final_report or "搜索源" in final_report


def test_ai_daily_tool_wraps_html_as_rich_output():
    html = '<!DOCTYPE html><html><body><article class="news-brief">AI 资讯</article></body></html>'

    with patch("nanobot_kt.tools.ai_daily._run_news_daily_pipeline", return_value=html):
        result = run_async(AiDailyTool().execute({"query": "今天 AI 新闻"}))

    assert result.success
    payload = json.loads(result.output)
    rich = payload["NANOBOT_RICH_OUTPUT"]
    assert rich["version"] == 1
    assert rich["report_kind"] == "ai_daily"
    assert rich["content_type"] == "text/html"
    assert rich["html"] == html


@pytest.mark.asyncio
async def test_reply_tool_preserves_plain_text_without_channel_normalization(monkeypatch):
    from core import text_style
    from nanobot_kt.tools.reply import REPLY_MARKER, ReplyTool

    monkeypatch.setattr(text_style, "normalize_chat_reply_style", lambda _text: "被通道层改写了")

    result = await ReplyTool()._execute({"content": "先看日志，别急。"})
    payload = json.loads(result.output)

    assert payload[REPLY_MARKER]["content"] == "先看日志，别急。"


@pytest.mark.asyncio
async def test_reply_tool_preserves_html_without_text_style_normalization():
    from nanobot_kt.tools.reply import REPLY_MARKER, ReplyTool

    html = (
        '<!DOCTYPE html><html><head><style>'
        ':root{--bg:#fff}.card{font-size:14px;color:#123;}'
        '</style></head><body class="news-brief">AI 日报：保持样式。</body></html>'
    )

    result = await ReplyTool()._execute({"content": html})
    payload = json.loads(result.output)

    assert payload[REPLY_MARKER]["content"] == html
    assert ":root{--bg:#fff}" in payload[REPLY_MARKER]["content"]


def test_web_search_preserves_partial_results_when_later_variant_fails(monkeypatch):
    monkeypatch.setattr(news_tool, "NEWS_SEARCH_DDG_ENABLED", True)
    monkeypatch.setattr(news_tool, "_fetch_juya_rss", lambda max_results=3, target_date=None: [])
    fresh_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    rss_results = [
        {
            "title": "RSS Fresh News",
            "href": "https://rss.example.com/fresh",
            "body": "rss body",
            "date": fresh_date,
            "source_weight": 3,
            "search_strategy": "rss:test",
        }
    ]

    with patch("nanobot_kt.tools.ai_daily._fetch_multi_rss", return_value=rss_results), \
         patch("nanobot_kt.tools.ai_daily.DDGS") as mock_ddgs:
        mock_instance = mock_ddgs.return_value.__enter__.return_value
        mock_instance.news.side_effect = RuntimeError("403 Ratelimit")

        def fake_text(*args, **kwargs):
            query = args[0] if args else ""
            if "model pricing" in query:
                raise RuntimeError("202 Ratelimit")
            return [{"title": "Web Fresh News", "href": "https://web.example.com/fresh", "body": "web body"}]

        mock_instance.text.side_effect = fake_text

        results = WebTools.search("今天 AI 最新资讯", max_results=5, deep=False)

        assert any(item["href"] == "https://rss.example.com/fresh" for item in results)
        assert any(item["href"] == "https://web.example.com/fresh" for item in results)
        assert WebTools.last_error == ""
