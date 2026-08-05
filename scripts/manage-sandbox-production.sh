#!/usr/bin/env bash

# Nanobot Sandbox 生产安装、验收、灰度与回滚入口。
#
# 必须直接执行本脚本，不要 source。脚本失败只会结束当前脚本进程，
# 不会把调用它的 Bash、zsh 或 SSH 会话置于 errexit/nounset 状态。

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  printf '请直接执行脚本，不要 source：sudo %s --help\n' "${BASH_SOURCE[0]}" >&2
  return 2
fi

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT
readonly CONFIG_FILE="/etc/nanobot/sandbox-production.conf"
readonly STATE_DIR="/var/lib/nanobot-sandbox-installer"
readonly SERVER_RELEASE_LINK="/opt/nanobot-server"
readonly SANDBOXD_ROOT="/opt/nanobot-sandboxd"
readonly SANDBOXD_VENV="${SANDBOXD_ROOT}/venv"
readonly PYTHON_ROOT="/opt/nanobot-python"
readonly UV_CACHE_DIR="/var/cache/nanobot-uv"
readonly EVIDENCE_CACHE_ROOT="/var/cache/nanobot"
readonly DATA_ROOT="/srv/nanobot"
readonly BUILT_PROFILE_MANIFEST="${STATE_DIR}/profile-manifest.json"
readonly INSTALLED_PROFILE_MANIFEST="/etc/nanobot/sandbox-execution-profiles.v1.json"
readonly RUNTIME_PROFILE_MANIFEST="/run/nanobot-sandboxd/profile-manifest.json"
readonly CANONICAL_PROXY_IMAGE="nanobot-sandbox-egress-proxy:2026.07.25"
readonly LOOPBACK_STORAGE_DIR="/var/lib/nanobot-sandbox-storage"
readonly LOOPBACK_IMAGE="${LOOPBACK_STORAGE_DIR}/data.xfs"
readonly XFS_LABEL="nanobot-sbx"
readonly LOCAL_SAME_DISK_RISK_MARKER="single_disk_logical_rollback_only"
readonly BACKUP_MAX_BYTES_FIXED="17179869184"
readonly BUILD_RESERVE_BYTES="21474836480"
readonly RUNTIME_UID="10001"
readonly RUNTIME_GID="10001"
# 旧 /etc/nanobot/sandbox-projects.tsv 仅可由显式迁移工具读取；本脚本不再写入。

readonly -a FIXED_SERVICES=(
  nanobot-server
  session-summary-worker
  outbound-delivery-worker
  semantic-index-worker
)
readonly -a FIXED_CONTAINERS=(
  nanobot-server
  nanobot-session-summary-worker
  nanobot-outbound-delivery-worker
  nanobot-semantic-index-worker
)
readonly -a SANDBOX_CONTROL_PLANE_PATHS=(
  core/sandbox
  sandboxd
  docker/sandbox
  deploy/apparmor
  deploy/systemd
  config/sandbox-execution-profiles.v1.json
  requirements-sandbox-smoke.in
  requirements-sandbox-smoke.lock
  requirements-sandboxd.in
  requirements-sandboxd.lock
  scripts/assign-sandbox-project-quota.sh
  scripts/build-sandbox-image.sh
  scripts/render-sandbox-profile-manifest.py
  scripts/sandbox-smoke-test.sh
)
CURRENT_COMMAND="${1:-help}"
ACTIVATED_FROM_RELEASE=""

on_error() {
  local exit_code="$1"
  local line_number="$2"
  printf '\n阶段失败：command=%s line=%s exit=%s\n' \
    "${CURRENT_COMMAND}" "${line_number}" "${exit_code}" >&2
  printf '仅当前脚本已停止；SSH 与调用它的 shell 不会退出。修正后可重复执行同一子命令。\n' >&2
}
trap 'on_error "$?" "$LINENO"' ERR

log() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

warn() {
  printf '警告：%s\n' "$*" >&2
}

die() {
  printf '错误：%s\n' "$*" >&2
  printf '当前脚本已停止；父 shell 与 SSH 会话不受影响。\n' >&2
  exit 1
}

usage() {
  cat <<'EOF'
Nanobot Sandbox 生产管理脚本

用法：
  sudo scripts/manage-sandbox-production.sh <子命令> [参数]

首次安装顺序：
  1. configure
  2. prepare-host --initialize-storage
  3. build-image
  4. smoke
  5. install-control-plane
  6. 使用 scripts/deploy-production.sh 和完整 ReleaseManifest 部署 Runtime
  7. 在 Web「Sandbox 管理」页按 canonical session 配置授权与配额

子命令：
  configure             保存设备、备份目标和镜像版本等非敏感参数
  update-release        更新已发布的精确提交与镜像版本
  promote-release       将已验收候选提交的来源提升为 origin/master
  status                只读显示主机、控制面、镜像和阶段状态
  prepare-host          安装依赖、准备数据盘、目录和 AppArmor
  build-image           构建固定版本 Sandbox 镜像
  smoke                 运行不得跳过的真实 Docker 安全矩阵
  install-control-plane 安装 Python 3.11、sandboxd、Token、UDS 和 systemd 单元
  deploy                已停用；正式 Runtime 只能由 ReleaseManifest 部署器发布
  deploy-runtime        已停用；正式 Runtime 只能由 ReleaseManifest 部署器发布
  provision-owner       已停用；请使用 Web「Sandbox 管理」页
  enable-workspace      已停用；请使用 Web「Sandbox 管理」页
  enable-assets         已停用；请使用 Web「Sandbox 管理」页
  enable-exec           已停用；请使用 Web「Sandbox 管理」页
  terminate-leases      通过 sandboxd 管理通道回收全部托管 Lease
  kill-switch           无损关闭 sandbox.enabled 与 sandbox.exec_enabled
  disable-owner         已停用；请使用 kill-switch 和 Web 管理页
  runtime-cleanup       预览或执行 runtime TTL 清理
  enable-runtime-timer  在维护审批流程建立后启用每日 timer

configure 参数：
  --storage-mode <模式>      block 或 loopback
  --data-device <设备>       block 模式的独立空白分区或 LV，例如 /dev/sdb1
  --loopback-size-gib <整数> loopback 模式容量，16..32，默认 16
  --backup-mode <模式>       independent 或 local_same_disk
  --backup-mount <目录>      备份目标目录
  --accept-local-same-disk-risk
                             明确接受同盘备份仅用于逻辑回滚、不提供硬盘灾备
  [--release <40 位提交>]
  [--release-ref <远端引用>]
  [--version <镜像版本>]
  [--sandboxd-gid 10001]

prepare-host 参数：
  --initialize-storage       首次初始化数据存储；仍需手工输入精确确认文本
  --repair-loopback-allocation
                             仅在构建前原地补足既有空 XFS 镜像的实际分配空间

update-release 参数：
  --release <40 位提交>      必须等于干净 checkout
  [--release-ref <远端引用>] origin/master，或显式候选引用
                             origin/release-candidates/<名称>
  [--version <镜像版本>]
  [--reuse-built-image]     镜像输入未变化时复用已构建镜像
  [--rerun-smoke]           配合复用镜像归档旧 Smoke 凭据并强制重新验证
  [--recover-failed-deploy] 已有控制面就绪时，归档旧控制面凭据并重新验收
                            必须同时使用 --reuse-built-image --rerun-smoke

build-image 参数：
  [--resume-failed-build-from <40 位提交>]
                            仅复用同 VERSION 的失败构建遗留镜像；要求该提交到
                            当前 RELEASE 的所有镜像构建输入完全未变

promote-release 参数：
  不接受参数。仅在 Smoke 与 control-plane-ready 完成且相同提交已进入
  origin/master 后，将 Sandbox 控制面来源提升为 origin/master；Runtime
  ReleaseManifest 状态由正式部署器独立管理。

Runtime 部署：
  本脚本不再构建或切换 nanobot-runtime:latest。取得完整 OCI digest、SBOM、
  验证结果与 ReleaseManifest 后，只能使用 scripts/deploy-production.sh。

smoke 参数：
  --retry                    删除并重建本脚本固定命名的失败 Smoke worktree

runtime-cleanup 参数：
  默认只预览；实际执行必须增加 --apply。

安全约束：
  - 不执行任何 Docker 全局 prune。
  - 不给 Nanobot Server、Worker 或 Sandbox 容器挂载 Docker Socket。
  - 不自动打开群聊 Sandbox。
  - block 模式拒绝根盘；loopback 模式只格式化固定容量的镜像文件。
  - local_same_disk 仅允许 16 GiB loopback，并保留 60 GiB 根分区水位。
  - 存储初始化、执行能力和 TTL 删除均为独立显式阶段。
  - Docker/runc Sandbox 不是 VM 级隔离。
EOF
}

require_root() {
  (( EUID == 0 )) || die "请使用 sudo 直接执行本脚本"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"
}

require_no_extra_args() {
  local command_name="$1"
  shift
  (( $# == 0 )) || die "${command_name} 不接受额外参数：$*"
}

repo_owner() {
  stat -c '%U' "${REPO_ROOT}"
}

deploy_user() {
  local owner
  owner="$(repo_owner)"
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    printf '%s\n' "${SUDO_USER}"
  else
    printf '%s\n' "${owner}"
  fi
}

deploy_home() {
  getent passwd "$(deploy_user)" | cut -d: -f6
}

run_as_deploy() {
  local user
  user="$(deploy_user)"
  if [[ "${user}" == "root" ]]; then
    "$@"
  else
    runuser -u "${user}" -- "$@"
  fi
}

repo_git() {
  run_as_deploy git -C "${REPO_ROOT}" "$@"
}

ensure_state_dir() {
  install -d -m 0700 -o root -g root "${STATE_DIR}"
}

mark_stage() {
  local name="$1"
  local value="${2:-ok}"
  local temporary
  ensure_state_dir
  temporary="$(mktemp "${STATE_DIR}/.${name}.XXXXXX")"
  printf '%s\n' "${value}" >"${temporary}"
  chmod 0600 "${temporary}"
  chown root:root "${temporary}"
  mv -f -- "${temporary}" "${STATE_DIR}/${name}"
}

stage_exists() {
  [[ -f "${STATE_DIR}/$1" && ! -L "${STATE_DIR}/$1" ]]
}

require_stage() {
  stage_exists "$1" || die "缺少阶段凭据：$1；请先完成对应子命令"
}

read_stage() {
  require_stage "$1"
  head -n 1 "${STATE_DIR}/$1"
}

assert_no_advanced_stages() {
  local stage
  for stage in \
    image-built \
    smoke-passed \
    control-plane-ready; do
    if stage_exists "${stage}"; then
      die "已存在后续阶段凭据 ${stage}，拒绝修改存储分配或 RELEASE"
    fi
  done
}

validate_release_ref() {
  local release_ref="$1"
  case "${release_ref}" in
    origin/master|origin/release-candidates/*)
      ;;
    *)
      die "RELEASE_REF 只允许 origin/master 或 origin/release-candidates/<名称>"
      ;;
  esac
  repo_git check-ref-format "refs/remotes/${release_ref}" >/dev/null 2>&1 \
    || die "RELEASE_REF 不是有效的远端跟踪分支：${release_ref}"
}

assert_release_published() {
  local release="$1"
  local release_ref="$2"
  validate_release_ref "${release_ref}"
  repo_git rev-parse --verify --quiet "${release_ref}^{commit}" >/dev/null \
    || die "发布来源不存在或不是提交：${release_ref}"
  repo_git merge-base --is-ancestor "${release}" "${release_ref}" \
    || die "RELEASE=${release} 尚未发布到 ${release_ref}"
}

validate_release() {
  [[ "${RELEASE}" =~ ^[0-9a-f]{40}$ ]] || die "RELEASE 必须是 40 位小写提交哈希"
}

validate_release_in_repo() {
  validate_release
  repo_git cat-file -e "${RELEASE}^{commit}" 2>/dev/null \
    || die "本地仓库不存在 RELEASE=${RELEASE}"
}

assert_release_checkout_current() {
  validate_release_in_repo
  [[ "$(repo_git rev-parse HEAD)" == "${RELEASE}" ]] \
    || die "生产 checkout HEAD 与配置 RELEASE 不一致"
  [[ -z "$(repo_git status --porcelain)" ]] \
    || die "生产 checkout 不干净，拒绝执行生产阶段"
  assert_release_published "${RELEASE}" "${RELEASE_REF}"
}

validate_config_values() {
  case "${STORAGE_MODE}" in
    block)
      [[ "${DATA_DEVICE}" == /dev/* ]] \
        || die "block 模式的 DATA_DEVICE 必须位于 /dev：${DATA_DEVICE}"
      [[ -z "${DATA_IMAGE}" && "${DATA_IMAGE_SIZE_BYTES}" == "0" ]] \
        || die "block 模式不得配置 DATA_IMAGE"
      ;;
    loopback)
      [[ -z "${DATA_DEVICE}" ]] \
        || die "loopback 模式不得配置 DATA_DEVICE"
      [[ "${DATA_IMAGE}" == "${LOOPBACK_IMAGE}" ]] \
        || die "loopback 镜像必须使用固定路径 ${LOOPBACK_IMAGE}"
      [[ "${DATA_IMAGE_SIZE_BYTES}" =~ ^[0-9]+$ ]] \
        || die "DATA_IMAGE_SIZE_BYTES 必须是整数"
      (( DATA_IMAGE_SIZE_BYTES >= 16 * 1024 * 1024 * 1024 \
          && DATA_IMAGE_SIZE_BYTES <= 32 * 1024 * 1024 * 1024 \
          && DATA_IMAGE_SIZE_BYTES % (1024 * 1024 * 1024) == 0 )) \
        || die "loopback 镜像容量必须是 16..32 GiB 的整数"
      ;;
    *)
      die "STORAGE_MODE 必须是 block 或 loopback"
      ;;
  esac
  [[ "${BACKUP_MOUNT}" == /* ]] \
    || die "BACKUP_MOUNT 必须是绝对路径：${BACKUP_MOUNT}"
  case "${BACKUP_MODE}" in
    independent)
      [[ "${BACKUP_RISK_MARKER}" == "none" ]] \
        || die "independent 模式不得携带同盘风险标记"
      ;;
    local_same_disk)
      [[ "${STORAGE_MODE}" == "loopback" \
          && "${DATA_IMAGE_SIZE_BYTES}" == "17179869184" ]] \
        || die "local_same_disk 仅允许 16 GiB loopback"
      [[ "${BACKUP_RISK_MARKER}" == "${LOCAL_SAME_DISK_RISK_MARKER}" ]] \
        || die "local_same_disk 缺少固定风险确认标记"
      ;;
    *)
      die "BACKUP_MODE 必须是 independent 或 local_same_disk"
      ;;
  esac
  [[ "${BACKUP_MAX_BYTES}" == "${BACKUP_MAX_BYTES_FIXED}" ]] \
    || die "单次协调备份容量上限必须固定为 16 GiB"
  [[ "${VERSION}" =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$ && "${VERSION}" != "latest" ]] \
    || die "VERSION 无效或使用了 latest"
  [[ "${SANDBOXD_GID}" =~ ^[0-9]+$ ]] \
    || die "SANDBOXD_GID 必须是整数"
  (( SANDBOXD_GID >= 1 && SANDBOXD_GID <= 2147483647 )) \
    || die "SANDBOXD_GID 超出范围"
  [[ "${SYSTEM_MIN_FREE_BYTES}" =~ ^[0-9]+$ ]] \
    || die "SYSTEM_MIN_FREE_BYTES 必须是整数"
  (( SYSTEM_MIN_FREE_BYTES >= 60 * 1024 * 1024 * 1024 )) \
    || die "根文件系统最低保留空间不得低于 60 GiB"
  validate_release
  validate_release_ref "${RELEASE_REF}"
}

load_config() {
  require_root
  [[ -f "${CONFIG_FILE}" && ! -L "${CONFIG_FILE}" ]] \
    || die "尚未配置；请先运行 configure"
  [[ "$(stat -c '%u:%a' "${CONFIG_FILE}")" == "0:600" ]] \
    || die "${CONFIG_FILE} 必须由 root 拥有且权限为 0600"

  # 旧配置没有 RELEASE_REF；先清除调用环境中的同名变量，避免环境覆盖默认来源。
  unset RELEASE_REF
  # 配置由本脚本以 root:0600 和 printf %q 写入，可安全加载。
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"

  STORAGE_MODE="${STORAGE_MODE:-block}"
  DATA_DEVICE="${DATA_DEVICE:-}"
  DATA_IMAGE="${DATA_IMAGE:-}"
  DATA_IMAGE_SIZE_BYTES="${DATA_IMAGE_SIZE_BYTES:-0}"
  BACKUP_MODE="${BACKUP_MODE:-independent}"
  BACKUP_RISK_MARKER="${BACKUP_RISK_MARKER:-none}"
  BACKUP_MAX_BYTES="${BACKUP_MAX_BYTES:-${BACKUP_MAX_BYTES_FIXED}}"
  SYSTEM_MIN_FREE_BYTES="${SYSTEM_MIN_FREE_BYTES:-64424509440}"
  RELEASE_REF="${RELEASE_REF:-origin/master}"
  : "${BACKUP_MOUNT:?}"
  : "${RELEASE:?}"
  : "${VERSION:?}"
  : "${SANDBOXD_GID:?}"
  : "${WORKSPACE_QUOTA_BYTES:?}"
  : "${ASSET_MAX_BYTES:?}"
  : "${TOTAL_QUOTA_BYTES:?}"
  : "${DISK_MAX_PERCENT:?}"
  : "${DISK_MIN_FREE_BYTES:?}"

  validate_config_values
  RELEASE_DIR="/opt/nanobot-releases/${RELEASE}"
  RESTRICTED_IMAGE="nanobot-sandbox-python:${VERSION}"
  DEVELOPER_IMAGE="nanobot-sandbox-developer:${VERSION}"
  PROXY_IMAGE="${CANONICAL_PROXY_IMAGE}"
  # 兼容现有一次性执行路径；生产凭据仍同时绑定三个镜像。
  SANDBOX_IMAGE="${RESTRICTED_IMAGE}"
}

write_config() {
  local temporary
  install -d -m 0700 -o root -g root "$(dirname "${CONFIG_FILE}")"
  temporary="$(mktemp "$(dirname "${CONFIG_FILE}")/.sandbox-production.XXXXXX")"
  {
    printf 'STORAGE_MODE=%q\n' "${STORAGE_MODE}"
    printf 'DATA_DEVICE=%q\n' "${DATA_DEVICE}"
    printf 'DATA_IMAGE=%q\n' "${DATA_IMAGE}"
    printf 'DATA_IMAGE_SIZE_BYTES=%q\n' "${DATA_IMAGE_SIZE_BYTES}"
    printf 'BACKUP_MODE=%q\n' "${BACKUP_MODE}"
    printf 'BACKUP_MOUNT=%q\n' "${BACKUP_MOUNT}"
    printf 'BACKUP_RISK_MARKER=%q\n' "${BACKUP_RISK_MARKER}"
    printf 'BACKUP_MAX_BYTES=%q\n' "${BACKUP_MAX_BYTES}"
    printf 'RELEASE=%q\n' "${RELEASE}"
    printf 'RELEASE_REF=%q\n' "${RELEASE_REF}"
    printf 'VERSION=%q\n' "${VERSION}"
    printf 'SANDBOXD_GID=%q\n' "${SANDBOXD_GID}"
    printf 'WORKSPACE_QUOTA_BYTES=%q\n' "${WORKSPACE_QUOTA_BYTES}"
    printf 'ASSET_MAX_BYTES=%q\n' "${ASSET_MAX_BYTES}"
    printf 'TOTAL_QUOTA_BYTES=%q\n' "${TOTAL_QUOTA_BYTES}"
    printf 'DISK_MAX_PERCENT=%q\n' "${DISK_MAX_PERCENT}"
    printf 'DISK_MIN_FREE_BYTES=%q\n' "${DISK_MIN_FREE_BYTES}"
    printf 'SYSTEM_MIN_FREE_BYTES=%q\n' "${SYSTEM_MIN_FREE_BYTES}"
  } >"${temporary}"
  chmod 0600 "${temporary}"
  chown root:root "${temporary}"
  mv -f -- "${temporary}" "${CONFIG_FILE}"
}

configure_command() {
  require_root

  local storage_mode="block"
  local data_device=""
  local loopback_size_gib="16"
  local backup_mode="independent"
  local backup_mount=""
  local accept_local_same_disk_risk=false
  local release
  local release_ref="origin/master"
  local version=""
  local sandboxd_gid="10001"

  release="$(repo_git rev-parse HEAD)"

  shift
  while (( $# )); do
    case "$1" in
      --storage-mode)
        [[ $# -ge 2 ]] || die "--storage-mode 缺少参数"
        storage_mode="$2"
        shift 2
        ;;
      --data-device)
        [[ $# -ge 2 ]] || die "--data-device 缺少参数"
        data_device="$2"
        shift 2
        ;;
      --loopback-size-gib)
        [[ $# -ge 2 ]] || die "--loopback-size-gib 缺少参数"
        loopback_size_gib="$2"
        shift 2
        ;;
      --backup-mount)
        [[ $# -ge 2 ]] || die "--backup-mount 缺少参数"
        backup_mount="$2"
        shift 2
        ;;
      --backup-mode)
        [[ $# -ge 2 ]] || die "--backup-mode 缺少参数"
        backup_mode="$2"
        shift 2
        ;;
      --accept-local-same-disk-risk)
        accept_local_same_disk_risk=true
        shift
        ;;
      --release)
        [[ $# -ge 2 ]] || die "--release 缺少参数"
        release="$2"
        shift 2
        ;;
      --release-ref)
        [[ $# -ge 2 ]] || die "--release-ref 缺少参数"
        release_ref="$2"
        shift 2
        ;;
      --version)
        [[ $# -ge 2 ]] || die "--version 缺少参数"
        version="$2"
        shift 2
        ;;
      --sandboxd-gid)
        [[ $# -ge 2 ]] || die "--sandboxd-gid 缺少参数"
        sandboxd_gid="$2"
        shift 2
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        die "configure 未知参数：$1"
        ;;
    esac
  done

  [[ -n "${backup_mount}" ]] || die "必须提供 --backup-mount"
  [[ -d "${backup_mount}" ]] || die "备份目录不存在：${backup_mount}"
  [[ ! -L "${backup_mount}" ]] || die "备份目录不得是符号链接"

  BACKUP_MODE="${backup_mode}"
  case "${BACKUP_MODE}" in
    independent)
      [[ "${accept_local_same_disk_risk}" == "false" ]] \
        || die "--accept-local-same-disk-risk 仅用于 local_same_disk"
      BACKUP_RISK_MARKER="none"
      ;;
    local_same_disk)
      [[ "${accept_local_same_disk_risk}" == "true" ]] \
        || die "local_same_disk 必须显式传入 --accept-local-same-disk-risk"
      BACKUP_RISK_MARKER="${LOCAL_SAME_DISK_RISK_MARKER}"
      ;;
    *)
      die "--backup-mode 必须是 independent 或 local_same_disk"
      ;;
  esac
  BACKUP_MAX_BYTES="${BACKUP_MAX_BYTES_FIXED}"

  STORAGE_MODE="${storage_mode}"
  case "${STORAGE_MODE}" in
    block)
      [[ -n "${data_device}" ]] || die "block 模式必须提供 --data-device"
      [[ "${data_device}" == /dev/* ]] || die "--data-device 必须位于 /dev"
      [[ -b "${data_device}" ]] || die "块设备不存在：${data_device}"
      [[ "${loopback_size_gib}" == "16" ]] \
        || die "block 模式不得提供 --loopback-size-gib"
      DATA_DEVICE="$(readlink -f "${data_device}")"
      DATA_IMAGE=""
      DATA_IMAGE_SIZE_BYTES=0
      DISK_MIN_FREE_BYTES=53687091200
      ;;
    loopback)
      [[ -z "${data_device}" ]] || die "loopback 模式不得提供 --data-device"
      [[ "${loopback_size_gib}" =~ ^[0-9]+$ ]] \
        || die "--loopback-size-gib 必须是整数"
      (( loopback_size_gib >= 16 && loopback_size_gib <= 32 )) \
        || die "--loopback-size-gib 必须位于 16..32"
      DATA_DEVICE=""
      DATA_IMAGE="${LOOPBACK_IMAGE}"
      DATA_IMAGE_SIZE_BYTES=$((loopback_size_gib * 1024 * 1024 * 1024))
      DISK_MIN_FREE_BYTES=2147483648
      ;;
    *)
      die "--storage-mode 必须是 block 或 loopback"
      ;;
  esac
  BACKUP_MOUNT="$(realpath -e "${backup_mount}")"
  RELEASE="${release}"
  RELEASE_REF="${release_ref}"
  VERSION="${version:-${release:0:7}-$(date +%Y%m%d)}"
  SANDBOXD_GID="${sandboxd_gid}"
  WORKSPACE_QUOTA_BYTES=2147483648
  ASSET_MAX_BYTES=536870912
  TOTAL_QUOTA_BYTES=10737418240
  DISK_MAX_PERCENT=80
  SYSTEM_MIN_FREE_BYTES=64424509440

  validate_config_values
  assert_backup_target_policy
  validate_release_in_repo
  [[ "$(repo_git rev-parse HEAD)" == "${RELEASE}" ]] \
    || die "configure 要求 RELEASE 等于当前 HEAD"
  [[ -z "$(repo_git status --porcelain)" ]] \
    || die "生产 checkout 不干净；请先完成审查、测试、提交和推送"
  repo_git ls-files --error-unmatch scripts/manage-sandbox-production.sh \
    >/dev/null 2>&1 \
    || die "本管理脚本尚未纳入 RELEASE"
  assert_release_published "${RELEASE}" "${RELEASE_REF}"
  [[ ! -e "${CONFIG_FILE}" ]] \
    || die "配置已存在：${CONFIG_FILE}；为防止阶段凭据错配，脚本拒绝直接覆盖"
  write_config
  ensure_state_dir

  log "配置已保存：${CONFIG_FILE}"
  printf 'STORAGE_MODE=%s\n' "${STORAGE_MODE}"
  if [[ "${STORAGE_MODE}" == "block" ]]; then
    printf 'DATA_DEVICE=%s\n' "${DATA_DEVICE}"
  else
    printf 'DATA_IMAGE=%s\n' "${DATA_IMAGE}"
    printf 'DATA_IMAGE_SIZE_GIB=%s\n' "$((DATA_IMAGE_SIZE_BYTES / 1024 / 1024 / 1024))"
  fi
  printf 'BACKUP_MOUNT=%s\n' "${BACKUP_MOUNT}"
  printf 'BACKUP_MODE=%s\n' "${BACKUP_MODE}"
  printf 'BACKUP_MAX_GIB=%s\n' "$((BACKUP_MAX_BYTES / 1024 / 1024 / 1024))"
  printf 'SYSTEM_MIN_FREE_GIB=%s\n' \
    "$((SYSTEM_MIN_FREE_BYTES / 1024 / 1024 / 1024))"
  printf 'BACKUP_RISK_MARKER=%s\n' "${BACKUP_RISK_MARKER}"
  printf 'RELEASE=%s\n' "${RELEASE}"
  printf 'RELEASE_REF=%s\n' "${RELEASE_REF}"
  printf 'VERSION=%s\n' "${VERSION}"
  printf '下一步：sudo %s prepare-host --initialize-storage\n' "$0"
}

update_release_command() {
  require_root
  load_config

  local previous_release="${RELEASE}"
  local previous_version="${VERSION}"
  local new_release=""
  local new_release_ref="${RELEASE_REF}"
  local new_version=""
  local reuse_built_image=false
  local rerun_smoke=false
  local recover_failed_deploy=false
  local smoke_stage_present=false
  local control_plane_stage_present=false
  local legacy_runtime_stage_present=false
  local archived_smoke_stage=""
  local archived_control_plane_stage=""
  local archived_legacy_runtime_stage=""

  shift
  while (( $# )); do
    case "$1" in
      --release)
        [[ $# -ge 2 ]] || die "--release 缺少参数"
        new_release="$2"
        shift 2
        ;;
      --release-ref)
        [[ $# -ge 2 ]] || die "--release-ref 缺少参数"
        new_release_ref="$2"
        shift 2
        ;;
      --version)
        [[ $# -ge 2 ]] || die "--version 缺少参数"
        new_version="$2"
        shift 2
        ;;
      --reuse-built-image)
        reuse_built_image=true
        shift
        ;;
      --rerun-smoke)
        rerun_smoke=true
        shift
        ;;
      --recover-failed-deploy)
        recover_failed_deploy=true
        shift
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        die "update-release 未知参数：$1"
        ;;
    esac
  done

  [[ "${new_release}" =~ ^[0-9a-f]{40}$ ]] \
    || die "update-release 必须提供 40 位小写 --release"
  repo_git cat-file -e "${new_release}^{commit}" 2>/dev/null \
    || die "本地仓库不存在新 RELEASE=${new_release}"
  [[ "$(repo_git rev-parse HEAD)" == "${new_release}" ]] \
    || die "update-release 要求新 RELEASE 等于当前 HEAD"
  [[ -z "$(repo_git status --porcelain)" ]] \
    || die "生产 checkout 不干净；请先完成审查、测试、提交和推送"
  assert_release_published "${new_release}" "${new_release_ref}"
  if [[ "${rerun_smoke}" == "true" \
      && "${reuse_built_image}" != "true" ]]; then
    die "--rerun-smoke 只能与 --reuse-built-image 同时使用"
  fi
  if [[ "${recover_failed_deploy}" == "true" \
      && ( "${reuse_built_image}" != "true" \
        || "${rerun_smoke}" != "true" ) ]]; then
    die "--recover-failed-deploy 必须同时使用 --reuse-built-image --rerun-smoke"
  fi

  if stage_exists runtime-deployed; then
    legacy_runtime_stage_present=true
  fi

  if [[ "${reuse_built_image}" == "true" ]]; then
    require_stage image-built
    if stage_exists control-plane-ready; then
      [[ "${recover_failed_deploy}" == "true" ]] \
        || die "已存在后续阶段凭据 control-plane-ready，拒绝复用已构建镜像更新 RELEASE"
      assert_control_plane_current
      control_plane_stage_present=true
    elif [[ "${recover_failed_deploy}" == "true" ]]; then
      die "--recover-failed-deploy 要求存在 control-plane-ready"
    fi
    if stage_exists smoke-passed; then
      [[ "${rerun_smoke}" == "true" ]] \
        || die "已存在后续阶段凭据 smoke-passed；如需更新 RELEASE，必须显式增加 --rerun-smoke 并重新验证"
      assert_smoke_current
      smoke_stage_present="true"
    elif [[ "${recover_failed_deploy}" == "true" ]]; then
      die "重新验收控制面要求存在 smoke-passed"
    fi
    repo_git merge-base --is-ancestor "${previous_release}" "${new_release}" \
      || die "复用已构建镜像只允许更新到当前 RELEASE 的快进后代"
    if ! repo_git diff --quiet "${previous_release}" "${new_release}" -- \
        scripts/build-sandbox-image.sh \
        scripts/render-sandbox-profile-manifest.py \
        config/sandbox-execution-profiles.v1.json \
        docker/sandbox/python \
        docker/sandbox/developer \
        docker/sandbox/egress-proxy; then
      die "Sandbox 镜像构建脚本或上下文已变化，必须重新构建，禁止复用"
    fi
    [[ -z "${new_version}" || "${new_version}" == "${previous_version}" ]] \
      || die "复用已构建镜像时不得修改 VERSION"
    image_id_from_stage >/dev/null
  else
    assert_no_advanced_stages
  fi

  if [[ "${smoke_stage_present}" == "true" ]]; then
    archived_smoke_stage="${STATE_DIR}/smoke-passed.superseded-${previous_release}-by-${new_release}"
    [[ ! -e "${archived_smoke_stage}" \
        && ! -L "${archived_smoke_stage}" ]] \
      || die "旧 Smoke 阶段凭据归档目标已存在：${archived_smoke_stage}"
  fi
  if [[ "${control_plane_stage_present}" == "true" ]]; then
    archived_control_plane_stage="${STATE_DIR}/control-plane-ready.superseded-${previous_release}-by-${new_release}"
    [[ ! -e "${archived_control_plane_stage}" \
        && ! -L "${archived_control_plane_stage}" ]] \
      || die "旧控制面阶段凭据归档目标已存在：${archived_control_plane_stage}"
  fi
  if [[ "${legacy_runtime_stage_present}" == "true" ]]; then
    archived_legacy_runtime_stage="${STATE_DIR}/runtime-deployed.legacy-${previous_release}-by-${new_release}"
    [[ ! -e "${archived_legacy_runtime_stage}" \
        && ! -L "${archived_legacy_runtime_stage}" ]] \
      || die "旧 Runtime 遗留凭据归档目标已存在：${archived_legacy_runtime_stage}"
  fi
  if [[ -n "${archived_smoke_stage}" ]]; then
    mv -- "${STATE_DIR}/smoke-passed" "${archived_smoke_stage}"
  fi
  if [[ -n "${archived_control_plane_stage}" ]]; then
    mv -- "${STATE_DIR}/control-plane-ready" "${archived_control_plane_stage}"
  fi
  if [[ -n "${archived_legacy_runtime_stage}" ]]; then
    mv -- "${STATE_DIR}/runtime-deployed" "${archived_legacy_runtime_stage}"
  fi

  RELEASE="${new_release}"
  RELEASE_REF="${new_release_ref}"
  if [[ "${reuse_built_image}" == "true" ]]; then
    VERSION="${previous_version}"
  else
    VERSION="${new_version:-${new_release:0:7}-$(date +%Y%m%d)}"
  fi
  validate_config_values
  write_config

  log "RELEASE 已受控更新；存储、备份、配额和 GID 配置保持不变"
  if [[ "${reuse_built_image}" == "true" ]]; then
    log "Sandbox 镜像输入未变化；保留既有 VERSION、IMAGE ID 和 image-built 凭据"
    log "旧 RELEASE 的失败 Smoke worktree 保留为现场证据，未自动删除"
  fi
  if [[ -n "${archived_smoke_stage}" ]]; then
    log "旧 Smoke 阶段凭据已归档：${archived_smoke_stage}"
    log "新 RELEASE 必须重新运行真实 Docker Smoke"
  fi
  if [[ -n "${archived_control_plane_stage}" ]]; then
    log "旧控制面阶段凭据已归档：${archived_control_plane_stage}"
    log "新 RELEASE 必须重新安装并验收 sandboxd 控制面"
  fi
  if [[ -n "${archived_legacy_runtime_stage}" ]]; then
    log "旧 Runtime 遗留凭据已归档：${archived_legacy_runtime_stage}"
    log "Runtime 发布状态继续由 ReleaseManifest 部署器独立维护"
  fi
  printf 'PREVIOUS_RELEASE=%s\n' "${previous_release}"
  printf 'PREVIOUS_VERSION=%s\n' "${previous_version}"
  printf 'RELEASE=%s\n' "${RELEASE}"
  printf 'RELEASE_REF=%s\n' "${RELEASE_REF}"
  printf 'VERSION=%s\n' "${VERSION}"
  printf '下一步：重新运行 sudo %s prepare-host；若状态报告稀疏，只能增加 --repair-loopback-allocation\n' "$0"
}

promote_release_command() {
  require_no_extra_args "$@"
  require_root
  load_config
  require_stage smoke-passed
  require_stage control-plane-ready
  assert_release_checkout_current
  assert_smoke_current
  assert_control_plane_current

  if [[ "${RELEASE_REF}" == "origin/master" ]]; then
    log "RELEASE 已由 origin/master 固定，无需重复提升"
    return 0
  fi
  [[ "${RELEASE_REF}" == origin/release-candidates/* ]] \
    || die "当前发布来源不是受控候选引用：${RELEASE_REF}"
  assert_release_published "${RELEASE}" origin/master

  local previous_release_ref="${RELEASE_REF}"
  RELEASE_REF="origin/master"
  validate_config_values
  write_config

  log "已将相同 RELEASE 的发布来源提升为 origin/master；阶段凭据保持不变"
  printf 'RELEASE=%s\n' "${RELEASE}"
  printf 'PREVIOUS_RELEASE_REF=%s\n' "${previous_release_ref}"
  printf 'RELEASE_REF=%s\n' "${RELEASE_REF}"
}

assert_data_device_not_root_chain() {
  local root_source
  local root_disks
  local data_disks
  root_source="$(findmnt -n -o SOURCE /)"
  if lsblk -s -n -o PATH "${root_source}" | grep -Fxq "${DATA_DEVICE}"; then
    die "DATA_DEVICE 属于根文件系统设备链：${DATA_DEVICE}"
  fi
  root_disks="$(lsblk -s -n -o PATH,TYPE "${root_source}" \
    | awk '$2 == "disk" {print $1}' | sort -u)"
  data_disks="$(lsblk -s -n -o PATH,TYPE "${DATA_DEVICE}" \
    | awk '$2 == "disk" {print $1}' | sort -u)"
  if [[ -n "${root_disks}" && -n "${data_disks}" ]] \
    && comm -12 <(printf '%s\n' "${root_disks}") \
      <(printf '%s\n' "${data_disks}") | grep -q .; then
    die "DATA_DEVICE 与根文件系统位于同一物理磁盘"
  fi
}

assert_backup_mount_independent_from_root() {
  local root_major_minor
  local backup_major_minor
  local root_source
  local backup_source
  local root_device=""
  local backup_device=""
  local root_disks=""
  local backup_disks=""

  mountpoint -q "${BACKUP_MOUNT}" \
    || die "BACKUP_MOUNT 不是独立挂载点：${BACKUP_MOUNT}"
  root_major_minor="$(findmnt -n -T / -o MAJ:MIN)"
  backup_major_minor="$(findmnt -n -T "${BACKUP_MOUNT}" -o MAJ:MIN)"
  [[ -n "${root_major_minor}" && -n "${backup_major_minor}" ]] \
    || die "无法核对根文件系统与备份挂载点"
  [[ "${backup_major_minor}" != "${root_major_minor}" ]] \
    || die "loopback 模式的备份不得位于根文件系统"

  root_source="$(findmnt -n -T / -o SOURCE)"
  backup_source="$(findmnt -n -T "${BACKUP_MOUNT}" -o SOURCE)"
  root_device="$(readlink -f "${root_source}" 2>/dev/null || true)"
  backup_device="$(readlink -f "${backup_source}" 2>/dev/null || true)"
  if [[ -b "${root_device}" && -b "${backup_device}" ]]; then
    root_disks="$(lsblk -s -n -o PATH,TYPE "${root_device}" \
      | awk '$2 == "disk" {print $1}' | sort -u)"
    backup_disks="$(lsblk -s -n -o PATH,TYPE "${backup_device}" \
      | awk '$2 == "disk" {print $1}' | sort -u)"
    if [[ -n "${root_disks}" && -n "${backup_disks}" ]] \
      && comm -12 <(printf '%s\n' "${root_disks}") \
        <(printf '%s\n' "${backup_disks}") | grep -q .; then
      die "loopback 模式的备份与根文件系统位于同一物理磁盘"
    fi
  fi
}

assert_local_same_disk_backup_target() {
  local root_major_minor
  local backup_major_minor

  [[ -d "${BACKUP_MOUNT}" && ! -L "${BACKUP_MOUNT}" ]] \
    || die "local_same_disk 备份目标必须是非符号链接目录"
  root_major_minor="$(findmnt -n -T / -o MAJ:MIN)"
  backup_major_minor="$(findmnt -n -T "${BACKUP_MOUNT}" -o MAJ:MIN)"
  [[ -n "${root_major_minor}" && "${backup_major_minor}" == "${root_major_minor}" ]] \
    || die "local_same_disk 备份目标必须位于根文件系统"
  case "${BACKUP_MOUNT}" in
    /)
      die "备份目标不得直接使用根目录"
      ;;
    "${DATA_ROOT}"|"${DATA_ROOT}"/*)
      die "备份目标不得位于 Sandbox 数据根目录"
      ;;
    "${LOOPBACK_STORAGE_DIR}"|"${LOOPBACK_STORAGE_DIR}"/*)
      die "备份目标不得位于 loopback 镜像目录"
      ;;
    "${REPO_ROOT}"|"${REPO_ROOT}"/*)
      die "备份目标不得位于生产仓库"
      ;;
  esac
}

assert_backup_target_policy() {
  case "${BACKUP_MODE}" in
    independent)
      mountpoint -q "${BACKUP_MOUNT}" \
        || die "independent 备份目标必须是独立挂载点：${BACKUP_MOUNT}"
      if [[ "${STORAGE_MODE}" == "loopback" ]]; then
        assert_backup_mount_independent_from_root
      fi
      ;;
    local_same_disk)
      assert_local_same_disk_backup_target
      ;;
  esac
}

ensure_data_mountpoint_empty() {
  if [[ -d "${DATA_ROOT}" ]] \
    && find "${DATA_ROOT}" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    die "${DATA_ROOT} 非空，拒绝覆盖挂载"
  fi
  install -d -m 0750 "${DATA_ROOT}"
}

append_fstab_line() {
  local fstab_line="$1"
  local fstab_backup=""
  local existing_line=""

  existing_line="$(awk -v target="${DATA_ROOT}" \
    '$1 !~ /^#/ && $2 == target {print}' /etc/fstab)"
  if [[ -n "${existing_line}" && "${existing_line}" != "${fstab_line}" ]]; then
    die "/etc/fstab 已存在其他 ${DATA_ROOT} 挂载配置"
  fi
  if ! grep -Fxq "${fstab_line}" /etc/fstab; then
    fstab_backup="/etc/fstab.pre-nanobot-sandbox-$(date +%Y%m%dT%H%M%S)"
    cp -a /etc/fstab "${fstab_backup}"
    printf '%s\n' "${fstab_line}" >>/etc/fstab
  fi
  findmnt --verify --verbose
}

loopback_fstab_line() {
  printf '%s %s xfs loop,nodev,nosuid,prjquota,X-fstrim.notrim 0 0\n' \
    "${DATA_IMAGE}" "${DATA_ROOT}"
}

legacy_loopback_fstab_line() {
  printf '%s %s xfs loop,nodev,nosuid,prjquota 0 0\n' \
    "${DATA_IMAGE}" "${DATA_ROOT}"
}

assert_loopback_fstrim_excluded() {
  local expected_line
  local existing_line
  expected_line="$(loopback_fstab_line)"
  existing_line="$(awk -v target="${DATA_ROOT}" \
    '$1 !~ /^#/ && $2 == target {print}' /etc/fstab)"
  [[ "${existing_line}" == "${expected_line}" ]] \
    || die "${DATA_ROOT} 未通过 X-fstrim.notrim 排除定时 TRIM"
  if systemctl is-active --quiet fstrim.service; then
    die "fstrim.service 正在运行，无法确认本轮未触碰 loopback 镜像"
  fi
}

ensure_loopback_fstab_line() (
  local expected_line
  local legacy_line
  local existing_line
  local fstab_backup=""
  local temporary=""

  # 仅由 EXIT trap 间接调用。
  # shellcheck disable=SC2317
  cleanup_fstab_update() {
    if [[ -n "${temporary}" && -f "${temporary}" \
        && ! -L "${temporary}" ]]; then
      rm -f -- "${temporary}"
    fi
  }
  trap cleanup_fstab_update EXIT

  [[ -f /etc/fstab && ! -L /etc/fstab ]] \
    || die "/etc/fstab 必须是非符号链接普通文件"
  expected_line="$(loopback_fstab_line)"
  legacy_line="$(legacy_loopback_fstab_line)"
  existing_line="$(awk -v target="${DATA_ROOT}" \
    '$1 !~ /^#/ && $2 == target {print}' /etc/fstab)"
  if systemctl is-active --quiet fstrim.service; then
    die "fstrim.service 正在运行，拒绝修改或确认 loopback fstab 配置"
  fi

  if [[ -z "${existing_line}" ]]; then
    append_fstab_line "${expected_line}"
  elif [[ "${existing_line}" == "${expected_line}" ]]; then
    findmnt --verify --verbose
  elif [[ "${existing_line}" == "${legacy_line}" ]]; then
    fstab_backup="/etc/fstab.pre-nanobot-sandbox-fstrim-$(date +%Y%m%dT%H%M%S)"
    [[ ! -e "${fstab_backup}" ]] \
      || die "fstab 备份路径已存在，拒绝覆盖：${fstab_backup}"
    cp -a /etc/fstab "${fstab_backup}"
    temporary="$(mktemp /etc/.fstab.nanobot-fstrim.XXXXXX)"
    cp --attributes-only --preserve=all /etc/fstab "${temporary}"
    awk -v old="${legacy_line}" -v replacement="${expected_line}" \
      '$0 == old {$0 = replacement} {print}' /etc/fstab >"${temporary}"
    [[ "$(awk -v target="${DATA_ROOT}" \
        '$1 !~ /^#/ && $2 == target {print}' "${temporary}")" \
        == "${expected_line}" ]] \
      || die "无法生成排除定时 TRIM 的 fstab 配置"
    findmnt --verify --verbose --tab-file "${temporary}"
    mv -- "${temporary}" /etc/fstab
    temporary=""
    findmnt --verify --verbose
    log "已将脚本生成的旧 loopback fstab 行迁移为 X-fstrim.notrim；备份：${fstab_backup}"
  else
    die "/etc/fstab 已存在其他 ${DATA_ROOT} 挂载配置，拒绝覆盖"
  fi

  assert_loopback_fstrim_excluded
  trap - EXIT
)

prepare_block_data_mount() {
  local allow_format="$1"
  local current_source=""
  local current_type=""
  local confirmation=""
  local data_uuid=""
  local fstab_line=""

  assert_data_device_not_root_chain

  if mountpoint -q "${DATA_ROOT}"; then
    current_source="$(readlink -f "$(findmnt -n -o SOURCE "${DATA_ROOT}")")"
    [[ "${current_source}" == "$(readlink -f "${DATA_DEVICE}")" ]] \
      || die "${DATA_ROOT} 已挂载到非配置设备：${current_source}"
  else
    current_type="$(blkid -s TYPE -o value "${DATA_DEVICE}" 2>/dev/null || true)"
    if [[ -z "${current_type}" ]]; then
      [[ "${allow_format}" == "true" ]] \
        || die "${DATA_ROOT} 尚未挂载；首次执行必须增加 --initialize-storage"
      [[ -t 0 ]] || die "格式化要求交互式 TTY，拒绝非交互执行"
      [[ "$(lsblk -dn -o TYPE "${DATA_DEVICE}")" =~ ^(part|lvm)$ ]] \
        || die "DATA_DEVICE 必须是独立分区或 LV，不能直接使用整盘"
      if findmnt -rn -S "${DATA_DEVICE}" | grep -q .; then
        die "DATA_DEVICE 已挂载"
      fi

      log "DATA_DEVICE 只读签名检查"
      wipefs -n "${DATA_DEVICE}" || true
      printf '即将格式化 %s。请输入：FORMAT %s\n' "${DATA_DEVICE}" "${DATA_DEVICE}"
      IFS= read -r confirmation
      [[ "${confirmation}" == "FORMAT ${DATA_DEVICE}" ]] \
        || die "设备确认不匹配，未格式化"

      mkfs.xfs -L "${XFS_LABEL}" "${DATA_DEVICE}"
      data_uuid="$(blkid -s UUID -o value "${DATA_DEVICE}")"
      [[ -n "${data_uuid}" ]] || die "无法读取新 XFS UUID"
      mark_stage data-formatted \
        "device=$(readlink -f "${DATA_DEVICE}")|uuid=${data_uuid}"
    else
      [[ "${current_type}" == "xfs" ]] \
        || die "DATA_DEVICE 已存在非 XFS 文件系统：${current_type}"
      require_stage data-formatted
      data_uuid="$(blkid -s UUID -o value "${DATA_DEVICE}")"
      [[ "$(read_stage data-formatted)" \
          == "device=$(readlink -f "${DATA_DEVICE}")|uuid=${data_uuid}" ]] \
        || die "现有 XFS 与本脚本的格式化凭据不匹配"
      log "检测到本脚本已完成格式化，继续恢复 fstab/mount 阶段"
    fi

    ensure_data_mountpoint_empty

    fstab_line="UUID=${data_uuid} ${DATA_ROOT} xfs defaults,nodev,nosuid,prjquota 0 2"
    append_fstab_line "${fstab_line}"
    mount "${DATA_ROOT}"
  fi
}

verify_loopback_image_metadata() {
  [[ -f "${DATA_IMAGE}" && ! -L "${DATA_IMAGE}" ]] \
    || die "loopback 镜像必须是非符号链接普通文件：${DATA_IMAGE}"
  [[ "$(stat -c '%u:%a' "${DATA_IMAGE}")" == "0:600" ]] \
    || die "loopback 镜像必须由 root 拥有且权限为 0600"
  [[ "$(stat -c '%s' "${DATA_IMAGE}")" == "${DATA_IMAGE_SIZE_BYTES}" ]] \
    || die "loopback 镜像容量与配置不一致"
}

verify_loopback_image() {
  verify_loopback_image_metadata
  if ! "${SCRIPT_DIR}/check-loopback-image-allocation.sh" \
      "${DATA_IMAGE}" "${DATA_IMAGE_SIZE_BYTES}"; then
    die "loopback 镜像未真实预分配，拒绝继续生产阶段"
  fi
}

verify_loopback_mount() {
  local mount_source
  local backing_file

  mountpoint -q "${DATA_ROOT}" || die "${DATA_ROOT} 尚未挂载"
  mount_source="$(readlink -f "$(findmnt -n -o SOURCE "${DATA_ROOT}")")"
  [[ -b "${mount_source}" && "$(lsblk -dn -o TYPE "${mount_source}")" == "loop" ]] \
    || die "${DATA_ROOT} 不是 loopback 块设备挂载"
  backing_file="$(losetup --noheadings --output BACK-FILE "${mount_source}" \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [[ -n "${backing_file}" ]] || die "无法读取 loopback backing file"
  [[ "$(realpath -e "${backing_file}")" == "${DATA_IMAGE}" ]] \
    || die "loopback backing file 与配置不一致"
}

create_loopback_image() (
  local confirmation=""
  local required_free
  local root_free
  local temporary="${DATA_IMAGE}.initializing"
  local loop_device=""
  local stale_loop=""
  local data_uuid=""
  local size_gib

  # 仅由 EXIT trap 间接调用。
  # shellcheck disable=SC2317
  cleanup_loopback_initialization() {
    if [[ -n "${loop_device}" && -n "${temporary}" ]] \
      && losetup --associated "${temporary}" --noheadings --output NAME \
        | grep -Fxq "${loop_device}"; then
      losetup --detach "${loop_device}" || true
    fi
    if [[ -n "${temporary}" && -f "${temporary}" && ! -L "${temporary}" ]]; then
      rm -f -- "${temporary}"
    fi
  }

  trap cleanup_loopback_initialization EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  [[ -t 0 ]] || die "创建 loopback 镜像要求交互式 TTY"
  [[ ! -e "${DATA_IMAGE}" ]] \
    || die "loopback 镜像路径已存在但缺少本轮阶段凭据，拒绝覆盖"
  [[ ! -L "${LOOPBACK_STORAGE_DIR}" ]] \
    || die "loopback 存储目录不得是符号链接"
  install -d -m 0700 -o root -g root "${LOOPBACK_STORAGE_DIR}"
  [[ "$(stat -c '%u:%a' "${LOOPBACK_STORAGE_DIR}")" == "0:700" ]] \
    || die "loopback 存储目录权限错误"
  if [[ -e "${temporary}" ]]; then
    [[ -f "${temporary}" && ! -L "${temporary}" \
        && "$(stat -c '%u' "${temporary}")" == "0" ]] \
      || die "残留初始化路径不是本脚本可安全处理的 root 普通文件"
    while IFS= read -r stale_loop; do
      [[ -n "${stale_loop}" ]] || continue
      if findmnt -rn -S "${stale_loop}" | grep -q .; then
        die "残留初始化 loop 仍被挂载，拒绝自动处理：${stale_loop}"
      fi
      losetup --detach "${stale_loop}"
    done < <(losetup --associated "${temporary}" --noheadings --output NAME)
    rm -f -- "${temporary}"
  fi

  # 预分配后仍保留 60 GiB 系统空间和 20 GiB Docker 构建余量。
  root_free="$(df -B1 --output=avail / | tail -n 1 | tr -d ' ')"
  required_free=$((DATA_IMAGE_SIZE_BYTES + SYSTEM_MIN_FREE_BYTES + BUILD_RESERVE_BYTES))
  (( root_free >= required_free )) \
    || die "根分区空间不足：预分配后无法同时保留 60 GiB 系统空间和 20 GiB 构建余量"

  size_gib=$((DATA_IMAGE_SIZE_BYTES / 1024 / 1024 / 1024))
  printf '将创建并格式化 %s GiB XFS 镜像文件 %s。\n' "${size_gib}" "${DATA_IMAGE}"
  printf '不会格式化任何现有块设备。请输入：CREATE LOOPBACK %sGiB\n' "${size_gib}"
  IFS= read -r confirmation
  [[ "${confirmation}" == "CREATE LOOPBACK ${size_gib}GiB" ]] \
    || die "loopback 创建确认不匹配，未写入"

  (umask 077; : >"${temporary}")
  if ! fallocate --length "${DATA_IMAGE_SIZE_BYTES}" "${temporary}"; then
    die "loopback 镜像预分配失败"
  fi
  chmod 0600 "${temporary}"
  chown root:root "${temporary}"
  if ! loop_device="$(losetup --find --show "${temporary}")"; then
    die "无法为临时镜像分配 loop 设备"
  fi
  if [[ "$(realpath -e "$(losetup --noheadings --output BACK-FILE "${loop_device}" \
      | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')")" \
      != "$(realpath -e "${temporary}")" ]]; then
    losetup --detach "${loop_device}" || true
    die "loop 设备 backing file 校验失败"
  fi
  if ! mkfs.xfs -K -L "${XFS_LABEL}" "${loop_device}"; then
    losetup --detach "${loop_device}" || true
    die "XFS 镜像格式化失败"
  fi
  data_uuid="$(blkid -s UUID -o value "${loop_device}" 2>/dev/null || true)"
  if [[ -z "${data_uuid}" ]]; then
    losetup --detach "${loop_device}" || true
    die "无法读取新 XFS 镜像 UUID"
  fi
  sync -f "${temporary}"
  if ! losetup --detach "${loop_device}"; then
    die "无法安全释放临时 loop 设备"
  fi
  loop_device=""
  if ! "${SCRIPT_DIR}/check-loopback-image-allocation.sh" \
      "${temporary}" "${DATA_IMAGE_SIZE_BYTES}"; then
    die "XFS 格式化后实际分配空间不足"
  fi
  mv -- "${temporary}" "${DATA_IMAGE}"
  temporary=""
  mark_stage data-formatted \
    "mode=loopback|image=${DATA_IMAGE}|size=${DATA_IMAGE_SIZE_BYTES}|uuid=${data_uuid}"
  trap - EXIT HUP INT TERM
)

repair_loopback_allocation() (
  local confirmation=""
  local active_sandboxes=""
  local data_uuid=""
  local expected_marker=""
  local mount_source=""
  local associated_loop=""
  local probe_loop=""
  local root_free=""
  local allocated_blocks=""
  local block_bytes=""
  local actual_allocated_bytes=""
  local missing_bytes=""
  local required_free=""
  local size_gib=""
  local unexpected_entry=""
  local required_path=""
  local remount_required=false

  # 仅由 EXIT trap 间接调用。
  # shellcheck disable=SC2317
  restore_loopback_mount() {
    if [[ -n "${probe_loop}" ]] \
      && losetup "${probe_loop}" >/dev/null 2>&1; then
      losetup --detach "${probe_loop}" || true
    fi
    if [[ "${remount_required}" == "true" ]] \
      && ! mountpoint -q "${DATA_ROOT}"; then
      mount "${DATA_ROOT}" \
        || warn "原地补充分配失败后未能恢复 ${DATA_ROOT} 挂载，请停止后续阶段并人工检查"
    fi
  }

  trap restore_loopback_mount EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  [[ "${STORAGE_MODE}" == "loopback" ]] \
    || die "--repair-loopback-allocation 仅适用于 loopback 模式"
  assert_no_advanced_stages
  verify_loopback_image_metadata
  verify_loopback_mount
  require_stage data-formatted
  require_command fallocate
  require_command losetup
  require_command xfs_repair

  mount_source="$(readlink -f "$(findmnt -n -o SOURCE "${DATA_ROOT}")")"
  data_uuid="$(blkid -s UUID -o value "${mount_source}" 2>/dev/null || true)"
  [[ -n "${data_uuid}" ]] || die "无法读取已挂载 loopback XFS UUID"
  expected_marker="mode=loopback|image=${DATA_IMAGE}|size=${DATA_IMAGE_SIZE_BYTES}|uuid=${data_uuid}"
  [[ "$(read_stage data-formatted)" == "${expected_marker}" ]] \
    || die "既有 XFS 与 data-formatted 阶段凭据不匹配"

  ensure_loopback_fstab_line

  if systemctl is-active --quiet nanobot-sandboxd.service; then
    die "sandboxd 正在运行，拒绝卸载 Sandbox 数据盘"
  fi
  active_sandboxes="$(docker ps \
    --filter 'label=com.nanobot.sandbox=true' \
    --filter 'label=com.nanobot.managed-by=sandboxd' \
    --format '{{.Names}}')" \
    || die "无法确认活动 Sandbox 容器"
  [[ -z "${active_sandboxes}" ]] \
    || die "仍有活动 Sandbox 容器，拒绝补充分配"

  for required_path in \
    "${DATA_ROOT}/workspaces" \
    "${DATA_ROOT}/assets" \
    "${DATA_ROOT}/assets/sha256" \
    "${DATA_ROOT}/runtime"; do
    [[ -d "${required_path}" && ! -L "${required_path}" ]] \
      || die "空存储布局缺少受控目录：${required_path}"
  done
  unexpected_entry="$(find "${DATA_ROOT}" -xdev -mindepth 1 \
    ! -path "${DATA_ROOT}/workspaces" \
    ! -path "${DATA_ROOT}/assets" \
    ! -path "${DATA_ROOT}/assets/sha256" \
    ! -path "${DATA_ROOT}/runtime" \
    -print -quit)" \
    || die "无法检查 Sandbox 数据盘是否为空"
  [[ -z "${unexpected_entry}" ]] \
    || die "Sandbox 数据盘已包含受控空目录之外的内容，拒绝原地补充分配：${unexpected_entry}"

  while IFS= read -r associated_loop; do
    [[ -n "${associated_loop}" ]] || continue
    [[ "$(readlink -f "${associated_loop}")" == "${mount_source}" ]] \
      || die "镜像还关联了非当前挂载 loop 设备：${associated_loop}"
  done < <(losetup --associated "${DATA_IMAGE}" --noheadings --output NAME)

  allocated_blocks="$(stat -c '%b' "${DATA_IMAGE}")"
  block_bytes="$(stat -c '%B' "${DATA_IMAGE}")"
  actual_allocated_bytes=$((allocated_blocks * block_bytes))
  if (( actual_allocated_bytes >= DATA_IMAGE_SIZE_BYTES )); then
    verify_loopback_image
    log "loopback 镜像已经完成真实预分配，无需修复"
    trap - EXIT HUP INT TERM
    return 0
  fi

  missing_bytes=$((DATA_IMAGE_SIZE_BYTES - actual_allocated_bytes))
  root_free="$(df -B1 --output=avail / | tail -n 1 | tr -d ' ')"
  required_free=$((missing_bytes + SYSTEM_MIN_FREE_BYTES + BUILD_RESERVE_BYTES))
  (( root_free >= required_free )) \
    || die "根分区空间不足：补充分配后无法同时保留 60 GiB 系统空间和 20 GiB 构建余量"

  [[ -t 0 ]] || die "原地补充分配要求交互式 TTY"
  size_gib=$((DATA_IMAGE_SIZE_BYTES / 1024 / 1024 / 1024))
  printf '将卸载 %s，并为既有 XFS backing file 原地补足实际分配空间。\n' "${DATA_ROOT}"
  printf '不会格式化、删除或重建文件系统。请输入：REPAIR LOOPBACK ALLOCATION %sGiB\n' "${size_gib}"
  IFS= read -r confirmation
  [[ "${confirmation}" == "REPAIR LOOPBACK ALLOCATION ${size_gib}GiB" ]] \
    || die "loopback 分配修复确认不匹配，未写入"

  sync -f "${DATA_ROOT}"
  umount "${DATA_ROOT}"
  remount_required=true

  if losetup "${mount_source}" >/dev/null 2>&1; then
    findmnt -rn -S "${mount_source}" | grep -q . \
      && die "卸载后 loop 设备仍被其他挂载使用：${mount_source}"
    losetup --detach "${mount_source}"
  fi
  if losetup --associated "${DATA_IMAGE}" --noheadings --output NAME \
      | grep -q .; then
    die "卸载后 backing file 仍被其他 loop 设备占用"
  fi

  if ! fallocate --keep-size --offset 0 \
      --length "${DATA_IMAGE_SIZE_BYTES}" "${DATA_IMAGE}"; then
    die "loopback 镜像原地补充分配失败"
  fi
  sync -f "${DATA_IMAGE}"
  verify_loopback_image

  if ! probe_loop="$(losetup --find --show --read-only "${DATA_IMAGE}")"; then
    die "无法为修复后的镜像建立只读检查 loop 设备"
  fi
  [[ "$(blkid -s UUID -o value "${probe_loop}" 2>/dev/null || true)" \
      == "${data_uuid}" ]] \
    || die "补充分配后 XFS UUID 发生变化"
  xfs_repair -n "${probe_loop}"
  losetup --detach "${probe_loop}"
  probe_loop=""

  mount "${DATA_ROOT}"
  remount_required=false
  verify_loopback_mount
  verify_loopback_image
  [[ "$(blkid -s UUID -o value \
      "$(findmnt -n -o SOURCE "${DATA_ROOT}")" 2>/dev/null || true)" \
      == "${data_uuid}" ]] \
    || die "重新挂载后的 XFS UUID 与修复前不一致"

  log "loopback 镜像已原地补足实际分配空间，XFS UUID 与空目录布局保持不变"
  trap - EXIT HUP INT TERM
)

prepare_loopback_data_mount() {
  local allow_format="$1"
  local data_uuid=""
  local expected_marker=""
  local probe_loop=""
  local mount_source=""

  require_command fallocate
  require_command losetup
  if mountpoint -q "${DATA_ROOT}"; then
    verify_loopback_image
    verify_loopback_mount
    require_stage data-formatted
    mount_source="$(readlink -f "$(findmnt -n -o SOURCE "${DATA_ROOT}")")"
    data_uuid="$(blkid -s UUID -o value "${mount_source}" 2>/dev/null || true)"
    [[ -n "${data_uuid}" ]] || die "无法读取已挂载 loopback XFS UUID"
    expected_marker="mode=loopback|image=${DATA_IMAGE}|size=${DATA_IMAGE_SIZE_BYTES}|uuid=${data_uuid}"
    [[ "$(read_stage data-formatted)" == "${expected_marker}" ]] \
      || die "已挂载 loopback 镜像与本脚本阶段凭据不匹配"
    ensure_loopback_fstab_line
  else
    if [[ ! -e "${DATA_IMAGE}" ]]; then
      [[ "${allow_format}" == "true" ]] \
        || die "${DATA_ROOT} 尚未挂载；首次执行必须增加 --initialize-storage"
      create_loopback_image
    fi

    verify_loopback_image
    require_stage data-formatted
    if ! probe_loop="$(losetup --find --show --read-only "${DATA_IMAGE}")"; then
      die "无法只读检查 loopback 镜像"
    fi
    data_uuid="$(blkid -s UUID -o value "${probe_loop}" 2>/dev/null || true)"
    if ! losetup --detach "${probe_loop}"; then
      die "无法释放只读检查 loop 设备"
    fi
    [[ -n "${data_uuid}" ]] || die "无法读取 loopback XFS UUID"
    expected_marker="mode=loopback|image=${DATA_IMAGE}|size=${DATA_IMAGE_SIZE_BYTES}|uuid=${data_uuid}"
    [[ "$(read_stage data-formatted)" == "${expected_marker}" ]] \
      || die "现有 loopback 镜像与本脚本阶段凭据不匹配"

    ensure_data_mountpoint_empty
    ensure_loopback_fstab_line
    mount "${DATA_ROOT}"
    verify_loopback_mount
  fi
}

prepare_data_mount() {
  local allow_format="$1"
  local free_bytes
  local root_free

  case "${STORAGE_MODE}" in
    block)
      prepare_block_data_mount "${allow_format}"
      ;;
    loopback)
      prepare_loopback_data_mount "${allow_format}"
      ;;
  esac

  "${REPO_ROOT}/scripts/check-sandbox-data-disk.sh" "${DATA_ROOT}"
  free_bytes="$(df -B1 --output=avail "${DATA_ROOT}" | tail -n 1 | tr -d ' ')"
  (( free_bytes >= DISK_MIN_FREE_BYTES )) \
    || die "Sandbox 数据文件系统可用空间低于配置水位"
  if [[ "${STORAGE_MODE}" == "loopback" ]]; then
    root_free="$(df -B1 --output=avail / | tail -n 1 | tr -d ' ')"
    (( root_free >= SYSTEM_MIN_FREE_BYTES )) \
      || die "根文件系统可用空间低于 60 GiB"
  fi
}

install_host_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y \
    apparmor \
    apparmor-utils \
    ca-certificates \
    curl \
    jq \
    openssl \
    quota \
    rsync \
    util-linux \
    xfsprogs

  local uv_source=""
  local home
  home="$(deploy_home)"
  if [[ -x "${home}/.local/bin/uv" ]]; then
    uv_source="${home}/.local/bin/uv"
  elif [[ -x /usr/local/bin/uv ]]; then
    uv_source=/usr/local/bin/uv
  else
    die "未找到已安装的 uv"
  fi
  install -m 0755 "${uv_source}" /usr/local/bin/uv
}

install_apparmor_profile() {
  [[ "$(cat /sys/module/apparmor/parameters/enabled 2>/dev/null || true)" == "Y" ]] \
    || die "宿主机 AppArmor 未启用"
  local profile
  for profile in nanobot-sandbox-restricted nanobot-sandbox-developer; do
    install -m 0644 \
      "${REPO_ROOT}/deploy/apparmor/${profile}" \
      "/etc/apparmor.d/${profile}"
    apparmor_parser -r "/etc/apparmor.d/${profile}"
    grep -q "^${profile} " /sys/kernel/security/apparmor/profiles \
      || die "${profile} AppArmor profile 未实际加载"
  done
  docker info --format '{{json .SecurityOptions}}' \
    | grep -qi apparmor \
    || die "Docker SecurityOptions 未报告 AppArmor"
  docker info --format '{{json .SecurityOptions}}' \
    | grep -qi seccomp \
    || die "Docker SecurityOptions 未报告 seccomp"
}

prepare_host_command() {
  require_root
  load_config
  assert_release_checkout_current
  assert_backup_target_policy

  local allow_format=false
  local repair_allocation=false
  shift
  while (( $# )); do
    case "$1" in
      --initialize-storage)
        allow_format=true
        shift
        ;;
      --repair-loopback-allocation)
        repair_allocation=true
        shift
        ;;
      --format-data-device)
        [[ "${STORAGE_MODE}" == "block" ]] \
          || die "--format-data-device 仅供 block 模式兼容使用；loopback 请使用 --initialize-storage"
        allow_format=true
        shift
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        die "prepare-host 未知参数：$1"
        ;;
    esac
  done

  if [[ "${allow_format}" == "true" \
      && "${repair_allocation}" == "true" ]]; then
    die "--initialize-storage/--format-data-device 与 --repair-loopback-allocation 互斥"
  fi

  log "安装宿主依赖"
  install_host_packages

  if ! getent group nanobot-sandboxd >/dev/null; then
    if getent group "${SANDBOXD_GID}" >/dev/null; then
      die "GID ${SANDBOXD_GID} 已被其他组占用"
    fi
    groupadd --gid "${SANDBOXD_GID}" nanobot-sandboxd
  fi
  [[ "$(getent group nanobot-sandboxd | cut -d: -f3)" == "${SANDBOXD_GID}" ]] \
    || die "nanobot-sandboxd 组 GID 与配置不一致"

  if [[ "${repair_allocation}" == "true" ]]; then
    log "原地补足既有空 loopback XFS 镜像的实际分配空间"
    repair_loopback_allocation
  fi

  log "准备 Sandbox 数据文件系统（${STORAGE_MODE}）"
  prepare_data_mount "${allow_format}"

  install -m 0644 \
    "${REPO_ROOT}/deploy/systemd/nanobot-sandboxd.tmpfiles.conf" \
    /etc/tmpfiles.d/nanobot-sandboxd.conf
  systemd-tmpfiles --create /etc/tmpfiles.d/nanobot-sandboxd.conf

  log "安装并加载 AppArmor profile"
  install_apparmor_profile

  mark_stage host-prepared \
    "release=${RELEASE}|storage=${STORAGE_MODE}|source=$(findmnt -n -o SOURCE "${DATA_ROOT}")"
  log "宿主准备阶段通过"
  printf '下一步：sudo %s build-image\n' "$0"
}

check_build_disk_gate() {
  local free_bytes
  local used_percent
  free_bytes="$(df -B1 --output=avail / | tail -n 1 | tr -d ' ')"
  used_percent="$(df --output=pcent / | tail -n 1 | tr -dc '0-9')"
  (( free_bytes >= SYSTEM_MIN_FREE_BYTES + BUILD_RESERVE_BYTES )) \
    || die "根分区不足以同时保留 60 GiB 系统水位和 20 GiB 本地构建余量"
  (( used_percent < 80 )) \
    || die "根分区使用率达到 80%，停止镜像构建"
}

assert_prepared_storage_current() {
  local free_bytes
  local root_free

  mountpoint -q "${DATA_ROOT}" || die "${DATA_ROOT} 尚未挂载"
  if [[ "${STORAGE_MODE}" == "loopback" ]]; then
    verify_loopback_image
    verify_loopback_mount
    assert_loopback_fstrim_excluded
  else
    [[ "$(readlink -f "$(findmnt -n -o SOURCE "${DATA_ROOT}")")" \
        == "$(readlink -f "${DATA_DEVICE}")" ]] \
      || die "Sandbox 数据盘与配置 DATA_DEVICE 不一致"
  fi
  "${REPO_ROOT}/scripts/check-sandbox-data-disk.sh" "${DATA_ROOT}"
  free_bytes="$(df -B1 --output=avail "${DATA_ROOT}" | tail -n 1 | tr -d ' ')"
  (( free_bytes >= DISK_MIN_FREE_BYTES )) \
    || die "Sandbox 数据文件系统可用空间低于配置水位"
  if [[ "${STORAGE_MODE}" == "loopback" ]]; then
    root_free="$(df -B1 --output=avail / | tail -n 1 | tr -d ' ')"
    (( root_free >= SYSTEM_MIN_FREE_BYTES )) \
      || die "根文件系统可用空间低于 60 GiB"
  fi
}

assert_host_prepared_current() {
  local expected_marker
  require_stage host-prepared
  expected_marker="release=${RELEASE}|storage=${STORAGE_MODE}|source=$(findmnt -n -o SOURCE "${DATA_ROOT}")"
  [[ "$(read_stage host-prepared)" == "${expected_marker}" ]] \
    || die "host-prepared 阶段凭据与当前 RELEASE 或数据挂载不一致；请重新运行 prepare-host"
}

build_image_command() {
  require_root
  load_config
  local resume_failed_build_release=""

  shift
  while (( $# )); do
    case "$1" in
      --resume-failed-build-from)
        [[ $# -ge 2 ]] || die "--resume-failed-build-from 缺少参数"
        resume_failed_build_release="$2"
        shift 2
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        die "build-image 未知参数：$1"
        ;;
    esac
  done

  assert_release_checkout_current
  assert_prepared_storage_current
  assert_host_prepared_current
  require_command python3

  local canonical_manifest="${REPO_ROOT}/config/sandbox-execution-profiles.v1.json"
  local canonical_generation
  local proxy_candidate
  local restricted_id
  local developer_id
  local proxy_id
  local restricted_user
  local developer_user
  local proxy_user
  local image_reference
  local image_id
  local manifest_generation
  local manifest_sha256

  [[ -f "${canonical_manifest}" && ! -L "${canonical_manifest}" ]] \
    || die "canonical Profile manifest 不是受管普通文件"
  canonical_generation="$(jq -er '.catalog_generation' "${canonical_manifest}")"
  jq -e '
    .profiles[]
    | select(.profile_id == "developer")
    | .network_proxy_image_allowlist
    | type == "array" and length == 0
  ' "${canonical_manifest}" >/dev/null \
    || die "canonical Profile manifest 的代理 IMAGE ID 必须留空"
  [[ "$(jq -er '
    .profiles[]
    | select(.profile_id == "developer")
    | .network_proxy_image_reference
  ' "${canonical_manifest}")" == "${PROXY_IMAGE}" ]] \
    || die "生产脚本固定代理引用与 canonical Profile manifest 不一致"

  proxy_candidate="nanobot-sandbox-egress-proxy:candidate-${VERSION}"
  if [[ -n "${resume_failed_build_release}" ]]; then
    [[ "${resume_failed_build_release}" =~ ^[0-9a-f]{40}$ ]] \
      || die "失败构建来源必须是 40 位小写提交哈希"
    repo_git cat-file -e \
      "${resume_failed_build_release}^{commit}" 2>/dev/null \
      || die "本地仓库不存在失败构建来源提交"
    repo_git merge-base --is-ancestor \
      "${resume_failed_build_release}" "${RELEASE}" \
      || die "失败构建来源不是当前 RELEASE 的祖先"
    [[ "${VERSION}" == "${resume_failed_build_release:0:7}-"* ]] \
      || die "VERSION 与失败构建来源提交不匹配"
    repo_git diff --quiet \
      "${resume_failed_build_release}" "${RELEASE}" -- \
      scripts/build-sandbox-image.sh \
      docker/sandbox/python \
      docker/sandbox/developer \
      docker/sandbox/egress-proxy \
      || die "失败构建后的镜像输入已变化，禁止复用"
    for image_reference in \
      "${RESTRICTED_IMAGE}" \
      "${DEVELOPER_IMAGE}" \
      "${proxy_candidate}"; do
      docker image inspect "${image_reference}" >/dev/null 2>&1 \
        || die "失败构建遗留镜像不存在：${image_reference}"
    done
    log "镜像构建输入未变；复用失败阶段已完成的三张镜像"
  else
    check_build_disk_gate
    log "构建固定 Restricted 镜像 ${RESTRICTED_IMAGE}"
    run_as_deploy env \
      -u http_proxy -u https_proxy \
      -u HTTP_PROXY -u HTTPS_PROXY \
      -u all_proxy -u ALL_PROXY \
      "${REPO_ROOT}/scripts/build-sandbox-image.sh" "${VERSION}"

    log "构建固定 Developer 镜像 ${DEVELOPER_IMAGE}"
    run_as_deploy env \
      -u http_proxy -u https_proxy \
      -u HTTP_PROXY -u HTTPS_PROXY \
      -u all_proxy -u ALL_PROXY \
      "${REPO_ROOT}/scripts/build-sandbox-image.sh" \
        "${VERSION}" --profile developer

    log "从当前固定提交构建候选出口代理镜像"
    run_as_deploy env \
      -u http_proxy -u https_proxy \
      -u HTTP_PROXY -u HTTPS_PROXY \
      -u all_proxy -u ALL_PROXY \
      docker build \
        --tag "${proxy_candidate}" \
        "${REPO_ROOT}/docker/sandbox/egress-proxy"
  fi

  restricted_id="$(docker image inspect "${RESTRICTED_IMAGE}" --format '{{.Id}}')"
  developer_id="$(docker image inspect "${DEVELOPER_IMAGE}" --format '{{.Id}}')"
  proxy_id="$(docker image inspect "${proxy_candidate}" --format '{{.Id}}')"
  restricted_user="$(docker image inspect "${RESTRICTED_IMAGE}" --format '{{.Config.User}}')"
  developer_user="$(docker image inspect "${DEVELOPER_IMAGE}" --format '{{.Config.User}}')"
  proxy_user="$(docker image inspect "${proxy_candidate}" --format '{{.Config.User}}')"
  for image_id in "${restricted_id}" "${developer_id}" "${proxy_id}"; do
    [[ "${image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] \
      || die "Sandbox IMAGE ID 格式无效"
  done
  [[ "${restricted_user}" == "10001:10001" ]] \
    || die "Restricted 镜像默认用户不是 10001:10001"
  [[ "${developer_user}" == "10001:10001" ]] \
    || die "Developer 镜像默认用户不是 10001:10001"
  [[ "${proxy_user}" == "13:13" ]] \
    || die "出口代理镜像默认用户不是 13:13"
  docker image tag "${proxy_id}" "${PROXY_IMAGE}"

  ensure_state_dir
  manifest_generation="${canonical_generation}.${RELEASE:0:12}"
  log "生成同时绑定三个 IMAGE ID 的部署 Profile manifest"
  PYTHONPATH="${REPO_ROOT}" python3 \
    "${REPO_ROOT}/scripts/render-sandbox-profile-manifest.py" \
    --source "${canonical_manifest}" \
    --output "${BUILT_PROFILE_MANIFEST}" \
    --generation "${manifest_generation}" \
    --restricted-reference "${RESTRICTED_IMAGE}" \
    --restricted-image-id "${restricted_id}" \
    --developer-reference "${DEVELOPER_IMAGE}" \
    --developer-image-id "${developer_id}" \
    --proxy-reference "${PROXY_IMAGE}" \
    --proxy-image-id "${proxy_id}"
  chown root:root "${BUILT_PROFILE_MANIFEST}"
  chmod 0600 "${BUILT_PROFILE_MANIFEST}"
  manifest_sha256="$(sha256sum "${BUILT_PROFILE_MANIFEST}" | awk '{print $1}')"

  mark_stage image-built \
    "restricted=${RESTRICTED_IMAGE}@${restricted_id}|developer=${DEVELOPER_IMAGE}@${developer_id}|proxy=${PROXY_IMAGE}@${proxy_id}|manifest=${manifest_sha256}"
  image_id_from_stage >/dev/null
  docker image rm "${proxy_candidate}" >/dev/null
  log "Sandbox 三镜像与部署 manifest 构建通过：${manifest_sha256}"
  printf '下一步：sudo %s smoke\n' "$0"
}

image_bundle_from_stage() {
  local marker
  local restricted_part
  local developer_part
  local proxy_part
  local manifest_part
  local extra_part
  local restricted_id
  local developer_id
  local proxy_id
  local manifest_sha256
  marker="$(read_stage image-built)"
  IFS='|' read -r \
    restricted_part developer_part proxy_part manifest_part extra_part \
    <<<"${marker}"
  [[ -z "${extra_part}" ]] \
    || die "image-built 阶段凭据字段数量无效"
  [[ "${restricted_part}" == "restricted=${RESTRICTED_IMAGE}@"* ]] \
    || die "image-built 阶段 Restricted 镜像引用无效"
  [[ "${developer_part}" == "developer=${DEVELOPER_IMAGE}@"* ]] \
    || die "image-built 阶段 Developer 镜像引用无效"
  [[ "${proxy_part}" == "proxy=${PROXY_IMAGE}@"* ]] \
    || die "image-built 阶段代理镜像引用无效"
  [[ "${manifest_part}" == "manifest="* ]] \
    || die "image-built 阶段缺少 manifest 摘要"
  restricted_id="${restricted_part#*@}"
  developer_id="${developer_part#*@}"
  proxy_id="${proxy_part#*@}"
  manifest_sha256="${manifest_part#manifest=}"
  for image_id in "${restricted_id}" "${developer_id}" "${proxy_id}"; do
    [[ "${image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] \
      || die "image-built 阶段 IMAGE ID 无效"
  done
  [[ "${manifest_sha256}" =~ ^[0-9a-f]{64}$ ]] \
    || die "image-built 阶段 manifest 摘要无效"
  [[ "$(docker image inspect "${RESTRICTED_IMAGE}" --format '{{.Id}}')" \
      == "${restricted_id}" ]] \
    || die "当前 Restricted 镜像已变化，请重新运行 build-image 与 smoke"
  [[ "$(docker image inspect "${DEVELOPER_IMAGE}" --format '{{.Id}}')" \
      == "${developer_id}" ]] \
    || die "当前 Developer 镜像已变化，请重新运行 build-image 与 smoke"
  [[ "$(docker image inspect "${PROXY_IMAGE}" --format '{{.Id}}')" \
      == "${proxy_id}" ]] \
    || die "当前出口代理镜像已变化，请重新运行 build-image 与 smoke"
  [[ -f "${BUILT_PROFILE_MANIFEST}" \
      && ! -L "${BUILT_PROFILE_MANIFEST}" \
      && "$(sha256sum "${BUILT_PROFILE_MANIFEST}" | awk '{print $1}')" \
        == "${manifest_sha256}" ]] \
    || die "部署 Profile manifest 已变化或丢失，请重新运行 build-image 与 smoke"
  PYTHONPATH="${REPO_ROOT}" python3 - \
    "${BUILT_PROFILE_MANIFEST}" \
    "${RESTRICTED_IMAGE}" "${restricted_id}" \
    "${DEVELOPER_IMAGE}" "${developer_id}" \
    "${PROXY_IMAGE}" "${proxy_id}" <<'PY'
import sys

from core.sandbox.profile_catalog import load_profile_catalog

(
    manifest,
    restricted_reference,
    restricted_id,
    developer_reference,
    developer_id,
    proxy_reference,
    proxy_id,
) = sys.argv[1:]
catalog = load_profile_catalog(manifest)
restricted = catalog.profile("restricted")
developer = catalog.profile("developer")
trusted = catalog.profile("trusted_developer")
if (
    restricted.image_reference != restricted_reference
    or restricted.image_allowlist != (restricted_id,)
    or developer.image_reference != developer_reference
    or developer.image_allowlist != (developer_id,)
    or developer.network_proxy_image_reference != proxy_reference
    or developer.network_proxy_image_allowlist != (proxy_id,)
    or trusted.image_reference != developer_reference
    or trusted.image_allowlist
    or trusted.grantable
):
    raise SystemExit("部署 Profile manifest 与 image-built 凭据不一致")
PY
  printf '%s|%s|%s|%s\n' \
    "${restricted_id}" "${developer_id}" "${proxy_id}" "${manifest_sha256}"
}

image_id_from_stage() {
  image_bundle_from_stage | cut -d'|' -f1
}

developer_image_id_from_stage() {
  image_bundle_from_stage | cut -d'|' -f2
}

proxy_image_id_from_stage() {
  image_bundle_from_stage | cut -d'|' -f3
}

profile_manifest_sha256_from_stage() {
  image_bundle_from_stage | cut -d'|' -f4
}

validate_smoke_evidence() {
  local evidence_dir="$1"
  local summary_file="${evidence_dir}/summary.json"
  [[ -d "${evidence_dir}" && ! -L "${evidence_dir}" \
      && -f "${summary_file}" && ! -L "${summary_file}" ]] \
    || die "Smoke 结构化证据已丢失或路径不安全"
  python3 - "${evidence_dir}" "${summary_file}" <<'PY'
import json
import sys
from pathlib import Path

evidence_candidate = Path(sys.argv[1])
summary_candidate = Path(sys.argv[2])
if evidence_candidate.is_symlink() or summary_candidate.is_symlink():
    raise SystemExit("Smoke 结构化证据路径不得是符号链接")
evidence_dir = evidence_candidate.resolve(strict=True)
summary_file = summary_candidate.resolve(strict=True)
try:
    summary_file.relative_to(evidence_dir)
except ValueError as exc:
    raise SystemExit("Smoke summary 越出证据目录") from exc
value = json.loads(summary_file.read_text(encoding="utf-8"))
expected_groups = {
    "basic-security",
    "lease",
    "process",
    "developer-toolchain",
    "network",
    "data-continuity",
}
groups = value.get("groups")
if (
    value.get("schema_version") != 1
    or value.get("result") != "passed"
    or (value.get("preflight") or {}).get("status") != "passed"
    or not isinstance(groups, list)
    or {item.get("id") for item in groups} != expected_groups
):
    raise SystemExit("Smoke summary 未报告完整通过")
for item in groups:
    if (
        item.get("status") != "passed"
        or item.get("exit_code") != 0
        or item.get("tests", 0) <= 0
        or item.get("failures") != 0
        or item.get("errors") != 0
        or item.get("skipped") != 0
        or item.get("parse_error")
    ):
        raise SystemExit(f"Smoke 分组未完整通过：{item.get('id')}")
    for field in ("junit", "log"):
        artifact_candidate = Path(str(item.get(field) or ""))
        if artifact_candidate.is_symlink() or not artifact_candidate.is_file():
            raise SystemExit(
                f"Smoke 分组证据不是普通文件：{item.get('id')}"
            )
        artifact = artifact_candidate.resolve(strict=True)
        try:
            artifact.relative_to(evidence_dir)
        except ValueError as exc:
            raise SystemExit(
                f"Smoke 分组证据越出目录：{item.get('id')}"
            ) from exc
PY
}

assert_smoke_current() {
  local marker
  local expected_manifest_sha256
  local evidence_dir
  marker="$(read_stage smoke-passed)"
  expected_manifest_sha256="$(profile_manifest_sha256_from_stage)"
  [[ "${marker}" == \
      "manifest=${expected_manifest_sha256}|evidence="* ]] \
    || die "Smoke 凭据与当前 Profile manifest 不匹配"
  evidence_dir="${marker#*|evidence=}"
  validate_smoke_evidence "${evidence_dir}"
}

assert_control_plane_current() {
  local marker
  marker="$(read_stage control-plane-ready)"
  image_bundle_from_stage >/dev/null
  [[ "${marker}" \
      == "release=${RELEASE}|manifest=$(profile_manifest_sha256_from_stage)" ]] \
    || die "控制面凭据与当前 RELEASE/Profile manifest 不匹配"
}

run_smoke_matrix_with_controller_quiesced() (
  local sandboxd_was_active=false
  local active_sandboxes=""

  restore_sandboxd_after_smoke() {
    local exit_code="$?"
    trap - EXIT HUP INT TERM
    if [[ "${sandboxd_was_active}" == "true" ]]; then
      log "恢复 nanobot-sandboxd，结束 Smoke 临时控制器独占窗口"
      if ! systemctl start nanobot-sandboxd.service; then
        warn "Smoke 结束后无法恢复 nanobot-sandboxd.service"
        exit 1
      fi
      if ! systemctl is-active --quiet nanobot-sandboxd.service; then
        warn "Smoke 结束后 nanobot-sandboxd.service 未进入运行态"
        exit 1
      fi
    fi
    exit "${exit_code}"
  }

  trap restore_sandboxd_after_smoke EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  require_command docker
  require_command systemctl
  active_sandboxes="$(docker ps \
    --filter 'label=com.nanobot.sandbox=true' \
    --filter 'label=com.nanobot.managed-by=sandboxd' \
    --format '{{.Names}}')" \
    || die "无法确认活动 Sandbox 容器"
  [[ -z "${active_sandboxes}" ]] \
    || die "仍有活动 Sandbox 容器，拒绝暂停生产 sandboxd：${active_sandboxes}"

  if systemctl is-active --quiet nanobot-sandboxd.service; then
    sandboxd_was_active=true
    log "暂停 nanobot-sandboxd，避免生产 Reconciler 回收 Smoke 临时资源"
    systemctl stop nanobot-sandboxd.service
    if systemctl is-active --quiet nanobot-sandboxd.service; then
      die "nanobot-sandboxd.service 未能进入停止态"
    fi
  fi

  "$@"
)

smoke_command() {
  require_root
  load_config
  require_stage image-built
  assert_release_checkout_current
  assert_prepared_storage_current
  assert_host_prepared_current
  image_id_from_stage >/dev/null

  local smoke_dir="/var/tmp/nanobot-sandbox-security-${RELEASE:0:7}"
  local evidence_dir
  local retry=false

  shift
  while (( $# )); do
    case "$1" in
      --retry)
        retry=true
        shift
        ;;
      *)
        die "smoke 未知参数：$1"
        ;;
    esac
  done

  if [[ -e "${smoke_dir}" ]] \
    || repo_git worktree list --porcelain | grep -Fq "worktree ${smoke_dir}"; then
    [[ "${retry}" == "true" ]] \
      || die "Smoke worktree 已存在；核对证据后使用 smoke --retry"
    repo_git worktree list --porcelain \
      | grep -Fq "worktree ${smoke_dir}" \
      || die "固定路径存在但不是 Git worktree，脚本拒绝删除"
    log "清理固定命名的失败 Smoke worktree"
    repo_git worktree remove --force "${smoke_dir}"
    [[ ! -e "${smoke_dir}" ]] \
      || die "失败 Smoke worktree 清理后路径仍存在"
  fi

  log "创建隔离 Smoke worktree"
  repo_git worktree add --detach "${smoke_dir}" "${RELEASE}"

  log "安装最小化且哈希锁定的 Smoke Python 3.11 环境"
  run_as_deploy /usr/local/bin/uv venv \
    --no-project \
    --python 3.11.14 \
    "${smoke_dir}/.venv"
  run_as_deploy env \
    -u http_proxy -u https_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY \
    -u all_proxy -u ALL_PROXY \
    /usr/local/bin/uv pip sync \
    --python "${smoke_dir}/.venv/bin/python" \
    --require-hashes \
    --only-binary :all: \
    --strict \
    "${smoke_dir}/requirements-sandbox-smoke.lock"
  run_as_deploy "${smoke_dir}/.venv/bin/python" -c \
    'import docker, pytest; assert docker.__version__ == "7.1.0"; assert pytest.__version__ == "9.1.1"'

  install -d -m 0700 "${EVIDENCE_CACHE_ROOT}"
  log "运行六组真实 Docker Sandbox 验收矩阵"
  run_smoke_matrix_with_controller_quiesced \
    env \
      PYTHONDONTWRITEBYTECODE=1 \
      PATH="${smoke_dir}/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
      XDG_CACHE_HOME="${EVIDENCE_CACHE_ROOT}" \
      "${smoke_dir}/scripts/sandbox-smoke-test.sh" \
        --manifest "${BUILT_PROFILE_MANIFEST}" \
        --data-root "${DATA_ROOT}" \
        --evidence-root "${EVIDENCE_CACHE_ROOT}/nanobot-sandbox-smoke"

  evidence_dir="$(find "${EVIDENCE_CACHE_ROOT}/nanobot-sandbox-smoke" \
    -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
    | sort -n | tail -n 1 | cut -d' ' -f2-)"
  [[ -n "${evidence_dir}" ]] || die "找不到 Smoke 结构化证据"
  validate_smoke_evidence "${evidence_dir}"

  log "清理本轮隔离 worktree"
  repo_git worktree remove --force "${smoke_dir}"
  [[ ! -e "${smoke_dir}" ]] || die "Smoke worktree 清理失败"

  mark_stage smoke-passed \
    "manifest=$(profile_manifest_sha256_from_stage)|evidence=${evidence_dir}"
  log "六组真实 Docker 验收全部通过且没有跳过"
  printf '下一步：sudo %s install-control-plane\n' "$0"
}

install_release_tree() {
  local partial_dir
  local quota_helper
  install -d -m 0755 /opt/nanobot-releases
  if [[ ! -e "${RELEASE_DIR}" ]]; then
    partial_dir="$(mktemp -d /opt/nanobot-releases/.nanobot-release.XXXXXX)"
    repo_git archive "${RELEASE}" | tar --extract --directory "${partial_dir}"
    printf '%s\n' "${RELEASE}" >"${partial_dir}/.nanobot-release"
    chown -R root:root "${partial_dir}"
    chmod 0644 "${partial_dir}/.nanobot-release"
    mv -- "${partial_dir}" "${RELEASE_DIR}"
  fi
  [[ -d "${RELEASE_DIR}" && ! -L "${RELEASE_DIR}" ]] \
    || die "发布树不是受管普通目录：${RELEASE_DIR}"
  [[ -f "${RELEASE_DIR}/.nanobot-release" \
      && ! -L "${RELEASE_DIR}/.nanobot-release" \
      && "$(cat "${RELEASE_DIR}/.nanobot-release")" == "${RELEASE}" ]] \
    || die "发布树缺少正确的版本标记：${RELEASE_DIR}"
  [[ -f "${RELEASE_DIR}/sandboxd/app.py" \
      && ! -L "${RELEASE_DIR}/sandboxd/app.py" \
      && -f "${RELEASE_DIR}/requirements-sandboxd.lock" \
      && ! -L "${RELEASE_DIR}/requirements-sandboxd.lock" ]] \
    || die "发布树内容不完整：${RELEASE_DIR}"

  quota_helper="${RELEASE_DIR}/scripts/assign-sandbox-project-quota.sh"
  [[ -f "${quota_helper}" && ! -L "${quota_helper}" ]] \
    || die "Workspace project quota helper 不是受管普通文件"
  chown root:root "${quota_helper}"
  chmod 0755 "${quota_helper}"
  [[ "$(stat -c '%u:%g:%a' "${quota_helper}")" == "0:0:755" ]] \
    || die "Workspace project quota helper 权限不安全"

  if [[ -e "${SERVER_RELEASE_LINK}" \
      && ! -L "${SERVER_RELEASE_LINK}" ]]; then
    die "${SERVER_RELEASE_LINK} 已存在且不是预期符号链接"
  fi
}

activate_release_tree() {
  local releases_root
  local current_target=""
  local current_release=""
  local link_parent
  local temporary_dir

  ACTIVATED_FROM_RELEASE=""

  releases_root="$(dirname "${RELEASE_DIR}")"
  if [[ -L "${SERVER_RELEASE_LINK}" ]]; then
    current_target="$(readlink -f -- "${SERVER_RELEASE_LINK}" 2>/dev/null)" \
      || die "${SERVER_RELEASE_LINK} 是无法解析的符号链接"
    [[ -n "${current_target}" ]] \
      || die "${SERVER_RELEASE_LINK} 是无法解析的符号链接"
    if [[ "${current_target}" == "${RELEASE_DIR}" ]]; then
      return 0
    fi

    current_release="${current_target#${releases_root}/}"
    [[ "${current_release}" =~ ^[0-9a-f]{40}$ \
        && "${current_target}" == "${releases_root}/${current_release}" ]] \
      || die "${SERVER_RELEASE_LINK} 当前目标不属于受管发布目录"
    [[ -d "${current_target}" && ! -L "${current_target}" ]] \
      || die "当前发布目录不存在或是符号链接：${current_target}"
    [[ -f "${current_target}/.nanobot-release" \
        && ! -L "${current_target}/.nanobot-release" \
        && "$(cat "${current_target}/.nanobot-release")" == "${current_release}" ]] \
      || die "当前发布目录版本标记无效：${current_target}"
    repo_git cat-file -e "${current_release}^{commit}" 2>/dev/null \
      || die "当前发布提交不在生产仓库中：${current_release}"
    repo_git merge-base --is-ancestor "${current_release}" "${RELEASE}" \
      || die "控制面发布树只允许快进切换"
    ACTIVATED_FROM_RELEASE="${current_release}"
  elif [[ -e "${SERVER_RELEASE_LINK}" ]]; then
    die "${SERVER_RELEASE_LINK} 已存在且不是预期符号链接"
  fi

  link_parent="$(dirname "${SERVER_RELEASE_LINK}")"
  temporary_dir="$(mktemp -d "${link_parent}/.nanobot-server-link.XXXXXX")"
  ln -s -- "${RELEASE_DIR}" "${temporary_dir}/nanobot-server"
  mv -Tf -- "${temporary_dir}/nanobot-server" "${SERVER_RELEASE_LINK}"
  rmdir -- "${temporary_dir}"
  [[ -L "${SERVER_RELEASE_LINK}" \
      && "$(readlink -f -- "${SERVER_RELEASE_LINK}")" == "${RELEASE_DIR}" ]] \
    || die "控制面发布树原子切换后校验失败"
}

install_sandboxd_python() {
  install -d -m 0755 \
    "${PYTHON_ROOT}" \
    "${SANDBOXD_ROOT}" \
    "${UV_CACHE_DIR}"

  env \
    UV_PYTHON_INSTALL_DIR="${PYTHON_ROOT}" \
    UV_CACHE_DIR="${UV_CACHE_DIR}" \
    /usr/local/bin/uv python install --no-bin 3.11.14

  local python311
  python311="$(env \
    UV_PYTHON_INSTALL_DIR="${PYTHON_ROOT}" \
    UV_CACHE_DIR="${UV_CACHE_DIR}" \
    /usr/local/bin/uv python find 3.11.14)"

  if [[ ! -x "${SANDBOXD_VENV}/bin/python" ]]; then
    /usr/local/bin/uv venv \
      --no-project \
      --python "${python311}" \
      --seed \
      "${SANDBOXD_VENV}"
  fi

  env \
    -u http_proxy -u https_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY \
    -u all_proxy -u ALL_PROXY \
    "${SANDBOXD_VENV}/bin/python" -m pip install \
    --require-hashes \
    -r "${RELEASE_DIR}/requirements-sandboxd.lock"
  "${SANDBOXD_VENV}/bin/python" -c \
    'import sys; assert sys.version_info[:2] == (3, 11)'
}

install_sandboxd_credentials_and_env() {
  local image_id
  local io_device
  image_id="$(image_id_from_stage)"
  if [[ "${STORAGE_MODE}" == "loopback" ]]; then
    # loop 编号可能在重启后变化；使用 backing file 所在的稳定宿主块设备。
    io_device="$(findmnt -n -T "${DATA_IMAGE}" -o SOURCE)"
  else
    io_device="$(findmnt -n -o SOURCE "${DATA_ROOT}")"
  fi
  [[ -b "${io_device}" ]] || die "无法确定 Sandbox 数据块设备"

  install -d -m 0700 -o root -g root /etc/nanobot
  if [[ -e /etc/nanobot/sandboxd.token \
      && ( ! -f /etc/nanobot/sandboxd.token \
        || -L /etc/nanobot/sandboxd.token ) ]]; then
    die "sandboxd Token 路径不是普通文件"
  fi
  if [[ ! -e /etc/nanobot/sandboxd.token ]]; then
    (umask 077; openssl rand -hex 32 >/etc/nanobot/sandboxd.token)
  fi
  chown root:root /etc/nanobot/sandboxd.token
  chmod 0600 /etc/nanobot/sandboxd.token
  [[ "$(stat -c '%u:%a' /etc/nanobot/sandboxd.token)" == "0:600" ]] \
    || die "sandboxd Token 权限错误"
  if [[ -e /etc/nanobot/sandboxd-admin.token \
      && ( ! -f /etc/nanobot/sandboxd-admin.token \
        || -L /etc/nanobot/sandboxd-admin.token ) ]]; then
    die "sandboxd 管理 Token 路径不是普通文件"
  fi
  if [[ ! -e /etc/nanobot/sandboxd-admin.token ]]; then
    (umask 077; openssl rand -hex 32 >/etc/nanobot/sandboxd-admin.token)
  fi
  chown root:root /etc/nanobot/sandboxd-admin.token
  chmod 0600 /etc/nanobot/sandboxd-admin.token
  [[ "$(stat -c '%u:%a' /etc/nanobot/sandboxd-admin.token)" == "0:600" ]] \
    || die "sandboxd 管理 Token 权限错误"

  if [[ -e /etc/nanobot/sandboxd.env && -L /etc/nanobot/sandboxd.env ]]; then
    die "sandboxd 环境文件不得是符号链接"
  fi
  cat >/etc/nanobot/sandboxd.env <<EOF
NANOBOT_SANDBOX_DATA_ROOT=${DATA_ROOT}
NANOBOT_SANDBOXD_SOCKET=/run/nanobot-sandboxd/sandboxd.sock
NANOBOT_SANDBOXD_TOKEN_FILE=/etc/nanobot/sandboxd.token
NANOBOT_SANDBOXD_CLIENT_TOKEN_FILE=/run/nanobot-sandboxd/client.token
NANOBOT_SANDBOXD_ADMIN_TOKEN_FILE=/etc/nanobot/sandboxd-admin.token
NANOBOT_SANDBOXD_ADMIN_CLIENT_TOKEN_FILE=/run/nanobot-sandboxd/admin-client.token
NANOBOT_SANDBOXD_QUOTA_HELPER=/opt/nanobot-server/scripts/assign-sandbox-project-quota.sh
NANOBOT_SANDBOXD_DOCKER_SOCKET=unix:///var/run/docker.sock
NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE=${RUNTIME_PROFILE_MANIFEST}
NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED=false
NANOBOT_SANDBOX_EGRESS_UPLINK_NETWORK=nanobot-sbx-egress-uplink-v1
NANOBOT_SANDBOX_EGRESS_NETWORK_MTU=1450
NANOBOT_SANDBOX_IMAGE=${SANDBOX_IMAGE}
NANOBOT_SANDBOX_IMAGE_ALLOWLIST=${image_id}
NANOBOT_SANDBOX_APPARMOR_PROFILE=nanobot-sandbox-restricted
NANOBOT_SANDBOX_UID=10001
NANOBOT_SANDBOX_GID=10001
NANOBOT_SANDBOX_GLOBAL_CONCURRENCY=2
NANOBOT_SANDBOX_DEFAULT_TIMEOUT=60
NANOBOT_SANDBOX_MAX_TIMEOUT=120
NANOBOT_SANDBOX_WORKSPACE_QUOTA_BYTES=${WORKSPACE_QUOTA_BYTES}
NANOBOT_SANDBOX_ASSET_MAX_BYTES=${ASSET_MAX_BYTES}
NANOBOT_SANDBOX_TOTAL_QUOTA_BYTES=${TOTAL_QUOTA_BYTES}
NANOBOT_SANDBOX_USAGE_RECONCILE_INTERVAL_SECONDS=60
NANOBOT_SANDBOX_LEASE_RECONCILE_INTERVAL_SECONDS=15
NANOBOT_SANDBOX_DISK_MAX_PERCENT=${DISK_MAX_PERCENT}
NANOBOT_SANDBOX_DISK_MIN_FREE_BYTES=${DISK_MIN_FREE_BYTES}
NANOBOT_SANDBOX_IO_DEVICE=${io_device}
NANOBOT_SANDBOX_IO_WRITE_BPS=16777216
NANOBOT_SANDBOX_RUNTIME_TTL_HOURS=168
NANOBOT_SANDBOX_RUNTIME_MAX_BYTES=10737418240
EOF
  chown root:nanobot-sandboxd /etc/nanobot/sandboxd.env
  chmod 0640 /etc/nanobot/sandboxd.env
}

install_profile_manifest() {
  local expected_sha256
  expected_sha256="$(profile_manifest_sha256_from_stage)"
  install -d -m 0700 -o root -g root /etc/nanobot
  install -m 0640 -o root -g nanobot-sandboxd \
    "${BUILT_PROFILE_MANIFEST}" \
    "${INSTALLED_PROFILE_MANIFEST}"
  [[ "$(sha256sum "${INSTALLED_PROFILE_MANIFEST}" | awk '{print $1}')" \
      == "${expected_sha256}" ]] \
    || die "安装后的 Profile manifest 摘要不一致"
  PYTHONPATH="${RELEASE_DIR}" "${SANDBOXD_VENV}/bin/python" - \
    "${INSTALLED_PROFILE_MANIFEST}" <<'PY'
import sys

from core.sandbox.profile_catalog import load_profile_catalog

load_profile_catalog(sys.argv[1])
PY
}

probe_sandboxd() {
  "${SANDBOXD_VENV}/bin/python" - <<'PY'
import socket
import time
from pathlib import Path

socket_path = "/run/nanobot-sandboxd/sandboxd.sock"
token = Path("/etc/nanobot/sandboxd.token").read_bytes().strip()
deadline = time.monotonic() + 30.0

for path in ("/v1/healthz", "/v1/readyz"):
    while True:
        request = (
            b"GET " + path.encode("ascii") + b" HTTP/1.1\r\n"
            b"Host: sandboxd\r\n"
            b"Authorization: Bearer " + token + b"\r\n"
            b"Connection: close\r\n\r\n"
        )
        first_line = b""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1.0)
                client.connect(socket_path)
                client.sendall(request)
                response = bytearray()
                while True:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    response.extend(chunk)
            first_line = bytes(response).split(b"\r\n", 1)[0]
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout):
            pass

        if b" 200 " in first_line:
            print(path, first_line.decode("ascii", "replace"))
            break
        if first_line and b" 503 " not in first_line:
            raise SystemExit(f"{path} failed: {first_line!r}")
        if time.monotonic() >= deadline:
            raise SystemExit(f"{path} did not become ready within 30 seconds")
        time.sleep(0.1)
PY
}

sandboxd_assert_quiesced() {
  PYTHONPATH="${SERVER_RELEASE_LINK}" \
    "${SANDBOXD_VENV}/bin/python" \
    -m sandboxd.maintenance_probe \
    --socket /run/nanobot-sandboxd/sandboxd.sock \
    --token-file /etc/nanobot/sandboxd-admin.token \
    --timeout-seconds 5
}

sandboxd_admin_terminate_all() {
  local reason="$1"
  "${SANDBOXD_VENV}/bin/python" - "${reason}" <<'PY'
import json
import secrets
import socket
import sys
from pathlib import Path

socket_path = "/run/nanobot-sandboxd/sandboxd.sock"
token = Path("/etc/nanobot/sandboxd-admin.token").read_bytes().strip()
reason = sys.argv[1]
request_id = f"sbxops_{secrets.token_hex(12)}"
body = json.dumps(
    {"request_id": request_id, "reason": reason},
    separators=(",", ":"),
).encode("utf-8")
request = (
    b"POST /v1/admin/leases/terminate-all HTTP/1.1\r\n"
    b"Host: sandboxd\r\n"
    b"Authorization: Bearer " + token + b"\r\n"
    b"X-Nanobot-Request-ID: " + request_id.encode("ascii") + b"\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
    b"Connection: close\r\n\r\n"
    + body
)
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.settimeout(30.0)
    client.connect(socket_path)
    client.sendall(request)
    response = bytearray()
    while True:
        chunk = client.recv(65536)
        if not chunk:
            break
        response.extend(chunk)

header, separator, raw_body = bytes(response).partition(b"\r\n\r\n")
status_line = header.split(b"\r\n", 1)[0]
if not separator or b" 200 " not in status_line:
    raise SystemExit(
        "sandboxd terminate-all failed: "
        + status_line.decode("ascii", "replace")
    )
result = json.loads(raw_body)
if result.get("status") != "success":
    raise SystemExit("sandboxd terminate-all returned non-success result")
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
PY
}

install_control_plane_command() {
  require_no_extra_args "$@"
  require_root
  load_config
  require_stage image-built
  assert_release_checkout_current
  assert_prepared_storage_current
  assert_host_prepared_current
  image_id_from_stage >/dev/null

  log "安装只读发布树"
  install_release_tree
  log "安装独立 Python 3.11 与 sandboxd 依赖"
  install_sandboxd_python
  log "创建 sandboxd Token 与固定策略配置"
  install_profile_manifest
  install_sandboxd_credentials_and_env

  install -m 0644 \
    "${RELEASE_DIR}/deploy/systemd/nanobot-sandboxd.tmpfiles.conf" \
    /etc/tmpfiles.d/nanobot-sandboxd.conf
  systemd-tmpfiles --create /etc/tmpfiles.d/nanobot-sandboxd.conf

  install -m 0644 \
    "${RELEASE_DIR}/deploy/systemd/nanobot-sandboxd.service" \
    /etc/systemd/system/nanobot-sandboxd.service
  install -m 0644 \
    "${RELEASE_DIR}/deploy/systemd/nanobot-sandbox-runtime-cleanup.service" \
    /etc/systemd/system/nanobot-sandbox-runtime-cleanup.service
  install -m 0644 \
    "${RELEASE_DIR}/deploy/systemd/nanobot-sandbox-runtime-cleanup.timer" \
    /etc/systemd/system/nanobot-sandbox-runtime-cleanup.timer

  systemd-analyze verify \
    /etc/systemd/system/nanobot-sandboxd.service \
    /etc/systemd/system/nanobot-sandbox-runtime-cleanup.service \
    /etc/systemd/system/nanobot-sandbox-runtime-cleanup.timer
  systemctl daemon-reload
  systemctl disable --now nanobot-sandbox-runtime-cleanup.timer >/dev/null 2>&1 || true
  if docker ps \
    --filter 'label=com.nanobot.sandbox=true' \
    --filter 'label=com.nanobot.managed-by=sandboxd' \
    --format '{{.Names}}' | grep -q .; then
    die "仍有活动 Sandbox 容器，拒绝重启 sandboxd"
  fi
  log "原子切换 sandboxd 只读发布树"
  activate_release_tree
  if [[ -n "${ACTIVATED_FROM_RELEASE}" ]]; then
    mark_stage control-plane-rollback \
      "release=${ACTIVATED_FROM_RELEASE}"
  fi
  systemctl enable nanobot-sandboxd.service
  systemctl restart nanobot-sandboxd.service
  systemctl is-active --quiet nanobot-sandboxd.service \
    || die "nanobot-sandboxd.service 未运行"

  probe_sandboxd
  [[ "$(stat -c '%a:%G' /run/nanobot-sandboxd/client.token)" \
      == "640:nanobot-sandboxd" ]] \
    || die "sandboxd client Token 权限错误"
  [[ "$(stat -c '%a:%G' /run/nanobot-sandboxd/admin-client.token)" \
      == "640:nanobot-sandboxd" ]] \
    || die "sandboxd 管理 client Token 权限错误"
  [[ "$(stat -c '%a:%G' /run/nanobot-sandboxd/sandboxd.sock)" \
      == "660:nanobot-sandboxd" ]] \
    || die "sandboxd UDS 权限错误"
  [[ -f "${RUNTIME_PROFILE_MANIFEST}" \
      && ! -L "${RUNTIME_PROFILE_MANIFEST}" \
      && "$(stat -c '%a:%G' "${RUNTIME_PROFILE_MANIFEST}")" \
        == "640:nanobot-sandboxd" \
      && "$(sha256sum "${RUNTIME_PROFILE_MANIFEST}" | awk '{print $1}')" \
        == "$(profile_manifest_sha256_from_stage)" ]] \
    || die "sandboxd 运行时 Profile manifest 权限或摘要错误"

  mark_stage control-plane-ready \
    "release=${RELEASE}|manifest=$(profile_manifest_sha256_from_stage)"
  log "sandboxd healthz/readyz 均通过"
  printf '下一步：准备 OCI digest、SBOM、验证结果与 ReleaseManifest，再使用 scripts/deploy-production.sh。\n'
}

upsert_env_value() {
  local file="$1"
  local key="$2"
  local value="$3"
  if grep -q "^${key}=" "${file}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${file}"
  else
    printf '%s=%s\n' "${key}" "${value}" >>"${file}"
  fi
}

configure_application_env_off() {
  local env_file="${REPO_ROOT}/.env"
  local original_uid
  local original_gid
  local backup
  local asset_secret

  [[ -f "${env_file}" && ! -L "${env_file}" ]] \
    || die "缺少生产 .env：${env_file}"
  original_uid="$(stat -c '%u' "${env_file}")"
  original_gid="$(stat -c '%g' "${env_file}")"
  backup="/root/nanobot.env.pre-sandbox-$(date +%Y%m%dT%H%M%S)"
  install -m 0600 "${env_file}" "${backup}"

  upsert_env_value "${env_file}" NANOBOT_SANDBOX_ENABLED false
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_EXEC_ENABLED false
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_GROUP_ENABLED false
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED false
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_SESSION_EXECUTION_ALLOWED false
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED false
  upsert_env_value "${env_file}" \
    NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE \
    "${RUNTIME_PROFILE_MANIFEST}"
  upsert_env_value "${env_file}" NANOBOT_RUNTIME_UID "${RUNTIME_UID}"
  upsert_env_value "${env_file}" NANOBOT_RUNTIME_GID "${RUNTIME_GID}"
  upsert_env_value "${env_file}" NANOBOT_SANDBOXD_GID "${SANDBOXD_GID}"
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_WORKSPACE_QUOTA_BYTES "${WORKSPACE_QUOTA_BYTES}"
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_ASSET_MAX_BYTES "${ASSET_MAX_BYTES}"
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_TOTAL_QUOTA_BYTES "${TOTAL_QUOTA_BYTES}"
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_DISK_MAX_PERCENT "${DISK_MAX_PERCENT}"
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_DISK_MIN_FREE_BYTES "${DISK_MIN_FREE_BYTES}"
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_BACKEND_TIMEOUT 15
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_RUN_TIMEOUT 165
  upsert_env_value "${env_file}" NANOBOT_SANDBOX_ASSET_TRANSFER_TIMEOUT 600
  upsert_env_value "${env_file}" NANOBOT_ASSET_TOKEN_TTL_SECONDS 300

  asset_secret="$(sed -n 's/^NANOBOT_ASSET_TOKEN_SECRET=//p' "${env_file}" \
    | awk 'length($0) > 0 {value=$0} END {printf "%s", value}')"
  if (( ${#asset_secret} < 32 )); then
    asset_secret="$(openssl rand -hex 32)"
  fi
  sed -i '/^NANOBOT_ASSET_TOKEN_SECRET=/d' "${env_file}"
  printf 'NANOBOT_ASSET_TOKEN_SECRET=%s\n' "${asset_secret}" >>"${env_file}"
  unset asset_secret
  grep -q '^NANOBOT_ASSET_TOKEN_SECRET=.' "${env_file}" \
    || die "Asset Token Secret 未配置"

  chown "${original_uid}:${original_gid}" "${env_file}"
  chmod 0600 "${env_file}"
  (cd "${REPO_ROOT}" && docker compose config --quiet)
}

wait_server_health() {
  local _
  for _ in {1..45}; do
    if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null; then
      return 0
    fi
    sleep 2
  done
  return 1
}

restart_fixed_containers() {
  docker start "${FIXED_CONTAINERS[@]}" >/dev/null
  wait_server_health || die "恢复固定 Nanobot 容器后健康检查失败"
}

coordinated_backup() {
  assert_backup_target_policy
  [[ -f "${REPO_ROOT}/data/nanobot.db" && ! -L "${REPO_ROOT}/data/nanobot.db" ]] \
    || die "当前协调备份仅支持现有 SQLite 文件"
  local data_source
  local backup_source
  local data_disks=""
  local backup_disks=""
  if [[ "${BACKUP_MODE}" == "independent" ]]; then
    data_source="$(findmnt -n -o SOURCE "${DATA_ROOT}")"
    backup_source="$(findmnt -n -o SOURCE "${BACKUP_MOUNT}")"
    [[ "${data_source}" != "${backup_source}" ]] \
      || die "备份目标与 Sandbox 数据盘使用相同来源"
    if [[ -b "$(readlink -f "${data_source}")" \
        && -b "$(readlink -f "${backup_source}")" ]]; then
      data_disks="$(lsblk -s -n -o PATH,TYPE "${data_source}" \
        | awk '$2 == "disk" {print $1}' | sort -u)"
      backup_disks="$(lsblk -s -n -o PATH,TYPE "${backup_source}" \
        | awk '$2 == "disk" {print $1}' | sort -u)"
      if [[ -n "${data_disks}" && -n "${backup_disks}" ]] \
        && comm -12 <(printf '%s\n' "${data_disks}") \
          <(printf '%s\n' "${backup_disks}") | grep -q .; then
        die "备份目标与 Sandbox 数据盘位于同一物理磁盘"
      fi
    fi
  fi

  log "停止 4 个固定 Nanobot 服务以执行协调备份"
  (cd "${REPO_ROOT}" && docker compose stop "${FIXED_SERVICES[@]}")
  if ! "${REPO_ROOT}/scripts/sandbox-coordinated-backup.sh" \
    --database "${REPO_ROOT}/data/nanobot.db" \
    --destination "${BACKUP_MOUNT}" \
    --data-root "${DATA_ROOT}" \
    --backup-mode "${BACKUP_MODE}" \
    --risk-marker "${BACKUP_RISK_MARKER}" \
    --max-bytes "${BACKUP_MAX_BYTES}" \
    --system-min-free-bytes "${SYSTEM_MIN_FREE_BYTES}" \
    --quiesced \
    --apply; then
    warn "协调备份失败，正在恢复原固定容器"
    restart_fixed_containers
    die "协调备份失败"
  fi
  restart_fixed_containers
}

prepare_runtime_bind_mounts() {
  local runtime_host_read_gid
  runtime_host_read_gid="$(id -g "$(deploy_user)")"
  log "停止 4 个固定 Nanobot 服务以迁移非 root Runtime 权限"
  (cd "${REPO_ROOT}" && docker compose stop "${FIXED_SERVICES[@]}")
  if ! env \
    NANOBOT_RUNTIME_UID="${RUNTIME_UID}" \
    NANOBOT_RUNTIME_GID="${RUNTIME_GID}" \
    NANOBOT_RUNTIME_HOST_READ_GID="${runtime_host_read_gid}" \
    "${REPO_ROOT}/scripts/prepare-runtime-directories.sh" --fix-existing; then
    warn "Runtime bind mount 权限迁移失败，正在恢复原固定容器"
    restart_fixed_containers
    die "Runtime bind mount 权限迁移失败"
  fi
  log "按部署账号 UID/GID 重建并恢复原固定服务"
  (cd "${REPO_ROOT}" \
    && docker compose up -d --force-recreate "${FIXED_SERVICES[@]}") \
    || die "按新 Runtime UID/GID 恢复原固定服务失败"
  wait_server_health || die "按新 Runtime UID/GID 恢复原固定服务后健康检查失败"
}

capture_nonfixed_containers() {
  local output_file="$1"
  docker ps --format '{{.Names}} {{.ID}}' \
    | awk '
        $1 != "nanobot-server" &&
        $1 != "nanobot-session-summary-worker" &&
        $1 != "nanobot-outbound-delivery-worker" &&
        $1 != "nanobot-semantic-index-worker"
      ' \
    | sort >"${output_file}"
}

admin_api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  docker exec -i \
    -e ADMIN_METHOD="${method}" \
    -e ADMIN_PATH="${path}" \
    -e ADMIN_BODY="${body}" \
    nanobot-server \
    python - <<'PY'
import json
import os
import urllib.error
import urllib.request

method = os.environ["ADMIN_METHOD"]
path = os.environ["ADMIN_PATH"]
body = os.environ.get("ADMIN_BODY", "")
token = os.environ.get("NANOBOT_ADMIN_TOKEN", "")
if not token:
    raise SystemExit("NANOBOT_ADMIN_TOKEN 未配置")

request = urllib.request.Request(
    "http://127.0.0.1:8000/api/v1/admin" + path,
    data=body.encode("utf-8") if body else None,
    method=method,
    headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    },
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    print(exc.read().decode("utf-8", "replace"))
    raise
print(json.dumps(result, ensure_ascii=False))
PY
}

deploy_command() {
  die "deploy 已停用；Sandbox 管理脚本不再构建或切换 nanobot-runtime:latest，请使用 scripts/deploy-production.sh"
}

deploy_runtime_command() {
  die "deploy-runtime 已停用；正式 Runtime 只能使用 scripts/deploy-production.sh 和完整 ReleaseManifest"
}

terminate_leases_command() {
  require_root
  load_config
  require_stage control-plane-ready
  (( $# <= 2 )) || die "terminate-leases 最多接受一个 reason 参数"
  local reason="${2:-admin_terminated}"
  case "${reason}" in
    admin_terminated|kill_switch|controller_restarted|lease_recycled) ;;
    *) die "terminate-leases reason 无效：${reason}" ;;
  esac
  sandboxd_admin_terminate_all "${reason}" | jq
}

kill_switch_command() {
  require_root
  (( $# <= 2 )) || die "kill-switch 最多接受一个 reason 参数"
  local reason="${2:-管理员紧急关闭 Sandbox}"
  local request_id
  local body
  request_id="sbxkill_$(openssl rand -hex 16)"
  body="$(jq -nc \
    --arg request_id "${request_id}" \
    --arg reason "${reason}" \
    '{request_id:$request_id,reason:$reason}')"
  admin_api POST /sandbox/kill-switch "${body}" | jq
}

runtime_cleanup_command() {
  require_root
  load_config
  require_stage control-plane-ready

  local apply=false
  shift
  while (( $# )); do
    case "$1" in
      --apply)
        apply=true
        shift
        ;;
      *)
        die "runtime-cleanup 未知参数：$1"
        ;;
    esac
  done

  if [[ "${apply}" != "true" ]]; then
    "${SERVER_RELEASE_LINK}/scripts/cleanup-sandbox-runtime.sh" \
      --data-root "${DATA_ROOT}" \
      --ttl-hours 168 \
      --max-bytes 10737418240
    return 0
  fi

  kill_switch_command kill-switch "Sandbox runtime TTL 维护窗口" >/dev/null
  sandboxd_assert_quiesced \
    || die "sandboxd 未能证明执行静默，拒绝 runtime 清理"
  install -m 0600 /dev/null \
    /run/nanobot-sandboxd/runtime-cleanup-approved
  systemctl start nanobot-sandbox-runtime-cleanup.service
  if systemctl is-failed --quiet nanobot-sandbox-runtime-cleanup.service; then
    die "runtime cleanup service 失败"
  fi
  log "runtime TTL 清理已执行；Sandbox 开关保持关闭"
}

enable_runtime_timer_command() {
  require_no_extra_args "$@"
  require_root
  load_config
  require_stage control-plane-ready
  systemctl enable --now nanobot-sandbox-runtime-cleanup.timer
  systemctl list-timers nanobot-sandbox-runtime-cleanup.timer --no-pager
}

status_command() {
  require_no_extra_args "$@"
  require_root
  local actual_allocated_bytes=""
  local allocated_blocks=""
  local allocation_status=""
  local block_bytes=""
  local fstrim_status=""
  local loopback_mount_line=""
  printf '仓库：%s\n' "${REPO_ROOT}"
  printf '当前 HEAD：%s\n' "$(repo_git rev-parse HEAD 2>/dev/null || printf unknown)"

  if [[ -f "${CONFIG_FILE}" && ! -L "${CONFIG_FILE}" ]]; then
    load_config
    printf '安装配置：已配置\n'
    printf 'RELEASE：%s\n' "${RELEASE}"
    printf '发布来源：%s\n' "${RELEASE_REF}"
    printf 'VERSION：%s\n' "${VERSION}"
    printf '存储模式：%s\n' "${STORAGE_MODE}"
    if [[ "${STORAGE_MODE}" == "loopback" ]]; then
      printf 'XFS 镜像：%s GiB（固定路径）\n' \
        "$((DATA_IMAGE_SIZE_BYTES / 1024 / 1024 / 1024))"
      if [[ -f "${DATA_IMAGE}" && ! -L "${DATA_IMAGE}" ]]; then
        allocated_blocks="$(stat -c '%b' "${DATA_IMAGE}")"
        block_bytes="$(stat -c '%B' "${DATA_IMAGE}")"
        actual_allocated_bytes=$((allocated_blocks * block_bytes))
        if "${SCRIPT_DIR}/check-loopback-image-allocation.sh" \
            "${DATA_IMAGE}" "${DATA_IMAGE_SIZE_BYTES}" >/dev/null 2>&1; then
          allocation_status="PASS"
        else
          allocation_status="BLOCKED"
        fi
        printf 'XFS 镜像实际分配：%s 字节（%s）\n' \
          "${actual_allocated_bytes}" "${allocation_status}"
      else
        printf 'XFS 镜像实际分配：MISSING（BLOCKED）\n'
      fi
      loopback_mount_line="$(awk -v target="${DATA_ROOT}" \
        '$1 !~ /^#/ && $2 == target {print}' /etc/fstab 2>/dev/null || true)"
      if [[ "${loopback_mount_line}" == "$(loopback_fstab_line)" ]] \
          && ! systemctl is-active --quiet fstrim.service; then
        fstrim_status="PASS"
      else
        fstrim_status="BLOCKED"
      fi
      printf '定时 TRIM 排除：%s\n' "${fstrim_status}"
    fi
    printf '备份模式：%s\n' "${BACKUP_MODE}"
    printf '备份目标：%s\n' "${BACKUP_MOUNT}"
    printf '单次备份上限：%s GiB\n' \
      "$((BACKUP_MAX_BYTES / 1024 / 1024 / 1024))"
    printf '根分区最低保留：%s GiB\n' \
      "$((SYSTEM_MIN_FREE_BYTES / 1024 / 1024 / 1024))"
    printf '本地镜像构建余量：%s GiB\n' \
      "$((BUILD_RESERVE_BYTES / 1024 / 1024 / 1024))"
    printf '备份风险标记：%s\n' "${BACKUP_RISK_MARKER}"
  else
    printf '安装配置：未配置\n'
  fi

  printf 'AppArmor 内核：%s\n' \
    "$(cat /sys/module/apparmor/parameters/enabled 2>/dev/null || printf unknown)"
  local apparmor_profile
  for apparmor_profile in \
      nanobot-sandbox-restricted \
      nanobot-sandbox-developer; do
    if grep -q "^${apparmor_profile} " \
        /sys/kernel/security/apparmor/profiles 2>/dev/null; then
      printf '%s profile：LOADED\n' "${apparmor_profile}"
    else
      printf '%s profile：NOT LOADED\n' "${apparmor_profile}"
    fi
  done
  if mountpoint -q "${DATA_ROOT}"; then
    printf 'Sandbox 数据盘：MOUNTED\n'
    findmnt -n -o SOURCE,FSTYPE,OPTIONS "${DATA_ROOT}"
    df -hP "${DATA_ROOT}" | tail -n 1
  else
    printf 'Sandbox 数据盘：NOT MOUNTED\n'
  fi
  if systemctl is-active --quiet nanobot-sandboxd.service; then
    printf 'sandboxd：ACTIVE\n'
  else
    printf 'sandboxd：INACTIVE\n'
  fi
  [[ -S /run/nanobot-sandboxd/sandboxd.sock ]] \
    && printf 'sandboxd UDS：EXISTS\n' \
    || printf 'sandboxd UDS：MISSING\n'

  if docker info >/dev/null 2>&1; then
    printf 'Docker SecurityOptions：'
    docker info --format '{{json .SecurityOptions}}'
    docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' \
      | grep -E '^nanobot-sandbox-(python|developer):' || true
    if docker inspect nanobot-server >/dev/null 2>&1; then
      printf '运行 Runtime 镜像：%s\n' "$(docker inspect nanobot-server \
        --format '{{.Config.Image}}' 2>/dev/null || true)"
      printf '运行 Runtime 提交：%s\n' "$(docker inspect nanobot-server \
        --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true)"
      printf 'Runtime 发布状态：由 ReleaseManifest 部署器独立维护\n'
      if docker exec nanobot-server python -c 'import core.sandbox' >/dev/null 2>&1; then
        local api_status
        if api_status="$(admin_api GET /sandbox/status 2>/dev/null)"; then
          jq '{feature,controller,disk_watermark}' <<<"${api_status}"
        fi
      fi
    fi
  else
    printf 'Docker：UNAVAILABLE\n'
  fi

  printf '阶段状态：\n'
  local stage
  for stage in \
    data-formatted \
    host-prepared \
    image-built \
    smoke-passed \
    control-plane-ready; do
    if stage_exists "${stage}"; then
      printf '  %-24s DONE\n' "${stage}"
    else
      printf '  %-24s PENDING\n' "${stage}"
    fi
  done
}

sandbox_web_management_required_command() {
  die "该 owner/ToolOverride 命令已永久停止执行；请使用 Web「Sandbox 管理」页按 canonical session 管理授权与配额"
}

main() {
  local command="${1:-help}"
  case "${command}" in
    help|-h|--help)
      usage
      ;;
    configure)
      configure_command "$@"
      ;;
    update-release)
      update_release_command "$@"
      ;;
    promote-release)
      promote_release_command "$@"
      ;;
    status)
      status_command "$@"
      ;;
    prepare-host)
      prepare_host_command "$@"
      ;;
    build-image)
      build_image_command "$@"
      ;;
    smoke)
      smoke_command "$@"
      ;;
    install-control-plane)
      install_control_plane_command "$@"
      ;;
    deploy)
      deploy_command "$@"
      ;;
    deploy-runtime)
      deploy_runtime_command "$@"
      ;;
    provision-owner)
      sandbox_web_management_required_command "$@"
      ;;
    enable-workspace)
      sandbox_web_management_required_command "$@"
      ;;
    enable-assets)
      sandbox_web_management_required_command "$@"
      ;;
    enable-exec)
      sandbox_web_management_required_command "$@"
      ;;
    terminate-leases)
      terminate_leases_command "$@"
      ;;
    kill-switch)
      kill_switch_command "$@"
      ;;
    disable-owner)
      sandbox_web_management_required_command "$@"
      ;;
    runtime-cleanup)
      runtime_cleanup_command "$@"
      ;;
    enable-runtime-timer)
      enable_runtime_timer_command "$@"
      ;;
    *)
      usage >&2
      die "未知子命令：${command}"
      ;;
  esac
}

main "$@"
