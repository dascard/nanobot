from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


RESEARCH_TOOL_NAMES = frozenset({
    "web_search",
    "reply",
    "no_reply",
})


def _enabled_names(enabled: dict[str, bool]) -> set[str]:
    return {name for name, is_enabled in enabled.items() if is_enabled}


def test_research_runtime_preset_has_a_fixed_read_only_tool_ceiling(db_session):
    from core.runtime_tool_service import normalize_runtime_preset, resolve_effective_tools

    enabled, disabled = resolve_effective_tools(
        chat_type="private",
        user_id="research-user",
        platform="internal",
        runtime_preset="research",
        db=db_session,
    )

    assert normalize_runtime_preset("research") == "research"
    assert _enabled_names(enabled) == RESEARCH_TOOL_NAMES
    assert enabled["knowledge_query"] is False
    assert disabled["knowledge_query"] == "研究预设固定权限上限"


def test_research_runtime_preset_tool_override_cannot_expand_ceiling(db_session):
    from core.database import ToolOverride
    from core.runtime_tool_service import resolve_effective_tools

    db_session.add(ToolOverride(
        tool_name="python_sandbox",
        scope_type="user",
        scope_id="research-user",
        enabled=1,
        reason="尝试扩大研究权限",
    ))
    db_session.add(ToolOverride(
        tool_name="memory_query",
        scope_type="user",
        scope_id="research-user",
        enabled=1,
        reason="尝试恢复研究记忆权限",
    ))
    db_session.commit()

    enabled, disabled = resolve_effective_tools(
        chat_type="private",
        user_id="research-user",
        platform="internal",
        runtime_preset="research",
        db=db_session,
    )

    assert _enabled_names(enabled) == RESEARCH_TOOL_NAMES
    assert enabled["python_sandbox"] is False
    assert enabled["memory_query"] is False
    assert "python_sandbox" in disabled
    assert "memory_query" in disabled


def test_research_runtime_preset_force_enabled_cannot_reopen_memory_query(
    monkeypatch,
    db_session,
):
    from dataclasses import replace

    from core import runtime_tool_service

    from core.tool_registry import get_tool_descriptor, list_user_tool_descriptors

    original_descriptor = get_tool_descriptor("memory_query")
    assert original_descriptor is not None
    forced_definition = replace(
        original_descriptor.definition,
        force_enabled=True,
    )
    forced_descriptor = replace(
        original_descriptor,
        definition=forced_definition,
        availability_policy="force_enabled",
    )
    descriptors = tuple(
        forced_descriptor if item.name == "memory_query" else item
        for item in list_user_tool_descriptors()
    )
    monkeypatch.setattr(
        runtime_tool_service,
        "list_user_tool_descriptors",
        lambda: descriptors,
    )
    monkeypatch.setattr(
        runtime_tool_service,
        "get_tool_descriptor",
        lambda name: (
            forced_descriptor
            if name == "memory_query"
            else get_tool_descriptor(name)
        ),
    )
    monkeypatch.setattr(
        runtime_tool_service,
        "get_tool_def",
        lambda name: (
            forced_definition
            if name == "memory_query"
            else (
                descriptor.definition
                if (descriptor := get_tool_descriptor(name)) is not None
                else None
            )
        ),
    )

    enabled, disabled = runtime_tool_service.resolve_effective_tools(
        chat_type="private",
        user_id="research-user",
        platform="internal",
        runtime_preset="research",
        db=db_session,
    )

    assert enabled["memory_query"] is False
    assert disabled["memory_query"] == "研究预设固定权限上限"


def test_research_runtime_preset_preserves_explicit_web_search_disable(db_session):
    from core.database import SystemSetting
    from core.runtime_tool_service import resolve_effective_tools

    db_session.add(SystemSetting(
        key="tool.defaults.web_search.private_default",
        value="false",
        description="后台显式禁用研究网页搜索",
    ))
    db_session.commit()

    enabled, _disabled = resolve_effective_tools(
        chat_type="private",
        user_id="research-user",
        platform="internal",
        runtime_preset="research",
        db=db_session,
    )

    assert enabled["web_search"] is False
    assert _enabled_names(enabled) == RESEARCH_TOOL_NAMES - {"web_search"}


def test_research_tool_plan_sends_and_executes_only_effective_research_tools(db_session):
    from core.tool_plan import build_tool_plan

    plan = build_tool_plan(
        chat_type="private",
        user_id="research-user",
        platform="internal",
        runtime_preset="research",
        db=db_session,
    )

    schema_names = {
        schema["function"]["name"]
        for schema in plan.sent_tool_schemas
    }
    assert plan.sent_tool_names == RESEARCH_TOOL_NAMES
    assert plan.executable_tool_names == RESEARCH_TOOL_NAMES
    assert schema_names == RESEARCH_TOOL_NAMES


def _bridge_start_fixture(*, preinstalled: bool = False):
    plugins = SimpleNamespace(_plugins=[])
    plugins.register = lambda plugin: plugins._plugins.append(plugin)
    controller = SimpleNamespace(_get_native_tool_schemas=lambda: [])
    if preinstalled:
        plugins._plugins.append(SimpleNamespace(name="nanobot_tool_plan_guard"))
        controller._nanobot_tool_plan_schema_filter_installed = True
    agent = SimpleNamespace(
        plugins=plugins,
        controller=controller,
        registry=SimpleNamespace(list_tools=lambda: []),
        start=AsyncMock(),
    )
    config = SimpleNamespace(
        name="research-test-agent",
        system_prompt="original",
        include_tools_in_prompt=True,
        include_hints_in_prompt=True,
        skill_index_budget_bytes=1,
    )
    return config, agent


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_component", ["guard", "native_schema_filter"])
async def test_bridge_start_fails_closed_when_tool_plan_component_is_missing(
    monkeypatch,
    missing_component,
):
    from nanobot_kt import bridge as bridge_module
    from nanobot_kt import tool_runtime

    config, agent = _bridge_start_fixture()
    agent.start = AsyncMock(side_effect=AssertionError("缺少权限组件时不得启动 Agent"))
    monkeypatch.setattr(bridge_module, "load_agent_config", lambda _path: config)
    monkeypatch.setattr(bridge_module, "Agent", lambda *_args, **_kwargs: agent)

    def install_guard(target):
        if missing_component == "guard":
            return False
        target.plugins._plugins.append(SimpleNamespace(name="nanobot_tool_plan_guard"))
        return True

    def install_schema_filter(target):
        if missing_component == "native_schema_filter":
            return False
        target.controller._nanobot_tool_plan_schema_filter_installed = True
        return True

    monkeypatch.setattr(tool_runtime, "install_tool_plan_guard", install_guard)
    monkeypatch.setattr(
        tool_runtime,
        "install_tool_plan_native_schema_filter",
        install_schema_filter,
    )

    with pytest.raises(RuntimeError, match="ToolPlan"):
        await bridge_module.NanobotBridge().start()

    agent.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_bridge_start_accepts_preinstalled_tool_plan_components(monkeypatch):
    from nanobot_kt import bridge as bridge_module
    from nanobot_kt import tool_runtime

    config, agent = _bridge_start_fixture(preinstalled=True)
    monkeypatch.setattr(bridge_module, "load_agent_config", lambda _path: config)
    monkeypatch.setattr(bridge_module, "Agent", lambda *_args, **_kwargs: agent)
    monkeypatch.setattr(tool_runtime, "install_tool_plan_guard", lambda _agent: False)
    monkeypatch.setattr(
        tool_runtime,
        "install_tool_plan_native_schema_filter",
        lambda _agent: False,
    )

    await bridge_module.NanobotBridge().start()

    agent.start.assert_awaited_once()
