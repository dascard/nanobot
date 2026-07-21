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
readonly LOOPBACK_STORAGE_DIR="/var/lib/nanobot-sandbox-storage"
readonly LOOPBACK_IMAGE="${LOOPBACK_STORAGE_DIR}/data.xfs"
readonly XFS_LABEL="nanobot-sbx"
readonly PROJECT_MAP="/etc/nanobot/sandbox-projects.tsv"

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
readonly -a WORKSPACE_TOOLS=(
  workspace_list
  workspace_read
  workspace_search
  workspace_write
)
readonly -a ASSET_TOOLS=(asset_import asset_publish)
readonly -a ALL_SANDBOX_TOOLS=(
  workspace_list
  workspace_read
  workspace_search
  workspace_write
  asset_import
  asset_publish
  sandbox_exec
)

CURRENT_COMMAND="${1:-help}"

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
  6. deploy
  7. provision-owner
  8. enable-workspace
  9. enable-assets       # Workspace 灰度稳定后
 10. enable-exec         # Asset 与真实执行验收稳定后

子命令：
  configure             保存设备、备份挂载点和首期 owner 等非敏感参数
  status                只读显示主机、控制面、镜像和阶段状态
  prepare-host          安装依赖、准备数据盘、目录和 AppArmor
  build-image           构建固定版本 Sandbox 镜像
  smoke                 运行不得跳过的真实 Docker 安全矩阵
  install-control-plane 安装 Python 3.11、sandboxd、Token、UDS 和 systemd 单元
  deploy                协调备份并部署新版 Nanobot Runtime，开关保持关闭
  provision-owner       预建首期 Workspace 并配置 project quota
  enable-workspace      只开放 4 个 Workspace 工具
  enable-assets         追加开放 asset_import / asset_publish
  enable-exec           最后开放 sandbox_exec
  kill-switch           无损关闭 sandbox.enabled 与 sandbox.exec_enabled
  disable-owner         关闭总开关并删除首期 owner 的 7 个 ToolOverride
  runtime-cleanup       预览或执行 runtime TTL 清理
  enable-runtime-timer  在维护审批流程建立后启用每日 timer

configure 参数：
  --storage-mode <模式>      block 或 loopback
  --data-device <设备>       block 模式的独立空白分区或 LV，例如 /dev/sdb1
  --loopback-size-gib <整数> loopback 模式容量，16..32，默认 16
  --backup-mount <目录>      独立备份文件系统挂载点
  --owner-id <QQ 用户 ID>   首期精确私聊 owner
  [--platform qq]
  [--release <40 位提交>]
  [--version <镜像版本>]
  [--project-id 10000]
  [--sandboxd-gid 10001]

prepare-host 参数：
  --initialize-storage       首次初始化数据存储；仍需手工输入精确确认文本

smoke 参数：
  --retry                    删除并重建本脚本固定命名的失败 Smoke worktree

runtime-cleanup 参数：
  默认只预览；实际执行必须增加 --apply。

安全约束：
  - 不执行任何 Docker 全局 prune。
  - 不给 Nanobot Server、Worker 或 Sandbox 容器挂载 Docker Socket。
  - 不自动打开群聊 Sandbox。
  - block 模式拒绝根盘；loopback 模式只格式化固定容量的镜像文件。
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

validate_release() {
  [[ "${RELEASE}" =~ ^[0-9a-f]{40}$ ]] || die "RELEASE 必须是 40 位小写提交哈希"
}

validate_release_in_repo() {
  validate_release
  repo_git cat-file -e "${RELEASE}^{commit}" 2>/dev/null \
    || die "本地仓库不存在 RELEASE=${RELEASE}"
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
  [[ "${SANDBOX_OWNER_ID}" =~ ^[0-9]{5,32}$ ]] \
    || die "SANDBOX_OWNER_ID 必须是准确的 QQ 数字用户 ID"
  [[ "${SANDBOX_PLATFORM}" =~ ^[0-9A-Za-z._-]{1,32}$ ]] \
    || die "SANDBOX_PLATFORM 无效"
  [[ "${VERSION}" =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$ && "${VERSION}" != "latest" ]] \
    || die "VERSION 无效或使用了 latest"
  [[ "${PROJECT_ID}" =~ ^[0-9]+$ ]] \
    || die "PROJECT_ID 必须是整数"
  (( PROJECT_ID >= 10000 && PROJECT_ID <= 2147483647 )) \
    || die "PROJECT_ID 必须位于 10000..2147483647"
  [[ "${SANDBOXD_GID}" =~ ^[0-9]+$ ]] \
    || die "SANDBOXD_GID 必须是整数"
  (( SANDBOXD_GID >= 1 && SANDBOXD_GID <= 2147483647 )) \
    || die "SANDBOXD_GID 超出范围"
  [[ "${SYSTEM_MIN_FREE_BYTES}" =~ ^[0-9]+$ ]] \
    || die "SYSTEM_MIN_FREE_BYTES 必须是整数"
  (( SYSTEM_MIN_FREE_BYTES >= 60 * 1024 * 1024 * 1024 )) \
    || die "根文件系统最低保留空间不得低于 60 GiB"
  validate_release
}

load_config() {
  require_root
  [[ -f "${CONFIG_FILE}" && ! -L "${CONFIG_FILE}" ]] \
    || die "尚未配置；请先运行 configure"
  [[ "$(stat -c '%u:%a' "${CONFIG_FILE}")" == "0:600" ]] \
    || die "${CONFIG_FILE} 必须由 root 拥有且权限为 0600"

  # 配置由本脚本以 root:0600 和 printf %q 写入，可安全加载。
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"

  STORAGE_MODE="${STORAGE_MODE:-block}"
  DATA_DEVICE="${DATA_DEVICE:-}"
  DATA_IMAGE="${DATA_IMAGE:-}"
  DATA_IMAGE_SIZE_BYTES="${DATA_IMAGE_SIZE_BYTES:-0}"
  SYSTEM_MIN_FREE_BYTES="${SYSTEM_MIN_FREE_BYTES:-64424509440}"
  : "${BACKUP_MOUNT:?}"
  : "${SANDBOX_OWNER_ID:?}"
  : "${SANDBOX_PLATFORM:?}"
  : "${RELEASE:?}"
  : "${VERSION:?}"
  : "${PROJECT_ID:?}"
  : "${SANDBOXD_GID:?}"
  : "${WORKSPACE_QUOTA_BYTES:?}"
  : "${ASSET_MAX_BYTES:?}"
  : "${TOTAL_QUOTA_BYTES:?}"
  : "${DISK_MAX_PERCENT:?}"
  : "${DISK_MIN_FREE_BYTES:?}"

  validate_config_values
  RELEASE_DIR="/opt/nanobot-releases/${RELEASE}"
  SANDBOX_IMAGE="nanobot-sandbox-python:${VERSION}"
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
    printf 'BACKUP_MOUNT=%q\n' "${BACKUP_MOUNT}"
    printf 'SANDBOX_OWNER_ID=%q\n' "${SANDBOX_OWNER_ID}"
    printf 'SANDBOX_PLATFORM=%q\n' "${SANDBOX_PLATFORM}"
    printf 'RELEASE=%q\n' "${RELEASE}"
    printf 'VERSION=%q\n' "${VERSION}"
    printf 'PROJECT_ID=%q\n' "${PROJECT_ID}"
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
  local backup_mount=""
  local owner_id=""
  local platform="qq"
  local release
  local version=""
  local project_id="10000"
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
      --owner-id)
        [[ $# -ge 2 ]] || die "--owner-id 缺少参数"
        owner_id="$2"
        shift 2
        ;;
      --platform)
        [[ $# -ge 2 ]] || die "--platform 缺少参数"
        platform="$2"
        shift 2
        ;;
      --release)
        [[ $# -ge 2 ]] || die "--release 缺少参数"
        release="$2"
        shift 2
        ;;
      --version)
        [[ $# -ge 2 ]] || die "--version 缺少参数"
        version="$2"
        shift 2
        ;;
      --project-id)
        [[ $# -ge 2 ]] || die "--project-id 缺少参数"
        project_id="$2"
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
  [[ -n "${owner_id}" ]] || die "必须提供 --owner-id"
  [[ -d "${backup_mount}" ]] || die "备份目录不存在：${backup_mount}"
  mountpoint -q "${backup_mount}" || die "备份目录本身必须是独立挂载点"

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
  SANDBOX_OWNER_ID="${owner_id}"
  SANDBOX_PLATFORM="${platform}"
  RELEASE="${release}"
  VERSION="${version:-${release:0:7}-$(date +%Y%m%d)}"
  PROJECT_ID="${project_id}"
  SANDBOXD_GID="${sandboxd_gid}"
  WORKSPACE_QUOTA_BYTES=2147483648
  ASSET_MAX_BYTES=536870912
  TOTAL_QUOTA_BYTES=10737418240
  DISK_MAX_PERCENT=80
  SYSTEM_MIN_FREE_BYTES=64424509440

  validate_config_values
  if [[ "${STORAGE_MODE}" == "loopback" ]]; then
    assert_backup_mount_independent_from_root
  fi
  validate_release_in_repo
  [[ "$(repo_git rev-parse HEAD)" == "${RELEASE}" ]] \
    || die "configure 要求 RELEASE 等于当前 HEAD"
  [[ -z "$(repo_git status --porcelain)" ]] \
    || die "生产 checkout 不干净；请先完成审查、测试、提交和推送"
  repo_git ls-files --error-unmatch scripts/manage-sandbox-production.sh \
    >/dev/null 2>&1 \
    || die "本管理脚本尚未纳入 RELEASE"
  [[ "$(repo_git rev-parse origin/master)" == "${RELEASE}" ]] \
    || die "configure 要求 RELEASE 已发布到 origin/master"
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
  printf 'OWNER=已配置（不回显）\n'
  printf 'RELEASE=%s\n' "${RELEASE}"
  printf 'VERSION=%s\n' "${VERSION}"
  printf '下一步：sudo %s prepare-host --initialize-storage\n' "$0"
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

verify_loopback_image() {
  [[ -f "${DATA_IMAGE}" && ! -L "${DATA_IMAGE}" ]] \
    || die "loopback 镜像必须是非符号链接普通文件：${DATA_IMAGE}"
  [[ "$(stat -c '%u:%a' "${DATA_IMAGE}")" == "0:600" ]] \
    || die "loopback 镜像必须由 root 拥有且权限为 0600"
  [[ "$(stat -c '%s' "${DATA_IMAGE}")" == "${DATA_IMAGE_SIZE_BYTES}" ]] \
    || die "loopback 镜像容量与配置不一致"
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
  required_free=$((DATA_IMAGE_SIZE_BYTES + SYSTEM_MIN_FREE_BYTES + 20 * 1024 * 1024 * 1024))
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
  if ! mkfs.xfs -L "${XFS_LABEL}" "${loop_device}"; then
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
  mv -- "${temporary}" "${DATA_IMAGE}"
  temporary=""
  mark_stage data-formatted \
    "mode=loopback|image=${DATA_IMAGE}|size=${DATA_IMAGE_SIZE_BYTES}|uuid=${data_uuid}"
  trap - EXIT HUP INT TERM
)

prepare_loopback_data_mount() {
  local allow_format="$1"
  local data_uuid=""
  local expected_marker=""
  local probe_loop=""
  local mount_source=""
  local fstab_line=""

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
    fstab_line="${DATA_IMAGE} ${DATA_ROOT} xfs loop,nodev,nosuid,prjquota 0 0"
    append_fstab_line "${fstab_line}"
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
    fstab_line="${DATA_IMAGE} ${DATA_ROOT} xfs loop,nodev,nosuid,prjquota 0 0"
    append_fstab_line "${fstab_line}"
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
  install -m 0644 \
    "${REPO_ROOT}/deploy/apparmor/nanobot-sandbox" \
    /etc/apparmor.d/nanobot-sandbox
  apparmor_parser -r /etc/apparmor.d/nanobot-sandbox
  grep -q '^nanobot-sandbox ' /sys/kernel/security/apparmor/profiles \
    || die "nanobot-sandbox AppArmor profile 未实际加载"
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
  validate_release_in_repo

  local allow_format=false
  shift
  while (( $# )); do
    case "$1" in
      --initialize-storage)
        allow_format=true
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
  (( free_bytes >= SYSTEM_MIN_FREE_BYTES )) \
    || die "根分区可用空间低于 60 GiB，停止镜像构建"
  (( used_percent < 80 )) \
    || die "根分区使用率达到 80%，停止镜像构建"
}

build_image_command() {
  require_no_extra_args "$@"
  require_root
  load_config
  require_stage host-prepared
  validate_release_in_repo
  check_build_disk_gate

  log "构建固定 Sandbox 镜像 ${SANDBOX_IMAGE}"
  run_as_deploy env \
    -u http_proxy -u https_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY \
    -u all_proxy -u ALL_PROXY \
    "${REPO_ROOT}/scripts/build-sandbox-image.sh" "${VERSION}"

  local image_id
  local image_user
  image_id="$(docker image inspect "${SANDBOX_IMAGE}" --format '{{.Id}}')"
  image_user="$(docker image inspect "${SANDBOX_IMAGE}" --format '{{.Config.User}}')"
  [[ "${image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || die "Sandbox IMAGE ID 格式无效"
  [[ "${image_user}" == "10001:10001" ]] \
    || die "Sandbox 镜像默认用户不是 10001:10001"

  mark_stage image-built "${SANDBOX_IMAGE}|${image_id}"
  log "Sandbox 镜像构建通过：${image_id}"
  printf '下一步：sudo %s smoke\n' "$0"
}

image_id_from_stage() {
  local marker
  local marker_image
  local marker_id
  marker="$(read_stage image-built)"
  IFS='|' read -r marker_image marker_id <<<"${marker}"
  [[ "${marker_image}" == "${SANDBOX_IMAGE}" ]] \
    || die "image-built 阶段对应其他镜像：${marker_image}"
  [[ "${marker_id}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || die "image-built 阶段 IMAGE ID 无效"
  [[ "$(docker image inspect "${SANDBOX_IMAGE}" --format '{{.Id}}')" == "${marker_id}" ]] \
    || die "当前 Sandbox 镜像已变化，请重新运行 build-image 与 smoke"
  printf '%s\n' "${marker_id}"
}

assert_smoke_current() {
  local marker
  local expected_image_id
  local evidence_dir
  marker="$(read_stage smoke-passed)"
  expected_image_id="$(image_id_from_stage)"
  [[ "${marker}" == "image=${expected_image_id}|evidence="* ]] \
    || die "Smoke 凭据与当前 Sandbox 镜像不匹配"
  evidence_dir="${marker#*|evidence=}"
  [[ -f "${evidence_dir}/pytest.txt" ]] \
    || die "Smoke pytest 证据已丢失"
  grep -Eq '1 passed' "${evidence_dir}/pytest.txt" \
    || die "Smoke pytest 证据不再满足 1 passed"
  if grep -Eq '[1-9][0-9]* (failed|skipped)' "${evidence_dir}/pytest.txt"; then
    die "Smoke pytest 证据包含 failure 或 skip"
  fi
}

assert_control_plane_current() {
  local marker
  marker="$(read_stage control-plane-ready)"
  [[ "${marker}" == "release=${RELEASE}|image=$(image_id_from_stage)" ]] \
    || die "控制面凭据与当前 RELEASE/IMAGE 不匹配"
}

assert_runtime_current() {
  local marker
  marker="$(read_stage runtime-deployed)"
  [[ "${marker}" == "release=${RELEASE}|flags=off" ]] \
    || die "Runtime 部署凭据与当前 RELEASE 不匹配"
}

assert_owner_current() {
  local marker
  marker="$(read_stage owner-provisioned)"
  [[ "${marker}" =~ ^owner=${SANDBOX_OWNER_ID}\|workspace=[0-9a-f-]{36}\|project=${PROJECT_ID}\|quota=${WORKSPACE_QUOTA_BYTES}$ ]] \
    || die "owner 配额凭据与当前配置不匹配"
}

smoke_command() {
  require_root
  load_config
  require_stage host-prepared
  require_stage image-built
  validate_release_in_repo
  image_id_from_stage >/dev/null

  local smoke_dir="/var/tmp/nanobot-sandbox-security-${RELEASE:0:7}"
  local torch_requirement
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
  run_as_deploy git -C "${smoke_dir}" submodule update --init --recursive
  [[ "$(run_as_deploy git -C "${smoke_dir}/vendor/KohakuTerrarium" rev-parse HEAD)" \
      == "6c2c5f1d059ac7f99379b0cddeea21da8e9b55c0" ]] \
    || die "KT submodule 版本错误"
  run_as_deploy bash "${smoke_dir}/scripts/apply_kohaku_patches.sh"
  run_as_deploy bash "${smoke_dir}/scripts/apply_kohaku_patches.sh"
  run_as_deploy git -C "${smoke_dir}/vendor/KohakuTerrarium" diff --check

  log "安装独立 Smoke Python 3.11 环境"
  run_as_deploy /usr/local/bin/uv venv \
    --python 3.11.14 --seed "${smoke_dir}/.venv"
  run_as_deploy env \
    -u http_proxy -u https_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY \
    -u all_proxy -u ALL_PROXY \
    "${smoke_dir}/.venv/bin/python" -m pip install --upgrade pip

  torch_requirement="$(sed -n '/^torch==/p' "${smoke_dir}/requirements-test.lock")"
  [[ -n "${torch_requirement}" ]] || die "requirements-test.lock 缺少 torch"
  run_as_deploy env \
    -u http_proxy -u https_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY \
    -u all_proxy -u ALL_PROXY \
    "${smoke_dir}/.venv/bin/python" -m pip install \
    --no-deps \
    --index-url https://download.pytorch.org/whl/cpu \
    "${torch_requirement}"
  run_as_deploy env \
    -u http_proxy -u https_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY \
    -u all_proxy -u ALL_PROXY \
    "${smoke_dir}/.venv/bin/python" -m pip install \
    -r "${smoke_dir}/requirements-test.lock"
  run_as_deploy env \
    -u http_proxy -u https_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY \
    -u all_proxy -u ALL_PROXY \
    "${smoke_dir}/.venv/bin/python" -m pip install \
    --no-deps "${smoke_dir}/vendor/KohakuTerrarium"
  run_as_deploy "${smoke_dir}/.venv/bin/python" -c \
    'import torch; assert not torch.cuda.is_available()'

  install -d -m 0700 "${EVIDENCE_CACHE_ROOT}"
  log "运行真实 Docker 安全矩阵"
  env \
    PATH="${smoke_dir}/.venv/bin:/usr/local/bin:/usr/bin:/bin" \
    XDG_CACHE_HOME="${EVIDENCE_CACHE_ROOT}" \
    "${smoke_dir}/scripts/sandbox-smoke-test.sh" "${SANDBOX_IMAGE}"

  evidence_dir="$(find "${EVIDENCE_CACHE_ROOT}/nanobot-sandbox-smoke" \
    -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
    | sort -n | tail -n 1 | cut -d' ' -f2-)"
  [[ -n "${evidence_dir}" && -f "${evidence_dir}/pytest.txt" ]] \
    || die "找不到 Smoke pytest 证据"
  grep -Eq '1 passed' "${evidence_dir}/pytest.txt" \
    || die "真实 Docker 安全矩阵没有得到 1 passed"
  if grep -Eq '[1-9][0-9]* (failed|skipped)' "${evidence_dir}/pytest.txt"; then
    die "真实 Docker 安全矩阵存在 failure 或 skip"
  fi

  log "清理本轮隔离 worktree"
  repo_git worktree remove --force "${smoke_dir}"
  [[ ! -e "${smoke_dir}" ]] || die "Smoke worktree 清理失败"

  mark_stage smoke-passed \
    "image=$(image_id_from_stage)|evidence=${evidence_dir}"
  log "真实 Docker 安全矩阵通过且未跳过"
  printf '下一步：sudo %s install-control-plane\n' "$0"
}

install_release_tree() {
  local partial_dir
  install -d -m 0755 /opt/nanobot-releases
  if [[ ! -e "${RELEASE_DIR}" ]]; then
    partial_dir="$(mktemp -d /opt/nanobot-releases/.nanobot-release.XXXXXX)"
    repo_git archive "${RELEASE}" | tar --extract --directory "${partial_dir}"
    printf '%s\n' "${RELEASE}" >"${partial_dir}/.nanobot-release"
    chown -R root:root "${partial_dir}"
    chmod 0644 "${partial_dir}/.nanobot-release"
    mv -- "${partial_dir}" "${RELEASE_DIR}"
  fi
  [[ -f "${RELEASE_DIR}/.nanobot-release" \
      && "$(cat "${RELEASE_DIR}/.nanobot-release")" == "${RELEASE}" ]] \
    || die "发布树缺少正确的版本标记：${RELEASE_DIR}"
  [[ -f "${RELEASE_DIR}/sandboxd/app.py" \
      && -f "${RELEASE_DIR}/requirements-sandboxd.lock" ]] \
    || die "发布树内容不完整：${RELEASE_DIR}"

  if [[ -L "${SERVER_RELEASE_LINK}" ]]; then
    [[ "$(readlink -f "${SERVER_RELEASE_LINK}")" == "${RELEASE_DIR}" ]] \
      || die "${SERVER_RELEASE_LINK} 指向其他发布版本"
  elif [[ -e "${SERVER_RELEASE_LINK}" ]]; then
    die "${SERVER_RELEASE_LINK} 已存在且不是预期符号链接"
  else
    ln -s "${RELEASE_DIR}" "${SERVER_RELEASE_LINK}"
  fi
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
    -r "${SERVER_RELEASE_LINK}/requirements-sandboxd.lock"
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

  if [[ -e /etc/nanobot/sandboxd.env && -L /etc/nanobot/sandboxd.env ]]; then
    die "sandboxd 环境文件不得是符号链接"
  fi
  cat >/etc/nanobot/sandboxd.env <<EOF
NANOBOT_SANDBOX_DATA_ROOT=${DATA_ROOT}
NANOBOT_SANDBOXD_SOCKET=/run/nanobot-sandboxd/sandboxd.sock
NANOBOT_SANDBOXD_TOKEN_FILE=/etc/nanobot/sandboxd.token
NANOBOT_SANDBOXD_CLIENT_TOKEN_FILE=/run/nanobot-sandboxd/client.token
NANOBOT_SANDBOXD_DOCKER_SOCKET=unix:///var/run/docker.sock
NANOBOT_SANDBOX_IMAGE=${SANDBOX_IMAGE}
NANOBOT_SANDBOX_IMAGE_ALLOWLIST=${image_id}
NANOBOT_SANDBOX_APPARMOR_PROFILE=nanobot-sandbox
NANOBOT_SANDBOX_UID=10001
NANOBOT_SANDBOX_GID=10001
NANOBOT_SANDBOX_GLOBAL_CONCURRENCY=2
NANOBOT_SANDBOX_DEFAULT_TIMEOUT=60
NANOBOT_SANDBOX_MAX_TIMEOUT=120
NANOBOT_SANDBOX_WORKSPACE_QUOTA_BYTES=${WORKSPACE_QUOTA_BYTES}
NANOBOT_SANDBOX_ASSET_MAX_BYTES=${ASSET_MAX_BYTES}
NANOBOT_SANDBOX_TOTAL_QUOTA_BYTES=${TOTAL_QUOTA_BYTES}
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

probe_sandboxd() {
  "${SANDBOXD_VENV}/bin/python" - <<'PY'
import socket
from pathlib import Path

socket_path = "/run/nanobot-sandboxd/sandboxd.sock"
token = Path("/etc/nanobot/sandboxd.token").read_bytes().strip()

for path in ("/v1/healthz", "/v1/readyz"):
    request = (
        b"GET " + path.encode("ascii") + b" HTTP/1.1\r\n"
        b"Host: sandboxd\r\n"
        b"Authorization: Bearer " + token + b"\r\n"
        b"Connection: close\r\n\r\n"
    )
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(socket_path)
    client.sendall(request)
    response = bytearray()
    while True:
        chunk = client.recv(65536)
        if not chunk:
            break
        response.extend(chunk)
    client.close()
    first_line = bytes(response).split(b"\r\n", 1)[0]
    if b" 200 " not in first_line:
        raise SystemExit(f"{path} failed: {first_line!r}")
    print(path, first_line.decode("ascii", "replace"))
PY
}

install_control_plane_command() {
  require_no_extra_args "$@"
  require_root
  load_config
  require_stage host-prepared
  require_stage image-built
  validate_release_in_repo
  image_id_from_stage >/dev/null

  log "安装只读发布树"
  install_release_tree
  log "安装独立 Python 3.11 与 sandboxd 依赖"
  install_sandboxd_python
  log "创建 sandboxd Token 与固定策略配置"
  install_sandboxd_credentials_and_env

  install -m 0644 \
    "${SERVER_RELEASE_LINK}/deploy/systemd/nanobot-sandboxd.tmpfiles.conf" \
    /etc/tmpfiles.d/nanobot-sandboxd.conf
  systemd-tmpfiles --create /etc/tmpfiles.d/nanobot-sandboxd.conf

  install -m 0644 \
    "${SERVER_RELEASE_LINK}/deploy/systemd/nanobot-sandboxd.service" \
    /etc/systemd/system/nanobot-sandboxd.service
  install -m 0644 \
    "${SERVER_RELEASE_LINK}/deploy/systemd/nanobot-sandbox-runtime-cleanup.service" \
    /etc/systemd/system/nanobot-sandbox-runtime-cleanup.service
  install -m 0644 \
    "${SERVER_RELEASE_LINK}/deploy/systemd/nanobot-sandbox-runtime-cleanup.timer" \
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
  systemctl enable nanobot-sandboxd.service
  systemctl restart nanobot-sandboxd.service
  systemctl is-active --quiet nanobot-sandboxd.service \
    || die "nanobot-sandboxd.service 未运行"

  probe_sandboxd
  [[ "$(stat -c '%a:%G' /run/nanobot-sandboxd/client.token)" \
      == "640:nanobot-sandboxd" ]] \
    || die "sandboxd client Token 权限错误"
  [[ "$(stat -c '%a:%G' /run/nanobot-sandboxd/sandboxd.sock)" \
      == "660:nanobot-sandboxd" ]] \
    || die "sandboxd UDS 权限错误"

  mark_stage control-plane-ready \
    "release=${RELEASE}|image=$(image_id_from_stage)"
  log "sandboxd healthz/readyz 均通过"
  printf '下一步：sudo %s deploy\n' "$0"
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
  mountpoint -q "${BACKUP_MOUNT}" \
    || die "BACKUP_MOUNT 不是独立挂载点：${BACKUP_MOUNT}"
  if [[ "${STORAGE_MODE}" == "loopback" ]]; then
    assert_backup_mount_independent_from_root
  fi
  [[ -f "${REPO_ROOT}/data/nanobot.db" && ! -L "${REPO_ROOT}/data/nanobot.db" ]] \
    || die "当前协调备份仅支持现有 SQLite 文件"
  local data_source
  local backup_source
  local data_disks=""
  local backup_disks=""
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

  log "停止 4 个固定 Nanobot 服务以执行协调备份"
  (cd "${REPO_ROOT}" && docker compose stop "${FIXED_SERVICES[@]}")
  if ! "${REPO_ROOT}/scripts/sandbox-coordinated-backup.sh" \
    --database "${REPO_ROOT}/data/nanobot.db" \
    --destination "${BACKUP_MOUNT}" \
    --data-root "${DATA_ROOT}" \
    --quiesced \
    --apply; then
    warn "协调备份失败，正在恢复原固定容器"
    restart_fixed_containers
    die "协调备份失败"
  fi
  restart_fixed_containers
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

set_setting() {
  local key="$1"
  local json_value="$2"
  local body
  body="$(jq -nc --argjson value "${json_value}" '{value: $value}')"
  admin_api PUT "/settings/${key}" "${body}" >/dev/null
}

validate_runtime_mount_boundaries() {
  local container
  local mounts
  for container in "${FIXED_CONTAINERS[@]}"; do
    mounts="$(docker inspect "${container}" \
      --format '{{range .Mounts}}{{println .Destination}}{{end}}')"
    if grep -Fxq /var/run/docker.sock <<<"${mounts}"; then
      die "${container} 错误挂载了 Docker Socket"
    fi
  done

  mounts="$(docker inspect nanobot-server \
    --format '{{range .Mounts}}{{println .Destination}}{{end}}')"
  grep -Fxq /run/nanobot-sandboxd <<<"${mounts}" \
    || die "nanobot-server 未挂载 sandboxd UDS 目录"
  if grep -Fxq "${DATA_ROOT}" <<<"${mounts}"; then
    die "nanobot-server 不得挂载 ${DATA_ROOT}"
  fi

  for container in \
    nanobot-session-summary-worker \
    nanobot-outbound-delivery-worker \
    nanobot-semantic-index-worker; do
    mounts="$(docker inspect "${container}" \
      --format '{{range .Mounts}}{{println .Destination}}{{end}}')"
    if grep -Fxq /run/nanobot-sandboxd <<<"${mounts}"; then
      die "${container} 不得访问 sandboxd UDS"
    fi
  done
}

force_feature_flags_off() {
  admin_api POST /settings/sandbox.sandboxd_socket/reset >/dev/null
  admin_api POST /settings/sandbox.sandboxd_token_file/reset >/dev/null
  admin_api POST /settings/sandbox.asset_token_secret/reset >/dev/null
  set_setting sandbox.enabled false
  set_setting sandbox.exec_enabled false
  set_setting sandbox.group_enabled false
  set_setting sandbox.workspace_quota_bytes "${WORKSPACE_QUOTA_BYTES}"
  set_setting sandbox.asset_max_bytes "${ASSET_MAX_BYTES}"
  set_setting sandbox.total_quota_bytes "${TOTAL_QUOTA_BYTES}"
  set_setting sandbox.disk_max_percent "${DISK_MAX_PERCENT}"
  set_setting sandbox.disk_min_free_bytes "${DISK_MIN_FREE_BYTES}"

  local status
  status="$(admin_api GET /sandbox/status)"
  jq -e '
    .feature.enabled == false and
    .feature.exec_enabled == false and
    .feature.group_enabled == false and
    .controller.health.ok == true and
    .controller.ready.ok == true
  ' <<<"${status}" >/dev/null \
    || die "Sandbox 开关关闭或 sandboxd ready 状态不符合预期"
}

deploy_command() {
  require_no_extra_args "$@"
  require_root
  load_config
  require_stage host-prepared
  require_stage image-built
  require_stage smoke-passed
  require_stage control-plane-ready
  validate_release_in_repo
  assert_smoke_current
  assert_control_plane_current
  probe_sandboxd
  check_build_disk_gate

  [[ "$(repo_git rev-parse HEAD)" == "${RELEASE}" ]] \
    || die "生产 checkout HEAD 与配置 RELEASE 不一致"
  [[ -z "$(repo_git status --porcelain)" ]] \
    || die "生产 checkout 不干净，拒绝部署"

  log "写入 feature-off 配置与 Asset Token Secret"
  configure_application_env_off

  ensure_state_dir
  local pre_file="${STATE_DIR}/nonfixed.pre"
  local post_file="${STATE_DIR}/nonfixed.post"
  capture_nonfixed_containers "${pre_file}"

  log "执行协调备份"
  coordinated_backup

  log "构建并重建 4 个 Nanobot 服务"
  run_as_deploy env \
    -u http_proxy -u https_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY \
    -u all_proxy -u ALL_PROXY \
    "${REPO_ROOT}/scripts/docker-build.sh" "${FIXED_SERVICES[@]}"

  wait_server_health || die "新版 Nanobot Runtime 健康检查失败"
  [[ "$(docker image inspect nanobot-runtime:latest \
      --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" \
      == "${RELEASE}" ]] \
    || die "运行镜像提交哈希不正确"
  docker exec nanobot-server python -c \
    'import core.sandbox, sandboxd; print("SANDBOX_CODE=OK")'

  validate_runtime_mount_boundaries
  force_feature_flags_off
  capture_nonfixed_containers "${post_file}"
  if ! diff -u "${pre_file}" "${post_file}"; then
    die "非 Nanobot 固定容器清单发生变化"
  fi

  mark_stage runtime-deployed "release=${RELEASE}|flags=off"
  log "Runtime 部署完成；三个 Sandbox 开关仍为关闭"
  printf '下一步：sudo %s provision-owner\n' "$0"
}

provision_workspace() {
  docker exec -i \
    -e SANDBOX_OWNER_ID="${SANDBOX_OWNER_ID}" \
    -e SANDBOX_PLATFORM="${SANDBOX_PLATFORM}" \
    nanobot-server \
    python - <<'PY'
import os
import secrets

from core.database import SessionLocal
from core.sandbox.client import HttpSandboxdBackend
from core.sandbox.identity import Principal
from core.sandbox.tool_service import workspace_policy_from_settings
from core.sandbox.workspace_service import WorkspaceService

db = SessionLocal()
backend = HttpSandboxdBackend(
    socket_path="/run/nanobot-sandboxd/sandboxd.sock",
    token_file="/run/nanobot-sandboxd/client.token",
)
try:
    principal = Principal(
        platform=os.environ["SANDBOX_PLATFORM"],
        owner_type="user",
        owner_id=os.environ["SANDBOX_OWNER_ID"],
    )
    service = WorkspaceService(db, policy=workspace_policy_from_settings(db))
    workspace = service.ensure_default(principal)
    workspace_id = str(workspace.id)
    backend.ensure_workspace(
        workspace_id,
        request_id="admin-provision-" + secrets.token_hex(8),
    )
    db.commit()
    print(workspace_id)
except Exception:
    db.rollback()
    raise
finally:
    backend.close()
    db.close()
PY
}

control_plane_exec_smoke() {
  local workspace_id="$1"
  docker exec -i \
    -e SANDBOX_WORKSPACE_ID="${workspace_id}" \
    -e SANDBOX_WORKSPACE_QUOTA_BYTES="${WORKSPACE_QUOTA_BYTES}" \
    nanobot-server \
    python - <<'PY'
import os
import secrets

from core.sandbox.client import HttpSandboxdBackend

backend = HttpSandboxdBackend(
    socket_path="/run/nanobot-sandboxd/sandboxd.sock",
    token_file="/run/nanobot-sandboxd/client.token",
)
suffix = secrets.token_hex(8)
try:
    response = backend.run({
        "request_id": "sbxreq_admin_" + suffix,
        "run_id": "sbxrun_admin_" + suffix,
        "workspace_id": os.environ["SANDBOX_WORKSPACE_ID"],
        "command": "printf SANDBOX_CONTROL_PLANE_OK",
        "cwd": "",
        "timeout_seconds": 20,
        "quota_bytes": int(os.environ["SANDBOX_WORKSPACE_QUOTA_BYTES"]),
    })
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    if data.get("termination_reason") != "completed":
        raise RuntimeError("sandboxd control-plane smoke did not complete")
    if data.get("stdout") != "SANDBOX_CONTROL_PLANE_OK":
        raise RuntimeError("sandboxd control-plane smoke output mismatch")
    print("SANDBOX_CONTROL_PLANE_SMOKE=OK")
finally:
    backend.close()
PY
}

record_project_mapping() {
  local workspace_id="$1"
  local existing_line=""
  local existing_project=""

  if [[ -e "${PROJECT_MAP}" \
      && ( ! -f "${PROJECT_MAP}" || -L "${PROJECT_MAP}" ) ]]; then
    die "project ID 映射路径不是普通文件"
  fi
  if [[ -f "${PROJECT_MAP}" ]]; then
    existing_line="$(awk -F '\t' -v owner="${SANDBOX_OWNER_ID}" \
      '$1 == owner {print; exit}' "${PROJECT_MAP}")"
    existing_project="$(awk -F '\t' -v project="${PROJECT_ID}" \
      '$3 == project {print $2; exit}' "${PROJECT_MAP}")"
    [[ -z "${existing_project}" || "${existing_project}" == "${workspace_id}" ]] \
      || die "PROJECT_ID 已被其他 Workspace 使用"
    if [[ -n "${existing_line}" ]]; then
      [[ "${existing_line}" == "${SANDBOX_OWNER_ID}"$'\t'"${workspace_id}"$'\t'"${PROJECT_ID}"$'\t'"${WORKSPACE_QUOTA_BYTES}" ]] \
        || die "owner 已存在但 Workspace/project/quota 映射不一致"
      return 0
    fi
  fi

  printf '%s\t%s\t%s\t%s\n' \
    "${SANDBOX_OWNER_ID}" \
    "${workspace_id}" \
    "${PROJECT_ID}" \
    "${WORKSPACE_QUOTA_BYTES}" >>"${PROJECT_MAP}"
  chown root:root "${PROJECT_MAP}"
  chmod 0600 "${PROJECT_MAP}"
}

provision_owner_command() {
  require_no_extra_args "$@"
  require_root
  load_config
  require_stage runtime-deployed
  require_stage control-plane-ready
  require_stage smoke-passed
  assert_runtime_current
  assert_control_plane_current
  assert_smoke_current

  local status
  local workspace_id
  status="$(admin_api GET /sandbox/status)"
  jq -e '
    .feature.enabled == false and
    .feature.exec_enabled == false and
    .feature.group_enabled == false and
    .controller.ready.ok == true
  ' <<<"${status}" >/dev/null \
    || die "Workspace 预建要求三个开关关闭且 sandboxd ready"

  workspace_id="$(provision_workspace | tail -n 1)"
  [[ "${workspace_id}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] \
    || die "Workspace ID 格式无效"

  log "预览 Workspace project quota"
  "${REPO_ROOT}/scripts/assign-sandbox-project-quota.sh" \
    --workspace-id "${workspace_id}" \
    --project-id "${PROJECT_ID}" \
    --quota-bytes "${WORKSPACE_QUOTA_BYTES}"
  log "应用 Workspace project quota"
  "${REPO_ROOT}/scripts/assign-sandbox-project-quota.sh" \
    --workspace-id "${workspace_id}" \
    --project-id "${PROJECT_ID}" \
    --quota-bytes "${WORKSPACE_QUOTA_BYTES}" \
    --quiesced \
    --apply
  log "通过实际 sandboxd UDS 运行受限容器 Smoke"
  control_plane_exec_smoke "${workspace_id}"
  if docker ps \
    --filter 'label=com.nanobot.sandbox=true' \
    --filter 'label=com.nanobot.managed-by=sandboxd' \
    --format '{{.Names}}' | grep -q .; then
    die "控制面 Smoke 后仍有活动 Sandbox 容器"
  fi
  record_project_mapping "${workspace_id}"

  mark_stage owner-provisioned \
    "owner=${SANDBOX_OWNER_ID}|workspace=${workspace_id}|project=${PROJECT_ID}|quota=${WORKSPACE_QUOTA_BYTES}"
  log "首期 owner Workspace 与 project quota 已配置"
  printf '下一步：sudo %s enable-workspace\n' "$0"
}

apply_tool_override() {
  local tool="$1"
  local reason="$2"
  local body
  body="$(jq -nc \
    --arg owner "${SANDBOX_OWNER_ID}" \
    --arg reason "${reason}" \
    '{scope_type:"user",scope_id:$owner,enabled:true,reason:$reason}')"
  admin_api PUT "/tools/${tool}/override" "${body}" >/dev/null
}

disable_tool_override() {
  local tool="$1"
  local reason="$2"
  local body
  body="$(jq -nc \
    --arg owner "${SANDBOX_OWNER_ID}" \
    --arg reason "${reason}" \
    '{scope_type:"user",scope_id:$owner,enabled:false,reason:$reason}')"
  admin_api PUT "/tools/${tool}/override" "${body}" >/dev/null
}

close_feature_flags() {
  local reason="$1"
  local body
  body="$(jq -nc --arg reason "${reason}" '{reason:$reason}')"
  if ! admin_api POST /sandbox/kill-switch "${body}" >/dev/null; then
    warn "管理端 kill switch 调用失败，请立即人工关闭 sandbox.enabled 与 sandbox.exec_enabled"
  fi
}

assert_no_foreign_sandbox_overrides() {
  local allowed_csv="$1"
  docker exec \
    -e SANDBOX_OWNER_ID="${SANDBOX_OWNER_ID}" \
    -e SANDBOX_ALLOWED_TOOLS="${allowed_csv}" \
    nanobot-server \
    python -c '
import os
from core.database import SessionLocal, ToolOverride
from core.tool_registry import SANDBOX_TOOL_NAMES

owner = os.environ["SANDBOX_OWNER_ID"]
allowed = {item for item in os.environ["SANDBOX_ALLOWED_TOOLS"].split(",") if item}
db = SessionLocal()
try:
    rows = (
        db.query(ToolOverride)
        .filter(
            ToolOverride.tool_name.in_(sorted(SANDBOX_TOOL_NAMES)),
            ToolOverride.enabled == 1,
        )
        .all()
    )
    invalid = [
        row for row in rows
        if row.scope_type != "user"
        or row.scope_id != owner
        or row.tool_name not in allowed
    ]
    if invalid:
        raise SystemExit(
            f"发现 {len(invalid)} 条非首期 owner 或超阶段 Sandbox override"
        )
    print(f"SANDBOX_OVERRIDE_AUDIT=OK count={len(rows)}")
finally:
    db.close()
'
}

assert_controller_ready() {
  local status
  status="$(admin_api GET /sandbox/status)"
  jq -e '
    .controller.health.ok == true and
    .controller.ready.ok == true and
    .controller.ready.docker == true and
    .controller.ready.apparmor_profile == "nanobot-sandbox"
  ' <<<"${status}" >/dev/null \
    || die "sandboxd controller 未 ready"
}

owner_tool_status() {
  local owner_uri
  owner_uri="$(printf '%s' "${SANDBOX_OWNER_ID}" | jq -sRr @uri)"
  admin_api GET \
    "/tools?chat_type=private_superuser&user_id=${owner_uri}&platform=${SANDBOX_PLATFORM}&runtime_preset=full"
}

assert_owner_tool_policy() {
  local phase="$1"
  local payload
  payload="$(owner_tool_status)"
  case "${phase}" in
    workspace)
      jq -e '
        def enabled($name):
          ([.tools[] | select(.name == $name)] | first) as $tool
          | $tool.configured_enabled == true
            and $tool.runtime_effective == true
            and $tool.registered == true;
        def disabled($name):
          ([.tools[] | select(.name == $name)] | first) as $tool
          | $tool.configured_enabled == false
            and $tool.runtime_effective == false;
        enabled("workspace_list")
        and enabled("workspace_read")
        and enabled("workspace_search")
        and enabled("workspace_write")
        and disabled("asset_import")
        and disabled("asset_publish")
        and disabled("sandbox_exec")
      ' <<<"${payload}" >/dev/null
      ;;
    assets)
      jq -e '
        def enabled($name):
          ([.tools[] | select(.name == $name)] | first) as $tool
          | $tool.configured_enabled == true
            and $tool.runtime_effective == true
            and $tool.registered == true;
        def disabled($name):
          ([.tools[] | select(.name == $name)] | first) as $tool
          | $tool.configured_enabled == false
            and $tool.runtime_effective == false;
        enabled("workspace_list")
        and enabled("workspace_read")
        and enabled("workspace_search")
        and enabled("workspace_write")
        and enabled("asset_import")
        and enabled("asset_publish")
        and disabled("sandbox_exec")
      ' <<<"${payload}" >/dev/null
      ;;
    exec)
      jq -e '
        def enabled($name):
          ([.tools[] | select(.name == $name)] | first) as $tool
          | $tool.configured_enabled == true
            and $tool.runtime_effective == true
            and $tool.registered == true;
        enabled("workspace_list")
        and enabled("workspace_read")
        and enabled("workspace_search")
        and enabled("workspace_write")
        and enabled("asset_import")
        and enabled("asset_publish")
        and enabled("sandbox_exec")
      ' <<<"${payload}" >/dev/null
      ;;
    *)
      die "未知工具策略阶段：${phase}"
      ;;
  esac
}

enable_workspace_command() {
  require_no_extra_args "$@"
  require_root
  load_config
  require_stage owner-provisioned
  require_stage smoke-passed
  assert_owner_current
  assert_runtime_current
  assert_smoke_current
  assert_controller_ready

  set_setting sandbox.enabled false
  set_setting sandbox.exec_enabled false
  set_setting sandbox.group_enabled false
  local tool
  for tool in "${WORKSPACE_TOOLS[@]}"; do
    apply_tool_override "${tool}" "Sandbox 首期私聊 Workspace 灰度"
  done
  for tool in "${ASSET_TOOLS[@]}" sandbox_exec; do
    disable_tool_override "${tool}" "Workspace 阶段保持关闭"
  done
  assert_no_foreign_sandbox_overrides \
    "workspace_list,workspace_read,workspace_search,workspace_write"
  set_setting sandbox.exec_enabled false
  set_setting sandbox.group_enabled false
  set_setting sandbox.enabled true

  local status
  status="$(admin_api GET /sandbox/status)"
  if ! jq -e '
    .feature.enabled == true and
    .feature.exec_enabled == false and
    .feature.group_enabled == false and
    .controller.ready.ok == true
  ' <<<"${status}" >/dev/null \
    || ! assert_owner_tool_policy workspace; then
    close_feature_flags "Workspace 灰度验证失败，自动关闭"
    die "Workspace 开关或工具实际策略不符合预期"
  fi
  mark_stage workspace-enabled "owner=${SANDBOX_OWNER_ID}"
  log "已只为首期 owner 开放 4 个 Workspace 工具；执行与群聊仍关闭"
}

enable_assets_command() {
  require_no_extra_args "$@"
  require_root
  load_config
  require_stage workspace-enabled
  assert_owner_current
  assert_runtime_current
  assert_controller_ready

  set_setting sandbox.enabled false
  set_setting sandbox.exec_enabled false
  set_setting sandbox.group_enabled false
  local tool
  for tool in "${ASSET_TOOLS[@]}"; do
    apply_tool_override "${tool}" "Sandbox 首期私聊 Asset 灰度"
  done
  disable_tool_override sandbox_exec "Asset 阶段保持执行关闭"
  assert_no_foreign_sandbox_overrides \
    "workspace_list,workspace_read,workspace_search,workspace_write,asset_import,asset_publish"
  set_setting sandbox.enabled true
  set_setting sandbox.exec_enabled false
  set_setting sandbox.group_enabled false
  local status
  status="$(admin_api GET /sandbox/status)"
  if ! jq -e '
    .feature.enabled == true and
    .feature.exec_enabled == false and
    .feature.group_enabled == false and
    .controller.ready.ok == true
  ' <<<"${status}" >/dev/null \
    || ! assert_owner_tool_policy assets; then
    close_feature_flags "Asset 灰度验证失败，自动关闭"
    die "Asset 开关或工具实际策略不符合预期"
  fi
  mark_stage assets-enabled "owner=${SANDBOX_OWNER_ID}"
  log "已追加开放 Asset 工具；执行与群聊状态未放宽"
}

enable_exec_command() {
  require_no_extra_args "$@"
  require_root
  load_config
  require_stage assets-enabled
  require_stage smoke-passed
  assert_owner_current
  assert_runtime_current
  assert_smoke_current
  assert_controller_ready

  set_setting sandbox.enabled false
  set_setting sandbox.exec_enabled false
  set_setting sandbox.group_enabled false
  disable_tool_override sandbox_exec "Exec 开启前置关闭"
  set_setting sandbox.enabled true
  set_setting sandbox.exec_enabled false
  set_setting sandbox.group_enabled false
  apply_tool_override sandbox_exec "Sandbox 首期私聊 Exec 灰度"
  assert_no_foreign_sandbox_overrides \
    "workspace_list,workspace_read,workspace_search,workspace_write,asset_import,asset_publish,sandbox_exec"
  set_setting sandbox.exec_enabled true

  local status
  status="$(admin_api GET /sandbox/status)"
  if ! jq -e '
    .feature.enabled == true and
    .feature.exec_enabled == true and
    .feature.group_enabled == false and
    .controller.ready.ok == true
  ' <<<"${status}" >/dev/null \
    || ! assert_owner_tool_policy exec; then
    close_feature_flags "Exec 灰度验证失败，自动关闭"
    die "Exec 开关或工具实际策略不符合预期"
  fi
  mark_stage exec-enabled "owner=${SANDBOX_OWNER_ID}"
  log "已为首期 owner 开放 sandbox_exec；群聊仍关闭"
}

kill_switch_command() {
  require_root
  (( $# <= 2 )) || die "kill-switch 最多接受一个 reason 参数"
  local reason="${2:-管理员紧急关闭 Sandbox}"
  local body
  body="$(jq -nc --arg reason "${reason}" '{reason:$reason}')"
  admin_api POST /sandbox/kill-switch "${body}" | jq
}

disable_owner_command() {
  require_no_extra_args "$@"
  require_root
  load_config
  kill_switch_command kill-switch "管理员关闭首期 owner Sandbox"

  local owner_uri
  local tool
  owner_uri="$(printf '%s' "${SANDBOX_OWNER_ID}" | jq -sRr @uri)"
  for tool in "${ALL_SANDBOX_TOOLS[@]}"; do
    if ! admin_api DELETE \
      "/tools/${tool}/override?scope_type=user&scope_id=${owner_uri}" \
      >/dev/null 2>&1; then
      warn "${tool} override 不存在或删除失败，请在管理端复核"
    fi
  done
  log "总开关已关闭，并已尝试删除首期 owner 的 7 个 override"
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
  if docker ps \
    --filter 'label=com.nanobot.sandbox=true' \
    --filter 'label=com.nanobot.managed-by=sandboxd' \
    --format '{{.Names}}' | grep -q .; then
    die "仍有活动 Sandbox 容器，拒绝 runtime 清理"
  fi
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
  printf '仓库：%s\n' "${REPO_ROOT}"
  printf '当前 HEAD：%s\n' "$(repo_git rev-parse HEAD 2>/dev/null || printf unknown)"

  if [[ -f "${CONFIG_FILE}" && ! -L "${CONFIG_FILE}" ]]; then
    load_config
    printf '安装配置：已配置\n'
    printf 'RELEASE：%s\n' "${RELEASE}"
    printf 'VERSION：%s\n' "${VERSION}"
    printf '存储模式：%s\n' "${STORAGE_MODE}"
    if [[ "${STORAGE_MODE}" == "loopback" ]]; then
      printf 'XFS 镜像：%s GiB（固定路径）\n' \
        "$((DATA_IMAGE_SIZE_BYTES / 1024 / 1024 / 1024))"
    fi
    printf 'OWNER：已配置（不回显）\n'
  else
    printf '安装配置：未配置\n'
  fi

  printf 'AppArmor 内核：%s\n' \
    "$(cat /sys/module/apparmor/parameters/enabled 2>/dev/null || printf unknown)"
  if grep -q '^nanobot-sandbox ' /sys/kernel/security/apparmor/profiles 2>/dev/null; then
    printf 'nanobot-sandbox profile：LOADED\n'
  else
    printf 'nanobot-sandbox profile：NOT LOADED\n'
  fi
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
      | grep '^nanobot-sandbox-python:' || true
    if docker inspect nanobot-server >/dev/null 2>&1; then
      printf '运行 Runtime 提交：%s\n' "$(docker image inspect nanobot-runtime:latest \
        --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true)"
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
    control-plane-ready \
    runtime-deployed \
    owner-provisioned \
    workspace-enabled \
    assets-enabled \
    exec-enabled; do
    if stage_exists "${stage}"; then
      printf '  %-24s DONE\n' "${stage}"
    else
      printf '  %-24s PENDING\n' "${stage}"
    fi
  done
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
    provision-owner)
      provision_owner_command "$@"
      ;;
    enable-workspace)
      enable_workspace_command "$@"
      ;;
    enable-assets)
      enable_assets_command "$@"
      ;;
    enable-exec)
      enable_exec_command "$@"
      ;;
    kill-switch)
      kill_switch_command "$@"
      ;;
    disable-owner)
      disable_owner_command "$@"
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
