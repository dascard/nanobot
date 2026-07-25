#!/usr/bin/env python3
"""生成结构化 Verification Plan 或校验固定输入 Golden。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_FIXTURE = Path("tests/fixtures/verification_plan_cases.json")
DEFAULT_GOLDEN = Path("tests/golden/verification_plans.json")
_CASE_FIELDS = {
    "id",
    "paths",
    "feature_ids",
    "artifact_profile_ids",
}


class VerificationPlanBuildError(RuntimeError):
    """Verification Plan fixture 或 Git 输入无效。"""


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


def _string_list(
    value: object,
    *,
    field_name: str,
) -> list[str]:
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item for item in value)
        or len(value) != len(set(value))
    ):
        raise VerificationPlanBuildError(
            f"Verification Plan fixture {field_name} 无效"
        )
    return list(value)


def load_fixture(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationPlanBuildError(
            f"无法读取 Verification Plan fixture：{path}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise VerificationPlanBuildError(
            "Verification Plan fixture schema_version 无效"
        )
    if set(payload) != {"schema_version", "cases"}:
        raise VerificationPlanBuildError(
            "Verification Plan fixture 包含未知字段"
        )
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise VerificationPlanBuildError(
            "Verification Plan fixture cases 无效"
        )
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != _CASE_FIELDS:
            raise VerificationPlanBuildError(
                "Verification Plan fixture case 字段无效"
            )
        case_id = case.get("id")
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in seen_ids
        ):
            raise VerificationPlanBuildError(
                "Verification Plan fixture case id 无效"
            )
        seen_ids.add(case_id)
        normalized.append({
            "id": case_id,
            "paths": _string_list(
                case.get("paths"),
                field_name=f"{case_id}.paths",
            ),
            "feature_ids": _string_list(
                case.get("feature_ids"),
                field_name=f"{case_id}.feature_ids",
            ),
            "artifact_profile_ids": _string_list(
                case.get("artifact_profile_ids"),
                field_name=f"{case_id}.artifact_profile_ids",
            ),
        })
    return normalized


def build_fixture_plans(
    cases: list[dict[str, Any]],
) -> dict[str, object]:
    from core.lifecycle import FEATURE_LIFECYCLE_REGISTRY
    from core.release import (
        BUILD_PROFILE_REGISTRY,
        RELEASE_IMPACT_REGISTRY,
        VERIFICATION_SUITE_REGISTRY,
        build_release_impact_report,
        build_verification_plan,
    )

    return {
        "schema_version": 1,
        "registries": {
            "verification_suite": (
                VERIFICATION_SUITE_REGISTRY.sha256
            ),
            "release_impact": RELEASE_IMPACT_REGISTRY.sha256,
            "feature_lifecycle": (
                FEATURE_LIFECYCLE_REGISTRY
                .registry_snapshot
                .sha256
            ),
            "build_profile": BUILD_PROFILE_REGISTRY.sha256,
        },
        "cases": [
            {
                "id": case["id"],
                "plan": build_verification_plan(
                    build_release_impact_report(case["paths"]),
                    feature_ids=case["feature_ids"],
                    artifact_profile_ids=(
                        case["artifact_profile_ids"]
                    ),
                ).to_dict(),
            }
            for case in cases
        ],
    }


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成或检查结构化 Verification Plan"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--feature", action="append", default=[])
    parser.add_argument(
        "--artifact-profile",
        action="append",
        default=[],
    )
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
    from core.registry.validation import RegistryError
    from core.release import (
        ReleaseImpactError,
        VerificationPlanError,
        build_release_impact_report,
        build_verification_plan,
    )
    from scripts.build_release_impact import changed_paths_from_git

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
                build_fixture_plans(load_fixture(fixture_path))
            )
            if args.write_golden:
                _write_atomic(golden_path, rendered)
                return 0
            try:
                current = golden_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                print("Verification Plan Golden 缺失", file=sys.stderr)
                return 1
            if current != rendered:
                print("Verification Plan Golden 已漂移", file=sys.stderr)
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
            raise VerificationPlanBuildError(
                "必须提供 --path、--base 或 Golden 模式"
            )
        impact = build_release_impact_report(paths)
        if args.strict:
            impact.require_owned()
        plan = build_verification_plan(
            impact,
            feature_ids=args.feature,
            artifact_profile_ids=args.artifact_profile,
        )
        print(render_json(plan.to_dict()), end="")
        return 0
    except (
        RegistryError,
        ReleaseImpactError,
        VerificationPlanBuildError,
        VerificationPlanError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
