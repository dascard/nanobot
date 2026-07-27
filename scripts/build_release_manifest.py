#!/usr/bin/env python3
"""生成、组合和校验版本化 Artifact／Release Manifest。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ManifestBuildError(RuntimeError):
    """构建证据缺失或路径不安全。"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_repository_path(root: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ManifestBuildError(
            f"构建输入不能是符号链接：{value}"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ManifestBuildError(
            f"构建输入不存在：{value}"
        ) from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ManifestBuildError(
            f"构建输入必须位于仓库内：{value}"
        ) from exc
    return resolved


def hash_repository_path(root: Path, value: str) -> str:
    """对普通文件或无符号链接目录生成路径敏感的稳定 Hash。"""

    resolved = _safe_repository_path(root, value)
    if resolved.is_file():
        return _sha256_file(resolved)
    if not resolved.is_dir():
        raise ManifestBuildError(
            f"构建输入不是普通文件或目录：{value}"
        )
    digest = hashlib.sha256()
    for path in sorted(resolved.rglob("*")):
        if path.is_symlink():
            raise ManifestBuildError(
                f"构建输入目录包含符号链接：{value}"
            )
        if not path.is_file():
            continue
        relative = path.relative_to(resolved).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _evidence_file(root: Path, value: str) -> Path:
    path = _safe_repository_path(root, value)
    if not path.is_file():
        raise ManifestBuildError(
            f"证据路径必须是普通文件：{value}"
        )
    return path


def _logical_reference(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _git(
    root: Path,
    arguments: Sequence[str],
    *,
    allow_empty: bool = False,
) -> str:
    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManifestBuildError("无法读取 Git 构建身份") from exc
    value = completed.stdout.strip()
    if not value and not allow_empty:
        raise ManifestBuildError("Git 构建身份为空")
    return value


def _resolve_source(args: argparse.Namespace, root: Path):
    from core.release.artifacts import ArtifactSource

    git_full_commit = (
        args.git_full_commit
        or _git(root, ("rev-parse", "HEAD"))
    )
    if args.git_dirty is None:
        status = _git(
            root,
            ("status", "--porcelain", "--untracked-files=normal"),
            allow_empty=True,
        )
        git_dirty = bool(status)
    else:
        git_dirty = args.git_dirty == "true"
    kt_commit = args.kt_commit
    if kt_commit is None:
        kt_commit = _git(
            root / "vendor/KohakuTerrarium",
            ("rev-parse", "HEAD"),
        )
    return ArtifactSource(
        git_full_commit=git_full_commit,
        git_dirty=git_dirty,
        kt_commit=kt_commit,
    )


def _parse_input_hashes(
    root: Path,
    values: Sequence[str],
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise ManifestBuildError(
                "--input 必须使用 name=repository/path"
            )
        if name in hashes:
            raise ManifestBuildError(
                f"重复构建输入名称：{name}"
            )
        hashes[name] = hash_repository_path(root, path)
    return hashes


def _artifact_command(args: argparse.Namespace) -> int:
    from core.release.artifacts import (
        build_artifact_manifest,
        dump_artifact_manifest,
    )

    root = args.root.resolve()
    source = _resolve_source(args, root)
    sbom = _evidence_file(root, args.sbom_file)
    dependency = _evidence_file(
        root,
        args.dependency_manifest_file,
    )
    verification = _evidence_file(
        root,
        args.verification_results,
    )
    schema_head = args.schema_migration_head
    if schema_head is None and args.profile == "nanobot-runtime":
        from core.release.runtime_verify import (
            current_schema_migration_head,
        )

        schema_head = current_schema_migration_head()
    artifact = build_artifact_manifest(
        profile_id=args.profile,
        provenance="built",
        source=source,
        input_hashes=_parse_input_hashes(root, args.input),
        schema_migration_head=schema_head or "",
        oci_image_reference=args.image_reference or "",
        oci_image_id=args.image_id or "",
        sbom_path=_logical_reference(root, sbom),
        sbom_sha256=_sha256_file(sbom),
        dependency_manifest_path=_logical_reference(
            root,
            dependency,
        ),
        dependency_manifest_sha256=_sha256_file(dependency),
        verification_suites=tuple(args.verification_suite),
        verification_results_path=_logical_reference(
            root,
            verification,
        ),
        verification_results_sha256=_sha256_file(verification),
        built_at=(
            args.built_at
            or datetime.now(timezone.utc).isoformat()
        ),
        builder_version=args.builder_version,
    )
    dump_artifact_manifest(args.output, artifact)
    print(f"ArtifactManifest 已生成：{artifact.sha256}")
    return 0


def _release_command(args: argparse.Namespace) -> int:
    from core.release.artifacts import (
        build_release_manifest,
        dump_release_manifest,
        load_artifact_manifest,
    )

    artifacts = tuple(
        load_artifact_manifest(path) for path in args.artifact
    )
    release = build_release_manifest(
        artifacts=artifacts,
        created_at=(
            args.created_at
            or datetime.now(timezone.utc).isoformat()
        ),
    )
    dump_release_manifest(args.output, release)
    print(
        f"ReleaseManifest 已生成：{release.release_id} "
        f"{release.sha256}"
    )
    return 0


def _validate_command(args: argparse.Namespace) -> int:
    from core.release.artifacts import load_release_manifest
    from core.release.production_preflight import (
        ProductionPreflightError,
        validate_release_artifact_evidence,
    )

    release = load_release_manifest(args.manifest)
    runtime = release.runtime_artifact
    if (
        args.runtime_image is not None
        and runtime.oci_image_reference != args.runtime_image
    ):
        raise ManifestBuildError(
            "NANOBOT_RUNTIME_IMAGE 与 ReleaseManifest 不一致"
        )
    if args.require_built and runtime.provenance != "built":
        raise ManifestBuildError(
            "目标 Runtime Artifact 必须是 provenance=built"
        )
    if args.require_built:
        try:
            validate_release_artifact_evidence(args.root.resolve(), runtime)
        except ProductionPreflightError as exc:
            raise ManifestBuildError(str(exc)) from exc
    print(
        f"ReleaseManifest 验证通过：{release.release_id} "
        f"{runtime.oci_image_reference}"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="构建和校验 Nanobot 发布清单"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    artifact = commands.add_parser(
        "artifact",
        help="生成单个 ArtifactManifest",
    )
    artifact.add_argument("--root", type=Path, default=ROOT)
    artifact.add_argument("--profile", required=True)
    artifact.add_argument("--git-full-commit")
    artifact.add_argument(
        "--git-dirty",
        choices=("true", "false"),
    )
    artifact.add_argument("--kt-commit")
    artifact.add_argument("--input", action="append", default=[])
    artifact.add_argument("--schema-migration-head")
    artifact.add_argument("--image-reference")
    artifact.add_argument("--image-id")
    artifact.add_argument("--sbom-file", required=True)
    artifact.add_argument(
        "--dependency-manifest-file",
        required=True,
    )
    artifact.add_argument(
        "--verification-suite",
        action="append",
        default=[],
    )
    artifact.add_argument(
        "--verification-results",
        required=True,
    )
    artifact.add_argument("--built-at")
    artifact.add_argument(
        "--builder-version",
        default="release-manifest-v1",
    )
    artifact.add_argument("--output", type=Path, required=True)
    artifact.set_defaults(handler=_artifact_command)

    release = commands.add_parser(
        "release",
        help="组合 ArtifactManifest 为 ReleaseManifest",
    )
    release.add_argument(
        "--artifact",
        type=Path,
        action="append",
        required=True,
    )
    release.add_argument("--created-at")
    release.add_argument("--output", type=Path, required=True)
    release.set_defaults(handler=_release_command)

    validate = commands.add_parser(
        "validate",
        help="校验 ReleaseManifest 和目标镜像",
    )
    validate.add_argument("--root", type=Path, default=ROOT)
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--runtime-image")
    validate.add_argument("--require-built", action="store_true")
    validate.set_defaults(handler=_validate_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from core.release.artifacts import (
        ArtifactManifestError,
        ReleaseManifestError,
    )

    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (
        ArtifactManifestError,
        ManifestBuildError,
        ReleaseManifestError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
