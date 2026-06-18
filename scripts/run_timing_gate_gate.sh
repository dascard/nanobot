#!/usr/bin/env bash
set -euo pipefail

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

export PYTHONDONTWRITEBYTECODE=1
export NANOBOT_TESTING="${NANOBOT_TESTING:-1}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///:memory:}"
export NEW_API_KEY="${NEW_API_KEY:-test-key-for-ci}"
export NANOBOT_ADMIN_TOKEN="${NANOBOT_ADMIN_TOKEN:-test-admin-token}"

python -B -m evals.run \
  --suite timing_gate \
  --baseline evals/baselines/timing_gate.json \
  --min-pass-rate 1.0 \
  --max-new-failures 0
