"""统一 Telemetry Contract、Registry 和持久化 Adapter。"""

from core.telemetry.contracts import (
    MetricInstrument,
    TelemetryContractError,
    TelemetryCorrelation,
    TelemetryMetricDescriptor,
    TelemetryProvenance,
)
from core.telemetry.registry import (
    TELEMETRY_METRIC_REGISTRY,
    TelemetryMetricRegistry,
)


def __getattr__(name: str):
    if name == "JobTelemetryEmitter":
        from core.telemetry.jobs import JobTelemetryEmitter

        return JobTelemetryEmitter
    if name == "SqlAlchemyRuntimeEventSink":
        from core.telemetry.persistence import SqlAlchemyRuntimeEventSink

        return SqlAlchemyRuntimeEventSink
    raise AttributeError(name)


__all__ = [
    "JobTelemetryEmitter",
    "MetricInstrument",
    "SqlAlchemyRuntimeEventSink",
    "TELEMETRY_METRIC_REGISTRY",
    "TelemetryContractError",
    "TelemetryCorrelation",
    "TelemetryMetricDescriptor",
    "TelemetryMetricRegistry",
    "TelemetryProvenance",
]
