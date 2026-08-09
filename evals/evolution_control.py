"""受控自进化离线入口。

该入口只冻结数据、登记不可变候选并验证外部评测证据；不会调用模型、网络、
生产数据或 Git，也不会写入主干源码。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence

from core.evolution_control import (
    DATASET_SPLIT_ROLES,
    EvolutionCandidateBundle,
    EvolutionContractError,
    EvolutionControlStore,
    EvolutionGateEvidence,
    EvolutionGenerationProof,
    EvolutionTarget,
    FrozenDatasetManifest,
    FrozenDatasetSplit,
    evolution_catalog_payload,
)
from core.evolution_control.contracts import canonical_json
from core.runtime_paths import RUNTIME_PATHS
from evals.harness_registry import EVAL_HARNESS_REGISTRY


_MAX_SPEC_BYTES = 2 * 1024 * 1024
_MAX_DATASET_BYTES = 64 * 1024 * 1024


class EvolutionCliError(ValueError):
    """离线入口参数或数据文件无效。"""


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvolutionCliError(f"{name} 必须是 JSON 对象")
    if any(not isinstance(key, str) for key in value):
        raise EvolutionCliError(f"{name} 的键必须是字符串")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise EvolutionCliError(f"{name} 必须是 JSON 数组")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    *,
    name: str,
    required: frozenset[str],
) -> None:
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required)
    if missing:
        raise EvolutionCliError(f"{name} 缺少字段: {', '.join(missing)}")
    if unknown:
        raise EvolutionCliError(
            f"{name} 包含未允许字段: {', '.join(unknown)}"
        )


def _read_regular_file(path: Path, *, maximum_bytes: int) -> bytes:
    resolved = path.resolve(strict=False)
    try:
        metadata = resolved.lstat()
    except FileNotFoundError as exc:
        raise EvolutionCliError(f"文件不存在: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise EvolutionCliError(f"文件必须是普通文件: {path}")
    if metadata.st_size > maximum_bytes:
        raise EvolutionCliError(f"文件超过 {maximum_bytes} 字节: {path}")
    try:
        return resolved.read_bytes()
    except OSError as exc:
        raise EvolutionCliError(f"无法读取文件: {path}") from exc


def read_json_object(path: str | Path) -> dict[str, Any]:
    raw = _read_regular_file(Path(path), maximum_bytes=_MAX_SPEC_BYTES)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvolutionCliError(f"JSON 文件无效: {path}") from exc
    return dict(_mapping(value, str(path)))


def _dataset_case_count(raw: bytes, path: Path) -> int:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        count = 0
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise EvolutionCliError(f"数据集不是 UTF-8: {path}") from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvolutionCliError(
                    f"JSONL 第 {line_number} 行无效: {path}"
                ) from exc
            _mapping(item, f"{path}:{line_number}")
            count += 1
        if count < 1:
            raise EvolutionCliError(f"数据集没有样本: {path}")
        return count

    if isinstance(value, list):
        cases = value
    elif isinstance(value, Mapping) and set(value) == {"cases"}:
        cases = list(_sequence(value["cases"], f"{path}.cases"))
    else:
        raise EvolutionCliError(
            f"数据集必须是 JSON 数组、仅含 cases 的对象或 JSONL: {path}"
        )
    if not cases:
        raise EvolutionCliError(f"数据集没有样本: {path}")
    for index, item in enumerate(cases):
        _mapping(item, f"{path}[{index}]")
    return len(cases)


def freeze_dataset_from_spec(
    spec: object,
    *,
    spec_directory: str | Path,
    store: EvolutionControlStore,
) -> FrozenDatasetManifest:
    payload = _mapping(spec, "dataset spec")
    _exact_keys(
        payload,
        name="dataset spec",
        required=frozenset({
            "schema_version",
            "dataset_id",
            "revision",
            "source_revision",
            "created_at",
            "splits",
        }),
    )
    split_specs = _sequence(payload["splits"], "dataset spec.splits")
    base = Path(spec_directory).resolve(strict=False)
    splits: list[FrozenDatasetSplit] = []
    for raw_split in split_specs:
        split = _mapping(raw_split, "dataset split spec")
        _exact_keys(
            split,
            name="dataset split spec",
            required=frozenset({
                "role",
                "path",
                "source_id",
                "revision",
                "license_id",
                "expected_count",
            }),
        )
        role = str(split["role"] or "")
        raw_path = str(split["path"] or "")
        if not raw_path or "\x00" in raw_path:
            raise EvolutionCliError("dataset split path 无效")
        path = Path(raw_path)
        if not path.is_absolute():
            path = base / path
        artifact = _read_regular_file(path, maximum_bytes=_MAX_DATASET_BYTES)
        actual_count = _dataset_case_count(artifact, path)
        expected_count = split["expected_count"]
        if type(expected_count) is not int or expected_count != actual_count:
            raise EvolutionCliError(
                f"{role or 'unknown'} expected_count={expected_count!r}，"
                f"实际为 {actual_count}"
            )
        splits.append(FrozenDatasetSplit(
            role=role,
            source_id=split["source_id"],
            revision=split["revision"],
            license_id=split["license_id"],
            artifact_sha256=hashlib.sha256(artifact).hexdigest(),
            expected_count=actual_count,
            answers_visible_to_generator=role in {"baseline", "training"},
        ))
    manifest = FrozenDatasetManifest(
        schema_version=payload["schema_version"],
        dataset_id=payload["dataset_id"],
        revision=payload["revision"],
        source_revision=payload["source_revision"],
        created_at=payload["created_at"],
        splits=tuple(splits),
    )
    if tuple(item.role for item in manifest.splits) != DATASET_SPLIT_ROLES:
        raise EvolutionCliError("必须冻结完整的四类数据集")
    store.put_dataset(manifest)
    return manifest


def generate_candidate_from_spec(
    spec: object,
    *,
    dataset_sha256: str,
    store: EvolutionControlStore,
) -> EvolutionCandidateBundle:
    payload = _mapping(spec, "candidate spec")
    _exact_keys(
        payload,
        name="candidate spec",
        required=frozenset({
            "schema_version",
            "candidate_id",
            "created_at",
            "generation",
            "target",
            "rationale",
            "evidence_sha256s",
            "repository_operations",
        }),
    )
    dataset = store.get_dataset(dataset_sha256)
    generation = EvolutionGenerationProof.from_dict(payload["generation"])
    if generation.source_revision != dataset.source_revision:
        raise EvolutionCliError("候选生成 revision 与冻结数据集不一致")
    candidate = EvolutionCandidateBundle(
        schema_version=payload["schema_version"],
        candidate_id=payload["candidate_id"],
        created_at=payload["created_at"],
        dataset_sha256=dataset.dataset_sha256,
        generation=generation,
        target=EvolutionTarget.from_dict(payload["target"]),
        rationale=payload["rationale"],
        evidence_sha256s=tuple(
            _sequence(
                payload["evidence_sha256s"],
                "candidate spec.evidence_sha256s",
            )
        ),
        repository_operations=payload["repository_operations"],
    )
    store.put_candidate(candidate)
    return candidate


def gate_candidate_from_evidence(
    evidence_payload: object,
    *,
    store: EvolutionControlStore,
) -> dict[str, object]:
    evidence = EvolutionGateEvidence.from_dict(evidence_payload)
    return store.evaluate_gate(
        evidence,
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )


def catalog_payload() -> dict[str, object]:
    return {
        **evolution_catalog_payload(),
        "harness_registry": {
            "namespace": EVAL_HARNESS_REGISTRY.namespace,
            "generation": EVAL_HARNESS_REGISTRY.generation,
            "sha256": EVAL_HARNESS_REGISTRY.sha256,
        },
    }


def _emit(value: object, output: str) -> None:
    encoded = (canonical_json(value) + "\n").encode("utf-8")
    if output == "-":
        sys.stdout.buffer.write(encoded)
        return
    path = Path(output).resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise EvolutionCliError(f"输出文件已存在: {path}") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="受控自进化离线控制面")
    parser.add_argument(
        "--store-root",
        type=Path,
        default=RUNTIME_PATHS.evolution_control_dir,
        help="不可变 Artifact 存储目录",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog = subparsers.add_parser("catalog", help="输出允许和禁止的能力边界")
    catalog.add_argument("--output", default="-")

    freeze = subparsers.add_parser("freeze-dataset", help="冻结四类数据集")
    freeze.add_argument("--spec", type=Path, required=True)
    freeze.add_argument("--output", default="-")

    generate = subparsers.add_parser(
        "generate-candidate",
        help="从离线规范登记不可变候选",
    )
    generate.add_argument("--spec", type=Path, required=True)
    generate.add_argument("--dataset-sha256", required=True)
    generate.add_argument("--output", default="-")

    gate = subparsers.add_parser("gate", help="执行安全、成本和质量门禁")
    gate.add_argument("--evidence", type=Path, required=True)
    gate.add_argument("--output", default="-")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    store = EvolutionControlStore(args.store_root)
    try:
        if args.command == "catalog":
            result: object = catalog_payload()
        elif args.command == "freeze-dataset":
            result = freeze_dataset_from_spec(
                read_json_object(args.spec),
                spec_directory=args.spec.parent,
                store=store,
            ).to_dict()
        elif args.command == "generate-candidate":
            result = generate_candidate_from_spec(
                read_json_object(args.spec),
                dataset_sha256=args.dataset_sha256,
                store=store,
            ).to_dict()
        else:
            result = gate_candidate_from_evidence(
                read_json_object(args.evidence),
                store=store,
            )
        _emit(result, args.output)
    except (EvolutionCliError, EvolutionContractError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EvolutionCliError",
    "catalog_payload",
    "freeze_dataset_from_spec",
    "gate_candidate_from_evidence",
    "generate_candidate_from_spec",
    "main",
    "read_json_object",
]
