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
    advanced_stages: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("update_release_command() {")
    end = source.index("\nassert_data_device_not_root_chain() {", start)
    update_release_function = source[start:end]

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "image-built").write_text("image\n", encoding="utf-8")
    (state_dir / "smoke-passed").write_text("smoke\n", encoding="utf-8")
    stages = (*((advanced_stage,) if advanced_stage else ()), *advanced_stages)
    for stage in stages:
        (state_dir / stage).write_text("advanced\n", encoding="utf-8")
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
assert_control_plane_current() {{ printf 'CONTROL_PLANE_CHECKED\n'; }}
assert_runtime_current() {{ printf 'RUNTIME_CHECKED\n'; }}
runtime_release_from_stage() {{ printf '%s\n' "${{RELEASE}}"; }}
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


def _run_runtime_release_validation_harness(
    tmp_path: Path,
    *,
    control_plane_changed: bool = False,
    runtime_flags: str = "off",
) -> subprocess.CompletedProcess[str]:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("runtime_release_from_stage() {")
    end = source.index("\nsmoke_command() {", start)
    functions = source[start:end]
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    current_release = "1" * 40
    target_release = "2" * 40
    (state_dir / "runtime-deployed").write_text(
        f"release={current_release}|flags={runtime_flags}\n",
        encoding="utf-8",
    )
    harness = f"""
set -Eeuo pipefail
STATE_DIR={shlex.quote(str(state_dir))}
TARGET_RELEASE={target_release}
TARGET_REF=origin/release-candidates/sandbox-control-plane
CONTROL_PLANE_CHANGED={"true" if control_plane_changed else "false"}
SANDBOX_CONTROL_PLANE_PATHS=(core/sandbox sandboxd docker/sandbox)
require_stage() {{ [[ -f "${{STATE_DIR}}/$1" ]] || die "missing stage: $1"; }}
read_stage() {{ require_stage "$1"; head -n 1 "${{STATE_DIR}}/$1"; }}
assert_release_published() {{ printf 'PUBLISHED %s %s\n' "$1" "$2"; }}
die() {{ printf '%s\n' "$*" >&2; exit 1; }}
repo_git() {{
  case "$1" in
    cat-file) return 0 ;;
    rev-parse) printf '%s\n' "${{TARGET_RELEASE}}"; return 0 ;;
    status) return 0 ;;
    merge-base) return 0 ;;
    diff)
      if [[ "${{CONTROL_PLANE_CHANGED}}" == "true" ]]; then
        printf 'sandboxd/app.py\n'
      fi
      return 0
      ;;
  esac
  die "unexpected repo_git call: $*"
}}
docker() {{
  if [[ "$1 $2 $3" == "image inspect nanobot-runtime:latest" ]]; then
    printf '%s\n' "${{TARGET_RELEASE}}"
    return 0
  fi
  die "unexpected docker call: $*"
}}
{functions}
validate_runtime_release_target "${{TARGET_RELEASE}}" "${{TARGET_REF}}"
"""
    return subprocess.run(
        ["bash", "-c", harness],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_runtime_marker_harness(
    tmp_path: Path,
    *,
    marker_release: str,
    running_release: str,
    flags: str,
) -> subprocess.CompletedProcess[str]:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("runtime_release_from_stage() {")
    end = source.index("\nvalidate_runtime_release_target() {", start)
    functions = source[start:end]
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "runtime-deployed").write_text(
        f"release={marker_release}|flags={flags}\n",
        encoding="utf-8",
    )
    harness = f"""
set -Eeuo pipefail
STATE_DIR={shlex.quote(str(state_dir))}
RUNNING_RELEASE={running_release}
require_stage() {{ [[ -f "${{STATE_DIR}}/$1" ]] || die "missing stage: $1"; }}
read_stage() {{ require_stage "$1"; head -n 1 "${{STATE_DIR}}/$1"; }}
die() {{ printf '%s\n' "$*" >&2; exit 1; }}
docker() {{ printf '%s\n' "${{RUNNING_RELEASE}}"; }}
{functions}
assert_runtime_current
"""
    return subprocess.run(
        ["bash", "-c", harness],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


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


def test_runtime_only_deploy_reuses_control_plane_and_preserves_feature_state():
    source = SCRIPT.read_text(encoding="utf-8")
    help_result = subprocess.run(
        [str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    runtime_body = source.split("deploy_runtime_command() {", 1)[1].split(
        "\nkill_switch_command() {",
        1,
    )[0]
    shared_body = source.split("deploy_runtime_release() {", 1)[1].split(
        "\ndeploy_command() {",
        1,
    )[0]

    assert help_result.returncode == 0, help_result.stderr
    assert "deploy-runtime" in help_result.stdout
    assert "--release <40 位提交>" in help_result.stdout
    assert "assert_smoke_current" in runtime_body
    assert "assert_control_plane_current" in runtime_body
    assert "validate_runtime_release_target" in runtime_body
    assert "build_image_command" not in runtime_body
    assert "smoke_command" not in runtime_body
    assert "install_control_plane_command" not in runtime_body
    assert "sandbox_feature_snapshot" in shared_body
    assert "assert_sandbox_feature_snapshot" in shared_body
    assert "configure_application_env_off" in shared_body
    assert 'if [[ "${feature_policy}" == "off" ]]' in shared_body
    assert "rollback_runtime_only_deploy" in shared_body
    assert "rollback_control_plane_after_deploy_failure" not in shared_body


def test_runtime_only_release_gate_accepts_runtime_fast_forward(tmp_path):
    result = _run_runtime_release_validation_harness(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "PUBLISHED" in result.stdout


def test_runtime_only_release_gate_rejects_control_plane_change(tmp_path):
    result = _run_runtime_release_validation_harness(
        tmp_path,
        control_plane_changed=True,
    )

    assert result.returncode != 0
    assert "sandboxd/app.py" in result.stderr
    assert "需要完整 Sandbox 验收" in result.stderr


def test_runtime_marker_can_track_release_independently_from_control_plane(
    tmp_path,
):
    release = "8" * 40
    result = _run_runtime_marker_harness(
        tmp_path,
        marker_release=release,
        running_release=release,
        flags="preserved",
    )

    assert result.returncode == 0, result.stderr


def test_runtime_marker_rejects_unknown_feature_policy(tmp_path):
    release = "8" * 40
    result = _run_runtime_marker_harness(
        tmp_path,
        marker_release=release,
        running_release=release,
        flags="unknown",
    )

    assert result.returncode != 0
    assert "Runtime 部署凭据格式无效" in result.stderr


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
    assert "control-plane-rollback" in install_body


def test_release_tree_normalizes_quota_helper_permissions():
    source = SCRIPT.read_text(encoding="utf-8")
    body = source.split("install_release_tree() {", 1)[1].split(
        "\nactivate_release_tree() {",
        1,
    )[0]

    assert '[[ -f "${quota_helper}" && ! -L "${quota_helper}" ]]' in body
    assert 'chown root:root "${quota_helper}"' in body
    assert 'chmod 0755 "${quota_helper}"' in body
    assert 'stat -c \'%u:%g:%a\' "${quota_helper}"' in body
    assert '== "0:0:755"' in body


def test_deploy_migrates_runtime_identity_only_after_backup():
    source = SCRIPT.read_text(encoding="utf-8")
    deploy_body = source.split("deploy_runtime_release() {", 1)[1].split(
        "\ndeploy_command() {",
        1,
    )[0]
    prepare_body = source.split("prepare_runtime_bind_mounts() {", 1)[1].split(
        "\ncapture_nonfixed_containers() {",
        1,
    )[0]

    assert deploy_body.index("coordinated_backup") < deploy_body.index(
        "prepare_runtime_bind_mounts"
    )
    assert deploy_body.index("prepare_runtime_bind_mounts") < deploy_body.index(
        '"${REPO_ROOT}/scripts/docker-build.sh"'
    )
    assert 'readonly RUNTIME_UID="10001"' in source
    assert 'readonly RUNTIME_GID="10001"' in source
    assert 'NANOBOT_RUNTIME_UID="${RUNTIME_UID}"' in prepare_body
    assert 'NANOBOT_RUNTIME_GID="${RUNTIME_GID}"' in prepare_body
    assert 'NANOBOT_RUNTIME_HOST_READ_GID="${runtime_host_read_gid}"' in prepare_body
    assert 'runtime_user="$(deploy_user)"' not in prepare_body
    assert "--fix-existing" in prepare_body
    assert "docker compose up -d --force-recreate" in prepare_body


def test_deploy_failure_rolls_back_runtime_and_control_plane():
    source = SCRIPT.read_text(encoding="utf-8")
    rollback_body = source.split("rollback_failed_deploy() {", 1)[1].split(
        "\ndeploy_command() {",
        1,
    )[0]

    assert "force_feature_flags_off" in rollback_body
    assert "rollback_runtime_after_deploy_failure" in rollback_body
    assert "rollback_control_plane_after_deploy_failure" in rollback_body


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


def test_update_release_recovers_control_plane_ready_before_runtime_deploy(tmp_path):
    new_release = "2" * 40
    previous_release = "1" * 40
    result, state_dir, config_capture, smoke_check = _run_update_release_harness(
        tmp_path,
        "--release",
        new_release,
        "--reuse-built-image",
        "--rerun-smoke",
        "--recover-failed-deploy",
        advanced_stage="control-plane-ready",
    )

    archived_smoke = state_dir / (
        f"smoke-passed.superseded-{previous_release}-by-{new_release}"
    )
    archived_control_plane = state_dir / (
        f"control-plane-ready.superseded-{previous_release}-by-{new_release}"
    )
    assert result.returncode == 0, result.stderr
    assert "CONTROL_PLANE_CHECKED" in result.stdout
    assert not (state_dir / "smoke-passed").exists()
    assert not (state_dir / "control-plane-ready").exists()
    assert archived_smoke.read_text(encoding="utf-8") == "smoke\n"
    assert archived_control_plane.read_text(encoding="utf-8") == "advanced\n"
    assert smoke_check.read_text(encoding="utf-8") == "checked\n"
    assert config_capture.read_text(encoding="utf-8") == (
        f"{new_release}\norigin/master\nexisting-version\n"
    )


def test_update_release_recovery_refuses_completed_runtime(tmp_path):
    new_release = "2" * 40
    result, state_dir, config_capture, smoke_check = _run_update_release_harness(
        tmp_path,
        "--release",
        new_release,
        "--reuse-built-image",
        "--rerun-smoke",
        "--recover-failed-deploy",
        advanced_stage="runtime-deployed",
    )

    assert result.returncode != 0
    assert "runtime-deployed" in result.stderr
    assert (state_dir / "runtime-deployed").exists()
    assert (state_dir / "smoke-passed").exists()
    assert not config_capture.exists()
    assert not smoke_check.exists()


def test_update_release_recovery_requires_control_plane_stage(tmp_path):
    new_release = "2" * 40
    result, state_dir, config_capture, smoke_check = _run_update_release_harness(
        tmp_path,
        "--release",
        new_release,
        "--reuse-built-image",
        "--rerun-smoke",
        "--recover-failed-deploy",
    )

    assert result.returncode != 0
    assert "要求存在 control-plane-ready" in result.stderr
    assert (state_dir / "smoke-passed").exists()
    assert not config_capture.exists()
    assert not smoke_check.exists()


def test_update_release_upgrades_fully_deployed_candidate(tmp_path):
    new_release = "2" * 40
    previous_release = "1" * 40
    result, state_dir, config_capture, smoke_check = _run_update_release_harness(
        tmp_path,
        "--release",
        new_release,
        "--reuse-built-image",
        "--rerun-smoke",
        "--upgrade-deployed-release",
        advanced_stages=("control-plane-ready", "runtime-deployed"),
    )

    assert result.returncode == 0, result.stderr
    for stage in ("smoke-passed", "control-plane-ready", "runtime-deployed"):
        archived = state_dir / (
            f"{stage}.superseded-{previous_release}-by-{new_release}"
        )
        assert archived.read_text(encoding="utf-8") in {"smoke\n", "advanced\n"}
        assert not (state_dir / stage).exists()
    assert "CONTROL_PLANE_CHECKED" in result.stdout
    assert "RUNTIME_CHECKED" in result.stdout
    assert smoke_check.read_text(encoding="utf-8") == "checked\n"
    assert config_capture.read_text(encoding="utf-8") == (
        f"{new_release}\norigin/master\nexisting-version\n"
    )


def test_update_release_upgrade_requires_runtime_stage(tmp_path):
    new_release = "2" * 40
    result, state_dir, config_capture, smoke_check = _run_update_release_harness(
        tmp_path,
        "--release",
        new_release,
        "--reuse-built-image",
        "--rerun-smoke",
        "--upgrade-deployed-release",
        advanced_stage="control-plane-ready",
    )

    assert result.returncode != 0
    assert "要求存在 runtime-deployed" in result.stderr
    assert (state_dir / "smoke-passed").exists()
    assert (state_dir / "control-plane-ready").exists()
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
