#!/usr/bin/env bash
set -euo pipefail

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

export PYTHONDONTWRITEBYTECODE=1
export NANOBOT_TESTING="${NANOBOT_TESTING:-1}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///:memory:}"
export NEW_API_KEY="${NEW_API_KEY:-test-key-for-ci}"
export NANOBOT_ADMIN_TOKEN="${NANOBOT_ADMIN_TOKEN:-test-admin-token}"

python -B -m pytest \
  tests/test_eval_baseline.py \
  tests/test_timing_gate_prompt_policy.py \
  -v \
  -p no:cacheprovider

bash scripts/run_timing_gate_gate.sh

python -B -m evals.run \
  --suite capability_model_routing \
  --baseline evals/baselines/capability_model_routing.json \
  --min-pass-rate 1.0 \
  --max-new-failures 0

python -B -m evals.run \
  --suite capability_reply_contract \
  --baseline evals/baselines/capability_reply_contract.json \
  --min-pass-rate 1.0 \
  --max-new-failures 0

python -B -m evals.run \
  --suite capability_rendering_contract \
  --baseline evals/baselines/capability_rendering_contract.json \
  --min-pass-rate 1.0 \
  --max-new-failures 0

python -B -m evals.rag_benchmark.run \
  --manual evals/cases/rag_benchmark/manual \
  --generated tmp/rag_benchmark/empty \
  --provider-mode deterministic \
  --manual-only \
  --fixture positive_v1 \
  --fixture-db tmp/rag_benchmark/fixtures/positive_v1.db \
  --baseline evals/baselines/rag_benchmark.json \
  --min-pass-rate 1.0 \
  --min-hit-at-5 1.0 \
  --min-mrr 1.0 \
  --max-new-failures 0 \
  --max-degraded-rate 0.0 \
  --max-unexpected-source-rate 0.0
