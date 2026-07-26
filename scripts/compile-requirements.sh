#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if ! command -v uv >/dev/null 2>&1; then
  echo "缺少 uv，无法生成依赖锁文件。" >&2
  exit 1
fi

compile_args=(
  --python-version 3.11
  --python-platform x86_64-manylinux_2_36
  --torch-backend cpu
  --no-emit-package kohakuterrarium
  --emit-index-url
  --emit-index-annotation
  --custom-compile-command scripts/compile-requirements.sh
)

uv pip compile requirements.txt \
  "${compile_args[@]}" \
  --output-file requirements-prod.lock

uv pip compile requirements-test.txt \
  "${compile_args[@]}" \
  --output-file requirements-test.lock

uv pip compile docker/sandbox/python/requirements.in \
  --python-version 3.11 \
  --python-platform x86_64-manylinux_2_36 \
  --generate-hashes \
  --emit-index-url \
  --emit-index-annotation \
  --custom-compile-command scripts/compile-requirements.sh \
  --output-file docker/sandbox/python/requirements.lock

uv pip compile docker/sandbox/developer/requirements.in \
  --python-version 3.11 \
  --python-platform x86_64-manylinux_2_36 \
  --generate-hashes \
  --emit-index-url \
  --emit-index-annotation \
  --custom-compile-command scripts/compile-requirements.sh \
  --output-file docker/sandbox/developer/requirements.lock

uv pip compile requirements-sandboxd.in \
  --python-version 3.11 \
  --python-platform x86_64-manylinux_2_36 \
  --generate-hashes \
  --emit-index-url \
  --emit-index-annotation \
  --custom-compile-command scripts/compile-requirements.sh \
  --output-file requirements-sandboxd.lock

uv pip compile requirements-sandbox-smoke.in \
  --python-version 3.11 \
  --python-platform x86_64-manylinux_2_36 \
  --generate-hashes \
  --emit-index-url \
  --emit-index-annotation \
  --custom-compile-command scripts/compile-requirements.sh \
  --output-file requirements-sandbox-smoke.lock

# 新版 CPU wheel 在 uv 锁中带 +cpu，本身的公开版本在 pip 索引中不带该后缀。
# 安装入口会从 CPU 专用索引单独安装 torch，因此锁文件统一保留公开版本号。
sed -E -i 's/^(torch==[^+[:space:]]+)\+cpu$/\1/' \
  requirements-prod.lock requirements-test.lock
