#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_root="${NANOBOT_PRODUCTION_ROOT:-${repo_root}}"
if [[ "${runtime_root}" != /* || ! -d "${runtime_root}" || -L "${runtime_root}" ]]; then
  echo "NANOBOT_PRODUCTION_ROOT 必须是现有普通目录的绝对路径。" >&2
  exit 2
fi
runtime_paths=(
  "${runtime_root}/data"
  "${runtime_root}/models"
  "${runtime_root}/sentinel"
)

runtime_uid="${NANOBOT_RUNTIME_UID:-10001}"
runtime_gid="${NANOBOT_RUNTIME_GID:-10001}"
runtime_host_read_gid="${NANOBOT_RUNTIME_HOST_READ_GID:-${runtime_gid}}"
fix_existing=false
if [[ "${1:-}" == "--fix-existing" ]]; then
  fix_existing=true
  shift
fi
if [[ "$#" -ne 0 ]]; then
  echo "用法：scripts/prepare-runtime-directories.sh [--fix-existing]" >&2
  exit 2
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "准备生产 bind mount 权限需要 root；请通过 sudo 显式执行本脚本。" >&2
  exit 2
fi
if [[ ! "${runtime_uid}" =~ ^[0-9]+$ \
    || ! "${runtime_gid}" =~ ^[0-9]+$ \
    || ! "${runtime_host_read_gid}" =~ ^[0-9]+$ ]]; then
  echo "NANOBOT_RUNTIME_UID/GID/HOST_READ_GID 必须是数字。" >&2
  exit 2
fi

install -d -m 2750 -o "${runtime_uid}" -g "${runtime_host_read_gid}" \
  "${runtime_paths[@]}"

ownership_mismatch="$(
  find "${runtime_paths[@]}" -xdev \
    \( ! -uid "${runtime_uid}" -o ! -gid "${runtime_host_read_gid}" \) \
    -print -quit
)"
if [[ -n "${ownership_mismatch}" ]]; then
  if [[ "${fix_existing}" != "true" ]]; then
    echo "现有文件不属于 ${runtime_uid}:${runtime_host_read_gid}：${ownership_mismatch}" >&2
    echo "确认停服并备份后，可用 --fix-existing 显式迁移所有权。" >&2
    exit 1
  fi
  chown -R "${runtime_uid}:${runtime_host_read_gid}" "${runtime_paths[@]}"
fi

chmod -R g+rX-w,o-rwx "${runtime_paths[@]}"
find "${runtime_paths[@]}" -xdev -type d -exec chmod g+s {} +

echo "生产运行目录 owner UID=${runtime_uid}、Runtime GID=${runtime_gid}、宿主只读 GID=${runtime_host_read_gid} 已验证。"
