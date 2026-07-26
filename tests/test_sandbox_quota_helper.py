from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sandboxd.quota import ProjectQuotaManager
from tests.test_sandboxd_api import WORKSPACE_ID


MIB = 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "assign-sandbox-project-quota.sh"


def _confirmation(*, scope: str, project_id: int, quota_bytes: int) -> str:
    return (
        "project_quota_verified=true\n"
        f"scope={scope}\n"
        f"project_id={project_id}\n"
        f"quota_bytes={quota_bytes}\n"
    )


def test_quota_helper_only_checks_the_target_workspace_container_label():
    source = HELPER.read_text(encoding="utf-8")

    assert (
        '--filter "label=com.nanobot.workspace-id=${workspace_id}"'
        in source
    )
    assert "--filter 'label=com.nanobot.sandbox=true'" in source
    assert "--filter 'label=com.nanobot.managed-by=sandboxd'" in source
    assert "docker ps -a" not in source
    assert subprocess.run(
        ["bash", "-n", os.fspath(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0


def test_quota_inspect_uses_fixed_targeted_verify_argv(tmp_path):
    data_root = tmp_path / "data"
    helper_path = tmp_path / "fixed-quota-helper"
    helper_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper_path.chmod(0o700)
    calls: list[tuple[tuple[str, ...], float]] = []

    def command(argv, *, timeout):
        calls.append((tuple(argv), timeout))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=_confirmation(
                scope="workspace",
                project_id=10000,
                quota_bytes=64 * MIB,
            ),
            stderr="",
        )

    manager = ProjectQuotaManager(
        data_root=data_root,
        helper_path=helper_path,
        command=command,
    )
    manager.layout.ensure_roots()
    manager.layout.ensure_workspace(WORKSPACE_ID)

    result = manager.inspect(
        workspace_id=WORKSPACE_ID,
        project_id=10000,
        quota_bytes=64 * MIB,
        generation=7,
        scope="workspace",
    )

    assert result["project_id_matches"] is True
    assert result["quota_bytes_matches"] is True
    assert result["verified"] is True
    assert calls == [(
        (
            os.fspath(helper_path),
            "--workspace-id",
            WORKSPACE_ID,
            "--scope",
            "workspace",
            "--project-id",
            "10000",
            "--quota-bytes",
            str(64 * MIB),
            "--data-root",
            os.fspath(data_root),
            "--quiesced",
            "--verify",
        ),
        15.0,
    )]


def test_quota_confirmation_must_match_both_project_and_hard_limit(tmp_path):
    data_root = tmp_path / "data"
    helper_path = tmp_path / "fixed-quota-helper"
    helper_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper_path.chmod(0o700)

    def command(argv, *, timeout):
        del timeout
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=_confirmation(
                scope="workspace",
                project_id=10000,
                quota_bytes=32 * MIB,
            ),
            stderr="",
        )

    manager = ProjectQuotaManager(
        data_root=data_root,
        helper_path=helper_path,
        command=command,
    )
    manager.layout.ensure_roots()
    manager.layout.ensure_workspace(WORKSPACE_ID)

    result = manager.inspect(
        workspace_id=WORKSPACE_ID,
        project_id=10000,
        quota_bytes=64 * MIB,
        generation=7,
        scope="workspace",
    )

    assert result["project_id_matches"] is False
    assert result["quota_bytes_matches"] is False
    assert result["verified"] is False
