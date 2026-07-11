from types import SimpleNamespace

import pytest


def _request(**overrides):
    values = {
        "group_id": "group-current",
        "message_id": "message-current",
        "sender_id": "sender-current",
        "client_meta": {"platform": " Web ", "chat_type": "group"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_group_ingress_result_requires_exactly_one_settlement_kind():
    from app.group_ingress.response_contract import build_completed_group_response
    from app.group_ingress.service import GroupIngressResult

    completion = build_completed_group_response(outcome="no_reply")
    error = RuntimeError("technical")

    with pytest.raises(ValueError, match="恰好一个"):
        GroupIngressResult(payload={})
    with pytest.raises(ValueError, match="恰好一个"):
        GroupIngressResult(
            payload={},
            completion=completion,
            technical_error=error,
        )


def test_group_technical_payload_uses_current_identity():
    from app.group_ingress.response_contract import technical_group_response_payload

    payload = technical_group_response_payload(
        _request(
            group_id="current-group",
            message_id="current-message",
            sender_id="current-user",
        ),
        reason="db_locked:ambient_log",
        generation=4,
        diagnostics={"agent_result": "db_locked"},
    )

    assert payload["action"] == "no_reply"
    assert payload["reason"] == "db_locked:ambient_log"
    assert payload["generation"] == 4
    assert payload["diagnostics"] == {"agent_result": "db_locked"}
    assert payload["meta"]["group_id"] == "current-group"
    assert payload["meta"]["message_id"] == "current-message"
    assert payload["meta"]["sender_id"] == "current-user"


def test_completed_group_payload_has_no_test_only_answer_override():
    import inspect

    from app.group_ingress.response_contract import completed_group_response_payload

    assert (
        "answer_override"
        not in inspect.signature(completed_group_response_payload).parameters
    )


def test_completed_group_respond_rebuilds_transport_with_current_identity(monkeypatch):
    from app.group_ingress import response_contract

    calls: list[tuple[str, object]] = []

    def fake_format(answer, *, max_chars):
        calls.append(("format", (answer, max_chars)))
        return f"formatted:{answer}"

    def fake_expand(answer, *, allow_base64):
        calls.append(("expand", (answer, allow_base64)))
        return f"expanded:{answer}"

    monkeypatch.setattr(
        response_contract.h,
        "format_group_reply_for_transport",
        fake_format,
    )
    monkeypatch.setattr(
        "core.generated_images.expand_generated_image_refs_in_content",
        fake_expand,
    )
    response = response_contract.build_completed_group_response(
        outcome="respond",
        reply="raw answer",
        reply_meta={"send_mode": "quote", "_private": "drop"},
        reason="reply now",
        generation=7,
        diagnostics={"timing_action": "continue"},
        duplicate_reply={"previous_log_id": 11, "similarity": 0.91},
        hard_rule="only-top-level",
    )

    payload = response_contract.completed_group_response_payload(
        _request(),
        response,
    )

    assert response.reply == "raw answer"
    assert payload["status"] == "ok"
    assert payload["action"] == "continue"
    assert payload["reply"] == "expanded:formatted:raw answer"
    assert payload["messages"] == [
        {"type": "text", "text": "expanded:formatted:raw answer"}
    ]
    assert payload["reply_meta"] == {"send_mode": "quote"}
    assert payload["generation"] == 7
    assert payload["reason"] == "reply now"
    assert payload["diagnostics"] == {"timing_action": "continue"}
    assert payload["duplicate_reply"] == {
        "previous_log_id": 11,
        "similarity": 0.91,
    }
    assert payload["hard_rule"] == "only-top-level"
    assert payload["meta"] == {
        "platform": "web",
        "chat_type": "group",
        "group_id": "group-current",
        "message_id": "message-current",
        "sender_id": "sender-current",
        "generation": 7,
        "reason": "reply now",
        "diagnostics": {"timing_action": "continue"},
        "duplicate_reply": {"previous_log_id": 11, "similarity": 0.91},
    }
    assert "hard_rule" not in payload["meta"]
    assert calls == [
        ("format", ("raw answer", 4000)),
        ("expand", ("formatted:raw answer", False)),
    ]


@pytest.mark.parametrize(
    ("outcome", "action", "status"),
    [
        ("no_reply", "no_reply", "no_reply"),
        ("silent", "no_reply", "no_reply"),
        ("blocked", "no_reply", "no_reply"),
        ("wait", "wait", "wait"),
    ],
)
def test_completed_group_non_respond_outcomes_force_empty_reply(
    outcome,
    action,
    status,
):
    from app.group_ingress import response_contract

    response = response_contract.build_completed_group_response(
        outcome=outcome,
        reply="must not leak",
        generation=2,
        delay_seconds=6,
        reason=f"{outcome} reason",
    )

    payload = response_contract.completed_group_response_payload(_request(), response)

    assert payload["action"] == action
    assert payload["status"] == status
    assert payload["reply"] == ""
    assert payload["messages"] == []
    assert payload["generation"] == 2
    assert payload["delay_seconds"] == 6
    assert payload["reason"] == f"{outcome} reason"


def test_completed_group_empty_optional_mappings_are_omitted():
    from app.group_ingress import response_contract

    response = response_contract.build_completed_group_response(
        outcome="no_reply",
        diagnostics={},
        duplicate_reply={},
    )

    payload = response_contract.completed_group_response_payload(
        _request(message_id="", sender_id=""),
        response,
    )

    assert "diagnostics" not in payload
    assert "duplicate_reply" not in payload
    assert "hard_rule" not in payload
    assert "generation" not in payload
    assert "delay_seconds" not in payload
    assert "reason" not in payload
    assert "diagnostics" not in payload["meta"]
    assert "duplicate_reply" not in payload["meta"]
    assert "message_id" not in payload["meta"]
    assert "sender_id" not in payload["meta"]


def test_duplicate_inflight_group_payload_uses_current_identity():
    from app.group_ingress import response_contract

    payload = response_contract.duplicate_inflight_group_response_payload(_request())

    assert payload["status"] == "duplicate_inflight"
    assert payload["action"] == "duplicate_inflight"
    assert payload["reply"] == ""
    assert payload["messages"] == []
    assert payload["reply_meta"] == {}
    assert payload["reason"] == "duplicate_inflight"
    assert payload["meta"] == {
        "platform": "web",
        "chat_type": "group",
        "group_id": "group-current",
        "message_id": "message-current",
        "sender_id": "sender-current",
        "reason": "duplicate_inflight",
    }


def test_completed_group_transport_expansion_failure_falls_back_to_formatted_raw_reply(
    monkeypatch,
):
    from app.group_ingress import response_contract

    format_calls: list[tuple[str, int]] = []

    def fake_format(answer, *, max_chars):
        format_calls.append((answer, max_chars))
        return f"formatted:{answer}"

    def fail_expand(*_args, **_kwargs):
        raise RuntimeError("expand failed")

    class BrokenLogger:
        def warning(self, *_args, **_kwargs):
            raise RuntimeError("logger failed")

    monkeypatch.setattr(
        response_contract.h,
        "format_group_reply_for_transport",
        fake_format,
    )
    monkeypatch.setattr(
        "core.generated_images.expand_generated_image_refs_in_content",
        fail_expand,
    )
    monkeypatch.setattr(response_contract, "logger", BrokenLogger())
    response = response_contract.build_completed_group_response(
        outcome="respond",
        reply="raw reply",
    )

    payload = response_contract.completed_group_response_payload(
        _request(),
        response,
    )

    assert payload["reply"] == "formatted:raw reply"
    assert payload["messages"] == [
        {"type": "text", "text": "formatted:raw reply"}
    ]
    assert format_calls == [("raw reply", 4000)]
