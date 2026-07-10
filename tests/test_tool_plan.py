import pytest


def _tool(name):
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def test_tool_plan_builds_prompt_schemas_and_stable_hash(monkeypatch):
    from core.tool_plan import ToolPlan

    monkeypatch.setattr(
        "core.tool_plan.build_effective_tool_schemas",
        lambda enabled: [_tool(name) for name, ok in sorted(enabled.items()) if ok],
    )

    plan = ToolPlan.from_effective_tools(
        enabled={"reply": True, "python_sandbox": False, "no_reply": True},
        disabled={"python_sandbox": "测试禁用"},
        chat_type="group",
    )
    same_plan = ToolPlan.from_effective_tools(
        enabled={"no_reply": True, "python_sandbox": False, "reply": True},
        disabled={"python_sandbox": "测试禁用"},
        chat_type="group",
    )

    assert plan.enabled == {"reply": True, "python_sandbox": False, "no_reply": True}
    assert plan.disabled == {"python_sandbox": "测试禁用"}
    assert plan.sent_tool_names == frozenset({"reply", "no_reply"})
    assert [tool["function"]["name"] for tool in plan.sent_tool_schemas] == ["no_reply", "reply"]
    assert plan.executable_tool_names == frozenset({"reply", "no_reply"})
    assert "[RuntimeTool]" in plan.runtime_tool_prompt
    assert "python_sandbox：测试禁用" in plan.runtime_tool_prompt
    assert len(plan.sha256) == 64
    assert plan.sha256 == same_plan.sha256


def test_tool_plan_exposes_memory_query_by_default_and_can_disable(db_session):
    from core.database import ToolOverride
    from core.tool_plan import build_tool_plan

    plan = build_tool_plan(chat_type="private", runtime_preset="full", db=db_session)
    assert "memory_query" in plan.sent_tool_names
    assert any(schema["function"]["name"] == "memory_query" for schema in plan.sent_tool_schemas)
    assert "memory_query" in plan.runtime_tool_prompt

    db_session.add(ToolOverride(
        tool_name="memory_query",
        scope_type="chat_type",
        scope_id="private",
        enabled=0,
        reason="测试禁用",
    ))
    db_session.commit()

    disabled_plan = build_tool_plan(chat_type="private", runtime_preset="full", db=db_session)
    assert "memory_query" not in disabled_plan.sent_tool_names
    assert all(schema["function"]["name"] != "memory_query" for schema in disabled_plan.sent_tool_schemas)
    assert "memory_query：测试禁用" in disabled_plan.runtime_tool_prompt


def test_platform_override_precedence_between_chat_type_group_and_user(db_session):
    from core.database import ToolOverride
    from core.runtime_tool_service import resolve_effective_tools

    db_session.add_all([
        ToolOverride(tool_name="memory_query", scope_type="chat_type", scope_id="private", enabled=0, reason="私聊禁用"),
        ToolOverride(tool_name="memory_query", scope_type="platform", scope_id="web", enabled=1, reason="Web 放开"),
        ToolOverride(tool_name="memory_query", scope_type="group", scope_id="g1", enabled=0, reason="群覆盖禁用"),
        ToolOverride(tool_name="memory_query", scope_type="user", scope_id="u1", enabled=1, reason="用户覆盖放开"),
    ])
    db_session.commit()

    enabled, disabled = resolve_effective_tools(
        chat_type="private",
        platform="web",
        group_id="g1",
        user_id="u1",
        runtime_preset="full",
        db=db_session,
    )
    assert enabled["memory_query"] is True
    assert "memory_query" not in disabled

    enabled_without_user, disabled_without_user = resolve_effective_tools(
        chat_type="private",
        platform="web",
        group_id="g1",
        user_id="",
        runtime_preset="full",
        db=db_session,
    )
    assert enabled_without_user["memory_query"] is False
    assert disabled_without_user["memory_query"] == "群覆盖禁用"

    enabled_platform_only, disabled_platform_only = resolve_effective_tools(
        chat_type="private",
        platform="web",
        group_id="",
        user_id="",
        runtime_preset="full",
        db=db_session,
    )
    assert enabled_platform_only["memory_query"] is True
    assert "memory_query" not in disabled_platform_only


def test_platform_override_cannot_bypass_none_or_hard_constraints(db_session):
    from core.database import ToolOverride
    from core.runtime_tool_service import resolve_effective_tools

    db_session.add_all([
        ToolOverride(tool_name="memory_query", scope_type="platform", scope_id="web", enabled=1, reason="Web 放开"),
        ToolOverride(tool_name="reply", scope_type="platform", scope_id="web", enabled=0, reason="错误禁用回复"),
        ToolOverride(tool_name="write", scope_type="platform", scope_id="web", enabled=1, reason="错误放开写文件"),
    ])
    db_session.commit()

    enabled_none, disabled_none = resolve_effective_tools(
        chat_type="private",
        platform="web",
        runtime_preset="none",
        db=db_session,
    )
    assert enabled_none["memory_query"] is False
    assert disabled_none["memory_query"] == "运行时预设=none"

    enabled_group, disabled_group = resolve_effective_tools(
        chat_type="group",
        platform="web",
        runtime_preset="full",
        db=db_session,
    )
    assert enabled_group["reply"] is True
    assert "reply" not in disabled_group
    assert enabled_group["write"] is False
    assert disabled_group["write"] == "群聊强制禁用"


def test_build_tool_plan_and_final_tools_pass_platform(db_session):
    from core.database import ToolOverride
    from core.final_tools import resolve_final_tools
    from core.tool_plan import build_tool_plan

    db_session.add(ToolOverride(
        tool_name="image_generation",
        scope_type="platform",
        scope_id="web",
        enabled=0,
        reason="Web 禁用图片生成",
    ))
    db_session.commit()

    plan = build_tool_plan(chat_type="private", platform="web", runtime_preset="full", db=db_session)
    final_tools = resolve_final_tools(chat_type="private", platform="web", runtime_preset="full", db=db_session)

    assert plan.enabled["image_generation"] is False
    assert "image_generation" not in plan.sent_tool_names
    assert "image_generation" not in final_tools.allowed
    assert final_tools.disabled["image_generation"] == "Web 禁用图片生成"


def test_filter_payload_tools_accepts_tool_plan(monkeypatch):
    from core.final_tools import filter_payload_tools
    from core.tool_plan import ToolPlan

    monkeypatch.setattr(
        "core.tool_plan.build_effective_tool_schemas",
        lambda enabled: [_tool(name) for name, ok in sorted(enabled.items()) if ok],
    )

    plan = ToolPlan.from_effective_tools(
        enabled={"reply": True, "python_sandbox": False},
        disabled={"python_sandbox": "测试禁用"},
        chat_type="private",
    )

    result = filter_payload_tools(
        {"tools": [_tool("reply"), _tool("python_sandbox"), _tool("skill")], "tool_choice": "auto"},
        plan,
    )

    assert [tool["function"]["name"] for tool in result["tools"]] == ["reply"]
    assert result["tool_choice"] == "auto"


def test_tool_plan_rejects_disabled_tool_execution():
    from core.tool_plan import ToolPlan, ToolPlanExecutionError

    plan = ToolPlan.from_effective_tools(
        enabled={"reply": True, "python_sandbox": False},
        disabled={"python_sandbox": "测试禁用"},
        chat_type="group",
        tool_schemas=[_tool("reply")],
    )

    plan.ensure_executable("reply")
    with pytest.raises(ToolPlanExecutionError) as exc:
        plan.ensure_executable("python_sandbox")

    assert "python_sandbox" in str(exc.value)
    assert "测试禁用" in str(exc.value)


def test_record_runtime_tool_decision_can_use_injected_db(monkeypatch, db_session):
    from core import database
    from core.database import RuntimeToolDecision
    from core.runtime_tool_service import record_runtime_tool_decision

    monkeypatch.setattr(
        database,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("must use injected db")),
    )

    record_runtime_tool_decision(
        session_id="s1",
        message_id="m1",
        chat_type="group",
        group_id="g1",
        user_id="u1",
        platform="web",
        runtime_preset="lightweight",
        enabled={"reply": True, "python_sandbox": False},
        disabled={"python_sandbox": "运行时轻量预设"},
        effective_tools=["reply"],
        db=db_session,
    )
    db_session.commit()

    row = db_session.query(RuntimeToolDecision).filter_by(session_id="s1").one()
    assert row.platform == "web"
    assert row.runtime_preset == "lightweight"
    assert row.effective_tools_json == '["reply"]'


def test_record_runtime_tool_decision_ignores_missing_observability_table():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from core.runtime_tool_service import record_runtime_tool_decision

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        result = record_runtime_tool_decision(
            session_id="s-missing-table",
            message_id="m1",
            chat_type="group",
            group_id="g1",
            user_id="u1",
            runtime_preset="full",
            enabled={"reply": True},
            disabled={},
            effective_tools=["reply"],
            db=session,
        )
    finally:
        session.close()

    assert result is False


def test_record_runtime_tool_decision_does_not_rollback_injected_db(monkeypatch):
    from core.runtime_tool_service import record_runtime_tool_decision
    import random

    class FakeDb:
        def __init__(self):
            self.rollback_called = False

        def add(self, _row):
            pass

        def flush(self):
            raise RuntimeError("observability table unavailable")

        def rollback(self):
            self.rollback_called = True

    fake_db = FakeDb()
    monkeypatch.setattr(random, "randint", lambda _a, _b: 2)

    result = record_runtime_tool_decision(
        session_id="s-no-rollback",
        message_id="m1",
        chat_type="group",
        group_id="g1",
        user_id="u1",
        runtime_preset="full",
        enabled={"reply": True},
        disabled={},
        effective_tools=["reply"],
        db=fake_db,
    )

    assert result is False
    assert fake_db.rollback_called is False


@pytest.mark.asyncio
async def test_tool_plan_guard_rejects_disabled_dispatch():
    from types import SimpleNamespace

    from kohakuterrarium.modules.plugin.base import PluginBlockError

    from core.tool_plan import ToolPlan, tool_plan_scope
    from nanobot_kt.tool_runtime import ToolPlanGuardPlugin

    plan = ToolPlan.from_effective_tools(
        enabled={"reply": True, "python_sandbox": False},
        disabled={"python_sandbox": "测试禁用"},
        chat_type="group",
        tool_schemas=[_tool("reply")],
    )
    plugin = ToolPlanGuardPlugin()

    with tool_plan_scope(plan):
        with pytest.raises(PluginBlockError) as exc:
            await plugin.pre_tool_dispatch(
                SimpleNamespace(name="python_sandbox", args={}),
                context=None,
            )

    assert "python_sandbox" in str(exc.value)
    assert "测试禁用" in str(exc.value)


def test_tool_plan_native_schema_filter_uses_sent_tool_schemas():
    from types import SimpleNamespace

    from core.tool_plan import ToolPlan, tool_plan_scope
    from nanobot_kt.tool_runtime import install_tool_plan_native_schema_filter

    web_search_schema = {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "项目 Web Search",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                    "provider": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    }
    plan = ToolPlan.from_effective_tools(
        enabled={"web_search": True},
        chat_type="private",
        tool_schemas=[web_search_schema],
    )
    controller = SimpleNamespace(
        _get_native_tool_schemas=lambda: [
            SimpleNamespace(
                name="web_search",
                description="KT 内置 Web Search",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            )
        ]
    )
    agent = SimpleNamespace(controller=controller)

    assert install_tool_plan_native_schema_filter(agent) is True

    with tool_plan_scope(plan):
        schemas = controller._get_native_tool_schemas()

    assert [schema.name for schema in schemas] == ["web_search"]
    props = schemas[0].parameters["properties"]
    assert {"query", "limit", "provider"} <= set(props)
    assert "max_results" not in props


@pytest.mark.asyncio
async def test_tool_plan_guard_blocks_disabled_native_subagent_call():
    from kohakuterrarium.modules.plugin.base import PluginBlockError

    from core.tool_plan import ToolPlan, tool_plan_scope
    from nanobot_kt.tool_runtime import ToolPlanGuardPlugin

    plan = ToolPlan.from_effective_tools(
        enabled={"web_search": True, "reply": True},
        chat_type="private",
        tool_schemas=[],
    )

    with tool_plan_scope(plan):
        with pytest.raises(PluginBlockError):
            await ToolPlanGuardPlugin().pre_subagent_run(
                "把内容写入持久记忆",
                name="memory_write",
                is_background=False,
            )
