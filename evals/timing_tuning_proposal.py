"""TimingGate 可审核调参提案报告。"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from core.eval_sampling.timing_signal_audit import FINAL_TIMING_ACTIONS


PROPOSAL_VERSION = 1

DEFAULT_MANIFEST = Path("evals/reports/periodic_manifest_latest.json")
DEFAULT_TRENDS = Path("evals/reports/artifact_trends_latest.json")
DEFAULT_ANALYSIS = Path("evals/reports/tuning_analysis_latest.json")
DEFAULT_TIMING_AUDIT = Path("evals/reports/timing_signal_audit_latest.json")
DEFAULT_PARAMS = Path("tmp/timing_gate/param_candidates.json")
DEFAULT_CASES_DIR = Path("evals/cases/timing_gate")
DEFAULT_BASELINE = Path("evals/baselines/timing_gate.json")
DEFAULT_OUT = Path("evals/reports/timing_tuning_proposal_latest.json")

ALLOWED_PARAM_NAMES = {
    "BASE_SCORE",
    "DIRECT_WEIGHT",
    "SUPPRESS_WEIGHT",
    "DECISION_MARGIN",
    "CONFLICT_THRESHOLD",
    "MODEL_WEIGHT_SCALE",
    "BOT_SOFT_REJECT_GAMMA",
    "s_ack",
    "s_transport",
    "s_other",
    "s_bot",
    "w_marker",
    "w_file",
    "w_incomplete",
}

BLOCKED_ACTIONS = ["auto_apply", "baseline_update", "gate_change"]


def load_json_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def write_timing_tuning_proposal(
    payload: dict[str, Any],
    out_path: str | Path,
) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _load_optional(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    src = Path(path)
    if not src.exists():
        return None
    return load_json_object(src)


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _reason(code: str, message: str, **extra: Any) -> dict[str, Any]:
    item = {"code": code, "message": message}
    item.update(extra)
    return item


def _source_mode(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    return str(source.get("mode") or "")


def _total_samples(payload: dict[str, Any] | None) -> int:
    if not isinstance(payload, dict):
        return 0
    try:
        return int(payload.get("total_samples") or 0)
    except (TypeError, ValueError):
        return 0


def _truth_stats(payload: dict[str, Any] | None) -> dict[str, int]:
    stats = {"valid": 0, "invalid": 0}
    if not isinstance(payload, dict):
        return stats
    samples = payload.get("samples")
    if not isinstance(samples, list):
        return stats
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        expected = ""
        for field in ("final_timing_action", "timing_action_truth", "expected_action"):
            value = str(sample.get(field) or "").strip()
            if value:
                expected = value
                break
        if not expected:
            continue
        if expected in FINAL_TIMING_ACTIONS:
            stats["valid"] += 1
        else:
            stats["invalid"] += 1
    return stats


def _is_latest_audit_path(path: Path) -> bool:
    return path.name == "timing_signal_audit_latest.json"


def _is_run_scoped_audit_path(path: Path, run_id: str = "") -> bool:
    parts = path.as_posix().split("/")
    for index, part in enumerate(parts[:-2]):
        if part != "runs":
            continue
        if parts[index + 2] != "timing_signal_audit.json":
            continue
        if not run_id or parts[index + 1] == run_id:
            return True
    return False


def _timing_audit_path_blocking(source_paths: dict[str, str], run_id: str) -> list[dict[str, Any]]:
    path_value = str(source_paths.get("timing_audit") or "")
    if not path_value:
        return [
            _reason(
                "missing_immutable_artifact",
                "TimingSignal audit 必须引用 run-scoped artifact",
            )
        ]
    path = Path(path_value)
    if _is_latest_audit_path(path):
        return [
            _reason(
                "explicit_latest_audit",
                "TimingSignal audit 不允许使用 latest artifact 作为调参证据",
                path=path_value,
            )
        ]
    if not _is_run_scoped_audit_path(path, run_id):
        return [
            _reason(
                "audit_not_run_scoped",
                "TimingSignal audit 必须使用 run-scoped artifact",
                path=path_value,
                run_id=run_id,
            )
        ]
    return []


def parse_candidate_sets(
    params: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(params, dict):
        return [], [], [_reason("missing_param_candidates", "缺少候选参数文件")]
    if params.get("candidate_version") != 1:
        return [], [], [
            _reason("unsupported_candidate_version", "只支持 candidate_version=1")
        ]
    candidates = params.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return [], [], [_reason("missing_param_candidates", "候选参数列表为空")]

    parsed: list[dict[str, Any]] = []
    parameters: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            blocking.append(_reason("unsupported_proposal_input", "候选参数必须是对象"))
            continue
        param_diff = item.get("param_diff") if isinstance(item.get("param_diff"), dict) else {}
        candidate_id = str(item.get("id") or "").strip()
        if not candidate_id:
            blocking.append(_reason("missing_candidate_id", "候选参数缺少 id"))
        elif candidate_id in seen_ids:
            blocking.append(
                _reason(
                    "duplicate_candidate_id",
                    "候选参数 id 重复",
                    candidate_id=candidate_id,
                )
            )
        else:
            seen_ids.add(candidate_id)
        if not param_diff:
            blocking.append(
                _reason(
                    "empty_candidate_param_diff",
                    "候选参数 param_diff 不能为空",
                    candidate_id=candidate_id,
                )
            )
        unsupported = sorted(
            str(name) for name in param_diff if str(name) not in ALLOWED_PARAM_NAMES
        )
        if unsupported:
            blocking.append(
                _reason(
                    "unsupported_proposal_input",
                    "候选参数包含不支持的字段",
                    params=unsupported,
                )
            )
        evidence_refs = item.get("evidence_refs")
        parsed.append({
            "id": candidate_id,
            "area": str(item.get("scope") or "timing_score"),
            "risk_level": str(item.get("risk_level") or "unknown"),
            "rationale": str(item.get("description") or ""),
            "param_diff": param_diff,
            "expected_effect": str(item.get("expected_effect") or ""),
            "evidence_refs": (
                [dict(ref) for ref in evidence_refs if isinstance(ref, dict)]
                if isinstance(evidence_refs, list)
                else []
            ),
            "non_goals": ["不自动修改 live 参数", "不更新 baseline"],
        })
        for name, value in param_diff.items():
            parameters.append({
                "candidate_id": candidate_id,
                "name": str(name),
                "value": value,
            })
    return parsed, parameters, blocking


def validation_plan() -> list[dict[str, str]]:
    return [
        {
            "name": "proposal_unit_tests",
            "command": (
                "python -B -m pytest tests/test_timing_tuning_proposal.py "
                "-q -p no:cacheprovider"
            ),
            "purpose": "验证 proposal schema、readiness 和 CLI",
        },
        {
            "name": "timing_gate_adjacent_tests",
            "command": (
                "python -B -m pytest tests/test_timing_score.py "
                "tests/test_timing_gate.py tests/test_timing_runtime.py "
                "-q -p no:cacheprovider"
            ),
            "purpose": "确认 proposal 生成不改变 live TimingGate 行为",
        },
        {
            "name": "artifact_adjacent_tests",
            "command": (
                "python -B -m pytest tests/test_eval_artifact_trends.py "
                "tests/test_periodic_tuning_analysis.py "
                "tests/test_timing_signal_audit.py tests/test_eval_baseline.py "
                "-q -p no:cacheprovider"
            ),
            "purpose": "确认输入 artifact 合同保持兼容",
        },
        {
            "name": "timing_gate_baseline_gate",
            "command": "bash scripts/run_timing_gate_gate.sh",
            "purpose": "确认现有 baseline gate 未被 proposal 生成过程改变",
        },
    ]


def _empty_simulation(candidate_count: int = 0) -> dict[str, Any]:
    return {
        "case_count": 0,
        "candidate_count": candidate_count,
        "flip_count": 0,
        "flips": [],
        "aggregates": [],
    }


def build_timing_tuning_proposal(
    *,
    manifest: dict[str, Any] | None,
    trends: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    timing_audit: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    params: dict[str, Any] | None,
    source_paths: dict[str, str] | None = None,
    simulation: dict[str, Any] | None = None,
    extra_blocking: list[dict[str, Any]] | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    source_paths = source_paths or {}
    proposal_run_id = str(run_id or (manifest or {}).get("run_id") or "")
    blocking: list[dict[str, Any]] = list(extra_blocking or [])

    if not isinstance(manifest, dict):
        blocking.append(_reason("manifest_missing", "缺少 periodic manifest"))
    if not isinstance(trends, dict):
        blocking.append(_reason("trends_missing", "缺少 artifact trends"))
    if not isinstance(analysis, dict):
        blocking.append(_reason("analysis_missing", "缺少 tuning analysis"))
    else:
        readiness = analysis.get("readiness")
        if isinstance(readiness, dict) and readiness.get("ready") is False:
            blocking.append(
                _reason(
                    "unsupported_proposal_input",
                    "tuning analysis 未 ready",
                    source="tuning_analysis",
                    upstream_reasons=readiness.get("blocking_reasons") or [],
                )
            )

    if not isinstance(timing_audit, dict):
        blocking.append(_reason("timing_audit_missing", "缺少 TimingSignal audit"))
    else:
        if _source_mode(timing_audit) == "skipped":
            blocking.append(_reason("timing_audit_skipped", "TimingSignal audit 被跳过"))
        if _total_samples(timing_audit) <= 0:
            blocking.append(
                _reason("timing_zero_samples", "TimingSignal audit 样本数为 0")
            )
        audit_source = (
            timing_audit.get("source")
            if isinstance(timing_audit.get("source"), dict)
            else {}
        )
        audit_run_id = str(audit_source.get("run_id") or "")
        if proposal_run_id and audit_run_id and proposal_run_id != audit_run_id:
            blocking.append(
                _reason(
                    "audit_run_mismatch",
                    "manifest run_id 与 TimingSignal audit run_id 不一致",
                    manifest_run_id=proposal_run_id,
                    audit_run_id=audit_run_id,
                )
            )
        truth = _truth_stats(timing_audit)
        if truth["valid"] <= 0:
            blocking.append(
                _reason(
                    "missing_action_truth",
                    "TimingSignal audit 不包含最终 timing_action truth",
                )
            )
        if truth["invalid"] > 0:
            blocking.append(
                _reason(
                    "invalid_action_truth",
                    "TimingSignal audit 包含非法 final_timing_action",
                    invalid_count=truth["invalid"],
                )
            )

    if not isinstance(baseline, dict):
        blocking.append(_reason("baseline_missing", "缺少 timing_gate baseline"))
    blocking.extend(_timing_audit_path_blocking(source_paths, proposal_run_id))

    candidate_sets, parameters, candidate_blocking = parse_candidate_sets(params)
    blocking.extend(candidate_blocking)

    return {
        "proposal_version": PROPOSAL_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "git_sha": _git_sha(),
            "manifest_path": source_paths.get("manifest", ""),
            "trends_path": source_paths.get("trends", ""),
            "analysis_path": source_paths.get("analysis", ""),
            "timing_audit_path": source_paths.get("timing_audit", ""),
            "baseline_path": source_paths.get("baseline", ""),
            "params_path": source_paths.get("params", ""),
            "cases_path": source_paths.get("cases", ""),
            "run_id": proposal_run_id,
            "timing_audit_mode": _source_mode(timing_audit),
        },
        "readiness": {"ready": not blocking, "blocking_reasons": blocking},
        "candidate_sets": candidate_sets,
        "parameters": parameters,
        "simulation": simulation or _empty_simulation(len(candidate_sets)),
        "validation_plan": validation_plan(),
        "apply_policy": "manual_only",
        "blocked_actions": list(BLOCKED_ACTIONS),
    }


def resolve_proposal_timing_audit_path(
    manifest: dict[str, Any] | None,
    explicit_path: str | Path | None,
    *,
    run_id: str = "",
) -> tuple[Path | None, list[dict[str, Any]]]:
    effective_run_id = str(run_id or (manifest or {}).get("run_id") or "")
    if explicit_path:
        explicit = Path(explicit_path)
        if _is_latest_audit_path(explicit):
            return None, [
                _reason(
                    "explicit_latest_audit",
                    "TimingSignal audit 不允许使用 latest artifact 作为调参证据",
                    path=str(explicit),
                )
            ]
        if not _is_run_scoped_audit_path(explicit, effective_run_id):
            return None, [
                _reason(
                    "audit_not_run_scoped",
                    "TimingSignal audit 必须使用 run-scoped artifact",
                    path=str(explicit),
                    run_id=effective_run_id,
                )
            ]
        if explicit.exists():
            return explicit, []
        return None, []
    if not isinstance(manifest, dict):
        return None, []

    steps = manifest.get("steps")
    if not isinstance(steps, list):
        return None, []
    saw_non_run_scoped = False
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("kind") or "") != "timing_signal_audit":
            continue
        report_paths = step.get("report_paths")
        if not isinstance(report_paths, list):
            continue
        for item in report_paths:
            path = Path(str(item))
            if not path.exists() or path.suffix.lower() != ".json":
                continue
            if _is_latest_audit_path(path) or not _is_run_scoped_audit_path(
                path,
                effective_run_id,
            ):
                saw_non_run_scoped = True
                continue
            return path, []
    if saw_non_run_scoped:
        return None, [
            _reason(
                "audit_not_run_scoped",
                "TimingSignal audit 必须使用 run-scoped artifact",
                run_id=effective_run_id,
            )
        ]
    return None, []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成只读 TimingGate 调参提案报告",
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--trends", default=str(DEFAULT_TRENDS))
    parser.add_argument("--analysis", default=str(DEFAULT_ANALYSIS))
    parser.add_argument("--timing-audit", default="")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_DIR))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--run-id", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    manifest_path = Path(args.manifest)
    trends_path = Path(args.trends)
    analysis_path = Path(args.analysis)
    baseline_path = Path(args.baseline)
    params_path = Path(args.params) if args.params else None
    cases_path = Path(args.cases)

    manifest = _load_optional(manifest_path)
    trends = _load_optional(trends_path)
    analysis = _load_optional(analysis_path)
    baseline = _load_optional(baseline_path)
    params = _load_optional(params_path)
    effective_run_id = str(args.run_id or (manifest or {}).get("run_id") or "")
    audit_path, path_blocking = resolve_proposal_timing_audit_path(
        manifest,
        args.timing_audit or None,
        run_id=effective_run_id,
    )
    timing_audit = load_json_object(audit_path) if audit_path else None
    raw_candidates = (
        params.get("candidates")
        if isinstance(params, dict) and isinstance(params.get("candidates"), list)
        else []
    )
    from evals.timing_score_simulation import load_timing_cases, simulate_timing_candidates

    simulation = simulate_timing_candidates(
        load_timing_cases(cases_path),
        raw_candidates,
    )

    payload = build_timing_tuning_proposal(
        manifest=manifest,
        trends=trends,
        analysis=analysis,
        timing_audit=timing_audit,
        baseline=baseline,
        params=params,
        simulation=simulation,
        extra_blocking=path_blocking,
        run_id=effective_run_id,
        source_paths={
            "manifest": str(manifest_path) if manifest_path.exists() else "",
            "trends": str(trends_path) if trends_path.exists() else "",
            "analysis": str(analysis_path) if analysis_path.exists() else "",
            "timing_audit": str(audit_path) if audit_path else "",
            "baseline": str(baseline_path) if baseline_path.exists() else "",
            "params": str(params_path) if params_path and params_path.exists() else "",
            "cases": str(cases_path) if cases_path.exists() else "",
        },
    )
    path = write_timing_tuning_proposal(payload, args.out)
    print(f"timing_tuning_proposal={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
