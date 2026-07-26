#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

usage() {
  cat <<'EOF'
用法：
  scripts/sandbox-smoke-test.sh \
    --manifest <部署 Profile manifest> \
    [--data-root /srv/nanobot] \
    [--evidence-root <证据目录>] \
    [--preflight-only]

运行六组真实 Docker 验收：基础安全、Lease、Process、Developer 工具链、
网络和数据连续性。每组保存独立日志与 JUnit，最终生成 summary.json。

任一测试失败、跳过、无测试或前置条件缺失都会返回非 0。脚本不会调用 sudo
或任何 Docker 全局 prune，也不会删除未知容器、网络、镜像或数据。
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
manifest=""
data_root="/srv/nanobot"
evidence_root="${XDG_CACHE_HOME:-${HOME}/.cache}/nanobot-sandbox-smoke"
preflight_only=false

while (( $# )); do
  case "$1" in
    --manifest)
      [[ $# -ge 2 ]] || {
        echo "--manifest 缺少参数。" >&2
        exit 2
      }
      manifest="$2"
      shift 2
      ;;
    --data-root)
      [[ $# -ge 2 ]] || {
        echo "--data-root 缺少参数。" >&2
        exit 2
      }
      data_root="$2"
      shift 2
      ;;
    --evidence-root)
      [[ $# -ge 2 ]] || {
        echo "--evidence-root 缺少参数。" >&2
        exit 2
      }
      evidence_root="$2"
      shift 2
      ;;
    --preflight-only)
      preflight_only=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "${manifest}" ]] || {
  echo "必须提供 --manifest。" >&2
  exit 2
}
[[ "${manifest}" == /* && "${data_root}" == /* && "${evidence_root}" == /* ]] \
  || {
    echo "manifest、data root 与 evidence root 必须使用绝对路径。" >&2
    exit 2
  }

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
evidence_dir="${evidence_root}/${timestamp}-$$"
preflight_log="${evidence_dir}/preflight.log"
summary_file="${evidence_dir}/summary.json"
quota_helper_source="${repo_root}/scripts/assign-sandbox-project-quota.sh"
quota_helper="${quota_helper_source}"
mkdir -p "${evidence_dir}/pre" "${evidence_dir}/post" "${evidence_dir}/groups"
export TMPDIR="${evidence_dir}/tmp"
mkdir -p "${TMPDIR}"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

capture_command() {
  local output_file="$1"
  shift
  {
    printf '$'
    printf ' %q' "$@"
    printf '\n'
    "$@"
  } >"${output_file}" 2>&1 || true
}

capture_apparmor() {
  local output_file="$1"
  {
    echo '$ cat /sys/module/apparmor/parameters/enabled'
    cat /sys/module/apparmor/parameters/enabled
    echo '$ cat /sys/kernel/security/apparmor/profiles'
    cat /sys/kernel/security/apparmor/profiles
    if command -v aa-status >/dev/null 2>&1; then
      echo '$ aa-status'
      aa-status
    fi
  } >"${output_file}" 2>&1 || true
}

capture_state() {
  local phase="$1"
  local phase_dir="${evidence_dir}/${phase}"

  capture_command "${phase_dir}/date.txt" date --iso-8601=seconds
  capture_command "${phase_dir}/id.txt" id
  capture_command "${phase_dir}/df-h.txt" df -h
  capture_command "${phase_dir}/df-i.txt" df -i
  capture_command "${phase_dir}/findmnt.txt" findmnt --target "${data_root}"
  capture_command "${phase_dir}/docker-info.txt" docker info
  capture_command "${phase_dir}/docker-ps-a.txt" docker ps -a --no-trunc
  capture_command "${phase_dir}/docker-network-ls.txt" docker network ls
  capture_command "${phase_dir}/docker-system-df.txt" docker system df
  capture_command \
    "${phase_dir}/docker-security-options.txt" \
    docker info --format '{{json .SecurityOptions}}'
  capture_command \
    "${phase_dir}/sandbox-images.txt" \
    docker images --no-trunc \
      --filter 'label=com.nanobot.sandbox-image=true'
  capture_apparmor "${phase_dir}/apparmor.txt"
}

on_exit() {
  local exit_code=$?
  trap - EXIT
  capture_state post
  printf '%s\n' "${exit_code}" >"${evidence_dir}/exit-code.txt"
  if (( exit_code == 0 )); then
    echo "Sandbox 真实验收矩阵通过。"
  else
    echo "Sandbox 真实验收矩阵未通过，退出码：${exit_code}" >&2
  fi
  echo "证据目录：${evidence_dir}"
  exit "${exit_code}"
}
trap on_exit EXIT

preflight_checks() {
  (( EUID == 0 )) || {
    echo "BLOCKED：真实验收必须以 root 运行。"
    return 1
  }
  command -v docker >/dev/null || {
    echo "BLOCKED：缺少 Docker CLI。"
    return 1
  }
  command -v python >/dev/null || {
    echo "BLOCKED：缺少当前 Smoke Python。"
    return 1
  }
  command -v install >/dev/null || {
    echo "BLOCKED：缺少 install 命令。"
    return 1
  }
  [[ -f "${manifest}" && ! -L "${manifest}" ]] || {
    echo "BLOCKED：部署 Profile manifest 不是普通文件。"
    return 1
  }
  [[ -d "${data_root}" && ! -L "${data_root}" ]] || {
    echo "BLOCKED：Sandbox 数据根目录不是普通目录。"
    return 1
  }
  [[ -x "${quota_helper_source}" && ! -L "${quota_helper_source}" ]] || {
    echo "BLOCKED：project quota helper 不可执行或是符号链接。"
    return 1
  }
  docker info >/dev/null || {
    echo "BLOCKED：Docker Engine 不可用。"
    return 1
  }
  local security_options
  security_options="$(docker info --format '{{json .SecurityOptions}}')"
  grep -qi seccomp <<<"${security_options}" || {
    echo "BLOCKED：Docker 未启用 seccomp。"
    return 1
  }
  grep -qi apparmor <<<"${security_options}" || {
    echo "BLOCKED：Docker 未启用 AppArmor。"
    return 1
  }
  [[ "$(cat /sys/module/apparmor/parameters/enabled 2>/dev/null || true)" \
      == "Y" ]] || {
    echo "BLOCKED：宿主内核未启用 AppArmor。"
    return 1
  }
  [[ -r /sys/kernel/security/apparmor/profiles ]] || {
    echo "BLOCKED：无法读取已加载 AppArmor profile。"
    return 1
  }
  for profile in nanobot-sandbox-restricted nanobot-sandbox-developer; do
    grep -q "^${profile} " /sys/kernel/security/apparmor/profiles || {
      echo "BLOCKED：${profile} 尚未加载。"
      return 1
    }
  done

  "${repo_root}/scripts/check-sandbox-data-disk.sh" "${data_root}" || {
    echo "BLOCKED：Sandbox 独立数据盘或 prjquota 未就绪。"
    return 1
  }
  "${quota_helper_source}" \
    --check-capability \
    --data-root "${data_root}" || {
    echo "BLOCKED：Workspace/Runtime project quota 能力未就绪。"
    return 1
  }

  PYTHONPATH="${repo_root}" python - \
    "${manifest}" <<'PY'
import sys

import docker

from core.sandbox.profile_catalog import load_profile_catalog

catalog = load_profile_catalog(sys.argv[1])
if set(catalog.by_id) != {
    "restricted",
    "developer",
    "trusted_developer",
}:
    raise SystemExit("BLOCKED：部署 manifest 的 Profile 集合不完整。")
client = docker.from_env(timeout=30)
try:
    if not client.ping():
        raise SystemExit("BLOCKED：Docker Engine ping 失败。")
    for profile_id in ("restricted", "developer"):
        profile = catalog.profile(profile_id)
        if not profile.grantable or len(profile.image_allowlist) != 1:
            raise SystemExit(
                f"BLOCKED：{profile_id} 镜像摘要未唯一固定。"
            )
        image = client.images.get(profile.image_reference)
        if str(image.id).lower() != profile.image_allowlist[0]:
            raise SystemExit(
                f"BLOCKED：{profile_id} tag 与 IMAGE ID 不一致。"
            )
        if str(image.attrs.get("Config", {}).get("User") or "") != "10001:10001":
            raise SystemExit(
                f"BLOCKED：{profile_id} 默认用户不是 10001:10001。"
            )
    developer = catalog.profile("developer")
    if len(developer.network_proxy_image_allowlist) != 1:
        raise SystemExit("BLOCKED：代理镜像摘要未唯一固定。")
    proxy = client.images.get(developer.network_proxy_image_reference)
    if str(proxy.id).lower() != developer.network_proxy_image_allowlist[0]:
        raise SystemExit("BLOCKED：代理 tag 与 IMAGE ID 不一致。")
    if str(proxy.attrs.get("Config", {}).get("User") or "") != "13:13":
        raise SystemExit("BLOCKED：代理镜像默认用户不是 13:13。")
    trusted = catalog.profile("trusted_developer")
    if trusted.grantable or trusted.image_allowlist:
        raise SystemExit("BLOCKED：trusted_developer 占位被错误开放。")
    print(f"catalog_generation={catalog.catalog_generation}")
    print(f"policy_sha256={catalog.policy_sha256}")
finally:
    client.close()
PY
}

summary_arguments=()
write_summary() {
  local preflight_status="$1"
  local summary_exit=0
  if python "${repo_root}/scripts/sandbox-smoke-summary.py" \
      --output "${summary_file}" \
      --preflight-status "${preflight_status}" \
      --preflight-log "${preflight_log}" \
      "${summary_arguments[@]}"; then
    summary_exit=0
  else
    summary_exit=$?
  fi
  return "${summary_exit}"
}

capture_state pre
if preflight_checks 2>&1 | tee "${preflight_log}"; then
  preflight_status="passed"
else
  preflight_status="blocked"
fi

if [[ "${preflight_status}" != "passed" ]]; then
  write_summary "${preflight_status}" || exit $?
  exit 2
fi
if [[ "${preflight_only}" == "true" ]]; then
  write_summary "${preflight_status}"
  exit 0
fi

# ProjectQuotaManager 以当前 EUID 校验 helper 所有权。Smoke 以 root 运行，
# 因而把本发布树内两个只读脚本复制到本轮证据目录，避免信任 deploy 用户拥有的
# worktree 文件；只复制本轮固定文件，不改变发布树和宿主全局路径。
install -d -m 0700 -o root -g root "${evidence_dir}/quota-helper"
install -m 0755 -o root -g root \
  "${repo_root}/scripts/assign-sandbox-project-quota.sh" \
  "${repo_root}/scripts/check-sandbox-data-disk.sh" \
  "${evidence_dir}/quota-helper/"
quota_helper="${evidence_dir}/quota-helper/assign-sandbox-project-quota.sh"

run_group() {
  local group_id="$1"
  local group_name="$2"
  local node_id="$3"
  local log_file="${evidence_dir}/groups/${group_id}.log"
  local junit_file="${evidence_dir}/groups/${group_id}.xml"
  local exit_code=0

  echo "开始分组：${group_name}"
  if env \
      PYTHONDONTWRITEBYTECODE=1 \
      NANOBOT_RUN_DOCKER_TESTS=1 \
      NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE="${manifest}" \
      NANOBOT_SANDBOX_TEST_DATA_ROOT="${data_root}" \
      NANOBOT_SANDBOX_QUOTA_HELPER="${quota_helper}" \
      NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED=true \
      python -m pytest \
        --noconftest \
        -c /dev/null \
        --junitxml="${junit_file}" \
        "${node_id}" \
        -v 2>&1 | tee "${log_file}"; then
    exit_code=0
  else
    exit_code=$?
  fi
  summary_arguments+=(
    --group
    "${group_id}"
    "${group_name}"
    "${exit_code}"
    "${junit_file}"
    "${log_file}"
  )
}

cd "${repo_root}"
run_group \
  basic-security \
  基础安全 \
  tests/test_sandbox_security.py::test_real_docker_security_matrix
run_group \
  lease \
  Lease \
  tests/test_sandbox_real_docker.py::test_real_docker_lease_lifecycle
run_group \
  process \
  Process \
  tests/test_sandbox_real_docker.py::test_real_docker_process_sessions
run_group \
  developer-toolchain \
  "Developer 工具链" \
  tests/test_sandbox_real_docker.py::test_real_docker_developer_toolchain
run_group \
  network \
  网络 \
  tests/test_sandbox_network_policy.py::test_real_docker_developer_egress_and_rejection_matrix
run_group \
  data-continuity \
  数据连续性 \
  tests/test_sandbox_real_docker.py::test_real_docker_data_continuity_and_project_quota

write_summary passed
