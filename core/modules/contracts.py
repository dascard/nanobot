"""模块化单体的框架无关 Manifest 与生命周期合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Protocol, runtime_checkable

from core.registry.validation import validate_identifier


_VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$"
)
_CONTRIBUTION_KINDS = frozenset({
    "content_rule",
    "endpoint",
    "event",
    "job",
    "metric",
    "model_route",
    "observer_hook",
    "policy",
    "port",
    "prompt",
    "setting",
    "task",
    "telemetry",
    "tool",
    "transform_hook",
    "web_feature",
})
_HEALTH_STATUSES = frozenset({
    "starting",
    "healthy",
    "degraded",
    "unhealthy",
    "stopped",
})


class CompositionState(str, Enum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ModuleContributionRef:
    kind: str
    contribution_id: str

    def __post_init__(self) -> None:
        if self.kind not in _CONTRIBUTION_KINDS:
            raise ValueError(
                f"未知 Module contribution kind: {self.kind}"
            )
        validate_identifier(
            self.contribution_id,
            field_name="contribution_id",
        )


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    """一个内建模块的静态所有权、依赖和贡献声明。"""

    module_id: str
    version: str
    owner: str
    domain: str
    lifecycle: str = "active"
    required_modules: tuple[str, ...] = ()
    optional_modules: tuple[str, ...] = ()
    provided_capabilities: tuple[str, ...] = ()
    contributions: tuple[ModuleContributionRef, ...] = ()
    startup_phase: int = 100
    shutdown_phase: int = 100
    health_checks: tuple[str, ...] = ()
    readiness_checks: tuple[str, ...] = ()
    feature_flag: str = ""
    compatibility_aliases: tuple[str, ...] = ()
    release_impacts: tuple[str, ...] = ()
    registry_namespace: str = field(
        default="application_module",
        init=False,
    )

    def __post_init__(self) -> None:
        validate_identifier(
            self.module_id,
            field_name="module_id",
        )
        if _VERSION_PATTERN.fullmatch(self.version) is None:
            raise ValueError("module.version 必须是语义版本")
        validate_identifier(self.owner, field_name="module.owner")
        validate_identifier(self.domain, field_name="module.domain")
        if self.lifecycle not in {"active", "deprecated"}:
            raise ValueError(
                "module.lifecycle 必须是 active 或 deprecated"
            )
        for field_name, values in (
            ("required_modules", self.required_modules),
            ("optional_modules", self.optional_modules),
            (
                "provided_capabilities",
                self.provided_capabilities,
            ),
            ("health_checks", self.health_checks),
            ("readiness_checks", self.readiness_checks),
            (
                "compatibility_aliases",
                self.compatibility_aliases,
            ),
            ("release_impacts", self.release_impacts),
        ):
            if not isinstance(values, tuple):
                raise ValueError(f"{field_name} 必须是 tuple")
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} 不能包含重复项")
            for value in values:
                validate_identifier(value, field_name=field_name)
        if set(self.required_modules) & set(self.optional_modules):
            raise ValueError(
                "required_modules 与 optional_modules 不能重叠"
            )
        if len(self.contributions) != len(set(self.contributions)):
            raise ValueError("contributions 不能包含重复项")
        if (
            isinstance(self.startup_phase, bool)
            or not isinstance(self.startup_phase, int)
            or self.startup_phase < 0
            or isinstance(self.shutdown_phase, bool)
            or not isinstance(self.shutdown_phase, int)
            or self.shutdown_phase < 0
        ):
            raise ValueError("startup/shutdown phase 必须是非负整数")
        if self.feature_flag:
            validate_identifier(
                self.feature_flag,
                field_name="feature_flag",
            )

    @property
    def registry_id(self) -> str:
        return self.module_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return self.required_modules

    def registry_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "owner": self.owner,
            "domain": self.domain,
            "lifecycle": self.lifecycle,
            "optional_modules": self.optional_modules,
            "provided_capabilities": self.provided_capabilities,
            "contributions": [
                {
                    "kind": contribution.kind,
                    "id": contribution.contribution_id,
                }
                for contribution in self.contributions
            ],
            "startup_phase": self.startup_phase,
            "shutdown_phase": self.shutdown_phase,
            "health_checks": self.health_checks,
            "readiness_checks": self.readiness_checks,
            "feature_flag": self.feature_flag,
            "compatibility_aliases": (
                self.compatibility_aliases
            ),
            "release_impacts": self.release_impacts,
        }


@dataclass(frozen=True, slots=True)
class ModuleHealthCheck:
    name: str
    healthy: bool
    detail_code: str = ""

    def __post_init__(self) -> None:
        validate_identifier(self.name, field_name="health.name")
        if type(self.healthy) is not bool:
            raise ValueError("health.healthy 必须是 bool")
        if self.detail_code:
            validate_identifier(
                self.detail_code,
                field_name="health.detail_code",
            )


@dataclass(frozen=True, slots=True)
class ModuleHealth:
    status: str
    ready: bool
    checks: tuple[ModuleHealthCheck, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in _HEALTH_STATUSES:
            raise ValueError("module health status 无效")
        if type(self.ready) is not bool:
            raise ValueError("module health ready 必须是 bool")
        if not isinstance(self.checks, tuple):
            raise ValueError("module health checks 必须是 tuple")
        names = [check.name for check in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("module health checks 不能重名")
        if self.ready and self.status in {"unhealthy", "stopped"}:
            raise ValueError(
                "unhealthy/stopped module 不能标记 ready"
            )


@dataclass(frozen=True, slots=True)
class ModuleRuntimeContext:
    """启动期上下文只携带宿主对象与冻结组合身份。"""

    application: object | None = None
    testing: bool = False
    logger: object | None = None
    composition_generation: int = 0
    composition_sha256: str = ""

    def __post_init__(self) -> None:
        if type(self.testing) is not bool:
            raise ValueError("testing 必须是 bool")
        if (
            isinstance(self.composition_generation, bool)
            or not isinstance(self.composition_generation, int)
            or self.composition_generation < 0
        ):
            raise ValueError(
                "composition_generation 必须是非负整数"
            )


@runtime_checkable
class ModuleRegistrationPort(Protocol):
    def register(
        self,
        kind: str,
        contribution_id: str,
    ) -> None: ...


@runtime_checkable
class ApplicationModule(Protocol):
    def manifest(self) -> ModuleManifest: ...

    def register(self, builder: ModuleRegistrationPort) -> None: ...

    async def start(
        self,
        runtime_context: ModuleRuntimeContext,
    ) -> None: ...

    async def stop(self) -> None: ...

    def health(self) -> ModuleHealth: ...
