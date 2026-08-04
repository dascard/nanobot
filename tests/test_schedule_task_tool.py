import json
from tests.async_helpers import run_async
from datetime import datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def _trusted_schedule_task_runtime_context():
    from core.agent_runtime.request_scope import runtime_context_scope

    with runtime_context_scope(
        {
            "platform": "qq",
            "chat_type": "group",
            "is_group": True,
            "session_id": "group_10001",
            "group_id": "10001",
            "user_id": "u1",
        }
    ):
        yield


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
    from core.scheduled_task_contract import (
        apply_scheduled_task_owner,
        scheduled_task_owner_from_target,
    )

    task = ScheduledTask(
        name="即时推送",
        cron_expr="0 9 * * *",
        target_type="group",
        target_id="10001",
        prompt_template="生成今日简报",
        enabled=True,
        delivery_status="idle",
    )
    apply_scheduled_task_owner(
        task,
        scheduled_task_owner_from_target(
            target_type="group",
            target_id="10001",
            created_by_actor_id="u1",
        ),
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
    from nanobot_kt.tools.schedule_task import ScheduleTaskTool

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
    from core.database import (
        OutboundDeliveryOutbox,
        ScheduledTask,
        ScheduledTaskExecution,
    )
    import nanobot_kt.tools.schedule_task as schedule_tool_module
    from nanobot_kt.tools.schedule_task import ScheduleTaskTool

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
    assert db_session.query(ScheduledTaskExecution).count() == 1
    assert db_session.query(OutboundDeliveryOutbox).count() == 0
    db_session.expire_all()
    execution = db_session.query(ScheduledTaskExecution).one()
    assert execution.status == "pending"
    assert db_session.get(ScheduledTask, task_id).last_run_at is None
    assert db_session.get(ScheduledTask, task_id).last_success_at is None


def test_schedule_task_schema_declares_manual_idempotency_key():
    from core.tool_schema_preview import STATIC_TOOL_SCHEMAS
    from nanobot_kt.tools.schedule_task import ScheduleTaskTool

    runtime_properties = ScheduleTaskTool().get_parameters_schema()["properties"]
    preview_properties = STATIC_TOOL_SCHEMAS["schedule_task"]["parameters"]["properties"]
    assert "idempotency_key" in runtime_properties
    assert runtime_properties == preview_properties


def test_schedule_task_toggle_cancels_pending_delivery(monkeypatch, db_session):
    from core.database import OutboundDeliveryOutbox
    from core.scheduled_task_outbound import (
        ScheduledTaskProducerConfig,
        enqueue_scheduled_task_occurrence,
    )
    from nanobot_kt.tools.schedule_task import ScheduleTaskTool

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
    from nanobot_kt.tools.schedule_task import ScheduleTaskTool

    task = _seed_task(db_session)

    result = run_async(ScheduleTaskTool().execute({"action": "list"}))

    assert result.success
    assert task.target_id not in result.output
    assert "最近尝试" in result.output
    assert "最近成功" in result.output
    assert "投递状态" in result.output


def test_schedule_task_create_uses_runtime_context_target(monkeypatch):
    from core import database
    from nanobot_kt.tools.schedule_task import ScheduleTaskTool

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
    from core.agent_runtime.request_scope import runtime_context_scope

    with runtime_context_scope(
        {
            "chat_type": "group",
            "is_group": True,
            "group_id": "10001",
            "user_id": "u1",
        }
    ):
        tool = ScheduleTaskTool()
        result = run_async(tool.execute({
            "action": "create",
            "name": "早报",
            "cron_expr": "0 9 * * *",
            "prompt_template": "生成今日简报",
        }))

    assert result.success
    assert added[0].target_type == "group"
    assert added[0].target_id == "10001"
    assert added[0].owner_chat_stream_id == "qq:10001:group"
    assert added[0].created_by_actor_id == "u1"


def test_schedule_task_create_one_shot_via_schedule(monkeypatch):
    from core import database
    from nanobot_kt.tools.schedule_task import ScheduleTaskTool

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
        "target_type": "group",
        "target_id": "10001",
        "prompt_template": "提醒喝水",
    }))

    assert result.success
    assert added[0].schedule_kind == "once"
    assert added[0].cron_expr == ""
    assert added[0].next_fire_at is not None
    assert "下次触发" in result.output


def test_schedule_task_create_rejects_invalid_schedule(monkeypatch):
    from core import database
    from nanobot_kt.tools.schedule_task import ScheduleTaskTool

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
        "target_type": "group",
        "target_id": "10001",
        "prompt_template": "提醒",
    }))

    assert not result.success
    assert "schedule 无效" in str(result.error)


def test_schedule_task_cannot_list_or_mutate_another_owner(db_session):
    from core.database import ScheduledTask
    from core.scheduled_task_contract import (
        apply_scheduled_task_owner,
        scheduled_task_owner_from_target,
    )
    from nanobot_kt.tools.schedule_task import (
        ScheduleTaskTool,
    )

    own_task = _seed_task(db_session)
    other_task = ScheduledTask(
        name="OTHER_OWNER_MARKER",
        cron_expr="0 10 * * *",
        target_type="group",
        target_id="20002",
        prompt_template="其他群任务",
        enabled=True,
    )
    apply_scheduled_task_owner(
        other_task,
        scheduled_task_owner_from_target(
            target_type="group",
            target_id="20002",
            created_by_actor_id="u2",
        ),
    )
    db_session.add(other_task)
    db_session.commit()

    listed = run_async(
        ScheduleTaskTool().execute({"action": "list"})
    )
    mutated = run_async(
        ScheduleTaskTool().execute(
            {"action": "toggle", "task_id": other_task.id}
        )
    )

    assert listed.success
    assert str(own_task.name) in listed.output
    assert "OTHER_OWNER_MARKER" not in listed.output
    assert not mutated.success
    assert "不存在" in str(mutated.error)
    db_session.expire_all()
    assert bool(db_session.get(ScheduledTask, other_task.id).enabled)


def test_schedule_task_create_rejects_cross_owner_target(monkeypatch):
    from core import database
    from nanobot_kt.tools.schedule_task import (
        ScheduleTaskTool,
    )

    class FakeDB:
        def add(self, _obj):
            raise AssertionError("跨 owner 任务不得落库")

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeDB())

    result = run_async(
        ScheduleTaskTool().execute(
            {
                "action": "create",
                "name": "越权任务",
                "schedule": "30m",
                "target_type": "group",
                "target_id": "20002",
                "prompt_template": "不应创建",
            }
        )
    )

    assert not result.success
    assert "其他会话" in str(result.error)


def test_schedule_task_rejects_oversized_prompt_before_database(
    monkeypatch,
):
    from core import database
    from core.scheduled_task_contract import (
        MAX_SCHEDULED_TASK_PROMPT_CHARS,
    )
    from nanobot_kt.tools.schedule_task import (
        ScheduleTaskTool,
    )

    class FakeDB:
        def add(self, _obj):
            raise AssertionError("超限任务不得落库")

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeDB())

    result = run_async(
        ScheduleTaskTool().execute(
            {
                "action": "create",
                "name": "超限任务",
                "schedule": "30m",
                "prompt_template": (
                    "长" * (MAX_SCHEDULED_TASK_PROMPT_CHARS + 1)
                ),
            }
        )
    )

    assert not result.success
    assert "16000" in str(result.error)


def test_schedule_task_create_content_compiles_to_static_emit(monkeypatch):
    from core import database
    from nanobot_kt.tools.schedule_task import (
        ScheduleTaskTool,
    )

    added = []

    class FakeDB:
        def add(self, obj):
            obj.id = 456
            added.append(obj)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeDB())

    result = run_async(
        ScheduleTaskTool().execute(
            {
                "action": "create",
                "name": "固定提醒",
                "schedule": "30m",
                "content": "该喝水了",
            }
        )
    )

    assert result.success
    assert added[0].prompt_template == ""
    program = json.loads(added[0].program_json)
    assert [step["op"] for step in program["steps"]] == ["emit"]
    assert program["steps"][0]["content"] == "该喝水了"


def test_schedule_task_list_detail_returns_program_and_latest_failure(
    db_session,
):
    from core.database import ScheduledTaskExecution
    from core.scheduled_workflow import enqueue_scheduled_task_execution
    from nanobot_kt.tools.schedule_task import (
        ScheduleTaskTool,
    )

    task = _seed_task(db_session)
    queued = enqueue_scheduled_task_execution(
        db_session,
        task_id=task.id,
        trigger_type="manual",
        manual_idempotency_key="detail-failure",
    )
    execution = db_session.get(
        ScheduledTaskExecution,
        queued.execution_id,
    )
    execution.status = "failed"
    execution.last_error_code = "tool_unavailable"
    execution.last_error_summary = "sandbox_exec 当前不可用"
    db_session.commit()

    result = run_async(
        ScheduleTaskTool().execute(
            {"action": "list", "task_id": task.id}
        )
    )

    assert result.success
    payload = json.loads(result.output)
    assert [step["op"] for step in payload["program"]["steps"]] == [
        "model",
        "emit",
    ]
    assert payload["latest_execution"]["status"] == "failed"
    assert (
        payload["latest_execution"]["error_code"]
        == "tool_unavailable"
    )


def test_schedule_task_prompt_update_cannot_overwrite_static_program(
    db_session,
):
    from core.scheduled_task_contract import apply_scheduled_task_program
    from nanobot_kt.tools.schedule_task import (
        ScheduleTaskTool,
    )

    task = _seed_task(db_session)
    apply_scheduled_task_program(
        task,
        name=task.name,
        content="固定正文",
    )
    original_program_json = task.program_json
    db_session.commit()

    result = run_async(
        ScheduleTaskTool().execute(
            {
                "action": "update",
                "task_id": task.id,
                "prompt_template": "把任务改成模型生成",
            }
        )
    )

    assert not result.success
    assert "确定性 program" in str(result.error)
    db_session.expire_all()
    assert (
        db_session.get(type(task), task.id).program_json
        == original_program_json
    )


def test_schedule_task_create_rejects_unavailable_direct_program_tool(
    monkeypatch,
):
    from core import database
    from core.tool_plan import ToolPlan, tool_plan_scope
    from nanobot_kt.tools.schedule_task import (
        ScheduleTaskTool,
    )

    class FakeDB:
        def add(self, _obj):
            raise AssertionError("包含不可用工具的任务不得落库")

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeDB())
    plan = ToolPlan.from_effective_tools(
        enabled={"schedule_task": True, "sandbox_exec": False},
        disabled={"sandbox_exec": "当前会话未授予 Sandbox"},
        chat_type="group",
        tool_schemas=[],
    )
    program = {
        "version": 1,
        "steps": [
            {
                "id": "run",
                "op": "tool",
                "tool": "sandbox_exec",
                "args": {"command": "true"},
            },
            {
                "id": "done",
                "op": "emit",
                "content": "完成",
            },
        ],
    }

    with tool_plan_scope(plan):
        result = run_async(
            ScheduleTaskTool().execute(
                {
                    "action": "create",
                    "name": "不可运行任务",
                    "schedule": "every 5m",
                    "program": program,
                }
            )
        )

    assert not result.success
    assert "sandbox_exec" in str(result.error)
    assert "未授予 Sandbox" in str(result.error)


def test_schedule_task_rejects_unknown_action_without_database(monkeypatch):
    from core import database
    from nanobot_kt.tools.schedule_task import (
        ScheduleTaskTool,
    )

    monkeypatch.setattr(
        database,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(
            AssertionError("未知 action 不得打开数据库")
        ),
    )

    result = run_async(
        ScheduleTaskTool().execute({"action": "invent"})
    )

    assert not result.success
    assert "不支持的 action" in str(result.error)
