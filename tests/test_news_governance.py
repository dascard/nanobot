from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


def _item(
    candidate_id: str,
    title: str,
    *,
    summary: str = "",
    score: float = 0.8,
):
    from creatures.nanobot.prompts.skills.news_search.news_daily.schema import (
        NewsItem,
    )

    return NewsItem(
        id=candidate_id,
        title=title,
        summary=summary,
        url=f"https://example.com/{candidate_id}",
        source_name="test_source",
        source_group="unknown",
        published_at="2026-07-23T08:00:00+08:00",
        trust=0.7,
        score=score,
    )


def _task_result(parsed_value):
    from core.task_runtime import TaskResult

    return TaskResult(
        parsed_value=parsed_value,
        contract_version="news_relevance_review_v1",
        route_key="news_relevance_review",
        provider="test",
        model="test",
        attempt_count=1,
        latency_ms=1,
        failure=None,
        raw_output_sha256="0" * 64,
        raw_output_bytes=10,
        validation_diagnostics=(),
        run_id="taskrun_test",
    )


def test_news_request_is_single_contract_for_search_and_daily():
    from core.tool_contracts.ai_daily import (
        AiDailyRequest,
        NewsRequest,
        parse_ai_daily_request,
        parse_news_search_request,
    )

    now = datetime.fromisoformat("2026-07-23T12:00:00+08:00")
    search = parse_news_search_request(
        "没有日报关键词的主题",
        max_results=3,
        now=now,
    )
    daily = parse_ai_daily_request(
        {"query": "没有日报关键词的主题", "max_results": 3},
        now=now,
    )

    assert AiDailyRequest is NewsRequest
    assert search.request_kind == "search"
    assert search.max_results == 3
    assert daily.request_kind == "daily_digest"
    assert daily.max_results == 8
    assert search.window_start == daily.window_start
    assert search.window_end == daily.window_end


def test_news_windows_preserve_request_and_pipeline_contracts():
    from core.news.policy import DEFAULT_NEWS_RANKING_POLICY
    from core.tool_contracts.ai_daily import (
        NEWS_LATEST_WINDOW_HOURS,
        parse_news_search_request,
    )
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.config import (
        DAILY_FRESHNESS_HOURS,
        TOP_STORY_FRESHNESS_HOURS,
    )

    request = parse_news_search_request(
        "测试主题",
        now=datetime.fromisoformat("2026-07-23T12:00:00+08:00"),
    )

    assert request.max_age_hours == NEWS_LATEST_WINDOW_HOURS == 72
    assert DEFAULT_NEWS_RANKING_POLICY.latest_hours == 72
    assert DAILY_FRESHNESS_HOURS == 48
    assert TOP_STORY_FRESHNESS_HOURS == 36


def test_news_source_registry_loads_frozen_canonical_resource():
    from core.news.source_registry import get_news_source_registry

    registry = get_news_source_registry()
    snapshot = registry.registry_snapshot

    assert snapshot.generation == 1
    assert len(snapshot.sha256) == 64
    assert registry.require("openai_news").url.startswith("https://")
    assert registry.require("reddit_localllama").modes == ("search",)
    with pytest.raises(TypeError):
        snapshot.items["new"] = registry.require("openai_news")


def test_news_source_override_only_allows_operator_fields():
    from core.news.source_registry import (
        NewsSourceRegistryError,
        load_news_source_registry,
    )

    overridden = load_news_source_registry(operator_overrides={
        "openai_news": {
            "enabled": False,
            "quality_weight": 0.8,
            "fetch_timeout_seconds": 5,
            "per_run_limit": 4,
        }
    })
    descriptor = overridden.require("openai_news")
    assert descriptor.enabled is False
    assert descriptor.quality_weight == 0.8
    assert descriptor.operator_overridden_fields == (
        "enabled",
        "fetch_timeout_seconds",
        "per_run_limit",
        "quality_weight",
    )

    with pytest.raises(NewsSourceRegistryError, match="不允许字段"):
        load_news_source_registry(operator_overrides={
            "openai_news": {"url": "https://evil.example/feed"}
        })


def test_news_source_registry_rejects_unsafe_url(tmp_path: Path):
    from core.news.source_registry import (
        NEWS_SOURCE_RESOURCE,
        NewsSourceRegistryError,
        load_news_source_registry,
    )

    payload = json.loads(NEWS_SOURCE_RESOURCE.read_text(encoding="utf-8"))
    payload["sources"][0]["url"] = "http://openai.com/news/rss.xml"
    resource = tmp_path / "sources.json"
    resource.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(NewsSourceRegistryError, match="https"):
        load_news_source_registry(resource)


def test_news_signals_mark_unknown_ai_entity_for_review_without_deletion():
    from core.news.signals import NewsReviewReason, NewsSignalExtractor

    assessment = NewsSignalExtractor().assess(
        candidate_id="novacortex",
        title="NovaCortex 发布新的开源大模型",
        summary="提供推理 API 和模型权重。",
    )

    assert assessment.positive_signals
    assert "NovaCortex" in assessment.unknown_entities
    assert assessment.review_reason is NewsReviewReason.UNKNOWN_ENTITY
    assert assessment.requires_review is True


def test_rank_only_scores_and_never_filters_by_keyword():
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.rank import (
        rank_items,
    )

    now = datetime.fromisoformat("2026-07-23T12:00:00")
    unknown_model = _item(
        "unknown",
        "NovaCortex 发布新能力",
        summary="新的模型服务。",
    )
    negative_collision = _item(
        "medical",
        "AI clinical assistant model released",
        summary="A patient-facing model API is now available.",
    )
    unrelated = _item("weather", "今天天气晴朗")

    ranked = rank_items(
        [unknown_model, negative_collision, unrelated],
        now=now,
    )

    assert {item.id for item in ranked} == {
        "unknown",
        "medical",
        "weather",
    }
    assert all("news_signals" in item.raw for item in ranked)


def test_news_review_batches_all_uncertain_candidates_once_in_observation():
    from core.news.review import (
        NewsRelevancePolicy,
        NewsReviewMode,
        review_news_candidates,
    )

    items = [
        _item("a", "NovaCortex 发布新大模型"),
        _item("b", "AI clinical model API update"),
    ]
    calls = []

    def execute(invocation):
        calls.append(invocation)
        return _task_result({
            "reviews": [
                {
                    "candidate_id": "a",
                    "relevant": True,
                    "category": "model_release",
                    "importance": 4,
                    "entities": ["NovaCortex"],
                    "confidence": 0.9,
                    "reason_code": "unknown_entity",
                },
                {
                    "candidate_id": "b",
                    "relevant": False,
                    "category": "other",
                    "importance": 1,
                    "entities": [],
                    "confidence": 0.95,
                    "reason_code": "clear_non_ai",
                },
            ]
        })

    outcome = review_news_candidates(
        items,
        policy=NewsRelevancePolicy(
            mode=NewsReviewMode.OBSERVATION,
            activation_ready=False,
        ),
        task_executor=execute,
    )

    assert len(calls) == 1
    assert tuple(outcome.items) == tuple(items)
    assert outcome.reviewed_count == 2
    assert outcome.removed_count == 0
    assert items[1].score == 0.8
    assert items[1].raw["news_review_proposal"]["relevant"] is False


def test_news_review_active_requires_activation_and_model_evidence_to_remove():
    from core.news.review import (
        NewsRelevancePolicy,
        NewsReviewMode,
        review_news_candidates,
    )

    item = _item(
        "medical",
        "AI clinical model API update",
        score=0.8,
    )

    def execute(_invocation):
        return _task_result({
            "reviews": [{
                "candidate_id": "medical",
                "relevant": False,
                "category": "other",
                "importance": 1,
                "entities": [],
                "confidence": 0.95,
                "reason_code": "clear_non_ai",
            }]
        })

    blocked = review_news_candidates(
        [item],
        policy=NewsRelevancePolicy(
            mode=NewsReviewMode.ACTIVE,
            activation_ready=False,
        ),
        task_executor=execute,
    )
    assert blocked.mode is NewsReviewMode.OBSERVATION
    assert blocked.items == (item,)

    active = review_news_candidates(
        [item],
        policy=NewsRelevancePolicy(
            mode=NewsReviewMode.ACTIVE,
            activation_ready=True,
        ),
        task_executor=execute,
    )
    assert active.mode is NewsReviewMode.ACTIVE
    assert active.items == ()
    assert active.removed_count == 1


def test_news_review_failure_retains_and_conservatively_downranks():
    from core.news.review import (
        NewsRelevancePolicy,
        NewsReviewMode,
        review_news_candidates,
    )

    item = _item(
        "medical",
        "AI clinical model API update",
        score=0.8,
    )
    failure = SimpleNamespace(
        ok=False,
        failure=SimpleNamespace(
            code=SimpleNamespace(value="execution_timeout")
        ),
    )
    outcome = review_news_candidates(
        [item],
        policy=NewsRelevancePolicy(
            mode=NewsReviewMode.ACTIVE,
            activation_ready=True,
        ),
        task_executor=lambda _invocation: failure,
    )

    assert outcome.items == (item,)
    assert outcome.failure_code == "execution_timeout"
    assert outcome.downranked_count == 1
    assert item.score == pytest.approx(0.68)


def test_news_review_business_validator_requires_exact_candidate_set():
    from core.task_runtime.validators import (
        TaskBusinessValidationError,
        validate_task_business_output,
    )

    valid = {
        "reviews": [
            {
                "candidate_id": "a",
                "relevant": True,
            },
            {
                "candidate_id": "b",
                "relevant": False,
            },
        ]
    }
    assert validate_task_business_output(
        "news_relevance_review_v1",
        valid,
        request_context={"allowed_candidate_ids": ("a", "b")},
    ) == valid

    with pytest.raises(
        TaskBusinessValidationError,
        match="逐一覆盖",
    ):
        validate_task_business_output(
            "news_relevance_review_v1",
            {"reviews": [valid["reviews"][0]]},
            request_context={"allowed_candidate_ids": ("a", "b")},
        )


def test_news_relevance_route_task_slo_and_feature_are_observation_only():
    from core.lifecycle.feature_registry import FEATURE_LIFECYCLE_REGISTRY
    from core.model_provider.route_registry import (
        require_model_route_descriptor,
    )
    from core.prompt_v2.task_contracts import get_task_contract
    from core.task_runtime.slo import (
        TaskSloActivationError,
        require_task_slo_activation,
        require_task_slo_descriptor,
    )

    route = require_model_route_descriptor("news_relevance_review")
    contract = get_task_contract("tasks/news_relevance_review")
    slo = require_task_slo_descriptor("news_relevance_review")
    feature = FEATURE_LIFECYCLE_REGISTRY.require(
        "news_relevance_review"
    )

    assert route.output_contract_id == "news_relevance_review_v1"
    assert contract is not None
    assert contract.output_failure_policy == (
        "single_attempt_conservative_downrank"
    )
    assert slo.status.value == "baseline_only"
    assert feature.default_enabled is False
    with pytest.raises(TaskSloActivationError):
        require_task_slo_activation("news_relevance_review")
