from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.retrieval import (
    CitationEvaluation,
    ManagedRerankerExecutor,
    RankingOutcome,
    RerankOutcome,
    RetrievalCandidates,
    RetrievalPipeline,
    RetrievalRequest,
)


@dataclass
class _Source:
    events: list[str]

    def recall(self, request: RetrievalRequest) -> tuple[int, ...]:
        self.events.append("source")
        return (1, 2, 3, 4)


@dataclass
class _Citation:
    events: list[str]

    def evaluate(
        self,
        request: RetrievalRequest,
        source: tuple[int, ...],
    ) -> CitationEvaluation[tuple[int, ...]]:
        self.events.append("citation")
        return CitationEvaluation(
            source=source,
            accepted_ids=frozenset({2, 3, 4}),
            metadata={"citation_checked": len(source)},
        )


@dataclass
class _Filter:
    events: list[str]

    def filter_candidates(
        self,
        request: RetrievalRequest,
        citation: CitationEvaluation[tuple[int, ...]],
    ) -> RetrievalCandidates[int]:
        self.events.append("filter")
        return RetrievalCandidates(
            items=tuple(
                item
                for item in citation.source
                if item in (citation.accepted_ids or frozenset())
            ),
            metadata={"filtered": 1},
        )


@dataclass
class _Scoring:
    events: list[str]

    def pre_rank(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[int],
    ) -> RetrievalCandidates[int]:
        self.events.append("score.pre")
        return candidates.with_items(tuple(sorted(candidates.items, reverse=True)))

    def final_rank(
        self,
        request: RetrievalRequest,
        outcome: RerankOutcome[int],
    ) -> RankingOutcome[int]:
        self.events.append("score.final")
        return RankingOutcome(
            items=tuple(sorted(outcome.items, reverse=True)),
            metadata={"gated": 0},
        )


@dataclass
class _Reranker:
    events: list[str]

    def rerank(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[int],
    ) -> RerankOutcome[int]:
        self.events.append("reranker")
        reranked = tuple(item + 100 for item in candidates.items)
        return RerankOutcome(
            items=reranked,
            reranker_items=candidates.items,
            metadata={"latency_ms": 1},
        )


@dataclass
class _Budget:
    events: list[str]

    def limit_candidates(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[int],
    ) -> RetrievalCandidates[int]:
        self.events.append("budget.candidates")
        return candidates.with_items(candidates.items[:2])

    def select_results(
        self,
        request: RetrievalRequest,
        ranking: RankingOutcome[int],
    ) -> tuple[int, ...]:
        self.events.append("budget.results")
        return ranking.items[:1]


@dataclass
class _Debug:
    events: list[str]

    def start(self, request, source, citation):
        self.events.append("debug.start")
        return {"events": []}

    def candidates_ready(self, trace, candidates):
        self.events.append("debug.candidates")
        trace["events"].append(tuple(candidates.items))

    def rerank_complete(self, trace, outcome):
        self.events.append("debug.rerank")
        trace["events"].append(tuple(outcome.items))

    def ranking_complete(self, trace, ranking):
        self.events.append("debug.ranking")
        trace["events"].append(tuple(ranking.items))

    def finish(self, trace, selected):
        self.events.append("debug.finish")
        trace["events"].append(tuple(selected))


class _Builder:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.states: list[object] = []

    def build(self, state):
        self.events.append("builder")
        self.states.append(state)
        return {
            "items": list(state.selected),
            "candidate_count": len(state.candidates.items),
            "debug": state.debug_trace,
        }


def _pipeline(events: list[str], builder: _Builder) -> RetrievalPipeline:
    return RetrievalPipeline(
        candidate_source=_Source(events),
        citation_policy=_Citation(events),
        filter_policy=_Filter(events),
        scoring_policy=_Scoring(events),
        reranker=_Reranker(events),
        budget_policy=_Budget(events),
        debug_trace_sink=_Debug(events),
        result_builder=builder,
    )


def test_pipeline_executes_explicit_stages_and_separate_budgets() -> None:
    events: list[str] = []
    builder = _Builder(events)

    result = _pipeline(events, builder).execute(
        RetrievalRequest(query="test", limit=1, include_debug=True)
    )

    assert result == {
        "items": [104],
        "candidate_count": 2,
        "debug": {"events": [(4, 3), (104, 103), (104, 103), (104,)]},
    }
    assert events == [
        "source",
        "citation",
        "debug.start",
        "filter",
        "score.pre",
        "budget.candidates",
        "debug.candidates",
        "reranker",
        "debug.rerank",
        "score.final",
        "debug.ranking",
        "budget.results",
        "debug.finish",
        "builder",
    ]


def test_pipeline_creates_request_local_state_and_does_not_swallow_errors() -> None:
    events: list[str] = []
    builder = _Builder(events)
    pipeline = _pipeline(events, builder)

    pipeline.execute(RetrievalRequest(query="first"))
    pipeline.execute(RetrievalRequest(query="second"))

    assert builder.states[0] is not builder.states[1]

    class BrokenSource:
        def recall(self, request):
            raise RuntimeError("recall failed")

    broken = RetrievalPipeline(
        candidate_source=BrokenSource(),
        citation_policy=_Citation(events),
        filter_policy=_Filter(events),
        scoring_policy=_Scoring(events),
        reranker=_Reranker(events),
        budget_policy=_Budget(events),
        debug_trace_sink=_Debug(events),
        result_builder=builder,
    )
    with pytest.raises(RuntimeError, match="recall failed"):
        broken.execute(RetrievalRequest(query="broken"))


def test_memory_and_knowledge_queries_consume_the_shared_pipeline(
    db_session,
    monkeypatch,
) -> None:
    from core.knowledge_rag import KnowledgeRagService
    from core.memory_rag import MemoryRagService
    from core.sticker_rag import StickerRagService
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService

    calls: list[str] = []
    original = RetrievalPipeline.execute

    def recording_execute(self, request):
        calls.append(request.options["domain"])
        return original(self, request)

    monkeypatch.setattr(RetrievalPipeline, "execute", recording_execute)

    memory_result = MemoryRagService(db_session).query("empty memory")
    knowledge_result = KnowledgeRagService(db_session).query("empty knowledge")
    sticker_result = StickerRagService(db_session).query("empty sticker")
    group_result = GroupMemoryRetrievalService(db_session).select(
        group_id="group_empty",
        current_user_input="empty group memory",
    )

    assert calls == ["memory", "knowledge", "sticker", "group_memory"]
    assert memory_result["items"] == []
    assert knowledge_result["items"] == []
    assert sticker_result == []
    assert group_result.selected == []


def test_managed_reranker_executor_requires_explicit_lifecycle() -> None:
    executor = ManagedRerankerExecutor(thread_name_prefix="retrieval-test")

    assert executor.started is False
    with pytest.raises(RuntimeError, match="尚未启动"):
        executor.submit(lambda: 1)

    executor.start()
    assert executor.started is True
    assert executor.submit(lambda left, right: left + right, 2, 3).result() == 5

    executor.stop()
    assert executor.started is False
