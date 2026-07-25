"""类型化 RuntimeEvent、字段策略和显式 Sink 接口。"""

from __future__ import annotations

import math
import os
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable

from core.registry import RegistryBuilder, RegistrySnapshot
from core.runtime.extensions import (
    RuntimeExtensionKind,
    RuntimeFailurePolicy,
    RuntimeHookDescriptor,
    RuntimeObserverBinding,
    RuntimeObserverDispatcher,
)
from core.telemetry.contracts import (
    TelemetryCorrelation,
    TelemetryProvenance,
)


RuntimeEventPhase = Literal["started", "succeeded", "failed", "state_changed"]
RuntimeEventFieldKind = Literal[
    "boolean",
    "count",
    "digest",
    "duration_ms",
    "identifier",
    "label",
]


class RuntimeEventRegistryError(ValueError):
    """RuntimeEvent 描述符注册失败。"""


@dataclass(frozen=True, slots=True)
class RuntimeEventField:
    name: str
    kind: RuntimeEventFieldKind
    required: bool = False
    max_chars: int = 128

    def __post_init__(self) -> None:
        normalized = str(self.name or "").strip()
        if not normalized or not normalized.replace("_", "").isalnum():
            raise RuntimeEventRegistryError(f"事件字段名不合法：{self.name!r}")
        if self.max_chars <= 0:
            raise RuntimeEventRegistryError(
                f"事件字段 {normalized} 的 max_chars 必须为正数"
            )
        object.__setattr__(self, "name", normalized)


@dataclass(frozen=True, slots=True)
class RuntimeEventDescriptor:
    name: str
    domain: str
    phases: tuple[RuntimeEventPhase, ...]
    fields: tuple[RuntimeEventField, ...] = ()
    owner_module: str = "runtime.agent"
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        domain = str(self.domain or "").strip()
        if not name or "." not in name:
            raise RuntimeEventRegistryError(f"事件名必须包含命名空间：{name!r}")
        if not domain:
            raise RuntimeEventRegistryError(f"事件 {name} 的 domain 不能为空")
        owner_module = str(self.owner_module or "").strip()
        version = str(self.version or "").strip()
        if not owner_module:
            raise RuntimeEventRegistryError(
                f"事件 {name} 的 owner_module 不能为空"
            )
        if not version:
            raise RuntimeEventRegistryError(
                f"事件 {name} 的 version 不能为空"
            )
        phases = tuple(dict.fromkeys(self.phases))
        if not phases:
            raise RuntimeEventRegistryError(f"事件 {name} 至少声明一个 phase")
        fields = tuple(self.fields)
        field_names = [item.name for item in fields]
        if len(field_names) != len(set(field_names)):
            raise RuntimeEventRegistryError(f"事件 {name} 包含重复字段")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "phases", phases)
        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "owner_module", owner_module)
        object.__setattr__(self, "version", version)

    @property
    def fields_by_name(self) -> Mapping[str, RuntimeEventField]:
        return MappingProxyType({item.name: item for item in self.fields})

    @property
    def registry_namespace(self) -> str:
        return "runtime_event"

    @property
    def registry_id(self) -> str:
        return self.name

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "domain": self.domain,
            "owner_module": self.owner_module,
            "version": self.version,
            "phases": list(self.phases),
            "fields": [
                {
                    "name": item.name,
                    "kind": item.kind,
                    "required": item.required,
                    "max_chars": item.max_chars,
                }
                for item in self.fields
            ],
        }


class RuntimeEventRegistry:
    """显式注册、可冻结的事件目录。"""

    def __init__(self, descriptors: Sequence[RuntimeEventDescriptor] = ()) -> None:
        self._descriptors: dict[str, RuntimeEventDescriptor] = {}
        self._frozen = False
        self._registry_snapshot: (
            RegistrySnapshot[RuntimeEventDescriptor] | None
        ) = None
        for descriptor in descriptors:
            self.register(descriptor)

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[RuntimeEventDescriptor]:
        if self._registry_snapshot is None:
            raise RuntimeEventRegistryError(
                "RuntimeEvent Registry 尚未冻结"
            )
        return self._registry_snapshot

    def register(self, descriptor: RuntimeEventDescriptor) -> None:
        if self._frozen:
            raise RuntimeEventRegistryError("RuntimeEvent Registry 已冻结")
        if not isinstance(descriptor, RuntimeEventDescriptor):
            raise TypeError("descriptor 必须是 RuntimeEventDescriptor")
        if descriptor.name in self._descriptors:
            raise RuntimeEventRegistryError(f"事件重复注册：{descriptor.name}")
        self._descriptors[descriptor.name] = descriptor

    def freeze(self) -> "RuntimeEventRegistry":
        if self._frozen:
            return self
        builder = RegistryBuilder[RuntimeEventDescriptor](
            "runtime_event"
        )
        for name in sorted(self._descriptors):
            builder.register(self._descriptors[name])
        self._registry_snapshot = builder.freeze()
        self._frozen = True
        return self

    def get(self, name: str) -> RuntimeEventDescriptor:
        normalized = str(name or "").strip()
        try:
            return self._descriptors[normalized]
        except KeyError as exc:
            raise RuntimeEventRegistryError(f"事件未注册：{normalized or '<empty>'}") from exc

    def list(self) -> tuple[RuntimeEventDescriptor, ...]:
        if self._registry_snapshot is not None:
            return tuple(self._registry_snapshot)
        return tuple(self._descriptors[name] for name in sorted(self._descriptors))


RuntimeEventContext = TelemetryCorrelation


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_id: str
    name: str
    domain: str
    phase: RuntimeEventPhase
    occurred_at: datetime
    context: RuntimeEventContext
    provenance: TelemetryProvenance
    attributes: Mapping[str, object] = field(default_factory=dict)
    dropped_attribute_count: int = 0

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("RuntimeEvent.occurred_at 必须带时区")
        object.__setattr__(
            self,
            "attributes",
            MappingProxyType(dict(self.attributes)),
        )


@runtime_checkable
class RuntimeEventSink(Protocol):
    def emit(self, event: RuntimeEvent) -> None: ...


class InMemoryRuntimeEventSink:
    """测试与本地诊断使用的线程安全 Sink。"""

    def __init__(self) -> None:
        self._events: list[RuntimeEvent] = []
        self._lock = threading.Lock()

    def emit(self, event: RuntimeEvent) -> None:
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> tuple[RuntimeEvent, ...]:
        with self._lock:
            return tuple(self._events)


class _RuntimeEventSinkObserver:
    """旧 Sink Port 到只读 Observer Hook 的兼容 Adapter。"""

    def __init__(self, sink: RuntimeEventSink) -> None:
        self._sink = sink

    def observe(self, event: object) -> object | None:
        if not isinstance(event, RuntimeEvent):
            raise TypeError("RuntimeEvent Observer 只接受 RuntimeEvent")
        return self._sink.emit(event)


def _observer_dispatcher_from_sinks(
    sinks: Sequence[RuntimeEventSink],
    *,
    fail_open: bool,
) -> RuntimeObserverDispatcher:
    failure_policy = (
        RuntimeFailurePolicy.FAIL_OPEN
        if fail_open
        else RuntimeFailurePolicy.FAIL_CLOSED
    )
    return RuntimeObserverDispatcher(tuple(
        RuntimeObserverBinding(
            descriptor=RuntimeHookDescriptor(
                hook_id=f"runtime.event_sink_{index:04d}",
                kind=RuntimeExtensionKind.OBSERVER,
                owner_module="runtime.events",
                domain="runtime",
                input_contract="runtime.event.v1",
                output_contract="none",
                priority=index,
                failure_policy=failure_policy,
                trusted_builtin=True,
            ),
            observer=_RuntimeEventSinkObserver(sink),
        )
        for index, sink in enumerate(sinks)
    ))


class RuntimeEventEmitter:
    """根据 Descriptor 白名单净化字段，再投递到显式 Sink。"""

    _SENSITIVE_KEY_PARTS = (
        "api_key",
        "authorization",
        "command",
        "content",
        "cookie",
        "password",
        "prompt",
        "secret",
        "stderr",
        "stdout",
        "token",
    )

    def __init__(
        self,
        registry: RuntimeEventRegistry,
        sinks: Sequence[RuntimeEventSink] = (),
        *,
        now: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        fail_open: bool = True,
        observer_dispatcher: RuntimeObserverDispatcher | None = None,
        artifact_revision: str | None = None,
    ) -> None:
        if not registry.frozen:
            raise RuntimeEventRegistryError("RuntimeEventEmitter 只接受已冻结 Registry")
        if sinks and observer_dispatcher is not None:
            raise ValueError("sinks 与 observer_dispatcher 不能同时提供")
        self._registry = registry
        self._observer_dispatcher = (
            observer_dispatcher
            if observer_dispatcher is not None
            else _observer_dispatcher_from_sinks(
                sinks,
                fail_open=bool(fail_open),
            )
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._event_id_factory = event_id_factory or (lambda: uuid.uuid4().hex)
        self._artifact_revision = str(
            artifact_revision
            if artifact_revision is not None
            else (
                os.environ.get("NANOBOT_RUNTIME_REVISION")
                or os.environ.get("GIT_COMMIT")
                or ""
            )
        ).strip()

    def emit(
        self,
        name: str,
        phase: RuntimeEventPhase,
        *,
        context: RuntimeEventContext | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> RuntimeEvent:
        descriptor = self._registry.get(name)
        if phase not in descriptor.phases:
            raise ValueError(f"事件 {name} 不允许 phase={phase}")
        sanitized, dropped = self._sanitize_attributes(
            descriptor,
            attributes or {},
        )
        occurred_at = self._now()
        if occurred_at.tzinfo is None:
            raise ValueError("RuntimeEvent 时钟必须返回带时区的 datetime")
        event = RuntimeEvent(
            event_id=str(self._event_id_factory()),
            name=descriptor.name,
            domain=descriptor.domain,
            phase=phase,
            occurred_at=occurred_at,
            context=context or RuntimeEventContext(),
            provenance=TelemetryProvenance(
                registry_generation=(
                    self._registry.registry_snapshot.generation
                ),
                registry_sha256=(
                    self._registry.registry_snapshot.sha256
                ),
                module_id=descriptor.owner_module,
                module_version=descriptor.version,
                artifact_revision=self._artifact_revision,
            ),
            attributes=sanitized,
            dropped_attribute_count=dropped,
        )
        self._observer_dispatcher.dispatch(event)
        return event

    def _sanitize_attributes(
        self,
        descriptor: RuntimeEventDescriptor,
        attributes: Mapping[str, object],
    ) -> tuple[dict[str, object], int]:
        fields = descriptor.fields_by_name
        sanitized: dict[str, object] = {}
        dropped = 0
        for raw_key, value in attributes.items():
            key = str(raw_key or "")
            lowered = key.lower()
            event_field = fields.get(key)
            if event_field is None or (
                event_field.kind not in {
                    "boolean",
                    "count",
                    "duration_ms",
                }
                and any(
                    part in lowered for part in self._SENSITIVE_KEY_PARTS
                )
            ):
                dropped += 1
                continue
            normalized = self._sanitize_value(event_field, value)
            if normalized is None:
                dropped += 1
                continue
            sanitized[key] = normalized
        missing = [
            event_field.name
            for event_field in descriptor.fields
            if event_field.required and event_field.name not in sanitized
        ]
        if missing:
            raise ValueError(
                f"事件 {descriptor.name} 缺少合法必填字段：{','.join(missing)}"
            )
        return sanitized, dropped

    @staticmethod
    def _sanitize_value(
        event_field: RuntimeEventField,
        value: object,
    ) -> object | None:
        if event_field.kind == "boolean":
            return value if type(value) is bool else None
        if event_field.kind in {"count", "duration_ms"}:
            if type(value) not in {int, float}:
                return None
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < 0:
                return None
            return int(numeric)
        text = str(value or "").strip()
        if not text or len(text) > event_field.max_chars:
            return None
        if any(ord(char) < 32 for char in text):
            return None
        if event_field.kind == "digest":
            lowered = text.lower()
            if len(lowered) != 64 or any(
                char not in "0123456789abcdef" for char in lowered
            ):
                return None
            return lowered
        return text
