from core.message_envelope import build_chat_response_envelope
from core.message_envelope import build_group_response_envelope
from core.message_envelope import build_text_messages
from core.message_envelope import envelope_to_message
from core.message_envelope import sanitize_reply_meta


def test_build_text_messages_handles_empty_text_and_html():
    assert build_text_messages("") == []
    assert build_text_messages("你好") == [{"type": "text", "text": "你好"}]
    html = "<article><h1>日报</h1></article>"
    assert build_text_messages(html) == [{"type": "html", "text": html}]


def test_sanitize_reply_meta_keeps_protocol_keys_only():
    raw = {
        "send_mode": "quote",
        "reply_to_message_id": "m1",
        "mentions": ["10001"],
        "quote": {"message_id": "m1"},
        "at_sender": True,
        "_agent_result": "prompt_audit_failed",
        "_no_reply": True,
        "_no_reply_reason": "internal",
        "debug": "drop",
    }

    assert sanitize_reply_meta(raw) == {
        "send_mode": "quote",
        "reply_to_message_id": "m1",
        "mentions": ["10001"],
        "quote": {"message_id": "m1"},
        "at_sender": True,
    }
    assert sanitize_reply_meta(None) == {}


def test_build_chat_response_envelope_filters_meta_and_messages():
    envelope = build_chat_response_envelope(
        status="ok",
        answer="你好",
        reply_meta={"send_mode": "normal", "_agent_result": "ok"},
        meta={
            "user_id": "u1",
            "session_id": "private_u1",
            "platform": "web",
            "chat_type": "private",
            "empty": "",
            "none_value": None,
            "count": 0,
        },
    )

    assert envelope == {
        "status": "ok",
        "reply": "你好",
        "messages": [{"type": "text", "text": "你好"}],
        "reply_meta": {"send_mode": "normal"},
        "meta": {
            "user_id": "u1",
            "session_id": "private_u1",
            "platform": "web",
            "chat_type": "private",
            "count": 0,
        },
    }


def test_build_group_response_envelope_preserves_action_fields():
    envelope = build_group_response_envelope(
        action="wait",
        reply="",
        generation=5,
        delay_seconds=8,
        reason="user may type more",
        meta={"platform": "qq", "chat_type": "group", "group_id": "789"},
    )

    assert envelope["status"] == "wait"
    assert envelope["action"] == "wait"
    assert envelope["reply"] == ""
    assert envelope["messages"] == []
    assert envelope["reply_meta"] == {}
    assert envelope["meta"]["generation"] == 5
    assert envelope["meta"]["delay_seconds"] == 8
    assert envelope["meta"]["reason"] == "user may type more"
    assert envelope["meta"]["platform"] == "qq"


def test_envelope_to_message_prefers_reply_then_textual_messages():
    assert (
        envelope_to_message(
            {"reply": "正文", "messages": [{"type": "text", "text": "忽略"}]}
        )
        == "正文"
    )
    assert (
        envelope_to_message(
            {
                "reply": "",
                "messages": [
                    {"type": "text", "text": "A"},
                    {"type": "html", "text": "<article>B</article>"},
                    {"type": "image", "url": "https://example.com/a.png"},
                ],
            }
        )
        == "A\n<article>B</article>"
    )
    assert (
        envelope_to_message(
            {"messages": [{"type": "image", "url": "https://example.com/a.png"}]}
        )
        == ""
    )
