import pytest


def test_validate_expected_rejects_empty_needs_label_and_unknown_key():
    from evals.expected_contract import validate_expected_contract

    for expected in ({}, {"needs_label": True}, {"unscored_field": "x"}):
        with pytest.raises(ValueError):
            validate_expected_contract("timing_gate", expected)


def test_validate_expected_accepts_scored_keys():
    from evals.expected_contract import validate_expected_contract

    validate_expected_contract("timing_gate", {"timing_action": "continue", "should_reply": True})
    validate_expected_contract("model_routing", {"model_used": "vision-model"})


def test_sticker_expected_fields_are_scored():
    from evals.schema import EvalCase, EvalOutput
    from evals.scorers import score_case

    case = EvalCase(
        id="sticker_contract",
        suite="sticker",
        expected={"served_sticker_id": 74, "send_source": "public_proxy"},
    )
    output = EvalOutput(
        case_id=case.id,
        suite=case.suite,
        raw={"served_sticker_id": 200, "send_source": "qq_temp"},
    )

    score = score_case(case, output)

    assert not score["passed"]
    assert any("served_sticker_id mismatch" in error for error in score["errors"])
    assert any("send_source mismatch" in error for error in score["errors"])


def test_sticker_runner_outputs_expected_contract_fields():
    from evals.run import load_cases
    from evals.runners.sticker_runner import run_sticker_case

    cases = {
        case.id: case
        for case in load_cases("regression")
        if case.id
        in {
            "regression_sticker_duplicate_canonical_001",
            "regression_sticker_public_proxy_001",
        }
    }

    duplicate = run_sticker_case(cases["regression_sticker_duplicate_canonical_001"])
    public_proxy = run_sticker_case(cases["regression_sticker_public_proxy_001"])

    assert duplicate.raw["served_sticker_id"] == 74
    assert public_proxy.raw["send_source"] == "public_proxy"
