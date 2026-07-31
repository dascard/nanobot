"""外部 Provider 凭据状态探针的进程级 Port。"""

from __future__ import annotations

from typing import Protocol


class ProviderCredentialStatusPort(Protocol):
    def resolve(self, driver_type: str) -> tuple[bool, str]: ...


_status_port: ProviderCredentialStatusPort | None = None


def start_provider_credential_status_runtime(
    status_port: ProviderCredentialStatusPort,
) -> None:
    global _status_port
    if _status_port is not None:
        raise RuntimeError("Provider Credential Status Runtime 已启动")
    _status_port = status_port


def stop_provider_credential_status_runtime() -> None:
    global _status_port
    _status_port = None


def resolve_provider_credential_status(
    driver_type: str,
) -> tuple[bool, str]:
    if _status_port is None:
        return False, "none"
    return _status_port.resolve(driver_type)


__all__ = [
    "ProviderCredentialStatusPort",
    "resolve_provider_credential_status",
    "start_provider_credential_status_runtime",
    "stop_provider_credential_status_runtime",
]
