"""图片预缓存的进程级 Port 绑定。"""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from typing import Protocol, runtime_checkable


class ImagePrecacheRuntimeUnavailableError(RuntimeError):
    """图片预处理 Adapter 未启动或已停止。"""


@runtime_checkable
class ImagePrecachePort(Protocol):
    def precache(
        self,
        sources: tuple[str, ...],
        *,
        source_type: str,
        source_name_prefix: str,
    ) -> tuple[Mapping[str, object], ...]: ...


_lock = RLock()
_port: ImagePrecachePort | None = None


def bind_image_precache_port(port: ImagePrecachePort) -> None:
    if not isinstance(port, ImagePrecachePort):
        raise TypeError("port 未实现 ImagePrecachePort")
    global _port
    with _lock:
        if _port is not None:
            raise RuntimeError("Image Precache Runtime 已绑定")
        _port = port


def clear_image_precache_port() -> None:
    global _port
    with _lock:
        _port = None


def get_image_precache_port() -> ImagePrecachePort:
    with _lock:
        port = _port
    if port is None:
        raise ImagePrecacheRuntimeUnavailableError(
            "Image Precache Runtime 尚未启动或已经停止"
        )
    return port


__all__ = [
    "ImagePrecachePort",
    "ImagePrecacheRuntimeUnavailableError",
    "bind_image_precache_port",
    "clear_image_precache_port",
    "get_image_precache_port",
]
