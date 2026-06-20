import json

from tests.test_eval_candidate_contract import _insert_candidate, _redirect_promote_root


class _SessionWrapper:
    def __init__(self, db_session):
        self._db_session = db_session

    def __getattr__(self, name):
        return getattr(self._db_session, name)

    def close(self):
        pass


def test_export_candidates_writes_jsonl(db_session, tmp_path):
    from evals.candidates import export_candidates

    _insert_candidate(db_session)
    out = tmp_path / "candidates.jsonl"

    count = export_candidates(db_session, out, suite="timing_gate", status="candidate")

    assert count == 1
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["case_id"] == "cand_timing_gate_1"
    assert row["expected"] == {"needs_label": True}


def test_export_candidates_supports_deferred_and_rejected_statuses(db_session, tmp_path):
    from core.eval_sampling.store import defer_candidate, reject_candidate
    from evals.candidates import export_candidates

    _insert_candidate(db_session, case_id="cand_deferred")
    defer_candidate(db_session, "cand_deferred", reason_code="needs_more_context")
    _insert_candidate(db_session, case_id="cand_rejected")
    reject_candidate(db_session, "cand_rejected", reason_code="low_value")

    deferred_path = tmp_path / "deferred.jsonl"
    rejected_path = tmp_path / "rejected.jsonl"

    assert export_candidates(db_session, deferred_path, status="deferred") == 1
    assert export_candidates(db_session, rejected_path, status="rejected") == 1
    assert json.loads(deferred_path.read_text(encoding="utf-8"))["status"] == "deferred"
    assert json.loads(rejected_path.read_text(encoding="utf-8"))["status"] == "rejected"


def test_import_labels_updates_expected_and_note(db_session, tmp_path):
    from core.eval_sampling.store import get_candidate
    from evals.candidates import import_labels

    _insert_candidate(db_session)
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps(
            {
                "case_id": "cand_timing_gate_1",
                "expected": {"timing_action": "continue"},
                "note": "人工确认",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = import_labels(db_session, labels)

    row = get_candidate(db_session, "cand_timing_gate_1")
    assert result == {"updated": 1}
    assert json.loads(row.expected_json) == {"timing_action": "continue"}
    assert row.note == "人工确认"


def test_promote_labeled_dry_run_and_apply(db_session, tmp_path, monkeypatch):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import get_candidate, label_candidate
    from evals.candidates import promote_labeled

    _insert_candidate(db_session)
    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})
    target = tmp_path / "evals" / "cases" / "timing_gate" / "cand_timing_gate_1.json"

    dry_run = promote_labeled(
        db_session,
        suite="timing_gate",
        target_dataset="timing_gate",
        apply=False,
    )

    assert dry_run["count"] == 1
    assert dry_run["ready"] == 1
    assert dry_run["blocked"] == 0
    assert dry_run["applied"] == 0
    assert dry_run["ok"] is True
    assert dry_run["items"][0]["case_id"] == "cand_timing_gate_1"
    assert dry_run["items"][0]["target_dataset"] == "timing_gate"
    assert not target.exists()
    assert get_candidate(db_session, "cand_timing_gate_1").status == "labeled"

    applied = promote_labeled(
        db_session,
        suite="timing_gate",
        target_dataset="timing_gate",
        apply=True,
    )

    assert applied == {
        "ok": True,
        "count": 1,
        "ready": 1,
        "blocked": 0,
        "applied": 1,
        "items": [{"case_id": "cand_timing_gate_1", "path": str(target)}],
    }
    assert target.exists()
    assert get_candidate(db_session, "cand_timing_gate_1").status == "promoted"


def test_promote_labeled_dry_run_reports_ready_and_blocked_without_writing(
    db_session,
    tmp_path,
    monkeypatch,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import get_candidate, label_candidate
    from evals.candidates import promote_labeled

    _insert_candidate(db_session, case_id="cand_ready")
    label_candidate(db_session, "cand_ready", {"timing_action": "continue"})
    _insert_candidate(db_session, case_id="cand_error", suite="error")
    label_candidate(db_session, "cand_error", {"timing_action": "continue"})
    target = tmp_path / "evals" / "cases" / "timing_gate" / "cand_ready.json"

    dry_run = promote_labeled(
        db_session,
        target_dataset="timing_gate",
        apply=False,
    )

    assert dry_run["count"] == 2
    assert dry_run["ready"] == 1
    assert dry_run["blocked"] == 1
    assert dry_run["applied"] == 0
    assert dry_run["ok"] is False
    by_id = {item["case_id"]: item for item in dry_run["items"]}
    assert by_id["cand_ready"]["ready"] is True
    assert by_id["cand_error"]["ready"] is False
    assert by_id["cand_error"]["error"] == "suite_not_runnable"
    assert not target.exists()
    assert get_candidate(db_session, "cand_ready").status == "labeled"


def test_promote_labeled_apply_rejects_blocked_batch_without_partial_write(
    db_session,
    tmp_path,
    monkeypatch,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import get_candidate, label_candidate
    from evals.candidates import promote_labeled

    _insert_candidate(db_session, case_id="cand_ready")
    label_candidate(db_session, "cand_ready", {"timing_action": "continue"})
    _insert_candidate(db_session, case_id="cand_error", suite="error")
    label_candidate(db_session, "cand_error", {"timing_action": "continue"})
    target = tmp_path / "evals" / "cases" / "timing_gate" / "cand_ready.json"

    result = promote_labeled(
        db_session,
        target_dataset="timing_gate",
        apply=True,
    )

    assert result["count"] == 2
    assert result["ready"] == 1
    assert result["blocked"] == 1
    assert result["applied"] == 0
    assert result["ok"] is False
    assert not target.exists()
    assert get_candidate(db_session, "cand_ready").status == "labeled"


def test_candidates_cli_audit_writes_read_only_batch_report(db_session, tmp_path, monkeypatch):
    from core.database import AdminAuditLog
    from core.eval_sampling.store import get_candidate, label_candidate
    from evals import candidates

    _redirect_promote_root(monkeypatch, tmp_path)
    _insert_candidate(db_session, case_id="cand_cli_audit")
    label_candidate(db_session, "cand_cli_audit", {"timing_action": "continue"})
    monkeypatch.setattr(candidates, "_open_db", lambda: _SessionWrapper(db_session))

    out = tmp_path / "candidate-audit.json"
    exit_code = candidates.main([
        "audit",
        "--suite",
        "timing_gate",
        "--status",
        "labeled",
        "--target-dataset",
        "timing_gate",
        "--out",
        str(out),
    ])

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["total"] == 1
    assert payload["counts"]["by_status"]["labeled"] == 1
    assert payload["items"][0]["readiness"]["ready"] is True
    assert db_session.query(AdminAuditLog).count() == 0
    assert get_candidate(db_session, "cand_cli_audit").status == "labeled"


def test_candidates_cli_trend_writes_read_only_report(db_session, tmp_path, monkeypatch, capsys):
    from core.database import AdminAuditLog
    from core.eval_sampling.store import get_candidate
    from evals import candidates

    _insert_candidate(db_session, case_id="cand_cli_trend")
    monkeypatch.setattr(candidates, "_open_db", lambda: _SessionWrapper(db_session))

    out = tmp_path / "candidate-trend.json"
    exit_code = candidates.main([
        "trend",
        "--days",
        "30",
        "--suite",
        "timing_gate",
        "--source",
        "db",
        "--out",
        str(out),
    ])

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["readiness"]["blocked"] == 1
    assert "ready=" in capsys.readouterr().out
    assert db_session.query(AdminAuditLog).count() == 0
    assert get_candidate(db_session, "cand_cli_trend").status == "candidate"


def test_candidates_cli_main_exports_candidates(db_session, tmp_path, monkeypatch, capsys):
    from evals import candidates

    _insert_candidate(db_session)
    out = tmp_path / "candidates.jsonl"
    monkeypatch.setattr(candidates, "_open_db", lambda: _SessionWrapper(db_session))

    exit_code = candidates.main(
        ["export", "--suite", "timing_gate", "--status", "candidate", "--out", str(out)]
    )

    assert exit_code == 0
    assert "exported=1" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8").strip())["case_id"] == "cand_timing_gate_1"
