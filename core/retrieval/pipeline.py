"""只负责编排顺序的 RetrievalPipeline。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Generic

from core.retrieval.contracts import (
    BudgetPolicy,
    CandidateSource,
    CandidateT,
    CitationPolicy,
    DebugTraceSink,
    FilterPolicy,
    ResultT,
    RetrievalPipelineState,
    RetrievalRequest,
    RetrievalResultBuilder,
    RerankerPort,
    ScoringPolicy,
    SelectedT,
    SourceT,
)


@dataclass(frozen=True, slots=True)
class RetrievalPipeline(Generic[SourceT, CandidateT, SelectedT, ResultT]):
    candidate_source: CandidateSource[SourceT]
    citation_policy: CitationPolicy[SourceT]
    filter_policy: FilterPolicy[SourceT, CandidateT]
    scoring_policy: ScoringPolicy[CandidateT]
    reranker: RerankerPort[CandidateT]
    budget_policy: BudgetPolicy[CandidateT, SelectedT]
    debug_trace_sink: DebugTraceSink[SourceT, CandidateT, SelectedT]
    result_builder: RetrievalResultBuilder[SourceT, CandidateT, SelectedT, ResultT]
    provider_id: str = "retrieval"

    def execute(self, request: RetrievalRequest) -> ResultT:
        from core.runtime.event_bus import emit_runtime_event

        encoded_query = request.query.encode("utf-8", errors="replace")
        started = time.perf_counter()
        event_attributes = {
            "provider": self.provider_id,
            "query_bytes": len(encoded_query),
            "query_sha256": hashlib.sha256(encoded_query).hexdigest(),
        }
        emit_runtime_event(
            "memory.retrieve",
            "started",
            attributes=event_attributes,
        )
        try:
            source = self.candidate_source.recall(request)
            citation = self.citation_policy.evaluate(request, source)
            debug_trace = self.debug_trace_sink.start(request, source, citation)

            candidates = self.filter_policy.filter_candidates(request, citation)
            candidates = self.scoring_policy.pre_rank(request, candidates)
            candidates = self.budget_policy.limit_candidates(request, candidates)
            self.debug_trace_sink.candidates_ready(debug_trace, candidates)

            rerank_outcome = self.reranker.rerank(request, candidates)
            self.debug_trace_sink.rerank_complete(debug_trace, rerank_outcome)

            ranking = self.scoring_policy.final_rank(request, rerank_outcome)
            self.debug_trace_sink.ranking_complete(debug_trace, ranking)

            selected = self.budget_policy.select_results(request, ranking)
            self.debug_trace_sink.finish(debug_trace, selected)

            state: RetrievalPipelineState[SourceT, CandidateT, SelectedT, ResultT] = (
                RetrievalPipelineState(
                    request=request,
                    source=source,
                    citation=citation,
                    candidates=candidates,
                    rerank_outcome=rerank_outcome,
                    ranking=ranking,
                    selected=selected,
                    debug_trace=debug_trace,
                )
            )
            result = self.result_builder.build(state)
        except BaseException as exc:
            emit_runtime_event(
                "memory.retrieve",
                "failed",
                attributes={
                    **event_attributes,
                    "latency_ms": (time.perf_counter() - started) * 1000,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        try:
            selected_count = len(selected)  # type: ignore[arg-type]
        except TypeError:
            selected_count = 0
        emit_runtime_event(
            "memory.retrieve",
            "succeeded",
            attributes={
                **event_attributes,
                "selected_count": selected_count,
                "latency_ms": (time.perf_counter() - started) * 1000,
            },
        )
        return result
