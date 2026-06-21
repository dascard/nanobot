from __future__ import annotations


def test_group_runtime_split_keeps_legacy_import_paths():
    import core.group_runtime.runtime as runtime_module
    import core.timing_runtime as timing_runtime

    assert timing_runtime.PendingMessage is runtime_module.GroupPendingMessage
    assert timing_runtime.GateState is runtime_module.GroupChatState
    assert timing_runtime.GroupRuntime is runtime_module.GroupRuntime
    assert timing_runtime._pending_payload is runtime_module._pending_payload
    assert timing_runtime.MAX_PENDING == runtime_module.MAX_PENDING
    assert timing_runtime.BOT_REPLY_COOLDOWN_SEC == runtime_module.BOT_REPLY_COOLDOWN_SEC

    runtime = runtime_module.GroupRuntime()
    assert hasattr(runtime, "_score_timing")
    assert hasattr(runtime, "_cooldown_scoring_shortcut")
    assert callable(runtime_module.GroupRuntime._build_timing_context)
    assert callable(runtime_module.should_suppress_directed_to_other)


def test_pending_payload_preserves_direction_reference_and_source_ids():
    from core.group_runtime.runtime import GroupPendingMessage, _pending_payload

    msgs = [
        GroupPendingMessage(
            sender_id="u1",
            sender_name="小明",
            message="@Nanobot 看看",
            message_id="m1",
            ts=1,
            is_at_bot=True,
            directed={"at_bot": True, "directed_to_other": False},
            mentions=[{"user_id": "bot", "nickname": "Nanobot", "is_bot": True}],
        ),
        GroupPendingMessage(
            sender_id="u2",
            sender_name="小红",
            message="@小明 这个呢",
            message_id="m2",
            ts=2,
            directed={"at_others": True, "reply_to_others": True, "directed_to_other": True},
            mentions=[{"user_id": "u1", "nickname": "小明", "is_bot": False}],
            reply_to={"sender_id": "u1", "sender_name": "小明", "content": "上一条消息"},
        ),
    ]

    payload = _pending_payload(msgs)

    assert payload["source_message_ids"] == ["m1", "m2"]
    assert "[指向性] @bot" in payload["pending_text"]
    assert "[指向性] @其他人: 小明" in payload["pending_text"]
    assert "[指向性] 回复其他人" in payload["pending_text"]
    assert "[引用] 小明: 上一条消息" in payload["pending_text"]


def test_pending_message_does_not_derive_directed_to_other_when_at_bot():
    from core.group_runtime.runtime import GroupPendingMessage

    msg = GroupPendingMessage(
        sender_id="u1",
        sender_name="小明",
        message="@Nanobot @小红",
        is_at_bot=True,
        directed={"at_bot": True, "at_others": True, "directed_to_other": True},
    )

    assert msg.is_directed_to_other is False


def test_timing_wait_delay_and_model_confidence_helpers_keep_contract():
    from core.group_runtime.runtime import (
        _clip_timing_wait_delay,
        _model_confidence_from_gate_result,
    )

    assert _clip_timing_wait_delay(None) == 5
    assert _clip_timing_wait_delay("bad") == 5
    assert _clip_timing_wait_delay(1) == 3
    assert _clip_timing_wait_delay(999) == 15

    assert _model_confidence_from_gate_result({"model_confidence": "0.7"}) == 0.7
    assert _model_confidence_from_gate_result({"model_confidence": "nan"}) == 0.0
    assert _model_confidence_from_gate_result({"parse_quality": "legacy"}) == 0.5
    assert _model_confidence_from_gate_result({"parse_quality": "invalid"}) == 0.0
    assert _model_confidence_from_gate_result({"parse_quality": "network_error"}) == 0.0
    assert _model_confidence_from_gate_result({}) == 0.8


def test_score_timing_maps_pending_signals_to_decide_timing(monkeypatch):
    import core.group_runtime.runtime as runtime_module

    captured = {}

    class FakeDecision:
        action = "continue"
        delay_seconds = None
        reason = "fake"
        stage = "rule_shortcut"
        signals = {}

    def fake_decide_timing(**kwargs):
        captured.update(kwargs)
        return FakeDecision()

    monkeypatch.setattr("core.timing_score.decide_timing", fake_decide_timing)

    runtime = runtime_module.GroupRuntime()
    state = runtime_module.GroupChatState()
    pending = [
        runtime_module.GroupPendingMessage(
            sender_id="u1",
            sender_name="小明",
            message="@Nanobot 这张图看看",
            is_at_bot=True,
            is_other_bot=True,
            segments=[{"type": "image"}],
            directed={"at_bot": True, "at_others": True},
            mentions=[{"user_id": "u2", "nickname": "小红", "is_bot": False}],
        )
    ]

    runtime._score_timing(
        state,
        "at_bot",
        pending=pending,
        force_direct_score=1.0,
        model_result={"action": "continue", "model_confidence": 0.9},
    )

    assert captured["is_at_bot"] is True
    assert captured["direct_call"] is True
    assert captured["is_other_bot"] is True
    assert captured["has_other_recipient"] is True
    assert captured["has_files"] is True
    assert captured["force_direct_score"] == 1.0
    assert captured["model_hint"].confidence == 0.9
