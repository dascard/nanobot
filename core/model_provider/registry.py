"""显式覆盖策略、可冻结的模型 Provider 注册表。"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from core.model_provider.contracts import (
    ModelProviderPort,
    ProviderCapability,
    ProviderDescriptor,
)
from core.registry import RegistryBuilder, RegistrySnapshot


class OverridePolicy(StrEnum):
    REJECT = "reject"
    REPLACE = "replace"


class ProviderRegistryError(RuntimeError):
    pass


class DuplicateProviderError(ProviderRegistryError):
    pass


class ProviderRegistryFrozenError(ProviderRegistryError):
    pass


class ProviderNotFoundError(ProviderRegistryError):
    pass


class ProviderCapabilityError(ProviderRegistryError):
    pass


class ProviderUnavailableError(ProviderRegistryError):
    pass


class ModelProviderRegistry:
    """启动期可写、服务期冻结的 Provider Registry。

    所有替换都需要同时声明 ``REPLACE`` 和 operator 授权，避免第三方插件
    仅凭同名注册覆盖内建供应商。
    """

    def __init__(self) -> None:
        self._providers: dict[str, ModelProviderPort] = {}
        self._aliases: dict[str, str] = {}
        self._frozen = False
        self._registry_snapshot: (
            RegistrySnapshot[ProviderDescriptor] | None
        ) = None

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def registry_snapshot(self) -> RegistrySnapshot[ProviderDescriptor]:
        if self._registry_snapshot is None:
            raise ProviderRegistryError("Provider Registry 尚未冻结")
        return self._registry_snapshot

    def register(
        self,
        provider: ModelProviderPort,
        *,
        override_policy: OverridePolicy = OverridePolicy.REJECT,
        operator_authorized: bool = False,
    ) -> None:
        if self._frozen:
            raise ProviderRegistryFrozenError("Provider Registry 已冻结")
        descriptor = provider.descriptor
        collisions = {
            name
            for name in (descriptor.id, *descriptor.aliases)
            if name in self._providers or name in self._aliases
        }
        if collisions:
            if override_policy is not OverridePolicy.REPLACE:
                raise DuplicateProviderError(
                    f"Provider 名称冲突: {sorted(collisions)}"
                )
            if not operator_authorized:
                raise DuplicateProviderError("替换 Provider 必须由 operator 显式授权")
            self._remove_collisions(collisions)
        self._providers[descriptor.id] = provider
        for alias in descriptor.aliases:
            self._aliases[alias] = descriptor.id

    def _remove_collisions(self, collisions: set[str]) -> None:
        provider_ids = {
            self._aliases.get(name, name)
            for name in collisions
        }
        for provider_id in provider_ids:
            old = self._providers.pop(provider_id, None)
            if old is None:
                continue
            for alias in old.descriptor.aliases:
                self._aliases.pop(alias, None)

    def freeze(self) -> "ModelProviderRegistry":
        if self._frozen:
            return self
        builder = RegistryBuilder[ProviderDescriptor]("model_provider")
        for provider_id in sorted(self._providers):
            builder.register(self._providers[provider_id].descriptor)
        self._registry_snapshot = builder.freeze()
        self._frozen = True
        return self

    def get(self, provider_id: str) -> ModelProviderPort | None:
        canonical = self._aliases.get(provider_id, provider_id)
        return self._providers.get(canonical)

    def require(
        self,
        provider_id: str,
        *,
        capabilities: frozenset[ProviderCapability] = frozenset(),
        require_available: bool = True,
    ) -> ModelProviderPort:
        provider = self.get(provider_id)
        if provider is None:
            raise ProviderNotFoundError(f"未注册 Provider: {provider_id}")
        if not provider.descriptor.supports(capabilities):
            missing = sorted(
                capability.value
                for capability in capabilities - provider.descriptor.capabilities
            )
            raise ProviderCapabilityError(
                f"Provider {provider.descriptor.id} 缺少能力: {missing}"
            )
        availability = provider.availability()
        if require_available and not availability.available:
            raise ProviderUnavailableError(
                f"Provider {provider.descriptor.id} 不可用: {availability.reason_code}"
            )
        return provider

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        if self._registry_snapshot is not None:
            return tuple(self._registry_snapshot)
        return tuple(
            provider.descriptor
            for _, provider in sorted(self._providers.items())
        )

    def providers(self) -> Mapping[str, ModelProviderPort]:
        """返回只读快照，调用方不能绕过冻结状态修改 Registry。"""

        return MappingProxyType(dict(self._providers))

    def introspect(self) -> tuple[Mapping[str, object], ...]:
        return tuple(
            provider.introspect()
            for _, provider in sorted(self._providers.items())
        )
