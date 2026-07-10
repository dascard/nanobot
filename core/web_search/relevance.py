"""Web Search 结果相关性启发式评分。"""

from __future__ import annotations

import re
import unicodedata
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
EXECUTED_QUERY_ACCEPT_THRESHOLD = 0.6
_QUERY_MODIFIERS = frozenset({
    "analysis",
    "date",
    "latest",
    "news",
    "official",
    "paper",
    "papers",
    "report",
    "reports",
    "research",
    "site",
    "study",
    "update",
    "官网",
    "官方",
    "报告",
    "新闻",
    "最新",
    "更新",
    "研究",
    "论文",
})
_GENERIC_CJK_ANCHORS = frozenset({
    "安全",
    "最新",
    "报告",
    "研究",
    "更新",
    "官方",
    "新闻",
    "论文",
})


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
    if re.match(r"^https?://", title.strip(), re.IGNORECASE):
        title = ""
    return f"{title}\n{snippet}".lower()


def _normalized_query_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\u200b-\u200f\u2060\ufeff]", " ", text)


def _query_scope_atoms(value: str) -> list[str]:
    """提取适合做执行权限比较的原子词，避免连续中文超串洗白。"""

    text = _normalized_query_text(value)
    text = re.sub(r"\bsite\s*:\s*\S+", " ", text, flags=re.IGNORECASE)
    for modifier in sorted(_QUERY_MODIFIERS, key=len, reverse=True):
        if _CJK_RE.fullmatch(modifier):
            text = text.replace(modifier, " ")
        else:
            text = re.sub(
                rf"(?<![a-z0-9]){re.escape(modifier)}(?![a-z0-9])",
                " ",
                text,
            )

    atoms: list[str] = []
    for chunk in _CJK_RE.findall(text):
        if len(chunk) == 2:
            atoms.append(chunk)
        elif len(chunk) > 2:
            atoms.extend(
                chunk[index : index + 2]
                for index in range(0, len(chunk) - 1)
            )
    atoms.extend(_word_terms(text))
    return _dedupe(atoms)


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


def judge_executed_query_relevance(
    research_query: str,
    executed_query: str,
) -> SearchRelevanceDecision:
    """要求搜索子查询共享主题锚点，且不得混入新的无关实体。"""

    research_tokens = _query_scope_atoms(research_query)
    executed_tokens = _query_scope_atoms(executed_query)
    if not executed_tokens:
        return SearchRelevanceDecision(
            ok=False,
            score=0.0,
            reason="实际搜索 query 缺少可核验关键词",
            matched_terms=[],
        )

    research_token_set = set(research_tokens)

    def shared_with_research(token: str) -> bool:
        return token in research_token_set

    matched = [token for token in executed_tokens if shared_with_research(token)]
    unrelated = [
        token
        for token in executed_tokens
        if not token.isdigit()
        and not shared_with_research(token)
    ]
    high_information_anchors = [
        token
        for token in matched
        if (
            (_CJK_RE.fullmatch(token) is not None
             and len(token) >= 2
             and token not in _GENERIC_CJK_ANCHORS)
            or (
                _CJK_RE.fullmatch(token) is None
                and not token.isdigit()
                and len(token) >= 3
                and token not in _QUERY_MODIFIERS
            )
        )
    ]
    scored_tokens = [
        token
        for token in executed_tokens
        if not token.isdigit()
    ]
    score = len([
        token for token in scored_tokens if shared_with_research(token)
    ]) / max(1, len(scored_tokens))
    ok = (
        bool(high_information_anchors)
        and not unrelated
        and score >= EXECUTED_QUERY_ACCEPT_THRESHOLD
    )
    return SearchRelevanceDecision(
        ok=ok,
        score=round(score, 3),
        reason=(
            "搜索子查询与研究主题直接相关"
            if ok
            else "搜索子查询缺少主题锚点或混入无关实体"
        ),
        matched_terms=_dedupe(matched),
    )
