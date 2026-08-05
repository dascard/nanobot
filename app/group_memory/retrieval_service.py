"""群体记忆检索服务。

第一版不依赖 embedding，用规则打分和类型限额保证注入结果可解释。
"""

from __future__ import annotations

import json
import logging
import math
import re
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from core.db import (
    GroupMemoryQueryRepositoryPort,
    group_memory_repository,
)
from core.group_memory import (
    group_memory_compatibility_projection,
    resolve_group_memory_identity,
)
from core.group_learning.prompt_injection import (
    evaluate_group_memory_prompt_injection,
)
from core.retrieval import (
    AllowAllCitationPolicy,
    ManagedRerankerExecutor,
    NullDebugTraceSink,
    RankingOutcome,
    RerankOutcome,
    RerankerExecutorPort,
    RetrievalCandidates,
    RetrievalPipeline,
    RetrievalPipelineState,
    RetrievalRequest,
    get_retrieval_reranker_executor,
)
from core.semantic.reranker import SemanticCandidate
from core.time_utils import db_now_naive
from core.token_utils import estimate_tokens

logger = logging.getLogger("nanobot.group_memory.retrieval")


TYPE_LIMITS = {
    "style": 2,
    "topic": 3,
    "expression": 3,
    "slang": 2,
}
TYPE_PRIOR = {
    "style": 0.80,
    "topic": 0.75,
    "expression": 0.65,
    "slang": 0.40,
}
STRICT_RELEVANCE_TYPES = {"slang"}
DEFAULT_MIN_RELEVANCE = 0.05
STRICT_MIN_RELEVANCE = 0.18


class GroupMemoryRerankerTimeout(TimeoutError):
    """群记忆 reranker 超过当前请求的硬时限。"""


@dataclass
class GroupMemorySelection:
    selected: list[Any] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    score_components: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def selected_ids(self) -> list[int]:
        return [
            int(getattr(row, "id", 0) or 0)
            for row in self.selected
            if getattr(row, "id", 0)
        ]


@dataclass(frozen=True, slots=True)
class _GroupMemoryRecall:
    group_id: str
    query_text: str
    rows: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class _GroupMemoryCandidate:
    row: Any
    memory_type: str
    relevance: float
    components: Mapping[str, Any]


def _safe_meta(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _tokens(text: str) -> set[str]:
    raw = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", raw))
    cjk = [ch for ch in raw if "\u4e00" <= ch <= "\u9fff"]
    grams = set()
    for size in (2, 3, 4):
        grams.update(
            "".join(cjk[i : i + size]) for i in range(max(0, len(cjk) - size + 1))
        )
    return {token for token in words | grams if token.strip()}


def _lexical_relevance(memory_text: str, query_text: str) -> float:
    memory_tokens = _tokens(memory_text)
    query_tokens = _tokens(query_text)
    if not memory_tokens or not query_tokens:
        return 0.0
    overlap = len(memory_tokens & query_tokens)
    if overlap <= 0:
        return 0.0
    return min(1.0, overlap / max(3, min(len(memory_tokens), len(query_tokens))))


def _recency_weight(last_seen: datetime | None) -> float:
    if not last_seen:
        return 0.0
    days = max(0.0, (db_now_naive() - last_seen).total_seconds() / 86400)
    return max(0.0, min(1.0, math.exp(-days / 30.0)))


def _evidence_weight(count: int) -> float:
    return min(1.0, max(0.0, float(count or 0) / 5.0))


class GroupMemoryRetrievalService:
    def __init__(
        self,
        repository,
        *,
        reranker_provider: Any = None,
        min_reranker: float = 0.55,
        max_sql_candidates: int = 2000,
        reranker_timeout_ms: int | None = None,
        reranker_executor: RerankerExecutorPort | None = None,
    ):
        self.repository: GroupMemoryQueryRepositoryPort = (
            group_memory_repository(repository)
        )
        self.reranker_provider = reranker_provider
        self.min_reranker = float(min_reranker)
        self.max_sql_candidates = int(max_sql_candidates)
        self.reranker_timeout_ms = (
            max(1, int(reranker_timeout_ms))
            if reranker_timeout_ms is not None
            else None
        )
        self.reranker_executor = reranker_executor

    def select(
        self,
        *,
        group_id: str,
        platform: str = "qq",
        current_user_input: str = "",
        recent_messages: list[dict[str, Any]] | None = None,
        max_items: int = 10,
        max_chars: int = 1200,
        max_tokens: int | None = None,
    ) -> GroupMemorySelection:
        identity = resolve_group_memory_identity(
            group_id,
            platform=platform,
        )
        projection = group_memory_compatibility_projection(identity)
        recent_text = "\n".join(
            str(item.get("content") or "") for item in (recent_messages or [])
        )
        query_text = f"{current_user_input}\n{recent_text}".strip()
        request = RetrievalRequest(
            query=query_text,
            limit=max(1, int(max_items)),
            options={
                "domain": "group_memory",
                "group_id": projection,
                "chat_stream_id": identity.chat_stream_id,
                "platform": identity.platform,
                "max_chars": int(max_chars),
                "max_tokens": int(max_tokens or 0),
            },
        )
        return self._build_pipeline().execute(request)

    def _build_pipeline(self) -> RetrievalPipeline:
        return RetrievalPipeline(
            candidate_source=_GroupMemoryCandidateSource(self),
            citation_policy=AllowAllCitationPolicy(),
            filter_policy=_GroupMemoryFilterPolicy(self),
            scoring_policy=_GroupMemoryScoringPolicy(self),
            reranker=_GroupMemoryReranker(self),
            budget_policy=_GroupMemoryBudgetPolicy(self),
            debug_trace_sink=NullDebugTraceSink(),
            result_builder=_GroupMemoryResultBuilder(),
            provider_id="group_memory",
        )

    def _reranker_scores(
        self, query_text: str, rows: list[Any]
    ) -> dict[int, float] | None:
        """返回 reranker 分数;运行时失败返回 None 表示需要降级。"""
        if self.reranker_provider is None or not rows:
            return {}
        candidates = [
            SemanticCandidate(
                candidate_id=f"group_memory:{row.id}:memory",
                source_type="group_memory",
                title=f"群体记忆：{row.memory_type}",
                text=row.content or "",
                metadata={
                    "memory_type": row.memory_type,
                    "evidence_short_summary": _safe_meta(
                        getattr(row, "meta_json", "")
                    ).get("evidence_short_summary", ""),
                },
            )
            for row in rows
        ]
        try:
            if self.reranker_timeout_ms is None:
                results = self.reranker_provider.rerank(
                    query_text, candidates, top_k=50
                )
            else:
                executor = self.reranker_executor or get_retrieval_reranker_executor()
                owns_executor = executor is None
                if executor is None:
                    executor = ManagedRerankerExecutor(
                        max_workers=1,
                        thread_name_prefix="group-memory-reranker-once",
                    )
                    executor.start()
                try:
                    future = executor.submit(
                        self.reranker_provider.rerank,
                        query_text,
                        candidates,
                        top_k=50,
                    )
                    try:
                        results = future.result(
                            timeout=self.reranker_timeout_ms / 1000.0
                        )
                    except FutureTimeoutError as exc:
                        future.cancel()
                        raise GroupMemoryRerankerTimeout(
                            "group memory reranker exceeded "
                            f"{self.reranker_timeout_ms}ms"
                        ) from exc
                finally:
                    if owns_executor:
                        executor.stop()
        except GroupMemoryRerankerTimeout:
            raise
        except Exception:
            # 运行时失败若按空分数处理,所有候选都会因 low_reranker 被静默
            # 丢弃;返回 None 让调用方走词面相关度降级路径。
            logger.warning(
                "[GroupMemory] reranker 调用失败,按运行时降级继续",
                exc_info=True,
            )
            return None
        scores: dict[int, float] = {}
        for result in results:
            parts = str(result.candidate_id).split(":")
            if len(parts) >= 2 and parts[1].isdigit():
                scores[int(parts[1])] = float(result.score or 0.0)
        return scores

    def _score_components(
        self, row: Any, memory_type: str, relevance: float
    ) -> dict[str, float]:
        components = {
            "confidence": max(0.0, min(1.0, float(getattr(row, "confidence", 0) or 0))),
            "decay": max(0.0, min(1.0, float(getattr(row, "decay_score", 0) or 0))),
            "evidence": _evidence_weight(int(getattr(row, "evidence_count", 0) or 0)),
            "recency": _recency_weight(getattr(row, "last_seen", None)),
            "relevance": relevance,
            "type_prior": TYPE_PRIOR.get(memory_type, 0.35),
        }
        final = (
            components["confidence"] * 0.30
            + components["decay"] * 0.15
            + components["evidence"] * 0.15
            + components["recency"] * 0.10
            + components["relevance"] * 0.25
            + components["type_prior"] * 0.05
        )
        components["final"] = round(final, 6)
        return components

    def _would_exceed_render_budget(
        self,
        group_id: str,
        rows: list[Any],
        max_chars: int,
        max_tokens: int = 0,
    ) -> bool:
        from app.group_memory.renderer import render_group_memory_context

        if int(max_chars or 0) <= 0 and int(max_tokens or 0) <= 0:
            return False
        rendered = render_group_memory_context(group_id, rows)
        return (
            (int(max_chars or 0) > 0 and len(rendered) > int(max_chars))
            or (
                int(max_tokens or 0) > 0
                and estimate_tokens(rendered) > int(max_tokens)
            )
        )

    def _skip_reason(self, row: Any) -> str:
        decision = evaluate_group_memory_prompt_injection(row)
        return "" if decision.eligible else decision.reason_code


@dataclass(frozen=True, slots=True)
class _GroupMemoryCandidateSource:
    service: GroupMemoryRetrievalService

    def recall(self, request: RetrievalRequest) -> _GroupMemoryRecall:
        from app.group_memory.query_service import (
            GroupMemoryQueryService,
        )

        group_id = str(request.options.get("group_id") or "")
        identity = resolve_group_memory_identity(
            str(request.options.get("chat_stream_id") or ""),
            platform=str(request.options.get("platform") or "qq"),
        )
        result = GroupMemoryQueryService(
            self.service.repository
        ).list_memories(
            identity.chat_stream_id,
            platform=identity.platform,
            limit=max(1, self.service.max_sql_candidates),
        )
        return _GroupMemoryRecall(
            group_id=group_id,
            query_text=request.query,
            rows=result.memories,
        )


@dataclass(frozen=True, slots=True)
class _GroupMemoryFilterPolicy:
    service: GroupMemoryRetrievalService

    def filter_candidates(
        self,
        request: RetrievalRequest,
        citation,
    ) -> RetrievalCandidates[_GroupMemoryCandidate]:
        del request
        recall: _GroupMemoryRecall = citation.source
        candidates: list[_GroupMemoryCandidate] = []
        skipped: list[dict[str, Any]] = []
        score_components: dict[str, dict[str, Any]] = {}
        for row in recall.rows:
            memory_type = str(row.memory_type or "")
            relevance = _lexical_relevance(row.content, recall.query_text)
            components = self.service._score_components(
                row,
                memory_type,
                relevance,
            )
            reason = self.service._skip_reason(row)
            if reason:
                components["skip_reason"] = reason
                score_components[str(row.id)] = components
                skipped.append({"id": row.id, "reason": reason})
                continue
            candidates.append(
                _GroupMemoryCandidate(
                    row=row,
                    memory_type=memory_type,
                    relevance=relevance,
                    components=dict(components),
                )
            )
        return RetrievalCandidates(
            items=tuple(candidates),
            metadata={
                "all_rows": recall.rows,
                "skipped": tuple(skipped),
                "score_components": score_components,
            },
        )


@dataclass(frozen=True, slots=True)
class _GroupMemoryScoringPolicy:
    service: GroupMemoryRetrievalService

    def pre_rank(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[_GroupMemoryCandidate],
    ) -> RetrievalCandidates[_GroupMemoryCandidate]:
        del request
        return candidates

    def final_rank(
        self,
        request: RetrievalRequest,
        outcome: RerankOutcome[_GroupMemoryCandidate],
    ) -> RankingOutcome[_GroupMemoryCandidate]:
        del request
        skipped = [dict(item) for item in outcome.metadata.get("skipped", ())]
        score_components = {
            str(key): dict(value)
            for key, value in dict(outcome.metadata.get("score_components", {})).items()
        }
        reranker_degraded = bool(outcome.metadata.get("reranker_degraded"))
        ranked: list[_GroupMemoryCandidate] = []
        for candidate in outcome.items:
            components = dict(candidate.components)
            if self.service.reranker_provider is None or reranker_degraded:
                minimum = (
                    STRICT_MIN_RELEVANCE
                    if candidate.memory_type in STRICT_RELEVANCE_TYPES
                    else DEFAULT_MIN_RELEVANCE
                )
                if candidate.relevance < minimum and candidate.memory_type != "style":
                    components["skip_reason"] = "low_relevance"
                    score_components[str(candidate.row.id)] = components
                    skipped.append({"id": candidate.row.id, "reason": "low_relevance"})
                    continue
            projected = _GroupMemoryCandidate(
                row=candidate.row,
                memory_type=candidate.memory_type,
                relevance=candidate.relevance,
                components=components,
            )
            score_components[str(candidate.row.id)] = components
            ranked.append(projected)
        ranked.sort(
            key=lambda item: float(item.components.get("final") or 0.0),
            reverse=True,
        )
        return RankingOutcome(
            items=tuple(ranked),
            metadata={
                "skipped": tuple(skipped),
                "score_components": score_components,
            },
        )


@dataclass(frozen=True, slots=True)
class _GroupMemoryReranker:
    service: GroupMemoryRetrievalService

    def rerank(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[_GroupMemoryCandidate],
    ) -> RerankOutcome[_GroupMemoryCandidate]:
        skipped = [dict(item) for item in candidates.metadata.get("skipped", ())]
        score_components = {
            str(key): dict(value)
            for key, value in dict(
                candidates.metadata.get("score_components", {})
            ).items()
        }
        if self.service.reranker_provider is None:
            return RerankOutcome(
                items=candidates.items,
                reranker_items=(),
                metadata={
                    "skipped": tuple(skipped),
                    "score_components": score_components,
                },
            )

        all_rows = list(candidates.metadata.get("all_rows", ()))
        scores = self.service._reranker_scores(request.query, all_rows)
        if scores is None:
            # 运行时降级:保留候选,交由 final_rank 按词面相关度门控。
            return RerankOutcome(
                items=candidates.items,
                reranker_items=(),
                metadata={
                    "skipped": tuple(skipped),
                    "score_components": score_components,
                    "reranker_degraded": True,
                },
            )
        accepted: list[_GroupMemoryCandidate] = []
        for candidate in candidates.items:
            components = dict(candidate.components)
            reranker = scores.get(int(candidate.row.id), 0.0)
            components["reranker"] = reranker
            if reranker < self.service.min_reranker:
                components["skip_reason"] = "low_reranker"
                score_components[str(candidate.row.id)] = components
                skipped.append({"id": candidate.row.id, "reason": "low_reranker"})
                continue
            components["final"] = round(
                float(components["final"]) * 0.65 + reranker * 0.35,
                6,
            )
            score_components[str(candidate.row.id)] = components
            accepted.append(
                _GroupMemoryCandidate(
                    row=candidate.row,
                    memory_type=candidate.memory_type,
                    relevance=candidate.relevance,
                    components=components,
                )
            )
        return RerankOutcome(
            items=tuple(accepted),
            reranker_items=candidates.items,
            metadata={
                "skipped": tuple(skipped),
                "score_components": score_components,
            },
        )


@dataclass(frozen=True, slots=True)
class _GroupMemoryBudgetPolicy:
    service: GroupMemoryRetrievalService

    def limit_candidates(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[_GroupMemoryCandidate],
    ) -> RetrievalCandidates[_GroupMemoryCandidate]:
        del request
        return candidates

    def select_results(
        self,
        request: RetrievalRequest,
        ranking: RankingOutcome[_GroupMemoryCandidate],
    ) -> GroupMemorySelection:
        selection = GroupMemorySelection(
            skipped=[dict(item) for item in ranking.metadata.get("skipped", ())],
            score_components={
                str(key): dict(value)
                for key, value in dict(
                    ranking.metadata.get("score_components", {})
                ).items()
            },
        )
        type_counts: dict[str, int] = {}
        group_id = str(request.options.get("group_id") or "")
        max_chars = int(request.options.get("max_chars") or 0)
        max_tokens = int(request.options.get("max_tokens") or 0)
        for candidate in ranking.items:
            row = candidate.row
            reason = ""
            if len(selection.selected) >= request.limit:
                reason = "max_items"
            elif type_counts.get(candidate.memory_type, 0) >= TYPE_LIMITS.get(
                candidate.memory_type,
                2,
            ):
                reason = "type_limit"
            elif self.service._would_exceed_render_budget(
                group_id,
                selection.selected + [row],
                max_chars,
                max_tokens,
            ):
                reason = "over_budget"
            if reason:
                components = selection.score_components[str(row.id)]
                components["skip_reason"] = reason
                selection.skipped.append({"id": row.id, "reason": reason})
                continue
            selection.selected.append(row)
            type_counts[candidate.memory_type] = (
                type_counts.get(candidate.memory_type, 0) + 1
            )
        return selection


@dataclass(frozen=True, slots=True)
class _GroupMemoryResultBuilder:
    def build(
        self,
        state: RetrievalPipelineState[
            _GroupMemoryRecall,
            _GroupMemoryCandidate,
            GroupMemorySelection,
            GroupMemorySelection,
        ],
    ) -> GroupMemorySelection:
        return state.selected
