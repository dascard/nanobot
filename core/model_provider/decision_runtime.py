"""私聊与群聊模型决策的 Port 及显式生命周期。"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class DecisionModelRuntimeState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    STOPPED = "stopped"


@runtime_checkable
class DecisionModelPort(Protocol):
    """面向核心时机决策用例的窄能力 Port。"""

    @property
    def adapter_id(self) -> str: ...

    def classify_private(
        self,
        message: str,
        has_files: bool = False,
    ) -> Mapping[str, Any]: ...

    def judge_group_timing(self, context: str) -> Mapping[str, Any]: ...

    def judge_group_proactive(self, context: str) -> Mapping[str, Any]: ...


class DecisionModelRuntime:
    """模型决策 Adapter 的单进程组合运行时。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = DecisionModelRuntimeState.NEW
        self._port: DecisionModelPort | None = None

    @property
    def state(self) -> DecisionModelRuntimeState:
        with self._lock:
            return self._state

    def start(self, port: DecisionModelPort) -> None:
        if not isinstance(port, DecisionModelPort):
            raise TypeError("port 未实现 DecisionModelPort")
        with self._lock:
            if self._state is DecisionModelRuntimeState.RUNNING:
                if self._port is port:
                    return
                raise RuntimeError("模型决策运行时已由其他 Adapter 启动")
            self._port = port
            self._state = DecisionModelRuntimeState.RUNNING

    def stop(self) -> None:
        with self._lock:
            self._port = None
            self._state = DecisionModelRuntimeState.STOPPED

    def _require_port(self) -> DecisionModelPort:
        with self._lock:
            if self._state is not DecisionModelRuntimeState.RUNNING:
                raise RuntimeError("模型决策运行时尚未启动或已经停止")
            port = self._port
        if port is None:
            raise RuntimeError("模型决策 Adapter 未配置")
        return port

    @staticmethod
    def _response(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise TypeError("模型决策 Adapter 返回了无效响应合同")
        return dict(value)

    def classify_private(self, message: str, has_files: bool = False) -> dict[str, Any]:
        return self._response(
            self._require_port().classify_private(message, has_files),
        )

    def judge_group_timing(self, context: str) -> dict[str, Any]:
        return self._response(self._require_port().judge_group_timing(context))

    def judge_group_proactive(self, context: str) -> dict[str, Any]:
        return self._response(self._require_port().judge_group_proactive(context))

    def introspect(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self._state.value,
                "adapter_id": (
                    str(self._port.adapter_id) if self._port is not None else ""
                ),
            }


_DECISION_MODEL_RUNTIME = DecisionModelRuntime()


def start_decision_model_runtime(port: DecisionModelPort) -> None:
    _DECISION_MODEL_RUNTIME.start(port)


def stop_decision_model_runtime() -> None:
    _DECISION_MODEL_RUNTIME.stop()


def decision_model_runtime_status() -> dict[str, object]:
    return _DECISION_MODEL_RUNTIME.introspect()


def classify_private_decision(
    message: str,
    has_files: bool = False,
) -> dict[str, Any]:
    return _DECISION_MODEL_RUNTIME.classify_private(message, has_files)


def judge_group_timing(context: str) -> dict[str, Any]:
    return _DECISION_MODEL_RUNTIME.judge_group_timing(context)


def judge_group_proactive(context: str) -> dict[str, Any]:
    return _DECISION_MODEL_RUNTIME.judge_group_proactive(context)


__all__ = [
    "DecisionModelPort",
    "DecisionModelRuntime",
    "DecisionModelRuntimeState",
    "classify_private_decision",
    "decision_model_runtime_status",
    "judge_group_proactive",
    "judge_group_timing",
    "start_decision_model_runtime",
    "stop_decision_model_runtime",
]
