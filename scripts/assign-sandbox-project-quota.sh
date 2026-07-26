#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
用法：
  scripts/assign-sandbox-project-quota.sh \
    --workspace-id <UUID> --scope <workspace|runtime> \
    --project-id <整数> --quota-bytes <字节数> \
    [--data-root /srv/nanobot] [--quiesced] [--apply|--verify]

  scripts/assign-sandbox-project-quota.sh \
    --check-capability [--data-root /srv/nanobot]

为一个现有 Workspace 的长期数据或 Runtime 配置 XFS/ext4 project quota。
默认 scope 为 workspace，且仅打印将执行的命令；真正写入或读回验证需要
分别传入 --apply 或 --verify，并同时传入 --quiesced。调用方必须把
project ID 映射持久记录在数据库中。

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
scope="workspace"
project_id=""
quota_bytes=""
data_root="${NANOBOT_SANDBOX_DATA_ROOT:-/srv/nanobot}"
apply=false
verify=false
quiesced=false
check_capability=false

while (( $# )); do
  case "$1" in
    --workspace-id)
      [[ $# -ge 2 ]] || die "--workspace-id 缺少参数"
      workspace_id="$2"
      shift 2
      ;;
    --scope)
      [[ $# -ge 2 ]] || die "--scope 缺少参数"
      scope="$2"
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
    --verify)
      verify=true
      shift
      ;;
    --check-capability)
      check_capability=true
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

[[ "${apply}" != "true" || "${verify}" != "true" ]] \
  || die "--apply 与 --verify 不能同时使用"

[[ "${data_root}" =~ ^/[0-9A-Za-z._/-]+$ ]] \
  || die "数据根目录只允许安全的绝对路径字符"
if [[ "${check_capability}" != "true" ]]; then
  [[ "${workspace_id}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] \
    || die "workspace_id 必须是小写规范 UUID"
  [[ "${scope}" == "workspace" || "${scope}" == "runtime" ]] \
    || die "scope 必须是 workspace 或 runtime"
  [[ "${project_id}" =~ ^[1-9][0-9]*$ ]] \
    || die "project_id 必须是十进制整数"
  (( project_id >= 10000 && project_id <= 2147483647 )) \
    || die "project_id 必须位于 10000..2147483647"
  [[ "${quota_bytes}" =~ ^[1-9][0-9]*$ ]] \
    || die "quota_bytes 必须是十进制整数"
  (( quota_bytes >= 1048576 && quota_bytes <= 1125899906842624 )) \
    || die "quota_bytes 必须位于 1 MiB..1 PiB"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"${script_dir}/check-sandbox-data-disk.sh" "${data_root}"
mount_target="$(findmnt -n -T "${data_root}" -o TARGET)"
mount_source="$(findmnt -n -T "${data_root}" -o SOURCE)"
filesystem_type="$(findmnt -n -T "${data_root}" -o FSTYPE)"
[[ "${mount_target}" =~ ^/[0-9A-Za-z._/-]+$ ]] \
  || die "挂载点包含不支持的字符"

if [[ "${check_capability}" == "true" ]]; then
  if [[ "${filesystem_type}" == "xfs" ]]; then
    command -v xfs_quota >/dev/null 2>&1 \
      || die "缺少 xfs_quota，请先安装发行版 xfsprogs"
  elif [[ "${filesystem_type}" == "ext4" ]]; then
    command -v chattr >/dev/null 2>&1 \
      || die "缺少 chattr，请先安装发行版 e2fsprogs"
    command -v tune2fs >/dev/null 2>&1 \
      || die "缺少 tune2fs，无法验证 ext4 project 特性"
    command -v setquota >/dev/null 2>&1 \
      || die "缺少 setquota，请先安装发行版 quota 工具"
    command -v lsattr >/dev/null 2>&1 \
      || die "缺少 lsattr，请先安装发行版 e2fsprogs"
    command -v quota >/dev/null 2>&1 \
      || die "缺少 quota，无法读回验证 ext4 project 硬限制"
    filesystem_features="$(tune2fs -l "${mount_source}" 2>/dev/null \
      | awk -F: '/^Filesystem features:/ {print $2}')"
    [[ " ${filesystem_features} " == *" project "* ]] \
      || die "ext4 尚未启用 project 文件系统特性"
  else
    die "不支持的文件系统：${filesystem_type}"
  fi
  echo "project_quota_ready=true"
  echo "workspace_scope=true"
  echo "runtime_scope=true"
  exit 0
fi

if [[ "${scope}" == "workspace" ]]; then
  quota_path="${data_root}/workspaces/${workspace_id:0:2}/${workspace_id}/data"
  expected_path="$(realpath -e -- "${data_root}")/workspaces/${workspace_id:0:2}/${workspace_id}/data"
else
  quota_path="${data_root}/runtime/${workspace_id}"
  expected_path="$(realpath -e -- "${data_root}")/runtime/${workspace_id}"
fi
[[ -d "${quota_path}" && ! -L "${quota_path}" ]] \
  || die "Sandbox 配额目录不存在或是符号链接"
resolved_quota_path="$(realpath -e -- "${quota_path}")" \
  || die "无法解析 Sandbox 配额目录"
[[ "${resolved_quota_path}" == "${expected_path}" ]] \
  || die "Sandbox 配额目录解析后越出预期布局"
hard_limit_kib=$(( (quota_bytes + 1023) / 1024 ))

if [[ "${filesystem_type}" == "xfs" ]]; then
  command -v xfs_quota >/dev/null 2>&1 \
    || die "缺少 xfs_quota，请先安装发行版 xfsprogs"
  setup_command=(
    xfs_quota -x
    -c "project -s -p ${resolved_quota_path} ${project_id}"
    "${mount_target}"
  )
  limit_command=(
    xfs_quota -x
    -c "limit -p bhard=${hard_limit_kib}k ${project_id}"
    "${mount_target}"
  )
  verify_command=(
    xfs_quota -x
    -c "project -c -p ${resolved_quota_path} ${project_id}"
    "${mount_target}"
  )
  report_command=(
    xfs_quota -x
    -c "report -p -n -N -b"
    "${mount_target}"
  )
elif [[ "${filesystem_type}" == "ext4" ]]; then
  command -v chattr >/dev/null 2>&1 \
    || die "缺少 chattr，请先安装发行版 e2fsprogs"
  command -v tune2fs >/dev/null 2>&1 \
    || die "缺少 tune2fs，无法验证 ext4 project 特性"
  command -v setquota >/dev/null 2>&1 \
    || die "缺少 setquota，请先安装发行版 quota 工具"
  command -v quota >/dev/null 2>&1 \
    || die "缺少 quota，无法读回验证 ext4 project 硬限制"
  filesystem_features="$(tune2fs -l "${mount_source}" 2>/dev/null \
    | awk -F: '/^Filesystem features:/ {print $2}')"
  [[ " ${filesystem_features} " == *" project "* ]] \
    || die "ext4 尚未启用 project 文件系统特性"
  setup_command=(
    chattr -R -p "${project_id}" +P "${resolved_quota_path}"
  )
  limit_command=(
    setquota -P "${project_id}" 0 "${hard_limit_kib}" 0 0 "${mount_target}"
  )
  verify_command=(
    lsattr -dp "${resolved_quota_path}"
  )
  report_command=(
    quota -P -w -v "${project_id}"
  )
else
  die "不支持的文件系统：${filesystem_type}"
fi

echo "将为 Sandbox ${scope} 配置 project quota："
echo "  workspace_id=${workspace_id}"
echo "  scope=${scope}"
echo "  project_id=${project_id}"
echo "  quota_bytes=${quota_bytes}"
echo "  filesystem=${filesystem_type}"
print_command "${setup_command[@]}"
print_command "${limit_command[@]}"
print_command "${verify_command[@]}"
print_command "${report_command[@]}"

if [[ "${apply}" != "true" && "${verify}" != "true" ]]; then
  echo "当前为预览；未传入 --apply，不会修改文件系统。"
  exit 0
fi
[[ "${quiesced}" == "true" ]] \
  || die "实际写入或验证必须显式传入 --quiesced"
(( EUID == 0 )) || die "实际写入或验证必须以 root 运行"
command -v docker >/dev/null 2>&1 || die "无法检查活动 Sandbox 容器"

while IFS= read -r container_name; do
  if [[ "${container_name}" == nanobot-sbx-* ]]; then
    die "仍有活动 Sandbox 容器 ${container_name}，拒绝修改 quota"
  fi
done < <(
  docker ps \
    --filter 'label=com.nanobot.sandbox=true' \
    --filter 'label=com.nanobot.managed-by=sandboxd' \
    --filter "label=com.nanobot.workspace-id=${workspace_id}" \
    --format '{{.Names}}'
)

if [[ "${apply}" == "true" ]]; then
  "${setup_command[@]}"
  "${limit_command[@]}"
fi

if [[ "${filesystem_type}" == "xfs" ]]; then
  "${verify_command[@]}"
  report_output="$("${report_command[@]}")" \
    || die "无法读回 XFS project quota"
  observed_hard_kib="$(
    awk -v project="${project_id}" '
      $1 == project || $1 == ("#" project) {
        value = $4
        gsub(/[^0-9]/, "", value)
        print value
        exit
      }
    ' <<<"${report_output}"
  )"
  [[ "${observed_hard_kib}" =~ ^[0-9]+$ ]] \
    || die "无法解析 XFS project quota 硬限制"
  (( observed_hard_kib == hard_limit_kib )) \
    || die "XFS project quota 硬限制与请求不一致"
else
  project_output="$("${verify_command[@]}")" \
    || die "无法读回 ext4 目录 project ID"
  observed_project_id="$(
    awk 'NR == 1 {print $1}' <<<"${project_output}"
  )"
  [[ "${observed_project_id}" == "${project_id}" ]] \
    || die "ext4 目录 project ID 与请求不一致"
  report_output="$("${report_command[@]}")" \
    || die "无法读回 ext4 project quota"
  observed_hard_kib="$(
    awk -v source="${mount_source}" -v target="${mount_target}" '
      $1 == source || $1 == target {
        print $4
        exit
      }
    ' <<<"${report_output}"
  )"
  [[ "${observed_hard_kib}" =~ ^[0-9]+$ ]] \
    || die "无法解析 ext4 project quota 硬限制"
  (( observed_hard_kib == hard_limit_kib )) \
    || die "ext4 project quota 硬限制与请求不一致"
fi

echo "project_quota_verified=true"
echo "scope=${scope}"
echo "project_id=${project_id}"
echo "quota_bytes=${quota_bytes}"
if [[ "${apply}" == "true" ]]; then
  echo "Sandbox ${scope} project quota 已配置并完成读回验证。"
else
  echo "Sandbox ${scope} project quota 已完成独立读回验证。"
fi
