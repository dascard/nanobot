from tests.async_helpers import run_async
from types import SimpleNamespace
from datetime import datetime, timedelta


def _seed_scheduled_outbox_control(db_session) -> None:
    from core.database import OutboundDeliveryControl

    now = datetime(2026, 7, 15, 4, 0, 0)
    db_session.add(OutboundDeliveryControl(
        source_type="scheduled_task",
        mode="outbox_active",
        cutover_epoch=1,
        effective_from=now - timedelta(days=1),
        protocol_version=2,
        writer_version=0,
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    ))
    db_session.commit()


def _seed_task(db_session):
    from core.database import ScheduledTask

    task = ScheduledTask(
        name="即时推送",
        cron_expr="0 9 * * *",
        target_type="group",
        target_id="10001",
        prompt_template="生成今日简报",
        enabled=True,
        delivery_status="idle",
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


def test_schedule_task_run_requires_explicit_idempotency_key(
    monkeypatch,
    db_session,
):
    from core import daily_digest
    from creatures.nanobot.prompts.skills.schedule_task.tool import ScheduleTaskTool

    _seed_scheduled_outbox_control(db_session)
    task = _seed_task(db_session)

    async def forbidden_generate(*_args, **_kwargs):
        raise AssertionError("缺少幂等键时不得调用模型")

    monkeypatch.setattr(daily_digest, "_generate_task_message", forbidden_generate)
    result = run_async(ScheduleTaskTool().execute({
        "action": "run",
        "task_id": task.id,
    }))

    assert not result.success
    assert "幂等" in str(result.error)


def test_schedule_task_run_queues_and_reports_not_delivered(monkeypatch, db_session):
    from core import daily_digest
    from core.database import OutboundDeliveryOutbox, ScheduledTask
    from creatures.nanobot.prompts.skills.schedule_task import tool as schedule_tool_module
    from creatures.nanobot.prompts.skills.schedule_task.tool import ScheduleTaskTool

    _seed_scheduled_outbox_control(db_session)
    task = _seed_task(db_session)
    task_id = task.id

    async def fake_generate(_task):
        return "今日简报内容"

    async def forbidden_push(*_args, **_kwargs):
        raise AssertionError("schedule_task run 不得直接调用 QQ push")

    monkeypatch.setattr(daily_digest, "_generate_task_message", fake_generate)
    monkeypatch.setattr(
        schedule_tool_module,
        "_generate_task_message",
        fake_generate,
        raising=False,
    )
    monkeypatch.setattr(daily_digest, "push_envelope_to_qq", forbidden_push)
    monkeypatch.setattr(
        schedule_tool_module,
        "push_envelope_to_qq",
        forbidden_push,
        raising=False,
    )
    monkeypatch.setattr(daily_digest, "push_to_qq", forbidden_push)
    monkeypatch.setattr(
        schedule_tool_module,
        "push_to_qq",
        forbidden_push,
        raising=False,
    )

    result = run_async(ScheduleTaskTool().execute({
        "action": "run",
        "task_id": task_id,
        "idempotency_key": "tool-request-1",
    }))

    assert result.success
    assert "已入队" in result.output
    assert "已执行并推送" not in result.output
    assert db_session.query(OutboundDeliveryOutbox).count() == 1
    db_session.expire_all()
    assert db_session.get(ScheduledTask, task_id).last_run_at is not None
    assert db_session.get(ScheduledTask, task_id).last_success_at is None


def test_schedule_task_schema_declares_manual_idempotency_key():
    from core.tool_schema_preview import STATIC_TOOL_SCHEMAS
    from creatures.nanobot.prompts.skills.schedule_task.tool import ScheduleTaskTool

    runtime_properties = ScheduleTaskTool().get_parameters_schema()["properties"]
    preview_properties = STATIC_TOOL_SCHEMAS["schedule_task"]["parameters"]["properties"]
    assert "idempotency_key" in runtime_properties
    assert runtime_properties["idempotency_key"] == preview_properties["idempotency_key"]


def test_schedule_task_toggle_cancels_pending_delivery(monkeypatch, db_session):
    from core.database import OutboundDeliveryOutbox
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )
    from creatures.nanobot.prompts.skills.schedule_task.tool import ScheduleTaskTool

    _seed_scheduled_outbox_control(db_session)
    task = _seed_task(db_session)
    queued = run_async(
        enqueue_scheduled_task_occurrence(
            db_session,
            task_id=task.id,
            trigger_type="manual",
            manual_idempotency_key="tool-toggle-pending",
            config=ScheduledTaskProducerConfig.for_tests(),
            generator=lambda _snapshot: "禁用前生成的内容",
        )
    )

    result = run_async(ScheduleTaskTool().execute({
        "action": "toggle",
        "task_id": task.id,
    }))

    assert result.success
    db_session.expire_all()
    assert not bool(db_session.get(type(task), task.id).enabled)
    assert db_session.get(OutboundDeliveryOutbox, queued.outbox_id).status == "cancelled"


def test_schedule_task_list_hides_target_id(db_session):
    from creatures.nanobot.prompts.skills.schedule_task.tool import ScheduleTaskTool

    task = _seed_task(db_session)

    result = run_async(ScheduleTaskTool().execute({"action": "list"}))

    assert result.success
    assert task.target_id not in result.output
    assert "最近尝试" in result.output
    assert "最近成功" in result.output
    assert "投递状态" in result.output


def test_schedule_task_create_uses_runtime_context_target(monkeypatch):
    from core import database
    from creatures.nanobot.prompts.skills.schedule_task.tool import ScheduleTaskTool

    added = []

    class FakeDB:
        def add(self, obj):
            obj.id = 123
            added.append(obj)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeDB())
    context = SimpleNamespace(session=SimpleNamespace(extra={
        "nanobot_runtime_context": {
            "chat_type": "group",
            "is_group": True,
            "group_id": "10001",
            "user_id": "u1",
        }
    }))

    tool = ScheduleTaskTool()
    result = run_async(tool.execute({
        "action": "create",
        "name": "早报",
        "cron_expr": "0 9 * * *",
        "prompt_template": "生成今日简报",
    }, context=context))

    assert result.success
    assert added[0].target_type == "group"
    assert added[0].target_id == "10001"


def test_schedule_task_create_one_shot_via_schedule(monkeypatch):
    from core import database
    from creatures.nanobot.prompts.skills.schedule_task.tool import ScheduleTaskTool

    added = []

    class FakeDB:
        def add(self, obj):
            obj.id = 321
            added.append(obj)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeDB())

    result = run_async(ScheduleTaskTool().execute({
        "action": "create",
        "name": "半小时后提醒",
        "schedule": "30m",
        "target_type": "private",
        "target_id": "0000000000",
        "prompt_template": "提醒喝水",
    }))

    assert result.success
    assert added[0].schedule_kind == "once"
    assert added[0].cron_expr == ""
    assert added[0].next_fire_at is not None
    assert "下次触发" in result.output


def test_schedule_task_create_rejects_invalid_schedule(monkeypatch):
    from core import database
    from creatures.nanobot.prompts.skills.schedule_task.tool import ScheduleTaskTool

    class FakeDB:
        def add(self, obj):
            raise AssertionError("无效 schedule 不得落库")

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeDB())

    result = run_async(ScheduleTaskTool().execute({
        "action": "create",
        "name": "坏任务",
        "schedule": "明天九点",
        "target_type": "private",
        "target_id": "0000000000",
        "prompt_template": "提醒",
    }))

    assert not result.success
    assert "schedule 无效" in str(result.error)
