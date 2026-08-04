from __future__ import annotations

import copy
import json

import pytest


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"执行 {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


@pytest.mark.asyncio
async def test_prefix_cache_keeps_dynamic_request_outside_stable_prefix():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    common = {
        "chat_type": "private",
        "platform": "qq",
        "session_id": "private-prefix",
        "user_id": "prefix-user",
        "bot_name": "小南",
        "bot_aliases": ["南南"],
        "tool_schemas": [_tool("z_tool"), _tool("a_tool")],
    }
    first = await compile_prompt_plan(PromptCompileRequest(
        **common,
        event_time="2026-08-04 10:00:00 CST",
        current_message_id="message-1",
        user_input="第一条请求正文",
    ))
    second = await compile_prompt_plan(PromptCompileRequest(
        **common,
        event_time="2026-08-04 10:05:00 CST",
        current_message_id="message-2",
        user_input="第二条请求正文",
    ))

    assert [
        item["function"]["name"] for item in first.tool_schemas
    ] == ["a_tool", "z_tool"]
    assert first.prompt_sha256 != second.prompt_sha256
    assert first.prefix_cache_manifest["cache_key"] == (
        second.prefix_cache_manifest["cache_key"]
    )
    assert first.prefix_cache_manifest["stable_prefix_sha256"] == (
        second.prefix_cache_manifest["stable_prefix_sha256"]
    )
    assert first.prefix_cache_manifest["stable_entry_ids"][-1] == (
        "identity_context"
    )
    boundary = first.prefix_cache_manifest["dynamic_suffix_start_index"]
    assert 0 < boundary < len(first.messages)
    serialized = json.dumps(
        first.prefix_cache_manifest,
        ensure_ascii=False,
    )
    assert "第一条请求正文" not in serialized
    assert "2026-08-04 10:00:00" not in serialized


@pytest.mark.asyncio
async def test_prefix_cache_tool_order_is_independent_of_caller_order():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    common = {
        "chat_type": "private",
        "session_id": "private-tool-order",
        "user_id": "tool-order-user",
        "user_input": "固定请求",
        "event_time": "2026-08-04 12:00:00 CST",
    }
    first = await compile_prompt_plan(PromptCompileRequest(
        **common,
        tool_schemas=[_tool("beta"), _tool("alpha")],
    ))
    second = await compile_prompt_plan(PromptCompileRequest(
        **common,
        tool_schemas=[_tool("alpha"), _tool("beta")],
    ))

    assert first.tool_schemas == second.tool_schemas
    assert first.prompt_sha256 == second.prompt_sha256
    assert first.prefix_cache_manifest == second.prefix_cache_manifest


@pytest.mark.asyncio
async def test_cache_shape_uses_manifest_prefix_instead_of_dynamic_system_tail():
    from core.llm_trace_context import attach_prompt_prefix_cache_context
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest
    from foundation.llm.cache_shape import build_llm_cache_shape

    plan = await compile_prompt_plan(PromptCompileRequest(
        chat_type="private",
        session_id="private-shape",
        user_id="shape-user",
        event_time="2026-08-04 11:00:00 CST",
        user_input="当前请求",
        tool_schemas=[_tool("reply")],
    ))
    cache_context = attach_prompt_prefix_cache_context(
        {"session_id": "private-shape"},
        plan.prefix_cache_manifest,
    )
    first_request = plan.request_json
    second_request = copy.deepcopy(first_request)
    boundary = plan.prefix_cache_manifest["dynamic_suffix_start_index"]
    second_request["messages"][boundary]["content"] += "\n动态变化"

    first = build_llm_cache_shape(first_request, cache_context=cache_context)
    second = build_llm_cache_shape(second_request, cache_context=cache_context)

    assert first["schema_version"] == 2
    assert first["stable_prefix_source"] == "manifest"
    assert first["stable_prefix_contract_match"] is True
    assert second["stable_prefix_contract_match"] is True
    assert first["stable_prefix_sha256"] == second["stable_prefix_sha256"]
    assert first["prefix_cache_key"] == second["prefix_cache_key"]
    assert first["leading_system_sha256"] != second["leading_system_sha256"]


@pytest.mark.asyncio
async def test_prefix_cache_manifest_rejects_tampering():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.prefix_cache import (
        PromptPrefixCacheError,
        validate_prompt_prefix_cache_manifest,
    )
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(PromptCompileRequest(
        chat_type="private",
        session_id="private-prefix-tamper",
        user_id="prefix-tamper-user",
        user_input="检查签名",
    ))
    tampered = copy.deepcopy(plan.prefix_cache_manifest)
    tampered["stable_message_count"] += 1

    with pytest.raises(PromptPrefixCacheError, match="sha256"):
        validate_prompt_prefix_cache_manifest(tampered)


def test_prefix_cache_rejects_duplicate_tool_names():
    from core.prompt_v2.prefix_cache import (
        PromptPrefixCacheError,
        canonicalize_tool_schemas,
    )

    with pytest.raises(PromptPrefixCacheError, match="不能重复"):
        canonicalize_tool_schemas([_tool("same"), _tool("same")])
