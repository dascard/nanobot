from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.async_helpers import run_async


CN_TZ = ZoneInfo("Asia/Shanghai")
FIXED_NOW = datetime(2026, 7, 13, 1, 30, tzinfo=CN_TZ)


@pytest.mark.parametrize(
    "freshness,target_date,expected_start,expected_end",
    [
        (
            "today",
            None,
            datetime(2026, 7, 13, 0, 0, tzinfo=CN_TZ),
            FIXED_NOW,
        ),
        (
            "latest",
            None,
            datetime(2026, 7, 10, 1, 30, tzinfo=CN_TZ),
            FIXED_NOW,
        ),
        (
            "week",
            None,
            datetime(2026, 7, 6, 1, 30, tzinfo=CN_TZ),
            FIXED_NOW,
        ),
        (
            "custom",
            "2026-07-01",
            datetime(2026, 7, 1, 0, 0, tzinfo=CN_TZ),
            datetime(2026, 7, 2, 0, 0, tzinfo=CN_TZ),
        ),
    ],
)
def test_ai_daily_request_builds_explicit_beijing_window(
    freshness,
    target_date,
    expected_start,
    expected_end,
):
    from core.tool_contracts.ai_daily import parse_ai_daily_request

    args = {"query": "AI 日报", "freshness": freshness}
    if target_date is not None:
        args["target_date"] = target_date

    request = parse_ai_daily_request(args, now=FIXED_NOW)

    assert request.window_start == expected_start
    assert request.window_end == expected_end
    assert request.freshness == freshness
    assert request.target_date == target_date
    assert request.max_results == 8


@pytest.mark.parametrize(
    "args",
    [
        {"query": "AI 日报", "freshness": "custom"},
        {"query": "AI 日报", "freshness": "custom", "target_date": "2026-02-30"},
        {"query": "AI 日报", "freshness": "custom", "target_date": "2026/07/01"},
        {"query": "AI 日报", "freshness": "future"},
        {"query": "AI 日报", "freshness": "latest", "target_date": "2026-07-01"},
        {"query": "AI 日报", "max_results": 0},
        {"query": "AI 日报", "max_results": 51},
        {"query": "AI 日报", "max_results": True},
    ],
)
def test_ai_daily_request_rejects_invalid_public_arguments(args):
    from core.tool_contracts.ai_daily import AiDailyRequestError, parse_ai_daily_request

    with pytest.raises(AiDailyRequestError):
        parse_ai_daily_request(args, now=FIXED_NOW)


def test_ai_daily_cache_key_covers_request_semantics_but_not_cache_controls():
    from core.tool_contracts.ai_daily import (
        AI_DAILY_CACHE_VERSION,
        parse_ai_daily_request,
    )
    from creatures.nanobot.prompts.skills.news_search.runtime_cache import (
        make_ai_daily_cache_key,
    )

    def build(**overrides):
        args = {"query": "  AI   日报 ", "freshness": "latest", **overrides}
        return parse_ai_daily_request(args, now=FIXED_NOW)

    base = make_ai_daily_cache_key(build(), mode="quality")

    assert base[0] == AI_DAILY_CACHE_VERSION
    assert make_ai_daily_cache_key(
        parse_ai_daily_request(
            {"query": "ai 日报", "freshness": "latest"},
            now=FIXED_NOW,
        ),
        mode="quality",
    ) == base
    assert make_ai_daily_cache_key(build(freshness="today"), mode="quality") != base
    assert make_ai_daily_cache_key(build(freshness="week"), mode="quality") != base
    assert make_ai_daily_cache_key(
        build(freshness="custom", target_date="2026-07-01"),
        mode="quality",
    ) != make_ai_daily_cache_key(
        build(freshness="custom", target_date="2026-07-02"),
        mode="quality",
    )
    assert make_ai_daily_cache_key(build(max_results=12), mode="quality") != base
    assert make_ai_daily_cache_key(build(), mode="daily") != base
    assert make_ai_daily_cache_key(build(no_cache=True), mode="quality") == base
    assert make_ai_daily_cache_key(build(refresh=True), mode="quality") == base
    assert make_ai_daily_cache_key(
        parse_ai_daily_request(
            {"query": "AI 芯片日报", "freshness": "latest"},
            now=FIXED_NOW,
        ),
        mode="quality",
    ) != base

    next_day = parse_ai_daily_request(
        {"query": "AI 日报", "freshness": "latest"},
        now=datetime(2026, 7, 14, 1, 30, tzinfo=CN_TZ),
    )
    assert make_ai_daily_cache_key(next_day, mode="quality") != base


@pytest.mark.parametrize("freshness", ["latest", "week"])
def test_ai_daily_rolling_window_cache_key_uses_five_minute_bucket(freshness):
    from core.tool_contracts.ai_daily import parse_ai_daily_request
    from creatures.nanobot.prompts.skills.news_search.runtime_cache import (
        make_ai_daily_cache_key,
    )

    def build(minute, second=0):
        return parse_ai_daily_request(
            {"query": "AI 日报", "freshness": freshness},
            now=datetime(2026, 7, 13, 1, minute, second, tzinfo=CN_TZ),
        )

    assert make_ai_daily_cache_key(
        build(30),
        mode="quality",
    ) == make_ai_daily_cache_key(
        build(34, 59),
        mode="quality",
    )
    assert make_ai_daily_cache_key(
        build(34, 59),
        mode="quality",
    ) != make_ai_daily_cache_key(
        build(35),
        mode="quality",
    )


def test_ai_daily_custom_date_cache_key_is_stable_across_runtime_clock():
    from core.tool_contracts.ai_daily import parse_ai_daily_request
    from creatures.nanobot.prompts.skills.news_search.runtime_cache import (
        make_ai_daily_cache_key,
    )

    args = {
        "query": "AI 历史日报",
        "freshness": "custom",
        "target_date": "2026-07-01",
    }
    morning = parse_ai_daily_request(args, now=FIXED_NOW)
    evening = parse_ai_daily_request(
        args,
        now=datetime(2026, 7, 13, 20, 30, tzinfo=CN_TZ),
    )

    assert make_ai_daily_cache_key(
        morning,
        mode="quality",
    ) == make_ai_daily_cache_key(
        evening,
        mode="quality",
    )


@pytest.mark.parametrize("cache_flag", ["no_cache", "refresh"])
def test_ai_daily_cache_controls_bypass_read_without_changing_request(
    monkeypatch,
    cache_flag,
):
    import nanobot_kt.tools.ai_daily as news_tool

    cache_reads = []
    pipeline_requests = []
    stored = []

    monkeypatch.setattr(
        news_tool,
        "_get_cached_news_result",
        lambda key: cache_reads.append(key) or "<article>cached</article>",
    )
    monkeypatch.setattr(
        news_tool,
        "_run_news_daily_pipeline",
        lambda request: pipeline_requests.append(request)
        or "<article>fresh</article>",
    )
    monkeypatch.setattr(
        news_tool,
        "_store_cached_news_result",
        lambda key, output: stored.append((key, output)),
    )

    result = run_async(
        news_tool.AiDailyTool().execute(
            {"query": "AI 日报", "freshness": "week", cache_flag: True}
        )
    )

    assert result.success
    assert cache_reads == []
    assert len(pipeline_requests) == 1
    assert pipeline_requests[0].freshness == "week"
    assert pipeline_requests[0].bypass_cache is True
    assert len(stored) == 1


@pytest.mark.parametrize(
    "pipeline_output",
    ["<article>ok</article>", "", "plain output"],
)
def test_ai_daily_logs_query_fingerprint_without_raw_query(
    monkeypatch,
    caplog,
    pipeline_output,
):
    import hashlib
    import logging

    import nanobot_kt.tools.ai_daily as news_tool

    query = "AI 日报 confidential-query-marker"
    monkeypatch.setattr(news_tool, "_get_cached_news_result", lambda _key: None)
    monkeypatch.setattr(
        news_tool,
        "_run_news_daily_pipeline",
        lambda _request: pipeline_output,
    )
    monkeypatch.setattr(
        news_tool,
        "_store_cached_news_result",
        lambda _key, _output: None,
    )
    caplog.set_level(logging.INFO, logger="nanobot.ai_daily")

    result = run_async(news_tool.AiDailyTool().execute({"query": query}))

    expected_sha = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
    assert result.success
    assert query not in caplog.text
    assert f"query_len={len(query)}" in caplog.text
    assert f"query_sha={expected_sha}" in caplog.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", "other-user"),
        ("session_id", "private_other-user"),
        ("mode", "daily"),
        ("timezone", "UTC"),
        ("now", "2026-07-13T01:30:00+08:00"),
        ("pipeline_version", "override"),
        ("output_format", "text"),
    ],
)
def test_ai_daily_tool_rejects_model_selected_server_fields(
    monkeypatch,
    field,
    value,
):
    import nanobot_kt.tools.ai_daily as news_tool

    monkeypatch.setattr(
        news_tool,
        "_run_news_daily_pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline called")),
    )

    result = run_async(news_tool.AiDailyTool().execute({"query": "AI 日报", field: value}))

    assert result.error
    assert "unsupported arguments" in result.error


def test_ai_daily_rank_uses_custom_window_reference_time():
    from core.tool_contracts.ai_daily import parse_ai_daily_request
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.rank import (
        rank_items,
    )
    from creatures.nanobot.prompts.skills.news_search.news_daily.schema import NewsItem

    request = parse_ai_daily_request(
        {
            "query": "OpenAI model release",
            "freshness": "custom",
            "target_date": "2026-05-01",
        },
        now=FIXED_NOW,
    )
    item = NewsItem(
        title="OpenAI model release",
        url="https://example.test/openai",
        published_at="2026-05-01T08:00:00+08:00",
    )

    ranked = rank_items([item], now=request.reference_time_naive)

    assert ranked == [item]
    assert item.freshness == 1.0


def test_ai_daily_rank_does_not_reward_future_timestamp():
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.rank import (
        rank_items,
    )
    from creatures.nanobot.prompts.skills.news_search.news_daily.schema import NewsItem

    item = NewsItem(
        title="OpenAI future model release",
        url="https://example.test/future-model",
        published_at="2026-07-13T20:00:00+08:00",
    )

    ranked = rank_items([item], now=FIXED_NOW.replace(tzinfo=None))

    assert ranked == [item]
    assert item.freshness == 0.0


def test_ai_daily_normalize_v2_converts_utc_timestamp_to_beijing_date():
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.normalize_v2 import (
        parse_date,
    )

    assert parse_date("2026-07-12T16:30:00Z") == datetime(2026, 7, 13, 0, 30)


def test_ai_daily_collection_budget_does_not_scale_per_provider_with_max_results(
    monkeypatch,
):
    from core.tool_contracts.ai_daily import parse_ai_daily_request
    from creatures.nanobot.prompts.skills.news_search.news_daily import tool as daily_tool
    from creatures.nanobot.prompts.skills.news_search.news_daily.schema import NewsItem

    request = parse_ai_daily_request(
        {
            "query": "OpenAI model range",
            "max_results": 50,
            "freshness": "custom",
            "target_date": "2026-07-01",
        },
        now=FIXED_NOW,
    )
    captured = {}
    items = [
        NewsItem(
            title=f"OpenAI model release {index}",
            url=f"https://example.test/{index}",
            published_at="2026-07-01T08:00:00+08:00",
            source_group="core_provider",
        )
        for index in range(60)
    ]

    monkeypatch.setattr(daily_tool, "_get_providers", lambda _mode: [object(), object()])

    def fake_collect(_providers, limit_per_source=8, timeout=10):
        captured["limit_per_source"] = limit_per_source
        return items

    monkeypatch.setattr(daily_tool, "collect_sources", fake_collect)
    monkeypatch.setattr(daily_tool, "filter_for_ai_daily_request", lambda value, _request: value)
    monkeypatch.setattr(daily_tool, "dedup_items", lambda value: value)
    monkeypatch.setattr(daily_tool, "rank_items", lambda value, now=None: value)
    monkeypatch.setattr(daily_tool, "_apply_quotas", lambda value, limit: value[:limit])
    monkeypatch.setattr(
        "core.ai_daily_ingest.best_effort_filter_new_ai_daily_items",
        lambda value, query="": (value, {"skipped_seen": 0}),
    )

    def fake_digest(value, _query, _mode):
        captured["candidate_count"] = len(value)
        return {
            "title": "AI 日报",
            "subtitle": "",
            "verdict": "ok",
            "generated_at": "2026-07-01 08:00",
            "mode": "fast",
            "highlights": [],
            "sources": [],
        }

    monkeypatch.setattr(daily_tool, "build_digest_deterministic", fake_digest)

    daily_tool.run_pipeline(request, mode="fast")

    assert captured["limit_per_source"] == 8
    assert captured["candidate_count"] == 50


def test_ai_daily_daily_fallback_wires_age_and_result_limits(monkeypatch):
    from types import SimpleNamespace

    from core.tool_contracts.ai_daily import parse_ai_daily_request
    from creatures.nanobot.prompts.skills.news_search.news_daily import tool as daily_tool
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline import (
        cluster as cluster_module,
        diversify as diversify_module,
        freshness as freshness_module,
        normalize_v2 as normalize_v2_module,
    )
    from creatures.nanobot.prompts.skills.news_search.news_daily.schema import NewsItem

    request = parse_ai_daily_request(
        {"query": "OpenAI weekly range", "max_results": 3, "freshness": "week"},
        now=FIXED_NOW,
    )
    item = NewsItem(
        title="OpenAI model release",
        url="https://example.test/openai",
        published_at="2026-07-07T08:00:00+08:00",
        source_group="core_provider",
    )
    article = SimpleNamespace(published_at=request.window_start_naive, id="article")
    event_cluster = SimpleNamespace(id="cluster")
    captured = {}

    monkeypatch.setattr(daily_tool, "_get_providers", lambda _mode: [object()])
    monkeypatch.setattr(
        daily_tool,
        "collect_sources",
        lambda _providers, limit_per_source=8, timeout=10: [item],
    )
    monkeypatch.setattr(daily_tool, "filter_for_ai_daily_request", lambda value, _request: value)
    monkeypatch.setattr(daily_tool, "dedup_items", lambda value: value)
    monkeypatch.setattr(daily_tool, "rank_items", lambda value, now=None: value)
    monkeypatch.setattr(
        "core.ai_daily_ingest.best_effort_filter_new_ai_daily_items",
        lambda value, query="": (value, {"skipped_seen": 0}),
    )
    monkeypatch.setattr(normalize_v2_module, "normalize_articles", lambda _items: [article])

    def fake_filter(articles, now, *, max_age_hours):
        captured["filter"] = (now, max_age_hours)
        return articles

    monkeypatch.setattr(freshness_module, "filter_fresh_articles", fake_filter)
    monkeypatch.setattr(cluster_module, "cluster_articles", lambda _articles: [event_cluster])
    monkeypatch.setattr(diversify_module, "score_clusters", lambda clusters, _now: clusters)

    def fake_select(clusters, now, *, max_age_hours, limit):
        captured["select"] = (now, max_age_hours, limit)
        return clusters

    def fake_report(clusters, now, *, max_age_hours, limit):
        captured["report"] = (now, max_age_hours, limit)
        return SimpleNamespace(highlights=clusters, top_story=None)

    monkeypatch.setattr(diversify_module, "select_diverse_clusters", fake_select)
    monkeypatch.setattr(diversify_module, "build_daily_report", fake_report)
    monkeypatch.setattr(
        daily_tool,
        "_report_to_digest",
        lambda _report, _articles: {
            "title": "AI 日报",
            "subtitle": "",
            "verdict": "ok",
            "generated_at": "2026-07-13 01:30",
            "mode": "daily",
            "highlights": [],
            "sources": [],
        },
    )

    daily_tool.run_pipeline(request, mode="daily")

    assert captured["filter"] == (request.reference_time_naive, 168)
    assert captured["select"] == (request.reference_time_naive, 168, 3)
    assert captured["report"] == (request.reference_time_naive, 168, 3)


def test_ai_daily_tool_passes_normalized_request_to_pipeline(monkeypatch):
    from core.tool_contracts.ai_daily import AiDailyRequest
    import nanobot_kt.tools.ai_daily as news_tool

    captured = []

    def fake_pipeline(request):
        captured.append(request)
        return "<article>AI daily</article>"

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(news_tool, "_run_news_daily_pipeline", fake_pipeline)
    monkeypatch.setattr(news_tool.asyncio, "to_thread", fake_to_thread)

    result = run_async(
        news_tool.AiDailyTool().execute(
            {
                "query": "指定日期范围",
                "max_results": 5,
                "freshness": "custom",
                "target_date": "2026-07-01",
                "no_cache": True,
            }
        )
    )

    assert result.success
    assert len(captured) == 1
    assert isinstance(captured[0], AiDailyRequest)
    assert captured[0].query == "指定日期范围"
    assert captured[0].max_results == 5
    assert captured[0].freshness == "custom"
    assert captured[0].target_date == "2026-07-01"
    assert captured[0].bypass_cache is True


def test_ai_daily_invalid_arguments_fail_before_cache_or_pipeline(monkeypatch):
    import nanobot_kt.tools.ai_daily as news_tool

    monkeypatch.setattr(
        news_tool,
        "_get_cached_news_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache accessed")),
    )
    monkeypatch.setattr(
        news_tool,
        "_run_news_daily_pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline called")),
    )

    result = run_async(
        news_tool.AiDailyTool().execute(
            {"query": "AI 日报", "freshness": "custom"}
        )
    )

    assert result.error
    assert "Invalid ai_daily arguments" in result.error


def test_ai_daily_quality_fallback_keeps_same_time_contract(monkeypatch):
    from core.tool_contracts.ai_daily import parse_ai_daily_request
    from creatures.nanobot.prompts.skills.news_search.news_daily import tool as daily_tool

    request = parse_ai_daily_request(
        {
            "query": "指定日期日报",
            "freshness": "custom",
            "target_date": "2026-07-01",
        },
        now=FIXED_NOW,
    )
    calls = []

    def fake_run_pipeline(received, mode="quality"):
        calls.append((received, mode))
        if mode == "quality":
            raise daily_tool.FallbackNeeded("force fallback")
        return "<article>" + ("x" * 900) + "</article>"

    monkeypatch.setattr(daily_tool, "run_pipeline", fake_run_pipeline)

    result = daily_tool.run_news_search_auto(request)

    assert "<article>" in result
    assert calls == [(request, "quality"), (request, "daily")]


def test_ai_daily_filter_uses_explicit_window_boundaries():
    from core.tool_contracts.ai_daily import parse_ai_daily_request
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.normalize import (
        filter_for_ai_daily_request,
    )
    from creatures.nanobot.prompts.skills.news_search.news_daily.schema import NewsItem

    request = parse_ai_daily_request(
        {
            "query": "指定日期日报",
            "freshness": "custom",
            "target_date": "2026-07-01",
        },
        now=FIXED_NOW,
    )

    def item(title, published_at):
        return NewsItem(
            title=title,
            url=f"https://example.test/{title}",
            published_at=published_at,
        )

    kept = filter_for_ai_daily_request(
        [
            item("before", "2026-06-30T23:59:59+08:00"),
            item("start", "2026-07-01T00:00:00+08:00"),
            item("utc_inside", "2026-06-30T16:30:00Z"),
            item("inside", "2026-07-01T12:00:00+08:00"),
            item("end", "2026-07-02T00:00:00+08:00"),
            item("unknown", ""),
        ],
        request,
    )

    assert [entry.title for entry in kept] == ["start", "utc_inside", "inside"]


def test_ai_daily_today_filter_rejects_same_day_future_item():
    from core.tool_contracts.ai_daily import parse_ai_daily_request
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.normalize import (
        filter_for_ai_daily_request,
    )
    from creatures.nanobot.prompts.skills.news_search.news_daily.schema import NewsItem

    request = parse_ai_daily_request(
        {"query": "今日 AI 日报", "freshness": "today"},
        now=FIXED_NOW,
    )
    occurred = NewsItem(
        title="occurred",
        url="https://example.test/occurred",
        published_at="2026-07-13T01:00:00+08:00",
    )
    future = NewsItem(
        title="future",
        url="https://example.test/future",
        published_at="2026-07-13T20:00:00+08:00",
    )

    kept = filter_for_ai_daily_request([occurred, future], request)

    assert kept == [occurred]


def test_ai_daily_schema_and_executor_share_one_contract():
    from core.tool_contracts.ai_daily import (
        AI_DAILY_SERVER_BOUND_FIELDS,
        ai_daily_parameters_schema,
    )
    from core.tool_schema_preview import build_tool_schema
    from nanobot_kt.tools.ai_daily import AiDailyTool

    contract_schema = ai_daily_parameters_schema()
    class_schema = AiDailyTool().get_parameters_schema()
    static_schema = build_tool_schema(
        "ai_daily",
        include_template_overlay=False,
    )["function"]["parameters"]
    static_schema["properties"].pop("run_in_background")

    assert class_schema == contract_schema
    assert static_schema == contract_schema
    assert contract_schema["additionalProperties"] is False
    assert contract_schema["required"] == ["query"]
    assert contract_schema["properties"]["freshness"]["enum"] == [
        "today",
        "latest",
        "week",
        "custom",
    ]
    assert contract_schema["properties"]["freshness"]["default"] == "latest"
    assert AI_DAILY_SERVER_BOUND_FIELDS.isdisjoint(contract_schema["properties"])
