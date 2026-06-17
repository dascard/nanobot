import asyncio
from tests.async_helpers import run_async
from types import SimpleNamespace


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
