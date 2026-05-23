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
