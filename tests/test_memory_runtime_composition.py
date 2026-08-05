from __future__ import annotations

import pytest

from core.memory_provider import (
    MemoryProviderInitContext,
    MemoryToolCall,
    MemoryToolSchemaContext,
)
from nanobot_kt.memory_runtime import (
    MEMORY_PROVIDER_DESCRIPTORS,
    bind_memory_tool_runtime,
    build_memory_provider_runtime,
    memory_provider_registry_snapshot,
    reset_memory_tool_runtime,
)
from nanobot_kt.tools.memory_query import MemoryQueryTool


@pytest.mark.asyncio
async def test_memory_runtime_composition_freezes_explicit_tool_ownership() -> None:
    calls = []

    async def execute_memory(arguments):
        calls.append(dict(arguments))
        return {
            "status": "success",
            "output": "命中一条摘要",
            "metadata": {"source": "fake"},
        }

    runtime = build_memory_provider_runtime(handlers={"memory_query": execute_memory})
    snapshot = runtime.registry_snapshot
    assert snapshot == memory_provider_registry_snapshot()
    assert snapshot.ordered_ids == ("knowledge", "memory", "sticker")
    assert snapshot.generation > 0
    assert len(snapshot.sha256) == 64
    await runtime.initialize(MemoryProviderInitContext(runtime_id="kt13:test"))

    schemas = await runtime.tool_schemas(
        MemoryToolSchemaContext(request_id="req-1", session_id="private_u1")
    )

    assert [item.id for item in MEMORY_PROVIDER_DESCRIPTORS] == [
        "memory",
        "knowledge",
        "sticker",
    ]
    assert {str(schema["function"]["name"]) for schema in schemas} == {
        "memory_query",
        "knowledge_query",
        "sticker_search",
    }
    result = await runtime.handle_tool_call(
        MemoryToolCall(
            request_id="req-1",
            session_id="private_u1",
            call_id="call-1",
            name="memory_query",
            arguments={"query": "测试"},
        )
    )

    assert calls == [{"query": "测试"}]
    assert result == {
        "status": "success",
        "output": "命中一条摘要",
        "metadata": {"source": "fake"},
    }

    token = bind_memory_tool_runtime(
        runtime,
        request_id="req-2",
        session_id="private_u1",
        principal_id="qq:user:u1",
    )
    try:
        kt_result = await MemoryQueryTool()._execute({"query": "生产路径"})
    finally:
        reset_memory_tool_runtime(token)

    assert calls[-1] == {"query": "生产路径"}
    assert kt_result.error is None
    assert kt_result.output == "命中一条摘要"

    await runtime.shutdown()
