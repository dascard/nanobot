"""自检使用的框架无关 Runtime 诊断 Port。"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RuntimeToolBindingDiagnostic:
    """单个 Agent Loop Runtime 的脱敏工具绑定快照。"""

    runtime_id: str
    binding_ids: tuple[str, ...]
    import_failure_count: int = 0


@dataclass(frozen=True, slots=True)
class ToolRuntimeDiagnosticsSnapshot:
    """工具注册表到各 Runtime Adapter 的投影结果。"""

    expected_binding_ids: tuple[str, ...]
    required_runtime_ids: tuple[str, ...]
    runtimes: tuple[RuntimeToolBindingDiagnostic, ...]
    unavailable_runtime_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EffectiveModelRouteDiagnostic:
    """不包含 URL、Token 或其他凭据正文的有效 Route 状态。"""

    route_key: str
    provider_id: str
    driver_type: str
    model: str
    provider_enabled: bool
    route_completion_supported: bool
    endpoint_configured: bool
    credential_configured: bool


@dataclass(frozen=True, slots=True)
class ReplyRouteCandidateDiagnostic:
    """已通过 reply Route 选择策略的脱敏候选。"""

    provider_id: str
    driver_type: str
    model: str
    endpoint_configured: bool


@dataclass(frozen=True, slots=True)
class ModelRuntimeDiagnosticsSnapshot:
    """模型业务 Route 和 Agent reply Route 的只读快照。"""

    routes: tuple[EffectiveModelRouteDiagnostic, ...]
    reply_candidates: tuple[ReplyRouteCandidateDiagnostic, ...]


@runtime_checkable
class SelfcheckRuntimeDiagnosticsPort(Protocol):
    """由 Composition Root 注入的具体 Runtime 诊断能力。"""

    def inspect_tool_bindings(self) -> ToolRuntimeDiagnosticsSnapshot: ...

    def inspect_model_routes(self) -> ModelRuntimeDiagnosticsSnapshot: ...


class SelfcheckRuntimeDiagnosticsUnavailableError(RuntimeError):
    """自检 Runtime 诊断 Adapter 尚未启动或已经停止。"""


_lock = RLock()
_port: SelfcheckRuntimeDiagnosticsPort | None = None


def bind_selfcheck_runtime_diagnostics_port(
    port: SelfcheckRuntimeDiagnosticsPort,
) -> None:
    if not isinstance(port, SelfcheckRuntimeDiagnosticsPort):
        raise TypeError("port 未实现 SelfcheckRuntimeDiagnosticsPort")
    global _port
    with _lock:
        if _port is not None:
            raise RuntimeError("Selfcheck Runtime Diagnostics 已绑定")
        _port = port


def clear_selfcheck_runtime_diagnostics_port() -> None:
    global _port
    with _lock:
        _port = None


def get_selfcheck_runtime_diagnostics_port(
) -> SelfcheckRuntimeDiagnosticsPort:
    with _lock:
        port = _port
    if port is None:
        raise SelfcheckRuntimeDiagnosticsUnavailableError(
            "Selfcheck Runtime Diagnostics 尚未启动或已经停止"
        )
    return port


__all__ = [
    "EffectiveModelRouteDiagnostic",
    "ModelRuntimeDiagnosticsSnapshot",
    "ReplyRouteCandidateDiagnostic",
    "RuntimeToolBindingDiagnostic",
    "SelfcheckRuntimeDiagnosticsPort",
    "SelfcheckRuntimeDiagnosticsUnavailableError",
    "ToolRuntimeDiagnosticsSnapshot",
    "bind_selfcheck_runtime_diagnostics_port",
    "clear_selfcheck_runtime_diagnostics_port",
    "get_selfcheck_runtime_diagnostics_port",
]
