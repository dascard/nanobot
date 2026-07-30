import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def test_build_info_prefers_image_environment_without_git(monkeypatch):
    from core import build_info

    monkeypatch.setenv("NANOBOT_GIT_COMMIT", "image123")
    monkeypatch.setenv("NANOBOT_GIT_FULL_COMMIT", "image123full")
    monkeypatch.setenv("NANOBOT_GIT_BRANCH", "release")
    monkeypatch.setenv("NANOBOT_GIT_COMMIT_DATE", "2026-07-14T00:00:00Z")
    monkeypatch.setenv("NANOBOT_GIT_DIRTY", "false")

    def fail_git(*_args, **_kwargs):
        raise AssertionError("镜像元数据完整时不应探测容器内 .git")

    monkeypatch.setattr(build_info.subprocess, "check_output", fail_git)

    resolved = build_info.resolve_build_info()

    assert resolved.as_dict() == {
        "commit": "image123",
        "full_commit": "image123full",
        "branch": "release",
        "commit_date": "2026-07-14T00:00:00Z",
        "dirty": False,
        "display": "image123",
    }


def test_build_info_keeps_dirty_unknown_when_git_probe_fails(
    monkeypatch,
    caplog,
):
    import logging

    from core import build_info

    monkeypatch.setenv("NANOBOT_GIT_COMMIT", "image123")
    monkeypatch.setenv("NANOBOT_GIT_FULL_COMMIT", "image123full")
    monkeypatch.setenv("NANOBOT_GIT_BRANCH", "release")
    monkeypatch.setenv("NANOBOT_GIT_COMMIT_DATE", "2026-07-14T00:00:00Z")
    monkeypatch.delenv("NANOBOT_GIT_DIRTY", raising=False)
    secret = "git-probe-secret"

    def fail_git(*_args, **_kwargs):
        raise OSError(secret)

    monkeypatch.setattr(build_info.subprocess, "check_output", fail_git)
    logger = logging.getLogger("nanobot.build-info.test")

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        resolved = build_info.resolve_build_info(logger=logger)

    assert resolved.dirty is None
    assert secret not in caplog.text
    assert "error_type=OSError" in caplog.text


def test_build_info_fills_only_missing_fields_from_git(monkeypatch):
    from core import build_info

    monkeypatch.setenv("NANOBOT_GIT_COMMIT", "image-short")
    for key in (
        "NANOBOT_GIT_FULL_COMMIT",
        "NANOBOT_GIT_BRANCH",
        "NANOBOT_GIT_COMMIT_DATE",
        "NANOBOT_GIT_DIRTY",
    ):
        monkeypatch.delenv(key, raising=False)

    def fake_git(command, **_kwargs):
        args = tuple(command[1:])
        values = {
            ("rev-parse", "HEAD"): "git-full\n",
            ("rev-parse", "--abbrev-ref", "HEAD"): "git-branch\n",
            ("log", "-1", "--format=%ci", "--date=iso-strict"): "git-date\n",
        }
        if args[:2] == ("status", "--porcelain"):
            return " M tracked.py\n"
        if args == ("rev-parse", "--short", "HEAD"):
            raise AssertionError("环境 commit 已配置，不应再次探测短 SHA")
        return values[args]

    monkeypatch.setattr(build_info.subprocess, "check_output", fake_git)

    resolved = build_info.resolve_build_info()

    assert resolved.commit == "image-short"
    assert resolved.full_commit == "git-full"
    assert resolved.branch == "git-branch"
    assert resolved.commit_date == "git-date"
    assert resolved.dirty is True


def test_sentinel_path_uses_one_runtime_resolver(monkeypatch):
    import config

    monkeypatch.delenv("SENTINEL_MODEL_PATH", raising=False)
    assert config.get_sentinel_model_path() == "./sentinel"

    monkeypatch.setenv("SENTINEL_MODEL_PATH", "/models/custom-sentinel")
    assert config.get_sentinel_model_path() == "/models/custom-sentinel"


def _service_block(compose: str, service_name: str) -> str:
    marker = f"  {service_name}:\n"
    start = compose.index(marker)
    next_service = compose.find("\n  ", start + len(marker))
    while next_service != -1:
        following = compose[next_service + 1 :]
        if following.startswith("  ") and not following.startswith("    "):
            return compose[start:next_service]
        next_service = compose.find("\n  ", next_service + 1)
    return compose[start:]


def _environment_keys(service_block: str) -> set[str]:
    lines = service_block.splitlines()
    try:
        start = next(
            index for index, line in enumerate(lines) if line.strip() == "environment:"
        )
    except StopIteration:
        return set()
    keys: set[str] = set()
    for line in lines[start + 1 :]:
        if line and not line.startswith("      "):
            break
        match = re.match(r"^      ([A-Z][A-Z0-9_]*):", line)
        if match:
            keys.add(match.group(1))
    return keys


def _docker_compose_command() -> list[str]:
    standalone = shutil.which("docker-compose")
    if standalone is not None:
        return [standalone]
    docker = shutil.which("docker")
    if docker is None:
        raise AssertionError("部署配置测试需要 Docker Compose CLI")
    return [docker, "compose"]


def _render_compose_with_env(tmp_path, values: dict[str, str]) -> dict:
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        Path("docker-compose.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            *_docker_compose_command(),
            "--project-directory",
            str(tmp_path),
            "--env-file",
            str(env_path),
            "-f",
            str(compose_path),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "LANG": "C.UTF-8",
            "PATH": os.environ.get("PATH", ""),
        },
    )
    return json.loads(completed.stdout)


def test_runtime_image_uses_python_311():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "ARG PYTHON_IMAGE=python:3.11.13-slim-bookworm" in dockerfile
    assert "FROM ${PYTHON_IMAGE} AS runtime" in dockerfile
    assert "FROM python:3.10" not in dockerfile


def test_timing_gate_uses_python_311():
    workflow = Path(".github/workflows/timing-gate-eval.yml").read_text(
        encoding="utf-8"
    )

    assert 'python-version: "3.11"' in workflow
    assert 'python-version: "3.10"' not in workflow


def test_worker_services_reuse_server_image_without_duplicate_builds():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    server = _service_block(compose, "nanobot-server")
    summary_worker = _service_block(compose, "session-summary-worker")
    semantic_worker = _service_block(compose, "semantic-index-worker")
    outbound_worker = _service_block(compose, "outbound-delivery-worker")

    assert "build:" in server
    image_contract = "image: ${NANOBOT_RUNTIME_IMAGE:-nanobot-runtime:latest}"
    assert image_contract in server
    assert image_contract in summary_worker
    assert image_contract in semantic_worker
    assert image_contract in outbound_worker
    assert "build:" not in summary_worker
    assert "build:" not in semantic_worker
    assert "build:" not in outbound_worker


def test_nanobot_services_have_bounded_json_file_logs(tmp_path):
    rendered = _render_compose_with_env(tmp_path, {})

    for service_name in (
        "nanobot-server",
        "session-summary-worker",
        "semantic-index-worker",
        "outbound-delivery-worker",
    ):
        logging = rendered["services"][service_name]["logging"]
        assert logging == {
            "driver": "json-file",
            "options": {"max-file": "3", "max-size": "20m"},
        }


def test_docker_daemon_example_bounds_build_cache_and_default_logs():
    config = json.loads(
        Path("deploy/docker/daemon.json.example").read_text(encoding="utf-8")
    )

    assert config["log-driver"] == "json-file"
    assert config["log-opts"] == {"max-size": "20m", "max-file": "3"}
    policies = config["builder"]["gc"]["policy"]
    assert config["builder"]["gc"]["enabled"] is True
    assert policies == [
        {
            "reservedSpace": "5GB",
            "maxUsedSpace": "20GB",
            "minFreeSpace": "60GB",
            "all": True,
        }
    ]


def test_production_compose_requires_immutable_runtime_image():
    compose = Path("docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "NANOBOT_RUNTIME_IMAGE" in compose
    assert "@sha256:" in compose
    assert "nanobot-runtime:latest" not in compose
    for service_name in (
        "nanobot-server",
        "session-summary-worker",
        "outbound-delivery-worker",
        "semantic-index-worker",
    ):
        block = _service_block(compose, service_name)
        assert "build: !reset null" in block


def test_production_compose_uses_external_prompt_runtime_and_absolute_mounts():
    compose = Path("docker-compose.prod.yml").read_text(encoding="utf-8")
    server = _service_block(compose, "nanobot-server")
    base_server = _service_block(
        Path("docker-compose.yml").read_text(encoding="utf-8"),
        "nanobot-server",
    )
    summary_worker = _service_block(compose, "session-summary-worker")

    assert "NANOBOT_PRODUCTION_ENV_FILE" in server
    assert "NANOBOT_PRODUCTION_DATA_DIR" in server
    assert "NANOBOT_PRODUCTION_MODELS_DIR" in server
    assert "NANOBOT_PRODUCTION_SENTINEL_DIR" in server
    assert "NANOBOT_PROMPT_HOST_ROOT" in server
    assert "/var/lib/nanobot/prompt-runtime/live/runtime" in server
    assert "./data:/app/data" not in server
    assert "./data/prompts_v2" not in server
    assert "/var/lib/nanobot/prompt-runtime/live:ro" in summary_worker
    assert "/var/lib/nanobot/prompt-runtime/state:ro" in summary_worker
    infrastructure_default = (
        "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED: "
        "${NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED:-true}"
    )
    assert infrastructure_default in server
    assert infrastructure_default in base_server
    assert (
        "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED=true"
        in Path(".env.example").read_text(encoding="utf-8").splitlines()
    )
    for key in (
        "NANOBOT_SANDBOX_ENABLED",
        "NANOBOT_SANDBOX_EXEC_ENABLED",
        "NANOBOT_SANDBOX_GROUP_ENABLED",
        "NANOBOT_GROUP_LEARNING_ENABLED",
        "GROUP_MEMORY_INJECTION_ENABLED",
    ):
        assert f'{key}: "false"' in server


def test_compose_services_apply_runtime_hardening_and_resource_limits():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    for service_name in (
        "nanobot-server",
        "session-summary-worker",
        "outbound-delivery-worker",
        "semantic-index-worker",
    ):
        block = _service_block(compose, service_name)
        assert "read_only: true" in block
        assert "cap_drop:" in block
        assert "- ALL" in block
        assert "no-new-privileges:true" in block
        assert "pids_limit:" in block
        assert "mem_limit:" in block
        assert "cpus:" in block
        assert "init: true" in block
        assert "/tmp:size=" in block
        assert "healthcheck:" in block


def test_compose_services_give_kt_a_bounded_ephemeral_home():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    kt_home_tmpfs = (
        "/home/nanobot/.kohakuterrarium:size=64m,mode=0700,"
        "uid=${NANOBOT_RUNTIME_UID:-10001},"
        "gid=${NANOBOT_RUNTIME_GID:-10001},nosuid,nodev,noexec"
    )

    for service_name in (
        "nanobot-server",
        "session-summary-worker",
        "outbound-delivery-worker",
        "semantic-index-worker",
    ):
        block = _service_block(compose, service_name)
        assert (
            'user: "${NANOBOT_RUNTIME_UID:-10001}:'
            '${NANOBOT_RUNTIME_GID:-10001}"'
        ) in block
        assert kt_home_tmpfs in block


def test_compose_workers_wait_for_server_readiness():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    server = _service_block(compose, "nanobot-server")

    assert "healthcheck:" in server
    assert "/api/v1/ready" in server
    for service_name in (
        "session-summary-worker",
        "outbound-delivery-worker",
        "semantic-index-worker",
    ):
        block = _service_block(compose, service_name)
        assert "condition: service_healthy" in block
        assert 'test: ["CMD", "python", "-m", "core.runtime_health"]' in block


def test_runtime_image_uses_non_root_user():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "ARG NANOBOT_UID=10001" in dockerfile
    assert "ARG NANOBOT_GID=10001" in dockerfile
    assert "USER nanobot:nanobot" in dockerfile


def test_runtime_image_labels_the_exact_source_revision():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert 'LABEL org.opencontainers.image.revision="${GIT_FULL_COMMIT}"' in dockerfile


def test_runtime_image_applies_kt_patch_without_dirtying_host_submodule():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")

    assert "AS kt-source" in dockerfile
    assert "stream-message-flag.patch" in dockerfile
    assert "git apply --check" in dockerfile
    assert "COPY --from=kt-source" in dockerfile
    assert "!patches/kohakuterrarium/stream-message-flag.patch" in dockerignore


def test_runtime_directory_preparer_is_executable():
    script = Path("scripts/prepare-runtime-directories.sh")

    assert os.access(script, os.X_OK)


def test_runtime_directory_preparer_preserves_host_read_audit_access():
    script = Path("scripts/prepare-runtime-directories.sh").read_text(
        encoding="utf-8"
    )

    assert "NANOBOT_RUNTIME_HOST_READ_GID" in script
    assert "NANOBOT_PRODUCTION_ROOT" in script
    assert 'install -d -m 2750 -o "${runtime_uid}"' in script
    assert 'chmod -R g+rX-w,o-rwx "${runtime_paths[@]}"' in script
    assert "-type d -exec chmod g+s" in script


def test_runtime_mutable_paths_stay_under_data_or_temp(monkeypatch, tmp_path):
    from core.runtime_paths import RuntimePaths

    data_dir = tmp_path / "persistent"
    temp_dir = tmp_path / "ephemeral"
    monkeypatch.setenv("NANOBOT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NANOBOT_TEMP_DIR", str(temp_dir))

    paths = RuntimePaths.from_environment()

    assert paths.data_dir == data_dir.resolve()
    assert paths.temp_dir == temp_dir.resolve()
    assert paths.rag_benchmark_manual_dir.is_relative_to(paths.data_dir)
    assert paths.rag_benchmark_report_dir.is_relative_to(paths.data_dir)
    assert paths.rag_benchmark_generated_dir.is_relative_to(paths.temp_dir)


def test_production_deploy_requires_digest_and_never_builds_in_place():
    script = Path("scripts/deploy-production.sh").read_text(encoding="utf-8")
    deployer = Path("scripts/deploy_release.py").read_text(encoding="utf-8")
    deployment = Path("core/release/deployment.py").read_text(
        encoding="utf-8"
    )
    implementation = script + deployer + deployment

    assert "@sha256:" in script
    assert "NANOBOT_RELEASE_MANIFEST" in script
    assert "NANOBOT_COORDINATED_BACKUP_DIR" in script
    assert "NANOBOT_PROMPT_AUDIT_RECEIPT" in script
    assert "NANOBOT_PRODUCTION_ROOT" in script
    assert "scripts/deploy_release.py" in script
    assert "docker-compose.prod.yml" in deployment
    assert "--no-build" in deployment
    assert "docker compose build" not in implementation
    assert "prune" not in implementation


def test_runtime_release_workflow_builds_sbom_and_manifest_after_both_gates():
    workflow = Path(".github/workflows/release-runtime.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_run:" in workflow
    assert "通用质量门禁" in workflow
    assert "timing-gate-eval.yml/runs" in workflow
    assert '.head_sha == $sha' in workflow
    assert '.conclusion == "success"' in workflow
    assert "docker buildx build" in workflow
    assert "--push" in workflow
    assert "--sbom=true" in workflow
    assert "anchore/sbom-action@v0" in workflow
    assert "build_release_manifest.py artifact" in workflow
    assert "build_release_manifest.py release" in workflow
    assert (
        "--schema-migration-head "
        "20260730_llm_request_execution_phase"
    ) in workflow
    assert "scripts/build_context_manifest.py" in workflow
    assert '--input-sha "build_context=${BUILD_CONTEXT_SHA256}"' in workflow
    assert "--require-built" in workflow
    assert 'tar --create --file "${bundle}" "${EVIDENCE_DIR}"' in workflow
    assert 'sha256sum "${bundle}" >"${bundle}.sha256"' in workflow
    assert "sandbox-real-docker" not in workflow
    assert "nanobot-runtime:latest" not in workflow


def test_webui_production_dependencies_are_patched_and_router_stays_client_only():
    lock = json.loads(
        Path("webui/package-lock.json").read_text(encoding="utf-8")
    )
    packages = lock["packages"]
    assert packages["node_modules/axios"]["version"] == "1.18.1"
    assert packages["node_modules/form-data"]["version"] == "4.0.6"
    assert packages["node_modules/react-router"]["version"] == "7.18.1"
    assert packages["node_modules/react-router-dom"]["version"] == "7.18.1"

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("webui/src").rglob("*"))
        if path.suffix in {".js", ".jsx", ".ts", ".tsx"}
    )
    # GHSA-qwww-vcr4-c8h2 影响 RSC action；当前后台必须保持纯 BrowserRouter SPA。
    assert "BrowserRouter" in source
    for server_or_data_router_api in (
        "createBrowserRouter",
        "RouterProvider",
        "RSCHydratedRouter",
        "RSCStaticRouter",
        "createCallServer",
    ):
        assert server_or_data_router_api not in source


def test_production_prompt_manager_uses_target_digest_and_restricted_container():
    path = Path("scripts/manage-prompt-runtime-production.sh")
    script = path.read_text(encoding="utf-8")

    assert os.access(path, os.X_OK)
    assert "@sha256:" in script
    assert "--network none" in script
    assert "--read-only" in script
    assert "--cap-drop ALL" in script
    assert "no-new-privileges:true" in script
    assert "/var/lib/nanobot/prompt-runtime" in script
    assert "scripts/verify_prompt_runtime_release.py" in script
    assert "data/prompts_v2" not in script


def test_local_build_rolls_back_when_compose_recreate_fails(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "docker.calls"
    up_state = tmp_path / "compose-up.count"

    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
case "${1:-}" in
  rev-parse)
    if [[ "${2:-}" == "--short" ]]; then
      printf 'abc1234\n'
    elif [[ "${2:-}" == "--abbrev-ref" ]]; then
      printf 'candidate\n'
    else
      printf '%040d\n' 1
    fi
    ;;
  log) printf '2026-07-23T00:00:00+08:00\n' ;;
  status) : ;;
esac
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
printf '%s\n' "$*" >>"${FAKE_DOCKER_LOG}"
if [[ "${1:-} ${2:-}" == "image inspect" ]]; then
  case "${3:-}" in
    nanobot-runtime:latest) printf 'sha256:old-runtime\n' ;;
    nanobot-runtime:rollback) printf 'sha256:older-runtime\n' ;;
    *) exit 1 ;;
  esac
  exit 0
fi
if [[ "${1:-} ${2:-}" == "image tag" ]]; then
  exit 0
fi
if [[ "${1:-} ${2:-}" == "image rm" ]]; then
  exit 0
fi
if [[ "${1:-} ${2:-}" == "compose build" ]]; then
  exit 0
fi
if [[ "${1:-} ${2:-}" == "compose up" ]]; then
  count=0
  [[ ! -f "${FAKE_DOCKER_STATE}" ]] || count="$(cat "${FAKE_DOCKER_STATE}")"
  count=$((count + 1))
  printf '%s\n' "${count}" >"${FAKE_DOCKER_STATE}"
  [[ "${count}" -gt 1 ]] || exit 42
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    fake_curl = fake_bin / "curl"
    fake_curl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_curl.chmod(0o755)

    result = subprocess.run(
        ["scripts/docker-build.sh", "nanobot-server"],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
        env={
            "FAKE_DOCKER_LOG": str(call_log),
            "FAKE_DOCKER_STATE": str(up_state),
            "HOME": str(tmp_path),
            "LANG": "C.UTF-8",
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        },
    )

    assert result.returncode == 42
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert calls == [
        "image inspect nanobot-runtime:latest --format {{.Id}}",
        "image inspect nanobot-runtime:rollback --format {{.Id}}",
        "image tag sha256:old-runtime nanobot-runtime:predeploy",
        "image tag sha256:old-runtime nanobot-runtime:rollback",
        "compose build nanobot-server",
        "compose up -d --force-recreate --wait --wait-timeout 90 "
        "nanobot-server session-summary-worker "
        "outbound-delivery-worker semantic-index-worker",
        "image tag nanobot-runtime:predeploy nanobot-runtime:latest",
        "compose up -d --force-recreate --wait --wait-timeout 90 "
        "nanobot-server session-summary-worker "
        "outbound-delivery-worker semantic-index-worker",
        "image tag sha256:old-runtime nanobot-runtime:rollback",
        "image rm nanobot-runtime:predeploy",
    ]
    assert "已恢复部署前 Runtime" in result.stderr


def test_local_build_preserves_current_runtime_when_build_fails(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "docker.calls"

    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
printf '%s\n' "$*" >>"${FAKE_DOCKER_LOG}"
if [[ "${1:-} ${2:-}" == "image inspect" ]]; then
  case "${3:-}" in
    nanobot-runtime:latest) printf 'sha256:old-runtime\n' ;;
    nanobot-runtime:rollback) printf 'sha256:older-runtime\n' ;;
    *) exit 1 ;;
  esac
  exit 0
fi
if [[ "${1:-} ${2:-}" == "image tag" ]]; then
  exit 0
fi
if [[ "${1:-} ${2:-}" == "image rm" ]]; then
  exit 0
fi
if [[ "${1:-} ${2:-}" == "compose build" ]]; then
  exit 37
fi
exit 1
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/docker-build.sh", "nanobot-server"],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
        env={
            "FAKE_DOCKER_LOG": str(call_log),
            "HOME": str(tmp_path),
            "LANG": "C.UTF-8",
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        },
    )

    assert result.returncode == 37
    assert call_log.read_text(encoding="utf-8").splitlines() == [
        "image inspect nanobot-runtime:latest --format {{.Id}}",
        "image inspect nanobot-runtime:rollback --format {{.Id}}",
        "image tag sha256:old-runtime nanobot-runtime:predeploy",
        "image tag sha256:old-runtime nanobot-runtime:rollback",
        "compose build nanobot-server",
        "image rm nanobot-runtime:predeploy",
    ]


def test_quality_gate_runs_full_backend_frontend_and_architecture_checks():
    workflow = Path(".github/workflows/quality-gate.yml").read_text(
        encoding="utf-8"
    )

    assert "python -m pytest tests/ -v" in workflow
    assert "python scripts/check_architecture.py" in workflow
    assert (
        "python scripts/build_release_impact.py --check-golden"
        in workflow
    )
    assert (
        "python scripts/build_verification_plan.py --check-golden"
        in workflow
    )
    assert "python scripts/audit_decision_rules.py --check" in workflow
    assert "python scripts/build_behavior_baseline.py --check" in workflow
    inventory_step = workflow.split(
        "- name: 检查决策规则清单漂移", maxsplit=1
    )[1].split("- name:", maxsplit=1)[0]
    behavior_step = workflow.split(
        "- name: 检查架构行为 Golden 漂移", maxsplit=1
    )[1].split("- name:", maxsplit=1)[0]
    assert "continue-on-error: true" not in inventory_step
    assert "continue-on-error: true" not in behavior_step
    assert "python -m ruff check" in workflow
    assert "npm run lint" in workflow
    assert "npm run build" in workflow
    assert "npm audit --omit=dev --audit-level=critical" in workflow
    assert "cp .env.example .env" in workflow
    assert "trap 'rm -f .env' EXIT" in workflow
    assert "docker compose -f docker-compose.yml config --quiet" in workflow
    assert "初始化 Prompt Runtime 验收副本" in workflow
    assert "init_prompt_v2_runtime_dir()" in workflow


def test_compose_workers_use_explicit_minimal_environment_allowlists():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    summary_worker = _service_block(compose, "session-summary-worker")
    semantic_worker = _service_block(compose, "semantic-index-worker")
    outbound_worker = _service_block(compose, "outbound-delivery-worker")
    expected = {
        "session-summary-worker": {
            "HOME",
            "DATABASE_URL",
            "LOG_DIR",
            "LOG_LEVEL",
            "NEW_API_BASE_URL",
            "NEW_API_KEY",
            "NEW_API_TIMEOUT",
            "NEW_API_MAX_RETRIES",
            "LLM_MODEL_SESSION_SUMMARY",
            "LLM_MODEL_FAST",
            "CLASSIFIER_API_URL",
            "NANOBOT_PROMPT_DEFAULT_DIR",
            "NANOBOT_PROMPT_RUNTIME_DIR",
        },
        "semantic-index-worker": {
            "HOME",
            "DATABASE_URL",
            "LOG_DIR",
            "LOG_LEVEL",
            "SEMANTIC_INDEX_ENABLED",
            "RAG_EMBEDDING_PROVIDER",
        },
        "outbound-delivery-worker": {
            "HOME",
            "DATABASE_URL",
            "LOG_DIR",
            "LOG_LEVEL",
            "NANOBOT_PUSH_TOKEN",
            "QQBOT_PUSH_URL",
            "QQBOT_PUSH_TIMEOUT",
                "NANOBOT_QQ_PUSH_CONFIG_REVISION",
                "NANOBOT_PUBLIC_BASE_URL",
                "NANOBOT_ASSET_TOKEN_SECRET",
                "NANOBOT_ASSET_TOKEN_TTL_SECONDS",
                "NANOBOT_OUTBOUND_BATCH_SIZE",
            "NANOBOT_OUTBOUND_LEASE_SECONDS",
            "NANOBOT_OUTBOUND_POLL_INTERVAL",
        },
    }
    denylist = {
        "NANOBOT_ADMIN_TOKEN",
        "NANOBOT_API_TOKEN",
        "NANOBOT_SUPER_USER_IDS",
        "NANOBOT_STICKER_IMAGE_TOKEN",
        "NANOBOT_GENERATED_IMAGE_TOKEN",
        "OPENAI_API_KEY",
    }

    for name, block in (
        ("session-summary-worker", summary_worker),
        ("semantic-index-worker", semantic_worker),
        ("outbound-delivery-worker", outbound_worker),
    ):
        assert "env_file:" not in block
        actual = _environment_keys(block)
        assert actual == expected[name]
        assert actual.isdisjoint(denylist)


def test_outbound_worker_receives_only_dedicated_push_token():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    worker = _service_block(compose, "outbound-delivery-worker")
    keys = _environment_keys(worker)

    assert "NANOBOT_PUSH_TOKEN" in keys
    assert "NANOBOT_API_TOKEN" not in keys
    assert "NANOBOT_ADMIN_TOKEN" not in keys


def test_rendered_compose_keeps_push_secret_out_of_server(tmp_path):
    push_sentinel = "push-token-compose-sentinel"
    server_settings = {
        "DATABASE_URL": "sqlite:///./data/compose-sentinel.db",
        "LOG_DIR": "./data/compose-sentinel",
        "LOG_LEVEL": "WARNING",
        "NANOBOT_API_TOKEN": "api-token-compose-sentinel",
        "NANOBOT_ADMIN_TOKEN": "admin-token-compose-sentinel",
        "NEW_API_BASE_URL": "http://new-api.compose.invalid/v1",
        "NEW_API_KEY": "new-api-compose-sentinel",
        "CLASSIFIER_API_URL": "http://classifier.compose.invalid/v1",
        "NANOBOT_PROMPT_RUNTIME_DIR": "./data/prompts-compose-sentinel",
        "NANOBOT_SESSION_SUMMARY_WORKER_MODE": "embedded",
    }
    rendered = _render_compose_with_env(
        tmp_path,
        {
            **server_settings,
            "NANOBOT_PUSH_TOKEN": push_sentinel,
        },
    )

    server_environment = rendered["services"]["nanobot-server"]["environment"]
    worker_environment = rendered["services"]["outbound-delivery-worker"][
        "environment"
    ]
    assert push_sentinel not in server_environment.values()
    assert not server_environment.get("NANOBOT_PUSH_TOKEN")
    assert worker_environment["NANOBOT_PUSH_TOKEN"] == push_sentinel
    assert "NANOBOT_API_TOKEN" not in worker_environment
    assert "NANOBOT_ADMIN_TOKEN" not in worker_environment
    for key, value in server_settings.items():
        expected = (
            "external"
            if key == "NANOBOT_SESSION_SUMMARY_WORKER_MODE"
            else value
        )
        assert server_environment[key] == expected


def test_outbound_worker_has_minimal_runtime_surface():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    worker = _service_block(compose, "outbound-delivery-worker")

    assert "python -m workers.outbound_delivery_worker --loop" in worker
    assert "env_file:" not in worker
    assert "ports:" not in worker
    assert "- ./data:/app/data" in worker
    assert "./sentinel" not in worker
    assert "./models" not in worker
    assert "restart: unless-stopped" in worker
    assert "stop_grace_period: 5m" in worker


def test_compose_server_uses_external_session_summary_worker_mode():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    server = _service_block(compose, "nanobot-server")
    summary_worker = _service_block(compose, "session-summary-worker")

    assert "NANOBOT_SESSION_SUMMARY_WORKER_MODE: external" in server
    assert "python -m workers.session_summary_worker --loop" in summary_worker


def test_readme_documents_session_summary_worker_modes_without_double_start():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "NANOBOT_SESSION_SUMMARY_WORKER_MODE=external" in readme
    assert "`embedded`" in readme
    assert "`external`" in readme
    assert "`disabled`" in readme
    assert "不要同时运行内嵌和独立 session-summary worker" in readme


def test_dockerignore_excludes_runtime_model_directories():
    ignored = {
        line.strip()
        for line in Path(".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert "models/" in ignored
    assert "sentinel/" in ignored
    assert "**/.git/" in ignored
    assert "*.egg-info/" in ignored
    assert "vendor/KohakuTerrarium/build/" in ignored


def test_new_api_timeout_defaults_to_300_seconds():
    config_text = Path("config.py").read_text(encoding="utf-8")
    registry_text = Path("core/config_registry.py").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert 'NEW_API_TIMEOUT = int(os.environ.get("NEW_API_TIMEOUT", "300"))' in config_text
    assert "key=\"new_api.timeout\", env_name=\"NEW_API_TIMEOUT\",\n        default=300" in registry_text
    assert "NEW_API_TIMEOUT=300" in env_example


def test_super_user_ids_use_one_canonical_environment_variable():
    config_text = Path("config.py").read_text(encoding="utf-8")
    registry_text = Path("core/config_registry.py").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")
    legacy_reads = (
        'os.environ.get("' + "SUPER" + '_USER_IDS"',
        'os.environ.get("' + "ADMIN" + '_USER_ID"',
    )

    assert 'os.environ.get("NANOBOT_SUPER_USER_IDS", "")' in config_text
    assert "NANOBOT_SUPER_USER_IDS=" in env_example
    assert "bot.super_user_ids" not in registry_text
    assert all(read not in config_text for read in legacy_reads)
    assert "\n" + "SUPER" + "_USER_IDS =" not in config_text
    assert "\n" + "ADMIN" + "_USER_ID =" not in config_text


def test_test_dependencies_declare_strict_asyncio_mode():
    production_requirements = Path("requirements.txt").read_text(encoding="utf-8")
    test_requirements = Path("requirements-test.txt").read_text(encoding="utf-8")
    pytest_path = Path("pytest.ini")

    assert not re.search(
        r"^pytest(?:-[a-z0-9-]+)?(?:[<>=!~].*)?$",
        production_requirements,
        re.MULTILINE,
    )
    assert re.search(
        r"^pytest-asyncio(?:[<>=!~].*)?$", test_requirements, re.MULTILINE
    )
    assert pytest_path.is_file()
    pytest_config = pytest_path.read_text(encoding="utf-8")
    assert re.search(r"^asyncio_mode\s*=\s*strict$", pytest_config, re.MULTILINE)


def test_production_lock_uses_cpu_only_torch():
    lock = Path("requirements-prod.lock").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert re.search(r"^torch==[^+\s]+$", lock, re.MULTILINE)
    assert "https://download.pytorch.org/whl/cpu" in lock
    assert "pip install --no-deps" in dockerfile
    assert "--index-url https://download.pytorch.org/whl/cpu" in dockerfile
    assert not re.search(r"^nvidia-[^=]+==", lock, re.MULTILINE)


def test_env_example_matches_current_runtime_configuration_contract():
    text = Path(".env.example").read_text(encoding="utf-8")
    required_keys = {
        "DATABASE_URL",
        "LOG_DIR",
        "LOG_LEVEL",
        "NANOBOT_API_TOKEN",
        "NANOBOT_ADMIN_TOKEN",
        "NANOBOT_SUPER_USER_IDS",
        "NEW_API_BASE_URL",
        "NEW_API_KEY",
        "NEW_API_TIMEOUT",
        "NEW_API_MAX_RETRIES",
        "LLM_MODEL_SESSION_SUMMARY",
        "LLM_MODEL_FAST",
        "CLASSIFIER_API_URL",
        "SENTINEL_MODEL_PATH",
        "MEMORY_DIGEST_SCHEDULER_ENABLED",
        "MEMORY_DIGEST_SCHEDULE_HOUR",
        "NANOBOT_SESSION_SUMMARY_WORKER_MODE",
        "SEMANTIC_INDEX_ENABLED",
        "RAG_EMBEDDING_PROVIDER",
        "NANOBOT_PROMPT_DEFAULT_DIR",
        "NANOBOT_PROMPT_RUNTIME_DIR",
        "NANOBOT_PUSH_TOKEN",
        "QQBOT_PUSH_URL",
        "QQBOT_PUSH_TIMEOUT",
        "NANOBOT_QQ_PUSH_CONFIG_REVISION",
        "NANOBOT_OUTBOUND_BATCH_SIZE",
        "NANOBOT_OUTBOUND_LEASE_SECONDS",
        "NANOBOT_OUTBOUND_POLL_INTERVAL",
        "NANOBOT_SCHEDULED_TASK_CLAIM_LEASE_SECONDS",
        "NANOBOT_SCHEDULED_TASK_WRITER_LEASE_SECONDS",
        "NANOBOT_OUTBOUND_MAX_ATTEMPTS",
        "NANOBOT_OUTBOUND_RETRY_DEADLINE_SECONDS",
        "NANOBOT_PUBLIC_BASE_URL",
        "NANOBOT_STICKER_IMAGE_TOKEN",
        "NANOBOT_GENERATED_IMAGE_TOKEN",
        "GIT_COMMIT",
        "GIT_BRANCH",
    }
    configured_keys = {
        match.group(1)
        for match in re.finditer(r"^([A-Z][A-Z0-9_]*)=", text, re.MULTILINE)
    }
    sensitive_keys = {
        "NANOBOT_API_TOKEN",
        "NANOBOT_ADMIN_TOKEN",
        "NANOBOT_PUSH_TOKEN",
        "NANOBOT_SUPER_USER_IDS",
        "NEW_API_KEY",
        "NANOBOT_STICKER_IMAGE_TOKEN",
        "NANOBOT_GENERATED_IMAGE_TOKEN",
    }

    assert "DIFY" not in text.upper()
    assert required_keys <= configured_keys
    assert not {
        "NANOBOT_GIT_COMMIT",
        "NANOBOT_GIT_BRANCH",
        "NANOBOT_GIT_FULL_COMMIT",
        "NANOBOT_GIT_COMMIT_DATE",
        "NANOBOT_GIT_DIRTY",
    } & configured_keys
    assert len(re.findall(r"^NANOBOT_PUSH_TOKEN=$", text, re.MULTILINE)) == 1
    for key in sensitive_keys:
        assert re.search(rf"^{key}=$", text, re.MULTILINE)


def test_config_import_never_generates_or_appends_admin_token():
    text = Path("config.py").read_text(encoding="utf-8")

    assert "secrets.token_hex" not in text
    assert 'open(_env_path, "a")' not in text
    assert "Token 必须由部署环境的单一来源显式提供" in text
