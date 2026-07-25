"""私聊 Timing v2 的结构化分类与确定性策略测试。"""

from __future__ import annotations

import pytest


def _model_result(**overrides):
    value = {
        "action": "reply_now",
        "effort": "short",
        "intent": "general_question",
        "response_mode": "agent",
        "confidence": 0.92,
        "parse_quality": "schema_valid",
        "error_type": None,
        "conflicting_signals": [],
        "material_state": "none",
        "reason_code": "clear_request",
        "contract_version": "private_decision_v2",
        "task_run_id": "taskrun_private",
    }
    value.update(overrides)
    return value


class RecordingClassifier:
    def __init__(self, result=None, error: BaseException | None = None):
        self.result = result or _model_result()
        self.error = error
        self.calls: list[tuple[str, bool]] = []

    def classify(self, text: str, has_files: bool):
        self.calls.append((text, has_files))
        if self.error is not None:
            raise self.error
        return dict(self.result)


def _active_policy():
    from core.private_timing_policy import (
        PrivateTimingPolicy,
        PrivateTimingRolloutMode,
    )

    return PrivateTimingPolicy(mode=PrivateTimingRolloutMode.ACTIVE)


def _observation_policy():
    from core.private_timing_policy import (
        PrivateTimingPolicy,
        PrivateTimingRolloutMode,
    )

    return PrivateTimingPolicy(mode=PrivateTimingRolloutMode.OBSERVATION)


@pytest.mark.asyncio
async def test_private_non_empty_message_calls_structured_classifier_exactly_once():
    from core.private_timing import PrivateTimingGate

    classifier = RecordingClassifier()
    decision = await PrivateTimingGate(
        classifier=classifier,
        policy=_active_policy(),
    ).classify(
        "帮我总结一下",
        user_id="u-private",
        session_id="private_u-private",
        has_files=False,
    )

    assert classifier.calls == [("帮我总结一下", False)]
    assert decision.action == "reply_now"
    assert decision.response_mode == "agent"
    assert decision.intent == "general_question"
    assert decision.parse_quality == "schema_valid"
    assert decision.task_run_id == "taskrun_private"


@pytest.mark.asyncio
async def test_private_synonymous_messages_do_not_enter_python_keyword_branches():
    from core.private_timing import PrivateTimingGate

    classifier = RecordingClassifier(_model_result(
        action="no_reply",
        effort="short",
        intent="acknowledgement",
        response_mode="none",
        confidence=0.96,
        reason_code="no_conversation_intent",
    ))
    gate = PrivateTimingGate(
        classifier=classifier,
        policy=_active_policy(),
    )

    first = await gate.classify(
        "收到",
        user_id="u-private",
        session_id="private_u-private",
    )
    second = await gate.classify(
        "了解啦",
        user_id="u-private",
        session_id="private_u-private",
    )

    assert classifier.calls == [("收到", False), ("了解啦", False)]
    assert first.action == second.action == "no_reply"
    assert first.response_mode == second.response_mode == "none"


@pytest.mark.parametrize(
    ("result", "expected_error"),
    [
        (
            _model_result(
                confidence=0.2,
                response_mode="agent",
            ),
            "low_confidence",
        ),
        (
            _model_result(
                conflicting_signals=["material"],
            ),
            "conflicting_signals",
        ),
        (
            _model_result(
                parse_quality="invalid",
                error_type="invalid_json",
                confidence=0.0,
            ),
            "invalid_json",
        ),
    ],
)
@pytest.mark.asyncio
async def test_private_low_quality_result_always_falls_back_to_normal_agent(
    result,
    expected_error,
):
    from core.private_timing import PrivateTimingGate

    decision = await PrivateTimingGate(
        classifier=RecordingClassifier(result),
        policy=_active_policy(),
    ).classify(
        "随便一条自然语言",
        user_id="u-private",
        session_id="private_u-private",
    )

    assert decision.action == "reply_now"
    assert decision.response_mode == "agent"
    assert decision.effort == "short"
    assert decision.error_type == expected_error


@pytest.mark.asyncio
async def test_private_network_failure_calls_once_and_falls_back_to_agent():
    import urllib.error

    from core.private_timing import PrivateTimingGate

    classifier = RecordingClassifier(error=urllib.error.URLError("offline"))
    decision = await PrivateTimingGate(
        classifier=classifier,
        policy=_active_policy(),
    ).classify(
        "帮我看看 https://example.com",
        user_id="u-private",
        session_id="private_u-private",
    )

    assert classifier.calls == [("帮我看看 https://example.com", False)]
    assert decision.action == "reply_now"
    assert decision.response_mode == "agent"
    assert decision.parse_quality == "invalid"
    assert decision.error_type == "provider_unavailable"


@pytest.mark.asyncio
async def test_private_programming_error_is_not_converted_to_semantic_decision():
    from core.private_timing import PrivateTimingGate

    classifier = RecordingClassifier(error=TypeError("programming error"))

    with pytest.raises(TypeError, match="programming error"):
        await PrivateTimingGate(
            classifier=classifier,
            policy=_active_policy(),
        ).classify(
            "帮我看看",
            user_id="u-private",
            session_id="private_u-private",
        )

    assert classifier.calls == [("帮我看看", False)]


@pytest.mark.asyncio
async def test_private_template_path_requires_high_confidence_and_allowed_intent():
    from core.private_timing import PrivateTimingGate

    classifier = RecordingClassifier(_model_result(
        effort="casual",
        intent="identity_probe",
        response_mode="template",
        confidence=0.95,
        reason_code="casual_exchange",
    ))
    decision = await PrivateTimingGate(
        classifier=classifier,
        policy=_active_policy(),
    ).classify(
        "请介绍一下你自己",
        user_id="u-private",
        session_id="private_u-private",
    )

    assert decision.action == "reply_now"
    assert decision.effort == "casual"
    assert decision.intent == "identity_probe"
    assert decision.response_mode == "template"


@pytest.mark.asyncio
async def test_private_observation_mode_records_proposal_without_enforcing_it():
    from core.private_timing import PrivateTimingGate

    classifier = RecordingClassifier(_model_result(
        action="no_reply",
        effort="short",
        intent="transport_only",
        response_mode="none",
        confidence=0.97,
        material_state="transport_only",
        reason_code="no_conversation_intent",
    ))
    decision = await PrivateTimingGate(
        classifier=classifier,
        policy=_observation_policy(),
    ).classify(
        "https://example.com",
        user_id="u-private",
        session_id="private_u-private",
    )

    assert classifier.calls == [("https://example.com", False)]
    assert decision.policy_mode == "observation"
    assert decision.action == "reply_now"
    assert decision.response_mode == "agent"
    assert decision.proposed_action == "no_reply"
    assert decision.proposed_response_mode == "none"
    assert decision.reason_code == "observation_only"


@pytest.mark.asyncio
async def test_private_disabled_mode_does_not_call_classifier():
    from core.private_timing import PrivateTimingGate
    from core.private_timing_policy import (
        PrivateTimingPolicy,
        PrivateTimingRolloutMode,
    )

    classifier = RecordingClassifier()
    decision = await PrivateTimingGate(
        classifier=classifier,
        policy=PrivateTimingPolicy(
            mode=PrivateTimingRolloutMode.DISABLED,
        ),
    ).classify(
        "普通私聊",
        user_id="u-private",
        session_id="private_u-private",
    )

    assert classifier.calls == []
    assert decision.action == "reply_now"
    assert decision.response_mode == "agent"
    assert decision.policy_mode == "disabled"
    assert decision.reason_code == "feature_disabled"


@pytest.mark.asyncio
async def test_private_empty_event_is_structural_and_skips_model():
    from core.private_timing import PrivateTimingGate

    classifier = RecordingClassifier()
    decision = await PrivateTimingGate(
        classifier=classifier,
        policy=_active_policy(),
    ).classify(
        "",
        user_id="u-private",
        session_id="private_u-private",
        has_files=False,
    )

    assert classifier.calls == []
    assert decision.action == "no_reply"
    assert decision.response_mode == "none"
    assert decision.reason_code == "empty_event"


@pytest.mark.asyncio
async def test_private_file_only_event_still_uses_one_semantic_classification():
    from core.private_timing import PrivateTimingGate

    classifier = RecordingClassifier(_model_result(
        effort="casual",
        intent="image_no_context",
        response_mode="template",
        confidence=0.94,
        material_state="attachment_only",
        reason_code="casual_exchange",
    ))
    decision = await PrivateTimingGate(
        classifier=classifier,
        policy=_active_policy(),
    ).classify(
        "",
        user_id="u-private",
        session_id="private_u-private",
        has_files=True,
    )

    assert classifier.calls == [("", True)]
    assert decision.response_mode == "template"
    assert decision.intent == "image_no_context"


def test_private_rollout_resolver_requires_release_gate_for_active_session(
    monkeypatch,
):
    from core.private_timing_policy import (
        PrivateTimingRolloutMode,
        resolve_private_timing_policy,
    )

    values = {
        "private_timing.rollout.default_mode": "disabled",
        "private_timing.rollout.session_modes": (
            '{"private_u1":"observation","private_u2":"active"}'
        ),
        "private_timing.rollout.active_allowed": False,
        "private_timing.decision_confidence_threshold": 0.7,
        "private_timing.template_confidence_threshold": 0.85,
    }
    monkeypatch.setattr(
        "core.private_timing_policy.settings.get",
        lambda key, default=None: values.get(key, default),
    )
    monkeypatch.setattr(
        "core.private_timing_policy.settings.get_bool",
        lambda key, default=False: bool(values.get(key, default)),
    )
    monkeypatch.setattr(
        "core.private_timing_policy.settings.get_float",
        lambda key, default=0.0: float(values.get(key, default)),
    )
    monkeypatch.setattr(
        "core.private_timing_policy.settings.get_str",
        lambda key, default="": str(values.get(key, default)),
    )

    assert (
        resolve_private_timing_policy("private_u1").mode
        is PrivateTimingRolloutMode.OBSERVATION
    )
    blocked = resolve_private_timing_policy("private_u2")
    assert blocked.mode is PrivateTimingRolloutMode.OBSERVATION
    assert blocked.source == "session:active_blocked"
    assert (
        resolve_private_timing_policy("private_u3").mode
        is PrivateTimingRolloutMode.DISABLED
    )
