"""现有 Memory/Knowledge/Sticker 工具到 MemoryProviderPort 的组合适配。

本模块把既有检索能力显式登记、冻结并接入生命周期；KT Tool Adapter 通过当前
请求绑定把真实调用交回 Provider Runtime，不再绕过工具所有权 Registry。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from core.memory_provider import (
    MemoryCompactionContext,
    MemoryDelegationContext,
    MemoryPrefetchContext,
    MemoryPromptBlock,
    MemoryPromptContext,
    MemoryProviderContractError,
    MemoryProviderDescriptor,
    MemoryProviderInitContext,
    MemoryProviderRegistry,
    MemoryProviderRuntime,
    MemorySessionContext,
    MemorySyncTurnContext,
    MemoryToolCall,
    MemoryToolSchemaContext,
)
from core.registry import RegistrySnapshot


MemoryToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(slots=True)
class MemoryToolRuntimeBinding:
    runtime: MemoryProviderRuntime
    request_id: str
    session_id: str
    principal_id: str
    call_index: int = 0

    def next_call_id(self, tool_name: str) -> str:
        self.call_index += 1
        return f"{self.request_id}:{tool_name}:{self.call_index}"


_CURRENT_MEMORY_TOOL_RUNTIME: ContextVar[MemoryToolRuntimeBinding | None] = ContextVar(
    "nanobot_memory_tool_runtime", default=None
)


def bind_memory_tool_runtime(
    runtime: MemoryProviderRuntime,
    *,
    request_id: str,
    session_id: str,
    principal_id: str,
) -> Token[MemoryToolRuntimeBinding | None]:
    """把已初始化 Runtime 绑定到当前 Bridge 请求。"""

    if not runtime.initialized:
        raise MemoryProviderContractError("不能绑定未初始化的 Memory Provider Runtime")
    return _CURRENT_MEMORY_TOOL_RUNTIME.set(
        MemoryToolRuntimeBinding(
            runtime=runtime,
            request_id=str(request_id or "").strip(),
            session_id=str(session_id or "").strip(),
            principal_id=str(principal_id or "").strip(),
        )
    )


def reset_memory_tool_runtime(
    token: Token[MemoryToolRuntimeBinding | None],
) -> None:
    _CURRENT_MEMORY_TOOL_RUNTIME.reset(token)


def has_memory_tool_runtime_binding() -> bool:
    binding = _CURRENT_MEMORY_TOOL_RUNTIME.get()
    return binding is not None and binding.runtime.initialized


async def dispatch_memory_tool_call(
    tool_name: str,
    arguments: Mapping[str, Any],
) -> Mapping[str, Any]:
    """经 Registry 所有权路由执行当前请求的 Memory 工具。"""

    binding = _CURRENT_MEMORY_TOOL_RUNTIME.get()
    if binding is None or not binding.runtime.initialized:
        raise MemoryProviderContractError("当前请求未绑定 Memory Provider Runtime")
    return await binding.runtime.handle_tool_call(
        MemoryToolCall(
            request_id=binding.request_id,
            session_id=binding.session_id,
            principal_id=binding.principal_id,
            call_id=binding.next_call_id(tool_name),
            name=tool_name,
            arguments=arguments,
        )
    )


def provider_result_to_tool_result(result: Mapping[str, Any]) -> Any:
    """在 KT Adapter 边界恢复框架 ToolResult。"""

    from nanobot_kt.optional_tool_api import ToolResult

    error = str(result.get("error") or "")
    if str(result.get("status") or "") == "error" and not error:
        error = "Memory Provider 执行失败"
    metadata = result.get("metadata")
    return ToolResult(
        output=str(result.get("output") or ""),
        exit_code=result.get("exit_code"),
        error=error or None,
        metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
    )


def _schema_name(schema: Mapping[str, Any]) -> str:
    function = schema.get("function")
    if not isinstance(function, Mapping):
        return ""
    return str(function.get("name") or "").strip()


class ExistingToolMemoryProvider:
    """把已存在的检索用例适配到 MemoryProviderPort。"""

    def __init__(
        self,
        descriptor: MemoryProviderDescriptor,
        handlers: Mapping[str, MemoryToolHandler],
    ) -> None:
        self._descriptor = descriptor
        self._handlers = dict(handlers)
        missing = set(descriptor.tool_names) - set(self._handlers)
        if missing:
            raise MemoryProviderContractError(
                f"Memory Provider {descriptor.id} 缺少工具执行器: {sorted(missing)}"
            )
        self._initialized = False

    @property
    def descriptor(self) -> MemoryProviderDescriptor:
        return self._descriptor

    async def initialize(self, context: MemoryProviderInitContext) -> None:
        del context
        self._initialized = True

    async def system_prompt_block(
        self,
        context: MemoryPromptContext,
    ) -> MemoryPromptBlock | None:
        del context
        return None

    async def prefetch(self, context: MemoryPrefetchContext) -> tuple[object, ...]:
        del context
        return ()

    async def sync_turn(self, context: MemorySyncTurnContext) -> None:
        del context

    async def tool_schemas(
        self,
        context: MemoryToolSchemaContext,
    ) -> tuple[Mapping[str, Any], ...]:
        raw_schemas = context.metadata.get("tool_schemas", ())
        schemas_by_name: dict[str, Mapping[str, Any]] = {}
        if isinstance(raw_schemas, (list, tuple)):
            for schema in raw_schemas:
                if isinstance(schema, Mapping):
                    name = _schema_name(schema)
                    if name:
                        schemas_by_name[name] = schema

        from core.tool_schema_preview import build_tool_schema

        return tuple(
            schemas_by_name.get(tool_name) or build_tool_schema(tool_name)
            for tool_name in self.descriptor.tool_names
        )

    async def handle_tool_call(self, call: MemoryToolCall) -> Mapping[str, Any]:
        if not self._initialized:
            raise MemoryProviderContractError(
                f"Memory Provider {self.descriptor.id} 尚未初始化"
            )
        handler = self._handlers.get(call.name)
        if handler is None or call.name not in self.descriptor.tool_names:
            raise MemoryProviderContractError(
                f"Memory Provider {self.descriptor.id} 不拥有工具 {call.name}"
            )
        result = await handler(dict(call.arguments))
        return _normalize_tool_result(result)

    async def on_session_start(self, context: MemorySessionContext) -> None:
        del context

    async def on_session_end(self, context: MemorySessionContext) -> None:
        del context

    async def on_compaction(self, context: MemoryCompactionContext) -> None:
        del context

    async def on_delegation_start(
        self,
        context: MemoryDelegationContext,
    ) -> None:
        del context

    async def on_delegation_end(self, context: MemoryDelegationContext) -> None:
        del context

    async def shutdown(self) -> None:
        self._initialized = False


MEMORY_PROVIDER_DESCRIPTORS = (
    MemoryProviderDescriptor(
        id="memory",
        display_name="摘要记忆",
        priority=10,
        tool_names=("memory_query",),
        capabilities=frozenset({"tools"}),
        failure_policy="fail_closed",
    ),
    MemoryProviderDescriptor(
        id="knowledge",
        display_name="外部知识库",
        priority=20,
        tool_names=("knowledge_query",),
        capabilities=frozenset({"tools"}),
        failure_policy="fail_closed",
    ),
    MemoryProviderDescriptor(
        id="sticker",
        display_name="表情记忆",
        priority=30,
        tool_names=("sticker_search",),
        capabilities=frozenset({"tools"}),
        failure_policy="fail_closed",
    ),
)


def _normalize_tool_result(result: Any) -> Mapping[str, Any]:
    """把 KT ToolResult 或测试 Mapping 投影到框架无关结果。"""

    if isinstance(result, Mapping):
        return dict(result)
    error = str(getattr(result, "error", "") or "")
    exit_code = getattr(result, "exit_code", None)
    get_text_output = getattr(result, "get_text_output", None)
    output = (
        str(get_text_output())
        if callable(get_text_output)
        else str(getattr(result, "output", "") or "")
    )
    metadata = getattr(result, "metadata", {})
    return {
        "status": (
            "success"
            if not error and (exit_code is None or int(exit_code) == 0)
            else "error"
        ),
        "output": output,
        "exit_code": exit_code,
        "error": error or None,
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
    }


def _default_tool_handlers() -> dict[str, MemoryToolHandler]:
    from app.tool_services.knowledge_query import execute_knowledge_query
    from app.tool_services.sticker_search import execute_sticker_search
    from nanobot_kt.tools.memory_query import execute_memory_query

    return {
        "memory_query": execute_memory_query,
        "knowledge_query": execute_knowledge_query,
        "sticker_search": execute_sticker_search,
    }


def _build_memory_provider_registry(
    handlers: Mapping[str, MemoryToolHandler],
) -> MemoryProviderRegistry:
    registry = MemoryProviderRegistry()
    for descriptor in MEMORY_PROVIDER_DESCRIPTORS:
        provider_handlers = {
            tool_name: handlers[tool_name]
            for tool_name in descriptor.tool_names
            if tool_name in handlers
        }
        registry.register(
            descriptor.id,
            descriptor,
            lambda descriptor=descriptor, provider_handlers=provider_handlers: (
                ExistingToolMemoryProvider(descriptor, provider_handlers)
            ),
        )
    return registry.freeze()


@lru_cache(maxsize=1)
def memory_provider_registry_snapshot() -> RegistrySnapshot[
    MemoryProviderDescriptor
]:
    """返回两种 Agent Runtime 共用的 canonical Provider 身份。"""

    return _build_memory_provider_registry({}).registry_snapshot


def build_memory_provider_runtime(
    *,
    handlers: Mapping[str, MemoryToolHandler] | None = None,
) -> MemoryProviderRuntime:
    """显式组合根：登记完成后冻结，运行期间禁止隐式覆盖。"""

    effective_handlers = _default_tool_handlers()
    if handlers:
        effective_handlers.update(handlers)
    return MemoryProviderRuntime(
        _build_memory_provider_registry(effective_handlers)
    )


__all__ = [
    "ExistingToolMemoryProvider",
    "MEMORY_PROVIDER_DESCRIPTORS",
    "MemoryToolHandler",
    "MemoryToolRuntimeBinding",
    "bind_memory_tool_runtime",
    "build_memory_provider_runtime",
    "dispatch_memory_tool_call",
    "has_memory_tool_runtime_binding",
    "memory_provider_registry_snapshot",
    "provider_result_to_tool_result",
    "reset_memory_tool_runtime",
]
