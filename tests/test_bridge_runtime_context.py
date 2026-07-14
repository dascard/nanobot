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


def test_bridge_clears_stale_controller_events_before_new_request():
    import asyncio
    from types import SimpleNamespace

    from kohakuterrarium.core.events import create_user_input_event
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge()
    queue = asyncio.Queue()
    queue.put_nowait(create_user_input_event("旧队列消息"))
    controller = SimpleNamespace(
        _pending_events=[create_user_input_event("旧 pending 消息")],
        _event_queue=queue,
        _pending_injections=[{"role": "system", "content": "旧注入"}],
    )
    bridge._agent = SimpleNamespace(controller=controller)

    bridge._clear_controller_event_state()

    assert controller._pending_events == []
    assert controller._pending_injections == []
    assert queue.empty()
