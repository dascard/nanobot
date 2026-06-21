#!/usr/bin/env bash
set -uo pipefail

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

export PYTHONDONTWRITEBYTECODE=1
export NANOBOT_TESTING="${NANOBOT_TESTING:-1}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///:memory:}"
export NEW_API_KEY="${NEW_API_KEY:-test-key-for-ci}"
export NANOBOT_ADMIN_TOKEN="${NANOBOT_ADMIN_TOKEN:-test-admin-token}"

status=0
PERIODIC_STARTED_AT="$(python - <<'PY'
from datetime import datetime
print(datetime.now().astimezone().isoformat(timespec="seconds"))
PY
)"
PERIODIC_REPORT_DATE="${PERIODIC_STARTED_AT:0:10}"
if [[ -n "${GITHUB_RUN_ID:-}" ]]; then
  PERIODIC_RUN_ID="${PERIODIC_RUN_ID:-${GITHUB_RUN_ID}_${GITHUB_RUN_ATTEMPT:-1}}"
else
  PERIODIC_RUN_ID="${PERIODIC_RUN_ID:-$(date +%Y%m%d_%H%M%S)_local}"
fi
export PERIODIC_RUN_ID
PERIODIC_STEPS_JSONL="${PERIODIC_STEPS_JSONL:-tmp/eval_periodic/${PERIODIC_RUN_ID}/steps.jsonl}"
TIMING_SIGNAL_AUDIT_LATEST_OUT="${TIMING_SIGNAL_AUDIT_OUT:-evals/reports/timing_signal_audit_latest.json}"
TIMING_SIGNAL_AUDIT_DATED_OUT="evals/reports/${PERIODIC_REPORT_DATE}-timing_signal_audit.json"
TIMING_SIGNAL_AUDIT_RUN_OUT="evals/reports/runs/${PERIODIC_RUN_ID}/timing_signal_audit.json"
TIMING_SIGNAL_AUDIT_EXTRA_OUTS_PREFIX="${TIMING_SIGNAL_AUDIT_RUN_OUT}:${TIMING_SIGNAL_AUDIT_DATED_OUT}"
export TIMING_SIGNAL_AUDIT_OUT="$TIMING_SIGNAL_AUDIT_LATEST_OUT"
export TIMING_SIGNAL_AUDIT_EXTRA_OUTS="${TIMING_SIGNAL_AUDIT_EXTRA_OUTS_PREFIX}${TIMING_SIGNAL_AUDIT_EXTRA_OUTS:+:${TIMING_SIGNAL_AUDIT_EXTRA_OUTS}}"
mkdir -p "$(dirname "$PERIODIC_STEPS_JSONL")"
: > "$PERIODIC_STEPS_JSONL"

record_step() {
  local name="$1"
  local kind="$2"
  local suite="$3"
  local exit_code="$4"
  local baseline_path="$5"
  local report_paths="$6"
  python - "$PERIODIC_STEPS_JSONL" "$name" "$kind" "$suite" "$exit_code" "$baseline_path" "$report_paths" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
paths = [item for item in sys.argv[7].split("|") if item]
payload = {
    "name": sys.argv[2],
    "kind": sys.argv[3],
    "suite": sys.argv[4],
    "exit_code": int(sys.argv[5]),
    "baseline_path": sys.argv[6],
    "report_paths": paths,
}
with out.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
PY
}

run_step() {
  local name="$1"
  local kind="$2"
  local suite="$3"
  local baseline_path="$4"
  local report_paths="$5"
  shift 5
  echo "==> ${name}"
  if "$@"; then
    echo "==> ${name}: passed"
    record_step "$name" "$kind" "$suite" "0" "$baseline_path" "$report_paths"
    return 0
  fi
  local step_status=$?
  echo "==> ${name}: failed"
  status=1
  record_step "$name" "$kind" "$suite" "$step_status" "$baseline_path" "$report_paths"
  return 0
}

run_step "eval guard tests" "pytest_guard" "eval_guard" "" "" \
  python -B -m pytest \
    tests/test_eval_baseline.py \
    tests/test_timing_gate_prompt_policy.py \
    -v \
    -p no:cacheprovider

run_step "timing gate" "eval_suite" "timing_gate" \
  "evals/baselines/timing_gate.json" \
  "evals/reports/${PERIODIC_REPORT_DATE}-timing_gate.json" \
  bash scripts/run_timing_gate_gate.sh

run_step "capability model routing" "eval_suite" "capability_model_routing" \
  "evals/baselines/capability_model_routing.json" \
  "evals/reports/${PERIODIC_REPORT_DATE}-capability_model_routing.json" \
  python -B -m evals.run \
    --suite capability_model_routing \
    --baseline evals/baselines/capability_model_routing.json \
    --min-pass-rate 1.0 \
    --max-new-failures 0

run_step "capability reply contract" "eval_suite" "capability_reply_contract" \
  "evals/baselines/capability_reply_contract.json" \
  "evals/reports/${PERIODIC_REPORT_DATE}-capability_reply_contract.json" \
  python -B -m evals.run \
    --suite capability_reply_contract \
    --baseline evals/baselines/capability_reply_contract.json \
    --min-pass-rate 1.0 \
    --max-new-failures 0

run_step "capability rendering contract" "eval_suite" "capability_rendering_contract" \
  "evals/baselines/capability_rendering_contract.json" \
  "evals/reports/${PERIODIC_REPORT_DATE}-capability_rendering_contract.json" \
  python -B -m evals.run \
    --suite capability_rendering_contract \
    --baseline evals/baselines/capability_rendering_contract.json \
    --min-pass-rate 1.0 \
    --max-new-failures 0

run_step "rag benchmark manual fixture deterministic gate" "rag_benchmark" "rag_benchmark" \
  "evals/baselines/rag_benchmark.json" \
  "tmp/rag_benchmark/reports/latest.json|tmp/rag_benchmark/reports/latest.md" \
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

run_step "timing signal audit" "timing_signal_audit" "timing_signal_audit" \
  "" \
  "$TIMING_SIGNAL_AUDIT_RUN_OUT|$TIMING_SIGNAL_AUDIT_DATED_OUT|$TIMING_SIGNAL_AUDIT_LATEST_OUT" \
  bash scripts/run_timing_signal_audit_periodic.sh

PERIODIC_FINISHED_AT="$(python - <<'PY'
from datetime import datetime
print(datetime.now().astimezone().isoformat(timespec="seconds"))
PY
)"
python -B -m evals.periodic_manifest \
  --steps "$PERIODIC_STEPS_JSONL" \
  --run-id "$PERIODIC_RUN_ID" \
  --started-at "$PERIODIC_STARTED_AT" \
  --finished-at "$PERIODIC_FINISHED_AT" \
  --exit-code "$status" \
  --trigger "${GITHUB_EVENT_NAME:-local}" \
  --reports-dir evals/reports \
  --git-sha "${GITHUB_SHA:-}" \
  --git-ref "${GITHUB_REF:-}" \
  --git-repository "${GITHUB_REPOSITORY:-}"

# Manifest helper writes:
# - evals/reports/periodic_manifest_latest.json
# - evals/reports/${PERIODIC_REPORT_DATE}-periodic_manifest.json
# - evals/reports/runs/${PERIODIC_RUN_ID}/manifest.json
exit "$status"
