"""周期趋势只读调参分析工具。"""
from __future__ import annotations

import argparse
import json
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


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


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


def _label_coverage(labeled: int, total: int) -> float:
    return round(labeled / total, 6) if total else 0.0


def _samples_for_signal(
    samples: list[dict[str, Any]],
    signal_name: str,
) -> list[dict[str, Any]]:
    return [
        item for item in samples
        if str(item.get("signal_name") or "") == signal_name
    ]


def _evidence_samples(
    samples: list[dict[str, Any]],
    signal_name: str,
) -> list[dict[str, Any]]:
    selected = sorted(
        _samples_for_signal(samples, signal_name),
        key=lambda item: (
            not bool(item.get("action_mismatch")),
            str(item.get("label") or "") != "false_positive",
            _as_int(item.get("log_id")),
        ),
    )[:3]
    return [
        {
            "log_id": item.get("log_id"),
            "signal_value": _as_float(item.get("signal_value")),
            "runtime_action": str(item.get("runtime_action") or ""),
            "scoring_action": str(item.get("scoring_action") or ""),
            "action_mismatch": bool(item.get("action_mismatch")),
            "text_preview": str(item.get("text_preview") or ""),
        }
        for item in selected
    ]


def _signal_items(timing_audit: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(timing_audit, dict):
        return []
    signals = (
        timing_audit.get("signals")
        if isinstance(timing_audit.get("signals"), dict)
        else {}
    )
    shadow = (
        timing_audit.get("shadow")
        if isinstance(timing_audit.get("shadow"), dict)
        else {}
    )
    mismatches = (
        shadow.get("mismatches_by_signal")
        if isinstance(shadow.get("mismatches_by_signal"), dict)
        else {}
    )
    samples = (
        timing_audit.get("samples")
        if isinstance(timing_audit.get("samples"), list)
        else []
    )
    items: list[dict[str, Any]] = []
    for name, stats in sorted(signals.items()):
        if not isinstance(stats, dict):
            continue
        total = _as_int(stats.get("samples"))
        labeled = _as_int(stats.get("labeled_samples"))
        items.append({
            "name": str(name),
            "samples": total,
            "labeled_samples": labeled,
            "label_coverage_rate": _label_coverage(labeled, total),
            "false_positive_rate": _as_float(stats.get("false_positive_rate")),
            "suggestion": str(stats.get("suggestion") or ""),
            "runtime_actions": (
                stats.get("actions")
                if isinstance(stats.get("actions"), dict)
                else {}
            ),
            "mismatch_count": _as_int(mismatches.get(name)),
            "evidence_samples": _evidence_samples(samples, str(name)),
        })
    return items


def _series(trends: dict[str, Any]) -> dict[str, Any]:
    return trends.get("series") if isinstance(trends.get("series"), dict) else {}


def _latest_timing_item(trends: dict[str, Any]) -> dict[str, Any] | None:
    items = _series(trends).get("timing_signal_audit")
    if isinstance(items, list) and items and isinstance(items[-1], dict):
        return items[-1]
    return None


def _latest_rag_item(trends: dict[str, Any]) -> dict[str, Any] | None:
    items = _series(trends).get("rag_benchmark")
    if isinstance(items, list) and items and isinstance(items[-1], dict):
        return items[-1]
    return None


def _latest_eval_items(trends: dict[str, Any]) -> list[dict[str, Any]]:
    suites = _series(trends).get("eval_suites")
    if not isinstance(suites, dict):
        return []
    latest: list[dict[str, Any]] = []
    for suite_items in suites.values():
        if (
            isinstance(suite_items, list)
            and suite_items
            and isinstance(suite_items[-1], dict)
        ):
            latest.append(suite_items[-1])
    return latest


def _regression_refs(trends: dict[str, Any]) -> list[dict[str, Any]]:
    regressions = trends.get("regressions")
    if not isinstance(regressions, list):
        return []
    refs: list[dict[str, Any]] = []
    for item in regressions:
        if isinstance(item, dict):
            ref = dict(item)
            ref["source"] = "artifact_trends"
            refs.append(ref)
    return refs


def load_json_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def resolve_timing_audit_path(
    manifest: dict[str, Any] | None,
    explicit_path: str | Path | None,
) -> Path | None:
    if explicit_path:
        path = Path(explicit_path)
        if path.exists():
            return path
    if not isinstance(manifest, dict):
        return None
    steps = manifest.get("steps")
    if not isinstance(steps, list):
        return None
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("kind") or "") != "timing_signal_audit":
            continue
        paths = step.get("report_paths")
        if not isinstance(paths, list):
            continue
        for item in paths:
            path = Path(str(item))
            if path.exists() and path.suffix.lower() == ".json":
                return path
    return None


def write_tuning_analysis(payload: dict[str, Any], out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _empty_trends() -> dict[str, Any]:
    return {
        "trend_version": None,
        "source": {},
        "summary": {},
        "series": {},
        "regressions": [],
    }


def _load_optional(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    src = Path(path)
    if not src.exists():
        return None
    return load_json_object(src)


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
    _ = manifest
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
    signal_items = _signal_items(timing_audit)

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
        labeled_samples = _as_int(timing_audit.get("labeled_samples"))
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
        elif total_samples < min_total_samples:
            blocking.append(
                _reason(
                    "insufficient_timing_samples",
                    f"TimingSignal audit 样本数低于 {min_total_samples}",
                    total_samples=total_samples,
                    min_total_samples=min_total_samples,
                )
            )
            recommendations.append(
                _recommendation(
                    "collect_more_artifact",
                    "timing_signal",
                    "medium",
                    "insufficient_timing_samples",
                    "TimingSignal audit 样本不足，需要先积累更厚的不可变样本 artifact",
                    {
                        "total_samples": total_samples,
                        "min_total_samples": min_total_samples,
                    },
                )
            )
        elif _label_coverage(labeled_samples, total_samples) < min_label_coverage:
            coverage = _label_coverage(labeled_samples, total_samples)
            blocking.append(
                _reason(
                    "low_label_coverage",
                    f"TimingSignal 标注覆盖率低于 {min_label_coverage:.0%}",
                    label_coverage_rate=coverage,
                )
            )
            recommendations.append(
                _recommendation(
                    "label_more_samples",
                    "timing_signal",
                    "medium",
                    "low_label_coverage",
                    "TimingSignal 标注覆盖不足，需要先补标注再讨论调参",
                    {
                        "labeled_samples": labeled_samples,
                        "total_samples": total_samples,
                        "label_coverage_rate": coverage,
                        "min_label_coverage": min_label_coverage,
                    },
                )
            )

    for signal in signal_items:
        if signal["labeled_samples"] < min_signal_labeled_samples:
            recommendations.append(
                _recommendation(
                    "label_more_samples",
                    "timing_signal",
                    "low",
                    "low_signal_label_count",
                    f"{signal['name']} 标注样本不足，需要补充该信号样本",
                    {
                        "signal": signal["name"],
                        "labeled_samples": signal["labeled_samples"],
                        "min_signal_labeled_samples": min_signal_labeled_samples,
                    },
                )
            )
            continue
        if signal["false_positive_rate"] >= high_false_positive_rate:
            recommendations.append(
                _recommendation(
                    "manual_review",
                    "timing_signal",
                    "medium",
                    "high_false_positive_rate",
                    f"{signal['name']} 标注样本假阳率偏高，需要人工复核信号提取器",
                    {
                        "signal": signal["name"],
                        "false_positive_rate": signal["false_positive_rate"],
                        "labeled_samples": signal["labeled_samples"],
                        "sample_log_ids": [
                            item["log_id"]
                            for item in signal["evidence_samples"]
                            if item.get("log_id") is not None
                        ],
                    },
                )
            )

    timing_item = _latest_timing_item(trends)
    if timing_item and (
        _as_float(timing_item.get("action_mismatch_count_delta")) > 0
        or _as_float(timing_item.get("action_mismatch_rate_delta")) > 0
    ):
        recommendations.append(
            _recommendation(
                "manual_review",
                "timing_shadow",
                "medium",
                "timing_action_mismatch_increase",
                "TimingSignal runtime / scoring action mismatch 上升，需要复核时机决策链路",
                {
                    "run_id": timing_item.get("run_id"),
                    "action_mismatch_count_delta": timing_item.get(
                        "action_mismatch_count_delta"
                    ),
                    "action_mismatch_rate_delta": timing_item.get(
                        "action_mismatch_rate_delta"
                    ),
                },
            )
        )

    rag_item = _latest_rag_item(trends)
    if rag_item and any(
        _as_float(rag_item.get(key)) < 0
        for key in ("pass_rate_delta", "hit@5_delta", "mrr_delta")
    ):
        recommendations.append(
            _recommendation(
                "manual_review",
                "rag_benchmark",
                "medium",
                "rag_metric_drop",
                "RAG benchmark 指标下降，需要复核检索或样本变化",
                {
                    "run_id": rag_item.get("run_id"),
                    "pass_rate_delta": rag_item.get("pass_rate_delta"),
                    "hit@5_delta": rag_item.get("hit@5_delta"),
                    "mrr_delta": rag_item.get("mrr_delta"),
                },
            )
        )

    for item in _latest_eval_items(trends):
        if (
            _as_float(item.get("pass_rate_delta")) < 0
            or _as_float(item.get("failed_delta")) > 0
            or _as_int(item.get("new_failed_count")) > 0
        ):
            recommendations.append(
                _recommendation(
                    "manual_review",
                    "eval_suite",
                    "medium",
                    "eval_suite_regression",
                    f"{item.get('suite') or 'eval suite'} 指标退化，需要复核失败样本",
                    {
                        "run_id": item.get("run_id"),
                        "suite": item.get("suite"),
                        "pass_rate_delta": item.get("pass_rate_delta"),
                        "failed_delta": item.get("failed_delta"),
                        "new_failed_count": item.get("new_failed_count"),
                    },
                )
            )

    if not blocking and not recommendations:
        recommendations.append(
            _recommendation(
                "no_change",
                "artifact_health",
                "info",
                "stable_metrics",
                "周期趋势和 TimingSignal audit 未显示需要调参的退化信号",
                {"run_count": run_count},
            )
        )

    return {
        "analysis_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "readiness": {"ready": not blocking, "blocking_reasons": blocking},
        "summary": _summarize(recommendations),
        "signals": signal_items,
        "recommendations": recommendations,
        "regression_refs": _regression_refs(trends),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trends", default=str(DEFAULT_TRENDS))
    parser.add_argument("--timing-audit", default=str(DEFAULT_TIMING_AUDIT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    trends_path = Path(args.trends)
    trends = load_json_object(trends_path) if trends_path.exists() else _empty_trends()
    manifest_path = Path(args.manifest)
    manifest = _load_optional(manifest_path)
    timing_path = resolve_timing_audit_path(manifest, args.timing_audit)
    timing_audit = load_json_object(timing_path) if timing_path is not None else None
    payload = build_tuning_analysis(
        trends,
        timing_audit=timing_audit,
        manifest=manifest,
        source_paths={
            "trends": str(trends_path),
            "timing_audit": str(timing_path) if timing_path is not None else "",
            "manifest": str(manifest_path) if manifest_path.exists() else "",
        },
    )
    path = write_tuning_analysis(payload, args.out)
    print(f"tuning_analysis={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
