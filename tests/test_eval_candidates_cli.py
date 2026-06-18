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

    assert applied == {"count": 1, "items": [{"case_id": "cand_timing_gate_1", "path": str(target)}]}
    assert target.exists()
    assert get_candidate(db_session, "cand_timing_gate_1").status == "promoted"


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
