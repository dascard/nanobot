#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
用法：scripts/sandbox-smoke-test.sh [Sandbox 镜像引用]

运行真实 Docker 隔离矩阵，并把测试前后宿主状态与 pytest 输出保存到用户缓存目录。
脚本不会调用 sudo、prune，也不会删除或修改现有 Nanobot 数据与容器。

默认镜像：nanobot-sandbox-python:poc-20260720
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if (( $# > 1 )); then
  usage >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
image_reference="${1:-${NANOBOT_SANDBOX_TEST_IMAGE:-nanobot-sandbox-python:poc-20260720}}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
evidence_root="${XDG_CACHE_HOME:-${HOME}/.cache}/nanobot-sandbox-smoke"
evidence_dir="${evidence_root}/${timestamp}"
build_tmp_dir="${XDG_CACHE_HOME:-${HOME}/.cache}/nanobot-build-tmp"

mkdir -p "${evidence_dir}" "${build_tmp_dir}"
export TMPDIR="${build_tmp_dir}"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

capture_command() {
  local output_file="$1"
  shift
  {
    printf '$'
    printf ' %q' "$@"
    printf '\n'
    "$@"
  } >"${output_file}" 2>&1 || true
}

capture_apparmor() {
  local output_file="$1"
  {
    echo '$ cat /sys/module/apparmor/parameters/enabled'
    cat /sys/module/apparmor/parameters/enabled
    echo '$ cat /sys/kernel/security/apparmor/profiles'
    cat /sys/kernel/security/apparmor/profiles
    if command -v aa-status >/dev/null 2>&1; then
      echo '$ aa-status'
      aa-status
    fi
  } >"${output_file}" 2>&1 || true
}

capture_test_containers() {
  local output_file="$1"
  docker ps -a \
    --filter 'label=com.nanobot.sandbox=true' \
    --filter 'label=com.nanobot.managed-by=sandboxd' \
    --format '{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Labels}}' 2>/dev/null \
    | awk 'index($0, "sbxrun_security_") > 0' \
    >"${output_file}" || true
}

capture_state() {
  local phase="$1"
  local phase_dir="${evidence_dir}/${phase}"
  mkdir -p "${phase_dir}"

  capture_command "${phase_dir}/date.txt" date --iso-8601=seconds
  capture_command "${phase_dir}/id.txt" id
  capture_command "${phase_dir}/df-h.txt" df -h
  capture_command "${phase_dir}/df-i.txt" df -i
  capture_command "${phase_dir}/docker-ps-a.txt" docker ps -a --no-trunc
  capture_command "${phase_dir}/docker-system-df.txt" docker system df
  capture_command \
    "${phase_dir}/docker-security-options.txt" \
    docker info --format '{{json .SecurityOptions}}'
  capture_command \
    "${phase_dir}/sandbox-image.txt" \
    docker image inspect "${image_reference}" \
      --format 'ID={{.Id}} USER={{.Config.User}}'
  capture_apparmor "${phase_dir}/apparmor.txt"
  capture_test_containers "${phase_dir}/security-test-containers.txt"
}

on_exit() {
  local exit_code=$?
  trap - EXIT
  capture_state post
  printf '%s\n' "${exit_code}" >"${evidence_dir}/exit-code.txt"
  if (( exit_code == 0 )); then
    echo "真实 Docker 隔离矩阵通过。"
  else
    echo "真实 Docker 隔离矩阵未通过，退出码：${exit_code}" >&2
  fi
  echo "证据目录：${evidence_dir}"
  exit "${exit_code}"
}

trap on_exit EXIT
capture_state pre

cd "${repo_root}"
NANOBOT_RUN_DOCKER_TESTS=1 \
NANOBOT_SANDBOX_TEST_IMAGE="${image_reference}" \
python -m pytest --noconftest -c /dev/null \
  tests/test_sandbox_security.py -v 2>&1 \
  | tee "${evidence_dir}/pytest.txt"
