import json

import pytest


def _error_result(*, retryable: bool, stop: bool, code: str = "invalid_path"):
    from kohakuterrarium.modules.tool.base import ToolResult

    payload = {
        "status": "error",
        "summary": "路径无效",
        "next_actions": [],
        "artifacts": [],
        "error": {
            "code": code,
            "retryable": retryable,
            "hint": "",
            "stop": stop,
        },
    }
    return ToolResult(
        output=json.dumps(payload, ensure_ascii=False),
        metadata={"structured_content": payload},
    )


def test_tool_execution_state_records_non_retryable_call_without_stopping_run():
    from core.tool_execution_policy import ToolExecutionState

    state = ToolExecutionState(request_id="run-1")
    failure = state.record_result(
        "workspace_read",
        {"path": "missing.txt"},
        _error_result(retryable=False, stop=False),
    )

    assert failure is not None
    assert failure.code == "invalid_path"
    assert failure.retryable is False
    assert (
        state.duplicate_failure("workspace_read", {"path": "missing.txt"})
        == failure
    )
    assert state.duplicate_failure("workspace_read", {"path": "other.txt"}) is None


def test_tool_execution_state_stop_true_blocks_same_capability_not_whole_run():
    from core.tool_execution_policy import ToolExecutionState

    state = ToolExecutionState(request_id="run-2")
    state.record_result(
        "sandbox_exec",
        {"command": "false"},
        _error_result(
            retryable=True,
            stop=True,
            code="execution_timeout",
        ),
    )

    assert (
        state.duplicate_failure("sandbox_exec", {"command": "false"})
        is not None
    )
    assert state.family_failure("sandbox_poll") is None


@pytest.mark.asyncio
async def test_tool_loop_plugin_blocks_same_non_retryable_call_without_interrupt():
    from kohakuterrarium.modules.plugin.base import PluginBlockError
    from core.tool_execution_policy import (
        ToolExecutionState,
        reset_current_tool_execution_state,
        set_current_tool_execution_state,
    )
    from nanobot_kt.tool_runtime import ToolLoopControlPlugin

    plugin = ToolLoopControlPlugin()
    state = ToolExecutionState(request_id="run-3")
    token = set_current_tool_execution_state(state)
    try:
        args = {"path": "missing.txt"}
        await plugin.post_tool_execute(
            _error_result(retryable=False, stop=False),
            tool_name="workspace_read",
            job_id="job-1",
            args=args,
        )

        with pytest.raises(PluginBlockError) as exc_info:
            await plugin.pre_tool_execute(
                args,
                tool_name="workspace_read",
                job_id="job-2",
            )
    finally:
        reset_current_tool_execution_state(token)

    blocked = json.loads(str(exc_info.value))
    assert blocked["error"]["code"] == "duplicate_non_retryable_call"
    assert blocked["error"]["retryable"] is False
    assert blocked["error"]["stop"] is True
    assert state.duplicate_suppressed == 1


@pytest.mark.asyncio
async def test_tool_loop_plugin_does_not_block_retryable_failure():
    from core.tool_execution_policy import (
        ToolExecutionState,
        reset_current_tool_execution_state,
        set_current_tool_execution_state,
    )
    from nanobot_kt.tool_runtime import ToolLoopControlPlugin

    plugin = ToolLoopControlPlugin()
    state = ToolExecutionState(request_id="run-4")
    token = set_current_tool_execution_state(state)
    try:
        args = {"command": "echo ok"}
        await plugin.post_tool_execute(
            _error_result(retryable=True, stop=False),
            tool_name="sandbox_exec",
            job_id="job-1",
            args=args,
        )
        result = await plugin.pre_tool_execute(
            args,
            tool_name="sandbox_exec",
            job_id="job-2",
        )
    finally:
        reset_current_tool_execution_state(token)

    assert result is None
    assert state.blocked_failures == {}


@pytest.mark.asyncio
async def test_authorization_failure_blocks_same_tool_family_only():
    from kohakuterrarium.modules.plugin.base import PluginBlockError
    from core.tool_execution_policy import (
        ToolExecutionState,
        reset_current_tool_execution_state,
        set_current_tool_execution_state,
    )
    from nanobot_kt.tool_runtime import ToolLoopControlPlugin

    plugin = ToolLoopControlPlugin()
    state = ToolExecutionState(request_id="run-5")
    token = set_current_tool_execution_state(state)
    try:
        await plugin.post_tool_execute(
            _error_result(
                retryable=False,
                stop=True,
                code="authorization_failed",
            ),
            tool_name="sandbox_exec",
            job_id="job-stop",
            args={"command": "pwd"},
        )
        with pytest.raises(PluginBlockError) as exc_info:
            await plugin.pre_tool_execute(
                {"process_id": "proc-1"},
                tool_name="sandbox_poll",
                job_id="job-poll",
            )
        unrelated = await plugin.pre_tool_execute(
            {"path": "README.md"},
            tool_name="workspace_read",
            job_id="job-read",
        )
    finally:
        reset_current_tool_execution_state(token)

    blocked = json.loads(str(exc_info.value))
    assert blocked["error"]["code"] == "tool_family_blocked"
    assert state.family_suppressed == 1
    assert unrelated is None


def test_restrict_tool_plan_only_reduces_permissions():
    from core.tool_plan import ToolPlan, restrict_tool_plan

    schemas = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in ("reply", "no_reply", "workspace_read")
    ]
    plan = ToolPlan.from_effective_tools(
        enabled={
            "reply": True,
            "no_reply": True,
            "workspace_read": True,
            "sandbox_exec": False,
        },
        disabled={"sandbox_exec": "原本禁用"},
        tool_schemas=schemas,
    )

    restricted = restrict_tool_plan(
        plan,
        frozenset({"reply", "no_reply", "not_registered"}),
    )

    assert restricted.executable_tool_names == frozenset({"reply", "no_reply"})
    assert [item["function"]["name"] for item in restricted.sent_tool_schemas] == [
        "reply",
        "no_reply",
    ]
    assert restricted.enabled["workspace_read"] is False
    assert restricted.enabled["sandbox_exec"] is False


def test_nanobot_max_iterations_is_top_level_safety_budget():
    from kohakuterrarium.core.config import load_agent_config

    config = load_agent_config("creatures/nanobot")

    assert config.max_iterations == 12


def test_max_tool_rounds_is_declared_as_legacy_adapter_only():
    from core.config_registry import SETTING_DEFS

    setting = SETTING_DEFS["max_tool_rounds"]

    assert setting.owner_module == "core.legacy_adapter"
    assert "不作用于 KT 主链路" in setting.description
