from core.timing_score import decide_timing


def _case(case_id: str = "at_bot_ack_continue_001") -> dict:
    return {
        "case_id": case_id,
        "input": {
            "text": "好的",
            "is_group": True,
            "is_at_bot": True,
            "trigger_reason": "at_bot",
        },
        "expected": {"timing_action": "continue"},
    }


def _candidate(value: float = 0.5) -> dict:
    return {
        "id": "ack_threshold_soften_v1",
        "param_diff": {"s_ack": value},
        "risk_level": "medium",
    }


def test_simulation_identity_has_no_flips_for_empty_candidates():
    from evals.timing_score_simulation import simulate_timing_candidates

    report = simulate_timing_candidates([_case()], [])

    assert report["case_count"] == 1
    assert report["candidate_count"] == 0
    assert report["flip_count"] == 0
    assert report["flips"] == []
    assert report["aggregates"] == []


def test_simulation_reports_candidate_flip_with_score_breakdown():
    from evals.timing_score_simulation import simulate_timing_candidates

    report = simulate_timing_candidates([_case()], [_candidate()])

    assert report["case_count"] == 1
    assert report["candidate_count"] == 1
    assert report["flip_count"] == 1
    flip = report["flips"][0]
    assert flip["candidate_id"] == "ack_threshold_soften_v1"
    assert flip["case_id"] == "at_bot_ack_continue_001"
    assert flip["expected_action"] == "continue"
    assert set(flip["before"]) >= {
        "action",
        "stage",
        "participation_score",
        "final_score",
        "theta",
        "conflict_score",
    }
    assert set(flip["after"]) >= {
        "action",
        "stage",
        "participation_score",
        "final_score",
        "theta",
        "conflict_score",
    }
    assert flip["before"]["action"] == "no_reply"
    assert flip["after"]["action"] == "continue"
    assert flip["signals"]["sub_signals"]["s_ack"] == 0.5
    assert flip["risk_tag"] == "expected_improved"
    assert report["aggregates"][0]["candidate_id"] == "ack_threshold_soften_v1"
    assert report["aggregates"][0]["flip_count"] == 1


def test_simulation_does_not_mutate_live_timing_defaults():
    from evals.timing_score_simulation import simulate_timing_candidates

    before = decide_timing(
        text="好的",
        is_group=True,
        is_at_bot=True,
        model_hint=None,
    )
    report = simulate_timing_candidates([_case()], [_candidate()])
    after = decide_timing(
        text="好的",
        is_group=True,
        is_at_bot=True,
        model_hint=None,
    )

    assert report["flip_count"] == 1
    assert before.action == "no_reply"
    assert after.action == before.action
    assert after.signals.sub_signals["s_ack"] == before.signals.sub_signals["s_ack"]
