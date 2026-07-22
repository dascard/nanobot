"""类型化 RuntimeEvent、字段策略和显式 Sink 接口。"""

from __future__ import annotations

import math
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable


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

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        domain = str(self.domain or "").strip()
        if not name or "." not in name:
            raise RuntimeEventRegistryError(f"事件名必须包含命名空间：{name!r}")
        if not domain:
            raise RuntimeEventRegistryError(f"事件 {name} 的 domain 不能为空")
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

    @property
    def fields_by_name(self) -> Mapping[str, RuntimeEventField]:
        return MappingProxyType({item.name: item for item in self.fields})


class RuntimeEventRegistry:
    """显式注册、可冻结的事件目录。"""

    def __init__(self, descriptors: Sequence[RuntimeEventDescriptor] = ()) -> None:
        self._descriptors: dict[str, RuntimeEventDescriptor] = {}
        self._frozen = False
        for descriptor in descriptors:
            self.register(descriptor)

    @property
    def frozen(self) -> bool:
        return self._frozen

    def register(self, descriptor: RuntimeEventDescriptor) -> None:
        if self._frozen:
            raise RuntimeEventRegistryError("RuntimeEvent Registry 已冻结")
        if not isinstance(descriptor, RuntimeEventDescriptor):
            raise TypeError("descriptor 必须是 RuntimeEventDescriptor")
        if descriptor.name in self._descriptors:
            raise RuntimeEventRegistryError(f"事件重复注册：{descriptor.name}")
        self._descriptors[descriptor.name] = descriptor

    def freeze(self) -> "RuntimeEventRegistry":
        self._frozen = True
        return self

    def get(self, name: str) -> RuntimeEventDescriptor:
        normalized = str(name or "").strip()
        try:
            return self._descriptors[normalized]
        except KeyError as exc:
            raise RuntimeEventRegistryError(f"事件未注册：{normalized or '<empty>'}") from exc

    def list(self) -> tuple[RuntimeEventDescriptor, ...]:
        return tuple(self._descriptors[name] for name in sorted(self._descriptors))


@dataclass(frozen=True, slots=True)
class RuntimeEventContext:
    trace_id: str = ""
    run_id: str = ""
    tool_call_id: str = ""

    def __post_init__(self) -> None:
        for name in ("trace_id", "run_id", "tool_call_id"):
            value = str(getattr(self, name) or "").strip()
            if len(value) > 128 or any(ord(char) < 32 for char in value):
                value = ""
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_id: str
    name: str
    domain: str
    phase: RuntimeEventPhase
    occurred_at: datetime
    context: RuntimeEventContext
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
    ) -> None:
        if not registry.frozen:
            raise RuntimeEventRegistryError("RuntimeEventEmitter 只接受已冻结 Registry")
        self._registry = registry
        self._sinks = tuple(sinks)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._event_id_factory = event_id_factory or (lambda: uuid.uuid4().hex)
        self._fail_open = bool(fail_open)

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
            attributes=sanitized,
            dropped_attribute_count=dropped,
        )
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception:
                if not self._fail_open:
                    raise
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
            if event_field is None or any(
                part in lowered for part in self._SENSITIVE_KEY_PARTS
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
