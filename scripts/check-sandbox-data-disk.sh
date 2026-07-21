#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
用法：scripts/check-sandbox-data-disk.sh [数据根目录]

只读检查 Sandbox 长期数据目录是否位于独立的 XFS/ext4 挂载点，并确认已启用
project quota。默认检查 /srv/nanobot；不会创建目录、重挂载或修改文件系统。

可选环境变量：
  NANOBOT_SANDBOX_EXPECTED_DEVICE  要求匹配的块设备或 UUID=... 来源
EOF
}

die() {
  echo "Sandbox 数据盘门禁失败：$*" >&2
  exit 1
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if (( $# > 1 )); then
  usage >&2
  exit 2
fi

data_root="${1:-${NANOBOT_SANDBOX_DATA_ROOT:-/srv/nanobot}}"
expected_source="${NANOBOT_SANDBOX_EXPECTED_DEVICE:-}"

[[ "${data_root}" == /* ]] || die "数据根目录必须是绝对路径"
[[ "${data_root}" != *$'\n'* && "${data_root}" != *$'\r'* ]] \
  || die "数据根目录包含非法控制字符"
[[ -e "${data_root}" ]] || die "数据根目录不存在：${data_root}"
[[ -d "${data_root}" && ! -L "${data_root}" ]] \
  || die "数据根目录必须是非符号链接目录"

resolved_root="$(realpath -e -- "${data_root}")" \
  || die "无法解析数据根目录"
mount_target="$(findmnt -n -T "${resolved_root}" -o TARGET)" \
  || die "无法查询数据根目录挂载点"
mount_source="$(findmnt -n -T "${resolved_root}" -o SOURCE)" \
  || die "无法查询数据盘来源"
filesystem_type="$(findmnt -n -T "${resolved_root}" -o FSTYPE)" \
  || die "无法查询数据盘文件系统"
mount_options="$(findmnt -n -T "${resolved_root}" -o OPTIONS)" \
  || die "无法查询数据盘挂载参数"
root_source="$(findmnt -n -T / -o SOURCE)" \
  || die "无法查询根文件系统来源"

[[ "${resolved_root}" != "/" && "${mount_target}" != "/" ]] \
  || die "数据目录仍位于根挂载点"
[[ "${resolved_root}" == "${mount_target}" ]] \
  || die "数据根目录本身不是独立挂载点：实际挂载点为 ${mount_target}"
[[ "${mount_source}" != "${root_source}" ]] \
  || die "数据目录与根文件系统使用同一来源 ${mount_source}"
if [[ -n "${expected_source}" && "${mount_source}" != "${expected_source}" ]]; then
  die "数据盘来源 ${mount_source} 与要求的 ${expected_source} 不一致"
fi

case "${filesystem_type}" in
  xfs)
    case ",${mount_options}," in
      *,prjquota,*|*,pquota,*) ;;
      *) die "XFS 未以 prjquota/pquota 挂载" ;;
    esac
    ;;
  ext4)
    case ",${mount_options}," in
      *,prjquota,*) ;;
      *) die "ext4 未以 prjquota 挂载" ;;
    esac
    ;;
  *)
    die "仅支持经过验收的 XFS 或 ext4，当前为 ${filesystem_type}"
    ;;
esac

echo "Sandbox 数据盘门禁通过"
echo "目录：${resolved_root}"
echo "来源：${mount_source}"
echo "文件系统：${filesystem_type}"
echo "挂载参数：${mount_options}"
df -h -- "${resolved_root}"
df -i -- "${resolved_root}"
