#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

runtime_image="${NANOBOT_RUNTIME_IMAGE:-}"
if [[ ! "${runtime_image}" =~ @sha256:[0-9a-fA-F]{64}$ ]]; then
  echo "生产部署要求 NANOBOT_RUNTIME_IMAGE 使用完整 digest，例如 registry/nanobot@sha256:<64位摘要>。" >&2
  exit 2
fi

release_manifest="${NANOBOT_RELEASE_MANIFEST:-}"
if [[ -z "${release_manifest}" || ! -f "${release_manifest}" ]]; then
  echo "生产部署要求 NANOBOT_RELEASE_MANIFEST 指向已验证的 ReleaseManifest。" >&2
  exit 2
fi

for runtime_dir in data models sentinel; do
  if [[ ! -d "${runtime_dir}" ]]; then
    echo "缺少运行目录 ${runtime_dir}/；请先执行 scripts/prepare-runtime-directories.sh。" >&2
    exit 2
  fi
done

exec python scripts/deploy_release.py \
  --manifest "${release_manifest}" \
  --state-dir "${NANOBOT_RELEASE_STATE_DIR:-data/release-state}" \
  --ready-url "${NANOBOT_DEPLOY_READY_URL:-http://127.0.0.1:8000/api/v1/ready}" \
  --health-timeout-seconds "${NANOBOT_DEPLOY_HEALTH_TIMEOUT_SECONDS:-120}" \
  --health-interval-seconds "${NANOBOT_DEPLOY_HEALTH_INTERVAL_SECONDS:-2}" \
  --command-timeout-seconds "${NANOBOT_DEPLOY_COMMAND_TIMEOUT_SECONDS:-600}"
