"""周期趋势只读调参分析工具。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_TRENDS = Path("evals/reports/artifact_trends_latest.json")
DEFAULT_TIMING_AUDIT = Path("evals/reports/timing_signal_audit_latest.json")
DEFAULT_MANIFEST = Path("evals/reports/periodic_manifest_latest.json")
DEFAULT_OUT = Path("evals/reports/tuning_analysis_latest.json")


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _reason(code: str, message: str, **extra: Any) -> dict[str, Any]:
    item = {"code": code, "message": message}
    item.update(extra)
    return item


def _recommendation(
    type_: str,
    area: str,
    severity: str,
    reason_code: str,
    message: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": type_,
        "area": area,
        "severity": severity,
        "reason_code": reason_code,
        "message": message,
        "evidence": evidence or {},
    }


def _trend_source(trends: dict[str, Any]) -> dict[str, Any]:
    source = trends.get("source") if isinstance(trends.get("source"), dict) else {}
    summary = trends.get("summary") if isinstance(trends.get("summary"), dict) else {}
    return {
        "trend_version": trends.get("trend_version"),
        "run_count": _as_int(source.get("run_count")),
        "latest_run_id": summary.get("latest_run_id"),
        "previous_run_id": summary.get("previous_run_id"),
    }


def _audit_source(timing_audit: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(timing_audit, dict):
        return {"timing_audit_mode": "", "timing_audit_reason": ""}
    source = (
        timing_audit.get("source")
        if isinstance(timing_audit.get("source"), dict)
        else {}
    )
    return {
        "timing_audit_mode": str(source.get("mode") or ""),
        "timing_audit_reason": str(source.get("reason") or ""),
    }


def _summarize(recommendations: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "recommendation_count": len(recommendations),
        "must_review_count": sum(
            1 for item in recommendations if item.get("type") == "manual_review"
        ),
        "no_change_count": sum(
            1 for item in recommendations if item.get("type") == "no_change"
        ),
        "label_more_samples_count": sum(
            1 for item in recommendations
            if item.get("type") == "label_more_samples"
        ),
        "collect_more_artifact_count": sum(
            1 for item in recommendations
            if item.get("type") == "collect_more_artifact"
        ),
    }


def build_tuning_analysis(
    trends: dict[str, Any],
    *,
    timing_audit: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    source_paths: dict[str, str] | None = None,
    min_runs: int = 3,
    min_total_samples: int = 20,
    min_label_coverage: float = 0.30,
    min_signal_labeled_samples: int = 5,
    high_false_positive_rate: float = 0.20,
) -> dict[str, Any]:
    _ = manifest, min_total_samples, min_label_coverage
    _ = min_signal_labeled_samples, high_false_positive_rate
    source_paths = source_paths or {}
    source = {
        **_trend_source(trends),
        **_audit_source(timing_audit),
        "trends_path": source_paths.get("trends", ""),
        "timing_audit_path": source_paths.get("timing_audit", ""),
        "manifest_path": source_paths.get("manifest", ""),
    }
    blocking: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []

    if trends.get("trend_version") != 1:
        blocking.append(
            _reason("unsupported_trend_version", "只支持 trend_version=1 的趋势报告")
        )
        recommendations.append(
            _recommendation(
                "collect_more_artifact",
                "artifact_health",
                "high",
                "unsupported_trend_version",
                "趋势报告版本不受支持，需要重新生成 artifact trends",
                {"trend_version": trends.get("trend_version")},
            )
        )

    run_count = _as_int(source.get("run_count"))
    if run_count < min_runs:
        blocking.append(
            _reason(
                "insufficient_runs",
                f"至少需要 {min_runs} 个周期 run 才生成调参候选建议",
            )
        )
        recommendations.append(
            _recommendation(
                "collect_more_artifact",
                "artifact_health",
                "medium",
                "insufficient_runs",
                f"当前只有 {run_count} 个周期 run，先积累更多周期趋势",
                {"run_count": run_count, "min_runs": min_runs},
            )
        )

    if not isinstance(timing_audit, dict):
        blocking.append(_reason("timing_audit_missing", "缺少 TimingSignal audit 报告"))
        recommendations.append(
            _recommendation(
                "collect_more_artifact",
                "timing_signal",
                "medium",
                "timing_audit_missing",
                "缺少 TimingSignal audit 报告，不能分析信号假阳率",
            )
        )
    else:
        audit_source = (
            timing_audit.get("source")
            if isinstance(timing_audit.get("source"), dict)
            else {}
        )
        if audit_source.get("mode") == "skipped":
            blocking.append(
                _reason(
                    "timing_audit_skipped",
                    "TimingSignal audit 被跳过",
                    reason=audit_source.get("reason"),
                )
            )
            recommendations.append(
                _recommendation(
                    "collect_more_artifact",
                    "timing_signal",
                    "medium",
                    "timing_audit_skipped",
                    "本轮 TimingSignal audit 被跳过，需要可审计的真实样本报告",
                    {"reason": audit_source.get("reason")},
                )
            )
        total_samples = _as_int(timing_audit.get("total_samples"))
        if total_samples <= 0:
            blocking.append(_reason("timing_zero_samples", "TimingSignal audit 样本数为 0"))
            recommendations.append(
                _recommendation(
                    "collect_more_artifact",
                    "timing_signal",
                    "medium",
                    "timing_zero_samples",
                    "TimingSignal audit 没有样本，不能推断信号质量",
                    {"total_samples": total_samples},
                )
            )

    return {
        "analysis_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "readiness": {"ready": not blocking, "blocking_reasons": blocking},
        "summary": _summarize(recommendations),
        "signals": [],
        "recommendations": recommendations,
        "regression_refs": [],
    }
