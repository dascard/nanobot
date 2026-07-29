import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import BackgroundTasks


def _seed_scheduled_outbox_control(db_session) -> None:
    from core.database import OutboundDeliveryControl

    now = datetime(2026, 7, 15, 4, 0, 0)
    control = db_session.get(OutboundDeliveryControl, "scheduled_task")
    if control is None:
        control = OutboundDeliveryControl(source_type="scheduled_task")
        db_session.add(control)
    control.mode = "outbox_active"
    control.cutover_epoch = 1
    control.effective_from = now - timedelta(days=1)
    control.protocol_version = 2
    control.writer_version = 0
    control.writer_owner = None
    control.writer_token = None
    control.writer_lease_expires_at = None
    control.created_at = now - timedelta(days=1)
    control.updated_at = now - timedelta(days=1)
    db_session.commit()


def _seed_manual_task(db_session):
    from core.database import ScheduledTask
    from core.scheduled_task_contract import (
        apply_scheduled_task_owner,
        scheduled_task_owner_from_target,
    )

    task = ScheduledTask(
        name="测试任务",
        cron_expr="0 8 * * *",
        target_type="private",
        target_id="u1",
        prompt_template="提醒我喝水",
        enabled=True,
    )
    apply_scheduled_task_owner(
        task,
        scheduled_task_owner_from_target(
            target_type="private",
            target_id="u1",
            created_by_actor_id="u1",
        ),
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


def test_manual_run_requires_idempotency_key(client, db_session, monkeypatch):
    _seed_scheduled_outbox_control(db_session)
    task = _seed_manual_task(db_session)

    async def forbidden_generate(*_args, **_kwargs):
        raise AssertionError("缺少幂等键时不得调用模型")

    monkeypatch.setattr(
        "core.daily_digest._generate_task_message",
        forbidden_generate,
    )

    response = client.post(f"/api/v1/tasks/{task.id}/run")

    assert response.status_code == 422


def test_manual_run_returns_queued_without_direct_http(
    client,
    db_session,
    monkeypatch,
):
    from core.database import (
        OutboundDeliveryOutbox,
        ScheduledTaskExecution,
    )

    _seed_scheduled_outbox_control(db_session)
    task = _seed_manual_task(db_session)

    async def fake_generate(*args, **kwargs):
        return "任务内容"

    async def forbidden_push(*_args, **_kwargs):
        raise AssertionError("outbox 模式不得直接调用 QQ push")

    monkeypatch.setattr("core.daily_digest._generate_task_message", fake_generate)
    monkeypatch.setattr("core.daily_digest.push_to_qq", forbidden_push)
    monkeypatch.setattr("core.daily_digest.push_envelope_to_qq", forbidden_push)

    response = client.post(
        f"/api/v1/tasks/{task.id}/run",
        headers={"Idempotency-Key": "manual-request-1"},
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "pending"
    assert data["execution_id"] > 0
    assert data["deduplicated"] is False
    assert "run_id" not in data
    assert "outbox_id" not in data
    assert "content" not in data
    assert "target" not in data
    assert db_session.query(ScheduledTaskExecution).count() == 1
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_task_update_cancels_pending_delivery_atomically(
    client,
    db_session,
):
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )
    from core.database import OutboundDeliveryOutbox
    from tests.async_helpers import run_async

    _seed_scheduled_outbox_control(db_session)
    task = _seed_manual_task(db_session)
    queued = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="pending-before-update",
            config=ScheduledTaskProducerConfig.for_tests(),
            generator=lambda _snapshot: "旧定义生成的内容",
        )
    )

    response = client.put(
        f"/api/v1/tasks/{task.id}",
        json={
            "name": "修改后的任务",
            "cron_expr": "0 10 * * *",
            "target_type": "private",
            "target_id": "u2",
            "prompt_template": "新定义",
        },
    )

    assert response.status_code == 200
    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    updated = db_session.get(type(task), task.id)
    assert outbox.status == "cancelled"
    assert updated.name == "修改后的任务"
    assert updated.target_id == "u2"


def test_task_update_rejects_leased_delivery_and_rolls_back(
    client,
    db_session,
):
    from core.outbound_delivery import claim_due_outbox
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )
    from tests.async_helpers import run_async

    _seed_scheduled_outbox_control(db_session)
    task = _seed_manual_task(db_session)
    queued = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="leased-before-update",
            config=ScheduledTaskProducerConfig.for_tests(),
            generator=lambda _snapshot: "投递中的旧内容",
        )
    )
    claim = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=60,
        endpoint_config_revision="test-revision",
    )
    assert claim is not None and claim.outbox_id == queued.outbox_id
    db_session.commit()

    response = client.put(
        f"/api/v1/tasks/{task.id}",
        json={
            "name": "不应生效的修改",
            "cron_expr": "0 11 * * *",
            "target_type": "private",
            "target_id": "u3",
            "prompt_template": "不应生效",
        },
    )

    assert response.status_code == 409
    db_session.expire_all()
    unchanged = db_session.get(type(task), task.id)
    assert unchanged.name == "测试任务"
    assert unchanged.target_id == "u1"


def test_task_list_hides_target_and_reports_delivery_watermarks(
    client,
    db_session,
):
    task = _seed_manual_task(db_session)
    task.last_attempt_at = datetime(2026, 7, 15, 4, 1, 0)
    task.last_success_at = datetime(2026, 7, 15, 4, 2, 0)
    task.delivery_status = "delivered"
    db_session.commit()

    response = client.get("/api/v1/tasks")

    assert response.status_code == 200
    body = response.json()[0]
    assert "target" not in body
    assert task.target_id not in response.text
    assert body["target_type"] == "private"
    assert body["target_configured"] is True
    assert body["last_attempt_at"] == "2026-07-15T04:01:00"
    assert body["last_success_at"] == "2026-07-15T04:02:00"
    assert body["delivery_status"] == "delivered"


def test_task_delete_cancels_pending_delivery_but_keeps_audit_leaf(
    client,
    db_session,
):
    from core.database import OutboundDeliveryOutbox, ScheduledTask
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )
    from tests.async_helpers import run_async

    _seed_scheduled_outbox_control(db_session)
    task = _seed_manual_task(db_session)
    task_id = task.id
    queued = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task_id,
            trigger_type="manual",
            manual_idempotency_key="pending-before-delete",
            config=ScheduledTaskProducerConfig.for_tests(),
            generator=lambda _snapshot: "删除前生成的内容",
        )
    )

    response = client.delete(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(ScheduledTask, task_id) is None
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    assert outbox is not None
    assert outbox.status == "cancelled"


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
