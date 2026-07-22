from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "manage-sandbox-production.sh"


def _run_update_release_harness(
    tmp_path: Path,
    *arguments: str,
    advanced_stage: str = "",
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("update_release_command() {")
    end = source.index("\nassert_data_device_not_root_chain() {", start)
    update_release_function = source[start:end]

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "image-built").write_text("image\n", encoding="utf-8")
    (state_dir / "smoke-passed").write_text("smoke\n", encoding="utf-8")
    if advanced_stage:
        (state_dir / advanced_stage).write_text("advanced\n", encoding="utf-8")
    config_capture = tmp_path / "config.capture"
    smoke_check = tmp_path / "smoke.checked"
    previous_release = "1" * 40
    new_release = "2" * 40
    harness = f"""
set -Eeuo pipefail
STATE_DIR={shlex.quote(str(state_dir))}
CONFIG_CAPTURE={shlex.quote(str(config_capture))}
SMOKE_CHECK={shlex.quote(str(smoke_check))}
RELEASE={previous_release}
VERSION=existing-version
SANDBOX_IMAGE=nanobot-sandbox-python:existing-version
NEW_RELEASE={new_release}
ORIGIN_RELEASE={"3" * 40}
require_root() {{ :; }}
load_config() {{ :; }}
require_stage() {{ stage_exists "$1" || die "missing stage: $1"; }}
stage_exists() {{ [[ -f "${{STATE_DIR}}/$1" && ! -L "${{STATE_DIR}}/$1" ]]; }}
assert_smoke_current() {{ printf 'checked\n' >"${{SMOKE_CHECK}}"; }}
assert_no_advanced_stages() {{ die "unexpected non-reuse path"; }}
image_id_from_stage() {{ printf 'sha256:%064d\n' 0; }}
validate_config_values() {{ :; }}
write_config() {{ printf '%s\n%s\n' "${{RELEASE}}" "${{VERSION}}" >"${{CONFIG_CAPTURE}}"; }}
log() {{ :; }}
die() {{ printf '%s\n' "$*" >&2; exit 1; }}
repo_git() {{
  if [[ "$1" == "cat-file" ]]; then return 0; fi
  if [[ "$1" == "rev-parse" && "$2" == "HEAD" ]]; then printf '%s\n' "${{NEW_RELEASE}}"; return 0; fi
  if [[ "$1" == "rev-parse" && "$2" == "origin/master" ]]; then printf '%s\n' "${{ORIGIN_RELEASE}}"; return 0; fi
  if [[ "$1" == "status" && "$2" == "--porcelain" ]]; then return 0; fi
  if [[ "$1" == "merge-base" ]]; then printf 'MERGE_BASE %s %s\n' "$3" "$4"; return 0; fi
  if [[ "$1" == "diff" ]]; then return 0; fi
  die "unexpected repo_git call: $*"
}}
{update_release_function}
update_release_command update-release "$@"
"""
    result = subprocess.run(
        ["bash", "-c", harness, "update-release-harness", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, state_dir, config_capture, smoke_check


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


def test_release_gate_pins_published_ancestor_instead_of_remote_tip():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'repo_git merge-base --is-ancestor "${RELEASE}" origin/master' in source
    assert (
        'repo_git merge-base --is-ancestor "${new_release}" origin/master'
        in source
    )
    assert 'repo_git rev-parse origin/master)" == "${RELEASE}"' not in source
    assert 'repo_git rev-parse origin/master)" == "${new_release}"' not in source


def test_update_release_can_archive_completed_smoke_for_explicit_rerun():
    source = SCRIPT.read_text(encoding="utf-8")
    help_result = subprocess.run(
        [str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert help_result.returncode == 0, help_result.stderr
    assert "--rerun-smoke" in help_result.stdout
    assert "--rerun-smoke 只能与 --reuse-built-image 同时使用" in source
    assert "assert_smoke_current" in source
    assert 'smoke_stage_present="true"' in source
    assert '${STATE_DIR}/smoke-passed.superseded-' in source
    assert 'mv -- "${STATE_DIR}/smoke-passed" "${archived_smoke_stage}"' in source
    assert "新 RELEASE 必须重新运行真实 Docker Smoke" in source


def test_update_release_rerun_smoke_archives_marker_before_writing_release(tmp_path):
    new_release = "2" * 40
    previous_release = "1" * 40
    result, state_dir, config_capture, smoke_check = _run_update_release_harness(
        tmp_path,
        "--release",
        new_release,
        "--reuse-built-image",
        "--rerun-smoke",
    )

    archived = state_dir / (
        f"smoke-passed.superseded-{previous_release}-by-{new_release}"
    )
    assert result.returncode == 0, result.stderr
    assert f"MERGE_BASE {new_release} origin/master" in result.stdout
    assert not (state_dir / "smoke-passed").exists()
    assert archived.read_text(encoding="utf-8") == "smoke\n"
    assert smoke_check.read_text(encoding="utf-8") == "checked\n"
    assert config_capture.read_text(encoding="utf-8") == (
        f"{new_release}\nexisting-version\n"
    )


def test_update_release_preserves_smoke_marker_without_explicit_rerun(tmp_path):
    new_release = "2" * 40
    result, state_dir, config_capture, smoke_check = _run_update_release_harness(
        tmp_path,
        "--release",
        new_release,
        "--reuse-built-image",
    )

    assert result.returncode != 0
    assert "必须显式增加 --rerun-smoke" in result.stderr
    assert (state_dir / "smoke-passed").read_text(encoding="utf-8") == "smoke\n"
    assert not config_capture.exists()
    assert not smoke_check.exists()


def test_update_release_rerun_smoke_refuses_completed_control_plane(tmp_path):
    new_release = "2" * 40
    result, state_dir, config_capture, smoke_check = _run_update_release_harness(
        tmp_path,
        "--release",
        new_release,
        "--reuse-built-image",
        "--rerun-smoke",
        advanced_stage="control-plane-ready",
    )

    assert result.returncode != 0
    assert "control-plane-ready" in result.stderr
    assert (state_dir / "smoke-passed").read_text(encoding="utf-8") == "smoke\n"
    assert not config_capture.exists()
    assert not smoke_check.exists()


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
