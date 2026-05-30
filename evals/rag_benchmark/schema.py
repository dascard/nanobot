"""RAG benchmark 的统一 case、结果与评分模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


CaseType = Literal["positive", "negative", "constraint_only"]


class BenchmarkExpected(BaseModel):
    candidate_ids: list[str] = Field(default_factory=list)
    hit_at: int = 5
    forbidden_candidate_ids: list[str] = Field(default_factory=list)
    expected_source_type: str = ""
    requires_citation: bool = False
    requires_sendable: bool = False
    requires_group_id: bool = False
    allow_empty: bool = False
    max_reranker_candidates: int | None = None
    max_merged_candidates: int | None = None
    max_latency_ms: int | None = None


class BenchmarkCase(BaseModel):
    id: str
    suite: str = "rag_benchmark"
    source_type: str
    case_type: CaseType = "positive"
    status: Literal["enabled", "disabled"] = "enabled"
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    expected: BenchmarkExpected = Field(default_factory=BenchmarkExpected)
    meta: dict[str, Any] = Field(default_factory=dict)


class BenchmarkCandidate(BaseModel):
    candidate_id: str
    source_type: str
    rank: int
    score: float | None = None
    title: str = ""
    text_preview: str = ""
    document_id: str | int | None = None
    chunk_id: str | None = None
    group_id: str = ""
    sendable: bool | None = None
    citation: bool | None = None
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkResult(BaseModel):
    case_id: str
    source_type: str
    candidate_ids: list[str] = Field(default_factory=list)
    candidates: list[BenchmarkCandidate] = Field(default_factory=list)
    merged_candidates_count: int = 0
    reranker_candidates_count: int = 0
    degraded: bool = False
    fallback_reason: str = ""
    latency_ms: int = 0
    reranker_latency_ms: int | None = None
    debug_summary: dict[str, Any] = Field(default_factory=dict)


class CaseScore(BaseModel):
    case_id: str
    source_type: str
    case_type: CaseType
    ok: bool
    rank: int | None = None
    hit_at: dict[str, bool] = Field(default_factory=dict)
    mrr: float = 0.0
    forbidden_hits: list[str] = Field(default_factory=list)
    unexpected_source: bool = False
    checks: dict[str, bool | None] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    reranker_candidates: int = 0
    degraded: bool = False
    reranker_latency_ms: int | None = None
