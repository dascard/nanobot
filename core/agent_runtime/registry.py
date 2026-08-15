"""进程级 Agent 身份注册表。

注册表只负责把稳定 ``agent_id`` 映射到框架无关 Gateway Port。具体 Agent
Loop、模型路由、工具和状态仍由各自 Adapter 组合，不进入核心层。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType

from core.agent_runtime.errors import (
    AgentRuntimeCapabilityError,
    AgentRuntimeNotFoundError,
)
from core.agent_runtime.gateway_contracts import (
    AgentMessageGatewayPort,
    ManagedAgentGatewayPort,
    ResearchAgentRuntimePort,
)
from core.registry import RegistryBuilder, RegistrySnapshot
from core.registry.validation import validate_identifier


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

AgentGatewayProvider = Callable[[], AgentMessageGatewayPort | None]
IsolatedAgentGatewayFactory = Callable[[], ManagedAgentGatewayPort | None]
ResearchAgentRuntimeFactory = Callable[[], ResearchAgentRuntimePort | None]


def _sha256(value: str, field_name: str, *, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if allow_empty and not normalized:
        return ""
    if _SHA256_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} 必须是 SHA-256 十六进制摘要")
    return normalized


@dataclass(frozen=True, slots=True)
class AgentRuntimeDescriptor:
    """不含凭据和可变状态的 Agent 注册描述。"""

    agent_id: str
    display_name: str
    description: str
    adapter: str
    source_ref: str
    source_sha256: str
    runtime_policy_sha256: str
    allowed_entrypoints: tuple[str, ...]
    default: bool = False
    manifest_snapshot_sha256: str = ""
    profile_sha256: str = ""
    tool_policy_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "agent_id",
            validate_identifier(self.agent_id, field_name="agent.agent_id"),
        )
        display_name = str(self.display_name or "").strip()
        if not display_name or len(display_name) > 128:
            raise ValueError("agent.display_name 必须是 1..128 字符")
        object.__setattr__(self, "display_name", display_name)
        description = str(self.description or "").strip()
        if len(description) > 1000:
            raise ValueError("agent.description 不能超过 1000 字符")
        object.__setattr__(self, "description", description)
        object.__setattr__(
            self,
            "adapter",
            validate_identifier(self.adapter, field_name="agent.adapter"),
        )
        source_ref = str(self.source_ref or "").strip()
        if not source_ref or "\x00" in source_ref or len(source_ref) > 512:
            raise ValueError("agent.source_ref 非法")
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(
            self,
            "source_sha256",
            _sha256(self.source_sha256, "agent.source_sha256"),
        )
        object.__setattr__(
            self,
            "runtime_policy_sha256",
            _sha256(
                self.runtime_policy_sha256,
                "agent.runtime_policy_sha256",
            ),
        )
        entrypoints = tuple(
            sorted(
                {
                    validate_identifier(
                        value,
                        field_name="agent.allowed_entrypoints",
                    )
                    for value in self.allowed_entrypoints
                }
            )
        )
        if not entrypoints:
            raise ValueError("agent.allowed_entrypoints 不能为空")
        object.__setattr__(self, "allowed_entrypoints", entrypoints)
        for field_name in (
            "manifest_snapshot_sha256",
            "profile_sha256",
            "tool_policy_sha256",
        ):
            object.__setattr__(
                self,
                field_name,
                _sha256(
                    getattr(self, field_name),
                    f"agent.{field_name}",
                    allow_empty=True,
                ),
            )

    @property
    def registry_namespace(self) -> str:
        return "agent_runtime"

    @property
    def registry_id(self) -> str:
        return self.agent_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "adapter": self.adapter,
            "allowed_entrypoints": list(self.allowed_entrypoints),
            "default": self.default,
            "description": self.description,
            "display_name": self.display_name,
            "manifest_snapshot_sha256": self.manifest_snapshot_sha256,
            "profile_sha256": self.profile_sha256,
            "runtime_policy_sha256": self.runtime_policy_sha256,
            "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "tool_policy_sha256": self.tool_policy_sha256,
        }

    def to_public_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "description": self.description,
            "adapter": self.adapter,
            "allowed_entrypoints": list(self.allowed_entrypoints),
            "default": self.default,
            "source_sha256": self.source_sha256,
            "runtime_policy_sha256": self.runtime_policy_sha256,
            "manifest_snapshot_sha256": self.manifest_snapshot_sha256,
            "profile_sha256": self.profile_sha256,
            "tool_policy_sha256": self.tool_policy_sha256,
        }


@dataclass(frozen=True, slots=True)
class AgentRuntimeRegistration:
    """描述符与进程内 Adapter 工厂的绑定。"""

    descriptor: AgentRuntimeDescriptor
    gateway_provider: AgentGatewayProvider
    isolated_gateway_factory: IsolatedAgentGatewayFactory
    research_runtime_factory: ResearchAgentRuntimeFactory | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, AgentRuntimeDescriptor):
            raise TypeError("descriptor 必须是 AgentRuntimeDescriptor")
        if not callable(self.gateway_provider):
            raise TypeError("gateway_provider 必须可调用")
        if not callable(self.isolated_gateway_factory):
            raise TypeError("isolated_gateway_factory 必须可调用")
        if self.research_runtime_factory is not None and not callable(
            self.research_runtime_factory
        ):
            raise TypeError("research_runtime_factory 必须可调用")


class AgentRuntimeRegistry:
    """一次完整构建后冻结的 Agent Runtime 注册表。"""

    def __init__(
        self,
        *,
        snapshot: RegistrySnapshot[AgentRuntimeDescriptor],
        registrations: Mapping[str, AgentRuntimeRegistration],
        default_agent_id: str,
    ) -> None:
        self._snapshot = snapshot
        self._registrations = MappingProxyType(dict(registrations))
        self._default_agent_id = default_agent_id

    @classmethod
    def build(
        cls,
        registrations: Iterable[AgentRuntimeRegistration],
    ) -> "AgentRuntimeRegistry":
        materialized = tuple(registrations)
        if not materialized:
            raise ValueError("Agent Runtime 注册表不能为空")
        builder = RegistryBuilder[AgentRuntimeDescriptor]("agent_runtime")
        by_id: dict[str, AgentRuntimeRegistration] = {}
        defaults: list[str] = []
        for registration in materialized:
            if not isinstance(registration, AgentRuntimeRegistration):
                raise TypeError("注册项必须是 AgentRuntimeRegistration")
            descriptor = registration.descriptor
            builder.register(descriptor)
            by_id[descriptor.agent_id] = registration
            if descriptor.default:
                defaults.append(descriptor.agent_id)
        if len(defaults) != 1:
            raise ValueError("Agent Runtime 注册表必须且只能声明一个默认 Agent")
        return cls(
            snapshot=builder.freeze(),
            registrations=by_id,
            default_agent_id=defaults[0],
        )

    @property
    def snapshot(self) -> RegistrySnapshot[AgentRuntimeDescriptor]:
        return self._snapshot

    @property
    def default_agent_id(self) -> str:
        return self._default_agent_id

    def descriptors(self) -> tuple[AgentRuntimeDescriptor, ...]:
        return tuple(self._snapshot)

    def require_registration(
        self,
        agent_id: str = "",
        *,
        entrypoint: str,
    ) -> AgentRuntimeRegistration:
        resolved_id = str(agent_id or "").strip() or self._default_agent_id
        registration = self._registrations.get(resolved_id)
        if registration is None:
            raise AgentRuntimeNotFoundError(
                f"Agent 未注册：{resolved_id}",
                runtime_id=resolved_id,
            )
        normalized_entrypoint = validate_identifier(
            entrypoint,
            field_name="agent.entrypoint",
        )
        if normalized_entrypoint not in registration.descriptor.allowed_entrypoints:
            raise AgentRuntimeCapabilityError(
                f"Agent {resolved_id} 未开放入口 {normalized_entrypoint}",
                runtime_id=resolved_id,
            )
        return registration


__all__ = [
    "AgentGatewayProvider",
    "AgentRuntimeDescriptor",
    "AgentRuntimeRegistration",
    "AgentRuntimeRegistry",
    "IsolatedAgentGatewayFactory",
    "ResearchAgentRuntimeFactory",
]
