#!/usr/bin/env python3
"""生成 Release Impact 报告或校验固定 diff Golden。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_FIXTURE = Path("tests/fixtures/release_impact_cases.json")
DEFAULT_GOLDEN = Path("tests/golden/release_impact_plans.json")


class ReleaseImpactBuildError(RuntimeError):
    """影响报告输入或 Git 读取失败。"""


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


def load_fixture(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseImpactBuildError(
            f"无法读取 Release Impact fixture：{path}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ReleaseImpactBuildError(
            "Release Impact fixture schema_version 无效"
        )
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ReleaseImpactBuildError(
            "Release Impact fixture cases 无效"
        )
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ReleaseImpactBuildError(
                "Release Impact fixture case 必须是对象"
            )
        case_id = case.get("id")
        paths = case.get("paths")
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in seen_ids
            or not isinstance(paths, list)
            or not all(isinstance(path, str) for path in paths)
        ):
            raise ReleaseImpactBuildError(
                "Release Impact fixture case id 或 paths 无效"
            )
        seen_ids.add(case_id)
        normalized.append({"id": case_id, "paths": paths})
    return normalized


def build_fixture_reports(cases: list[dict[str, Any]]) -> dict[str, object]:
    from core.release import (
        RELEASE_IMPACT_REGISTRY,
        build_release_impact_report,
    )

    return {
        "schema_version": 1,
        "registry_sha256": RELEASE_IMPACT_REGISTRY.sha256,
        "cases": [
            {
                "id": case["id"],
                "report": build_release_impact_report(
                    case["paths"]
                ).to_dict(),
            }
            for case in cases
        ],
    }


def changed_paths_from_git(
    root: Path,
    *,
    base: str,
    head: str,
) -> list[str]:
    try:
        completed = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=ACMR",
                f"{base}...{head}",
                "--",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseImpactBuildError("无法读取 Git diff") from exc
    return sorted({
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    })


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成或检查 Release Impact 报告"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--strict", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write-golden", action="store_true")
    mode.add_argument("--check-golden", action="store_true")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument(
        "--golden-output",
        type=Path,
        default=DEFAULT_GOLDEN,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from core.release import (
        ReleaseImpactError,
        build_release_impact_report,
    )

    args = _build_parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.write_golden or args.check_golden:
            fixture_path = (
                args.fixture
                if args.fixture.is_absolute()
                else root / args.fixture
            )
            golden_path = (
                args.golden_output
                if args.golden_output.is_absolute()
                else root / args.golden_output
            )
            rendered = render_json(
                build_fixture_reports(load_fixture(fixture_path))
            )
            if args.write_golden:
                _write_atomic(golden_path, rendered)
                return 0
            try:
                current = golden_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                print("Release Impact Golden 缺失", file=sys.stderr)
                return 1
            if current != rendered:
                print("Release Impact Golden 已漂移", file=sys.stderr)
                return 1
            return 0

        if args.path:
            paths = args.path
        elif args.base:
            paths = changed_paths_from_git(
                root,
                base=args.base,
                head=args.head,
            )
        else:
            raise ReleaseImpactBuildError(
                "必须提供 --path、--base 或 Golden 模式"
            )
        report = build_release_impact_report(paths)
        if args.strict:
            report.require_owned()
        print(render_json(report.to_dict()), end="")
        return 0
    except (ReleaseImpactBuildError, ReleaseImpactError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
