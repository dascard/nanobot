#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/docker-build.sh [--production] [--build-only] [docker compose build args...]

默认行为：构建镜像后原子重建四个固定 Runtime 服务，并等待全部健康，确保它们使用
同一镜像。只想构建镜像时传 --build-only。部署并通过健康检查后，脚本仅保留当前镜像
和最近一个已验证回滚镜像；不会执行任何全局 prune。--production 要求 Git 和实际
Docker 构建上下文均无未跟踪输入。
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

MODE="deploy"
PRODUCTION_BUILD=false
while [[ $# -gt 0 ]]; do
  case "${1}" in
    --help|-h)
      usage
      exit 0
      ;;
    --build-only)
      MODE="build-only"
      shift
      ;;
    --production)
      PRODUCTION_BUILD=true
      shift
      ;;
    *)
      break
      ;;
  esac
done

export GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
export GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
export GIT_FULL_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
export GIT_COMMIT_DATE="$(git log -1 --format=%ci --date=iso-strict 2>/dev/null || true)"
built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

build_tmp_dir="${XDG_CACHE_HOME:-${HOME}/.cache}/nanobot-build"
mkdir -p "${build_tmp_dir}"
status_file="$(mktemp "${build_tmp_dir}/git-status.XXXXXX")"
trap 'rm -f "${status_file}"' EXIT

if git status --porcelain --untracked-files=normal >"${status_file}" 2>/dev/null; then
  if [ -s "${status_file}" ]; then
    export GIT_DIRTY=true
  else
    export GIT_DIRTY=false
  fi
else
  export GIT_DIRTY=null
fi

build_evidence_dir="${NANOBOT_BUILD_EVIDENCE_DIR:-${build_tmp_dir}/evidence/${GIT_FULL_COMMIT:-unknown}}"
mkdir -p "${build_evidence_dir}"
build_context_manifest="${build_evidence_dir}/build-context.json"
export BUILD_CONTEXT_SHA256="$(
  python scripts/build_context_manifest.py \
    --root "${REPO_ROOT}" \
    --dockerignore "${REPO_ROOT}/.dockerignore" \
    --include-git-identity \
    --output "${build_context_manifest}" \
    --print-sha
)"
context_untracked_count="$(
  python -c \
    'import json,sys; print(len(json.load(open(sys.argv[1], encoding="utf-8")).get("untracked_context_files", [])))' \
    "${build_context_manifest}"
)"
if [[ "${context_untracked_count}" -gt 0 ]]; then
  export GIT_DIRTY=true
fi
if [[ "${PRODUCTION_BUILD}" == "true" && "${GIT_DIRTY}" != "false" ]]; then
  echo "生产构建拒绝 dirty 或含未跟踪上下文的工作树；证据：${build_context_manifest}" >&2
  exit 2
fi
echo "构建上下文：sha256=${BUILD_CONTEXT_SHA256} evidence=${build_context_manifest}"

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

write_build_evidence() {
  local deployment_status="$1"
  local image_id="$2"
  local rollback_id="$3"
  shift 3
  local git_dirty_value="${GIT_DIRTY}"
  if [[ "${git_dirty_value}" == "null" ]]; then
    git_dirty_value="unknown"
  fi
  python scripts/write_runtime_build_evidence.py \
    --git-full-commit "${GIT_FULL_COMMIT}" \
    --git-dirty "${git_dirty_value}" \
    --build-context-sha256 "${BUILD_CONTEXT_SHA256}" \
    --build-context-manifest "${build_context_manifest}" \
    --image-reference "${runtime_image}" \
    --image-id "${image_id}" \
    --rollback-image-id "${rollback_id}" \
    --built-at "${built_at}" \
    --deployment-status "${deployment_status}" \
    "$@" \
    --output "${build_evidence_dir}/runtime-build.json"
}

if [[ "${MODE}" == "build-only" ]]; then
  built_image_id="$(
    docker image inspect "${runtime_image}" --format '{{.Id}}'
  )"
  write_build_evidence \
    "built_only" \
    "${built_image_id}" \
    "${previous_image_id}"
  if [[ -n "${previous_image_id}" ]]; then
    docker image rm "${predeploy_image}" >/dev/null 2>&1 \
      || echo "临时 predeploy 标签清理失败，已保留现场证据。" >&2
  fi
  echo "Build completed. Container was not recreated because --build-only was used."
  exit 0
fi

services=(
  "nanobot-server"
  "session-summary-worker"
  "outbound-delivery-worker"
  "semantic-index-worker"
)

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
  if ! docker compose up -d --force-recreate --wait \
    --wait-timeout "${health_timeout_seconds}" "${services[@]}"; then
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

if docker compose up -d --force-recreate --wait \
  --wait-timeout "${health_timeout_seconds}" "${services[@]}"; then
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
service_names=(
  "nanobot-server"
  "session-summary-worker"
  "outbound-delivery-worker"
  "semantic-index-worker"
)
container_names=(
  "nanobot-server"
  "nanobot-session-summary-worker"
  "nanobot-outbound-delivery-worker"
  "nanobot-semantic-index-worker"
)
service_image_args=()
for index in "${!service_names[@]}"; do
  service_image_id="$(
    docker inspect \
      --format '{{.Image}}' \
      "${container_names[${index}]}"
  )"
  if [[ "${service_image_id}" != "${current_image_id}" ]]; then
    echo "Runtime 服务镜像身份不一致：${service_names[${index}]}" >&2
    restore_previous_runtime || true
    exit 1
  fi
  service_image_args+=(
    --service-image
    "${service_names[${index}]}=${service_image_id}"
  )
done
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

write_build_evidence \
  "deployed" \
  "${current_image_id}" \
  "${previous_image_id}" \
  "${service_image_args[@]}"
echo "部署健康检查通过。当前镜像：${current_image_id}；回滚镜像：${previous_image_id:-无}"
