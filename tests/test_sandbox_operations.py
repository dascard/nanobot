"""Sandbox 阶段 5/6 运维脚本的失败关闭与非破坏性约束。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAMES = (
    "sandbox-smoke-test.sh",
    "check-sandbox-data-disk.sh",
    "assign-sandbox-project-quota.sh",
    "cleanup-sandbox-runtime.sh",
    "sandbox-coordinated-backup.sh",
)


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
def test_sandbox_operation_script_has_valid_bash_and_help(script_name):
    script = REPO_ROOT / "scripts" / script_name
    assert script.stat().st_mode & 0o111
    syntax = subprocess.run(
        ["bash", "-n", str(script)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr
    help_result = subprocess.run(
        [str(script), "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "用法" in help_result.stdout


def test_data_disk_gate_rejects_root_mount_without_writes():
    result = subprocess.run(
        [str(REPO_ROOT / "scripts/check-sandbox-data-disk.sh"), "/"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "根挂载点" in result.stderr


def test_quota_script_rejects_invalid_workspace_before_disk_actions():
    result = subprocess.run(
        [
            str(REPO_ROOT / "scripts/assign-sandbox-project-quota.sh"),
            "--workspace-id",
            "../../etc",
            "--project-id",
            "10000",
            "--quota-bytes",
            "2147483648",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "规范 UUID" in result.stderr


def test_operation_scripts_contain_no_global_prune_or_sudo_execution():
    forbidden_patterns = (
        r"\bdocker\s+system\s+prune\b",
        r"\bdocker\s+image\s+prune\b",
        r"\bdocker\s+volume\s+prune\b",
        r"\bdocker\s+compose\s+down\s+-v\b",
        r"(^|\n)\s*sudo\s+",
    )
    for script_name in SCRIPT_NAMES:
        content = (REPO_ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            assert re.search(pattern, content) is None, (script_name, pattern)


def test_runtime_cleanup_service_requires_one_time_maintenance_approval():
    service = (
        REPO_ROOT
        / "deploy/systemd/nanobot-sandbox-runtime-cleanup.service"
    ).read_text(encoding="utf-8")
    assert "ConditionPathIsMountPoint=/srv/nanobot" in service
    assert "ConditionPathExists=/run/nanobot-sandboxd/runtime-cleanup-approved" in service
    assert "--quiesced --apply" in service
    assert "ExecStartPre=/usr/bin/rm -f" in service


def test_storage_templates_require_project_quota_mount_options():
    xfs = (
        REPO_ROOT / "deploy/storage/sandbox-xfs-prjquota.example"
    ).read_text(encoding="utf-8")
    ext4 = (
        REPO_ROOT / "deploy/storage/sandbox-ext4-project-quota.example"
    ).read_text(encoding="utf-8")
    assert " xfs defaults,nodev,nosuid,prjquota " in xfs
    assert " ext4 defaults,nodev,nosuid,prjquota " in ext4
    assert "mkfs.ext4 -O project" in ext4


def test_backup_and_rollback_docs_preserve_persistent_data():
    backup = (REPO_ROOT / "docs/sandbox-backup-restore.md").read_text(
        encoding="utf-8",
    )
    rollback = (REPO_ROOT / "docs/sandbox-rollout-rollback.md").read_text(
        encoding="utf-8",
    )
    assert "runtime" in backup
    assert "明确不备份" in backup
    assert "删除 `/srv/nanobot`" in rollback
    assert "全局 Docker prune" in rollback
