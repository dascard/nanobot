#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "正式部署必须由 root 在维护窗口执行；请用 sudo 显式传入非敏感路径和 digest 变量。" >&2
  exit 2
fi

runtime_image="${NANOBOT_RUNTIME_IMAGE:-}"
if [[ ! "${runtime_image}" =~ @sha256:[0-9a-fA-F]{64}$ ]]; then
  echo "生产部署要求 NANOBOT_RUNTIME_IMAGE 使用完整 digest，例如 registry/nanobot@sha256:<64位摘要>。" >&2
  exit 2
fi

release_manifest="${NANOBOT_RELEASE_MANIFEST:-}"
if [[ "${release_manifest}" != /* || ! -f "${release_manifest}" || -L "${release_manifest}" ]]; then
  echo "生产部署要求 NANOBOT_RELEASE_MANIFEST 指向已验证的 ReleaseManifest。" >&2
  exit 2
fi

production_root="${NANOBOT_PRODUCTION_ROOT:-}"
if [[ "${production_root}" != /* || ! -d "${production_root}" || -L "${production_root}" ]]; then
  echo "生产部署要求 NANOBOT_PRODUCTION_ROOT 指向现有生产数据根目录的绝对路径。" >&2
  exit 2
fi

export NANOBOT_PRODUCTION_ENV_FILE="${production_root}/.env"
export NANOBOT_PRODUCTION_DATA_DIR="${production_root}/data"
export NANOBOT_PRODUCTION_MODELS_DIR="${production_root}/models"
export NANOBOT_PRODUCTION_SENTINEL_DIR="${production_root}/sentinel"
export NANOBOT_PROMPT_HOST_ROOT="${NANOBOT_PROMPT_HOST_ROOT:-/var/lib/nanobot/prompt-runtime}"
release_state_dir="${NANOBOT_RELEASE_STATE_DIR:-/var/lib/nanobot/release-state}"
backup_dir="${NANOBOT_COORDINATED_BACKUP_DIR:-}"
prompt_receipt="${NANOBOT_PROMPT_AUDIT_RECEIPT:-}"
sandbox_data_root="${NANOBOT_SANDBOX_DATA_ROOT:-/srv/nanobot}"

if [[ "${release_state_dir}" != /var/lib/nanobot/release-state ]]; then
  echo "NANOBOT_RELEASE_STATE_DIR 只允许固定路径 /var/lib/nanobot/release-state。" >&2
  exit 2
fi
install -d -m 0750 -o root -g root "${release_state_dir}"

for runtime_path in \
  "${NANOBOT_PRODUCTION_ENV_FILE}" \
  "${NANOBOT_PRODUCTION_DATA_DIR}" \
  "${NANOBOT_PRODUCTION_MODELS_DIR}" \
  "${NANOBOT_PRODUCTION_SENTINEL_DIR}" \
  "${NANOBOT_PROMPT_HOST_ROOT}" \
  "${release_state_dir}" \
  "${sandbox_data_root}"; do
  if [[ ! -e "${runtime_path}" || -L "${runtime_path}" ]]; then
    echo "生产部署路径缺失或为符号链接；请先完成受控目录准备。" >&2
    exit 2
  fi
done

if [[ "${backup_dir}" != /* || ! -d "${backup_dir}" || -L "${backup_dir}" ]]; then
  echo "生产部署要求 NANOBOT_COORDINATED_BACKUP_DIR 指向本维护窗口的协调备份。" >&2
  exit 2
fi
if [[ "${prompt_receipt}" != /* || ! -f "${prompt_receipt}" || -L "${prompt_receipt}" ]]; then
  echo "生产部署要求 NANOBOT_PROMPT_AUDIT_RECEIPT 指向目标 digest 的 Prompt 审计回执。" >&2
  exit 2
fi

exec python scripts/deploy_release.py \
  --manifest "${release_manifest}" \
  --state-dir "${release_state_dir}" \
  --production-root "${production_root}" \
  --compose-env-file "${NANOBOT_PRODUCTION_ENV_FILE}" \
  --database "${NANOBOT_PRODUCTION_DATA_DIR}/nanobot.db" \
  --sandbox-data-root "${sandbox_data_root}" \
  --backup-dir "${backup_dir}" \
  --backup-risk-marker "${NANOBOT_BACKUP_RISK_MARKER:-single_disk_logical_rollback_only}" \
  --prompt-host-root "${NANOBOT_PROMPT_HOST_ROOT}" \
  --prompt-audit-receipt "${prompt_receipt}" \
  --evidence-max-age-seconds "${NANOBOT_DEPLOY_EVIDENCE_MAX_AGE_SECONDS:-21600}" \
  --system-min-free-bytes "${NANOBOT_SYSTEM_MIN_FREE_BYTES:-64424509440}" \
  --pull-reserve-bytes "${NANOBOT_DEPLOY_PULL_RESERVE_BYTES:-5368709120}" \
  --ready-url "${NANOBOT_DEPLOY_READY_URL:-http://127.0.0.1:8000/api/v1/ready}" \
  --health-timeout-seconds "${NANOBOT_DEPLOY_HEALTH_TIMEOUT_SECONDS:-120}" \
  --health-interval-seconds "${NANOBOT_DEPLOY_HEALTH_INTERVAL_SECONDS:-2}" \
  --command-timeout-seconds "${NANOBOT_DEPLOY_COMMAND_TIMEOUT_SECONDS:-600}"
