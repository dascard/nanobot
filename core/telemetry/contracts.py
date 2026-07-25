"""框架无关的 Telemetry 关联、来源与指标合同。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


_METRIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,127}$")
_LABEL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TelemetryContractError(ValueError):
    """Telemetry Descriptor 或运行时元数据不符合稳定合同。"""


class MetricInstrument(StrEnum):
    COUNTER = "counter"
    HISTOGRAM = "histogram"
    GAUGE = "gauge"


def _safe_identifier(value: object, *, max_chars: int = 160) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > max_chars
        or any(ord(character) < 32 for character in text)
    ):
        return ""
    return text


@dataclass(frozen=True, slots=True)
class TelemetryCorrelation:
    """跨 HTTP、Agent、Task、Job、Tool 和 Delivery 的不透明关联键。"""

    request_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    trace_id: str = ""
    run_id: str = ""
    task_id: str = ""
    task_run_id: str = ""
    job_id: str = ""
    tool_call_id: str = ""
    delivery_id: str = ""
    parent_job_id: str = ""

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            object.__setattr__(
                self,
                field_name,
                _safe_identifier(getattr(self, field_name)),
            )

    def to_dict(self) -> dict[str, str]:
        return {
            field_name: str(getattr(self, field_name) or "")
            for field_name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class TelemetryProvenance:
    """一条观测事实对应的 Registry、模块和发布版本。"""

    registry_generation: int
    registry_sha256: str
    module_id: str
    module_version: str
    artifact_revision: str = ""

    def __post_init__(self) -> None:
        if (
            isinstance(self.registry_generation, bool)
            or not isinstance(self.registry_generation, int)
            or self.registry_generation <= 0
        ):
            raise TelemetryContractError(
                "Telemetry Registry generation 必须为正整数"
            )
        registry_sha256 = str(self.registry_sha256 or "").strip().lower()
        if _SHA256_RE.fullmatch(registry_sha256) is None:
            raise TelemetryContractError(
                "Telemetry Registry SHA-256 无效"
            )
        module_id = str(self.module_id or "").strip()
        if _MODULE_ID_RE.fullmatch(module_id) is None:
            raise TelemetryContractError("Telemetry module_id 无效")
        module_version = _safe_identifier(
            self.module_version,
            max_chars=64,
        )
        if not module_version:
            raise TelemetryContractError("Telemetry module_version 不能为空")
        object.__setattr__(self, "registry_sha256", registry_sha256)
        object.__setattr__(self, "module_id", module_id)
        object.__setattr__(self, "module_version", module_version)
        object.__setattr__(
            self,
            "artifact_revision",
            _safe_identifier(self.artifact_revision, max_chars=128),
        )


@dataclass(frozen=True, slots=True)
class TelemetryMetricDescriptor:
    """稳定指标名、类型和低基数 label 白名单。"""

    metric_name: str
    instrument: MetricInstrument
    unit: str
    owner_module: str
    labels: tuple[str, ...] = ()
    histogram_buckets: tuple[float, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        metric_name = str(self.metric_name or "").strip()
        if _METRIC_NAME_RE.fullmatch(metric_name) is None:
            raise TelemetryContractError(
                f"Telemetry 指标名不合法：{metric_name!r}"
            )
        instrument = MetricInstrument(self.instrument)
        unit = _safe_identifier(self.unit, max_chars=32)
        if not unit:
            raise TelemetryContractError("Telemetry 指标 unit 不能为空")
        owner_module = str(self.owner_module or "").strip()
        if _MODULE_ID_RE.fullmatch(owner_module) is None:
            raise TelemetryContractError(
                "Telemetry 指标 owner_module 无效"
            )
        labels = tuple(str(label or "").strip() for label in self.labels)
        if len(labels) != len(set(labels)):
            raise TelemetryContractError(
                f"Telemetry 指标 {metric_name} 包含重复 label"
            )
        invalid_labels = [
            label
            for label in labels
            if _LABEL_NAME_RE.fullmatch(label) is None
        ]
        if invalid_labels:
            raise TelemetryContractError(
                f"Telemetry 指标 {metric_name} label 不合法"
            )
        if len(labels) > 12:
            raise TelemetryContractError(
                f"Telemetry 指标 {metric_name} label 过多"
            )
        buckets = tuple(float(item) for item in self.histogram_buckets)
        if instrument is MetricInstrument.HISTOGRAM:
            if (
                not buckets
                or tuple(sorted(set(buckets))) != buckets
                or any(
                    not math.isfinite(item) or item <= 0
                    for item in buckets
                )
            ):
                raise TelemetryContractError(
                    f"Telemetry Histogram {metric_name} buckets 无效"
                )
        elif buckets:
            raise TelemetryContractError(
                f"非 Histogram 指标 {metric_name} 不能声明 buckets"
            )
        object.__setattr__(self, "metric_name", metric_name)
        object.__setattr__(self, "instrument", instrument)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "owner_module", owner_module)
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "histogram_buckets", buckets)
        object.__setattr__(
            self,
            "description",
            _safe_identifier(self.description, max_chars=240),
        )

    @property
    def registry_namespace(self) -> str:
        return "telemetry_metric"

    @property
    def registry_id(self) -> str:
        return self.metric_name

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return MappingProxyType({
            "instrument": self.instrument.value,
            "unit": self.unit,
            "owner_module": self.owner_module,
            "labels": list(self.labels),
            "histogram_buckets": list(self.histogram_buckets),
            "description": self.description,
        })


__all__ = [
    "MetricInstrument",
    "TelemetryContractError",
    "TelemetryCorrelation",
    "TelemetryMetricDescriptor",
    "TelemetryProvenance",
]
