import asyncio

import pytest


def test_message_stream_flag_is_internal_not_wire():
    from kohakuterrarium.llm.message import Message, UserMessage

    msg = UserMessage("你好", stream=True)
    assert msg.stream is True
    assert "stream" not in msg.to_dict()

    restored = Message.from_dict({"role": "user", "content": "你好", "stream": True})
    assert restored.stream is True
    assert "stream" not in restored.to_dict()


@pytest.mark.asyncio
async def test_controller_user_message_carries_stream_without_wire_leak():
    from kohakuterrarium.core.controller import Controller, ControllerConfig
    from nanobot_kt.kt_adapter import create_user_event

    class FakeLLM:
        provider_name = "fake"
        last_tool_calls = []
        last_assistant_extra_fields = {}
        last_assistant_content_parts = None
        last_usage = {}

        def __init__(self):
            self.seen_messages = None

        async def chat(self, messages, **_kwargs):
            self.seen_messages = messages
            yield "ok"

    llm = FakeLLM()
    controller = Controller(llm, ControllerConfig(include_job_status=False))
    await controller.push_event(create_user_event("你好", stream=True))

    async for _event in controller.run_once():
        pass

    user_msg = next(msg for msg in controller.conversation.get_messages() if msg.role == "user")
    assert user_msg.stream is True
    user_wire = next(msg for msg in llm.seen_messages if msg["role"] == "user")
    assert user_wire == {"role": "user", "content": "你好"}


@pytest.mark.asyncio
async def test_bridge_pool_passes_stream_flag_to_child(monkeypatch):
    import nanobot_kt.bridge as bridge_mod

    captured = {}

    class FakeBridge:
        def __init__(self, _creature_path="creatures/nanobot"):
            pass

        async def start(self):
            pass

        async def stop(self):
            pass

        async def handle_message(self, query, **kwargs):
            captured.update(kwargs)
            return "ok"

    monkeypatch.setattr(bridge_mod, "NanobotBridge", FakeBridge)

    pool = bridge_mod.NanobotBridgePool()
    await pool.start()
    try:
        result = await pool.handle_message(
            "你好",
            user_id="u1",
            session_id="private_u1",
            stream=True,
            stream_queue=asyncio.Queue(),
        )
    finally:
        await pool.stop()

    assert result == "ok"
    assert captured["stream"] is True
    assert captured["stream_queue"] is not None
