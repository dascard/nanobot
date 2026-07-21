#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
用法：scripts/cleanup-sandbox-runtime.sh [选项]

清理 /runtime 中超过 TTL 的可重建缓存。默认只预览；实际删除必须同时传入
--quiesced 和 --apply，并且不能存在活动的 Nanobot Sandbox 容器。

选项：
  --data-root <目录>    默认 /srv/nanobot
  --ttl-hours <小时>    默认 168（7 天）
  --max-bytes <字节>    清理后总量门禁，默认 10737418240（10 GiB）
  --quiesced            确认已关闭执行入口并停止新的 Sandbox 运行
  --apply               实际删除；不传时仅列出候选
EOF
}

die() {
  echo "Sandbox runtime 清理失败：$*" >&2
  exit 1
}

data_root="${NANOBOT_SANDBOX_DATA_ROOT:-/srv/nanobot}"
ttl_hours="${NANOBOT_SANDBOX_RUNTIME_TTL_HOURS:-168}"
max_bytes="${NANOBOT_SANDBOX_RUNTIME_MAX_BYTES:-10737418240}"
quiesced=false
apply=false

while (( $# )); do
  case "$1" in
    --data-root)
      [[ $# -ge 2 ]] || die "--data-root 缺少参数"
      data_root="$2"
      shift 2
      ;;
    --ttl-hours)
      [[ $# -ge 2 ]] || die "--ttl-hours 缺少参数"
      ttl_hours="$2"
      shift 2
      ;;
    --max-bytes)
      [[ $# -ge 2 ]] || die "--max-bytes 缺少参数"
      max_bytes="$2"
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

[[ "${ttl_hours}" =~ ^[1-9][0-9]*$ ]] \
  || die "TTL 小时数必须是十进制整数"
(( ttl_hours >= 1 && ttl_hours <= 8760 )) \
  || die "TTL 必须位于 1..8760 小时"
[[ "${max_bytes}" =~ ^[1-9][0-9]*$ ]] \
  || die "容量门禁必须是十进制整数"
(( max_bytes >= 1048576 )) || die "容量门禁不得低于 1 MiB"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"${script_dir}/check-sandbox-data-disk.sh" "${data_root}"

runtime_root="${data_root}/runtime"
[[ -d "${runtime_root}" && ! -L "${runtime_root}" ]] \
  || die "runtime 根目录不存在或是符号链接"
resolved_runtime="$(realpath -e -- "${runtime_root}")"
[[ "${resolved_runtime}" == "$(realpath -e -- "${data_root}")/runtime" ]] \
  || die "runtime 根目录越出预期布局"

command -v docker >/dev/null 2>&1 || die "无法检查活动 Sandbox 容器"
while IFS= read -r container_name; do
  if [[ "${container_name}" == nanobot-sbx-* ]]; then
    die "仍有活动 Sandbox 容器 ${container_name}，本轮不清理"
  fi
done < <(
  docker ps \
    --filter 'label=com.nanobot.sandbox=true' \
    --filter 'label=com.nanobot.managed-by=sandboxd' \
    --format '{{.Names}}'
)

ttl_minutes=$(( ttl_hours * 60 ))
echo "超过 ${ttl_hours} 小时的 runtime 清理候选："
find "${resolved_runtime}" -xdev -mindepth 2 \
  ! -type d -mmin "+${ttl_minutes}" -print

if [[ "${apply}" != "true" ]]; then
  echo "当前为预览；未传入 --apply，不会删除缓存。"
  exit 0
fi
[[ "${quiesced}" == "true" ]] \
  || die "实际清理必须显式传入 --quiesced"
(( EUID == 0 )) || die "实际清理必须以 root 运行"

lock_path="/run/lock/nanobot-sandbox-runtime-cleanup.lock"
exec 9>"${lock_path}"
flock -n 9 || die "已有 runtime 清理任务运行中"

find "${resolved_runtime}" -xdev -mindepth 2 \
  ! -type d -mmin "+${ttl_minutes}" -delete
find "${resolved_runtime}" -xdev -depth -mindepth 2 \
  -type d -empty -delete

used_bytes="$(du -sx --block-size=1 "${resolved_runtime}" | awk '{print $1}')"
[[ "${used_bytes}" =~ ^[0-9]+$ ]] || die "无法核算 runtime 使用量"
if (( used_bytes > max_bytes )); then
  die "TTL 清理后 runtime 仍占用 ${used_bytes} 字节，超过 ${max_bytes} 字节门禁"
fi
echo "runtime TTL 清理完成，当前占用 ${used_bytes} 字节。"
