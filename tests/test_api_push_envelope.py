import asyncio

import pytest
from fastapi import BackgroundTasks


def test_run_scheduled_task_now_uses_push_envelope(client, db_session, monkeypatch):
    from core.database import ScheduledTask

    task = ScheduledTask(
        name="测试任务",
        cron_expr="0 8 * * *",
        target_type="private",
        target_id="u1",
        prompt_template="提醒我喝水",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    async def fake_generate(*args, **kwargs):
        return "任务内容"

    legacy_calls = []
    envelope_calls = []

    async def fake_legacy_push(target_type, target_id, message):
        legacy_calls.append((target_type, target_id, message))
        return True

    async def fake_push_envelope(target_type, target_id, envelope):
        envelope_calls.append((target_type, target_id, envelope))
        return True

    monkeypatch.setattr("core.daily_digest._generate_task_message", fake_generate)
    monkeypatch.setattr("core.daily_digest.push_to_qq", fake_legacy_push)
    monkeypatch.setattr("core.daily_digest.push_envelope_to_qq", fake_push_envelope)

    response = client.post(f"/api/v1/tasks/{task.id}/run")

    assert response.status_code == 200
    assert legacy_calls == []
    assert envelope_calls[0][0:2] == ("private", "u1")
    envelope = envelope_calls[0][2]
    assert envelope["reply"] == "任务内容"
    assert envelope["messages"] == [{"type": "text", "text": "任务内容"}]
    assert envelope["meta"]["platform"] == "qq"
    assert envelope["meta"]["chat_type"] == "scheduled_task"
    assert envelope["meta"]["task_id"] == task.id


@pytest.mark.asyncio
async def test_stream_disconnect_background_push_uses_envelope_and_no_base64(
    db_session,
    monkeypatch,
):
    from api import chat_route_runner
    from api.routes import ChatProxyRequest, _private_buffers, proxy_chat
    from tests.test_api import _fast_private_reply

    _private_buffers.clear()
    _fast_private_reply(monkeypatch)

    release = asyncio.Event()
    expand_calls = []
    legacy_calls = []
    envelope_calls = []

    class FakeBridge:
        async def handle_message(self, *args, stream_queue=None, **kwargs):
            await stream_queue.put({"status": "progress", "message": "thinking"})
            await release.wait()
            return "断连图 [generated_image:abc123]"

        def pop_last_reply_meta(self, session_id):
            return {}

    def fake_expand(content, *, allow_base64=True):
        expand_calls.append((content, allow_base64))
        return "展开后 CQ 图片"

    async def fake_legacy_push(target_type, target_id, message):
        legacy_calls.append((target_type, target_id, message))
        return True

    async def fake_push_envelope(target_type, target_id, envelope):
        envelope_calls.append((target_type, target_id, envelope))
        return True

    monkeypatch.setattr("api.routes.get_bridge", lambda: FakeBridge())
    monkeypatch.setattr(
        "core.generated_images.expand_generated_image_refs_in_content",
        fake_expand,
    )
    monkeypatch.setattr("core.daily_digest.push_to_qq", fake_legacy_push)
    monkeypatch.setattr("core.daily_digest.push_envelope_to_qq", fake_push_envelope)

    background_tasks = BackgroundTasks()
    response = await proxy_chat(
        ChatProxyRequest(
            user_id="u-stream-envelope",
            session_id="private_u-stream-envelope",
            query="流式断连",
            stream=True,
        ),
        background_tasks,
        db_session,
        None,
    )

    iterator = response.body_iterator
    try:
        first_event = await asyncio.wait_for(iterator.__anext__(), timeout=1)
        assert "thinking" in first_event

        await iterator.aclose()
        assert chat_route_runner._STREAM_FINALIZER_TASKS
        release.set()
        await asyncio.wait_for(background_tasks(), timeout=1)

        async def wait_for_owned_finalizers() -> None:
            while chat_route_runner._STREAM_FINALIZER_TASKS:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_owned_finalizers(), timeout=3)
    finally:
        release.set()
        pending = list(chat_route_runner._STREAM_FINALIZER_TASKS)
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    assert expand_calls == [("断连图 [generated_image:abc123]", False)]
    assert legacy_calls == []
    assert envelope_calls[0][0:2] == ("private", "u-stream-envelope")
    envelope = envelope_calls[0][2]
    assert envelope["reply"] == "展开后 CQ 图片"
    assert envelope["messages"] == [{"type": "text", "text": "展开后 CQ 图片"}]
    assert envelope["meta"]["platform"] == "qq"
    assert envelope["meta"]["chat_type"] == "private"
    assert envelope["meta"]["user_id"] == "u-stream-envelope"
    assert envelope["meta"]["session_id"] == "private_u-stream-envelope"
    assert envelope["meta"]["target_type"] == "private"
    assert envelope["meta"]["target_id"] == "u-stream-envelope"
