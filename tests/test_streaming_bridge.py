import asyncio

import pytest


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
