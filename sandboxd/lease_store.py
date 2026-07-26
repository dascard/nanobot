"""sandboxd 的最小 Lease 状态与 controller epoch 持久层。

这里保存的只是 Docker 事实的安全索引和请求幂等信息，不是 Nanobot
业务账本。命令正文、输出正文、宿主路径和凭据均不得写入。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.paths import validate_workspace_id


_LEASE_ID_RE = re.compile(r"sbxlease_[A-Za-z0-9_-]{1,55}")
_PROCESS_ID_RE = re.compile(r"sbxrun_[A-Za-z0-9_-]{1,56}")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_CONTROLLER_EPOCH_RE = re.compile(r"sbxctl_[0-9a-f]{32}")
_PROFILE_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_GENERATION_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
_MAX_STATE_BYTES = 64 * 1024


def validate_lease_id(value: str) -> str:
    normalized = str(value or "")
    if not _LEASE_ID_RE.fullmatch(normalized):
        raise SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "Sandbox Lease 标识无效",
        )
    return normalized


def validate_process_id(value: str) -> str:
    normalized = str(value or "")
    if not _PROCESS_ID_RE.fullmatch(normalized):
        raise SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "Sandbox 进程标识无效",
        )
    return normalized


def validate_request_id(value: str) -> str:
    normalized = str(value or "")
    if not _REQUEST_ID_RE.fullmatch(normalized):
        raise SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "Sandbox 请求标识无效",
        )
    return normalized


def validate_controller_epoch(value: str) -> str:
    normalized = str(value or "")
    if not _CONTROLLER_EPOCH_RE.fullmatch(normalized):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "sandboxd controller epoch 无效",
        )
    return normalized


@dataclass(frozen=True, slots=True)
class LeaseSnapshot:
    """不含命令和输出正文的 Lease 控制器快照。"""

    lease_id: str
    workspace_id: str
    profile_id: str
    catalog_generation: str
    policy_sha256: str
    image_digest: str
    controller_epoch: str
    quota_generation: int
    status: str
    created_at_unix: float
    last_active_at_unix: float
    idle_expires_at_unix: float
    max_expires_at_unix: float
    active_process_ids: tuple[str, ...] = ()

    def validated(self) -> "LeaseSnapshot":
        lease_id = validate_lease_id(self.lease_id)
        workspace_id = validate_workspace_id(self.workspace_id)
        profile_id = str(self.profile_id or "")
        generation = str(self.catalog_generation or "")
        policy_sha256 = str(self.policy_sha256 or "").lower()
        image_digest = str(self.image_digest or "").lower()
        controller_epoch = validate_controller_epoch(self.controller_epoch)
        try:
            quota_generation = int(self.quota_generation)
        except (TypeError, ValueError):
            quota_generation = 0
        status = str(self.status or "")
        if not _PROFILE_ID_RE.fullmatch(profile_id):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease Profile 快照无效",
            )
        if not _GENERATION_RE.fullmatch(generation):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease catalog generation 快照无效",
            )
        if not _SHA256_RE.fullmatch(policy_sha256):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 策略摘要快照无效",
            )
        if not _IMAGE_ID_RE.fullmatch(image_digest):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 镜像摘要快照无效",
            )
        if (
            isinstance(self.quota_generation, bool)
            or not 1 <= quota_generation <= 2_147_483_647
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease quota generation 快照无效",
            )
        if status not in {"active", "idle", "stopping"}:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 控制器状态无效",
            )
        created = float(self.created_at_unix)
        last_active = float(self.last_active_at_unix)
        idle_expires = float(self.idle_expires_at_unix)
        max_expires = float(self.max_expires_at_unix)
        if (
            created <= 0
            or last_active < created
            or idle_expires < last_active
            or max_expires < created
            or max_expires < idle_expires
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 时间快照无效",
            )
        process_ids = tuple(
            sorted({validate_process_id(item) for item in self.active_process_ids})
        )
        return replace(
            self,
            lease_id=lease_id,
            workspace_id=workspace_id,
            profile_id=profile_id,
            catalog_generation=generation,
            policy_sha256=policy_sha256,
            image_digest=image_digest,
            controller_epoch=controller_epoch,
            quota_generation=quota_generation,
            status=status,
            created_at_unix=created,
            last_active_at_unix=last_active,
            idle_expires_at_unix=idle_expires,
            max_expires_at_unix=max_expires,
            active_process_ids=process_ids,
        )

    def to_dict(self) -> dict[str, Any]:
        normalized = self.validated()
        value = asdict(normalized)
        value["active_process_ids"] = list(
            normalized.active_process_ids
        )
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LeaseSnapshot":
        expected = {
            "lease_id",
            "workspace_id",
            "profile_id",
            "catalog_generation",
            "policy_sha256",
            "image_digest",
            "controller_epoch",
            "quota_generation",
            "status",
            "created_at_unix",
            "last_active_at_unix",
            "idle_expires_at_unix",
            "max_expires_at_unix",
            "active_process_ids",
        }
        if set(value) != expected or not isinstance(
            value.get("active_process_ids"),
            list,
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 状态文件结构无效",
            )
        try:
            return cls(
                lease_id=str(value["lease_id"]),
                workspace_id=str(value["workspace_id"]),
                profile_id=str(value["profile_id"]),
                catalog_generation=str(value["catalog_generation"]),
                policy_sha256=str(value["policy_sha256"]),
                image_digest=str(value["image_digest"]),
                controller_epoch=str(value["controller_epoch"]),
                quota_generation=int(value["quota_generation"]),
                status=str(value["status"]),
                created_at_unix=float(value["created_at_unix"]),
                last_active_at_unix=float(value["last_active_at_unix"]),
                idle_expires_at_unix=float(value["idle_expires_at_unix"]),
                max_expires_at_unix=float(value["max_expires_at_unix"]),
                active_process_ids=tuple(
                    str(item) for item in value["active_process_ids"]
                ),
            ).validated()
        except SandboxServiceError:
            raise
        except (OverflowError, TypeError, ValueError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 状态文件结构无效",
            ) from exc


class LeaseStore:
    """以 root-only JSON 保存 controller epoch、幂等绑定和安全快照。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.leases_root = root / "leases"
        self.requests_root = root / "requests"
        self.controller_path = root / "controller.json"
        self.recovery_path = root / "startup-recovery.json"
        self._controller_epoch = ""
        self._guard = threading.RLock()

    def ensure(self) -> None:
        for path in (self.root, self.leases_root, self.requests_root):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)

    @staticmethod
    def _encoded(value: dict[str, Any]) -> bytes:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _MAX_STATE_BYTES:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 状态超过安全上限",
            )
        return encoded

    def _write(self, path: Path, value: dict[str, Any]) -> None:
        encoded = self._encoded(value)
        self.ensure()
        temp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
        file_fd = os.open(
            temp,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            try:
                view = memoryview(encoded)
                written = 0
                while written < len(view):
                    written += os.write(file_fd, view[written:])
                os.fsync(file_fd)
                os.fchmod(file_fd, 0o600)
            finally:
                os.close(file_fd)
            os.replace(temp, path)
        except BaseException:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            metadata = path.lstat()
            if (
                not path.is_file()
                or path.is_symlink()
                or metadata.st_size > _MAX_STATE_BYTES
            ):
                raise OSError("unsafe state file")
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 状态文件不可用",
            ) from exc
        if not isinstance(value, dict):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 状态文件结构无效",
            )
        return value

    def start_controller(self, *, now_unix: float) -> str:
        """每次 sandboxd 生命周期启动都生成并持久发布一个新 epoch。"""

        epoch = f"sbxctl_{secrets.token_hex(16)}"
        validate_controller_epoch(epoch)
        with self._guard:
            self._write(
                self.controller_path,
                {
                    "controller_epoch": epoch,
                    "started_at_unix": float(now_unix),
                },
            )
            self._controller_epoch = epoch
        return epoch

    @property
    def controller_epoch(self) -> str:
        with self._guard:
            if not self._controller_epoch:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "sandboxd controller epoch 尚未初始化",
                )
            return validate_controller_epoch(self._controller_epoch)

    def _lease_path(self, lease_id: str) -> Path:
        return self.leases_root / f"{validate_lease_id(lease_id)}.json"

    def save(self, snapshot: LeaseSnapshot) -> LeaseSnapshot:
        normalized = snapshot.validated()
        if normalized.controller_epoch != self.controller_epoch:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox Lease controller epoch 不属于当前实例",
            )
        with self._guard:
            self._write(self._lease_path(normalized.lease_id), normalized.to_dict())
        return normalized

    def get(self, lease_id: str) -> LeaseSnapshot | None:
        with self._guard:
            value = self._read(self._lease_path(lease_id))
        if value is None:
            return None
        return LeaseSnapshot.from_dict(value)

    def list(
        self,
        *,
        ignore_invalid: bool = False,
    ) -> list[LeaseSnapshot]:
        with self._guard:
            self.ensure()
            paths = sorted(self.leases_root.glob("sbxlease_*.json"))
            snapshots: list[LeaseSnapshot] = []
            for path in paths:
                try:
                    value = self._read(path)
                    if value is not None:
                        snapshots.append(LeaseSnapshot.from_dict(value))
                except SandboxServiceError:
                    if not ignore_invalid:
                        raise
            return snapshots

    def delete(self, lease_id: str) -> None:
        with self._guard:
            try:
                self._lease_path(lease_id).unlink(missing_ok=True)
            except OSError as exc:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Sandbox Lease 状态清理失败",
                ) from exc

    def touch(
        self,
        lease_id: str,
        *,
        now_unix: float,
        idle_ttl_seconds: int,
        active_process_ids: tuple[str, ...] | None = None,
    ) -> LeaseSnapshot:
        with self._guard:
            current = self.get(lease_id)
            if current is None:
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "Sandbox Lease 不存在",
                )
            processes = (
                current.active_process_ids
                if active_process_ids is None
                else active_process_ids
            )
            activity_at = max(
                current.last_active_at_unix,
                float(now_unix),
            )
            if activity_at >= current.max_expires_at_unix:
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "Sandbox Lease 已达到最大 TTL",
                )
            updated = replace(
                current,
                status="active" if processes else "idle",
                last_active_at_unix=activity_at,
                idle_expires_at_unix=min(
                    current.max_expires_at_unix,
                    activity_at
                    + max(1, int(idle_ttl_seconds)),
                ),
                active_process_ids=tuple(processes),
            )
            return self.save(updated)

    def bind_request(
        self,
        request_id: str,
        *,
        lease_id: str,
        fingerprint: str,
    ) -> None:
        request_id = validate_request_id(request_id)
        lease_id = validate_lease_id(lease_id)
        fingerprint = str(fingerprint or "").lower()
        if not _SHA256_RE.fullmatch(fingerprint):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox Lease 请求指纹无效",
            )
        path = self.requests_root / (
            hashlib.sha256(request_id.encode("utf-8")).hexdigest() + ".json"
        )
        expected = {
            "request_id": request_id,
            "lease_id": lease_id,
            "fingerprint": fingerprint,
        }
        with self._guard:
            existing = self._read(path)
            if existing is not None:
                if existing != expected:
                    raise SandboxServiceError(
                        SandboxErrorCode.AUTHORIZATION_FAILED,
                        "幂等 request_id 已绑定其他 Lease 请求",
                    )
                return
            self._write(path, expected)

    def record_startup_recovery(
        self,
        *,
        recovered_lease_ids: list[str],
        recovered_process_ids: list[str],
    ) -> None:
        value = {
            "controller_epoch": self.controller_epoch,
            "recovered_lease_ids": sorted(
                {validate_lease_id(item) for item in recovered_lease_ids}
            ),
            "recovered_process_ids": sorted(
                {validate_process_id(item) for item in recovered_process_ids}
            ),
        }
        with self._guard:
            self._write(self.recovery_path, value)

    def startup_recovery(self) -> dict[str, Any]:
        with self._guard:
            value = self._read(self.recovery_path)
        if value is None:
            return {
                "controller_epoch": self.controller_epoch,
                "recovered_lease_ids": [],
                "recovered_process_ids": [],
            }
        if (
            set(value)
            != {
                "controller_epoch",
                "recovered_lease_ids",
                "recovered_process_ids",
            }
            or str(value.get("controller_epoch")) != self.controller_epoch
            or not isinstance(value.get("recovered_lease_ids"), list)
            or not isinstance(value.get("recovered_process_ids"), list)
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "sandboxd 启动恢复记录无效",
            )
        return {
            "controller_epoch": self.controller_epoch,
            "recovered_lease_ids": [
                validate_lease_id(str(item))
                for item in value["recovered_lease_ids"]
            ],
            "recovered_process_ids": [
                validate_process_id(str(item))
                for item in value["recovered_process_ids"]
            ],
        }


__all__ = [
    "LeaseSnapshot",
    "LeaseStore",
    "validate_controller_epoch",
    "validate_lease_id",
    "validate_process_id",
    "validate_request_id",
]
