"""Bridge Pool 的状态、启停与诊断生命周期。"""

from __future__ import annotations

import asyncio
from enum import Enum
import logging
from typing import Any


logger = logging.getLogger("nanobot.kt.bridge")


class BridgeLifecycleState(str, Enum):
    """Bridge 与全局 Runtime 共用的显式生命周期状态。"""

    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class BridgeUnavailableError(RuntimeError):
    """Bridge 尚未就绪或已进入关闭流程。"""


class BridgePoolLifecycleMixin:
    """集中管理 Bridge Pool 生命周期，不介入请求路由。"""

    async def start(self) -> None:
        async with self._create_lock:
            if self._lifecycle_state is BridgeLifecycleState.RUNNING:
                return
            if self._lifecycle_state is not BridgeLifecycleState.NEW:
                raise BridgeUnavailableError(
                    f"BridgePool 无法从 {self._lifecycle_state.value} 状态启动"
                )
            self._lifecycle_state = BridgeLifecycleState.STARTING
            self._lifecycle_state = BridgeLifecycleState.RUNNING
        logger.info("[NanobotBridgePool] started")

    @property
    def lifecycle_state(self) -> BridgeLifecycleState:
        return self._lifecycle_state

    @property
    def _tool_registry_info(self) -> dict[str, Any]:
        """从第一个 child bridge 获取工具注册表信息。"""
        for bridge in self._bridges.values():
            info = getattr(bridge, "_tool_registry_info", {})
            if info and info.get("kt_loaded"):
                return info
        return {}

    @property
    def bridge_count(self) -> int:
        return len(self._bridges)

    async def ensure_registry_probe(self) -> None:
        """确保至少有一个 child bridge 提供 registry 信息。"""
        if not self._tool_registry_info.get("kt_loaded"):
            await self._get_bridge("_admin_registry_probe")

    async def stop(self) -> None:
        import time as monotonic_time

        async with self._stop_lock:
            bridges: list[Any] = []
            try:
                async with self._create_lock:
                    if self._lifecycle_state is BridgeLifecycleState.STOPPED:
                        return
                    if self._lifecycle_state is BridgeLifecycleState.NEW:
                        self._lifecycle_state = BridgeLifecycleState.STOPPED
                        return
                    if self._lifecycle_state is not BridgeLifecycleState.RUNNING:
                        raise BridgeUnavailableError(
                            f"BridgePool 无法从 {self._lifecycle_state.value} 状态关闭"
                        )
                    self._lifecycle_state = BridgeLifecycleState.STOPPING

                deadline = monotonic_time.monotonic() + max(
                    0.0,
                    float(self.BRIDGE_STOP_TIMEOUT_SECONDS),
                )
                forced = False
                while True:
                    async with self._create_lock:
                        inflight = dict(self._bridge_inflight)
                        if not inflight:
                            bridges = list(self._bridges.values())
                            self._clear_bridge_state()
                            break
                    if monotonic_time.monotonic() >= deadline:
                        logger.warning(
                            "[BridgePool] stop inflight timeout after %.1fs, "
                            "forcing shutdown: %s",
                            float(self.BRIDGE_STOP_TIMEOUT_SECONDS),
                            inflight,
                        )
                        async with self._create_lock:
                            bridges = list(self._bridges.values())
                            self._clear_bridge_state()
                        forced = True
                        break
                    logger.debug(
                        "[BridgePool] waiting for inflight requests before stop: %s",
                        inflight,
                    )
                    await asyncio.sleep(0.01)

                if bridges:
                    await asyncio.gather(
                        *(bridge.stop() for bridge in bridges),
                        return_exceptions=True,
                    )
                if self._stop_tasks:
                    await asyncio.gather(
                        *list(self._stop_tasks),
                        return_exceptions=True,
                    )
                suffix = " (forced after inflight timeout)" if forced else ""
                logger.info("[NanobotBridgePool] stopped%s", suffix)
            finally:
                async with self._create_lock:
                    self._lifecycle_state = BridgeLifecycleState.STOPPED

    def _clear_bridge_state(self) -> None:
        self._bridges.clear()
        self._bridge_runtime_kinds.clear()
        self._last_runtime_kinds.clear()
        self._bridge_last_used.clear()
        self._bridge_inflight.clear()

    def _session_key(self, user_id: str = "", session_id: str = "") -> str:
        sid = str(session_id or "").strip()
        if sid:
            return sid
        uid = str(user_id or "").strip()
        return f"user:{uid}" if uid else "_default"

    def _track_stop_task(
        self,
        bridge: Any,
        key: str,
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(bridge.stop(), name=f"bridge-stop:{key}")
        self._stop_tasks.add(task)

        def _done(done: asyncio.Task[Any]) -> None:
            self._stop_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                logger.debug(
                    "[BridgePool] stop task cancelled for session=%s",
                    key,
                )
            except Exception as exc:
                logger.warning(
                    "[BridgePool] stop task failed for session=%s: %s",
                    key,
                    exc,
                    exc_info=True,
                )

        task.add_done_callback(_done)
        return task

    async def _release_bridge(self, key: str) -> None:
        import time as wall_time

        async with self._create_lock:
            count = self._bridge_inflight.get(key, 0)
            if count <= 1:
                self._bridge_inflight.pop(key, None)
                if key in self._bridges:
                    self._bridge_last_used[key] = wall_time.time()
            else:
                self._bridge_inflight[key] = count - 1


__all__ = [
    "BridgeLifecycleState",
    "BridgePoolLifecycleMixin",
    "BridgeUnavailableError",
]
