from __future__ import annotations

from unittest.mock import MagicMock


def test_search_backend_module_exposes_split_entrypoints():
    from creatures.nanobot.prompts.skills.news_search import search_backend

    assert callable(search_backend.search)
    assert callable(search_backend.extract_web_content)
    assert callable(search_backend._fetch_multi_rss)
    assert callable(search_backend._fetch_juya_rss)


def test_tool_webtools_search_uses_legacy_patch_points(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search import tool as news_tool

    rss_results = [
        {
            "title": "RSS Fresh",
            "href": "https://rss.example.com/a",
            "body": "rss body",
            "date": "",
            "source_weight": 3,
            "search_strategy": "rss:test",
        }
    ]
    web_results = [
        {
            "title": "Web Fresh",
            "href": "https://web.example.com/b",
            "body": "web body",
        }
    ]

    monkeypatch.setattr(news_tool, "NEWS_SEARCH_DDG_ENABLED", True)
    monkeypatch.setattr(news_tool, "_fetch_multi_rss", lambda query=None, max_results=3: rss_results)
    monkeypatch.setattr(news_tool, "_fetch_juya_rss", lambda max_results=3, target_date=None: [])

    class FakeDDGS:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def text(self, *args, **kwargs):
            return web_results

        def news(self, *args, **kwargs):
            return []

    monkeypatch.setattr(news_tool, "DDGS", FakeDDGS)

    results = news_tool.WebTools.search("AI 最新资讯", max_results=3, deep=False)

    assert any(item["href"] == "https://rss.example.com/a" for item in results)
    assert any(item["href"] == "https://web.example.com/b" for item in results)
    assert news_tool.WebTools.last_error == ""


def test_tool_fetch_juya_rss_uses_legacy_urlopen_patch(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search import tool as news_tool

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
    calls = []

    def fake_urlopen(url, timeout=10):
        calls.append((url, timeout))
        return mock_resp

    monkeypatch.setattr(news_tool, "_urlopen", fake_urlopen)

    results = news_tool._fetch_juya_rss(max_results=3, target_date="2026-05-01")

    assert calls == [(news_tool.JUYA_RSS_URL, 6)]
    assert len(results) == 1
    assert results[0]["date"].startswith("2026-05-01T")


def test_tool_extract_web_content_uses_legacy_trafilatura_patch(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search import tool as news_tool

    monkeypatch.setattr(news_tool.trafilatura, "fetch_url", lambda url, timeout=5: "<html>ok</html>")
    monkeypatch.setattr(
        news_tool.trafilatura,
        "extract",
        lambda downloaded, **kwargs: f"extracted:{downloaded}",
    )

    assert news_tool.WebTools.extract_web_content("https://example.com/a") == "extracted:<html>ok</html>"
