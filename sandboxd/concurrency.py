"""Sandbox Profile 与 Lease 维度的非阻塞并发门禁。"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Final

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.profile_catalog import ProfileCatalog


_PROFILE_BUSY_SUMMARY: Final = "目标 Sandbox Profile 并发已满"
_LEASE_BUSY_SUMMARY: Final = "目标 Sandbox Lease 活动进程已满"


@dataclass(slots=True)
class ConcurrencyPermit:
    """一次并发占位；重复释放保持幂等。"""

    _limiter: "ProfileConcurrencyLimiter"
    profile_id: str
    lease_id: str
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._limiter._release(self.profile_id, self.lease_id)
        self._released = True

    def __enter__(self) -> "ConcurrencyPermit":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.release()


class ProfileConcurrencyLimiter:
    """按 canonical Profile 限制全局并发，并为 Lease 限制进程数。"""

    def __init__(self, catalog: ProfileCatalog) -> None:
        self._limits = {
            profile.profile_id: (
                int(profile.global_concurrency),
                int(profile.max_processes),
            )
            for profile in catalog.profiles
        }
        self._active_by_profile: dict[str, int] = {}
        self._active_by_lease: dict[tuple[str, str], int] = {}
        self._guard = threading.Lock()

    def acquire(
        self,
        profile_id: str,
        *,
        lease_id: str = "",
    ) -> ConcurrencyPermit:
        """非阻塞领取一个槽位；Lease 为空表示一次性容器。"""

        normalized_profile = str(profile_id or "")
        normalized_lease = str(lease_id or "")
        try:
            global_limit, lease_limit = self._limits[normalized_profile]
        except KeyError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 执行 Profile 无效",
            ) from exc

        with self._guard:
            profile_active = self._active_by_profile.get(normalized_profile, 0)
            if profile_active >= global_limit:
                raise SandboxServiceError(
                    SandboxErrorCode.SANDBOX_BUSY,
                    _PROFILE_BUSY_SUMMARY,
                    retryable=True,
                    stop=False,
                )
            lease_key = (normalized_profile, normalized_lease)
            if normalized_lease:
                lease_active = self._active_by_lease.get(lease_key, 0)
                if lease_active >= lease_limit:
                    raise SandboxServiceError(
                        SandboxErrorCode.SANDBOX_BUSY,
                        _LEASE_BUSY_SUMMARY,
                        retryable=True,
                        stop=False,
                    )
                self._active_by_lease[lease_key] = lease_active + 1
            self._active_by_profile[normalized_profile] = profile_active + 1
        return ConcurrencyPermit(self, normalized_profile, normalized_lease)

    def _release(self, profile_id: str, lease_id: str) -> None:
        with self._guard:
            profile_active = self._active_by_profile.get(profile_id, 0)
            if profile_active <= 0:
                raise RuntimeError("Sandbox Profile 并发计数发生下溢")
            if profile_active == 1:
                self._active_by_profile.pop(profile_id, None)
            else:
                self._active_by_profile[profile_id] = profile_active - 1

            if not lease_id:
                return
            lease_key = (profile_id, lease_id)
            lease_active = self._active_by_lease.get(lease_key, 0)
            if lease_active <= 0:
                raise RuntimeError("Sandbox Lease 并发计数发生下溢")
            if lease_active == 1:
                self._active_by_lease.pop(lease_key, None)
            else:
                self._active_by_lease[lease_key] = lease_active - 1

    def snapshot(self) -> dict[str, dict[str, int]]:
        """返回不含命令和身份信息的并发计数快照。"""

        with self._guard:
            return {
                "profiles": dict(self._active_by_profile),
                "leases": {
                    f"{profile_id}:{lease_id}": count
                    for (profile_id, lease_id), count in self._active_by_lease.items()
                },
            }


__all__ = [
    "ConcurrencyPermit",
    "ProfileConcurrencyLimiter",
]
