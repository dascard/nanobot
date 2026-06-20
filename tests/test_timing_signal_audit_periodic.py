"""TimingGate 信号周期审计脚本测试。"""

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_timing_signal_audit_periodic_script_skips_missing_db(tmp_path):
    out = tmp_path / "reports" / "timing_signal_audit_latest.json"
    missing_db = tmp_path / "missing.db"
    env = {
        **os.environ,
        "TIMING_SIGNAL_AUDIT_DB": str(missing_db),
        "TIMING_SIGNAL_AUDIT_OUT": str(out),
        "TIMING_SIGNAL_AUDIT_LIMIT": "17",
        "TIMING_SIGNAL_AUDIT_AFTER_ID": "5",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    result = subprocess.run(
        ["bash", "scripts/run_timing_signal_audit_periodic.sh"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["total_samples"] == 0
    assert payload["samples"] == []
    assert payload["source"]["mode"] == "skipped"
    assert payload["source"]["reason"] == "db_not_found"
    assert payload["source"]["db"] == str(missing_db)
    assert payload["source"]["after_id"] == 5
    assert payload["source"]["limit"] == 17


def test_eval_periodic_script_runs_timing_signal_audit_step():
    text = Path("scripts/run_eval_periodic.sh").read_text(encoding="utf-8")

    assert "timing signal audit" in text
    assert "scripts/run_timing_signal_audit_periodic.sh" in text
