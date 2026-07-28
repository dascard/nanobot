#!/usr/bin/env bash

# 按 ReleaseManifest 协调 Prompt 审计、条件备份、业务静默与 Runtime 切换。
# 整个维护窗口只需要一次 sudo；失败时恢复服务和维护前业务开关。

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT
readonly BASE_DEPLOY="${SCRIPT_DIR}/deploy-production.sh"
readonly PROMPT_MANAGER="${SCRIPT_DIR}/manage-prompt-runtime-production.sh"
readonly BACKUP_SCRIPT="${SCRIPT_DIR}/sandbox-coordinated-backup.sh"
readonly SANDBOX_CONFIG="/etc/nanobot/sandbox-production.conf"
readonly RELEASE_STATE_DIR="${NANOBOT_RELEASE_STATE_DIR:-/var/lib/nanobot/release-state}"
readonly -a FIXED_CONTAINERS=(
  nanobot-server
  nanobot-session-summary-worker
  nanobot-outbound-delivery-worker
  nanobot-semantic-index-worker
)

FEATURE_RESTORE_REQUIRED=false
SERVICES_QUIESCED=false
PRESTATE_FILE=""
readonly START_SECONDS="${SECONDS}"

die() {
  printf 'ERROR=%s\n' "$*" >&2
  exit 1
}

admin_api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  docker exec -i \
    -e ADMIN_METHOD="${method}" \
    -e ADMIN_PATH="${path}" \
    -e ADMIN_BODY="${body}" \
    nanobot-server \
    python - <<'PY'
import json
import os
import urllib.error
import urllib.request

token = os.environ.get("NANOBOT_ADMIN_TOKEN", "")
if not token:
    raise SystemExit("NANOBOT_ADMIN_TOKEN 未配置")
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/v1/admin" + os.environ["ADMIN_PATH"],
    data=(
        os.environ.get("ADMIN_BODY", "").encode("utf-8")
        if os.environ.get("ADMIN_BODY", "")
        else None
    ),
    method=os.environ["ADMIN_METHOD"],
    headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    },
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request, timeout=30) as response:
        value = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    print(exc.read().decode("utf-8", "replace"))
    raise
print(json.dumps(value, ensure_ascii=False, sort_keys=True))
PY
}

wait_fixed_services() {
  local attempt
  local container
  local health
  for attempt in {1..90}; do
    for container in "${FIXED_CONTAINERS[@]}"; do
      health="$(docker inspect "${container}" --format \
        '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' \
        2>/dev/null || true)"
      [[ "${health}" == "healthy" ]] || break
    done
    if [[ "${health}" == "healthy" ]] \
        && curl --fail --silent --show-error --max-time 5 \
          http://127.0.0.1:8000/api/v1/ready >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

restore_services() {
  [[ "${SERVICES_QUIESCED}" == "true" ]] || return 0
  systemctl start nanobot-sandboxd.service >/dev/null 2>&1 || true
  docker start "${FIXED_CONTAINERS[@]}" >/dev/null 2>&1 || true
  wait_fixed_services >/dev/null 2>&1 || true
  SERVICES_QUIESCED=false
}

restore_feature_state() {
  local key
  local source
  local value
  local sandbox_body
  [[ "${FEATURE_RESTORE_REQUIRED}" == "true" ]] || return 0
  [[ -f "${PRESTATE_FILE}" && ! -L "${PRESTATE_FILE}" ]] || return 0
  wait_fixed_services || return 1
  sandbox_body="$(jq -c \
    '.sandbox + {reason:"Runtime 协调部署结束，恢复维护前业务开关"}' \
    "${PRESTATE_FILE}")"
  admin_api PUT /sandbox/features "${sandbox_body}" >/dev/null
  while IFS=$'\t' read -r key source value; do
    if [[ "${source}" == "database" ]]; then
      admin_api PUT "/settings/${key}" \
        "$(jq -cn --argjson value "${value}" '{value:$value}')" \
        >/dev/null
    else
      admin_api POST "/settings/${key}/reset" >/dev/null
    fi
  done < <(jq -r \
    '.settings[] | [.key,.source,(.value|tostring)] | @tsv' \
    "${PRESTATE_FILE}")
  FEATURE_RESTORE_REQUIRED=false
}

update_prestate_evidence() {
  local key="$1"
  local value="$2"
  local temporary
  [[ -f "${PRESTATE_FILE}" && ! -L "${PRESTATE_FILE}" ]] \
    || die "Runtime 协调部署前态文件缺失"
  temporary="$(mktemp \
    "${RELEASE_STATE_DIR}/.coordinated-prestate.XXXXXX")"
  jq --arg key "${key}" --arg value "${value}" '
    .evidence = (.evidence // {}) |
    .evidence[$key] = $value
  ' "${PRESTATE_FILE}" >"${temporary}"
  chmod 0600 "${temporary}"
  chown root:root "${temporary}"
  mv -f -- "${temporary}" "${PRESTATE_FILE}"
}

on_exit() {
  local exit_code="$?"
  trap - EXIT
  if (( exit_code != 0 )); then
    restore_services
    restore_feature_state || true
    printf 'ELAPSED_SECONDS=%s\n' "$((SECONDS - START_SECONDS))" >&2
    printf 'NANOBOT_COORDINATED_DEPLOY_STATUS=failed\n' >&2
  fi
  exit "${exit_code}"
}
trap on_exit EXIT

if [[ "$(id -u)" -ne 0 ]]; then
  die "请使用 sudo 运行 ${BASH_SOURCE[0]}"
fi
[[ -x "${BASE_DEPLOY}" && -x "${PROMPT_MANAGER}" \
    && -x "${BACKUP_SCRIPT}" ]] \
  || die "正式部署、Prompt 或备份入口缺失"
[[ "${RELEASE_STATE_DIR}" == "/var/lib/nanobot/release-state" ]] \
  || die "Runtime 协调部署只允许固定 release-state 目录"
install -d -m 0750 -o root -g root "${RELEASE_STATE_DIR}"
exec 9>"${RELEASE_STATE_DIR}/coordinated-deploy.lock"
flock -n 9 || die "已有 Runtime 协调部署正在执行"

plan_output="$(env \
  -u NANOBOT_COORDINATED_BACKUP_DIR \
  -u NANOBOT_PROMPT_AUDIT_RECEIPT \
  NANOBOT_DEPLOY_PLAN_ONLY=true \
  bash "${BASE_DEPLOY}")"
plan_json="$(tail -n 1 <<<"${plan_output}")"
jq -e '
  .schema_version == 1 and
  (.runtime_deployment_required|type)=="boolean" and
  (.coordinated_backup_required|type)=="boolean" and
  (.prompt_audit_required|type)=="boolean" and
  (.target_source_sha|type)=="string"
' <<<"${plan_json}" >/dev/null \
  || die "Runtime 部署计划不是有效结构化结果"
printf 'DEPLOYMENT_PLAN=%s\n' "${plan_json}"

target_sha="$(jq -r '.target_source_sha' <<<"${plan_json}")"
[[ "${target_sha}" =~ ^[0-9a-f]{40}$ ]] \
  || die "Runtime 部署计划的目标 Git SHA 无效"
PRESTATE_FILE="${RELEASE_STATE_DIR}/coordinated-deploy-${target_sha}.json"
if [[ -e "${PRESTATE_FILE}" || -L "${PRESTATE_FILE}" ]]; then
  [[ -f "${PRESTATE_FILE}" && ! -L "${PRESTATE_FILE}" \
      && "$(stat -c '%u:%a' "${PRESTATE_FILE}")" == "0:600" ]] \
    || die "Runtime 协调部署前态文件权限无效"
  jq -e '
    .schema_version == 1 and
    (.sandbox.enabled|type) == "boolean" and
    (.sandbox.exec_enabled|type) == "boolean" and
    (.sandbox.group_enabled|type) == "boolean" and
    (.settings|type) == "array" and
    (.settings|length) == 2 and
    ([.settings[].key]|unique|length) == 2 and
    all(.settings[];
      (.key == "group_learning.enabled" or
       .key == "group_memory.injection_enabled") and
      (.value|type) == "boolean" and
      (.source == "database" or
       .source == "environment" or
       .source == "default")) and
    ((.evidence // {})|type) == "object"
  ' "${PRESTATE_FILE}" >/dev/null \
    || die "Runtime 协调部署前态文件结构无效"
fi

if [[ "$(jq -r '.runtime_deployment_required' <<<"${plan_json}")" \
    == "false" ]]; then
  if [[ -f "${PRESTATE_FILE}" ]]; then
    FEATURE_RESTORE_REQUIRED=true
    restore_feature_state
    rm -f -- "${PRESTATE_FILE}"
    printf 'DEPLOYMENT_RECOVERY_EXECUTED=true\n'
  else
    printf 'DEPLOYMENT_RECOVERY_EXECUTED=false\n'
  fi
  printf 'RUNTIME_DEPLOY_EXECUTED=false\n'
  printf 'PROMPT_AUDIT_EXECUTED=false\n'
  printf 'COORDINATED_BACKUP_EXECUTED=false\n'
  printf 'ELAPSED_SECONDS=%s\n' "$((SECONDS - START_SECONDS))"
  printf 'NANOBOT_COORDINATED_DEPLOY_STATUS=ok\n'
  trap - EXIT
  exit 0
fi

runtime_image="${NANOBOT_RUNTIME_IMAGE:-}"
release_manifest="${NANOBOT_RELEASE_MANIFEST:-}"
production_root="${NANOBOT_PRODUCTION_ROOT:-}"
prompt_root="${NANOBOT_PROMPT_HOST_ROOT:-/var/lib/nanobot/prompt-runtime}"
database="${production_root}/data/nanobot.db"
sandbox_data_root="${NANOBOT_SANDBOX_DATA_ROOT:-/srv/nanobot}"

if [[ "$(jq -r '.prompt_audit_required' <<<"${plan_json}")" == "true" ]]; then
  if [[ -z "${NANOBOT_PROMPT_AUDIT_RECEIPT:-}" ]]; then
    runtime_digest="${runtime_image##*@sha256:}"
    [[ "${runtime_digest}" =~ ^[0-9a-f]{64}$ ]] \
      || die "无法从 Runtime 镜像解析 digest"
    prompt_receipt="${prompt_root}/receipts/prompt-audit-${runtime_digest}.json"
    if [[ ! -f "${prompt_receipt}" ]]; then
      env \
        NANOBOT_RUNTIME_IMAGE="${runtime_image}" \
        NANOBOT_RELEASE_MANIFEST="${release_manifest}" \
        NANOBOT_PROMPT_HOST_ROOT="${prompt_root}" \
        bash "${PROMPT_MANAGER}" verify-release
      printf 'PROMPT_AUDIT_EXECUTED=true\n'
    else
      printf 'PROMPT_AUDIT_EXECUTED=false\n'
    fi
    export NANOBOT_PROMPT_AUDIT_RECEIPT="${prompt_receipt}"
  else
    printf 'PROMPT_AUDIT_EXECUTED=false\n'
  fi
else
  unset NANOBOT_PROMPT_AUDIT_RECEIPT
  printf 'PROMPT_AUDIT_EXECUTED=false\n'
fi

if [[ -f "${PRESTATE_FILE}" && ! -L "${PRESTATE_FILE}" ]]; then
  :
else
  sandbox_status="$(admin_api GET /sandbox/status)"
  settings_status="$(admin_api GET /settings)"
  prestate_json="$(jq -cn \
    --argjson sandbox "$(jq -c \
      '{enabled:.feature.enabled,exec_enabled:.feature.exec_enabled,group_enabled:.feature.group_enabled}' \
      <<<"${sandbox_status}")" \
    --argjson settings "$(jq -c '
      [.settings[]
       | select(.key == "group_learning.enabled"
           or .key == "group_memory.injection_enabled")
       | {key,value,source}]
    ' <<<"${settings_status}")" \
    '{schema_version:1,sandbox:$sandbox,settings:$settings,evidence:{}}')"
  jq -e '.settings|length == 2' <<<"${prestate_json}" >/dev/null \
    || die "无法读取群学习 Feature 前态"
  temporary_prestate="$(mktemp \
    "${RELEASE_STATE_DIR}/.coordinated-prestate.XXXXXX")"
  printf '%s\n' "${prestate_json}" >"${temporary_prestate}"
  chmod 0600 "${temporary_prestate}"
  chown root:root "${temporary_prestate}"
  mv -f -- "${temporary_prestate}" "${PRESTATE_FILE}"
fi

FEATURE_RESTORE_REQUIRED=true
admin_api PUT /sandbox/features \
  '{"enabled":false,"exec_enabled":false,"group_enabled":false,"reason":"Runtime 协调部署维护窗口"}' \
  >/dev/null
for feature_key in group_learning.enabled group_memory.injection_enabled; do
  admin_api PUT "/settings/${feature_key}" '{"value":false}' >/dev/null
done
if docker ps \
    --filter 'label=com.nanobot.sandbox=true' \
    --filter 'label=com.nanobot.managed-by=sandboxd' \
    --format '{{.Names}}' | grep -q .; then
  die "仍有活动 Sandbox 容器，拒绝 Runtime 协调部署"
fi
printf 'FEATURE_QUIESCENCE_STATUS=ok\n'

if [[ "$(jq -r '.coordinated_backup_required' <<<"${plan_json}")" \
    == "true" ]]; then
  if [[ -z "${NANOBOT_COORDINATED_BACKUP_DIR:-}" ]]; then
    backup_dir="$(jq -r \
      '.evidence.coordinated_backup_dir // empty' \
      "${PRESTATE_FILE}")"
    if [[ -n "${backup_dir}" ]]; then
      [[ "${backup_dir}" == /* && -d "${backup_dir}" \
          && ! -L "${backup_dir}" ]] \
        || die "已记录的协调备份目录缺失或不安全；拒绝生成第二份备份"
      export NANOBOT_COORDINATED_BACKUP_DIR="${backup_dir}"
      printf 'COORDINATED_BACKUP_EXECUTED=false\n'
      printf 'COORDINATED_BACKUP_REUSED=true\n'
    else
      [[ -f "${SANDBOX_CONFIG}" && ! -L "${SANDBOX_CONFIG}" \
          && "$(stat -c '%u:%a' "${SANDBOX_CONFIG}")" == "0:600" ]] \
        || die "自动协调备份需要安全的 Sandbox 生产配置"
      # 配置由 root 管理脚本以 printf %q 原子写入。
      # shellcheck disable=SC1090
      source "${SANDBOX_CONFIG}"
      : "${BACKUP_MOUNT:?}" "${BACKUP_MODE:?}" \
        "${BACKUP_RISK_MARKER:?}" "${BACKUP_MAX_BYTES:?}"
      systemctl is-active --quiet nanobot-sandboxd.service \
        || die "协调备份前 sandboxd 未运行"
      for container in "${FIXED_CONTAINERS[@]}"; do
        [[ "$(docker inspect "${container}" --format \
          '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}')" \
          == "healthy" ]] || die "协调备份前固定服务不健康：${container}"
      done
      systemctl stop nanobot-sandboxd.service
      docker stop "${FIXED_CONTAINERS[@]}" >/dev/null
      SERVICES_QUIESCED=true
      backup_output="$(bash "${BACKUP_SCRIPT}" \
        --database "${database}" \
        --destination "${BACKUP_MOUNT}" \
        --data-root "${sandbox_data_root}" \
        --backup-mode "${BACKUP_MODE}" \
        --risk-marker "${BACKUP_RISK_MARKER}" \
        --max-bytes "${BACKUP_MAX_BYTES}" \
        --system-min-free-bytes \
          "${NANOBOT_SYSTEM_MIN_FREE_BYTES:-64424509440}" \
        --quiesced \
        --apply)"
      printf '%s\n' "${backup_output}"
      backup_dir="$(sed -n \
        's/^COORDINATED_BACKUP_DIR=//p' <<<"${backup_output}" | tail -n 1)"
      [[ "${backup_dir}" == /* && -d "${backup_dir}" \
          && ! -L "${backup_dir}" ]] \
        || die "协调备份未返回安全的结果目录"
      update_prestate_evidence coordinated_backup_dir "${backup_dir}"
      export NANOBOT_COORDINATED_BACKUP_DIR="${backup_dir}"
      restore_services
      wait_fixed_services || die "协调备份后固定服务未恢复健康"
      printf 'COORDINATED_BACKUP_EXECUTED=true\n'
      printf 'COORDINATED_BACKUP_REUSED=false\n'
    fi
  else
    backup_dir="${NANOBOT_COORDINATED_BACKUP_DIR}"
    [[ "${backup_dir}" == /* && -d "${backup_dir}" \
        && ! -L "${backup_dir}" ]] \
      || die "显式协调备份目录缺失或不安全"
    update_prestate_evidence coordinated_backup_dir "${backup_dir}"
    printf 'COORDINATED_BACKUP_EXECUTED=false\n'
    printf 'COORDINATED_BACKUP_REUSED=true\n'
  fi
else
  unset NANOBOT_COORDINATED_BACKUP_DIR
  printf 'COORDINATED_BACKUP_EXECUTED=false\n'
fi

NANOBOT_DEPLOY_PLAN_ONLY=false bash "${BASE_DEPLOY}"
restore_feature_state
rm -f -- "${PRESTATE_FILE}"

ready_response="$(curl --fail --silent --show-error --max-time 5 \
  http://127.0.0.1:8000/api/v1/ready)"
jq -e '.ready == true and .checks.database == true and .checks.prompt_runtime == true and .checks.bridge == true' \
  <<<"${ready_response}" >/dev/null \
  || die "Runtime 协调部署后 readiness 无效"
printf 'RUNTIME_DEPLOY_EXECUTED=true\n'
printf 'READY_RESPONSE=%s\n' "${ready_response}"
printf 'ELAPSED_SECONDS=%s\n' "$((SECONDS - START_SECONDS))"
printf 'NANOBOT_COORDINATED_DEPLOY_STATUS=ok\n'
trap - EXIT
