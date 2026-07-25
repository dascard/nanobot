"""显式 Memory Provider Registry 与生命周期容器。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from core.memory_provider.contracts import (
    MemoryCompactionContext,
    MemoryDelegationContext,
    MemoryPrefetchContext,
    MemoryPromptBlock,
    MemoryPromptContext,
    MemoryProviderCapability,
    MemoryProviderDescriptor,
    MemoryProviderDiagnostic,
    MemoryProviderInitContext,
    MemoryProviderPort,
    MemorySessionContext,
    MemorySyncTurnContext,
    MemoryToolCall,
    MemoryToolSchemaContext,
)
from core.registry import RegistryBuilder, RegistrySnapshot


class MemoryProviderRegistryError(RuntimeError):
    pass


class MemoryProviderDependencyError(MemoryProviderRegistryError):
    pass


class MemoryProviderContractError(MemoryProviderRegistryError):
    pass


MemoryProviderFactory = Callable[[], MemoryProviderPort]
_SKIPPED = object()


@dataclass(frozen=True, slots=True)
class MemoryProviderRegistration:
    descriptor: MemoryProviderDescriptor
    factory: MemoryProviderFactory


@dataclass(slots=True)
class _DiagnosticState:
    state: str = "registered"
    call_counts: dict[str, int] | None = None
    failure_counts: dict[str, int] | None = None
    last_error_type: str = ""

    def __post_init__(self) -> None:
        self.call_counts = dict(self.call_counts or {})
        self.failure_counts = dict(self.failure_counts or {})


class MemoryProviderRegistry:
    """仅在组合根启动阶段可写；冻结后才能创建运行时。"""

    def __init__(self) -> None:
        self._registrations: dict[str, MemoryProviderRegistration] = {}
        self._ordered_ids: tuple[str, ...] = ()
        self._frozen = False
        self._registry_snapshot: (
            RegistrySnapshot[MemoryProviderDescriptor] | None
        ) = None

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[MemoryProviderDescriptor]:
        if self._registry_snapshot is None:
            raise MemoryProviderRegistryError(
                "Memory Provider Registry 尚未冻结"
            )
        return self._registry_snapshot

    def register(
        self,
        key: str,
        descriptor: MemoryProviderDescriptor,
        factory: MemoryProviderFactory,
    ) -> None:
        if self._frozen:
            raise MemoryProviderRegistryError("Memory Provider Registry 已冻结")
        normalized_key = str(key or "").strip()
        if normalized_key != descriptor.id:
            raise MemoryProviderRegistryError(
                f"Registry key 与 descriptor.id 不一致: {normalized_key!r} != {descriptor.id!r}"
            )
        if normalized_key in self._registrations:
            raise MemoryProviderRegistryError(
                f"Memory Provider 重复注册: {normalized_key}"
            )
        if not callable(factory):
            raise MemoryProviderRegistryError(
                f"Memory Provider {normalized_key} factory 不可调用"
            )
        self._registrations[normalized_key] = MemoryProviderRegistration(
            descriptor=descriptor,
            factory=factory,
        )

    def freeze(self) -> "MemoryProviderRegistry":
        if self._frozen:
            return self
        self._validate_tool_ownership()
        self._ordered_ids = self._resolve_order()
        builder = RegistryBuilder[MemoryProviderDescriptor](
            "memory_provider"
        )
        for provider_id in sorted(self._registrations):
            builder.register(
                self._registrations[provider_id].descriptor
            )
        self._registry_snapshot = builder.freeze()
        self._frozen = True
        return self

    def registrations(self) -> Mapping[str, MemoryProviderRegistration]:
        return MappingProxyType(dict(self._registrations))

    def descriptors(self) -> tuple[MemoryProviderDescriptor, ...]:
        self._require_frozen()
        return tuple(
            self._registrations[provider_id].descriptor
            for provider_id in self._ordered_ids
        )

    def ordered_registrations(self) -> tuple[MemoryProviderRegistration, ...]:
        self._require_frozen()
        return tuple(
            self._registrations[provider_id] for provider_id in self._ordered_ids
        )

    def tool_owner(self, tool_name: str) -> str | None:
        self._require_frozen()
        for descriptor in self.descriptors():
            if tool_name in descriptor.tool_names:
                return descriptor.id
        return None

    def _require_frozen(self) -> None:
        if not self._frozen:
            raise MemoryProviderRegistryError("Memory Provider Registry 尚未冻结")

    def _validate_tool_ownership(self) -> None:
        owners: dict[str, str] = {}
        for provider_id, registration in self._registrations.items():
            for tool_name in registration.descriptor.tool_names:
                previous = owners.get(tool_name)
                if previous is not None:
                    raise MemoryProviderRegistryError(
                        f"Memory 工具 {tool_name} 所有权冲突: {previous}, {provider_id}"
                    )
                owners[tool_name] = provider_id

    def _resolve_order(self) -> tuple[str, ...]:
        known = set(self._registrations)
        for provider_id, registration in self._registrations.items():
            missing = set(registration.descriptor.dependencies) - known
            if missing:
                raise MemoryProviderDependencyError(
                    f"Memory Provider {provider_id} 依赖未注册 Provider: {sorted(missing)}"
                )

        remaining = set(known)
        resolved: list[str] = []
        resolved_set: set[str] = set()
        while remaining:
            ready = [
                provider_id
                for provider_id in remaining
                if set(
                    self._registrations[provider_id].descriptor.dependencies
                ).issubset(resolved_set)
            ]
            if not ready:
                raise MemoryProviderDependencyError(
                    f"Memory Provider 存在循环依赖: {sorted(remaining)}"
                )
            ready.sort(
                key=lambda provider_id: (
                    self._registrations[provider_id].descriptor.priority,
                    provider_id,
                )
            )
            for provider_id in ready:
                remaining.remove(provider_id)
                resolved.append(provider_id)
                resolved_set.add(provider_id)
        return tuple(resolved)


class MemoryProviderRuntime:
    """按冻结 Registry 编排 Provider 生命周期和请求钩子。"""

    def __init__(self, registry: MemoryProviderRegistry) -> None:
        if not registry.frozen:
            raise MemoryProviderRegistryError(
                "创建 MemoryProviderRuntime 前必须冻结 Registry"
            )
        self._registry = registry
        self._providers: tuple[MemoryProviderPort, ...] = ()
        self._provider_by_id: dict[str, MemoryProviderPort] = {}
        self._initialized = False
        self._diagnostics = {
            descriptor.id: _DiagnosticState()
            for descriptor in self._registry.descriptors()
        }

    @property
    def initialized(self) -> bool:
        return self._initialized

    def diagnostics(self) -> tuple[MemoryProviderDiagnostic, ...]:
        """返回确定性、无正文和无异常消息的 Provider 诊断快照。"""

        snapshots: list[MemoryProviderDiagnostic] = []
        for descriptor in self._registry.descriptors():
            state = self._diagnostics[descriptor.id]
            snapshots.append(MemoryProviderDiagnostic(
                provider_id=descriptor.id,
                state=state.state,  # type: ignore[arg-type]
                capabilities=tuple(sorted(descriptor.capabilities)),
                failure_policy=descriptor.failure_policy,
                call_counts=dict(state.call_counts or {}),
                failure_counts=dict(state.failure_counts or {}),
                last_error_type=state.last_error_type,
            ))
        return tuple(snapshots)

    async def initialize(self, context: MemoryProviderInitContext) -> None:
        if self._initialized or self._providers:
            raise MemoryProviderContractError("Memory Provider Runtime 已初始化")
        initialized: list[MemoryProviderPort] = []
        try:
            for registration in self._registry.ordered_registrations():
                descriptor = registration.descriptor
                self._record_call(descriptor.id, "initialize")
                try:
                    provider = registration.factory()
                except BaseException as exc:
                    self._record_failure(descriptor.id, "initialize", exc)
                    raise
                if not isinstance(provider, MemoryProviderPort):
                    error = MemoryProviderContractError(
                        f"Memory Provider {descriptor.id} factory 返回值不满足 Port"
                    )
                    self._record_failure(descriptor.id, "initialize", error)
                    raise error
                if provider.descriptor != descriptor:
                    error = MemoryProviderContractError(
                        f"Memory Provider {descriptor.id} 实例 descriptor 与注册信息不一致"
                    )
                    self._record_failure(descriptor.id, "initialize", error)
                    raise error
                initialized.append(provider)
                try:
                    await provider.initialize(context)
                except BaseException as exc:
                    self._record_failure(descriptor.id, "initialize", exc)
                    raise
                self._diagnostics[descriptor.id].state = "initialized"
            self._providers = tuple(initialized)
            self._provider_by_id = {
                provider.descriptor.id: provider for provider in self._providers
            }
            self._initialized = True
        except BaseException:
            for provider in reversed(initialized):
                try:
                    self._record_call(provider.descriptor.id, "shutdown")
                    await provider.shutdown()
                except BaseException as exc:
                    self._record_failure(
                        provider.descriptor.id,
                        "shutdown",
                        exc,
                        mark_failed=False,
                    )
                finally:
                    if self._diagnostics[provider.descriptor.id].state != "failed":
                        self._diagnostics[provider.descriptor.id].state = "stopped"
            self._providers = ()
            self._provider_by_id = {}
            self._initialized = False
            raise

    async def shutdown(self) -> None:
        if not self._providers:
            self._initialized = False
            return
        providers = self._providers
        self._providers = ()
        self._provider_by_id = {}
        self._initialized = False
        first_error: BaseException | None = None
        for provider in reversed(providers):
            try:
                self._record_call(provider.descriptor.id, "shutdown")
                await provider.shutdown()
            except BaseException as exc:
                self._record_failure(
                    provider.descriptor.id,
                    "shutdown",
                    exc,
                    mark_failed=False,
                )
                if first_error is None:
                    first_error = exc
            finally:
                self._diagnostics[provider.descriptor.id].state = "stopped"
        if first_error is not None:
            raise first_error

    async def system_prompt_blocks(
        self,
        context: MemoryPromptContext,
    ) -> tuple[MemoryPromptBlock, ...]:
        blocks: list[MemoryPromptBlock] = []
        for provider in self._require_providers():
            block = await self._invoke_optional(
                provider,
                capability="prompt_block",
                operation="system_prompt_block",
                callback=lambda provider=provider: provider.system_prompt_block(context),
            )
            if block is _SKIPPED or block is None:
                continue
            if not isinstance(block, MemoryPromptBlock):
                raise MemoryProviderContractError(
                    f"Memory Provider {provider.descriptor.id} 返回非法 Prompt 块"
                )
            if block.provider_id != provider.descriptor.id:
                raise MemoryProviderContractError(
                    f"Memory Provider {provider.descriptor.id} 返回了其他 Provider 的 Prompt 块"
                )
            blocks.append(block)
        return tuple(blocks)

    async def prefetch(self, context: MemoryPrefetchContext) -> tuple[object, ...]:
        items: list[object] = []
        for provider in self._require_providers():
            prefetched = await self._invoke_optional(
                provider,
                capability="prefetch",
                operation="prefetch",
                callback=lambda provider=provider: provider.prefetch(context),
            )
            if prefetched is not _SKIPPED:
                items.extend(prefetched)
        return tuple(items)

    async def sync_turn(self, context: MemorySyncTurnContext) -> None:
        for provider in self._require_providers():
            await self._invoke_optional(
                provider,
                capability="sync_turn",
                operation="sync_turn",
                callback=lambda provider=provider: provider.sync_turn(context),
            )

    async def tool_schemas(
        self,
        context: MemoryToolSchemaContext,
    ) -> tuple[Mapping[str, Any], ...]:
        schemas: list[Mapping[str, Any]] = []
        for provider in self._require_providers():
            if not provider.descriptor.supports("tools"):
                continue
            provider_schemas = await self._invoke_required(
                provider,
                operation="tool_schemas",
                callback=lambda provider=provider: provider.tool_schemas(context),
            )
            self._validate_tool_schemas(provider, provider_schemas)
            schemas.extend(provider_schemas)
        return tuple(schemas)

    async def handle_tool_call(self, call: MemoryToolCall) -> Mapping[str, Any]:
        self._require_providers()
        owner = self._registry.tool_owner(call.name)
        if owner is None:
            raise MemoryProviderContractError(f"Memory 工具未注册: {call.name}")
        provider = self._provider_by_id[owner]
        result = await self._invoke_required(
            provider,
            operation="handle_tool_call",
            callback=lambda: provider.handle_tool_call(call),
        )
        if not isinstance(result, Mapping):
            raise MemoryProviderContractError(
                f"Memory Provider {provider.descriptor.id} 返回非法工具结果"
            )
        return result

    async def on_session_start(self, context: MemorySessionContext) -> None:
        for provider in self._require_providers():
            await self._invoke_optional(
                provider,
                capability="session_lifecycle",
                operation="on_session_start",
                callback=lambda provider=provider: provider.on_session_start(context),
            )

    async def on_session_end(self, context: MemorySessionContext) -> None:
        for provider in reversed(self._require_providers()):
            await self._invoke_optional(
                provider,
                capability="session_lifecycle",
                operation="on_session_end",
                callback=lambda provider=provider: provider.on_session_end(context),
            )

    async def on_compaction(self, context: MemoryCompactionContext) -> None:
        for provider in self._require_providers():
            await self._invoke_optional(
                provider,
                capability="compaction",
                operation="on_compaction",
                callback=lambda provider=provider: provider.on_compaction(context),
            )

    async def on_delegation_start(self, context: MemoryDelegationContext) -> None:
        for provider in self._require_providers():
            await self._invoke_optional(
                provider,
                capability="delegation",
                operation="on_delegation_start",
                callback=lambda provider=provider: provider.on_delegation_start(context),
            )

    async def on_delegation_end(self, context: MemoryDelegationContext) -> None:
        for provider in reversed(self._require_providers()):
            await self._invoke_optional(
                provider,
                capability="delegation",
                operation="on_delegation_end",
                callback=lambda provider=provider: provider.on_delegation_end(context),
            )

    async def _invoke_optional(
        self,
        provider: MemoryProviderPort,
        *,
        capability: MemoryProviderCapability,
        operation: str,
        callback: Callable[[], Awaitable[Any]],
    ) -> Any:
        descriptor = provider.descriptor
        if not descriptor.supports(capability):
            return _SKIPPED
        try:
            return await self._invoke_required(
                provider,
                operation=operation,
                callback=callback,
            )
        except Exception:
            if descriptor.failure_policy == "skip_optional":
                return _SKIPPED
            raise

    async def _invoke_required(
        self,
        provider: MemoryProviderPort,
        *,
        operation: str,
        callback: Callable[[], Awaitable[Any]],
    ) -> Any:
        provider_id = provider.descriptor.id
        self._record_call(provider_id, operation)
        try:
            return await callback()
        except Exception as exc:
            self._record_failure(
                provider_id,
                operation,
                exc,
                mark_failed=False,
            )
            raise

    def _record_call(self, provider_id: str, operation: str) -> None:
        state = self._diagnostics[provider_id]
        assert state.call_counts is not None
        state.call_counts[operation] = state.call_counts.get(operation, 0) + 1

    def _record_failure(
        self,
        provider_id: str,
        operation: str,
        error: BaseException,
        *,
        mark_failed: bool = True,
    ) -> None:
        state = self._diagnostics[provider_id]
        assert state.failure_counts is not None
        state.failure_counts[operation] = state.failure_counts.get(operation, 0) + 1
        state.last_error_type = type(error).__name__[:128]
        if mark_failed:
            state.state = "failed"

    def _require_providers(self) -> tuple[MemoryProviderPort, ...]:
        if not self._initialized:
            raise MemoryProviderContractError("Memory Provider Runtime 尚未初始化")
        return self._providers

    @staticmethod
    def _validate_tool_schemas(
        provider: MemoryProviderPort,
        schemas: tuple[Mapping[str, Any], ...],
    ) -> None:
        declared = set(provider.descriptor.tool_names)
        actual: set[str] = set()
        for schema in schemas:
            function = schema.get("function")
            if not isinstance(function, Mapping):
                raise MemoryProviderContractError(
                    f"Memory Provider {provider.descriptor.id} 返回非法工具 Schema"
                )
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise MemoryProviderContractError(
                    f"Memory Provider {provider.descriptor.id} 返回无名称工具 Schema"
                )
            actual.add(name)
        if actual != declared:
            raise MemoryProviderContractError(
                f"Memory Provider {provider.descriptor.id} 工具声明与 Schema 不一致"
            )
