"""RAG benchmark 评分与聚合。"""

from __future__ import annotations

from statistics import mean
from typing import Iterable

from evals.rag_benchmark.schema import BenchmarkCase, BenchmarkResult, CaseScore


HIT_KS = (1, 3, 5)
MEMORY_SOURCE_TYPES = {"memory_digest", "session_summary"}


def _allowed_source_types(case: BenchmarkCase) -> set[str]:
    expected = case.expected.expected_source_type.strip()
    if expected:
        return {expected}
    if case.source_type == "memory":
        return MEMORY_SOURCE_TYPES
    return {case.source_type}


def _first_rank(result: BenchmarkResult, expected_ids: set[str]) -> int | None:
    for index, candidate_id in enumerate(result.candidate_ids, start=1):
        if candidate_id in expected_ids:
            return index
    return None


def _check_boolean_constraint(
    *,
    required: bool,
    values: list[bool | None],
    allow_empty: bool,
) -> bool | None:
    if not required:
        return None
    if not values:
        return True if allow_empty else False
    return all(value is True for value in values)


def score_case(case: BenchmarkCase, result: BenchmarkResult) -> CaseScore:
    expected_ids = set(case.expected.candidate_ids)
    forbidden = set(case.expected.forbidden_candidate_ids)
    rank = _first_rank(result, expected_ids)
    hit_at = {str(k): bool(rank is not None and rank <= k) for k in HIT_KS}
    forbidden_hits = [candidate_id for candidate_id in result.candidate_ids if candidate_id in forbidden]
    allowed_sources = _allowed_source_types(case)
    unexpected_source = any(candidate.source_type not in allowed_sources for candidate in result.candidates)
    allow_empty = bool(case.expected.allow_empty)
    errors: list[str] = []

    if not result.candidate_ids and not allow_empty and case.case_type != "negative":
        errors.append("empty result is not allowed")
    if case.case_type == "positive" and rank is None:
        errors.append("expected candidate not found")
    if forbidden_hits:
        errors.append(f"forbidden candidates returned: {forbidden_hits}")
    if unexpected_source:
        errors.append("unexpected source type returned")
    if case.expected.max_reranker_candidates is not None and (
        result.reranker_candidates_count > int(case.expected.max_reranker_candidates)
    ):
        errors.append("reranker candidate limit exceeded")
    if case.expected.max_merged_candidates is not None and (
        result.merged_candidates_count > int(case.expected.max_merged_candidates)
    ):
        errors.append("merged candidate limit exceeded")
    if case.expected.max_latency_ms is not None and result.latency_ms > int(case.expected.max_latency_ms):
        errors.append("latency limit exceeded")

    citation = _check_boolean_constraint(
        required=case.expected.requires_citation,
        values=[candidate.citation for candidate in result.candidates],
        allow_empty=allow_empty,
    )
    sendable = _check_boolean_constraint(
        required=case.expected.requires_sendable,
        values=[candidate.sendable for candidate in result.candidates],
        allow_empty=allow_empty,
    )
    if case.expected.requires_group_id:
        wanted_group = str(case.filters.get("group_id") or "")
        group_filter = _check_boolean_constraint(
            required=True,
            values=[str(candidate.group_id or "") == wanted_group for candidate in result.candidates],
            allow_empty=allow_empty,
        )
    else:
        group_filter = None
    checks = {"citation": citation, "sendable": sendable, "group_filter": group_filter}
    for name, passed in checks.items():
        if passed is False:
            errors.append(f"{name} check failed")

    return CaseScore(
        case_id=case.id,
        source_type=case.source_type,
        case_type=case.case_type,
        ok=not errors,
        rank=rank,
        hit_at=hit_at,
        mrr=(1.0 / rank) if rank else 0.0,
        forbidden_hits=forbidden_hits,
        unexpected_source=unexpected_source,
        checks=checks,
        errors=errors,
        latency_ms=result.latency_ms,
        reranker_candidates=result.reranker_candidates_count,
        degraded=result.degraded,
        reranker_latency_ms=result.reranker_latency_ms,
    )


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return int(ordered[index])


def _metric_block(cases: list[BenchmarkCase], scores: list[CaseScore]) -> dict:
    total = len(scores)
    positives = [score for score in scores if score.case_type == "positive"]
    positive_count = len(positives)
    return {
        "total_cases": total,
        "positive_cases": positive_count,
        "passed_cases": sum(1 for score in scores if score.ok),
        "pass_rate": (sum(1 for score in scores if score.ok) / total) if total else 0.0,
        "hit@1": (sum(1 for score in positives if score.hit_at.get("1")) / positive_count) if positive_count else 0.0,
        "hit@3": (sum(1 for score in positives if score.hit_at.get("3")) / positive_count) if positive_count else 0.0,
        "hit@5": (sum(1 for score in positives if score.hit_at.get("5")) / positive_count) if positive_count else 0.0,
        "mrr": mean([score.mrr for score in positives]) if positives else 0.0,
        "case_false_positive_rate": (
            sum(1 for score in scores if score.forbidden_hits) / total
        ) if total else 0.0,
        "forbidden_hit_rate@5": (
            sum(len(score.forbidden_hits) for score in scores) / total
        ) if total else 0.0,
        "unexpected_source_rate": (
            sum(1 for score in scores if score.unexpected_source) / total
        ) if total else 0.0,
        "degraded_rate": (sum(1 for score in scores if score.degraded) / total) if total else 0.0,
        "avg_latency_ms": mean([score.latency_ms for score in scores]) if scores else 0.0,
        "p95_latency_ms": _percentile([score.latency_ms for score in scores], 0.95),
        "avg_reranker_candidates": mean([score.reranker_candidates for score in scores]) if scores else 0.0,
        "max_reranker_candidates": max([score.reranker_candidates for score in scores], default=0),
        "avg_reranker_latency_ms": mean([
            score.reranker_latency_ms for score in scores if score.reranker_latency_ms is not None
        ]) if any(score.reranker_latency_ms is not None for score in scores) else None,
        "missing_reranker_latency_rate": (
            sum(1 for score in scores if score.reranker_latency_ms is None) / total
        ) if total else 0.0,
    }


def _origin_group(case: BenchmarkCase) -> str:
    origin = str(case.meta.get("origin") or "manual_hard")
    if origin == "generated_exact":
        return "overall_exact"
    if origin == "generated_weak":
        return "overall_weak"
    return "overall_manual"


def aggregate_scores(cases: Iterable[BenchmarkCase], scores: Iterable[CaseScore]) -> dict:
    case_list = list(cases)
    score_list = list(scores)
    by_id = {score.case_id: score for score in score_list}
    report = {"overall": _metric_block(case_list, score_list)}
    for group in ("overall_exact", "overall_weak", "overall_manual"):
        group_cases = [case for case in case_list if _origin_group(case) == group]
        group_scores = [by_id[case.id] for case in group_cases if case.id in by_id]
        report[group] = _metric_block(group_cases, group_scores)
    for source in sorted({case.source_type for case in case_list}):
        source_cases = [case for case in case_list if case.source_type == source]
        source_scores = [by_id[case.id] for case in source_cases if case.id in by_id]
        report[f"source:{source}"] = _metric_block(source_cases, source_scores)
    return report
