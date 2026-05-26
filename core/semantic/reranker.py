"""Reranker provider 抽象与分数归一化。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.semantic.scoring import clamp01


@dataclass(frozen=True)
class SemanticCandidate:
    candidate_id: str
    source_type: str
    text: str
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RerankResult:
    candidate_id: str
    raw_score: float | None
    score: float | None
    model: str = ""
    score_mode: str = "sigmoid"


class RerankerProvider:
    model_name = ""
    score_mode = "sigmoid"

    def rerank(
        self,
        query: str,
        candidates: list[SemanticCandidate],
        *,
        top_k: int | None = None,
    ) -> list[RerankResult]:
        raise NotImplementedError


def normalize_reranker_score(
    raw: float | None,
    *,
    mode: str = "sigmoid",
    best: float | None = None,
    worst: float | None = None,
) -> float | None:
    if raw is None:
        return None
    value = float(raw)
    if mode == "sigmoid":
        if value >= 0:
            z = math.exp(-value)
            return 1.0 / (1.0 + z)
        z = math.exp(value)
        return z / (1.0 + z)
    if mode == "minmax":
        if best is None or worst is None or best == worst:
            return 1.0
        return clamp01((value - float(worst)) / (float(best) - float(worst)))
    return clamp01(value)


def build_reranker_text(candidate: SemanticCandidate) -> str:
    meta = candidate.metadata or {}
    source_type = candidate.source_type
    if source_type == "group_memory":
        return "\n".join([
            "[类型] 群体记忆",
            f"[记忆类型] {meta.get('memory_type', '')}",
            f"[内容] {candidate.text}",
            f"[证据摘要] {meta.get('evidence_short_summary', '')}",
        ]).strip()
    if source_type == "knowledge":
        return "\n".join([
            f"[标题] {candidate.title}",
            f"[来源] {meta.get('source_name') or meta.get('domain', '')}",
            f"[发布时间] {meta.get('published_at', '')}",
            f"[正文片段] {candidate.text}",
        ]).strip()
    if source_type == "sticker":
        return "\n".join([
            f"[表情名称] {candidate.title}",
            f"[描述] {candidate.text}",
            f"[标签] {', '.join(meta.get('tags') or [])}",
            f"[情绪] {', '.join(meta.get('emotions') or [])}",
            f"[适用场景] {meta.get('scenario', '')}",
        ]).strip()
    return "\n".join(part for part in [candidate.title, candidate.text] if part).strip()


class LocalCrossEncoderRerankerProvider(RerankerProvider):
    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        *,
        model: Any = None,
        score_mode: str = "sigmoid",
        max_text_chars: int = 1200,
    ):
        self.model_name = model_name
        self.model = model
        self.score_mode = score_mode
        self.max_text_chars = int(max_text_chars)

    def _load_model(self) -> Any:
        if self.model is not None:
            return self.model
        try:
            from sentence_transformers import CrossEncoder
        except Exception as exc:  # pragma: no cover - 依赖缺失只在集成环境验证
            raise RuntimeError("sentence-transformers is required for LocalCrossEncoderRerankerProvider") from exc
        self.model = CrossEncoder(self.model_name)
        return self.model

    def rerank(
        self,
        query: str,
        candidates: list[SemanticCandidate],
        *,
        top_k: int | None = None,
    ) -> list[RerankResult]:
        model = self._load_model()
        limited = candidates[:top_k] if top_k else list(candidates)
        pairs = [
            [query, build_reranker_text(candidate)[: self.max_text_chars]]
            for candidate in limited
        ]
        raw_scores = model.predict(pairs) if pairs else []
        results = [
            RerankResult(
                candidate_id=candidate.candidate_id,
                raw_score=float(raw),
                score=normalize_reranker_score(float(raw), mode=self.score_mode),
                model=self.model_name,
                score_mode=self.score_mode,
            )
            for candidate, raw in zip(limited, raw_scores)
        ]
        return sorted(results, key=lambda item: item.score or 0.0, reverse=True)


class HttpRerankerProvider(RerankerProvider):
    def __init__(
        self,
        url: str,
        *,
        timeout_ms: int = 3000,
        model_name: str = "http-reranker",
        score_mode: str = "sigmoid",
        max_text_chars: int = 1200,
    ):
        self.url = url
        self.timeout_ms = int(timeout_ms)
        self.model_name = model_name
        self.score_mode = score_mode
        self.max_text_chars = int(max_text_chars)

    def rerank(
        self,
        query: str,
        candidates: list[SemanticCandidate],
        *,
        top_k: int | None = None,
    ) -> list[RerankResult]:
        limited = candidates[:top_k] if top_k else list(candidates)
        payload = {
            "query": query,
            "candidates": [
                {
                    "id": candidate.candidate_id,
                    "text": build_reranker_text(candidate)[: self.max_text_chars],
                    "source_type": candidate.source_type,
                }
                for candidate in limited
            ],
            "top_k": top_k,
        }
        response = httpx.post(self.url, json=payload, timeout=self.timeout_ms / 1000)
        response.raise_for_status()
        data = response.json()
        rows = data.get("results") if isinstance(data, dict) else data
        results: list[RerankResult] = []
        for row in rows or []:
            raw = row.get("raw_score", row.get("score"))
            normalized = row.get("normalized_score")
            score = float(normalized) if normalized is not None else normalize_reranker_score(raw, mode=self.score_mode)
            results.append(RerankResult(
                candidate_id=str(row.get("candidate_id") or row.get("id") or ""),
                raw_score=None if raw is None else float(raw),
                score=score,
                model=str(row.get("model") or data.get("model") or self.model_name),
                score_mode=str(row.get("score_mode") or self.score_mode),
            ))
        return sorted(results, key=lambda item: item.score or 0.0, reverse=True)
