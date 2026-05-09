#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/docker-build.sh [docker compose build args...]
  scripts/docker-build.sh --build-only [docker compose build args...]

默认行为：构建镜像后执行 docker compose up -d --force-recreate，确保运行中的容器使用新镜像。
只想构建镜像时传 --build-only。
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

status_file="$(mktemp)"
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

docker compose build "$@"

if [[ "${MODE}" == "build-only" ]]; then
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

docker compose up -d --force-recreate "${services[@]}"
