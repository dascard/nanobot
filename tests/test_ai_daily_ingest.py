import asyncio
import json


def test_ai_daily_ingests_summary_metadata_only(db_session):
    from core.ai_daily_ingest import ingest_ai_daily_items
    from core.database import KnowledgeChunk, KnowledgeDocument, SemanticIndexItem

    result = ingest_ai_daily_items(db_session, [{
        "title": "OpenAI 发布新模型",
        "url": "https://example.com/openai-model",
        "summary": "<article>OpenAI 发布新模型，重点是推理能力。</article>",
        "source_name": "Example AI",
        "published_at": "2026-05-26T08:00:00Z",
        "author": "Reporter",
    }], query="今天 AI 新闻")

    assert result["created"] == 1
    doc = db_session.query(KnowledgeDocument).one()
    chunk = db_session.query(KnowledgeChunk).one()
    assert doc.document_kind == "ai_daily"
    assert "<article" not in doc.summary
    assert "<article" not in chunk.text
    assert doc.url == "https://example.com/openai-model"
    assert json.loads(chunk.citation_json)["url"] == doc.url
    assert db_session.query(SemanticIndexItem).filter_by(source_type="knowledge").count() == 1


def test_ai_daily_ingest_failure_does_not_fail_tool(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search import tool as news_tool

    html = '<article class="news-brief"><a href="https://example.com/a">AI 新闻</a></article>'
    monkeypatch.setattr(news_tool, "_run_news_daily_pipeline", lambda *args, **kwargs: html)
    monkeypatch.setattr("core.ai_daily_ingest.ingest_ai_daily_html", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db down")))
    news_tool._NEWS_SEARCH_CACHE.clear()

    result = asyncio.run(news_tool.AiDailyTool().execute({
        "query": "今天 AI 新闻",
        "max_results": 8,
        "no_cache": True,
    }))

    assert result.success
    payload = json.loads(result.output)
    assert payload["NANOBOT_REPLY_OUTPUT"]["content"] == html


def test_duplicate_url_does_not_create_duplicate_active_document(db_session):
    from core.ai_daily_ingest import ingest_ai_daily_items
    from core.database import KnowledgeDocument

    first = {
        "title": "同一 URL 首次",
        "url": "https://example.com/same",
        "summary": "首次摘要",
        "source_name": "Example AI",
        "published_at": "2026-05-26",
    }
    second = {
        "title": "同一 URL 更新",
        "url": "https://example.com/same",
        "summary": "更新摘要",
        "source_name": "Example AI",
        "published_at": "2026-05-26",
    }

    ingest_ai_daily_items(db_session, [first], query="AI")
    result = ingest_ai_daily_items(db_session, [second], query="AI")

    docs = db_session.query(KnowledgeDocument).filter_by(document_kind="ai_daily", status="active").all()
    assert len(docs) == 1
    assert result["updated"] == 1
    assert docs[0].latest_seen is not None


def test_ai_daily_ingest_records_warning_in_tool_meta(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search import tool as news_tool

    monkeypatch.setattr(news_tool, "_run_news_daily_pipeline", lambda *args, **kwargs: "<article>AI 新闻</article>")
    monkeypatch.setattr("core.ai_daily_ingest.ingest_ai_daily_html", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("write failed")))
    news_tool._NEWS_SEARCH_CACHE.clear()

    result = asyncio.run(news_tool.AiDailyTool().execute({
        "query": "今天 AI 新闻",
        "no_cache": True,
    }))

    assert result.success
    assert result.metadata["ai_daily_ingest"]["warnings"]


def test_ai_daily_dedup_uses_summary_hash_with_source_and_date(db_session):
    from core.ai_daily_ingest import ingest_ai_daily_items
    from core.database import KnowledgeDocument

    same_summary_a = {
        "title": "无 URL A",
        "url": "",
        "summary": "同一摘要但不同来源不能互相覆盖",
        "source_name": "Source A",
        "published_at": "2026-05-26T08:00:00Z",
    }
    same_summary_b = {
        "title": "无 URL B",
        "url": "",
        "summary": "同一摘要但不同来源不能互相覆盖",
        "source_name": "Source B",
        "published_at": "2026-05-26T08:00:00Z",
    }
    same_source_same_day = {
        "title": "无 URL A 更新",
        "url": "",
        "summary": "同一摘要但不同来源不能互相覆盖",
        "source_name": "Source A",
        "published_at": "2026-05-26T18:00:00Z",
    }

    ingest_ai_daily_items(db_session, [same_summary_a], query="AI")
    ingest_ai_daily_items(db_session, [same_summary_b], query="AI")
    result = ingest_ai_daily_items(db_session, [same_source_same_day], query="AI")

    assert db_session.query(KnowledgeDocument).filter_by(document_kind="ai_daily", status="active").count() == 2
    assert result["updated"] == 1


def test_ai_daily_filters_items_already_ingested(db_session):
    from core.ai_daily_ingest import filter_new_ai_daily_items, ingest_ai_daily_items

    ingest_ai_daily_items(db_session, [{
        "title": "已推送新闻",
        "url": "https://example.com/seen",
        "summary": "已推送摘要",
        "source_name": "Example AI",
        "published_at": "2026-05-26",
    }], query="AI")

    kept, stats = filter_new_ai_daily_items(db_session, [
        {
            "title": "已推送新闻",
            "url": "https://example.com/seen",
            "summary": "已推送摘要",
            "source_name": "Example AI",
            "published_at": "2026-05-26",
        },
        {
            "title": "新新闻",
            "url": "https://example.com/new",
            "summary": "新摘要",
            "source_name": "Example AI",
            "published_at": "2026-05-27",
        },
    ], query="AI")

    assert stats["skipped_seen"] == 1
    assert [item["url"] for item in kept] == ["https://example.com/new"]


def test_news_daily_pipeline_filters_seen_items_before_digest(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search.news_daily import tool as daily_tool
    from creatures.nanobot.prompts.skills.news_search.news_daily.schema import NewsItem

    captured = {}
    seen = NewsItem(title="已推送新闻", url="https://example.com/seen", published_at="2026-05-26")
    fresh = NewsItem(title="新新闻", url="https://example.com/new", published_at="2026-05-27")

    monkeypatch.setattr(daily_tool, "_get_providers", lambda mode: [])
    monkeypatch.setattr(daily_tool, "collect_sources", lambda providers, limit_per_source=8, timeout=10: [seen, fresh])
    monkeypatch.setattr(daily_tool, "filter_recent", lambda items, hours=72: items)
    monkeypatch.setattr(daily_tool, "dedup_items", lambda items: items)
    monkeypatch.setattr(daily_tool, "rank_items", lambda items: items)
    monkeypatch.setattr(
        "core.ai_daily_ingest.best_effort_filter_new_ai_daily_items",
        lambda items, query="": ([item for item in items if item.url.endswith("/new")], {"skipped_seen": 1}),
    )

    def fake_digest(items, query="", mode="fast"):
        captured["urls"] = [item.url for item in items]
        return {
            "title": "AI 日报",
            "subtitle": "",
            "verdict": "ok",
            "generated_at": "2026-05-27 08:00",
            "mode": mode,
            "highlights": [{"label": "新", "text": "新新闻", "source_ids": []}],
            "sources": [],
        }

    monkeypatch.setattr(daily_tool, "build_digest_deterministic", fake_digest)

    html = daily_tool.run_pipeline("今天 AI 新闻", mode="fast", limit=8)

    assert "<html" in html
    assert captured["urls"] == ["https://example.com/new"]
