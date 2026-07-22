"""模型目录写入 Port；核心 Scout 不依赖具体 registry 实现。"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class ModelCatalogRuntimeState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    STOPPED = "stopped"


@runtime_checkable
class ModelCatalogWriterPort(Protocol):
    @property
    def adapter_id(self) -> str: ...

    def upsert_models(self, models: tuple[Mapping[str, Any], ...]) -> int: ...


class ModelCatalogRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = ModelCatalogRuntimeState.NEW
        self._port: ModelCatalogWriterPort | None = None

    @property
    def state(self) -> ModelCatalogRuntimeState:
        with self._lock:
            return self._state

    def start(self, port: ModelCatalogWriterPort) -> None:
        if not isinstance(port, ModelCatalogWriterPort):
            raise TypeError("port 未实现 ModelCatalogWriterPort")
        with self._lock:
            if self._state is ModelCatalogRuntimeState.RUNNING:
                if self._port is port:
                    return
                raise RuntimeError("模型目录运行时已由其他 Adapter 启动")
            self._port = port
            self._state = ModelCatalogRuntimeState.RUNNING

    def stop(self) -> None:
        with self._lock:
            self._port = None
            self._state = ModelCatalogRuntimeState.STOPPED

    def upsert_models(self, models: tuple[Mapping[str, Any], ...]) -> int:
        with self._lock:
            if self._state is not ModelCatalogRuntimeState.RUNNING:
                raise RuntimeError("模型目录运行时尚未启动或已经停止")
            port = self._port
        if port is None:
            raise RuntimeError("模型目录 Adapter 未配置")
        result = port.upsert_models(models)
        if not isinstance(result, int) or result < 0:
            raise TypeError("模型目录 Adapter 返回了无效写入计数")
        return result

    def introspect(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self._state.value,
                "adapter_id": (
                    str(self._port.adapter_id) if self._port is not None else ""
                ),
            }


_MODEL_CATALOG_RUNTIME = ModelCatalogRuntime()


def start_model_catalog_runtime(port: ModelCatalogWriterPort) -> None:
    _MODEL_CATALOG_RUNTIME.start(port)


def stop_model_catalog_runtime() -> None:
    _MODEL_CATALOG_RUNTIME.stop()


def model_catalog_runtime_status() -> dict[str, object]:
    return _MODEL_CATALOG_RUNTIME.introspect()


def upsert_model_catalog(models: list[dict[str, Any]]) -> int:
    return _MODEL_CATALOG_RUNTIME.upsert_models(tuple(models))


__all__ = [
    "ModelCatalogRuntime",
    "ModelCatalogRuntimeState",
    "ModelCatalogWriterPort",
    "model_catalog_runtime_status",
    "start_model_catalog_runtime",
    "stop_model_catalog_runtime",
    "upsert_model_catalog",
]
