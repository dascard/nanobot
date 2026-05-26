"""语义检索测试 fake provider。"""

from __future__ import annotations

import hashlib

from core.semantic.reranker import RerankResult, SemanticCandidate, normalize_reranker_score


class FakeEmbeddingProvider:
    def __init__(self, dim: int = 8):
        self.dim = int(dim)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(str(text).encode("utf-8")).digest()
        values: list[float] = []
        for index in range(self.dim):
            byte = digest[index % len(digest)]
            values.append(round((byte / 255.0) * 2.0 - 1.0, 6))
        return values


class FakeRerankerProvider:
    def __init__(self, scores: dict[str, float] | None = None):
        self.scores = scores or {}
        self.model_name = "fake-reranker"
        self.score_mode = "sigmoid"

    def rerank(
        self,
        query: str,
        candidates: list[SemanticCandidate],
        *,
        top_k: int | None = None,
    ) -> list[RerankResult]:
        limited = candidates[:top_k] if top_k else list(candidates)
        results = []
        for candidate in limited:
            raw = self.scores.get(candidate.candidate_id)
            if raw is None:
                raw = 1.0 if str(query) and str(query) in candidate.text else 0.0
            results.append(RerankResult(
                candidate_id=candidate.candidate_id,
                raw_score=float(raw),
                score=normalize_reranker_score(float(raw), mode=self.score_mode),
                model=self.model_name,
                score_mode=self.score_mode,
            ))
        return sorted(results, key=lambda item: item.score or 0.0, reverse=True)
