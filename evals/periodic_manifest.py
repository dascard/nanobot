"""周期评测运行清单生成工具。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ARTIFACTS = [
    "evals/reports/*.json",
    "tmp/rag_benchmark/reports/*.json",
    "tmp/rag_benchmark/reports/*.md",
]


def write_steps_jsonl(path: str | Path, steps: list[dict[str, Any]]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(step, ensure_ascii=False, sort_keys=True) for step in steps]
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _read_steps_jsonl(path: str | Path) -> list[dict[str, Any]]:
    src = Path(path)
    if not src.exists():
        return []
    steps: list[dict[str, Any]] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("periodic step record must be a JSON object")
        steps.append(item)
    return steps


def _load_first_report(paths: list[str]) -> tuple[dict[str, Any] | None, bool]:
    for item in paths:
        path = Path(item)
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"report must be a JSON object: {path}")
        return payload, False
    return None, bool(paths)


def _eval_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "total": int(report.get("total") or 0),
        "passed": int(report.get("passed") or 0),
        "failed": int(report.get("failed") or 0),
        "pass_rate": float(report.get("pass_rate") or 0.0),
    }


def _rag_summary(report: dict[str, Any]) -> dict[str, Any]:
    overall = ((report.get("metrics") or {}).get("overall") or {})
    return {
        "total_cases": int(overall.get("total_cases") or 0),
        "pass_rate": float(overall.get("pass_rate") or 0.0),
        "hit@5": float(overall.get("hit@5") or 0.0),
        "mrr": float(overall.get("mrr") or 0.0),
        "positive_cases": int(overall.get("positive_cases") or 0),
    }


def _timing_signal_summary(report: dict[str, Any]) -> dict[str, Any]:
    shadow = report.get("shadow") or {}
    return {
        "total_samples": int(report.get("total_samples") or 0),
        "labeled_samples": int(report.get("labeled_samples") or 0),
        "action_mismatch_count": int(shadow.get("action_mismatch_count") or 0),
        "action_mismatch_rate": float(shadow.get("action_mismatch_rate") or 0.0),
    }


def _step_summary(kind: str, report: dict[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return {}
    if kind == "rag_benchmark":
        return _rag_summary(report)
    if kind == "timing_signal_audit":
        return _timing_signal_summary(report)
    if kind == "eval_suite":
        return _eval_summary(report)
    return {}


def _normalize_report_paths(step: dict[str, Any]) -> list[str]:
    paths = step.get("report_paths")
    if isinstance(paths, list):
        return [str(path) for path in paths if str(path)]
    path = step.get("report_path")
    if path:
        return [str(path)]
    return []


def _build_step(step: dict[str, Any]) -> dict[str, Any]:
    kind = str(step.get("kind") or "")
    report_paths = _normalize_report_paths(step)
    report, report_missing = _load_first_report(report_paths)
    exit_code = int(step.get("exit_code") or 0)
    baseline_diff = (report or {}).get("baseline_diff") or {}
    gate = (report or {}).get("gate") or {}
    source = (report or {}).get("source") or {}
    notes: dict[str, Any] = {}
    if source.get("reason"):
        notes["reason"] = source.get("reason")
    if source.get("mode"):
        notes["mode"] = source.get("mode")

    payload: dict[str, Any] = {
        "name": str(step.get("name") or ""),
        "kind": kind,
        "suite": str(step.get("suite") or ""),
        "status": "passed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "baseline_path": str(step.get("baseline_path") or ""),
        "report_paths": report_paths,
        "summary": _step_summary(kind, report),
        "gate_passed": gate.get("passed"),
        "new_failed_cases": baseline_diff.get("new_failed_cases") or [],
        "failed_cases": (report or {}).get("failed_cases") or [],
    }
    if report_missing:
        payload["report_missing"] = True
    if notes:
        payload["notes"] = notes
    return payload


def build_periodic_manifest(
    *,
    steps_path: str | Path,
    run_id: str,
    started_at: str,
    finished_at: str,
    exit_code: int,
    trigger: str,
    git: dict[str, str] | None = None,
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    steps = [_build_step(step) for step in _read_steps_jsonl(steps_path)]
    return {
        "manifest_version": 1,
        "run_id": run_id,
        "run_type": "periodic",
        "trigger": trigger,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": int(exit_code),
        "status": "passed" if int(exit_code) == 0 else "failed",
        "git": git or {"sha": "", "ref": "", "repository": ""},
        "artifacts": artifacts or list(DEFAULT_ARTIFACTS),
        "steps": steps,
    }


def _manifest_date(manifest: dict[str, Any]) -> str:
    started_at = str(manifest.get("started_at") or "")
    if len(started_at) >= 10:
        return started_at[:10]
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def write_periodic_manifest(
    manifest: dict[str, Any],
    *,
    reports_dir: str | Path = "evals/reports",
) -> dict[str, str]:
    root = Path(reports_dir)
    run_id = str(manifest["run_id"])
    latest = root / "periodic_manifest_latest.json"
    dated = root / f"{_manifest_date(manifest)}-periodic_manifest.json"
    run_scoped = root / "runs" / run_id / "manifest.json"
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    latest.parent.mkdir(parents=True, exist_ok=True)
    run_scoped.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(text, encoding="utf-8")
    dated.write_text(text, encoding="utf-8")
    run_scoped.write_text(text, encoding="utf-8")
    return {
        "latest": str(latest),
        "dated": str(dated),
        "run": str(run_scoped),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--finished-at", required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--trigger", default="local")
    parser.add_argument("--reports-dir", default="evals/reports")
    parser.add_argument("--git-sha", default="")
    parser.add_argument("--git-ref", default="")
    parser.add_argument("--git-repository", default="")
    parser.add_argument("--artifact", action="append", default=[])
    args = parser.parse_args(argv)

    manifest = build_periodic_manifest(
        steps_path=args.steps,
        run_id=args.run_id,
        started_at=args.started_at,
        finished_at=args.finished_at,
        exit_code=args.exit_code,
        trigger=args.trigger,
        git={
            "sha": args.git_sha,
            "ref": args.git_ref,
            "repository": args.git_repository,
        },
        artifacts=args.artifact or None,
    )
    paths = write_periodic_manifest(manifest, reports_dir=args.reports_dir)
    print(f"periodic_manifest={paths['latest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
