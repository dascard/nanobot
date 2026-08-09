"""Skill 经验候选的纯离线 CLI。

只读取显式传入的脱敏 Run Viewer、规格和评测证据文件；不调用模型、网络、
生产数据库、工具或 Git，也不写正式 Skill Registry。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence

from core.runtime_paths import RUNTIME_PATHS
from core.skill_candidates import (
    SkillCandidateContractError,
    SkillCandidateEvaluationEvidence,
    SkillCandidateStore,
    SkillDraftSpec,
    extract_skill_candidate,
    skill_candidate_catalog_payload,
)
from evals.harness_registry import EVAL_HARNESS_REGISTRY


_MAX_FILE_BYTES = 8 * 1024 * 1024


class SkillCandidateCliError(ValueError):
    """离线 Skill 候选 CLI 输入无效。"""


def _read_json(path: str | Path) -> object:
    resolved = Path(path).resolve(strict=False)
    try:
        metadata = resolved.lstat()
    except FileNotFoundError as exc:
        raise SkillCandidateCliError(f"文件不存在: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SkillCandidateCliError(f"文件必须是普通文件: {path}")
    if metadata.st_size > _MAX_FILE_BYTES:
        raise SkillCandidateCliError(f"文件超过 8 MiB: {path}")
    try:
        return json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillCandidateCliError(f"JSON 文件无效: {path}") from exc


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SkillCandidateCliError(f"{name} 必须是 JSON 对象")
    return value


def _extra_evidence(value: object) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    payload = _mapping(value, "extra evidence")
    result: dict[str, tuple[str, ...]] = {}
    for run_id, raw_hashes in payload.items():
        if not isinstance(run_id, str):
            raise SkillCandidateCliError("extra evidence 的 Run ID 必须是字符串")
        if not isinstance(raw_hashes, Sequence) or isinstance(raw_hashes, (str, bytes)):
            raise SkillCandidateCliError("extra evidence 的值必须是 SHA-256 数组")
        result[run_id] = tuple(str(item) for item in raw_hashes)
    return result


def _store(root: str) -> SkillCandidateStore:
    return SkillCandidateStore(
        Path(root) if root else RUNTIME_PATHS.skill_candidate_dir
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从脱敏 trajectory 离线提取并门禁 Skill 候选",
    )
    parser.add_argument(
        "--root",
        default="",
        help="候选隔离存储目录；默认使用 NANOBOT_DATA_DIR/evals/skill_candidates",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("catalog", help="输出候选治理合同")

    extract = subparsers.add_parser("extract", help="离线提取 Skill 候选")
    extract.add_argument("--spec", required=True, help="Skill 草案规格 JSON")
    extract.add_argument(
        "--run-view",
        action="append",
        required=True,
        help="脱敏 Run Viewer JSON；可重复指定 2..20 次",
    )
    extract.add_argument(
        "--extra-evidence",
        default="",
        help="可选的 Run ID 到额外证据摘要数组 JSON",
    )

    gate = subparsers.add_parser("gate", help="执行独立阻断门禁")
    gate.add_argument("--evidence", required=True, help="独立评测证据 JSON")

    get_candidate = subparsers.add_parser("get", help="读取不可变候选")
    get_candidate.add_argument("candidate_sha256")
    subparsers.add_parser("state", help="输出候选区安全状态")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "catalog":
            result = {
                **skill_candidate_catalog_payload(),
                "harness_registry": {
                    "namespace": EVAL_HARNESS_REGISTRY.namespace,
                    "generation": EVAL_HARNESS_REGISTRY.generation,
                    "sha256": EVAL_HARNESS_REGISTRY.sha256,
                },
            }
        elif args.command == "extract":
            spec = SkillDraftSpec.from_dict(_read_json(args.spec))
            views = [
                dict(_mapping(_read_json(path), f"run viewer {path}"))
                for path in args.run_view
            ]
            extra = (
                _extra_evidence(_read_json(args.extra_evidence))
                if args.extra_evidence
                else {}
            )
            candidate = extract_skill_candidate(
                views,
                spec=spec,
                extra_evidence_by_run=extra,
            )
            result = _store(args.root).put_candidate(candidate)
        elif args.command == "gate":
            evidence = SkillCandidateEvaluationEvidence.from_dict(
                _read_json(args.evidence)
            )
            result = _store(args.root).evaluate(
                evidence,
                current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
            )
        elif args.command == "get":
            result = _store(args.root).get_candidate(
                args.candidate_sha256
            ).to_dict()
        else:
            result = _store(args.root).state()
    except (
        SkillCandidateCliError,
        SkillCandidateContractError,
    ) as exc:
        print(json.dumps(
            {"ok": False, "error": str(exc)},
            ensure_ascii=False,
            sort_keys=True,
        ), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SkillCandidateCliError", "main"]
