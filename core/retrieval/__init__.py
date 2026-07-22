"""可组合的检索阶段合同与同步 Pipeline。"""

from core.retrieval.contracts import (
    BudgetPolicy,
    CandidateSource,
    CitationEvaluation,
    CitationPolicy,
    DebugTraceSink,
    FilterPolicy,
    RankingOutcome,
    RerankOutcome,
    RetrievalCandidates,
    RetrievalPipelineState,
    RetrievalRequest,
    RetrievalResultBuilder,
    RerankerPort,
    ScoringPolicy,
)
from core.retrieval.defaults import AllowAllCitationPolicy, NullDebugTraceSink
from core.retrieval.pipeline import RetrievalPipeline
from core.retrieval.executor import (
    ManagedRerankerExecutor,
    RerankerExecutorPort,
    get_retrieval_reranker_executor,
    start_retrieval_runtime,
    stop_retrieval_runtime,
)

__all__ = [
    "AllowAllCitationPolicy",
    "BudgetPolicy",
    "CandidateSource",
    "CitationEvaluation",
    "CitationPolicy",
    "DebugTraceSink",
    "FilterPolicy",
    "NullDebugTraceSink",
    "RankingOutcome",
    "RerankOutcome",
    "RetrievalCandidates",
    "RetrievalPipeline",
    "RetrievalPipelineState",
    "ManagedRerankerExecutor",
    "RerankerExecutorPort",
    "get_retrieval_reranker_executor",
    "start_retrieval_runtime",
    "stop_retrieval_runtime",
    "RetrievalRequest",
    "RetrievalResultBuilder",
    "RerankerPort",
    "ScoringPolicy",
]
