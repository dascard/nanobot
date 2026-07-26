"""基于真实 Agent 事件 artifact 评估 Sandbox 工具兼容性。"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_DIR = ROOT / "evals" / "cases" / "sandbox_agent_compatibility"
_MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
_EVENT_TYPES = {
    "workspace_ready",
    "tool_call",
    "tool_result",
    "retry",
    "checkpoint",
    "security_policy_violation",
    "lease_rebuilt",
}
_CASE_KEYS = {
    "schema_version",
    "case_id",
    "title",
    "task",
    "required_checkpoints",
    "thresholds",
}
_THRESHOLD_KEYS = {
    "max_invalid_tool_calls",
    "max_environment_misunderstanding_retries",
    "max_calls_to_first_effective_test",
    "require_long_task_recovery",
    "require_post_rebuild_continuation",
    "max_security_policy_violation_attempts",
}
_ARTIFACT_KEYS = {
    "schema_version",
    "run_id",
    "case_id",
    "started_at",
    "finished_at",
    "events",
    "outcome",
}
_EVENT_KEYS = {
    "seq",
    "type",
    "tool",
    "valid",
    "reason",
    "category",
    "effective",
}
_OUTCOME_KEYS = {
    "task_success",
    "long_task_recovered",
    "post_rebuild_continued",
    "checkpoints",
}


class CompatibilityEvalError(ValueError):
    """兼容性 Eval 输入不满足失败关闭契约。"""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CompatibilityEvalError(f"JSON 存在重复字段：{key}")
        value[key] = item
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
        if (
            not path.is_file()
            or path.is_symlink()
            or metadata.st_size > _MAX_ARTIFACT_BYTES
        ):
            raise OSError
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda item: (_ for _ in ()).throw(
                CompatibilityEvalError(f"JSON 非法常量：{item}")
            ),
        )
    except CompatibilityEvalError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompatibilityEvalError(f"无法安全读取 JSON：{path}") from exc
    if not isinstance(value, dict):
        raise CompatibilityEvalError(f"JSON 根节点必须是对象：{path}")
    return value


def _bounded_string(value: object, *, name: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CompatibilityEvalError(f"{name} 无效")
    return value


def _bounded_count(value: object, *, name: str, maximum: int = 10_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CompatibilityEvalError(f"{name} 必须是整数")
    if not 0 <= value <= maximum:
        raise CompatibilityEvalError(f"{name} 超出范围")
    return value


def _case(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    if set(value) != _CASE_KEYS or value.get("schema_version") != 1:
        raise CompatibilityEvalError(f"Eval case schema 无效：{path}")
    case_id = _bounded_string(value["case_id"], name="case_id", maximum=128)
    _bounded_string(value["title"], name=f"{case_id}.title", maximum=255)
    _bounded_string(value["task"], name=f"{case_id}.task", maximum=8192)
    checkpoints = value["required_checkpoints"]
    if (
        not isinstance(checkpoints, list)
        or not checkpoints
        or len(checkpoints) > 64
        or any(
            not isinstance(item, str) or not item or len(item) > 128
            for item in checkpoints
        )
        or len(checkpoints) != len(set(checkpoints))
    ):
        raise CompatibilityEvalError(f"{case_id}.required_checkpoints 无效")
    thresholds = value["thresholds"]
    if not isinstance(thresholds, dict) or set(thresholds) != _THRESHOLD_KEYS:
        raise CompatibilityEvalError(f"{case_id}.thresholds 无效")
    normalized_thresholds = {
        key: (
            thresholds[key]
            if key.startswith("require_")
            else _bounded_count(
                thresholds[key],
                name=f"{case_id}.{key}",
                maximum=1000,
            )
        )
        for key in sorted(_THRESHOLD_KEYS)
    }
    if any(
        not isinstance(normalized_thresholds[key], bool)
        for key in normalized_thresholds
        if key.startswith("require_")
    ):
        raise CompatibilityEvalError(f"{case_id} 的布尔阈值无效")
    return {
        **value,
        "case_id": case_id,
        "required_checkpoints": tuple(checkpoints),
        "thresholds": normalized_thresholds,
    }


def load_cases(cases_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        metadata = cases_dir.lstat()
        if (
            cases_dir.is_symlink()
            or not cases_dir.is_dir()
            or metadata.st_nlink < 1
        ):
            raise OSError
        paths = sorted(cases_dir.glob("*.json"))
    except OSError as exc:
        raise CompatibilityEvalError("无法读取兼容性 Eval cases") from exc
    if not paths:
        raise CompatibilityEvalError("没有 Sandbox Agent 兼容性 Eval case")
    cases: dict[str, dict[str, Any]] = {}
    for path in paths:
        value = _case(path)
        case_id = value["case_id"]
        if case_id in cases:
            raise CompatibilityEvalError(f"重复 case_id：{case_id}")
        cases[case_id] = value
    return cases


def _artifact(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    if set(value) != _ARTIFACT_KEYS or value.get("schema_version") != 1:
        raise CompatibilityEvalError(f"Agent artifact schema 无效：{path}")
    run_id = _bounded_string(value["run_id"], name="run_id", maximum=128)
    case_id = _bounded_string(value["case_id"], name="case_id", maximum=128)
    _bounded_string(value["started_at"], name=f"{run_id}.started_at", maximum=64)
    _bounded_string(value["finished_at"], name=f"{run_id}.finished_at", maximum=64)
    events = value["events"]
    if not isinstance(events, list) or not events or len(events) > 10_000:
        raise CompatibilityEvalError(f"{run_id}.events 无效")
    normalized_events = []
    for expected_seq, event in enumerate(events, start=1):
        if not isinstance(event, dict) or set(event) != _EVENT_KEYS:
            raise CompatibilityEvalError(f"{run_id}.events schema 无效")
        if event["seq"] != expected_seq or event["type"] not in _EVENT_TYPES:
            raise CompatibilityEvalError(f"{run_id}.events 顺序或类型无效")
        for key in ("tool", "reason", "category"):
            if not isinstance(event[key], str) or len(event[key]) > 255:
                raise CompatibilityEvalError(f"{run_id}.events.{key} 无效")
        if not isinstance(event["valid"], bool) or not isinstance(
            event["effective"],
            bool,
        ):
            raise CompatibilityEvalError(f"{run_id}.events 布尔事实无效")
        normalized_events.append(dict(event))
    outcome = value["outcome"]
    if not isinstance(outcome, dict) or set(outcome) != _OUTCOME_KEYS:
        raise CompatibilityEvalError(f"{run_id}.outcome 无效")
    for key in (
        "task_success",
        "long_task_recovered",
        "post_rebuild_continued",
    ):
        if not isinstance(outcome[key], bool):
            raise CompatibilityEvalError(f"{run_id}.outcome.{key} 无效")
    checkpoints = outcome["checkpoints"]
    if (
        not isinstance(checkpoints, list)
        or len(checkpoints) > 64
        or any(
            not isinstance(item, str) or not item or len(item) > 128
            for item in checkpoints
        )
        or len(checkpoints) != len(set(checkpoints))
    ):
        raise CompatibilityEvalError(f"{run_id}.outcome.checkpoints 无效")
    return {
        **value,
        "run_id": run_id,
        "case_id": case_id,
        "events": normalized_events,
        "outcome": {**outcome, "checkpoints": tuple(checkpoints)},
        "source": os.fspath(path),
    }


def load_artifacts(paths: list[Path]) -> dict[str, dict[str, Any]]:
    candidates: list[Path] = []
    for path in paths:
        try:
            path.lstat()
        except OSError as exc:
            raise CompatibilityEvalError(
                f"缺少真实 Agent 执行 artifact：{path}"
            ) from exc
        if path.is_dir() and not path.is_symlink():
            candidates.extend(sorted(path.glob("*.json")))
        else:
            candidates.append(path)
    if not candidates:
        raise CompatibilityEvalError(
            "缺少真实 Agent 执行 artifact，静态 case 不能视为已运行"
        )
    artifacts: dict[str, dict[str, Any]] = {}
    for path in candidates:
        value = _artifact(path)
        case_id = value["case_id"]
        if case_id in artifacts:
            raise CompatibilityEvalError(f"同一 case 存在多个 artifact：{case_id}")
        artifacts[case_id] = value
    return artifacts


def _calls_to_first_effective_test(events: list[dict[str, Any]]) -> int | None:
    ready_seq = next(
        (
            int(event["seq"])
            for event in events
            if event["type"] == "workspace_ready"
        ),
        None,
    )
    if ready_seq is None:
        return None
    test_seq = next(
        (
            int(event["seq"])
            for event in events
            if (
                int(event["seq"]) > ready_seq
                and event["type"] == "tool_result"
                and event["category"] == "test"
                and event["effective"] is True
            )
        ),
        None,
    )
    if test_seq is None:
        return None
    return sum(
        event["type"] == "tool_call"
        for event in events
        if ready_seq < int(event["seq"]) <= test_seq
    )


def evaluate_case(
    case: dict[str, Any],
    artifact: dict[str, Any],
) -> dict[str, Any]:
    events = artifact["events"]
    outcome = artifact["outcome"]
    thresholds = case["thresholds"]
    invalid_tool_calls = sum(
        event["type"] == "tool_call" and event["valid"] is False
        for event in events
    )
    environment_retries = sum(
        event["type"] == "retry"
        and event["reason"] == "environment_misunderstanding"
        for event in events
    )
    security_attempts = sum(
        event["type"] == "security_policy_violation"
        for event in events
    )
    calls_to_test = _calls_to_first_effective_test(events)
    missing_checkpoints = sorted(
        set(case["required_checkpoints"]) - set(outcome["checkpoints"])
    )
    errors: list[str] = []
    if outcome["task_success"] is not True:
        errors.append("任务未成功")
    if missing_checkpoints:
        errors.append(f"缺少检查点：{','.join(missing_checkpoints)}")
    if invalid_tool_calls > thresholds["max_invalid_tool_calls"]:
        errors.append("无效工具调用超过阈值")
    if (
        environment_retries
        > thresholds["max_environment_misunderstanding_retries"]
    ):
        errors.append("环境误解重试超过阈值")
    if (
        calls_to_test is None
        or calls_to_test > thresholds["max_calls_to_first_effective_test"]
    ):
        errors.append("到首次有效测试的调用数未达标")
    if (
        thresholds["require_long_task_recovery"]
        and outcome["long_task_recovered"] is not True
    ):
        errors.append("长任务恢复失败")
    if (
        thresholds["require_post_rebuild_continuation"]
        and outcome["post_rebuild_continued"] is not True
    ):
        errors.append("容器重建后未继续工作")
    if (
        security_attempts
        > thresholds["max_security_policy_violation_attempts"]
    ):
        errors.append("安全策略违规尝试超过阈值")
    return {
        "case_id": case["case_id"],
        "run_id": artifact["run_id"],
        "passed": not errors,
        "errors": errors,
        "metrics": {
            "task_success": outcome["task_success"],
            "invalid_tool_calls": invalid_tool_calls,
            "environment_misunderstanding_retries": environment_retries,
            "calls_to_first_effective_test": calls_to_test,
            "long_task_recovered": outcome["long_task_recovered"],
            "post_rebuild_continued": outcome["post_rebuild_continued"],
            "security_policy_violation_attempts": security_attempts,
        },
        "artifact": artifact["source"],
    }


def build_report(
    cases: dict[str, dict[str, Any]],
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    missing = sorted(set(cases) - set(artifacts))
    unknown = sorted(set(artifacts) - set(cases))
    if missing:
        raise CompatibilityEvalError(
            f"缺少真实 Agent artifact：{','.join(missing)}"
        )
    if unknown:
        raise CompatibilityEvalError(
            f"artifact 引用了未知 case：{','.join(unknown)}"
        )
    results = [
        evaluate_case(cases[case_id], artifacts[case_id])
        for case_id in sorted(cases)
    ]
    required_long = [
        result
        for result in results
        if cases[result["case_id"]]["thresholds"][
            "require_long_task_recovery"
        ]
    ]
    required_rebuild = [
        result
        for result in results
        if cases[result["case_id"]]["thresholds"][
            "require_post_rebuild_continuation"
        ]
    ]

    def rate(values: list[dict[str, Any]], key: str) -> float | None:
        if not values:
            return None
        return sum(item["metrics"][key] is True for item in values) / len(values)

    passed = sum(result["passed"] for result in results)
    calls_to_test = [
        result["metrics"]["calls_to_first_effective_test"] for result in results
    ]
    metrics = {
        "task_success_rate": (
            sum(result["metrics"]["task_success"] is True for result in results)
            / len(results)
        ),
        "invalid_tool_calls": sum(
            result["metrics"]["invalid_tool_calls"] for result in results
        ),
        "environment_misunderstanding_retries": sum(
            result["metrics"]["environment_misunderstanding_retries"]
            for result in results
        ),
        "mean_calls_to_first_effective_test": (
            sum(int(value) for value in calls_to_test) / len(calls_to_test)
            if all(value is not None for value in calls_to_test)
            else None
        ),
        "long_task_recovery_success_rate": rate(
            required_long,
            "long_task_recovered",
        ),
        "post_rebuild_continuation_success_rate": rate(
            required_rebuild,
            "post_rebuild_continued",
        ),
        "security_policy_violation_attempts": sum(
            result["metrics"]["security_policy_violation_attempts"]
            for result in results
        ),
    }
    return {
        "schema_version": 1,
        "suite": "sandbox_agent_compatibility",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if passed == len(results) else "failed",
        "case_count": len(results),
        "passed_count": passed,
        "failed_count": len(results) - passed,
        "metrics": metrics,
        "results": results,
    }


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="评估真实 Agent 在 Developer Sandbox 中的兼容性。",
    )
    parser.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES_DIR)
    parser.add_argument(
        "--artifacts",
        type=Path,
        nargs="+",
        required=True,
        help="真实 Agent 执行 artifact 文件或目录。",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        report = build_report(
            load_cases(arguments.cases_dir),
            load_artifacts(arguments.artifacts),
        )
    except CompatibilityEvalError as exc:
        print(f"Sandbox Agent 兼容性 Eval 阻断：{exc}", file=os.sys.stderr)
        return 2
    _write_atomic(arguments.output, report)
    print(f"report={arguments.output}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
