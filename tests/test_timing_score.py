from __future__ import annotations

from dataclasses import asdict

import pytest

from core.timing_score import TimingModelHint, decide_timing, extract_signals


def test_at_bot_request_rule_shortcuts_continue_without_model():
    decision = decide_timing(
        text="@bot 帮我查一下 X",
        is_group=True,
        is_at_bot=True,
        model_hint=None,
    )

    assert decision.action == "continue"
    assert decision.stage == "rule_shortcut"
    assert decision.model_used is False
    assert decision.participation_score >= decision.high_threshold


def test_ambient_ack_rule_shortcuts_no_reply_without_model():
    decision = decide_timing(text="嗯", is_group=True, model_hint=None)

    assert decision.action == "no_reply"
    assert decision.stage == "rule_shortcut"
    assert decision.model_used is False


def test_at_bot_with_image_waits_without_transport_suppression():
    decision = decide_timing(
        text="@bot",
        is_group=True,
        is_at_bot=True,
        has_files=True,
        model_hint=None,
    )

    assert decision.action == "wait"
    assert decision.delay_seconds == 5
    assert decision.model_used is False
    assert decision.signals.sub_signals["s_transport"] == 0.0
    assert decision.signals.wait_signal >= 0.4


def test_directed_to_other_with_linger_escalates_to_model():
    decision = decide_timing(
        text="张三你看看这个",
        is_group=True,
        is_directed_to_other=True,
        linger_score=0.55,
        model_hint=TimingModelHint(
            action="continue",
            confidence=0.8,
            raw="{}",
            reason="仍在上一轮对话中",
        ),
    )

    assert decision.stage == "model_assisted_conflict"
    assert decision.model_used is True
    assert decision.action == "continue"
    assert decision.model_weight == 0.7


def test_non_pure_ack_is_not_suppressed_as_ack():
    signals = extract_signals(text="好的，帮我查下 X", is_group=True)

    assert signals.sub_signals["s_ack"] == 0.0


@pytest.mark.parametrize(
    ("text", "expected_score", "expected_tier"),
    [
        ("sk-secret-token-value", 0.95, "secret"),
        ("a3f9" * 24, 0.95, "blob"),
        ("https://example.com/a", 0.75, "url"),
        ("```python\nprint(1)\n```", 0.65, "codeblock"),
        ("日志" * 420, 0.55, "long_dump"),
    ],
)
def test_transport_signal_tiers_match_design(text, expected_score, expected_tier):
    signals = extract_signals(text=text, is_group=True)

    assert signals.sub_signals["s_transport"] == expected_score
    assert signals.sub_signals["s_transport_tier"] == expected_tier


@pytest.mark.parametrize(
    "text",
    [
        "好的，帮我查下 X",
        "好的？",
        "好的 https://example.com",
        "好的\n```python\nprint(1)\n```",
    ],
)
def test_non_pure_ack_variants_are_not_suppressed_as_ack(text):
    signals = extract_signals(text=text, is_group=True)

    assert signals.sub_signals["s_ack"] == 0.0


def test_at_bot_url_conflict_uses_model_instead_of_rule_shortcut():
    decision = decide_timing(
        text="@bot https://example.com/a",
        is_group=True,
        is_at_bot=True,
        model_hint=TimingModelHint(
            action="no_reply",
            confidence=0.8,
            raw="{}",
            reason="只是转发链接",
        ),
    )

    assert decision.stage == "model_assisted_conflict"
    assert decision.model_used is True
    assert decision.signals.sub_signals["s_transport"] == 0.75
    assert decision.action == "no_reply"


def test_at_bot_with_other_mention_escalates_to_model_without_soft_reject():
    decision = decide_timing(
        text="@bot @张三 你看下",
        is_group=True,
        is_at_bot=True,
        has_other_recipient=True,
        model_hint=TimingModelHint(
            action="continue",
            confidence=0.8,
            raw="{}",
            reason="同时点名 bot 和他人，需要模型判断",
        ),
    )

    assert decision.stage == "model_assisted_conflict"
    assert decision.model_used is True
    assert decision.signals.sub_signals["s_other"] == 0.75
    assert decision.soft_reject_cap == 1.0
    assert decision.action == "continue"


def test_model_failure_in_fuzzy_band_falls_back_to_rule_side():
    decision = decide_timing(
        text="接着呢",
        is_group=True,
        linger_score=0.7,
        model_hint=TimingModelHint(
            action="no_reply",
            confidence=0.0,
            raw="",
            reason="timeout",
        ),
    )

    assert decision.stage == "rule_fallback"
    assert decision.model_used is False
    assert decision.action == "continue"


def test_decision_exposes_conflict_and_soft_reject_debug_fields():
    decision = decide_timing(
        text="@bot https://example.com/a",
        is_group=True,
        is_at_bot=True,
        is_other_bot=True,
        model_hint=TimingModelHint(
            action="continue",
            confidence=0.8,
            raw="{}",
            reason="被明确点名",
        ),
    )

    payload = asdict(decision)

    assert payload["conflict_score"] == min(
        decision.signals.direct_score,
        decision.signals.suppress_score,
    )
    assert payload["soft_reject_cap"] == 0.44
