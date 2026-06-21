"""TimingGate 信号周期审计脚本测试。"""

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_timing_signal_audit_periodic_script_skips_missing_db(tmp_path):
    latest = tmp_path / "reports" / "timing_signal_audit_latest.json"
    dated = tmp_path / "reports" / "2026-06-20-timing_signal_audit.json"
    run_scoped = tmp_path / "reports" / "runs" / "unit_run" / "timing_signal_audit.json"
    missing_db = tmp_path / "missing.db"
    env = {
        **os.environ,
        "TIMING_SIGNAL_AUDIT_DB": str(missing_db),
        "TIMING_SIGNAL_AUDIT_OUT": str(latest),
        "TIMING_SIGNAL_AUDIT_EXTRA_OUTS": f"{run_scoped}:{dated}",
        "TIMING_SIGNAL_AUDIT_LIMIT": "17",
        "TIMING_SIGNAL_AUDIT_AFTER_ID": "5",
        "PERIODIC_RUN_ID": "unit_run",
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
    assert latest.exists()
    assert dated.exists()
    assert run_scoped.exists()

    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert json.loads(dated.read_text(encoding="utf-8")) == payload
    assert json.loads(run_scoped.read_text(encoding="utf-8")) == payload
    assert payload["total_samples"] == 0
    assert payload["samples"] == []
    assert payload["source"]["mode"] == "skipped"
    assert payload["source"]["reason"] == "db_not_found"
    assert payload["source"]["db"] == str(missing_db)
    assert payload["source"]["after_id"] == 5
    assert payload["source"]["limit"] == 17
    assert payload["source"]["run_id"] == "unit_run"


def test_eval_periodic_script_runs_timing_signal_audit_step():
    text = Path("scripts/run_eval_periodic.sh").read_text(encoding="utf-8")

    assert "timing signal audit" in text
    assert "scripts/run_timing_signal_audit_periodic.sh" in text


def test_periodic_script_indexes_timing_signal_audit_report():
    text = Path("scripts/run_eval_periodic.sh").read_text(encoding="utf-8")

    assert "timing signal audit" in text
    assert "timing_signal_audit" in text
    assert "evals/reports/timing_signal_audit_latest.json" in text
