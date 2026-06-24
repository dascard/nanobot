from tests.async_helpers import run_async
from types import SimpleNamespace


def test_schedule_task_run_uses_push_envelope(monkeypatch, db_session):
    from core import daily_digest
    from core.database import ScheduledTask
    from creatures.nanobot.prompts.skills.schedule_task import tool as schedule_tool_module
    from creatures.nanobot.prompts.skills.schedule_task.tool import ScheduleTaskTool

    task = ScheduledTask(
        name="即时推送",
        cron_expr="0 9 * * *",
        target_type="group",
        target_id="10001",
        prompt_template="生成今日简报",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    task_id = task.id
    calls = []

    async def fake_generate(_task):
        return "今日简报内容"

    async def fake_push_envelope_to_qq(target_type, target_id, envelope):
        calls.append((target_type, target_id, envelope))
        return True

    async def forbidden_push_to_qq(*_args, **_kwargs):
        raise AssertionError("schedule_task run must not call push_to_qq directly")

    monkeypatch.setattr(daily_digest, "_generate_task_message", fake_generate)
    monkeypatch.setattr(
        schedule_tool_module,
        "_generate_task_message",
        fake_generate,
        raising=False,
    )
    monkeypatch.setattr(daily_digest, "push_envelope_to_qq", fake_push_envelope_to_qq)
    monkeypatch.setattr(
        schedule_tool_module,
        "push_envelope_to_qq",
        fake_push_envelope_to_qq,
        raising=False,
    )
    monkeypatch.setattr(daily_digest, "push_to_qq", forbidden_push_to_qq)
    monkeypatch.setattr(
        schedule_tool_module,
        "push_to_qq",
        forbidden_push_to_qq,
        raising=False,
    )

    result = run_async(ScheduleTaskTool().execute({"action": "run", "task_id": task_id}))

    assert result.success
    assert calls
    target_type, target_id, envelope = calls[0]
    assert target_type == "group"
    assert target_id == "10001"
    assert envelope["reply"] == "今日简报内容"
    assert envelope["messages"] == [{"type": "text", "text": "今日简报内容"}]
    assert envelope["meta"]["platform"] == "qq"
    assert envelope["meta"]["chat_type"] == "group"
    assert envelope["meta"]["source"] == "schedule_task_tool"
    assert envelope["meta"]["task_id"] == task_id
    db_session.expire_all()
    assert db_session.get(ScheduledTask, task_id).last_run_at is not None


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
