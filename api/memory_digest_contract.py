"""MemoryDigest 运行报告的 API 安全投影。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_COUNT_KEYS = ("created", "skipped", "no_input", "failed", "in_progress")
_REPORT_STATUSES = {"ok", "partial", "failed", "no_input"}
_RESULT_STATUSES = {"created", "skipped", "failed", "in_progress"}
_ERROR_TYPES = {
    "",
    "build_failed",
    "claim_failed",
    "generator_not_llm",
    "input_invalid",
    "internal_error",
    "job_settlement_failed",
    "lease_expired_exhausted",
    "lease_lost",
    "model_error",
    "output_invalid",
    "quality_rejected",
    "retry_job_not_found",
    "retry_status_conflict",
    "session_processing_failed",
    "source_changed",
    "template_invalid",
    "write_failed",
}


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def safe_memory_digest_report(report: object) -> dict[str, Any]:
    """只公开稳定字段，避免内部诊断、Prompt 或正文被路由透传。"""

    source = report if isinstance(report, Mapping) else {}
    raw_counts = source.get("counts")
    counts_source = raw_counts if isinstance(raw_counts, Mapping) else {}
    counts = {
        key: _non_negative_int(counts_source.get(key))
        for key in _COUNT_KEYS
    }

    results: list[dict[str, Any]] = []
    raw_results = source.get("results")
    if isinstance(raw_results, list):
        for raw_item in raw_results:
            if not isinstance(raw_item, Mapping):
                continue
            status = str(raw_item.get("status") or "failed")
            if status not in _RESULT_STATUSES:
                status = "failed"
            raw_job_id = raw_item.get("job_id")
            job_id = (
                _non_negative_int(raw_job_id)
                if raw_job_id is not None
                else None
            )
            error_type = str(raw_item.get("error_type") or "")[:64]
            if error_type not in _ERROR_TYPES:
                error_type = "internal_error"
            results.append({
                "session_id": str(raw_item.get("session_id") or "")[:256],
                "status": status,
                "job_id": job_id,
                "retryable": bool(raw_item.get("retryable", False)),
                "error_type": error_type,
            })

    status = str(source.get("status") or "failed")
    if status not in _REPORT_STATUSES:
        status = "failed"
    return {
        "status": status,
        "target_date": str(source.get("target_date") or "")[:10],
        "created_sessions": _non_negative_int(source.get("created_sessions")),
        "counts": counts,
        "results": results,
    }
