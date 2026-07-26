from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sandbox-smoke-summary.py"


def _junit(
    path: Path,
    *,
    tests: int,
    failures: int = 0,
    errors: int = 0,
    skipped: int = 0,
) -> None:
    path.write_text(
        (
            f'<testsuite tests="{tests}" failures="{failures}" '
            f'errors="{errors}" skipped="{skipped}"></testsuite>'
        ),
        encoding="utf-8",
    )


def _run(
    tmp_path: Path,
    *,
    preflight: str = "passed",
    groups: list[tuple[str, str, int, Path, Path]] | None = None,
):
    output = tmp_path / "summary.json"
    preflight_log = tmp_path / "preflight.log"
    preflight_log.write_text("预检证据\n", encoding="utf-8")
    command = [
        sys.executable,
        str(SCRIPT),
        "--output",
        str(output),
        "--preflight-status",
        preflight,
        "--preflight-log",
        str(preflight_log),
    ]
    for group in groups or []:
        command.extend([
            "--group",
            group[0],
            group[1],
            str(group[2]),
            str(group[3]),
            str(group[4]),
        ])
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, json.loads(output.read_text(encoding="utf-8"))


def test_summary_passes_only_when_every_group_has_unskipped_tests(tmp_path):
    groups = []
    for index, group_id in enumerate(
        ("基础安全", "租约", "进程", "工具链", "网络", "数据连续性"),
    ):
        junit = tmp_path / f"{index}.xml"
        log = tmp_path / f"{index}.log"
        _junit(junit, tests=index + 1)
        log.write_text("通过\n", encoding="utf-8")
        groups.append((group_id, group_id, 0, junit, log))

    result, summary = _run(tmp_path, groups=groups)

    assert result.returncode == 0, result.stderr
    assert summary["result"] == "passed"
    assert summary["totals"] == {
        "blocked": 0,
        "errors": 0,
        "failed": 0,
        "failures": 0,
        "groups": 6,
        "passed": 6,
        "skipped": 0,
        "tests": 21,
    }


def test_summary_treats_skip_or_missing_junit_as_nonpassing(tmp_path):
    skipped_junit = tmp_path / "skipped.xml"
    skipped_log = tmp_path / "skipped.log"
    _junit(skipped_junit, tests=1, skipped=1)
    skipped_log.write_text("跳过\n", encoding="utf-8")

    skipped_result, skipped = _run(
        tmp_path,
        groups=[("network", "网络", 0, skipped_junit, skipped_log)],
    )
    assert skipped_result.returncode == 2
    assert skipped["result"] == "blocked"
    assert skipped["groups"][0]["status"] == "blocked"

    missing_result, missing = _run(
        tmp_path,
        groups=[(
            "lease",
            "Lease",
            0,
            tmp_path / "missing.xml",
            tmp_path / "missing.log",
        )],
    )
    assert missing_result.returncode == 1
    assert missing["result"] == "failed"
    assert missing["groups"][0]["parse_error"]


def test_summary_records_preflight_block_without_fake_group_passes(tmp_path):
    result, summary = _run(tmp_path, preflight="blocked")

    assert result.returncode == 2
    assert summary["preflight"]["status"] == "blocked"
    assert summary["groups"] == []
    assert summary["result"] == "blocked"
