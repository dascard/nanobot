#!/usr/bin/env python3
"""把 Sandbox Smoke 的分组 JUnit 证据汇总为失败关闭的 JSON。"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 Sandbox Smoke 汇总证据。")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--preflight-status",
        choices=("passed", "blocked"),
        required=True,
    )
    parser.add_argument("--preflight-log", type=Path, required=True)
    parser.add_argument(
        "--group",
        action="append",
        nargs=5,
        metavar=("ID", "名称", "退出码", "JUNIT", "日志"),
        default=[],
    )
    return parser.parse_args()


def _count(root: ElementTree.Element, name: str) -> int:
    raw = root.attrib.get(name)
    if raw is not None:
        return int(raw)
    return sum(int(child.attrib.get(name, "0")) for child in root)


def _group(value: list[str]) -> dict[str, object]:
    group_id, name, raw_exit_code, junit_name, log_name = value
    exit_code = int(raw_exit_code)
    junit = Path(junit_name)
    tests = failures = errors = skipped = 0
    parse_error = ""
    try:
        root = ElementTree.parse(junit).getroot()
        if root.tag not in {"testsuite", "testsuites"}:
            raise ValueError("JUnit 根节点无效")
        tests = _count(root, "tests")
        failures = _count(root, "failures")
        errors = _count(root, "errors")
        skipped = _count(root, "skipped")
    except (OSError, ElementTree.ParseError, TypeError, ValueError) as exc:
        parse_error = type(exc).__name__

    if exit_code != 0 or failures or errors or parse_error:
        status = "failed"
    elif tests <= 0 or skipped:
        status = "blocked"
    else:
        status = "passed"
    return {
        "id": group_id,
        "name": name,
        "status": status,
        "exit_code": exit_code,
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "junit": os.fspath(junit),
        "log": os.fspath(Path(log_name)),
        "parse_error": parse_error,
    }


def build_summary(arguments: argparse.Namespace) -> dict[str, object]:
    groups = [_group(list(value)) for value in arguments.group]
    failed = sum(item["status"] == "failed" for item in groups)
    blocked = sum(item["status"] == "blocked" for item in groups)
    passed = sum(item["status"] == "passed" for item in groups)
    if failed:
        result = "failed"
    elif arguments.preflight_status != "passed" or blocked:
        result = "blocked"
    else:
        result = "passed"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preflight": {
            "status": arguments.preflight_status,
            "log": os.fspath(arguments.preflight_log),
        },
        "groups": groups,
        "totals": {
            "groups": len(groups),
            "passed": passed,
            "failed": failed,
            "blocked": blocked,
            "tests": sum(int(item["tests"]) for item in groups),
            "failures": sum(int(item["failures"]) for item in groups),
            "errors": sum(int(item["errors"]) for item in groups),
            "skipped": sum(int(item["skipped"]) for item in groups),
        },
        "result": result,
    }


def _write_atomic(output: Path, value: dict[str, object]) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    arguments = _arguments()
    summary = build_summary(arguments)
    _write_atomic(arguments.output, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if summary["result"] == "passed":
        return 0
    if summary["result"] == "blocked":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
