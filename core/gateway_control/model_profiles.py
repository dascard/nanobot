"""Gateway 可切换模型 Profile 的框架无关 Port。"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class GatewayModelProfileDescriptor:
    """可向远程客户端公开的最小模型 Profile 描述。"""

    profile_id: str
    model: str
    provider_id: str
    provider_name: str
    supports_tools: bool
    supports_image: bool

    def __post_init__(self) -> None:
        profile_id = str(self.profile_id or "").strip()
        if not profile_id:
            raise ValueError("Gateway 模型 Profile ID 不能为空")
        object.__setattr__(self, "profile_id", profile_id)

    def to_payload(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "model": str(self.model or ""),
            "provider_id": str(self.provider_id or ""),
            "provider_name": str(self.provider_name or ""),
            "supports_tools": bool(self.supports_tools),
            "supports_image": bool(self.supports_image),
        }


@runtime_checkable
class GatewayModelProfilePort(Protocol):
    """由具体 Agent Runtime 提供已验证的 reply Profile。"""

    def list_profiles(self) -> tuple[GatewayModelProfileDescriptor, ...]: ...


class GatewayModelProfileRuntimeUnavailableError(RuntimeError):
    """Gateway 模型 Profile Adapter 尚未启动或已经停止。"""


_lock = RLock()
_port: GatewayModelProfilePort | None = None


def bind_gateway_model_profile_port(port: GatewayModelProfilePort) -> None:
    if not isinstance(port, GatewayModelProfilePort):
        raise TypeError("port 未实现 GatewayModelProfilePort")
    global _port
    with _lock:
        if _port is not None:
            raise RuntimeError("Gateway Model Profile Runtime 已绑定")
        _port = port


def clear_gateway_model_profile_port() -> None:
    global _port
    with _lock:
        _port = None


def get_gateway_model_profile_port() -> GatewayModelProfilePort:
    with _lock:
        port = _port
    if port is None:
        raise GatewayModelProfileRuntimeUnavailableError(
            "Gateway Model Profile Runtime 尚未启动或已经停止"
        )
    return port


__all__ = [
    "GatewayModelProfileDescriptor",
    "GatewayModelProfilePort",
    "GatewayModelProfileRuntimeUnavailableError",
    "bind_gateway_model_profile_port",
    "clear_gateway_model_profile_port",
    "get_gateway_model_profile_port",
]
