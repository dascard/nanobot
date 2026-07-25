#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROMPT_HOST_ROOT="${NANOBOT_PROMPT_HOST_ROOT:-/var/lib/nanobot/prompt-runtime}"
readonly RUNTIME_UID="${NANOBOT_RUNTIME_UID:-10001}"
readonly RUNTIME_GID="${NANOBOT_RUNTIME_GID:-10001}"
readonly SYSTEM_MIN_FREE_BYTES="${NANOBOT_SYSTEM_MIN_FREE_BYTES:-64424509440}"
readonly PULL_RESERVE_BYTES="${NANOBOT_DEPLOY_PULL_RESERVE_BYTES:-5368709120}"

usage() {
  cat <<'EOF'
用法：
  sudo scripts/manage-prompt-runtime-production.sh prepare
  sudo NANOBOT_RUNTIME_IMAGE='仓库/镜像@sha256:<64位摘要>' \
    scripts/manage-prompt-runtime-production.sh initialize|audit|plan|resolve|apply [参数]
  sudo NANOBOT_RUNTIME_IMAGE='仓库/镜像@sha256:<64位摘要>' \
    NANOBOT_RELEASE_MANIFEST='/绝对路径/release.json' \
    scripts/manage-prompt-runtime-production.sh verify-release \
      [--accept-local-override <template-key> ...]

约束：
  - canonical 默认模板只从目标不可变镜像读取。
  - mutable Runtime、状态和备份固定在仓库外宿主目录。
  - audit／verify-release 只输出 template key、状态和 Hash，不输出 Prompt 正文。
  - plan／resolve／apply 必须由运维人员逐步显式执行，不自动覆盖本地修改。
EOF
}

die() {
  printf '错误：%s\n' "$*" >&2
  exit 2
}

require_root() {
  (( EUID == 0 )) || die "生产 Prompt Runtime 管理必须通过 sudo 以受控身份执行"
}

validate_common() {
  [[ "${PROMPT_HOST_ROOT}" == /* ]] \
    || die "NANOBOT_PROMPT_HOST_ROOT 必须是绝对路径"
  case "${PROMPT_HOST_ROOT}/" in
    "${REPO_ROOT}/"*)
      die "Prompt Runtime 必须位于 Git 工作树之外"
      ;;
  esac
  [[ "${RUNTIME_UID}" =~ ^[0-9]+$ && "${RUNTIME_GID}" =~ ^[0-9]+$ ]] \
    || die "Runtime UID/GID 必须是数字"
  [[ "${SYSTEM_MIN_FREE_BYTES}" =~ ^[0-9]+$ \
      && "${PULL_RESERVE_BYTES}" =~ ^[0-9]+$ ]] \
    || die "磁盘水位必须是整数"
}

prepare_directories() {
  install -d -m 0755 -o root -g root "$(dirname "${PROMPT_HOST_ROOT}")"
  install -d -m 0750 -o root -g "${RUNTIME_GID}" "${PROMPT_HOST_ROOT}"
  install -d -m 0750 -o "${RUNTIME_UID}" -g "${RUNTIME_GID}" \
    "${PROMPT_HOST_ROOT}/live" \
    "${PROMPT_HOST_ROOT}/live/runtime" \
    "${PROMPT_HOST_ROOT}/state" \
    "${PROMPT_HOST_ROOT}/backups" \
    "${PROMPT_HOST_ROOT}/merge-inputs"
  install -d -m 0750 -o root -g "${RUNTIME_GID}" \
    "${PROMPT_HOST_ROOT}/receipts"
  local governance_lock="${PROMPT_HOST_ROOT}/live/.prompt-template-governance.lock"
  if [[ ! -e "${governance_lock}" ]]; then
    install -m 0600 -o "${RUNTIME_UID}" -g "${RUNTIME_GID}" \
      /dev/null "${governance_lock}"
  fi
  [[ -f "${governance_lock}" && ! -L "${governance_lock}" \
      && "$(stat -c '%u:%g:%a' "${governance_lock}")" \
        == "${RUNTIME_UID}:${RUNTIME_GID}:600" ]] \
    || die "Prompt Runtime 治理锁权限或类型无效"
  printf 'Prompt Runtime 受管目录已准备：%s\n' "${PROMPT_HOST_ROOT}"
}

validate_image() {
  local image="${NANOBOT_RUNTIME_IMAGE:-}"
  [[ "${image}" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
    || die "NANOBOT_RUNTIME_IMAGE 必须是完整 OCI digest"
  local free_bytes
  free_bytes="$(df -B1 --output=avail / | tail -n 1 | tr -d ' ')"
  (( free_bytes >= SYSTEM_MIN_FREE_BYTES + PULL_RESERVE_BYTES )) \
    || die "拉取镜像前无法同时保留系统水位与拉取/解包预算"
}

run_prompt_cli() {
  docker run --rm \
    --network none \
    --user "${RUNTIME_UID}:${RUNTIME_GID}" \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 128 \
    --memory 512m \
    --memory-swap 512m \
    --cpus 1 \
    --tmpfs /tmp:size=64m,mode=1777,nosuid,nodev,noexec \
    --mount "type=bind,src=${PROMPT_HOST_ROOT},dst=/var/lib/nanobot/prompt-runtime" \
    --env NANOBOT_PROMPT_DEFAULT_DIR=/app/prompts.v2.default \
    --env NANOBOT_PROMPT_RUNTIME_DIR=/var/lib/nanobot/prompt-runtime/live/runtime \
    --env NANOBOT_PROMPT_TEMPLATE_STATE_DIR=/var/lib/nanobot/prompt-runtime/state \
    --env NANOBOT_PROMPT_BACKUP_DIR=/var/lib/nanobot/prompt-runtime/backups \
    "${NANOBOT_RUNTIME_IMAGE}" \
    python scripts/manage_prompt_templates.py "$@"
}

initialize_runtime() {
  docker run --rm \
    --network none \
    --user "${RUNTIME_UID}:${RUNTIME_GID}" \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 128 \
    --memory 512m \
    --memory-swap 512m \
    --cpus 1 \
    --tmpfs /tmp:size=64m,mode=1777,nosuid,nodev,noexec \
    --mount "type=bind,src=${PROMPT_HOST_ROOT},dst=/var/lib/nanobot/prompt-runtime" \
    --env NANOBOT_PROMPT_DEFAULT_DIR=/app/prompts.v2.default \
    --env NANOBOT_PROMPT_RUNTIME_DIR=/var/lib/nanobot/prompt-runtime/live/runtime \
    --env NANOBOT_PROMPT_TEMPLATE_STATE_DIR=/var/lib/nanobot/prompt-runtime/state \
    --env NANOBOT_PROMPT_BACKUP_DIR=/var/lib/nanobot/prompt-runtime/backups \
    "${NANOBOT_RUNTIME_IMAGE}" \
    python -c 'import json; from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir; result = init_prompt_v2_runtime_dir(); print(json.dumps(result, ensure_ascii=False, sort_keys=True))'
}

verify_release() {
  local manifest="${NANOBOT_RELEASE_MANIFEST:-}"
  [[ "${manifest}" == /* && -f "${manifest}" && ! -L "${manifest}" ]] \
    || die "NANOBOT_RELEASE_MANIFEST 必须是现有普通文件的绝对路径"
  local evidence_dir
  local host_root_sha256
  local receipt_name
  evidence_dir="$(mktemp -d /var/tmp/nanobot-prompt-audit.XXXXXX)"
  chown "${RUNTIME_UID}:${RUNTIME_GID}" "${evidence_dir}"
  chmod 0700 "${evidence_dir}"
  host_root_sha256="$(printf '%s' "$(realpath -e "${PROMPT_HOST_ROOT}")" | sha256sum | cut -d' ' -f1)"

  if ! docker run --rm \
      --network none \
      --user "${RUNTIME_UID}:${RUNTIME_GID}" \
      --read-only \
      --cap-drop ALL \
      --security-opt no-new-privileges:true \
      --pids-limit 128 \
      --memory 512m \
      --memory-swap 512m \
      --cpus 1 \
      --tmpfs /tmp:size=64m,mode=1777,nosuid,nodev,noexec \
      --mount "type=bind,src=${PROMPT_HOST_ROOT},dst=/var/lib/nanobot/prompt-runtime" \
      --mount "type=bind,src=${manifest},dst=/tmp/release.json,readonly" \
      --mount "type=bind,src=${evidence_dir},dst=/tmp/evidence" \
      --env NANOBOT_PROMPT_DEFAULT_DIR=/app/prompts.v2.default \
      --env NANOBOT_PROMPT_RUNTIME_DIR=/var/lib/nanobot/prompt-runtime/live/runtime \
      --env NANOBOT_PROMPT_TEMPLATE_STATE_DIR=/var/lib/nanobot/prompt-runtime/state \
      --env NANOBOT_PROMPT_BACKUP_DIR=/var/lib/nanobot/prompt-runtime/backups \
      --env "NANOBOT_PROMPT_HOST_ROOT_SHA256=${host_root_sha256}" \
      "${NANOBOT_RUNTIME_IMAGE}" \
      python scripts/verify_prompt_runtime_release.py \
        --manifest /tmp/release.json \
        --image-reference "${NANOBOT_RUNTIME_IMAGE}" \
        --output /tmp/evidence/receipt.json \
        "$@"; then
    rm -f -- "${evidence_dir}/receipt.json"
    rmdir -- "${evidence_dir}" 2>/dev/null || true
    die "Prompt Runtime 尚未满足目标 Release；请审查输出后执行 plan/resolve/apply"
  fi
  [[ -f "${evidence_dir}/receipt.json" \
      && ! -L "${evidence_dir}/receipt.json" ]] \
    || die "目标镜像未生成 Prompt Runtime 审计回执"
  receipt_name="prompt-audit-${NANOBOT_RUNTIME_IMAGE##*@sha256:}.json"
  [[ ! -e "${PROMPT_HOST_ROOT}/receipts/${receipt_name}" ]] \
    || die "同一目标 digest 的 Prompt Runtime 回执已存在，拒绝覆盖"
  install -m 0440 -o root -g "${RUNTIME_GID}" \
    "${evidence_dir}/receipt.json" \
    "${PROMPT_HOST_ROOT}/receipts/${receipt_name}"
  rm -f -- "${evidence_dir}/receipt.json"
  rmdir -- "${evidence_dir}"
  printf 'PROMPT_AUDIT_RECEIPT=%s\n' \
    "${PROMPT_HOST_ROOT}/receipts/${receipt_name}"
}

main() {
  require_root
  validate_common
  local command="${1:-help}"
  shift || true
  case "${command}" in
    help|-h|--help)
      usage
      ;;
    prepare)
      (( $# == 0 )) || die "prepare 不接受额外参数"
      prepare_directories
      ;;
    initialize)
      (( $# == 0 )) || die "initialize 不接受额外参数"
      [[ -d "${PROMPT_HOST_ROOT}/live/runtime" ]] \
        || die "请先执行 prepare"
      validate_image
      initialize_runtime
      ;;
    audit|plan|resolve|apply|rollback)
      [[ -d "${PROMPT_HOST_ROOT}/live/runtime" ]] \
        || die "请先执行 prepare"
      validate_image
      run_prompt_cli "${command}" "$@"
      ;;
    verify-release)
      [[ -d "${PROMPT_HOST_ROOT}/live/runtime" ]] \
        || die "请先执行 prepare"
      validate_image
      verify_release "$@"
      ;;
    *)
      die "未知命令：${command}"
      ;;
  esac
}

main "$@"
