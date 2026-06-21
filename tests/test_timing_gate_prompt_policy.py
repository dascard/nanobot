"""TimingGate 群聊保守发言策略测试。"""

import json
from pathlib import Path


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            return text[end + 4 :].strip()
    return text.strip()


def _assert_directed_to_other_softened_semantics(prompt: str) -> None:
    assert "仅指向其他人" in prompt
    assert "默认 no_reply" in prompt
    assert "同时指向 bot" in prompt
    assert "回复 bot" in prompt
    assert "余韵" in prompt
    assert "结合上下文" in prompt


def test_embedded_timing_gate_prompt_is_conservative_and_parser_aligned():
    from clients.classifier_client import TIMING_GATE_PROMPT

    prompt = TIMING_GATE_PROMPT

    _assert_directed_to_other_softened_semantics(prompt)
    assert "continue|wait|no_reply" in prompt
    assert "默认 no_reply" in prompt
    assert "误触发" in prompt or "假阳性" in prompt
    assert "不确定就 no_reply" in prompt
    assert "bot刚说过话" in prompt
    assert "[指向性] @其他人" in prompt
    assert "其他 bot" in prompt
    assert "Agent模式" in prompt
    assert "reply|ignore|merge" not in prompt
    assert "- reply:" not in prompt
    assert "- ignore:" not in prompt
    assert "- merge:" not in prompt


def test_default_timing_gate_template_matches_runtime_actions():
    prompt_text = _strip_frontmatter(
        Path("prompts.v2.default/tasks/timing_gate.md").read_text(encoding="utf-8")
    )

    _assert_directed_to_other_softened_semantics(prompt_text)
    assert "continue|wait|no_reply" in prompt_text
    assert "默认 no_reply" in prompt_text
    assert "不确定就 no_reply" in prompt_text
    assert "[指向性] @其他人" in prompt_text
    assert "其他 bot" in prompt_text
    assert "- reply:" not in prompt_text
    assert "- ignore:" not in prompt_text
    assert "- merge:" not in prompt_text
    assert "merge" not in prompt_text


def test_timing_gate_eval_suite_has_conservative_group_coverage():
    from evals.run import load_cases

    cases = load_cases("timing_gate")

    assert len(cases) >= 12
    actions = [case.expected.get("timing_action") for case in cases]
    assert actions.count("no_reply") >= 7
    assert actions.count("continue") >= 3
    assert actions.count("wait") >= 2

    tags = {tag for case in cases for tag in case.tags}
    assert {
        "ambient",
        "directed",
        "reply_to_bot",
        "cooldown",
        "open_question",
        "continuous_input",
        "directed_to_other",
    }.issubset(tags)


def test_timing_gate_eval_suite_contains_rule_scoring_cases():
    from evals.run import load_cases

    cases = load_cases("timing_gate")
    scoring_cases = [case for case in cases if not case.input.get("action")]

    assert len(scoring_cases) >= 2
    for case in scoring_cases:
        assert case.expected.get("timing_action") in {"continue", "wait", "no_reply"}
        assert isinstance(case.expected.get("scoring"), dict)


def test_timing_gate_eval_suite_contains_directed_to_other_paired_conflict_cases():
    from evals.run import load_cases

    cases = {case.id: case for case in load_cases("timing_gate")}

    pure_other = cases["timing_gate_scoring_directed_other_no_reply"]
    at_bot_conflict = cases["timing_gate_scoring_at_bot_with_other_mention_model"]
    linger_conflict = cases["timing_gate_scoring_directed_other_linger_model"]

    assert pure_other.expected["timing_action"] == "no_reply"
    assert pure_other.expected["scoring"]["stage"] == "rule_shortcut"
    assert pure_other.expected["scoring"]["model_used"] is False

    assert at_bot_conflict.input["is_at_bot"] is True
    assert at_bot_conflict.input["is_directed_to_other"] is True
    assert at_bot_conflict.expected["timing_action"] == "continue"
    assert at_bot_conflict.expected["scoring"]["stage"] == "model_assisted_conflict"
    assert at_bot_conflict.expected["scoring"]["model_used"] is True

    assert linger_conflict.input["is_directed_to_other"] is True
    assert linger_conflict.input["linger_score"] > 0
    assert linger_conflict.expected["timing_action"] == "continue"
    assert linger_conflict.expected["scoring"]["stage"] == "model_assisted_conflict"
    assert linger_conflict.expected["scoring"]["model_used"] is True


def test_timing_gate_eval_suite_runs_offline():
    from evals.run import run_suite

    report = run_suite("timing_gate")

    assert report.total >= 12
    assert report.failed == 0
    assert report.pass_rate == 1.0


def test_timing_gate_eval_runner_uses_rule_scoring_when_action_missing():
    from evals.schema import EvalCase
    from evals.runners.timing_gate_runner import run_timing_gate_case

    case = EvalCase(
        id="timing_gate_runner_scoring",
        suite="timing_gate",
        input={
            "text": "@nanobot 这个报错怎么修",
            "is_group": True,
            "is_at_bot": True,
            "trigger_reason": "at_bot",
        },
    )

    output = run_timing_gate_case(case)

    assert output.timing_action == "continue"
    assert output.should_reply is True
    scoring = output.raw["scoring"]
    assert scoring["stage"] == "rule_shortcut"
    assert scoring["model_used"] is False
    assert scoring["signals"]["explicit_direct_score"] == 0.95


def test_timing_gate_scorer_checks_expected_action():
    from evals.schema import EvalCase, EvalOutput
    from evals.scorers import score_case

    case = EvalCase(
        id="timing_gate_scorer_action",
        suite="timing_gate",
        expected={"timing_action": "no_reply"},
    )
    output = EvalOutput(case_id=case.id, suite=case.suite, timing_action="continue")

    result = score_case(case, output)

    assert result["passed"] is False
    assert any("timing_action mismatch" in err for err in result["errors"])


def test_timing_gate_scorer_rejects_scoring_stage_model_used_signal_mismatch():
    from evals.schema import EvalCase, EvalOutput
    from evals.scorers import score_case

    case = EvalCase(
        id="timing_gate_scorer_scoring",
        suite="timing_gate",
        expected={
            "scoring": {
                "stage": "rule_shortcut",
                "model_used": False,
                "signals": {"sub_signals": {"s_transport_tier": "url"}},
            },
        },
    )
    output = EvalOutput(
        case_id=case.id,
        suite=case.suite,
        raw={
            "scoring": {
                "stage": "model_assisted",
                "model_used": True,
                "signals": {"sub_signals": {"s_transport_tier": "blob"}},
            }
        },
    )

    result = score_case(case, output)

    assert result["passed"] is False
    assert any("scoring.stage mismatch" in err for err in result["errors"])
    assert any("scoring.model_used mismatch" in err for err in result["errors"])
    assert any("scoring.signals.sub_signals.s_transport_tier mismatch" in err for err in result["errors"])
