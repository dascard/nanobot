from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "manage-sandbox-production.sh"


def test_retired_owner_commands_fail_closed_without_host_actions():
    for command in (
        "provision-owner",
        "enable-workspace",
        "enable-assets",
        "enable-exec",
        "disable-owner",
    ):
        result = subprocess.run(
            [str(SCRIPT), command],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        combined = f"{result.stdout}\n{result.stderr}"
        assert result.returncode != 0
        assert "已永久停止执行" in combined
        assert "Sandbox 管理" in combined


def test_production_script_no_longer_contains_owner_or_tsv_write_logic():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "SANDBOX_OWNER_ID" not in source
    assert "SANDBOX_PLATFORM" not in source
    assert "PROJECT_MAP" not in source
    assert "record_project_mapping" not in source
    assert "apply_tool_override" not in source
    assert "--owner-id)" not in source
    assert "--project-id)" not in source


def test_production_script_requires_explicit_local_same_disk_risk_marker():
    syntax = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    help_result = subprocess.run(
        [str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    source = SCRIPT.read_text(encoding="utf-8")

    assert syntax.returncode == 0, syntax.stderr
    assert help_result.returncode == 0, help_result.stderr
    assert "--backup-mode <模式>" in help_result.stdout
    assert "local_same_disk" in help_result.stdout
    assert "--accept-local-same-disk-risk" in help_result.stdout
    assert (
        'LOCAL_SAME_DISK_RISK_MARKER="single_disk_logical_rollback_only"'
        in source
    )
    assert 'BACKUP_MAX_BYTES_FIXED="17179869184"' in source
    assert "根文件系统最低保留空间不得低于 60 GiB" in source


def test_production_script_guards_real_loopback_preallocation():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'mkfs.xfs -K -L "${XFS_LABEL}" "${loop_device}"' in source
    assert "check-loopback-image-allocation.sh" in source
    assert "--repair-loopback-allocation" in source
    assert "REPAIR LOOPBACK ALLOCATION" in source
    assert "update-release" in source
    assert "X-fstrim.notrim" in source


def test_update_release_can_reuse_unchanged_image_before_smoke():
    source = SCRIPT.read_text(encoding="utf-8")
    help_result = subprocess.run(
        [str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert help_result.returncode == 0, help_result.stderr
    assert "--reuse-built-image" in help_result.stdout
    assert "repo_git merge-base --is-ancestor" in source
    assert "scripts/build-sandbox-image.sh" in source
    assert "docker/sandbox/python" in source
    assert 'VERSION="${previous_version}"' in source
    assert "smoke-passed" in source
    assert "control-plane-ready" in source
    assert "runtime-deployed" in source


def test_control_plane_probe_has_bounded_uds_startup_retry():
    source = SCRIPT.read_text(encoding="utf-8")
    probe_body = source.split("probe_sandboxd() {", 1)[1].split(
        "\ninstall_control_plane_command() {",
        1,
    )[0]

    assert "time.monotonic()" in probe_body
    assert "FileNotFoundError" in probe_body
    assert "ConnectionRefusedError" in probe_body
    assert "socket.timeout" in probe_body
    assert "b\" 503 \"" in probe_body
    assert "time.sleep(0.1)" in probe_body
