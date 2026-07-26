"""采样限流——per-suite 待标注候选上限,防止 memory_learning 式无限积压。"""
from tests.test_eval_candidate_contract import _insert_candidate


def _candidate_dict(case_id: str, suite: str) -> dict:
    return {
        "case_id": case_id,
        "suite": suite,
        "source": "db",
        "source_ref": f"chatlog:{case_id}",
        "description": "sampled",
        "input": {"message": "测试消息"},
        "expected": {"needs_label": True},
        "tags": ["sampled", suite],
        "fingerprint": f"fp-{case_id}",
    }


def test_candidate_gate_skips_when_suite_pending_reaches_cap(db_session):
    from core.eval_sampling.scheduler import CandidateGate

    gate = CandidateGate(db_session, max_pending=2)

    assert gate.insert(_candidate_dict("cand_a", "memory_learning")) is True
    assert gate.insert(_candidate_dict("cand_b", "memory_learning")) is True
    assert gate.insert(_candidate_dict("cand_c", "memory_learning")) is False
    assert gate.insert(_candidate_dict("cand_d", "timing_gate")) is True
    assert gate.created == 3
    assert gate.skipped == {"memory_learning": 1}


def test_candidate_gate_counts_existing_pending_rows(db_session):
    from core.eval_sampling.scheduler import CandidateGate

    _insert_candidate(db_session, case_id="cand_seed_1", suite="memory_learning")
    _insert_candidate(db_session, case_id="cand_seed_2", suite="memory_learning")

    gate = CandidateGate(db_session, max_pending=2)

    assert gate.insert(_candidate_dict("cand_new", "memory_learning")) is False
    assert gate.created == 0
    assert gate.skipped == {"memory_learning": 1}


def test_candidate_gate_unlimited_when_cap_zero(db_session):
    from core.eval_sampling.scheduler import CandidateGate

    gate = CandidateGate(db_session, max_pending=0)

    for i in range(5):
        assert gate.insert(_candidate_dict(f"cand_{i}", "memory_learning")) is True
    assert gate.created == 5
    assert gate.skipped == {}


def test_candidate_gate_fingerprint_duplicate_does_not_count_created(db_session):
    from core.eval_sampling.scheduler import CandidateGate

    gate = CandidateGate(db_session, max_pending=10)

    assert gate.insert(_candidate_dict("cand_dup", "timing_gate")) is True
    assert gate.insert(_candidate_dict("cand_dup", "timing_gate")) is False
    assert gate.created == 1
    assert gate.skipped == {}


def test_count_pending_by_suite_only_counts_candidate_status(db_session):
    from core.eval_sampling.store import count_pending_by_suite, label_candidate

    _insert_candidate(db_session, case_id="cand_p1", suite="memory_learning")
    _insert_candidate(db_session, case_id="cand_p2", suite="memory_learning")
    _insert_candidate(db_session, case_id="cand_t1", suite="timing_gate")
    label_candidate(db_session, "cand_t1", {"timing_action": "continue"})

    counts = count_pending_by_suite(db_session)

    assert counts == {"memory_learning": 2}
