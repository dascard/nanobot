"""私聊 timing gate 的运行时工具策略测试。"""

import pytest


def test_superuser_general_query_uses_full_runtime_preset():
    from core.private_timing import _infer_effort

    effort, runtime_preset, intent = _infer_effort("随便聊两句", is_superuser=True)

    assert effort == "short"
    assert runtime_preset == "full"
    assert intent == "superuser_query"


@pytest.mark.asyncio
async def test_private_task_request_uses_shared_scoring_without_classifier():
    from core.private_timing import PrivateTimingGate

    class ExplodingClassifier:
        def classify(self, *_args, **_kwargs):
            raise AssertionError("classifier should not be called")

    gate = PrivateTimingGate(classifier=ExplodingClassifier())

    decision = await gate.classify("帮我总结一下", user_id="u-private")

    assert decision.action == "reply_now"
    assert decision.raw_label == "scoring_rule_shortcut"
    assert decision.timing_scoring["stage"] == "rule_shortcut"
    assert decision.timing_scoring["signals"]["sub_signals"]["is_private"] is True
    assert decision.effort == "short"
    assert decision.runtime_preset == "lightweight"


@pytest.mark.asyncio
async def test_private_conflict_uses_classifier_as_scoring_model_hint():
    from core.private_timing import PrivateTimingGate

    class ReplyClassifier:
        def __init__(self):
            self.calls = 0

        def classify(self, text, has_files):
            self.calls += 1
            return {
                "action": "reply_now",
                "complexity": 4,
                "reason": "用户要求查看链接",
                "raw": '{"action":"reply_now"}',
            }

    classifier = ReplyClassifier()
    gate = PrivateTimingGate(classifier=classifier)

    decision = await gate.classify("帮我看看 https://example.com", user_id="u-private")

    assert classifier.calls == 1
    assert decision.action == "reply_now"
    assert decision.complexity == 4
    assert decision.raw_label == '{"action":"reply_now"}'
    assert decision.timing_scoring["stage"] == "model_assisted_conflict"
    assert decision.timing_scoring["model_used"] is True
    assert decision.timing_scoring["model_action"] == "reply_now"


@pytest.mark.parametrize("reason", ["invalid output fallback", "classifier fallback"])
@pytest.mark.asyncio
async def test_private_classifier_failure_result_uses_zero_confidence_rule_fallback(reason):
    from core.private_timing import PrivateTimingGate

    class FallbackClassifier:
        def __init__(self):
            self.calls = 0

        def classify(self, text, has_files):
            self.calls += 1
            return {
                "action": "reply_now",
                "complexity": 5,
                "reason": reason,
                "raw": "fallback raw",
            }

    classifier = FallbackClassifier()
    gate = PrivateTimingGate(classifier=classifier)

    decision = await gate.classify("帮我看看 https://example.com", user_id="u-private")

    assert classifier.calls == 1
    assert decision.confidence == 0.0
    assert decision.raw_label == "fallback raw"
    assert decision.timing_scoring["stage"] == "rule_fallback"
    assert decision.timing_scoring["model_used"] is False
    assert decision.timing_scoring["model_confidence"] == 0.0


@pytest.mark.asyncio
async def test_private_image_only_wait_keeps_lightweight_runtime_for_final_processing():
    from core.private_timing import PrivateTimingGate

    gate = PrivateTimingGate()

    decision = await gate.classify("", user_id="u-private", has_files=True)

    assert decision.action == "wait"
    assert decision.raw_label == "scoring_rule_shortcut"
    assert decision.runtime_preset == "lightweight"
    assert decision.effort == "short"
    assert decision.timing_scoring["stage"] == "rule_shortcut"
    assert decision.timing_scoring["signals"]["sub_signals"]["w_file"] == 0.45
