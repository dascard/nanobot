"""Docker 构建上下文身份与 dirty 判定回归测试。"""

from __future__ import annotations

import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.build_context_manifest import (
    BuildContextManifestError,
    build_context_manifest,
)
from scripts.write_runtime_build_evidence import (
    RuntimeBuildEvidenceError,
    build_runtime_evidence,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_manifest_hashes_only_effective_docker_context_and_lists_untracked(
    tmp_path,
):
    _git(tmp_path, "init", "-q")
    _write(
        tmp_path / ".dockerignore",
        ".git/\ndata/\n*.log\n",
    )
    _write(tmp_path / ".gitignore", "ignored-by-git.py\n")
    _write(tmp_path / "Dockerfile", "FROM scratch\n")
    _write(tmp_path / "tracked.py", "print('tracked')\n")
    _write(tmp_path / "ignored-by-git.py", "print('context input')\n")
    _write(tmp_path / "untracked.py", "print('untracked')\n")
    _write(tmp_path / "debug.log", "ignored\n")
    _write(tmp_path / "data" / "database.db", "ignored\n")
    _git(
        tmp_path,
        "add",
        ".dockerignore",
        ".gitignore",
        "Dockerfile",
        "tracked.py",
    )

    first = build_context_manifest(
        tmp_path,
        include_git_identity=True,
    )
    paths = {str(item["path"]) for item in first["files"]}

    assert paths == {
        ".dockerignore",
        ".gitignore",
        "Dockerfile",
        "ignored-by-git.py",
        "tracked.py",
        "untracked.py",
    }
    assert first["untracked_context_files"] == [
        "ignored-by-git.py",
        "untracked.py",
    ]

    _write(tmp_path / "debug.log", "ignored but changed\n")
    ignored_change = build_context_manifest(
        tmp_path,
        include_git_identity=True,
    )
    assert (
        ignored_change["build_context_sha256"]
        == first["build_context_sha256"]
    )

    _write(tmp_path / "tracked.py", "print('changed')\n")
    included_change = build_context_manifest(
        tmp_path,
        include_git_identity=True,
    )
    assert (
        included_change["build_context_sha256"]
        != first["build_context_sha256"]
    )


def test_manifest_rejects_secret_if_dockerignore_does_not_exclude_it(
    tmp_path,
):
    _write(tmp_path / ".dockerignore", ".git/\n")
    _write(tmp_path / "Dockerfile", "FROM scratch\n")
    _write(tmp_path / ".env", "SECRET=must-not-enter-context\n")

    with pytest.raises(
        BuildContextManifestError,
        match="环境凭据文件进入构建上下文",
    ):
        build_context_manifest(tmp_path)


def test_local_production_build_checks_untracked_context_and_cleanliness():
    script = Path("scripts/docker-build.sh").read_text(encoding="utf-8")

    assert "git status --porcelain --untracked-files=normal" in script
    assert "--include-git-identity" in script
    assert 'PRODUCTION_BUILD=true' in script
    assert '"${GIT_DIRTY}" != "false"' in script
    assert "BUILD_CONTEXT_SHA256" in script


def test_runtime_build_evidence_records_exact_identity_and_blocked_smokes():
    image_id = "sha256:" + "a" * 64
    payload = build_runtime_evidence(Namespace(
        git_full_commit="1" * 40,
        git_dirty="false",
        build_context_sha256="3" * 64,
        build_context_manifest="/evidence/build-context.json",
        image_reference="nanobot-runtime:local",
        image_id=image_id,
        registry_digest="",
        rollback_image_id="sha256:" + "b" * 64,
        built_at="2026-07-29T00:00:00Z",
        deployment_status="deployed",
        service_image=[
            f"nanobot-server={image_id}",
            f"session-summary-worker={image_id}",
            f"outbound-delivery-worker={image_id}",
            f"semantic-index-worker={image_id}",
        ],
    ))

    assert payload["source"]["build_context_sha256"] == "3" * 64
    assert set(payload["runtime_services"].values()) == {image_id}
    assert payload["smoke"]["health_endpoint"] == "PASSED"
    assert payload["smoke"]["agent_link_roundtrip"] == "BLOCKED_NOT_RUN"
    assert payload["smoke"]["sandbox_matrix"] == (
        "NOT_REQUIRED_BY_RUNTIME_BUILD"
    )


def test_runtime_build_evidence_rejects_mixed_service_images():
    with pytest.raises(
        RuntimeBuildEvidenceError,
        match="没有使用同一 IMAGE ID",
    ):
        build_runtime_evidence(Namespace(
            git_full_commit="1" * 40,
            git_dirty="false",
            build_context_sha256="3" * 64,
            build_context_manifest="/evidence/build-context.json",
            image_reference="nanobot-runtime:local",
            image_id="sha256:" + "a" * 64,
            registry_digest="",
            rollback_image_id="",
            built_at="",
            deployment_status="deployed",
            service_image=[
                "nanobot-server=sha256:" + "b" * 64,
            ],
        ))
