"""Web Search 结果相关性启发式评分。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9.+_-]*")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}

# 相关性评分参数(命名常量,避免散落魔法值)
RELEVANCE_ACCEPT_THRESHOLD = 0.5   # score ≥ 此值视为结果与 query 相关
EMPTY_QUERY_SCORE = 0.5            # query 无可提取关键词时的保守分
MAX_RESULTS_CONSIDERED = 5         # 相关性评分只看前 N 条结果


@dataclass(frozen=True)
class SearchRelevanceDecision:
    ok: bool
    score: float
    reason: str
    matched_terms: list[str]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _cjk_terms(text: str) -> list[str]:
    terms: list[str] = []
    for chunk in _CJK_RE.findall(text):
        if len(chunk) >= 2:
            terms.append(chunk)
        if len(chunk) > 2:
            terms.extend(chunk[index : index + 2] for index in range(0, len(chunk) - 1))
    return terms


def _word_terms(text: str) -> list[str]:
    terms = []
    for term in _WORD_RE.findall(text.lower()):
        if len(term) < 2 or term in _STOPWORDS:
            continue
        terms.append(term)
    return terms


def extract_query_terms(query: str) -> list[str]:
    """提取用于相关性判断的轻量关键词。"""

    text = str(query or "").strip().lower()
    return _dedupe(_cjk_terms(text) + _word_terms(text))


def _result_text(item: Any) -> str:
    title = str(getattr(item, "title", "") or "")
    snippet = str(getattr(item, "snippet", "") or "")
    url = str(getattr(item, "url", "") or "")
    return f"{title}\n{snippet}\n{url}".lower()


def judge_search_relevance(query: str, results: list[Any]) -> SearchRelevanceDecision:
    """判断一批搜索结果是否足够匹配 query。"""

    if not results:
        return SearchRelevanceDecision(
            ok=False,
            score=0.0,
            reason="无搜索结果",
            matched_terms=[],
        )

    terms = extract_query_terms(query)
    if not terms:
        return SearchRelevanceDecision(
            ok=True,
            score=EMPTY_QUERY_SCORE,
            reason="query 缺少可提取关键词，保守接受非空结果",
            matched_terms=[],
        )

    combined_text = "\n".join(_result_text(item) for item in results[:MAX_RESULTS_CONSIDERED])
    matched = [term for term in terms if term in combined_text]
    match_ratio = len(matched) / max(1, len(terms))
    score = min(1.0, match_ratio)

    ok = score >= RELEVANCE_ACCEPT_THRESHOLD
    reason = "结果与 query 相关" if ok else "结果未充分命中 query 关键词"
    return SearchRelevanceDecision(
        ok=ok,
        score=round(score, 3),
        reason=reason,
        matched_terms=_dedupe(matched),
    )
