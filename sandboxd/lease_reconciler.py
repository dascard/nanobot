"""sandboxd Lease 的启动回收、TTL 对账与定时执行。"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from sandboxd.lease_backend import LeaseBackend
from sandboxd.lease_store import LeaseStore


logger = logging.getLogger("nanobot.sandboxd.lease_reconciler")


class LeaseReconciler:
    def __init__(
        self,
        backend: LeaseBackend,
        store: LeaseStore,
        *,
        interval_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.backend = backend
        self.store = store
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.clock = clock
        self._reconcile_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def recover_previous_controller(self) -> dict[str, Any]:
        """只回收带完整 Lease 所有权标签且 epoch 不是当前值的容器。"""

        current_epoch = self.store.controller_epoch
        recovered_leases: list[str] = []
        recovered_processes: list[str] = []
        failed_leases: list[str] = []
        for fact in self.backend.list(include_cached=False):
            lease_id = str(fact.get("lease_id") or "")
            if (
                not fact.get("present")
                or str(fact.get("controller_epoch") or "") == current_epoch
            ):
                continue
            try:
                result = self.backend.recycle(
                    lease_id,
                    reason="controller_restarted",
                )
            except SandboxServiceError:
                failed_leases.append(lease_id)
                continue
            if result.get("lease_recycled"):
                recovered_leases.append(lease_id)
            recovered_processes.extend(
                str(item)
                for item in result.get("affected_process_ids") or []
            )
        cleaned_orphan_network_leases = (
            self.backend.cleanup_orphan_networks()
        )
        self.store.record_startup_recovery(
            recovered_lease_ids=recovered_leases,
            recovered_process_ids=recovered_processes,
        )
        return {
            "controller_epoch": current_epoch,
            "recovered_lease_ids": sorted(set(recovered_leases)),
            "recovered_process_ids": sorted(set(recovered_processes)),
            "failed_lease_ids": sorted(set(failed_leases)),
            "cleaned_orphan_network_lease_ids": (
                cleaned_orphan_network_leases
            ),
        }

    def reconcile(self) -> dict[str, Any]:
        """按 Docker 事实和安全 TTL 回收 Lease；并发重入快速失败。"""

        if not self._reconcile_lock.acquire(blocking=False):
            raise SandboxServiceError(
                SandboxErrorCode.SANDBOX_BUSY,
                "Sandbox Lease 对账正在执行",
                retryable=True,
                stop=False,
            )
        try:
            now_unix = float(self.clock())
            current_epoch = self.store.controller_epoch
            inspected = 0
            recycled: list[dict[str, Any]] = []
            failed_lease_ids: list[str] = []
            for fact in self.backend.list():
                inspected += 1
                lease_id = str(fact.get("lease_id") or "")
                reason = ""
                catalog = self.backend.config.profile_catalog
                if (
                    fact.get("present")
                    and str(fact.get("controller_epoch") or "")
                    != current_epoch
                ):
                    reason = "controller_restarted"
                elif (
                    catalog is None
                    or str(fact.get("catalog_generation") or "")
                    != catalog.catalog_generation
                    or str(fact.get("policy_sha256") or "")
                    != catalog.policy_sha256
                    or str(fact.get("profile_id") or "")
                    not in catalog.by_id
                    or not catalog.profile(
                        str(fact.get("profile_id") or "")
                    ).grantable
                    or catalog.profile(
                        str(fact.get("profile_id") or "")
                    ).execution_mode
                    != "lease"
                ):
                    reason = "lease_policy_drift"
                elif not fact.get("present") or not fact.get("running"):
                    reason = "lease_missing"
                elif not fact.get("network_ready"):
                    reason = "lease_network_drift"
                elif now_unix >= float(fact.get("max_expires_at_unix") or 0):
                    reason = "lease_max_ttl"
                elif (
                    not list(fact.get("active_process_ids") or [])
                    and now_unix
                    >= float(fact.get("idle_expires_at_unix") or 0)
                ):
                    reason = "lease_idle_ttl"
                if not reason:
                    continue
                try:
                    result = self.backend.recycle(
                        lease_id,
                        reason=reason,
                    )
                except SandboxServiceError:
                    failed_lease_ids.append(lease_id)
                    continue
                recycled.append({
                    "lease_id": lease_id,
                    "termination_reason": reason,
                    "affected_process_ids": list(
                        result.get("affected_process_ids") or []
                    ),
                })
            cleaned_orphan_network_leases = (
                self.backend.cleanup_orphan_networks()
            )
            return {
                "controller_epoch": current_epoch,
                "inspected": inspected,
                "recycled": recycled,
                "failed_lease_ids": sorted(set(failed_lease_ids)),
                "cleaned_orphan_network_lease_ids": (
                    cleaned_orphan_network_leases
                ),
            }
        finally:
            self._reconcile_lock.release()

    def start(self) -> "LeaseReconciler":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="sandboxd-lease-reconciler",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout)))
        self._thread = None

    def _run_loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.reconcile()
            except SandboxServiceError as exc:
                if exc.code is not SandboxErrorCode.SANDBOX_BUSY:
                    logger.error(
                        "sandboxd Lease 周期对账失败 code=%s",
                        exc.code.value,
                    )
            except Exception:
                logger.error("sandboxd Lease 周期对账异常", exc_info=True)


__all__ = ["LeaseReconciler"]
