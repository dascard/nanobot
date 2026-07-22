"""无领域假设的默认 Policy。"""

from __future__ import annotations

from core.retrieval.contracts import (
    CitationEvaluation,
    RetrievalRequest,
    SourceT,
)


class AllowAllCitationPolicy:
    """用于没有引用概念的检索域，显式记录该阶段为不适用。"""

    def evaluate(
        self,
        request: RetrievalRequest,
        source: SourceT,
    ) -> CitationEvaluation[SourceT]:
        return CitationEvaluation(
            source=source,
            accepted_ids=None,
            metadata={"citation_policy": "not_applicable"},
        )


class NullDebugTraceSink:
    def start(self, request, source, citation):
        return None

    def candidates_ready(self, trace, candidates) -> None:
        return None

    def rerank_complete(self, trace, outcome) -> None:
        return None

    def ranking_complete(self, trace, ranking) -> None:
        return None

    def finish(self, trace, selected) -> None:
        return None
