"""Memory Provider Port 的确定性测试替身。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.memory_provider.contracts import (
    MemoryCompactionContext,
    MemoryDelegationContext,
    MemoryPrefetchContext,
    MemoryPromptBlock,
    MemoryPromptContext,
    MemoryProviderDescriptor,
    MemoryProviderInitContext,
    MemorySessionContext,
    MemorySyncTurnContext,
    MemoryToolCall,
    MemoryToolSchemaContext,
    freeze_mapping,
)


class FakeMemoryProvider:
    def __init__(
        self,
        descriptor: MemoryProviderDescriptor,
        *,
        prompt_content: str = "",
        prefetch_items: tuple[object, ...] = (),
        tool_schemas: tuple[Mapping[str, Any], ...] = (),
        tool_result: Mapping[str, Any] | None = None,
        event_log: list[str] | None = None,
    ) -> None:
        self._descriptor = descriptor
        self.prompt_content = prompt_content
        self.prefetch_items = tuple(prefetch_items)
        self.configured_tool_schemas = tuple(
            freeze_mapping(schema) for schema in tool_schemas
        )
        self.tool_result = freeze_mapping(tool_result or {})
        self.event_log = event_log
        self.calls: list[tuple[str, object | None]] = []

    @property
    def descriptor(self) -> MemoryProviderDescriptor:
        return self._descriptor

    @property
    def call_names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def _record(self, name: str, context: object | None = None) -> None:
        self.calls.append((name, context))
        if self.event_log is not None:
            self.event_log.append(f"{self.descriptor.id}.{name}")

    async def initialize(self, context: MemoryProviderInitContext) -> None:
        self._record("initialize", context)

    async def system_prompt_block(
        self,
        context: MemoryPromptContext,
    ) -> MemoryPromptBlock | None:
        self._record("system_prompt_block", context)
        if not self.prompt_content:
            return None
        return MemoryPromptBlock(
            provider_id=self.descriptor.id,
            content=self.prompt_content,
            priority=self.descriptor.priority,
        )

    async def prefetch(self, context: MemoryPrefetchContext) -> tuple[object, ...]:
        self._record("prefetch", context)
        return self.prefetch_items

    async def sync_turn(self, context: MemorySyncTurnContext) -> None:
        self._record("sync_turn", context)

    async def tool_schemas(
        self,
        context: MemoryToolSchemaContext,
    ) -> tuple[Mapping[str, Any], ...]:
        self._record("tool_schemas", context)
        return self.configured_tool_schemas

    async def handle_tool_call(self, call: MemoryToolCall) -> Mapping[str, Any]:
        self._record("handle_tool_call", call)
        return self.tool_result

    async def on_session_start(self, context: MemorySessionContext) -> None:
        self._record("on_session_start", context)

    async def on_session_end(self, context: MemorySessionContext) -> None:
        self._record("on_session_end", context)

    async def on_compaction(self, context: MemoryCompactionContext) -> None:
        self._record("on_compaction", context)

    async def on_delegation_start(
        self,
        context: MemoryDelegationContext,
    ) -> None:
        self._record("on_delegation_start", context)

    async def on_delegation_end(self, context: MemoryDelegationContext) -> None:
        self._record("on_delegation_end", context)

    async def shutdown(self) -> None:
        self._record("shutdown")
