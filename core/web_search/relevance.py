"""Web Search 结果相关性启发式评分。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


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
_OFF_TOPIC_TERMS = {
    "ad",
    "ads",
    "download",
    "login",
    "proton",
    "protonvpn",
    "signup",
    "vpn",
}
_WEATHER_TERMS = {"天气", "气温", "预报", "weather"}
_WEATHER_DOMAINS = (
    "weather.com.cn",
    "cma.gov.cn",
    "tianqi.com",
    "weather.gov",
)


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


def _domain(item: Any) -> str:
    try:
        return urlparse(str(getattr(item, "url", "") or "")).netloc.lower()
    except Exception:
        return ""


def _is_weather_query(query: str, terms: list[str]) -> bool:
    text = str(query or "").lower()
    return any(term in text or term in terms for term in _WEATHER_TERMS)


def _domain_matches(domain: str, suffixes: tuple[str, ...]) -> bool:
    return any(domain == suffix or domain.endswith(f".{suffix}") for suffix in suffixes)


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
            score=0.5,
            reason="query 缺少可提取关键词，保守接受非空结果",
            matched_terms=[],
        )

    combined_text = "\n".join(_result_text(item) for item in results[:5])
    matched = [term for term in terms if term in combined_text]
    match_ratio = len(matched) / max(1, len(terms))
    score = min(1.0, match_ratio)

    if _is_weather_query(query, terms):
        domains = [_domain(item) for item in results[:5]]
        if any(_domain_matches(domain, _WEATHER_DOMAINS) for domain in domains):
            score = min(1.0, score + 0.25)

    off_topic_hits = sorted(term for term in _OFF_TOPIC_TERMS if term in combined_text)
    if off_topic_hits and not matched:
        return SearchRelevanceDecision(
            ok=False,
            score=0.0,
            reason=f"明显跑偏：结果包含 {', '.join(off_topic_hits[:3])}，且未命中 query 关键词",
            matched_terms=[],
        )
    if off_topic_hits:
        score = max(0.0, score - 0.2)

    ok = score >= 0.5
    reason = "结果与 query 相关" if ok else "结果未充分命中 query 关键词"
    return SearchRelevanceDecision(
        ok=ok,
        score=round(score, 3),
        reason=reason,
        matched_terms=_dedupe(matched),
    )
