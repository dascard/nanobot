from __future__ import annotations

import hashlib
from pathlib import Path
import sys


def _write_evidence(root: Path) -> dict[str, Path]:
    paths = {
        "python_lock": root / "requirements-prod.lock",
        "web_lock": root / "webui/package-lock.json",
        "prompt_defaults": root / "prompts.v2.default",
        "sbom": root / "artifacts/runtime.spdx.json",
        "verification": root / "artifacts/verification.json",
    }
    paths["python_lock"].write_text("httpx==1\n", encoding="utf-8")
    paths["web_lock"].parent.mkdir()
    paths["web_lock"].write_text('{"lockfileVersion":3}\n')
    paths["prompt_defaults"].mkdir()
    (paths["prompt_defaults"] / "system.md").write_text(
        "系统提示词\n",
        encoding="utf-8",
    )
    paths["sbom"].parent.mkdir()
    paths["sbom"].write_text('{"spdxVersion":"SPDX-2.3"}\n')
    paths["verification"].write_text(
        '{"schema_version":2,"source_sha":"'
        + "a" * 40
        + '","suites":{"backend-full":{"run_id":"1",'
        '"job":"backend","conclusion":"success"}}}\n'
    )
    return paths


def test_manifest_cli_builds_artifact_release_and_validates_target(
    tmp_path: Path,
    monkeypatch,
):
    from core.release.artifacts import (
        load_artifact_manifest,
        load_release_manifest,
    )
    from scripts.build_release_manifest import main

    evidence = _write_evidence(tmp_path)
    artifact_path = tmp_path / "out/runtime-artifact.json"
    release_path = tmp_path / "out/release.json"
    image_reference = (
        "registry.example/nanobot@sha256:" + "c" * 64
    )

    artifact_args = [
        "artifact",
        "--root",
        str(tmp_path),
        "--profile",
        "nanobot-runtime",
        "--git-full-commit",
        "a" * 40,
        "--git-dirty",
        "false",
        "--input",
        "python_lock=requirements-prod.lock",
        "--input",
        "prompt_defaults=prompts.v2.default",
        "--input",
        "web_lock=webui/package-lock.json",
        "--input-sha",
        "build_context=" + "e" * 64,
        "--schema-migration-head",
        "20260723_release_manifest",
        "--image-reference",
        image_reference,
        "--image-id",
        "sha256:" + "d" * 64,
        "--sbom-file",
        str(evidence["sbom"]),
        "--dependency-manifest-file",
        str(evidence["python_lock"]),
        "--verification-suite",
        "backend-full",
        "--verification-results",
        str(evidence["verification"]),
        "--built-at",
        "2026-07-23T12:00:00+00:00",
        "--output",
        str(artifact_path),
    ]
    monkeypatch.setitem(sys.modules, "core.release.runtime_verify", None)
    assert main(artifact_args) == 0

    artifact = load_artifact_manifest(artifact_path)
    assert artifact.input_hashes["python_lock"] == hashlib.sha256(
        evidence["python_lock"].read_bytes()
    ).hexdigest()
    assert artifact.input_hashes["build_context"] == "e" * 64
    assert artifact.sbom_path == "artifacts/runtime.spdx.json"
    assert artifact.sbom_sha256 == hashlib.sha256(
        evidence["sbom"].read_bytes()
    ).hexdigest()
    assert artifact.dependency_manifest_path == (
        "requirements-prod.lock"
    )
    assert artifact.dependency_manifest_sha256 == hashlib.sha256(
        evidence["python_lock"].read_bytes()
    ).hexdigest()
    assert artifact.verification_results_path == "artifacts/verification.json"

    assert main([
        "release",
        "--artifact",
        str(artifact_path),
        "--created-at",
        "2026-07-23T12:01:00+00:00",
        "--output",
        str(release_path),
    ]) == 0
    release = load_release_manifest(release_path)
    assert release.runtime_artifact == artifact

    assert main([
        "validate",
        "--root",
        str(tmp_path),
        "--manifest",
        str(release_path),
        "--runtime-image",
        image_reference,
        "--require-built",
    ]) == 0
    assert main([
        "validate",
        "--root",
        str(tmp_path),
        "--manifest",
        str(release_path),
        "--runtime-image",
        "registry.example/other@sha256:" + "e" * 64,
        "--require-built",
    ]) == 2


def test_manifest_cli_rejects_duplicate_inputs_and_symlinks(
    tmp_path: Path,
):
    from scripts.build_release_manifest import main

    evidence = _write_evidence(tmp_path)
    symlink = tmp_path / "linked.lock"
    symlink.symlink_to(evidence["python_lock"])
    common = [
        "artifact",
        "--root",
        str(tmp_path),
        "--profile",
        "nanobot-runtime",
        "--git-full-commit",
        "a" * 40,
        "--git-dirty",
        "false",
        "--schema-migration-head",
        "20260723_release_manifest",
        "--image-reference",
        "registry.example/nanobot@sha256:" + "c" * 64,
        "--image-id",
        "sha256:" + "d" * 64,
        "--sbom-file",
        str(evidence["sbom"]),
        "--dependency-manifest-file",
        str(evidence["python_lock"]),
        "--verification-suite",
        "backend-full",
        "--verification-results",
        str(evidence["verification"]),
        "--built-at",
        "2026-07-23T12:00:00+00:00",
        "--output",
        str(tmp_path / "artifact.json"),
    ]

    assert main([
        *common,
        "--input",
        "python_lock=requirements-prod.lock",
        "--input",
        "python_lock=prompts.v2.default",
    ]) == 2
    assert main([
        *common,
        "--input",
        "python_lock=linked.lock",
    ]) == 2
