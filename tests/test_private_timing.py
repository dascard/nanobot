"""私聊 timing gate 的运行时工具策略测试。"""

import pytest


@pytest.mark.parametrize(
    ("text", "is_superuser", "expected"),
    [
        ("我是不是超级用户?", True, ("short", "lightweight", "superuser_query")),
        ("这件事靠谱吗?", True, ("short", "lightweight", "superuser_query")),
        ("为什么今天这么累?", True, ("short", "lightweight", "superuser_query")),
        ("怎么了?", True, ("short", "lightweight", "superuser_query")),
        ("如何保持好心情?", True, ("short", "lightweight", "superuser_query")),
        ("能不能聊会儿?", True, ("short", "lightweight", "superuser_query")),
        (
            "为什么最近总觉得很累\n我已经连续好几天睡不好而且白天也没精神还一直提不起劲做任何事情",
            True,
            ("short", "lightweight", "superuser_query"),
        ),
        ("你是谁?", True, ("casual", "none", "identity_probe")),
        ("你能做什么?", True, ("casual", "none", "check_capability")),
        ("请审查这段代码并给出修复方案", True, ("serious", "full", "superuser_task")),
        ("为什么这个 Traceback 会出现", True, ("serious", "full", "superuser_task")),
        ("给我今日的 AI 日报", True, ("serious", "full", "daily_request")),
        ("最新 AI 新闻", True, ("serious", "full", "daily_request")),
        ("来份 AI 简报", True, ("serious", "full", "daily_request")),
        ("总结今天的 AI 日报", True, ("serious", "full", "daily_request")),
        ("帮我看下最新 AI 新闻", True, ("serious", "full", "daily_request")),
        ("今日新闻真离谱", True, ("short", "lightweight", "superuser_query")),
        ("他刚给我发了今天的新闻", True, ("short", "lightweight", "superuser_query")),
        ("我已经整理完本周日报", True, ("short", "lightweight", "superuser_query")),
        ("请审查这段代码并给出修复方案", False, ("short", "lightweight", "specific_task")),
        ("给我今日的 AI 日报", False, ("casual", "none", "daily_request_casual")),
        ("帮我看下最新 AI 新闻", False, ("casual", "none", "daily_request_casual")),
    ],
)
def test_infer_effort_applies_superuser_as_permission_ceiling_only(
    text,
    is_superuser,
    expected,
):
    from core.private_timing import _infer_effort

    assert _infer_effort(text, is_superuser=is_superuser) == expected


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
