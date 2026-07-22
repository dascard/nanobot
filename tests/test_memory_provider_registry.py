from __future__ import annotations

from types import MappingProxyType

import pytest

from core.memory_provider import (
    FakeMemoryProvider,
    MemoryCompactionContext,
    MemoryDelegationContext,
    MemoryPrefetchContext,
    MemoryPromptContext,
    MemoryProviderContractError,
    MemoryProviderDependencyError,
    MemoryProviderDescriptor,
    MemoryProviderInitContext,
    MemoryProviderRegistry,
    MemoryProviderRegistryError,
    MemoryProviderRuntime,
    MemorySessionContext,
    MemorySyncTurnContext,
    MemoryToolCall,
    MemoryToolSchemaContext,
)


def _descriptor(
    provider_id: str,
    *,
    priority: int = 100,
    dependencies: tuple[str, ...] = (),
    tool_names: tuple[str, ...] = (),
    capabilities=None,
    failure_policy: str = "fail_closed",
) -> MemoryProviderDescriptor:
    kwargs = {}
    if capabilities is not None:
        kwargs["capabilities"] = frozenset(capabilities)
    return MemoryProviderDescriptor(
        id=provider_id,
        display_name=provider_id,
        priority=priority,
        dependencies=dependencies,
        tool_names=tool_names,
        failure_policy=failure_policy,
        **kwargs,
    )


def test_registry_requires_matching_key_and_descriptor_id() -> None:
    registry = MemoryProviderRegistry()
    descriptor = _descriptor("semantic")

    with pytest.raises(MemoryProviderRegistryError, match="不一致"):
        registry.register(
            "other",
            descriptor,
            lambda: FakeMemoryProvider(descriptor),
        )


def test_registry_rejects_duplicate_provider_and_tool_ownership() -> None:
    registry = MemoryProviderRegistry()
    first = _descriptor("first", tool_names=("memory_search",))
    second = _descriptor("second", tool_names=("memory_search",))
    registry.register("first", first, lambda: FakeMemoryProvider(first))

    with pytest.raises(MemoryProviderRegistryError, match="重复"):
        registry.register("first", first, lambda: FakeMemoryProvider(first))

    registry.register("second", second, lambda: FakeMemoryProvider(second))
    with pytest.raises(MemoryProviderRegistryError, match="memory_search"):
        registry.freeze()


def test_registry_freeze_validates_dependencies_and_cycles() -> None:
    missing = MemoryProviderRegistry()
    descriptor = _descriptor("consumer", dependencies=("missing",))
    missing.register("consumer", descriptor, lambda: FakeMemoryProvider(descriptor))
    with pytest.raises(MemoryProviderDependencyError, match="missing"):
        missing.freeze()

    cyclic = MemoryProviderRegistry()
    first = _descriptor("first", dependencies=("second",))
    second = _descriptor("second", dependencies=("first",))
    cyclic.register("first", first, lambda: FakeMemoryProvider(first))
    cyclic.register("second", second, lambda: FakeMemoryProvider(second))
    with pytest.raises(MemoryProviderDependencyError, match="循环"):
        cyclic.freeze()


def test_descriptor_requires_explicit_tools_capability_for_owned_tools() -> None:
    with pytest.raises(ValueError, match="tools capability"):
        _descriptor(
            "semantic",
            tool_names=("memory_search",),
            capabilities={"prefetch"},
        )

    with pytest.raises(ValueError, match="capabilities"):
        _descriptor("semantic", capabilities={"unknown"})


def test_frozen_registry_is_deterministic_and_read_only() -> None:
    registry = MemoryProviderRegistry()
    late = _descriptor("late", priority=1, dependencies=("base",))
    alpha = _descriptor("alpha", priority=20)
    base = _descriptor("base", priority=30)
    registry.register("late", late, lambda: FakeMemoryProvider(late))
    registry.register("base", base, lambda: FakeMemoryProvider(base))
    registry.register("alpha", alpha, lambda: FakeMemoryProvider(alpha))

    registry.freeze()

    assert [item.id for item in registry.descriptors()] == ["alpha", "base", "late"]
    assert isinstance(registry.registrations(), MappingProxyType)
    with pytest.raises(TypeError):
        registry.registrations()["other"] = object()  # type: ignore[index]
    with pytest.raises(MemoryProviderRegistryError, match="冻结"):
        registry.register("other", _descriptor("other"), lambda: object())


@pytest.mark.asyncio
async def test_runtime_exercises_the_complete_provider_lifecycle() -> None:
    descriptor = _descriptor("semantic", tool_names=("memory_search",))
    provider = FakeMemoryProvider(
        descriptor,
        prompt_content="长期记忆摘要",
        prefetch_items=("memory-1",),
        tool_schemas=(
            {
                "type": "function",
                "function": {"name": "memory_search", "parameters": {}},
            },
        ),
        tool_result={"status": "success"},
    )
    registry = MemoryProviderRegistry()
    registry.register("semantic", descriptor, lambda: provider)
    runtime = MemoryProviderRuntime(registry.freeze())

    await runtime.initialize(MemoryProviderInitContext(runtime_id="runtime-1"))
    await runtime.on_session_start(MemorySessionContext(session_id="session-1"))
    prompt_blocks = await runtime.system_prompt_blocks(
        MemoryPromptContext(request_id="request-1", session_id="session-1")
    )
    prefetched = await runtime.prefetch(
        MemoryPrefetchContext(
            request_id="request-1",
            session_id="session-1",
            query="今天聊什么",
        )
    )
    await runtime.sync_turn(
        MemorySyncTurnContext(
            request_id="request-1",
            session_id="session-1",
            user_content="你好",
            assistant_content="你好呀",
        )
    )
    schemas = await runtime.tool_schemas(
        MemoryToolSchemaContext(request_id="request-1", session_id="session-1")
    )
    tool_result = await runtime.handle_tool_call(
        MemoryToolCall(
            request_id="request-1",
            session_id="session-1",
            call_id="call-1",
            name="memory_search",
            arguments={"query": "今天"},
        )
    )
    await runtime.on_compaction(
        MemoryCompactionContext(
            session_id="session-1",
            source_turn_count=20,
            retained_turn_count=8,
        )
    )
    delegation = MemoryDelegationContext(
        session_id="session-1",
        delegation_id="delegation-1",
        target="research-agent",
    )
    await runtime.on_delegation_start(delegation)
    await runtime.on_delegation_end(delegation)
    await runtime.on_session_end(MemorySessionContext(session_id="session-1"))
    await runtime.shutdown()

    assert [block.content for block in prompt_blocks] == ["长期记忆摘要"]
    assert prefetched == ("memory-1",)
    assert schemas == provider.configured_tool_schemas
    assert tool_result == {"status": "success"}
    assert provider.call_names == [
        "initialize",
        "on_session_start",
        "system_prompt_block",
        "prefetch",
        "sync_turn",
        "tool_schemas",
        "handle_tool_call",
        "on_compaction",
        "on_delegation_start",
        "on_delegation_end",
        "on_session_end",
        "shutdown",
    ]


@pytest.mark.asyncio
async def test_runtime_initializes_by_dependency_and_shuts_down_in_reverse() -> None:
    events: list[str] = []
    base_descriptor = _descriptor("base", priority=50)
    consumer_descriptor = _descriptor(
        "consumer",
        priority=1,
        dependencies=("base",),
    )
    base = FakeMemoryProvider(base_descriptor, event_log=events)
    consumer = FakeMemoryProvider(consumer_descriptor, event_log=events)
    registry = MemoryProviderRegistry()
    registry.register("consumer", consumer_descriptor, lambda: consumer)
    registry.register("base", base_descriptor, lambda: base)
    runtime = MemoryProviderRuntime(registry.freeze())

    await runtime.initialize(MemoryProviderInitContext(runtime_id="runtime-1"))
    await runtime.shutdown()

    assert events == [
        "base.initialize",
        "consumer.initialize",
        "consumer.shutdown",
        "base.shutdown",
    ]


@pytest.mark.asyncio
async def test_runtime_fails_closed_for_invalid_factory_and_rolls_back_startup() -> None:
    events: list[str] = []
    base_descriptor = _descriptor("base")
    invalid_descriptor = _descriptor("invalid", dependencies=("base",))
    base = FakeMemoryProvider(base_descriptor, event_log=events)
    registry = MemoryProviderRegistry()
    registry.register("base", base_descriptor, lambda: base)
    registry.register("invalid", invalid_descriptor, lambda: object())
    runtime = MemoryProviderRuntime(registry.freeze())

    with pytest.raises(MemoryProviderContractError, match="invalid"):
        await runtime.initialize(MemoryProviderInitContext(runtime_id="runtime-1"))

    assert events == ["base.initialize", "base.shutdown"]


@pytest.mark.asyncio
async def test_optional_failure_policy_skips_hook_and_records_safe_diagnostics() -> None:
    descriptor = _descriptor(
        "optional",
        capabilities={"prefetch"},
        failure_policy="skip_optional",
    )

    class FailingProvider(FakeMemoryProvider):
        async def prefetch(self, context):
            self._record("prefetch", context)
            raise RuntimeError("不得进入诊断的查询正文 SECRET_QUERY")

    provider = FailingProvider(descriptor)
    registry = MemoryProviderRegistry()
    registry.register("optional", descriptor, lambda: provider)
    runtime = MemoryProviderRuntime(registry.freeze())
    await runtime.initialize(MemoryProviderInitContext(runtime_id="runtime-1"))

    result = await runtime.prefetch(
        MemoryPrefetchContext(
            request_id="request-1",
            session_id="session-1",
            query="SECRET_QUERY",
        )
    )
    diagnostic = runtime.diagnostics()[0]

    assert result == ()
    assert diagnostic.state == "initialized"
    assert diagnostic.failure_policy == "skip_optional"
    assert diagnostic.call_counts["prefetch"] == 1
    assert diagnostic.failure_counts["prefetch"] == 1
    assert diagnostic.last_error_type == "RuntimeError"
    assert "SECRET_QUERY" not in str(diagnostic.metadata())

    await runtime.shutdown()
    assert runtime.diagnostics()[0].state == "stopped"


@pytest.mark.asyncio
async def test_tool_call_remains_fail_closed_under_optional_failure_policy() -> None:
    descriptor = _descriptor(
        "optional",
        tool_names=("memory_search",),
        capabilities={"tools"},
        failure_policy="skip_optional",
    )

    class FailingProvider(FakeMemoryProvider):
        async def handle_tool_call(self, call):
            self._record("handle_tool_call", call)
            raise RuntimeError("tool failed")

    provider = FailingProvider(descriptor)
    registry = MemoryProviderRegistry()
    registry.register("optional", descriptor, lambda: provider)
    runtime = MemoryProviderRuntime(registry.freeze())
    await runtime.initialize(MemoryProviderInitContext(runtime_id="runtime-1"))

    with pytest.raises(RuntimeError, match="tool failed"):
        await runtime.handle_tool_call(
            MemoryToolCall(
                request_id="request-1",
                session_id="session-1",
                call_id="call-1",
                name="memory_search",
            )
        )

    diagnostic = runtime.diagnostics()[0]
    assert diagnostic.failure_counts["handle_tool_call"] == 1
