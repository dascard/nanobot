#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

runtime_uid="${NANOBOT_RUNTIME_UID:-10001}"
runtime_gid="${NANOBOT_RUNTIME_GID:-10001}"
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
if [[ ! "${runtime_uid}" =~ ^[0-9]+$ || ! "${runtime_gid}" =~ ^[0-9]+$ ]]; then
  echo "NANOBOT_RUNTIME_UID/GID 必须是数字。" >&2
  exit 2
fi

install -d -m 0750 -o "${runtime_uid}" -g "${runtime_gid}" \
  data models sentinel

ownership_mismatch="$(
  find data models sentinel -xdev \
    \( ! -uid "${runtime_uid}" -o ! -gid "${runtime_gid}" \) \
    -print -quit
)"
if [[ -n "${ownership_mismatch}" ]]; then
  if [[ "${fix_existing}" != "true" ]]; then
    echo "现有文件不属于 ${runtime_uid}:${runtime_gid}：${ownership_mismatch}" >&2
    echo "确认停服并备份后，可用 --fix-existing 显式迁移所有权。" >&2
    exit 1
  fi
  chown -R "${runtime_uid}:${runtime_gid}" data models sentinel
fi

echo "运行目录权限已验证为 ${runtime_uid}:${runtime_gid}。"
