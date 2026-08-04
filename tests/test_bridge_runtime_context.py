import json


def _runtime_facts(text: str) -> dict:
    body = text.split("<runtime_context>", 1)[1].split("</runtime_context>", 1)[0]
    return json.loads(body)


def test_bridge_runtime_context_delegates_to_canonical_json():
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge()
    text = bridge._build_runtime_context(
        user_id="group_1001",
        session_id="group_1001",
        sender_name="雀",
        meta={
            "chat_type": "group",
            "is_group": True,
            "group_id": "1001",
            "message_id": "msg-1",
        },
    )

    facts = _runtime_facts(text)
    assert facts["chat_type"] == "group"
    assert facts["group_id"] == "1001"
    assert "current_message_id" not in facts


def test_bridge_runtime_context_keeps_source_message_id_out_of_system_facts():
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge()
    text = bridge._build_runtime_context(
        user_id="group_1001",
        session_id="group_1001",
        sender_name="雀",
        meta={
            "chat_type": "group",
            "is_group": True,
            "group_id": "1001",
            "source_message_ids": ["msg-source"],
        },
    )

    facts = _runtime_facts(text)
    assert facts["chat_type"] == "group"
    assert "current_message_id" not in facts
