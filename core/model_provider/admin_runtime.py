"""模型管理控制面所需的 Provider Port。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from core.model_provider.route_plan import ReplyRoutePlan


class ModelProviderAdminRuntimeUnavailableError(RuntimeError):
    """模型管理 Adapter 未启动或已停止。"""


class CodexAdminErrorCode(str, Enum):
    INVALID_ACCOUNT = "invalid_account"
    ACCOUNT_NOT_FOUND = "account_not_found"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    UPSTREAM_FAILED = "upstream_failed"


class CodexAdminError(RuntimeError):
    def __init__(self, code: CodexAdminErrorCode, message: str) -> None:
        self.code = CodexAdminErrorCode(code)
        super().__init__(str(message or self.code.value))


@dataclass(frozen=True, slots=True)
class ModelPresetProbeResult:
    content: str
    usage: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", str(self.content or ""))
        object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))


@runtime_checkable
class NativeToolCatalogPort(Protocol):
    def list_native_tools(self) -> tuple[Mapping[str, object], ...]: ...


@runtime_checkable
class PresetConnectivityPort(Protocol):
    async def probe_preset(
        self,
        plan: ReplyRoutePlan,
        *,
        prompt: str,
    ) -> ModelPresetProbeResult: ...


@runtime_checkable
class CodexAdminPort(Protocol):
    def status(self) -> Mapping[str, object]: ...

    def list_accounts(
        self,
        database: object,
    ) -> tuple[Mapping[str, object], ...]: ...

    def update_account(
        self,
        account_id: str,
        *,
        name: str | None,
        enabled: bool | None,
        weight: int | None,
        database: object,
    ) -> Mapping[str, object]: ...

    def delete_account(self, account_id: str, *, database: object) -> bool: ...

    async def start_device_login(
        self,
        *,
        account_id: str,
        name: str,
        database: object,
    ) -> Mapping[str, object]: ...

    async def get_device_login(
        self,
        login_id: str,
    ) -> Mapping[str, object] | None: ...

    async def usage(self) -> Mapping[str, object]: ...


_lock = RLock()
_native_tools: NativeToolCatalogPort | None = None
_connectivity: PresetConnectivityPort | None = None
_codex_admin: CodexAdminPort | None = None


def start_model_provider_admin_runtime(
    *,
    native_tools: NativeToolCatalogPort,
    connectivity: PresetConnectivityPort,
    codex_admin: CodexAdminPort,
) -> None:
    if not isinstance(native_tools, NativeToolCatalogPort):
        raise TypeError("native_tools 未实现 NativeToolCatalogPort")
    if not isinstance(connectivity, PresetConnectivityPort):
        raise TypeError("connectivity 未实现 PresetConnectivityPort")
    if not isinstance(codex_admin, CodexAdminPort):
        raise TypeError("codex_admin 未实现 CodexAdminPort")
    global _native_tools, _connectivity, _codex_admin
    with _lock:
        if (
            _native_tools is not None
            or _connectivity is not None
            or _codex_admin is not None
        ):
            raise RuntimeError("Model Provider Admin Runtime 已启动")
        _native_tools = native_tools
        _connectivity = connectivity
        _codex_admin = codex_admin


def stop_model_provider_admin_runtime() -> None:
    global _native_tools, _connectivity, _codex_admin
    with _lock:
        _native_tools = None
        _connectivity = None
        _codex_admin = None


def _require_codex_admin() -> CodexAdminPort:
    with _lock:
        port = _codex_admin
    if port is None:
        raise ModelProviderAdminRuntimeUnavailableError(
            "Codex Admin Runtime 尚未启动或已经停止"
        )
    return port


def list_provider_native_tools() -> tuple[Mapping[str, object], ...]:
    with _lock:
        port = _native_tools
    if port is None:
        raise ModelProviderAdminRuntimeUnavailableError(
            "Native Tool Catalog 尚未启动或已经停止"
        )
    return tuple(MappingProxyType(dict(item)) for item in port.list_native_tools())


async def probe_model_preset(
    plan: ReplyRoutePlan,
    *,
    prompt: str,
) -> ModelPresetProbeResult:
    if not isinstance(plan, ReplyRoutePlan):
        raise TypeError("plan 必须是 ReplyRoutePlan")
    with _lock:
        port = _connectivity
    if port is None:
        raise ModelProviderAdminRuntimeUnavailableError(
            "Preset Connectivity Runtime 尚未启动或已经停止"
        )
    result = await port.probe_preset(plan, prompt=str(prompt or ""))
    if not isinstance(result, ModelPresetProbeResult):
        raise TypeError("Preset Connectivity Port 返回了无效结果")
    return result


def codex_admin_status() -> Mapping[str, object]:
    return MappingProxyType(dict(_require_codex_admin().status()))


def list_codex_account_views(
    database: object,
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        MappingProxyType(dict(item))
        for item in _require_codex_admin().list_accounts(database)
    )


def update_codex_account_view(
    account_id: str,
    *,
    name: str | None,
    enabled: bool | None,
    weight: int | None,
    database: object,
) -> Mapping[str, object]:
    return MappingProxyType(
        dict(
            _require_codex_admin().update_account(
                account_id,
                name=name,
                enabled=enabled,
                weight=weight,
                database=database,
            )
        )
    )


def delete_codex_account(account_id: str, *, database: object) -> bool:
    return bool(_require_codex_admin().delete_account(account_id, database=database))


async def start_codex_device_login(
    *,
    account_id: str,
    name: str,
    database: object,
) -> Mapping[str, object]:
    return MappingProxyType(
        dict(
            await _require_codex_admin().start_device_login(
                account_id=account_id,
                name=name,
                database=database,
            )
        )
    )


async def get_codex_device_login(
    login_id: str,
) -> Mapping[str, object] | None:
    result = await _require_codex_admin().get_device_login(login_id)
    return MappingProxyType(dict(result)) if result is not None else None


async def get_codex_usage() -> Mapping[str, object]:
    return MappingProxyType(dict(await _require_codex_admin().usage()))


__all__ = [
    "CodexAdminError",
    "CodexAdminErrorCode",
    "CodexAdminPort",
    "ModelPresetProbeResult",
    "ModelProviderAdminRuntimeUnavailableError",
    "NativeToolCatalogPort",
    "PresetConnectivityPort",
    "codex_admin_status",
    "delete_codex_account",
    "get_codex_device_login",
    "get_codex_usage",
    "list_codex_account_views",
    "list_provider_native_tools",
    "probe_model_preset",
    "start_codex_device_login",
    "start_model_provider_admin_runtime",
    "stop_model_provider_admin_runtime",
    "update_codex_account_view",
]
