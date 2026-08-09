"""模型供应商核心契约；不得依赖具体 HTTP SDK 或配置来源。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


_PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class ProviderCapability(StrEnum):
    """Provider 实现层能力；模型自身能力仍由模型目录约束。"""

    CHAT_COMPLETION = "chat_completion"
    STREAMING = "streaming"
    TOOL_CALLING = "tool_calling"
    VISION = "vision"
    REASONING_CONTENT = "reasoning_content"
    CACHE_USAGE = "cache_usage"


class ProviderRequestProtocol(StrEnum):
    """Provider Adapter 实际实现的请求协议。"""

    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    OPENAI_RESPONSES = "openai_responses"


@dataclass(frozen=True)
class ProviderDescriptor:
    """供应商实现的静态身份与能力声明。"""

    id: str
    display_name: str
    capabilities: frozenset[ProviderCapability]
    aliases: tuple[str, ...] = ()
    implementation: str = "openai_compatible"
    built_in: bool = False
    override_protected: bool = True
    request_protocol: ProviderRequestProtocol = (
        ProviderRequestProtocol.OPENAI_CHAT_COMPLETIONS
    )
    request_path: str = "/chat/completions"
    capability_evidence: Mapping[ProviderCapability, str] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        if not _PROVIDER_ID_PATTERN.fullmatch(self.id):
            raise ValueError(f"非法 provider id: {self.id!r}")
        if not self.display_name.strip():
            raise ValueError(f"provider {self.id} 必须声明 display_name")
        if not self.capabilities:
            raise ValueError(f"provider {self.id} 必须声明至少一项 capability")
        capabilities = frozenset(ProviderCapability(item) for item in self.capabilities)
        protocol = ProviderRequestProtocol(self.request_protocol)
        request_path = str(self.request_path or "").strip()
        if (
            not request_path.startswith("/")
            or "?" in request_path
            or "#" in request_path
        ):
            raise ValueError(f"provider {self.id} request_path 无效")
        raw_evidence = dict(self.capability_evidence)
        evidence: dict[ProviderCapability, str] = {}
        for capability in capabilities:
            source = str(
                raw_evidence.get(capability)
                or raw_evidence.get(capability.value)
                or "adapter_contract"
            ).strip()
            if not source or len(source) > 80:
                raise ValueError(
                    f"provider {self.id} capability evidence 无效: {capability.value}"
                )
            evidence[capability] = source
        normalized_aliases: list[str] = []
        for alias in self.aliases:
            if not _PROVIDER_ID_PATTERN.fullmatch(alias):
                raise ValueError(f"provider {self.id} 包含非法 alias: {alias!r}")
            if alias == self.id or alias in normalized_aliases:
                raise ValueError(f"provider {self.id} 包含重复 alias: {alias!r}")
            normalized_aliases.append(alias)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "request_protocol", protocol)
        object.__setattr__(self, "request_path", request_path)
        object.__setattr__(
            self,
            "capability_evidence",
            MappingProxyType(evidence),
        )

    def supports(self, required: frozenset[ProviderCapability]) -> bool:
        return required.issubset(self.capabilities)

    def metadata(self) -> dict[str, object]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "capabilities": sorted(capability.value for capability in self.capabilities),
            "aliases": list(self.aliases),
            "implementation": self.implementation,
            "built_in": self.built_in,
            "override_protected": self.override_protected,
            "request_protocol": self.request_protocol.value,
            "request_path": self.request_path,
            "capability_evidence": {
                capability.value: self.capability_evidence[capability]
                for capability in sorted(
                    self.capability_evidence,
                    key=lambda item: item.value,
                )
            },
        }

    @property
    def registry_namespace(self) -> str:
        return "model_provider"

    @property
    def registry_id(self) -> str:
        return self.id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return self.metadata()


@dataclass(frozen=True)
class ProviderAvailability:
    """不执行网络探测的本地可用性快照。"""

    available: bool
    configured: bool
    reason_code: str
    retryable: bool = False

    def metadata(self) -> dict[str, object]:
        return {
            "available": self.available,
            "configured": self.configured,
            "reason_code": self.reason_code,
            "retryable": self.retryable,
        }


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class ModelProviderRequest:
    """与 OpenAI HTTP payload 解耦的单次文本生成请求。"""

    messages: tuple[Mapping[str, Any], ...]
    model: str = ""
    max_tokens: int = 1024
    temperature: float = 0.0
    timeout_seconds: float = 30.0
    enable_thinking: str = "auto"
    reasoning_effort: str = ""
    service_tier: str = ""
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    trace_source: str = "model_provider"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("model provider request messages 不能为空")
        if self.max_tokens <= 0:
            raise ValueError("model provider request max_tokens 必须大于 0")
        if self.timeout_seconds <= 0:
            raise ValueError("model provider request timeout_seconds 必须大于 0")
        object.__setattr__(
            self,
            "messages",
            tuple(_freeze_mapping(message) for message in self.messages),
        )
        object.__setattr__(self, "extra_headers", _freeze_mapping(self.extra_headers))
        object.__setattr__(self, "extra_body", _freeze_mapping(self.extra_body))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True)
class ModelProviderResponse:
    """Provider Adapter 归一后的生成结果。"""

    content: str
    reasoning_content: str = ""
    finish_reason: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    raw_response: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "usage", _freeze_mapping(self.usage))
        object.__setattr__(self, "raw_response", _freeze_mapping(self.raw_response))


@runtime_checkable
class ModelProviderPort(Protocol):
    """所有模型供应商共同的身份、可用性与诊断 Port。"""

    @property
    def descriptor(self) -> ProviderDescriptor:
        ...

    def availability(self) -> ProviderAvailability:
        ...

    def introspect(self) -> Mapping[str, object]:
        """返回不包含 endpoint、凭据或请求正文的安全状态快照。"""

        ...


@runtime_checkable
class SyncModelCompletionPort(ModelProviderPort, Protocol):
    """供同步分类、裁判等调用链使用的生成能力。"""

    def complete(self, request: ModelProviderRequest) -> ModelProviderResponse:
        ...


@runtime_checkable
class AsyncModelCompletionPort(ModelProviderPort, Protocol):
    """供主聊天与流式网关使用的异步生成能力。"""

    async def complete_async(
        self,
        request: ModelProviderRequest,
    ) -> ModelProviderResponse:
        ...
