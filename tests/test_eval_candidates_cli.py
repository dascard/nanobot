import json

from tests.test_eval_candidate_contract import _insert_candidate, _redirect_promote_root


class _SessionWrapper:
    def __init__(self, db_session):
        self._db_session = db_session

    def __getattr__(self, name):
        # 护栏:_db_session 自身缺失时直接抛错,避免 __getattr__ 自引用无限递归
        if name == "_db_session":
            raise AttributeError(name)
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


def test_promote_labeled_with_cases_root_writes_into_given_dir(db_session, tmp_path, monkeypatch):
    _redirect_promote_root(monkeypatch, tmp_path / "data_root")
    from core.eval_sampling.store import get_candidate, label_candidate
    from evals.candidates import promote_labeled

    _insert_candidate(db_session)
    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})
    repo_cases = tmp_path / "repo" / "evals" / "cases"

    applied = promote_labeled(
        db_session,
        suite="timing_gate",
        target_dataset="timing_gate",
        apply=True,
        cases_root=repo_cases,
    )

    target = repo_cases / "timing_gate" / "cand_timing_gate_1.json"
    assert applied["ok"] is True
    assert applied["applied"] == 1
    assert applied["items"] == [{"case_id": "cand_timing_gate_1", "path": str(target)}]
    assert target.exists()
    assert not (tmp_path / "data_root" / "evals" / "cases").exists()
    assert get_candidate(db_session, "cand_timing_gate_1").status == "promoted"


def test_export_promoted_cases_rebuilds_missing_files_and_skips_existing(
    db_session, tmp_path, monkeypatch
):
    _redirect_promote_root(monkeypatch, tmp_path / "data_root")
    from core.eval_sampling.store import (
        export_promoted_cases,
        label_candidate,
        promote_candidate,
    )

    _insert_candidate(db_session)
    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})
    promote_candidate(db_session, "cand_timing_gate_1", target_dataset="timing_gate")
    repo_cases = tmp_path / "repo" / "evals" / "cases"

    first = export_promoted_cases(db_session, cases_root=repo_cases)

    target = repo_cases / "timing_gate" / "cand_timing_gate_1.json"
    assert first["total"] == 1
    assert first["written"] == [{"case_id": "cand_timing_gate_1", "path": str(target)}]
    assert first["skipped"] == []
    assert first["invalid"] == []
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["id"] == "cand_timing_gate_1"
    assert payload["suite"] == "timing_gate"
    assert payload["expected"] == {"timing_action": "continue"}
    assert "promoted" in payload["tags"]
    assert payload["meta"]["origin"] == "eval_candidate"

    second = export_promoted_cases(db_session, cases_root=repo_cases)

    assert second["written"] == []
    assert second["skipped"] == [
        {"case_id": "cand_timing_gate_1", "path": str(target), "reason": "target_exists"}
    ]


def test_candidates_cli_main_export_cases_writes_case_files(
    db_session, tmp_path, monkeypatch, capsys
):
    from evals import candidates

    _redirect_promote_root(monkeypatch, tmp_path / "data_root")
    from core.eval_sampling.store import label_candidate, promote_candidate

    _insert_candidate(db_session)
    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})
    promote_candidate(db_session, "cand_timing_gate_1", target_dataset="timing_gate")
    monkeypatch.setattr(candidates, "_open_db", lambda: _SessionWrapper(db_session))
    repo_cases = tmp_path / "repo" / "evals" / "cases"

    exit_code = candidates.main(["export-cases", "--cases-root", str(repo_cases)])

    assert exit_code == 0
    assert "written=1" in capsys.readouterr().out
    assert (repo_cases / "timing_gate" / "cand_timing_gate_1.json").exists()


def test_candidates_cli_main_promote_supports_cases_root(
    db_session, tmp_path, monkeypatch, capsys
):
    from evals import candidates

    _redirect_promote_root(monkeypatch, tmp_path / "data_root")
    from core.eval_sampling.store import get_candidate, label_candidate

    _insert_candidate(db_session)
    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})
    monkeypatch.setattr(candidates, "_open_db", lambda: _SessionWrapper(db_session))
    repo_cases = tmp_path / "repo" / "evals" / "cases"

    exit_code = candidates.main(
        [
            "promote",
            "--suite", "timing_gate",
            "--target-dataset", "timing_gate",
            "--apply",
            "--cases-root", str(repo_cases),
        ]
    )

    assert exit_code == 0
    assert (repo_cases / "timing_gate" / "cand_timing_gate_1.json").exists()
    assert get_candidate(db_session, "cand_timing_gate_1").status == "promoted"


def test_bulk_reject_candidates_dry_run_counts_without_mutation(db_session):
    from core.database import AdminAuditLog
    from core.eval_sampling.store import bulk_reject_candidates, get_candidate

    _insert_candidate(db_session, case_id="cand_bulk_1", suite="memory_learning")
    _insert_candidate(db_session, case_id="cand_bulk_2", suite="memory_learning")

    result = bulk_reject_candidates(
        db_session, suite="memory_learning", reason_code="low_value", apply=False
    )

    assert result["matched"] == 2
    assert result["rejected"] == 0
    assert result["audit_log_id"] is None
    assert get_candidate(db_session, "cand_bulk_1").status == "candidate"
    assert db_session.query(AdminAuditLog).count() == 0


def test_bulk_reject_candidates_apply_rejects_and_audits(db_session):
    from core.database import AdminAuditLog
    from core.eval_sampling.store import bulk_reject_candidates, get_candidate

    _insert_candidate(db_session, case_id="cand_bulk_1", suite="memory_learning")
    _insert_candidate(db_session, case_id="cand_bulk_2", suite="memory_learning")
    _insert_candidate(db_session, case_id="cand_keep", suite="timing_gate")

    result = bulk_reject_candidates(
        db_session,
        suite="memory_learning",
        reason_code="low_value",
        note="积压清理",
        apply=True,
    )

    assert result["matched"] == 2
    assert result["rejected"] == 2
    assert result["audit_log_id"] is not None
    assert get_candidate(db_session, "cand_bulk_1").status == "rejected"
    assert get_candidate(db_session, "cand_bulk_2").status == "rejected"
    assert get_candidate(db_session, "cand_keep").status == "candidate"
    audit = db_session.query(AdminAuditLog).one()
    assert audit.action == "bulk_reject_eval_candidates"
    detail = json.loads(audit.detail_json)
    assert detail["reason_code"] == "low_value"
    assert detail["rejected"] == 2


def test_bulk_reject_candidates_respects_created_before(db_session):
    from datetime import datetime

    from core.eval_sampling.store import bulk_reject_candidates, get_candidate

    _insert_candidate(db_session, case_id="cand_old", suite="memory_learning")
    _insert_candidate(db_session, case_id="cand_new", suite="memory_learning")
    old = get_candidate(db_session, "cand_old")
    old.created_at = datetime(2026, 6, 1, 0, 0, 0)
    new = get_candidate(db_session, "cand_new")
    new.created_at = datetime(2026, 7, 20, 0, 0, 0)
    db_session.commit()

    result = bulk_reject_candidates(
        db_session,
        suite="memory_learning",
        created_before="2026-07-01",
        reason_code="low_value",
        apply=True,
    )

    assert result["rejected"] == 1
    assert get_candidate(db_session, "cand_old").status == "rejected"
    assert get_candidate(db_session, "cand_new").status == "candidate"


def test_candidates_cli_main_batch_reject(db_session, monkeypatch, capsys):
    from evals import candidates
    from core.eval_sampling.store import get_candidate

    _insert_candidate(db_session, case_id="cand_bulk_cli", suite="memory_learning")
    monkeypatch.setattr(candidates, "_open_db", lambda: _SessionWrapper(db_session))

    exit_code = candidates.main(
        ["batch-reject", "--suite", "memory_learning", "--reason-code", "low_value", "--apply"]
    )

    assert exit_code == 0
    assert "rejected=1" in capsys.readouterr().out
    assert get_candidate(db_session, "cand_bulk_cli").status == "rejected"
