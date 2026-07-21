#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
用法：
  scripts/assign-sandbox-project-quota.sh \
    --workspace-id <UUID> --project-id <整数> --quota-bytes <字节数> \
    [--data-root /srv/nanobot] [--quiesced] [--apply]

为一个现有 Workspace 配置 XFS/ext4 project quota。默认仅打印将执行的命令；
真正写入需要同时传入 --quiesced 和 --apply。调用方必须把 project ID 与
workspace_id 的映射持久记录在受控配置或数据库中。

脚本不会格式化、重挂载文件系统，也不会停止、删除容器。
EOF
}

die() {
  echo "Workspace project quota 配置失败：$*" >&2
  exit 1
}

print_command() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

workspace_id=""
project_id=""
quota_bytes=""
data_root="${NANOBOT_SANDBOX_DATA_ROOT:-/srv/nanobot}"
apply=false
quiesced=false

while (( $# )); do
  case "$1" in
    --workspace-id)
      [[ $# -ge 2 ]] || die "--workspace-id 缺少参数"
      workspace_id="$2"
      shift 2
      ;;
    --project-id)
      [[ $# -ge 2 ]] || die "--project-id 缺少参数"
      project_id="$2"
      shift 2
      ;;
    --quota-bytes)
      [[ $# -ge 2 ]] || die "--quota-bytes 缺少参数"
      quota_bytes="$2"
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

[[ "${workspace_id}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] \
  || die "workspace_id 必须是小写规范 UUID"
[[ "${project_id}" =~ ^[1-9][0-9]*$ ]] \
  || die "project_id 必须是十进制整数"
(( project_id >= 10000 && project_id <= 2147483647 )) \
  || die "project_id 必须位于 10000..2147483647"
[[ "${quota_bytes}" =~ ^[1-9][0-9]*$ ]] \
  || die "quota_bytes 必须是十进制整数"
(( quota_bytes >= 1048576 && quota_bytes <= 1125899906842624 )) \
  || die "quota_bytes 必须位于 1 MiB..1 PiB"
[[ "${data_root}" =~ ^/[0-9A-Za-z._/-]+$ ]] \
  || die "数据根目录只允许安全的绝对路径字符"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"${script_dir}/check-sandbox-data-disk.sh" "${data_root}"

workspace_path="${data_root}/workspaces/${workspace_id:0:2}/${workspace_id}/data"
[[ -d "${workspace_path}" && ! -L "${workspace_path}" ]] \
  || die "Workspace 数据目录不存在或是符号链接"
resolved_workspace="$(realpath -e -- "${workspace_path}")" \
  || die "无法解析 Workspace 数据目录"
expected_workspace="$(realpath -e -- "${data_root}")/workspaces/${workspace_id:0:2}/${workspace_id}/data"
[[ "${resolved_workspace}" == "${expected_workspace}" ]] \
  || die "Workspace 目录解析后越出预期布局"

mount_target="$(findmnt -n -T "${data_root}" -o TARGET)"
mount_source="$(findmnt -n -T "${data_root}" -o SOURCE)"
filesystem_type="$(findmnt -n -T "${data_root}" -o FSTYPE)"
[[ "${mount_target}" =~ ^/[0-9A-Za-z._/-]+$ ]] \
  || die "挂载点包含不支持的字符"

if [[ "${filesystem_type}" == "xfs" ]]; then
  command -v xfs_quota >/dev/null 2>&1 \
    || die "缺少 xfs_quota，请先安装发行版 xfsprogs"
  setup_command=(
    xfs_quota -x
    -c "project -s -p ${resolved_workspace} ${project_id}"
    "${mount_target}"
  )
  limit_command=(
    xfs_quota -x
    -c "limit -p bhard=${quota_bytes}b ${project_id}"
    "${mount_target}"
  )
  verify_command=(
    xfs_quota -x
    -c "project -c -p ${resolved_workspace} ${project_id}"
    "${mount_target}"
  )
elif [[ "${filesystem_type}" == "ext4" ]]; then
  command -v chattr >/dev/null 2>&1 \
    || die "缺少 chattr，请先安装发行版 e2fsprogs"
  command -v tune2fs >/dev/null 2>&1 \
    || die "缺少 tune2fs，无法验证 ext4 project 特性"
  command -v setquota >/dev/null 2>&1 \
    || die "缺少 setquota，请先安装发行版 quota 工具"
  filesystem_features="$(tune2fs -l "${mount_source}" 2>/dev/null \
    | awk -F: '/^Filesystem features:/ {print $2}')"
  [[ " ${filesystem_features} " == *" project "* ]] \
    || die "ext4 尚未启用 project 文件系统特性"
  hard_limit_kib=$(( (quota_bytes + 1023) / 1024 ))
  setup_command=(
    chattr -R -p "${project_id}" +P "${resolved_workspace}"
  )
  limit_command=(
    setquota -P "${project_id}" 0 "${hard_limit_kib}" 0 0 "${mount_target}"
  )
  verify_command=(
    lsattr -dp "${resolved_workspace}"
  )
else
  die "不支持的文件系统：${filesystem_type}"
fi

echo "将为 Workspace 配置 project quota："
echo "  workspace_id=${workspace_id}"
echo "  project_id=${project_id}"
echo "  quota_bytes=${quota_bytes}"
echo "  filesystem=${filesystem_type}"
print_command "${setup_command[@]}"
print_command "${limit_command[@]}"
print_command "${verify_command[@]}"

if [[ "${apply}" != "true" ]]; then
  echo "当前为预览；未传入 --apply，不会修改文件系统。"
  exit 0
fi
[[ "${quiesced}" == "true" ]] \
  || die "实际写入必须显式传入 --quiesced"
(( EUID == 0 )) || die "实际写入必须以 root 运行"
command -v docker >/dev/null 2>&1 || die "无法检查活动 Sandbox 容器"

while IFS= read -r container_name; do
  if [[ "${container_name}" == nanobot-sbx-* ]]; then
    die "仍有活动 Sandbox 容器 ${container_name}，拒绝修改 quota"
  fi
done < <(
  docker ps \
    --filter 'label=com.nanobot.sandbox=true' \
    --filter 'label=com.nanobot.managed-by=sandboxd' \
    --format '{{.Names}}'
)

"${setup_command[@]}"
"${limit_command[@]}"
"${verify_command[@]}"
echo "Workspace project quota 已配置；请立即持久记录 project ID 映射。"
