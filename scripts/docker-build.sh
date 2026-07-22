#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/docker-build.sh [docker compose build args...]
  scripts/docker-build.sh --build-only [docker compose build args...]

默认行为：构建镜像后执行 docker compose up -d --force-recreate，确保运行中的容器使用新镜像。
只想构建镜像时传 --build-only。部署并通过健康检查后，脚本仅保留当前镜像和最近一个
已验证回滚镜像；不会执行任何全局 prune。
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

MODE="deploy"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ "${1:-}" == "--build-only" ]]; then
  MODE="build-only"
  shift
fi

export GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
export GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
export GIT_FULL_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
export GIT_COMMIT_DATE="$(git log -1 --format=%ci --date=iso-strict 2>/dev/null || true)"

build_tmp_dir="${XDG_CACHE_HOME:-${HOME}/.cache}/nanobot-build"
mkdir -p "${build_tmp_dir}"
status_file="$(mktemp "${build_tmp_dir}/git-status.XXXXXX")"
trap 'rm -f "${status_file}"' EXIT

if git status --porcelain --untracked-files=no >"${status_file}" 2>/dev/null; then
  if [ -s "${status_file}" ]; then
    export GIT_DIRTY=true
  else
    export GIT_DIRTY=false
  fi
else
  export GIT_DIRTY=null
fi

runtime_image="${NANOBOT_RUNTIME_IMAGE:-nanobot-runtime:latest}"
rollback_image="nanobot-runtime:rollback"
predeploy_image="nanobot-runtime:predeploy"
if [[ "${runtime_image}" == *@sha256:* ]]; then
  echo "digest 镜像由 scripts/deploy-production.sh 部署，本地构建入口不接受 digest 引用。" >&2
  exit 2
fi
previous_image_id="$(docker image inspect "${runtime_image}" --format '{{.Id}}' 2>/dev/null || true)"
previous_rollback_id="$(docker image inspect "${rollback_image}" --format '{{.Id}}' 2>/dev/null || true)"
if [[ -n "${previous_image_id}" ]]; then
  docker image tag "${previous_image_id}" "${predeploy_image}"
  docker image tag "${previous_image_id}" "${rollback_image}"
fi

if docker compose build "$@"; then
  :
else
  build_exit=$?
  if [[ -n "${previous_image_id}" ]]; then
    docker image rm "${predeploy_image}" >/dev/null 2>&1 \
      || echo "临时 predeploy 标签清理失败，已保留现场证据。" >&2
  fi
  exit "${build_exit}"
fi

if [[ "${MODE}" == "build-only" ]]; then
  if [[ -n "${previous_image_id}" ]]; then
    docker image rm "${predeploy_image}" >/dev/null 2>&1 \
      || echo "临时 predeploy 标签清理失败，已保留现场证据。" >&2
  fi
  echo "Build completed. Container was not recreated because --build-only was used."
  exit 0
fi

services=()
skip_next=false
for arg in "$@"; do
  if [[ "${skip_next}" == "true" ]]; then
    skip_next=false
    continue
  fi
  case "${arg}" in
    --build-arg|--builder|-m|--memory|--progress|--ssh)
      skip_next=true
      continue
      ;;
  esac
  if [[ "${arg}" == -* || "${arg}" == *=* ]]; then
    continue
  fi
  services+=("${arg}")
done
if [ "${#services[@]}" -eq 0 ]; then
  services=("nanobot-server")
fi

health_url="${NANOBOT_DEPLOY_HEALTH_URL:-http://127.0.0.1:8000/api/v1/health}"
health_timeout_seconds="${NANOBOT_DEPLOY_HEALTH_TIMEOUT_SECONDS:-90}"

wait_for_health() {
  local health_deadline=$((SECONDS + health_timeout_seconds))
  until curl --fail --silent --show-error --max-time 5 "${health_url}" >/dev/null; do
    if (( SECONDS >= health_deadline )); then
      return 1
    fi
    sleep 2
  done
}

restore_previous_runtime() {
  [[ -n "${previous_image_id}" ]] || {
    echo "部署失败且不存在可回滚的前一 Runtime 镜像。" >&2
    return 1
  }
  echo "部署失败，正在恢复部署前 Runtime：${previous_image_id}" >&2
  if ! docker image tag "${predeploy_image}" "${runtime_image}"; then
    echo "无法恢复部署前 Runtime 镜像标签：${predeploy_image}" >&2
    return 1
  fi
  if ! docker compose up -d --force-recreate "${services[@]}"; then
    echo "部署前 Runtime 容器恢复失败。" >&2
    return 1
  fi
  if ! wait_for_health; then
    echo "前一 Runtime 已恢复容器，但健康检查仍未通过：${health_url}" >&2
    return 1
  fi
  if ! docker image tag "${previous_image_id}" "${rollback_image}"; then
    echo "前一 Runtime 已恢复，但无法更新回滚镜像标签。" >&2
    return 1
  fi
  docker image rm "${predeploy_image}" >/dev/null 2>&1 \
    || echo "临时 predeploy 标签清理失败，已保留现场证据。" >&2
  echo "已恢复部署前 Runtime：${previous_image_id}" >&2
}

if docker compose up -d --force-recreate "${services[@]}"; then
  :
else
  deploy_exit=$?
  restore_previous_runtime || true
  exit "${deploy_exit}"
fi
if ! wait_for_health; then
  echo "部署健康检查超时：${health_url}" >&2
  restore_previous_runtime || true
  exit 1
fi

current_image_id="$(docker image inspect "${runtime_image}" --format '{{.Id}}')"
if [[ -n "${previous_image_id}" && "${previous_image_id}" != "${current_image_id}" ]]; then
  docker image tag "${previous_image_id}" "${rollback_image}"
fi
if [[ -n "${previous_image_id}" ]]; then
  docker image rm "${predeploy_image}" >/dev/null 2>&1 \
    || echo "临时 predeploy 标签清理失败，已保留现场证据。" >&2
fi

if [[ -n "${previous_rollback_id}" \
  && "${previous_rollback_id}" != "${previous_image_id}" \
  && "${previous_rollback_id}" != "${current_image_id}" ]]; then
  if [[ -z "$(docker ps -aq --filter "ancestor=${previous_rollback_id}")" ]]; then
    docker image rm "${previous_rollback_id}" >/dev/null 2>&1 || \
      echo "旧回滚镜像仍被引用，已保留：${previous_rollback_id}" >&2
  else
    echo "旧回滚镜像仍被容器使用，已保留：${previous_rollback_id}" >&2
  fi
fi

echo "部署健康检查通过。当前镜像：${current_image_id}；回滚镜像：${previous_image_id:-无}"
