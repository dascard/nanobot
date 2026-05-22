"""TimingGate 群聊保守发言策略测试。"""

import json
from pathlib import Path


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            return text[end + 4 :].strip()
    return text.strip()


def test_embedded_timing_gate_prompt_is_conservative_and_parser_aligned():
    from clients.classifier_client import TIMING_GATE_PROMPT

    prompt = TIMING_GATE_PROMPT

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
    prompt_text = _strip_frontmatter(Path("prompts.default/timing_gate.md").read_text(encoding="utf-8"))

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


def test_timing_gate_eval_suite_runs_offline():
    from evals.run import run_suite

    report = run_suite("timing_gate")

    assert report.total >= 12
    assert report.failed == 0
    assert report.pass_rate == 1.0


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
