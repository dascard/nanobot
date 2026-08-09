"""离线语义回放 CLI。

示例：
    python -m evals.replay compare --input replay-compare.json --output report.json
    python -m evals.replay fault-matrix --input replay-faults.json
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from core.replay import (
    FrozenReplayFixture,
    ReplayContractError,
    ReplayScript,
    ReplayStatus,
    compare_replays,
    parse_faults,
    run_fault_matrix,
)


def _payload(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReplayContractError(f"{name} 必须是 JSON 对象")
    return value


def _exact_keys(
    payload: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    missing = sorted(required - payload.keys())
    unknown = sorted(payload.keys() - required - optional)
    if missing:
        raise ReplayContractError(f"缺少字段: {', '.join(missing)}")
    if unknown:
        raise ReplayContractError(f"包含未允许字段: {', '.join(unknown)}")


def execute_compare(value: object) -> dict[str, object]:
    """解析并执行管理 API/CLI 共用的 A/B 回放请求。"""

    payload = _payload(value, "compare payload")
    _exact_keys(
        payload,
        required=frozenset({"fixture", "baseline", "candidate"}),
    )
    fixture = FrozenReplayFixture.from_dict(payload["fixture"])
    baseline = ReplayScript.from_dict(payload["baseline"])
    candidate = ReplayScript.from_dict(payload["candidate"])
    return compare_replays(fixture, baseline, candidate).to_dict()


def execute_fault_matrix(value: object) -> dict[str, object]:
    """解析并执行管理 API/CLI 共用的故障矩阵请求。"""

    payload = _payload(value, "fault matrix payload")
    _exact_keys(
        payload,
        required=frozenset({"fixture", "script"}),
        optional=frozenset({"faults"}),
    )
    fixture = FrozenReplayFixture.from_dict(payload["fixture"])
    script = ReplayScript.from_dict(payload["script"])
    faults = None
    if "faults" in payload:
        faults = parse_faults(payload["faults"])
    return run_fault_matrix(fixture, script, faults=faults)


def _load_input(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReplayContractError(f"无法读取输入文件: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReplayContractError(f"输入文件不是有效 JSON: {exc}") from exc


def _emit_report(report: dict[str, object], output: str) -> None:
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{rendered}\n", encoding="utf-8")
        return
    print(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="使用冻结 Event 和替身执行离线语义回放",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("compare", "fault-matrix"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--input", required=True, help="冻结回放 JSON")
        subparser.add_argument("--output", default="", help="安全报告输出路径")
    args = parser.parse_args(argv)

    try:
        payload = _load_input(args.input)
        if args.command == "compare":
            report = execute_compare(payload)
            exit_code = 0
            if (
                report["baseline"]["status"] != ReplayStatus.SUCCEEDED.value
                or report["candidate"]["status"]
                != ReplayStatus.SUCCEEDED.value
            ):
                exit_code = 1
        else:
            report = execute_fault_matrix(payload)
            exit_code = 0 if (
                report["failed"] == 0 and report["complete_coverage"]
            ) else 1
        _emit_report(report, args.output)
        return exit_code
    except ReplayContractError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["execute_compare", "execute_fault_matrix", "main"]
