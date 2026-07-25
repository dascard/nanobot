"""新闻候选的确定性召回信号；本模块不返回删除决定。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class NewsReviewReason(StrEnum):
    STABLE = "stable"
    CONFLICT = "conflict"
    UNKNOWN_ENTITY = "unknown_entity"
    BOUNDARY = "boundary"


_POSITIVE_PATTERNS = (
    (
        "ai_model",
        re.compile(
            r"(?:\b(?:llm|gpt|claude|gemini|qwen|deepseek|mistral|"
            r"llama|grok|kimi|transformer|embedding)\b|"
            r"大模型|人工智能|多模态|智能体|模型权重)",
            re.IGNORECASE,
        ),
    ),
    (
        "ai_platform",
        re.compile(
            r"(?:\b(?:openai|anthropic|huggingface|nvidia|api|token|"
            r"benchmark|fine[-. ]?tun(?:e|ing)?)\b|"
            r"模型发布|推理服务|算力|微调|开源模型)",
            re.IGNORECASE,
        ),
    ),
)
_NEGATIVE_PATTERNS = (
    (
        "medical_context",
        re.compile(
            r"\b(?:clinical|patient|hospital|surgery|cancer|drug|"
            r"neuroscience|diagnosis)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "entertainment_context",
        re.compile(
            r"\b(?:oscar|movie|film|actor|music streaming)\b",
            re.IGNORECASE,
        ),
    ),
)
KNOWN_ENTITY_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "openai": ("openai", "chatgpt", "gpt"),
    "anthropic": ("anthropic", "claude"),
    "google": ("google", "deepmind", "gemini"),
    "deepseek": ("deepseek", "深度求索"),
    "qwen": ("qwen", "通义千问", "alibaba"),
    "kimi": ("kimi", "moonshot", "月之暗面"),
    "mistral": ("mistral",),
    "meta": ("meta", "llama"),
    "nvidia": ("nvidia",),
    "xai": ("xai", "grok"),
})
TOPIC_SIGNAL_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "model_release": (
        "发布",
        "推出",
        "release",
        "launch",
        "open-source",
        "开源",
    ),
    "benchmark": ("benchmark", "评测", "swe-bench", "mmlu"),
    "funding": ("融资", "funding", "raised", "valuation"),
    "product": ("app", "agent", "api", "platform", "browser"),
    "policy": ("regulation", "policy", "法案", "监管"),
    "research": ("paper", "论文", "arxiv", "research"),
    "incident": (
        "outage",
        "leak",
        "breach",
        "security",
        "故障",
        "泄露",
    ),
    "infrastructure": ("gpu", "芯片", "算力", "data center", "数据中心"),
})
NEWS_TOKEN_STOP_WORDS = frozenset({
    "发布",
    "宣布",
    "推出",
    "上线",
    "开源",
    "模型",
    "AI",
    "正式",
    "全新",
    "最新",
    "重磅",
})
_ENTITY_CANDIDATE = re.compile(
    r"\b[A-Z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*\b"
)
_GENERIC_ENTITY_TOKENS = frozenset({
    "AI",
    "API",
    "GPU",
    "LLM",
    "RSS",
    "HTML",
    "HTTP",
    "HTTPS",
})


@dataclass(frozen=True, slots=True)
class NewsSignalAssessment:
    candidate_id: str
    positive_signals: tuple[str, ...]
    negative_signals: tuple[str, ...]
    known_entities: tuple[str, ...]
    unknown_entities: tuple[str, ...]
    relevance_score: float
    review_reason: NewsReviewReason

    @property
    def requires_review(self) -> bool:
        return self.review_reason is not NewsReviewReason.STABLE


class NewsSignalExtractor:
    """只输出可审计信号和边界原因，绝不丢弃候选。"""

    def assess(
        self,
        *,
        candidate_id: str,
        title: str,
        summary: str = "",
    ) -> NewsSignalAssessment:
        text = f"{title or ''} {summary or ''}".strip()
        lowered = text.lower()
        positive = tuple(
            signal
            for signal, pattern in _POSITIVE_PATTERNS
            if pattern.search(text)
        )
        negative = tuple(
            signal
            for signal, pattern in _NEGATIVE_PATTERNS
            if pattern.search(text)
        )
        known = tuple(
            entity
            for entity, aliases in KNOWN_ENTITY_ALIASES.items()
            if any(alias.lower() in lowered for alias in aliases)
        )
        unknown = tuple(sorted({
            token
            for token in _ENTITY_CANDIDATE.findall(text)
            if token.upper() not in _GENERIC_ENTITY_TOKENS
            and all(
                token.lower() not in aliases
                for aliases in KNOWN_ENTITY_ALIASES.values()
            )
        }))[:8]

        score = min(
            1.0,
            max(
                0.0,
                0.15
                + len(positive) * 0.30
                + (0.20 if known else 0.0)
                + (0.10 if unknown and positive else 0.0)
                - len(negative) * 0.25,
            ),
        )
        if positive and negative:
            reason = NewsReviewReason.CONFLICT
        elif unknown and positive:
            reason = NewsReviewReason.UNKNOWN_ENTITY
        elif not positive or 0.25 <= score <= 0.65:
            reason = NewsReviewReason.BOUNDARY
        else:
            reason = NewsReviewReason.STABLE
        return NewsSignalAssessment(
            candidate_id=str(candidate_id or "").strip(),
            positive_signals=positive,
            negative_signals=negative,
            known_entities=known,
            unknown_entities=unknown,
            relevance_score=round(score, 4),
            review_reason=reason,
        )
