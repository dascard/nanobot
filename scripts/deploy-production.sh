#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

runtime_image="${NANOBOT_RUNTIME_IMAGE:-}"
if [[ ! "${runtime_image}" =~ @sha256:[0-9a-fA-F]{64}$ ]]; then
  echo "生产部署要求 NANOBOT_RUNTIME_IMAGE 使用完整 digest，例如 registry/nanobot@sha256:<64位摘要>。" >&2
  exit 2
fi

for runtime_dir in data models sentinel; do
  if [[ ! -d "${runtime_dir}" ]]; then
    echo "缺少运行目录 ${runtime_dir}/；请先执行 scripts/prepare-runtime-directories.sh。" >&2
    exit 2
  fi
done

compose=(
  docker compose
  -f docker-compose.yml
  -f docker-compose.prod.yml
)

"${compose[@]}" config --quiet
"${compose[@]}" pull
"${compose[@]}" up -d --no-build

health_url="${NANOBOT_DEPLOY_READY_URL:-http://127.0.0.1:8000/api/v1/ready}"
timeout_seconds="${NANOBOT_DEPLOY_HEALTH_TIMEOUT_SECONDS:-120}"
deadline=$((SECONDS + timeout_seconds))
until curl --fail --silent --show-error --max-time 5 "${health_url}" >/dev/null; do
  if (( SECONDS >= deadline )); then
    echo "生产部署 readiness 检查超时：${health_url}" >&2
    "${compose[@]}" ps >&2 || true
    exit 1
  fi
  sleep 2
done

"${compose[@]}" ps
echo "生产部署 readiness 检查通过：${runtime_image}"
