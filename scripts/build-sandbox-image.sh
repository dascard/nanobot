#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
用法：scripts/build-sandbox-image.sh <version> [--profile python|developer] [docker build 参数...]

构建指定 Sandbox Profile 镜像，执行只读根、断网、非 root 冒烟验证，
并把 inspect/history 证据保存到用户缓存目录。默认 Profile 为 python，
禁止使用 latest 作为版本。
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

version="${1:-}"
if [[ -z "${version}" ]]; then
  usage >&2
  exit 2
fi
if [[ "${version}" == "latest" || ! "${version}" =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$ ]]; then
  echo "Sandbox 镜像版本无效，且禁止使用 latest。" >&2
  exit 2
fi
shift

profile="python"
if [[ "${1:-}" == "--profile" ]]; then
  profile="${2:-}"
  [[ -n "${profile}" ]] || {
    echo "--profile 缺少值。" >&2
    exit 2
  }
  shift 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
case "${profile}" in
  python)
    context_dir="${repo_root}/docker/sandbox/python"
    image_repository="nanobot-sandbox-python"
    pids_limit=128
    memory_limit=512m
    cpu_limit=1
    tmpfs_limit=128m
    ;;
  developer)
    context_dir="${repo_root}/docker/sandbox/developer"
    image_repository="nanobot-sandbox-developer"
    pids_limit=512
    memory_limit=2g
    cpu_limit=2
    tmpfs_limit=256m
    ;;
  *)
    echo "未知 Sandbox Profile：${profile}" >&2
    exit 2
    ;;
esac
image="${image_repository}:${version}"

docker build \
  --file "${context_dir}/Dockerfile" \
  --build-arg "SANDBOX_IMAGE_VERSION=${version}" \
  --tag "${image}" \
  "$@" \
  "${context_dir}"

image_id="$(docker image inspect "${image}" --format '{{.Id}}')"
configured_user="$(docker image inspect "${image}" --format '{{.Config.User}}')"
if [[ "${configured_user}" != "10001:10001" ]]; then
  echo "镜像默认用户不符合要求：${configured_user}" >&2
  exit 1
fi

docker_run_args=(
  --rm
  --name "nanobot-sandbox-${profile}-image-smoke-${version//[^0-9A-Za-z_.-]/-}"
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit "${pids_limit}" \
  --memory "${memory_limit}" \
  --memory-swap "${memory_limit}" \
  --cpus "${cpu_limit}" \
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=${tmpfs_limit}" \
  --tmpfs /workspace:rw,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700 \
  --tmpfs /runtime:rw,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700 \
)

if [[ "${profile}" == "python" ]]; then
  docker run "${docker_run_args[@]}" "${image}" /bin/sh -c '
    set -eu
    test "$(id -u)" = "10001"
    test "$(id -g)" = "10001"
    test ! -e /workspace/server.py
    test ! -e /app
    test ! -e /.env
    ! command -v docker >/dev/null 2>&1
    ! test -S /var/run/docker.sock
    ! touch /etc/nanobot-sandbox-smoke 2>/dev/null
    touch /workspace/persistent-output.txt
    touch /runtime/cache-output.txt
    python -c "import numpy, openpyxl, pandas; print(numpy.__version__, openpyxl.__version__, pandas.__version__)"
  '
else
  docker run "${docker_run_args[@]}" "${image}" /bin/bash -lc '
    set -euo pipefail
    test "$(id -u)" = "10001"
    test "$(id -g)" = "10001"
    test "${HOME}" = "/runtime/home"
    test "${XDG_CACHE_HOME}" = "/runtime/cache"
    test "${PIP_CACHE_DIR}" = "/runtime/pip-cache"
    test "${UV_CACHE_DIR}" = "/runtime/uv-cache"
    test "${npm_config_cache}" = "/runtime/npm-cache"
    test "${PYTHONPYCACHEPREFIX}" = "/runtime/pycache"
    test "${TMPDIR}" = "/tmp"
    for required in \
        bash git curl wget rg grep sed find diff patch tar gzip unzip jq \
        make gcc g++ pkg-config python pip pytest node npm ssh; do
      command -v "${required}" >/dev/null
    done
    ! command -v docker >/dev/null 2>&1
    ! command -v sudo >/dev/null 2>&1
    ! test -S /var/run/docker.sock
    ! touch /etc/nanobot-sandbox-smoke 2>/dev/null
    touch /workspace/developer-output.txt
    touch /runtime/cache-output.txt
    python -m venv /runtime/venv-smoke
    python -m pytest --version
    node --version
    npm --version
    if curl --fail --silent --show-error \
        --connect-timeout 1 --max-time 2 https://1.1.1.1/ \
        >/dev/null 2>&1; then
      echo "network=none 下不应访问外部网络" >&2
      exit 1
    fi
    printf "loopback-ok\n" >/workspace/index.html
    python -m http.server 18080 \
      --bind 127.0.0.1 \
      --directory /workspace \
      >/tmp/http-server.log 2>&1 &
    server_pid=$!
    trap "kill ${server_pid} 2>/dev/null || true" EXIT
    loopback_ready=false
    for _attempt in $(seq 1 30); do
      if [[ "$(curl --fail --silent --max-time 1 \
          http://127.0.0.1:18080/index.html 2>/dev/null || true)" \
          == "loopback-ok" ]]; then
        loopback_ready=true
        break
      fi
      sleep 0.1
    done
    [[ "${loopback_ready}" == "true" ]]
    kill "${server_pid}"
    wait "${server_pid}" || true
    trap - EXIT
  '
fi

evidence_dir="${XDG_CACHE_HOME:-${HOME}/.cache}/nanobot-sandbox-images"
mkdir -p "${evidence_dir}"
evidence_prefix="${profile}-${version}"
docker image inspect "${image}" >"${evidence_dir}/${evidence_prefix}.inspect.json"
docker history --no-trunc "${image}" >"${evidence_dir}/${evidence_prefix}.history.txt"

echo "Sandbox 镜像构建和冒烟验证通过：${image}"
echo "镜像内容摘要：${image_id}"
echo "证据目录：${evidence_dir}"
