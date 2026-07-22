#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
用法：
  scripts/check-loopback-image-allocation.sh <镜像文件> <预期字节数>

只读核对 loopback 镜像的逻辑大小和底层实际分配空间。实际分配空间
小于预期字节数时失败关闭，避免把稀疏文件误判为预分配镜像。
EOF
}

die() {
  printf 'loopback 镜像分配检查失败：%s\n' "$*" >&2
  exit 1
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

(( $# == 2 )) || die "必须提供镜像文件和预期字节数"

image_path="$1"
expected_bytes="$2"

[[ "${image_path}" == /* ]] || die "镜像文件必须使用绝对路径"
[[ -f "${image_path}" && ! -L "${image_path}" ]] \
  || die "镜像必须是非符号链接普通文件：${image_path}"
[[ "${expected_bytes}" =~ ^[0-9]+$ && "${expected_bytes}" != "0" ]] \
  || die "预期字节数必须是正整数"

logical_bytes="$(stat -c '%s' -- "${image_path}")"
allocated_blocks="$(stat -c '%b' -- "${image_path}")"
block_bytes="$(stat -c '%B' -- "${image_path}")"

[[ "${logical_bytes}" =~ ^[0-9]+$ \
    && "${allocated_blocks}" =~ ^[0-9]+$ \
    && "${block_bytes}" =~ ^[0-9]+$ ]] \
  || die "无法解析镜像分配信息"

actual_allocated_bytes=$((allocated_blocks * block_bytes))

printf 'logical_bytes=%s\n' "${logical_bytes}"
printf 'actual_allocated_bytes=%s\n' "${actual_allocated_bytes}"
printf 'expected_bytes=%s\n' "${expected_bytes}"

[[ "${logical_bytes}" == "${expected_bytes}" ]] \
  || die "逻辑大小与预期不一致"
(( actual_allocated_bytes >= expected_bytes )) \
  || die "实际分配空间不足：${actual_allocated_bytes} < ${expected_bytes}"
