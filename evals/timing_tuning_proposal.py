"""TimingGate 可审核调参提案报告。"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


PROPOSAL_VERSION = 1

DEFAULT_MANIFEST = Path("evals/reports/periodic_manifest_latest.json")
DEFAULT_TRENDS = Path("evals/reports/artifact_trends_latest.json")
DEFAULT_ANALYSIS = Path("evals/reports/tuning_analysis_latest.json")
DEFAULT_TIMING_AUDIT = Path("evals/reports/timing_signal_audit_latest.json")
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


def _has_action_truth(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    samples = payload.get("samples")
    if not isinstance(samples, list):
        return False
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        expected = (
            sample.get("expected_action")
            or sample.get("timing_action_truth")
            or sample.get("final_timing_action")
        )
        if str(expected or "") in {"continue", "wait", "no_reply"}:
            return True
    return False


def _has_immutable_artifact_path(source_paths: dict[str, str]) -> bool:
    path = str(source_paths.get("timing_audit") or "")
    return "/runs/" in path or "-timing_signal_audit.json" in path


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
    for item in candidates:
        if not isinstance(item, dict):
            blocking.append(_reason("unsupported_proposal_input", "候选参数必须是对象"))
            continue
        param_diff = item.get("param_diff") if isinstance(item.get("param_diff"), dict) else {}
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
        candidate_id = str(item.get("id") or "")
        parsed.append({
            "id": candidate_id,
            "area": str(item.get("scope") or "timing_score"),
            "risk_level": str(item.get("risk_level") or "unknown"),
            "rationale": str(item.get("description") or ""),
            "param_diff": param_diff,
            "evidence_refs": [],
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
) -> dict[str, Any]:
    source_paths = source_paths or {}
    blocking: list[dict[str, Any]] = []

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
        if not _has_action_truth(timing_audit):
            blocking.append(
                _reason(
                    "missing_action_truth",
                    "TimingSignal audit 不包含最终 timing_action truth",
                )
            )

    if not isinstance(baseline, dict):
        blocking.append(_reason("baseline_missing", "缺少 timing_gate baseline"))
    if not _has_immutable_artifact_path(source_paths):
        blocking.append(
            _reason(
                "missing_immutable_artifact",
                "TimingSignal audit 必须引用 run-scoped 或 dated artifact",
            )
        )

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
            "run_id": str((manifest or {}).get("run_id") or ""),
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
