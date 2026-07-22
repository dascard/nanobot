#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
用法：
  scripts/sandbox-coordinated-backup.sh \
    --database <SQLite 文件> --destination <备份目标目录> \
    [--backup-mode independent|local_same_disk] \
    [--risk-marker single_disk_logical_rollback_only] \
    [--max-bytes 17179869184] \
    [--system-min-free-bytes 64424509440] \
    [--data-root /srv/nanobot] --quiesced --apply

在所有 Nanobot 固定服务和临时 Sandbox 容器均已停止后，协调备份数据库、
workspaces 与 assets。runtime 和单次输入 staging 不进入备份。默认不写入；
必须同时传入 --quiesced 与 --apply。

independent 要求独立挂载和存储来源；local_same_disk 只允许固定的
16 GiB XFS loopback，并要求显式风险标记。脚本不会停止服务、删除源数据
或覆盖已有备份。
EOF
}

die() {
  echo "Sandbox 协调备份失败：$*" >&2
  exit 1
}

database=""
destination=""
data_root="${NANOBOT_SANDBOX_DATA_ROOT:-/srv/nanobot}"
backup_mode="independent"
risk_marker="none"
max_bytes="17179869184"
system_min_free_bytes="64424509440"
quiesced=false
apply=false
readonly local_same_disk_risk_marker="single_disk_logical_rollback_only"
readonly local_same_disk_image="/var/lib/nanobot-sandbox-storage/data.xfs"
readonly local_same_disk_image_bytes="17179869184"

while (( $# )); do
  case "$1" in
    --database)
      [[ $# -ge 2 ]] || die "--database 缺少参数"
      database="$2"
      shift 2
      ;;
    --destination)
      [[ $# -ge 2 ]] || die "--destination 缺少参数"
      destination="$2"
      shift 2
      ;;
    --data-root)
      [[ $# -ge 2 ]] || die "--data-root 缺少参数"
      data_root="$2"
      shift 2
      ;;
    --backup-mode)
      [[ $# -ge 2 ]] || die "--backup-mode 缺少参数"
      backup_mode="$2"
      shift 2
      ;;
    --risk-marker)
      [[ $# -ge 2 ]] || die "--risk-marker 缺少参数"
      risk_marker="$2"
      shift 2
      ;;
    --max-bytes)
      [[ $# -ge 2 ]] || die "--max-bytes 缺少参数"
      max_bytes="$2"
      shift 2
      ;;
    --system-min-free-bytes)
      [[ $# -ge 2 ]] || die "--system-min-free-bytes 缺少参数"
      system_min_free_bytes="$2"
      shift 2
      ;;
    --quiesced)
      quiesced=true
      shift
      ;;
    --apply)
      apply=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "未知参数：$1"
      ;;
  esac
done

[[ "${database}" == /* ]] || die "必须提供 SQLite 数据库绝对路径"
[[ "${destination}" == /* ]] || die "必须提供备份目标绝对路径"
[[ -f "${database}" && ! -L "${database}" ]] \
  || die "数据库必须是现有普通文件且不能是符号链接"
[[ -d "${destination}" && ! -L "${destination}" ]] \
  || die "备份目标必须是现有目录且不能是符号链接"
[[ "${max_bytes}" =~ ^[0-9]+$ ]] \
  || die "--max-bytes 必须是整数"
(( max_bytes >= 1024 * 1024 * 1024 \
    && max_bytes <= local_same_disk_image_bytes )) \
  || die "单次协调备份容量上限必须位于 1..16 GiB"
[[ "${system_min_free_bytes}" =~ ^[0-9]+$ ]] \
  || die "--system-min-free-bytes 必须是整数"
(( system_min_free_bytes >= 60 * 1024 * 1024 * 1024 )) \
  || die "根文件系统最低保留空间不得低于 60 GiB"
case "${backup_mode}" in
  independent)
    [[ "${risk_marker}" == "none" ]] \
      || die "independent 模式不得携带同盘风险标记"
    ;;
  local_same_disk)
    [[ "${risk_marker}" == "${local_same_disk_risk_marker}" ]] \
      || die "local_same_disk 缺少固定风险确认标记"
    ;;
  *)
    die "--backup-mode 必须是 independent 或 local_same_disk"
    ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
"${script_dir}/check-sandbox-data-disk.sh" "${data_root}"

resolved_data_root="$(realpath -e -- "${data_root}")"
resolved_database="$(realpath -e -- "${database}")"
resolved_destination="$(realpath -e -- "${destination}")"
[[ "${resolved_destination}" != "${resolved_data_root}"/* ]] \
  || die "备份目标不能位于 Sandbox 数据根目录内"

data_source="$(findmnt -n -T "${resolved_data_root}" -o SOURCE)"
destination_target="$(findmnt -n -T "${resolved_destination}" -o TARGET)"
destination_source="$(findmnt -n -T "${resolved_destination}" -o SOURCE)"
case "${resolved_destination}" in
  /)
    die "备份目标不能直接使用根目录"
    ;;
  "${repo_root}"|"${repo_root}"/*)
    die "备份目标不能位于生产仓库内"
    ;;
  /var/lib/nanobot-sandbox-storage|/var/lib/nanobot-sandbox-storage/*)
    die "备份目标不能位于 loopback 镜像目录内"
    ;;
esac

case "${backup_mode}" in
  independent)
    [[ "${resolved_destination}" == "${destination_target}" ]] \
      || die "independent 备份目标本身必须是独立挂载点"
    [[ "${destination_source}" != "${data_source}" ]] \
      || die "independent 备份目标与 Sandbox 数据盘不能使用同一存储来源"
    ;;
  local_same_disk)
    root_major_minor="$(findmnt -n -T / -o MAJ:MIN)"
    destination_major_minor="$(findmnt -n -T "${resolved_destination}" -o MAJ:MIN)"
    [[ -n "${root_major_minor}" \
        && "${destination_major_minor}" == "${root_major_minor}" ]] \
      || die "local_same_disk 备份目标必须位于根文件系统"
    data_device="$(readlink -f "${data_source}")"
    [[ -b "${data_device}" && "$(lsblk -dn -o TYPE "${data_device}")" == "loop" ]] \
      || die "local_same_disk 仅允许 loopback Sandbox 数据文件系统"
    backing_file="$(losetup --noheadings --output BACK-FILE "${data_device}" \
      | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -n "${backing_file}" \
        && "$(realpath -e "${backing_file}")" == "${local_same_disk_image}" ]] \
      || die "local_same_disk loopback backing file 与固定路径不一致"
    [[ "$(stat -c '%s' "${local_same_disk_image}")" \
        == "${local_same_disk_image_bytes}" ]] \
      || die "local_same_disk loopback 镜像必须为 16 GiB"
    ;;
esac

for required_dir in workspaces assets; do
  [[ -d "${resolved_data_root}/${required_dir}" \
      && ! -L "${resolved_data_root}/${required_dir}" ]] \
    || die "缺少受控数据目录：${required_dir}"
done

command -v docker >/dev/null 2>&1 || die "无法确认容器已停止"
while IFS= read -r container_name; do
  if [[ "${container_name}" == nanobot-sbx-* ]]; then
    die "仍有活动 Sandbox 容器 ${container_name}"
  fi
done < <(
  docker ps \
    --filter 'label=com.nanobot.sandbox=true' \
    --filter 'label=com.nanobot.managed-by=sandboxd' \
    --format '{{.Names}}'
)

cd "${repo_root}"
if ! running_services="$(docker compose ps --status running -q)"; then
  die "无法确认 Docker Compose 固定服务状态"
fi
[[ -z "${running_services}" ]] \
  || die "仍有 Nanobot Docker Compose 固定服务运行，拒绝生成非协调备份"

measure_archive_bytes() {
  local totals
  local measured_bytes

  if ! totals="$(LC_ALL=C tar "$@" 2>&1)"; then
    die "无法计算协调备份归档容量"
  fi
  measured_bytes="$(awk '/^Total bytes written:/ {print $4}' <<<"${totals}")"
  [[ "${measured_bytes}" =~ ^[0-9]+$ ]] \
    || die "无法解析协调备份归档容量"
  printf '%s\n' "${measured_bytes}"
}

database_backup_bytes="$(python - "${resolved_database}" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
finally:
    connection.close()
print(page_size * page_count)
PY
)"
[[ "${database_backup_bytes}" =~ ^[0-9]+$ ]] \
  || die "无法计算 SQLite 备份容量"
workspaces_archive_bytes="$(measure_archive_bytes \
  --create --file /dev/null --totals \
  --directory "${resolved_data_root}" --one-file-system \
  --numeric-owner --acls --xattrs -- workspaces)"
assets_archive_bytes="$(measure_archive_bytes \
  --create --file /dev/null --totals \
  --directory "${resolved_data_root}" --one-file-system \
  --numeric-owner --acls --xattrs \
  --exclude='assets/sha256/.tmp' -- assets)"
backup_required_bytes=$((database_backup_bytes \
  + workspaces_archive_bytes + assets_archive_bytes + 1024 * 1024))
(( backup_required_bytes <= max_bytes )) \
  || die "预计协调备份超过配置的单次容量上限"
destination_free_bytes="$(df -B1 --output=avail "${resolved_destination}" \
  | tail -n 1 | tr -d ' ')"
(( destination_free_bytes >= backup_required_bytes )) \
  || die "备份目标可用空间不足"
if [[ "${backup_mode}" == "local_same_disk" ]]; then
  root_free_bytes="$(df -B1 --output=avail / | tail -n 1 | tr -d ' ')"
  (( root_free_bytes >= system_min_free_bytes + backup_required_bytes )) \
    || die "同盘备份完成后将跌破 60 GiB 根分区水位"
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_name="nanobot-sandbox-${timestamp}"
partial_dir="${resolved_destination}/.${backup_name}.partial"
final_dir="${resolved_destination}/${backup_name}"

echo "协调备份将写入：${final_dir}"
echo "包含：SQLite、workspaces、assets"
echo "排除：runtime、资产临时上传目录、单次 input staging"
echo "备份模式：${backup_mode}"
echo "单次容量上限：${max_bytes} bytes"
if [[ "${backup_mode}" == "local_same_disk" ]]; then
  echo "风险标记：${risk_marker}（仅逻辑回滚，不提供硬盘灾备）"
fi
if [[ "${apply}" != "true" ]]; then
  echo "当前为预览；未传入 --apply，不会写入备份。"
  exit 0
fi
[[ "${quiesced}" == "true" ]] \
  || die "实际备份必须显式传入 --quiesced"
(( EUID == 0 )) || die "实际备份必须以 root 运行"
[[ ! -e "${partial_dir}" && ! -e "${final_dir}" ]] \
  || die "同名备份或未完成目录已存在，拒绝覆盖"

install -d -m 0700 -- "${partial_dir}"

python - "${resolved_database}" "${partial_dir}/nanobot.db" <<'PY'
import os
import sqlite3
import sys

source_path, target_path = sys.argv[1:]
source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
target = sqlite3.connect(target_path)
try:
    source.backup(target)
    result = target.execute("PRAGMA quick_check").fetchone()
    if result != ("ok",):
        raise RuntimeError("SQLite 备份 quick_check 未通过")
    target.commit()
finally:
    target.close()
    source.close()
with open(target_path, "rb+") as backup_file:
    os.fsync(backup_file.fileno())
PY

tar --create --file "${partial_dir}/workspaces.tar" \
  --directory "${resolved_data_root}" --one-file-system \
  --numeric-owner --acls --xattrs -- workspaces
tar --create --file "${partial_dir}/assets.tar" \
  --directory "${resolved_data_root}" --one-file-system \
  --numeric-owner --acls --xattrs \
  --exclude='assets/sha256/.tmp' -- assets

{
  echo "created_at=${timestamp}"
  echo "hostname=$(hostname)"
  echo "data_root=${resolved_data_root}"
  echo "data_source=${data_source}"
  echo "database=${resolved_database}"
  echo "backup_mode=${backup_mode}"
  echo "backup_risk_marker=${risk_marker}"
  echo "backup_max_bytes=${max_bytes}"
  echo "system_min_free_bytes=${system_min_free_bytes}"
  echo "runtime_included=false"
  echo "input_staging_included=false"
} >"${partial_dir}/manifest.txt"

(
  cd "${partial_dir}"
  sha256sum nanobot.db workspaces.tar assets.tar manifest.txt \
    >manifest.sha256
)
actual_backup_bytes="$(du -sb -- "${partial_dir}" | cut -f1)"
[[ "${actual_backup_bytes}" =~ ^[0-9]+$ \
    && "${actual_backup_bytes}" -le "${max_bytes}" ]] \
  || die "实际协调备份超过配置的单次容量上限"
if [[ "${backup_mode}" == "local_same_disk" ]]; then
  root_free_bytes="$(df -B1 --output=avail / | tail -n 1 | tr -d ' ')"
  (( root_free_bytes >= system_min_free_bytes )) \
    || die "协调备份写入后根分区可用空间低于 60 GiB"
fi
sync -f "${partial_dir}"
mv -- "${partial_dir}" "${final_dir}"
sync -f "${resolved_destination}"

echo "Sandbox 协调备份完成：${final_dir}"
echo "恢复前必须先校验 manifest.sha256，并按恢复手册演练。"
