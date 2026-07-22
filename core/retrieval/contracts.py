"""检索流水线的框架无关类型化合同。"""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Generic, Protocol, TypeVar


SourceT = TypeVar("SourceT")
CandidateT = TypeVar("CandidateT")
SelectedT = TypeVar("SelectedT")
ResultT = TypeVar("ResultT")


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    query: str
    limit: int = 5
    include_debug: bool = False
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("retrieval limit 必须大于 0")
        object.__setattr__(self, "query", str(self.query or ""))
        object.__setattr__(self, "options", _freeze_mapping(self.options))


@dataclass(frozen=True, slots=True)
class CitationEvaluation(Generic[SourceT]):
    source: SourceT
    accepted_ids: frozenset[Hashable] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.accepted_ids is not None:
            object.__setattr__(self, "accepted_ids", frozenset(self.accepted_ids))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class RetrievalCandidates(Generic[CandidateT]):
    items: tuple[CandidateT, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def with_items(
        self,
        items: tuple[CandidateT, ...],
    ) -> "RetrievalCandidates[CandidateT]":
        return RetrievalCandidates(items=items, metadata=self.metadata)


@dataclass(frozen=True, slots=True)
class RerankOutcome(Generic[CandidateT]):
    """重排后的全候选与实际消耗重排预算的候选。"""

    items: tuple[CandidateT, ...]
    reranker_items: tuple[CandidateT, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "reranker_items", tuple(self.reranker_items))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class RankingOutcome(Generic[CandidateT]):
    items: tuple[CandidateT, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class RetrievalPipelineState(Generic[SourceT, CandidateT, SelectedT, ResultT]):
    """一次 execute 独占的状态快照，不在 Pipeline 实例间复用。"""

    request: RetrievalRequest
    source: SourceT
    citation: CitationEvaluation[SourceT]
    candidates: RetrievalCandidates[CandidateT]
    rerank_outcome: RerankOutcome[CandidateT]
    ranking: RankingOutcome[CandidateT]
    selected: SelectedT
    debug_trace: object | None


class CandidateSource(Protocol[SourceT]):
    def recall(self, request: RetrievalRequest) -> SourceT: ...


class CitationPolicy(Protocol[SourceT]):
    def evaluate(
        self,
        request: RetrievalRequest,
        source: SourceT,
    ) -> CitationEvaluation[SourceT]: ...


class FilterPolicy(Protocol[SourceT, CandidateT]):
    def filter_candidates(
        self,
        request: RetrievalRequest,
        citation: CitationEvaluation[SourceT],
    ) -> RetrievalCandidates[CandidateT]: ...


class ScoringPolicy(Protocol[CandidateT]):
    def pre_rank(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[CandidateT],
    ) -> RetrievalCandidates[CandidateT]: ...

    def final_rank(
        self,
        request: RetrievalRequest,
        outcome: RerankOutcome[CandidateT],
    ) -> RankingOutcome[CandidateT]: ...


class RerankerPort(Protocol[CandidateT]):
    def rerank(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[CandidateT],
    ) -> RerankOutcome[CandidateT]: ...


class BudgetPolicy(Protocol[CandidateT, SelectedT]):
    def limit_candidates(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[CandidateT],
    ) -> RetrievalCandidates[CandidateT]: ...

    def select_results(
        self,
        request: RetrievalRequest,
        ranking: RankingOutcome[CandidateT],
    ) -> SelectedT: ...


class DebugTraceSink(Protocol[SourceT, CandidateT, SelectedT]):
    def start(
        self,
        request: RetrievalRequest,
        source: SourceT,
        citation: CitationEvaluation[SourceT],
    ) -> object | None: ...

    def candidates_ready(
        self,
        trace: object | None,
        candidates: RetrievalCandidates[CandidateT],
    ) -> None: ...

    def rerank_complete(
        self,
        trace: object | None,
        outcome: RerankOutcome[CandidateT],
    ) -> None: ...

    def ranking_complete(
        self,
        trace: object | None,
        ranking: RankingOutcome[CandidateT],
    ) -> None: ...

    def finish(self, trace: object | None, selected: SelectedT) -> None: ...


class RetrievalResultBuilder(Protocol[SourceT, CandidateT, SelectedT, ResultT]):
    def build(
        self,
        state: RetrievalPipelineState[SourceT, CandidateT, SelectedT, ResultT],
    ) -> ResultT: ...
