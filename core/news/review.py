"""新闻相关性批量审核的 Task Adapter 与确定性生效 Policy。"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.news.policy import DEFAULT_NEWS_RANKING_POLICY
from core.news.signals import NewsSignalAssessment, NewsSignalExtractor
from core.settings_service import settings
from core.task_runtime import (
    TaskInvocation,
    TaskResult,
    execute_task,
    thaw_task_value,
)
from core.task_runtime.slo import (
    TaskSloActivationError,
    require_task_slo_activation,
)


logger = logging.getLogger("nanobot.news.review")


class NewsReviewMode(StrEnum):
    DISABLED = "disabled"
    OBSERVATION = "observation"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class NewsRelevancePolicy:
    mode: NewsReviewMode = NewsReviewMode.DISABLED
    confidence_threshold: float = 0.80
    max_batch_size: int = 24
    activation_ready: bool = False
    source: str = "default"

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", NewsReviewMode(self.mode))
        threshold = float(self.confidence_threshold)
        if not 0 <= threshold <= 1:
            raise ValueError("新闻审核置信度门槛必须位于 [0, 1]")
        if (
            isinstance(self.max_batch_size, bool)
            or not 1 <= int(self.max_batch_size) <= 40
        ):
            raise ValueError("新闻审核批大小必须位于 [1, 40]")
        object.__setattr__(self, "confidence_threshold", threshold)
        object.__setattr__(self, "max_batch_size", int(self.max_batch_size))

    @property
    def effective_mode(self) -> NewsReviewMode:
        if self.mode is NewsReviewMode.ACTIVE and not self.activation_ready:
            return NewsReviewMode.OBSERVATION
        return self.mode


@dataclass(frozen=True, slots=True)
class NewsReviewOutcome:
    items: tuple[Any, ...]
    mode: NewsReviewMode
    requested_count: int
    reviewed_count: int
    removed_count: int
    downranked_count: int
    failure_code: str = ""


def _parse_mode(value: object) -> NewsReviewMode:
    try:
        return NewsReviewMode(str(value or "").strip().lower())
    except ValueError:
        return NewsReviewMode.DISABLED


def resolve_news_relevance_policy() -> NewsRelevancePolicy:
    requested = _parse_mode(
        settings.get_str("news.relevance_review.mode", "disabled")
    )
    source = "setting"
    active_allowed = settings.get_bool(
        "news.relevance_review.active_allowed",
        False,
    )
    activation_ready = False
    if requested is NewsReviewMode.ACTIVE and active_allowed:
        try:
            require_task_slo_activation("news_relevance_review")
        except TaskSloActivationError:
            source = "active_blocked_by_slo"
        else:
            activation_ready = True
    elif requested is NewsReviewMode.ACTIVE:
        source = "active_blocked_by_release_gate"
    return NewsRelevancePolicy(
        mode=requested,
        confidence_threshold=settings.get_float(
            "news.relevance_review.confidence_threshold",
            0.80,
        ),
        max_batch_size=settings.get_int(
            "news.relevance_review.max_batch_size",
            24,
        ),
        activation_ready=activation_ready,
        source=source,
    )


def _assessment_for_item(
    item: Any,
    extractor: NewsSignalExtractor,
) -> NewsSignalAssessment:
    return extractor.assess(
        candidate_id=str(getattr(item, "id", "") or ""),
        title=str(getattr(item, "title", "") or ""),
        summary=str(getattr(item, "summary", "") or ""),
    )


def _review_message(
    items: Sequence[Any],
    assessments: Mapping[str, NewsSignalAssessment],
) -> str:
    cards = []
    for item in items:
        candidate_id = str(getattr(item, "id", "") or "")
        assessment = assessments[candidate_id]
        cards.append({
            "candidate_id": candidate_id,
            "title": str(getattr(item, "title", "") or "")[:240],
            "summary": str(getattr(item, "summary", "") or "")[:600],
            "source_id": str(
                getattr(item, "source_name", "") or ""
            )[:96],
            "published_at": str(
                getattr(item, "published_at", "") or ""
            )[:64],
            "positive_signals": list(assessment.positive_signals),
            "negative_signals": list(assessment.negative_signals),
            "known_entities": list(assessment.known_entities),
            "unknown_entities": list(assessment.unknown_entities),
            "review_reason": assessment.review_reason.value,
        })
    return json.dumps(
        {"candidates": cards},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _downrank(items: Sequence[Any]) -> int:
    factor = DEFAULT_NEWS_RANKING_POLICY.failure_downrank_factor
    count = 0
    for item in items:
        old_score = float(getattr(item, "score", 0.0) or 0.0)
        setattr(item, "score", round(old_score * factor, 4))
        count += 1
    return count


def review_news_candidates(
    items: Sequence[Any],
    *,
    policy: NewsRelevancePolicy | None = None,
    task_executor: Callable[[TaskInvocation], TaskResult] = execute_task,
) -> NewsReviewOutcome:
    """一次批量审核最终候选；任何失败都保留候选。"""

    current_policy = policy or resolve_news_relevance_policy()
    effective_mode = current_policy.effective_mode
    original = list(items)
    if effective_mode is NewsReviewMode.DISABLED or not original:
        return NewsReviewOutcome(
            items=tuple(original),
            mode=effective_mode,
            requested_count=0,
            reviewed_count=0,
            removed_count=0,
            downranked_count=0,
        )

    extractor = NewsSignalExtractor()
    assessments = {
        assessment.candidate_id: assessment
        for assessment in (
            _assessment_for_item(item, extractor) for item in original
        )
        if assessment.candidate_id
    }
    review_items = [
        item
        for item in original
        if (
            candidate_id := str(getattr(item, "id", "") or "")
        ) in assessments
        and assessments[candidate_id].requires_review
    ][: current_policy.max_batch_size]
    if not review_items:
        return NewsReviewOutcome(
            items=tuple(original),
            mode=effective_mode,
            requested_count=0,
            reviewed_count=0,
            removed_count=0,
            downranked_count=0,
        )
    candidate_ids = tuple(
        str(getattr(item, "id", "") or "") for item in review_items
    )
    message = _review_message(review_items, assessments)
    batch_hash = hashlib.sha256(
        "\n".join(candidate_ids).encode("utf-8")
    ).hexdigest()
    result = task_executor(TaskInvocation(
        invocation_id="news_relevance_review",
        route_key="news_relevance_review",
        input_values={"message": message},
        request_context={"allowed_candidate_ids": candidate_ids},
        idempotency_key=f"news_relevance_review:{batch_hash}",
        timeout_budget_seconds=30.0,
    ))
    if not result.ok:
        failure_code = (
            result.failure.code.value
            if result.failure is not None
            else "provider_error"
        )
        downranked = (
            _downrank(review_items)
            if effective_mode is NewsReviewMode.ACTIVE
            else 0
        )
        return NewsReviewOutcome(
            items=tuple(sorted(
                original,
                key=lambda item: float(
                    getattr(item, "score", 0.0) or 0.0
                ),
                reverse=True,
            )),
            mode=effective_mode,
            requested_count=len(review_items),
            reviewed_count=0,
            removed_count=0,
            downranked_count=downranked,
            failure_code=failure_code,
        )

    parsed = thaw_task_value(result.parsed_value)
    reviews = {
        str(review["candidate_id"]): review
        for review in parsed["reviews"]
    }
    if effective_mode is NewsReviewMode.OBSERVATION:
        for item in review_items:
            candidate_id = str(getattr(item, "id", "") or "")
            raw = getattr(item, "raw", None)
            if isinstance(raw, dict):
                proposal = reviews[candidate_id]
                raw["news_review_proposal"] = {
                    "relevant": bool(proposal["relevant"]),
                    "category": str(proposal["category"]),
                    "importance": int(proposal["importance"]),
                    "confidence": float(proposal["confidence"]),
                    "reason_code": str(proposal["reason_code"]),
                }
        return NewsReviewOutcome(
            items=tuple(original),
            mode=effective_mode,
            requested_count=len(review_items),
            reviewed_count=len(reviews),
            removed_count=0,
            downranked_count=0,
        )

    kept: list[Any] = []
    removed_count = 0
    downranked_count = 0
    review_ids = set(reviews)
    for item in original:
        candidate_id = str(getattr(item, "id", "") or "")
        if candidate_id not in review_ids:
            kept.append(item)
            continue
        review = reviews[candidate_id]
        confidence = float(review["confidence"])
        relevant = bool(review["relevant"])
        reason_code = str(review["reason_code"])
        if (
            not relevant
            and confidence >= current_policy.confidence_threshold
            and reason_code == "clear_non_ai"
        ):
            removed_count += 1
            continue
        if not relevant:
            downranked_count += _downrank((item,))
        item.category = str(review["category"])
        kept.append(item)
    kept.sort(
        key=lambda item: float(getattr(item, "score", 0.0) or 0.0),
        reverse=True,
    )
    return NewsReviewOutcome(
        items=tuple(kept),
        mode=effective_mode,
        requested_count=len(review_items),
        reviewed_count=len(reviews),
        removed_count=removed_count,
        downranked_count=downranked_count,
    )


__all__ = [
    "NewsRelevancePolicy",
    "NewsReviewMode",
    "NewsReviewOutcome",
    "resolve_news_relevance_policy",
    "review_news_candidates",
]
