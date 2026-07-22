"""主动外呼进程身份的显式生命周期。

进程 owner 与 writer token 属于运行态，不是配置常量。它们只能由 composition
root 在启动阶段创建；业务模块在运行时读取，未启动或已经停止时一律失败关闭。
"""

from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass
from typing import Callable, Literal


ProactiveRuntimeState = Literal["new", "started", "stopped"]


class ProactiveRuntimeUnavailableError(RuntimeError):
    """主动外呼运行时尚未启动或已经停止。"""


@dataclass(frozen=True, slots=True)
class ProactiveProcessIdentity:
    owner: str
    writer_token: str

    def __post_init__(self) -> None:
        owner = str(self.owner or "").strip()
        writer_token = str(self.writer_token or "").strip()
        if not owner or len(owner) > 128 or any(ord(char) < 32 for char in owner):
            raise ValueError("主动外呼 owner 必须是 1-128 字符的安全文本")
        if len(writer_token) < 32 or len(writer_token) > 256:
            raise ValueError("主动外呼 writer token 长度必须为 32-256 字符")
        object.__setattr__(self, "owner", owner)
        object.__setattr__(self, "writer_token", writer_token)


class ProactiveRuntimeLifecycle:
    """线程安全、可重复进入 lifespan 的进程级运行时容器。"""

    def __init__(
        self,
        *,
        pid_provider: Callable[[], int] | None = None,
        token_factory: Callable[[int], str] | None = None,
    ) -> None:
        self._pid_provider = pid_provider or os.getpid
        self._token_factory = token_factory or secrets.token_hex
        self._lock = threading.RLock()
        self._state: ProactiveRuntimeState = "new"
        self._identity: ProactiveProcessIdentity | None = None

    @property
    def state(self) -> ProactiveRuntimeState:
        with self._lock:
            return self._state

    def start(
        self,
        identity: ProactiveProcessIdentity | None = None,
    ) -> ProactiveProcessIdentity:
        with self._lock:
            if self._state == "started":
                if identity is not None and identity != self._identity:
                    raise RuntimeError("主动外呼运行时已使用另一身份启动")
                assert self._identity is not None
                return self._identity
            resolved = identity or ProactiveProcessIdentity(
                owner=(
                    f"proactive-outreach:{self._pid_provider()}:"
                    f"{self._token_factory(16)}"
                )[:128],
                writer_token=self._token_factory(32),
            )
            self._identity = resolved
            self._state = "started"
            return resolved

    def require_identity(self) -> ProactiveProcessIdentity:
        with self._lock:
            if self._state != "started" or self._identity is None:
                raise ProactiveRuntimeUnavailableError(
                    "主动外呼运行时未启动，拒绝获取进程 writer 身份"
                )
            return self._identity

    def stop(self) -> None:
        with self._lock:
            self._identity = None
            self._state = "stopped"


_PROCESS_RUNTIME = ProactiveRuntimeLifecycle()


def start_proactive_runtime(
    identity: ProactiveProcessIdentity | None = None,
) -> ProactiveProcessIdentity:
    return _PROCESS_RUNTIME.start(identity)


def get_proactive_process_identity() -> ProactiveProcessIdentity:
    return _PROCESS_RUNTIME.require_identity()


def stop_proactive_runtime() -> None:
    _PROCESS_RUNTIME.stop()


def proactive_runtime_state() -> ProactiveRuntimeState:
    return _PROCESS_RUNTIME.state


__all__ = [
    "ProactiveProcessIdentity",
    "ProactiveRuntimeLifecycle",
    "ProactiveRuntimeState",
    "ProactiveRuntimeUnavailableError",
    "get_proactive_process_identity",
    "proactive_runtime_state",
    "start_proactive_runtime",
    "stop_proactive_runtime",
]
