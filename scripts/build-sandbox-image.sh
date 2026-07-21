#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
用法：scripts/build-sandbox-image.sh <version> [docker build 参数...]

构建 nanobot-sandbox-python:<version>，执行只读根、断网、非 root 冒烟验证，
并把 inspect/history 证据保存到用户缓存目录。禁止使用 latest 作为版本。
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

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
context_dir="${repo_root}/docker/sandbox/python"
image="nanobot-sandbox-python:${version}"

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

docker run --rm \
  --name "nanobot-sandbox-image-smoke-${version//[^0-9A-Za-z_.-]/-}" \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit 128 \
  --memory 512m \
  --memory-swap 512m \
  --cpus 1 \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=128m \
  --tmpfs /workspace:rw,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700 \
  --tmpfs /runtime:rw,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700 \
  "${image}" \
  /bin/sh -c '
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

evidence_dir="${XDG_CACHE_HOME:-${HOME}/.cache}/nanobot-sandbox-images"
mkdir -p "${evidence_dir}"
docker image inspect "${image}" >"${evidence_dir}/${version}.inspect.json"
docker history --no-trunc "${image}" >"${evidence_dir}/${version}.history.txt"

echo "Sandbox 镜像构建和冒烟验证通过：${image}"
echo "镜像内容摘要：${image_id}"
echo "证据目录：${evidence_dir}"
