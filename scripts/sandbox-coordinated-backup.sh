#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
用法：
  scripts/sandbox-coordinated-backup.sh \
    --database <SQLite 文件> --destination <独立备份挂载点> \
    [--data-root /srv/nanobot] --quiesced --apply

在所有 Nanobot 固定服务和临时 Sandbox 容器均已停止后，协调备份数据库、
workspaces 与 assets。runtime 和单次输入 staging 不进入备份。默认不写入；
必须同时传入 --quiesced 与 --apply。

脚本不会停止服务、删除源数据或覆盖已有备份。
EOF
}

die() {
  echo "Sandbox 协调备份失败：$*" >&2
  exit 1
}

database=""
destination=""
data_root="${NANOBOT_SANDBOX_DATA_ROOT:-/srv/nanobot}"
quiesced=false
apply=false

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
[[ "${resolved_destination}" == "${destination_target}" ]] \
  || die "备份目标本身必须是独立挂载点"
[[ "${destination_source}" != "${data_source}" ]] \
  || die "备份目标与 Sandbox 数据盘不能使用同一存储来源"

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

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_name="nanobot-sandbox-${timestamp}"
partial_dir="${resolved_destination}/.${backup_name}.partial"
final_dir="${resolved_destination}/${backup_name}"

echo "协调备份将写入：${final_dir}"
echo "包含：SQLite、workspaces、assets"
echo "排除：runtime、资产临时上传目录、单次 input staging"
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
  echo "runtime_included=false"
  echo "input_staging_included=false"
} >"${partial_dir}/manifest.txt"

(
  cd "${partial_dir}"
  sha256sum nanobot.db workspaces.tar assets.tar manifest.txt \
    >manifest.sha256
)
sync -f "${partial_dir}"
mv -- "${partial_dir}" "${final_dir}"
sync -f "${resolved_destination}"

echo "Sandbox 协调备份完成：${final_dir}"
echo "恢复前必须先校验 manifest.sha256，并按恢复手册演练。"
