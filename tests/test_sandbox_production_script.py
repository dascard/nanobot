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
RELEASE_REF=origin/master
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
write_config() {{ printf '%s\n%s\n%s\n' "${{RELEASE}}" "${{RELEASE_REF}}" "${{VERSION}}" >"${{CONFIG_CAPTURE}}"; }}
log() {{ :; }}
die() {{ printf '%s\n' "$*" >&2; exit 1; }}
assert_release_published() {{ printf 'PUBLISHED %s %s\n' "$1" "$2"; }}
repo_git() {{
  if [[ "$1" == "cat-file" ]]; then return 0; fi
  if [[ "$1" == "rev-parse" && "$2" == "HEAD" ]]; then printf '%s\n' "${{NEW_RELEASE}}"; return 0; fi
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


def _run_release_ref_validation_harness(
    release_ref: str,
) -> subprocess.CompletedProcess[str]:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("validate_release_ref() {")
    end = source.index("\nvalidate_release() {", start)
    validation_function = source[start:end]
    harness = f"""
set -Eeuo pipefail
die() {{ printf '%s\n' "$*" >&2; exit 1; }}
repo_git() {{ git "$@"; }}
{validation_function}
validate_release_ref {shlex.quote(release_ref)}
"""
    return subprocess.run(
        ["bash", "-c", harness],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_promote_release_harness(
    tmp_path: Path,
    *,
    release_ref: str = "origin/release-candidates/sandbox-control-plane",
    runtime_stage: bool = True,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("promote_release_command() {")
    end = source.index("\nassert_data_device_not_root_chain() {", start)
    promote_function = source[start:end]
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    runtime_marker = state_dir / "runtime-deployed"
    if runtime_stage:
        runtime_marker.write_text("runtime\n", encoding="utf-8")
    config_capture = tmp_path / "config.capture"
    release = "6" * 40
    harness = f"""
set -Eeuo pipefail
STATE_DIR={shlex.quote(str(state_dir))}
CONFIG_CAPTURE={shlex.quote(str(config_capture))}
RELEASE={release}
RELEASE_REF={shlex.quote(release_ref)}
require_no_extra_args() {{ shift; (( $# == 0 )); }}
require_root() {{ :; }}
load_config() {{ :; }}
require_stage() {{ [[ -f "${{STATE_DIR}}/$1" ]] || die "missing stage: $1"; }}
assert_release_checkout_current() {{ printf 'CHECKOUT_OK\n'; }}
assert_runtime_current() {{ printf 'RUNTIME_OK\n'; }}
assert_release_published() {{ printf 'PUBLISHED %s %s\n' "$1" "$2"; }}
validate_config_values() {{ :; }}
write_config() {{ printf '%s\n%s\n' "${{RELEASE}}" "${{RELEASE_REF}}" >"${{CONFIG_CAPTURE}}"; }}
log() {{ :; }}
die() {{ printf '%s\n' "$*" >&2; exit 1; }}
{promote_function}
promote_release_command promote-release "$@"
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, config_capture, runtime_marker


def _run_activate_release_tree_harness(
    tmp_path: Path,
    *,
    managed_current: bool = True,
    allow_forward: bool = True,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("activate_release_tree() {")
    end = source.index("\ninstall_sandboxd_python() {", start)
    activate_function = source[start:end]

    releases_root = tmp_path / "releases"
    releases_root.mkdir()
    previous_release = "4" * 40
    target_release = "5" * 40
    previous_dir = (
        releases_root / previous_release
        if managed_current
        else tmp_path / "outside" / previous_release
    )
    previous_dir.mkdir(parents=True)
    (previous_dir / ".nanobot-release").write_text(
        f"{previous_release}\n",
        encoding="utf-8",
    )
    target_dir = releases_root / target_release
    target_dir.mkdir()
    release_link = tmp_path / "nanobot-server"
    release_link.symlink_to(previous_dir)

    harness = f"""
set -Eeuo pipefail
SERVER_RELEASE_LINK={shlex.quote(str(release_link))}
RELEASE_DIR={shlex.quote(str(target_dir))}
RELEASE={target_release}
ALLOW_FORWARD={"true" if allow_forward else "false"}
die() {{ printf '%s\n' "$*" >&2; exit 1; }}
repo_git() {{
  if [[ "$1" == "cat-file" ]]; then return 0; fi
  if [[ "$1" == "merge-base" ]]; then [[ "${{ALLOW_FORWARD}}" == "true" ]]; return; fi
  die "unexpected repo_git call: $*"
}}
{activate_function}
activate_release_tree
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, release_link, previous_dir, target_dir


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


def test_release_gate_accepts_only_master_or_explicit_candidate_ref():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'RELEASE_REF="${RELEASE_REF:-origin/master}"' in source
    assert "unset RELEASE_REF" in source
    assert 'origin/release-candidates/*' in source
    assert 'assert_release_published "${RELEASE}" "${RELEASE_REF}"' in source
    assert 'assert_release_published "${new_release}" "${new_release_ref}"' in source
    assert 'repo_git rev-parse origin/master)" == "${RELEASE}"' not in source
    assert 'repo_git rev-parse origin/master)" == "${new_release}"' not in source

    for release_ref in (
        "origin/master",
        "origin/release-candidates/sandbox-control-plane",
        "origin/release-candidates/20260723/rc-1",
    ):
        result = _run_release_ref_validation_harness(release_ref)
        assert result.returncode == 0, result.stderr

    for release_ref in (
        "master",
        "origin/feature/sandbox",
        "origin/release-candidates/../master",
        "origin/release-candidates/bad ref",
        "refs/remotes/origin/release-candidates/rc-1",
    ):
        result = _run_release_ref_validation_harness(release_ref)
        assert result.returncode != 0, release_ref


def test_control_plane_activates_new_release_only_after_preparation():
    source = SCRIPT.read_text(encoding="utf-8")
    python_body = source.split("install_sandboxd_python() {", 1)[1].split(
        "\ninstall_sandboxd_credentials_and_env() {",
        1,
    )[0]
    install_body = source.split("install_control_plane_command() {", 1)[1].split(
        "\nupsert_env_value() {",
        1,
    )[0]

    assert '"${RELEASE_DIR}/requirements-sandboxd.lock"' in python_body
    assert '"${SERVER_RELEASE_LINK}/requirements-sandboxd.lock"' not in python_body
    assert '"${RELEASE_DIR}/deploy/systemd/nanobot-sandboxd.service"' in install_body
    assert install_body.index("仍有活动 Sandbox 容器") < install_body.index(
        "activate_release_tree"
    )
    assert install_body.index("activate_release_tree") < install_body.index(
        "systemctl restart nanobot-sandboxd.service"
    )


def test_activate_release_tree_atomically_switches_managed_forward_release(tmp_path):
    result, release_link, previous_dir, target_dir = (
        _run_activate_release_tree_harness(tmp_path)
    )

    assert result.returncode == 0, result.stderr
    assert release_link.is_symlink()
    assert release_link.resolve() == target_dir.resolve()
    assert previous_dir.is_dir()
    assert not list(tmp_path.glob(".nanobot-server-link.*"))


def test_activate_release_tree_rejects_unmanaged_current_target(tmp_path):
    result, release_link, previous_dir, _target_dir = (
        _run_activate_release_tree_harness(tmp_path, managed_current=False)
    )

    assert result.returncode != 0
    assert "不属于受管发布目录" in result.stderr
    assert release_link.resolve() == previous_dir.resolve()


def test_activate_release_tree_rejects_non_forward_switch(tmp_path):
    result, release_link, previous_dir, _target_dir = (
        _run_activate_release_tree_harness(tmp_path, allow_forward=False)
    )

    assert result.returncode != 0
    assert "只允许快进切换" in result.stderr
    assert release_link.resolve() == previous_dir.resolve()


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
    assert f"PUBLISHED {new_release} origin/master" in result.stdout
    assert not (state_dir / "smoke-passed").exists()
    assert archived.read_text(encoding="utf-8") == "smoke\n"
    assert smoke_check.read_text(encoding="utf-8") == "checked\n"
    assert config_capture.read_text(encoding="utf-8") == (
        f"{new_release}\norigin/master\nexisting-version\n"
    )


def test_update_release_can_pin_explicit_remote_candidate_ref(tmp_path):
    new_release = "2" * 40
    candidate_ref = "origin/release-candidates/sandbox-control-plane"
    result, state_dir, config_capture, smoke_check = _run_update_release_harness(
        tmp_path,
        "--release",
        new_release,
        "--release-ref",
        candidate_ref,
        "--reuse-built-image",
        "--rerun-smoke",
    )

    assert result.returncode == 0, result.stderr
    assert f"PUBLISHED {new_release} {candidate_ref}" in result.stdout
    assert config_capture.read_text(encoding="utf-8") == (
        f"{new_release}\n{candidate_ref}\nexisting-version\n"
    )
    assert not (state_dir / "smoke-passed").exists()
    assert smoke_check.read_text(encoding="utf-8") == "checked\n"


def test_promote_release_keeps_hash_and_stage_when_master_contains_candidate(
    tmp_path,
):
    result, config_capture, runtime_marker = _run_promote_release_harness(tmp_path)

    assert result.returncode == 0, result.stderr
    assert f"PUBLISHED {'6' * 40} origin/master" in result.stdout
    assert config_capture.read_text(encoding="utf-8") == (
        f"{'6' * 40}\norigin/master\n"
    )
    assert runtime_marker.read_text(encoding="utf-8") == "runtime\n"


def test_promote_release_refuses_before_runtime_deploy(tmp_path):
    result, config_capture, _runtime_marker = _run_promote_release_harness(
        tmp_path,
        runtime_stage=False,
    )

    assert result.returncode != 0
    assert "runtime-deployed" in result.stderr
    assert not config_capture.exists()


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
