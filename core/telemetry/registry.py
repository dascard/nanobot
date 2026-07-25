"""Nanobot 内建 Telemetry 指标的代码所有 Registry。"""

from __future__ import annotations

from core.registry import RegistryBuilder, RegistrySnapshot
from core.telemetry.contracts import (
    MetricInstrument,
    TelemetryMetricDescriptor,
)


class TelemetryMetricRegistry:
    def __init__(
        self,
        descriptors: tuple[TelemetryMetricDescriptor, ...],
    ) -> None:
        builder = RegistryBuilder[TelemetryMetricDescriptor](
            "telemetry_metric"
        )
        for descriptor in descriptors:
            builder.register(descriptor)
        self._snapshot = builder.freeze()

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[TelemetryMetricDescriptor]:
        return self._snapshot

    def require(self, metric_name: str) -> TelemetryMetricDescriptor:
        try:
            return self._snapshot.require(str(metric_name or "").strip())
        except KeyError as exc:
            raise ValueError(
                f"未登记的 Telemetry 指标：{metric_name or '<empty>'}"
            ) from exc

    def descriptors(self) -> tuple[TelemetryMetricDescriptor, ...]:
        return tuple(self._snapshot)


TELEMETRY_METRIC_REGISTRY = TelemetryMetricRegistry((
    TelemetryMetricDescriptor(
        metric_name="nanobot_runtime_events_total",
        instrument=MetricInstrument.COUNTER,
        unit="event",
        owner_module="runtime.telemetry",
        labels=("domain", "event_name", "failure_code", "phase"),
        description="按事件合同统计 RuntimeEvent 数量",
    ),
    TelemetryMetricDescriptor(
        metric_name="nanobot_runtime_event_duration_ms",
        instrument=MetricInstrument.HISTOGRAM,
        unit="millisecond",
        owner_module="runtime.telemetry",
        labels=("domain", "event_name", "phase"),
        histogram_buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 3000, 10000),
        description="RuntimeEvent 所代表操作的有界耗时",
    ),
    TelemetryMetricDescriptor(
        metric_name="nanobot_runtime_jobs_total",
        instrument=MetricInstrument.COUNTER,
        unit="transition",
        owner_module="runtime.telemetry",
        labels=("failure_code", "job_type", "status", "transition"),
        description="Durable Job 租约、重试与结算状态变化",
    ),
    TelemetryMetricDescriptor(
        metric_name="nanobot_runtime_jobs_active",
        instrument=MetricInstrument.GAUGE,
        unit="job",
        owner_module="runtime.telemetry",
        labels=("job_type", "status"),
        description="按稳定 Job 类型和状态统计活动任务",
    ),
    TelemetryMetricDescriptor(
        metric_name="nanobot_runtime_telemetry_dropped_total",
        instrument=MetricInstrument.COUNTER,
        unit="event",
        owner_module="runtime.telemetry",
        labels=("reason", "sink"),
        description="Telemetry 队列或持久化失败时丢弃的事件数",
    ),
))


__all__ = [
    "TELEMETRY_METRIC_REGISTRY",
    "TelemetryMetricRegistry",
]
