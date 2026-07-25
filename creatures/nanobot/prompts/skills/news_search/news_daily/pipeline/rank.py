"""评分排序。"""

import logging
from datetime import datetime

from core.news.policy import DEFAULT_NEWS_RANKING_POLICY
from core.news.signals import NewsSignalExtractor
from core.time_utils import db_now_naive

from ..schema import NewsItem
from .normalize import _parse_date

logger = logging.getLogger("nanobot.news_daily.rank")
_SIGNAL_EXTRACTOR = NewsSignalExtractor()


def rank_items(items: list[NewsItem], *, now: datetime | None = None) -> list[NewsItem]:
    """只评分排序；词典和正则只能生成信号，不能删除候选。"""
    reference_time = now or db_now_naive()
    policy = DEFAULT_NEWS_RANKING_POLICY

    for item in items:
        if item.published_at:
            try:
                dt = _parse_date(item.published_at)
                if dt is None:
                    raise ValueError("unparseable published_at")
                age = reference_time - dt
                if age.total_seconds() < 0:
                    item.freshness = 0.0
                else:
                    days = age.days
                    item.freshness = (
                        1.0
                        if days <= 1
                        else (0.8 if days <= 3 else (0.5 if days <= 7 else 0.2))
                    )
            except Exception:
                item.freshness = 0.5
        else:
            item.freshness = policy.unknown_date_score

        assessment = _SIGNAL_EXTRACTOR.assess(
            candidate_id=item.id,
            title=item.title,
            summary=item.summary,
        )
        item.relevance = assessment.relevance_score
        item.raw["news_signals"] = {
            "positive": list(assessment.positive_signals),
            "negative": list(assessment.negative_signals),
            "known_entities": list(assessment.known_entities),
            "unknown_entities": list(assessment.unknown_entities),
            "review_reason": assessment.review_reason.value,
        }

        item.score = round(
            item.trust * policy.trust_weight
            + item.freshness * policy.freshness_weight
            + item.relevance * policy.relevance_weight,
            2,
        )

    items.sort(key=lambda x: x.score, reverse=True)
    return items
