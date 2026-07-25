#!/usr/bin/env python3
"""生成或校验语义 Task SLO Manifest。

输入只包含代码所有的 SLO Registry 和已聚合的性能基线。产物不会读取或写出
Prompt、模型正文、用户身份、会话、Trace 或原始请求日志。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path(
    "docs/architecture/task-slo-manifest.v1.json"
)


class TaskSloManifestError(RuntimeError):
    """SLO Manifest 的输入或输出不符合稳定合同。"""


def render_json(value: object) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _load_baseline(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TaskSloManifestError("无法读取语义 Task 基线") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("tasks"), dict)
    ):
        raise TaskSloManifestError("语义 Task 基线 Schema 无效")
    return payload


def _number(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return None
    return value


def _nested_number(
    value: object,
    *path: str,
) -> float | int | None:
    current = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return _number(current)


def _budget_check(
    metric: str,
    observed: float | int | None,
    budget: float | int | None,
) -> dict[str, object] | None:
    if observed is None or budget is None:
        return None
    return {
        "metric": metric,
        "observed": observed,
        "budget": budget,
        "passed": float(observed) <= float(budget),
    }


def _task_entry(
    descriptor,
    baseline_tasks: Mapping[str, object],
) -> dict[str, object]:
    from core.task_runtime.slo import (
        TaskBillingClass,
        TaskSloStatus,
    )

    baseline = baseline_tasks.get(descriptor.baseline_task_id)
    baseline = baseline if isinstance(baseline, Mapping) else {}
    calls_value = _number(baseline.get("calls"))
    calls = int(calls_value) if calls_value is not None else 0
    sample_sufficient = calls >= descriptor.min_sample_count

    checks = tuple(
        item
        for item in (
            _budget_check(
                "p50_latency_ms",
                _nested_number(baseline, "latency_ms", "p50"),
                descriptor.p50_latency_ms,
            ),
            _budget_check(
                "p95_latency_ms",
                _nested_number(baseline, "latency_ms", "p95"),
                descriptor.p95_latency_ms,
            ),
            _budget_check(
                "p99_latency_ms",
                _nested_number(baseline, "latency_ms", "p99"),
                descriptor.p99_latency_ms,
            ),
            _budget_check(
                "total_failure_rate",
                _nested_number(baseline, "failure_rate"),
                descriptor.max_total_failure_rate,
            ),
        )
        if item is not None
    )
    required_check_count = (
        4 if descriptor.status is TaskSloStatus.FROZEN else 0
    )
    baseline_evaluable = bool(
        sample_sufficient
        and required_check_count
        and len(checks) == required_check_count
    )
    baseline_pass = bool(
        baseline_evaluable
        and all(bool(item["passed"]) for item in checks)
    )
    token_coverage = int(
        _nested_number(baseline, "tokens", "coverage_calls") or 0
    )
    cost_coverage = int(
        _nested_number(baseline, "cost", "coverage_calls") or 0
    )
    observability_ready = bool(
        token_coverage > 0
        and (
            descriptor.billing_class is TaskBillingClass.LOCAL_FREE
            or cost_coverage > 0
        )
    )
    activation_ready = bool(
        descriptor.status is TaskSloStatus.FROZEN
        and baseline_pass
        and observability_ready
    )

    blockers: list[str] = []
    if descriptor.status is not TaskSloStatus.FROZEN:
        blockers.append("slo_not_frozen")
    if not sample_sufficient:
        blockers.append("sample_insufficient")
    if not baseline_evaluable:
        blockers.append("baseline_not_evaluable")
    elif not baseline_pass:
        blockers.append("baseline_budget_failed")
    if token_coverage <= 0:
        blockers.append("token_observability_missing")
    if (
        descriptor.billing_class is TaskBillingClass.PROVIDER_GATEWAY
        and cost_coverage <= 0
    ):
        blockers.append("cost_observability_missing")

    return {
        "task_id": descriptor.task_id,
        "slo_id": descriptor.slo_id,
        "slo_version": descriptor.version,
        "slo_status": descriptor.status.value,
        "descriptor": descriptor.metadata(),
        "baseline_task_id": descriptor.baseline_task_id,
        "baseline_calls": calls,
        "sample_sufficient": sample_sufficient,
        "budget_checks": list(checks),
        "baseline_evaluable": baseline_evaluable,
        "baseline_pass": baseline_pass,
        "token_coverage_calls": token_coverage,
        "cost_coverage_calls": cost_coverage,
        "observability_ready": observability_ready,
        "activation_ready": activation_ready,
        "blocking_reasons": blockers,
    }


def build_manifest(root: Path) -> dict[str, object]:
    root = root.resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from core.task_runtime.slo import TASK_SLO_REGISTRY

    descriptors = TASK_SLO_REGISTRY.descriptors()
    baseline_paths = {
        descriptor.baseline_artifact
        for descriptor in descriptors
    }
    if len(baseline_paths) != 1:
        raise TaskSloManifestError(
            "Task SLO Descriptor 未引用唯一基线 Artifact"
        )
    baseline_relative = Path(next(iter(baseline_paths)))
    baseline_path = root / baseline_relative
    baseline = _load_baseline(baseline_path)
    snapshot = TASK_SLO_REGISTRY.registry_snapshot

    return {
        "schema_version": 1,
        "registry": {
            "namespace": snapshot.namespace,
            "generation": snapshot.generation,
            "sha256": snapshot.sha256,
        },
        "baseline": {
            "path": baseline_relative.as_posix(),
            "sha256": hashlib.sha256(
                baseline_path.read_bytes()
            ).hexdigest(),
            "schema_version": baseline["schema_version"],
        },
        "tasks": [
            _task_entry(descriptor, baseline["tasks"])
            for descriptor in descriptors
        ],
    }


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成或校验语义 Task SLO Manifest"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    output = (
        args.output
        if args.output.is_absolute()
        else root / args.output
    )
    try:
        rendered = render_json(build_manifest(root))
        if args.write:
            _write_atomic(output, rendered)
            return 0
        try:
            current = output.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            print("Task SLO Manifest 缺失", file=sys.stderr)
            return 1
        if current != rendered:
            print("Task SLO Manifest 已漂移", file=sys.stderr)
            return 1
        return 0
    except TaskSloManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
