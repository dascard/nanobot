#!/usr/bin/env bash
set -euo pipefail

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

export PYTHONDONTWRITEBYTECODE=1

DB="${TIMING_SIGNAL_AUDIT_DB:-data/nanobot.db}"
OUT="${TIMING_SIGNAL_AUDIT_OUT:-evals/reports/timing_signal_audit_latest.json}"
LIMIT="${TIMING_SIGNAL_AUDIT_LIMIT:-200}"
AFTER_ID="${TIMING_SIGNAL_AUDIT_AFTER_ID:-0}"
SIGNALS="${TIMING_SIGNAL_AUDIT_SIGNALS:-}"

if [[ ! -f "$DB" ]]; then
  python - "$OUT" "$DB" "$LIMIT" "$AFTER_ID" "$SIGNALS" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

from core.eval_sampling.timing_signal_audit import build_timing_signal_audit_report

out = Path(sys.argv[1])
db = sys.argv[2]
limit = int(sys.argv[3])
after_id = int(sys.argv[4])
signals = [item.strip() for item in sys.argv[5].split(",") if item.strip()]

payload = {
    **build_timing_signal_audit_report([]),
    "samples": [],
    "generated_at": datetime.now().isoformat(timespec="seconds"),
    "source": {
        "mode": "skipped",
        "reason": "db_not_found",
        "db": db,
        "after_id": after_id,
        "limit": limit,
        "signals": signals,
    },
}
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Timing signal audit skipped: db_not_found db={db} out={out}")
PY
  exit 0
fi

args=(
  python -B -m evals.timing_signal_audit
  --db "$DB"
  --out "$OUT"
  --limit "$LIMIT"
  --after-id "$AFTER_ID"
)

if [[ -n "$SIGNALS" ]]; then
  args+=(--signals "$SIGNALS")
fi

"${args[@]}"
