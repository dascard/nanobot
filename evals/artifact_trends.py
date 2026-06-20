"""跨周期 artifact 趋势聚合工具。"""
from __future__ import annotations

from datetime import datetime
from typing import Any


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


def _delta(current: float | int, previous: float | int | None) -> float | int | None:
    if previous is None:
        return None
    return round(current - previous, 10)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _duration_sec(started_at: Any, finished_at: Any) -> float | None:
    started = _parse_datetime(started_at)
    finished = _parse_datetime(finished_at)
    if started is None or finished is None:
        return None
    return (finished - started).total_seconds()


def _steps(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    steps = manifest.get("steps")
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict)]


def _step_failed(step: dict[str, Any]) -> bool:
    return str(step.get("status") or "") == "failed" or _as_int(step.get("exit_code")) != 0


def _new_failed_cases(step: dict[str, Any]) -> list[Any]:
    cases = step.get("new_failed_cases")
    return cases if isinstance(cases, list) else []


def _failed_cases(step: dict[str, Any]) -> list[Any]:
    cases = step.get("failed_cases")
    return cases if isinstance(cases, list) else []


def dedupe_manifests(manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, tuple[int, int, dict[str, Any]]] = {}
    for index, manifest in enumerate(manifests):
        if not isinstance(manifest, dict):
            continue
        if manifest.get("manifest_version") != 1:
            continue
        run_id = str(manifest.get("run_id") or "")
        if not run_id:
            continue
        step_count = len(_steps(manifest))
        existing = selected.get(run_id)
        if existing is None or step_count > existing[0] or (
            step_count == existing[0] and index > existing[1]
        ):
            selected[run_id] = (step_count, index, manifest)
    return sorted(
        (item[2] for item in selected.values()),
        key=lambda manifest: str(manifest.get("started_at") or ""),
    )


def _run_item(manifest: dict[str, Any]) -> dict[str, Any]:
    steps = _steps(manifest)
    return {
        "run_id": str(manifest.get("run_id") or ""),
        "started_at": str(manifest.get("started_at") or ""),
        "finished_at": str(manifest.get("finished_at") or ""),
        "status": str(manifest.get("status") or ""),
        "exit_code": _as_int(manifest.get("exit_code")),
        "duration_sec": _duration_sec(
            manifest.get("started_at"),
            manifest.get("finished_at"),
        ),
        "failed_step_count": sum(1 for step in steps if _step_failed(step)),
        "git": manifest.get("git") if isinstance(manifest.get("git"), dict) else {},
        "trigger": str(manifest.get("trigger") or ""),
    }


def _base_step_item(manifest: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(manifest.get("run_id") or ""),
        "suite": str(step.get("suite") or ""),
        "status": str(step.get("status") or ""),
        "exit_code": _as_int(step.get("exit_code")),
        "gate_passed": step.get("gate_passed"),
        "report_missing": bool(step.get("report_missing")),
    }


def _eval_item(
    manifest: dict[str, Any],
    step: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = step.get("summary") if isinstance(step.get("summary"), dict) else {}
    failed = _as_int(summary.get("failed"))
    pass_rate = _as_float(summary.get("pass_rate"))
    previous_failed = None if previous is None else _as_int(previous.get("failed"))
    previous_pass_rate = None if previous is None else _as_float(previous.get("pass_rate"))
    item = {
        **_base_step_item(manifest, step),
        "total": _as_int(summary.get("total")),
        "passed": _as_int(summary.get("passed")),
        "failed": failed,
        "pass_rate": pass_rate,
        "pass_rate_delta": _delta(pass_rate, previous_pass_rate),
        "failed_delta": _delta(failed, previous_failed),
        "new_failed_count": len(_new_failed_cases(step)),
        "failed_cases": _failed_cases(step),
    }
    return item


def _rag_item(
    manifest: dict[str, Any],
    step: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = step.get("summary") if isinstance(step.get("summary"), dict) else {}
    pass_rate = _as_float(summary.get("pass_rate"))
    hit_at_5 = _as_float(summary.get("hit@5"))
    mrr = _as_float(summary.get("mrr"))
    previous_pass_rate = None if previous is None else _as_float(previous.get("pass_rate"))
    previous_hit_at_5 = None if previous is None else _as_float(previous.get("hit@5"))
    previous_mrr = None if previous is None else _as_float(previous.get("mrr"))
    return {
        **_base_step_item(manifest, step),
        "total_cases": _as_int(summary.get("total_cases")),
        "positive_cases": _as_int(summary.get("positive_cases")),
        "pass_rate": pass_rate,
        "pass_rate_delta": _delta(pass_rate, previous_pass_rate),
        "hit@5": hit_at_5,
        "hit@5_delta": _delta(hit_at_5, previous_hit_at_5),
        "mrr": mrr,
        "mrr_delta": _delta(mrr, previous_mrr),
    }


def _timing_item(
    manifest: dict[str, Any],
    step: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = step.get("summary") if isinstance(step.get("summary"), dict) else {}
    total_samples = _as_int(summary.get("total_samples"))
    labeled_samples = _as_int(summary.get("labeled_samples"))
    mismatch_count = _as_int(summary.get("action_mismatch_count"))
    mismatch_rate = _as_float(summary.get("action_mismatch_rate"))
    previous_count = (
        None if previous is None else _as_int(previous.get("action_mismatch_count"))
    )
    previous_rate = (
        None if previous is None else _as_float(previous.get("action_mismatch_rate"))
    )
    notes = step.get("notes") if isinstance(step.get("notes"), dict) else {}
    return {
        **_base_step_item(manifest, step),
        "total_samples": total_samples,
        "labeled_samples": labeled_samples,
        "label_coverage_rate": round(labeled_samples / total_samples, 10)
        if total_samples
        else 0.0,
        "action_mismatch_count": mismatch_count,
        "action_mismatch_count_delta": _delta(mismatch_count, previous_count),
        "action_mismatch_rate": mismatch_rate,
        "action_mismatch_rate_delta": _delta(mismatch_rate, previous_rate),
        "notes": notes,
    }


def _regression(type_: str, item: dict[str, Any], **extra: Any) -> dict[str, Any]:
    payload = {
        "type": type_,
        "run_id": str(item.get("run_id") or ""),
    }
    suite = str(item.get("suite") or "")
    if suite:
        payload["suite"] = suite
    payload.update(extra)
    return payload


def _build_regressions(
    *,
    runs: list[dict[str, Any]],
    eval_suites: dict[str, list[dict[str, Any]]],
    rag_benchmark: list[dict[str, Any]],
    timing_signal_audit: list[dict[str, Any]],
    latest_manifest: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    regressions: list[dict[str, Any]] = []
    if not runs or latest_manifest is None:
        return regressions
    latest_run = runs[-1]
    if latest_run.get("status") != "passed":
        regressions.append(_regression("run_failed", latest_run))

    for step in _steps(latest_manifest):
        base = {
            "run_id": str(latest_manifest.get("run_id") or ""),
            "suite": str(step.get("suite") or ""),
        }
        if step.get("gate_passed") is False:
            regressions.append(_regression("gate_failed", base, kind=step.get("kind")))
        if step.get("report_missing"):
            regressions.append(_regression("report_missing", base, kind=step.get("kind")))

    for suite, items in eval_suites.items():
        if not items or items[-1].get("run_id") != latest_run.get("run_id"):
            continue
        item = items[-1]
        pass_rate_delta = item.get("pass_rate_delta")
        failed_delta = item.get("failed_delta")
        if pass_rate_delta is not None and pass_rate_delta < 0:
            regressions.append(
                _regression("eval_pass_rate_drop", item, delta=pass_rate_delta)
            )
        if failed_delta is not None and failed_delta > 0:
            regressions.append(
                _regression("eval_failed_count_increase", item, delta=failed_delta)
            )
        if item.get("new_failed_count", 0) > 0:
            regressions.append(
                _regression(
                    "eval_new_failures",
                    item,
                    count=item.get("new_failed_count"),
                    suite=suite,
                )
            )

    if rag_benchmark and rag_benchmark[-1].get("run_id") == latest_run.get("run_id"):
        item = rag_benchmark[-1]
        for key, type_ in (
            ("pass_rate_delta", "rag_pass_rate_drop"),
            ("hit@5_delta", "rag_hit_at_5_drop"),
            ("mrr_delta", "rag_mrr_drop"),
        ):
            delta = item.get(key)
            if delta is not None and delta < 0:
                regressions.append(_regression(type_, item, delta=delta))

    if (
        timing_signal_audit
        and timing_signal_audit[-1].get("run_id") == latest_run.get("run_id")
    ):
        item = timing_signal_audit[-1]
        count_delta = item.get("action_mismatch_count_delta")
        rate_delta = item.get("action_mismatch_rate_delta")
        if count_delta is not None and count_delta > 0:
            regressions.append(
                _regression(
                    "timing_action_mismatch_count_increase",
                    item,
                    delta=count_delta,
                )
            )
        if rate_delta is not None and rate_delta > 0:
            regressions.append(
                _regression(
                    "timing_action_mismatch_rate_increase",
                    item,
                    delta=rate_delta,
                )
            )
    return regressions


def build_artifact_trends(
    manifests: list[dict[str, Any]],
    manifest_globs: list[str] | None = None,
) -> dict[str, Any]:
    deduped = dedupe_manifests(manifests)
    runs = [_run_item(manifest) for manifest in deduped]
    eval_suites: dict[str, list[dict[str, Any]]] = {}
    rag_benchmark: list[dict[str, Any]] = []
    timing_signal_audit: list[dict[str, Any]] = []
    previous_eval: dict[str, dict[str, Any]] = {}
    previous_rag: dict[str, Any] | None = None
    previous_timing: dict[str, Any] | None = None

    for manifest in deduped:
        for step in _steps(manifest):
            kind = str(step.get("kind") or "")
            if kind == "eval_suite":
                suite = str(step.get("suite") or "")
                item = _eval_item(manifest, step, previous_eval.get(suite))
                eval_suites.setdefault(suite, []).append(item)
                previous_eval[suite] = item
            elif kind == "rag_benchmark":
                item = _rag_item(manifest, step, previous_rag)
                rag_benchmark.append(item)
                previous_rag = item
            elif kind == "timing_signal_audit":
                item = _timing_item(manifest, step, previous_timing)
                timing_signal_audit.append(item)
                previous_timing = item

    latest_run = runs[-1] if runs else None
    previous_run = runs[-2] if len(runs) >= 2 else None
    latest_manifest = deduped[-1] if deduped else None
    regressions = _build_regressions(
        runs=runs,
        eval_suites=eval_suites,
        rag_benchmark=rag_benchmark,
        timing_signal_audit=timing_signal_audit,
        latest_manifest=latest_manifest,
    )

    return {
        "trend_version": 1,
        "source": {
            "manifest_count": len(manifests),
            "run_count": len(deduped),
            "manifest_globs": list(manifest_globs or []),
            "deduped_run_ids": [str(manifest.get("run_id") or "") for manifest in deduped],
        },
        "summary": {
            "latest_run_id": latest_run.get("run_id") if latest_run else None,
            "previous_run_id": previous_run.get("run_id") if previous_run else None,
            "latest_status": latest_run.get("status") if latest_run else None,
            "failed_run_count": sum(1 for run in runs if run.get("status") != "passed"),
            "latest_failed_step_count": latest_run.get("failed_step_count")
            if latest_run
            else 0,
        },
        "series": {
            "runs": runs,
            "eval_suites": eval_suites,
            "rag_benchmark": rag_benchmark,
            "timing_signal_audit": timing_signal_audit,
        },
        "regressions": regressions,
    }
