"""会话型 Sandbox Lease 的 Docker 控制面。"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.paths import validate_workspace_id
from sandboxd.config import SandboxdConfig
from sandboxd.container_security import (
    ContainerMountPaths,
    build_container_kwargs,
)
from sandboxd.docker_backend import (
    MANAGED_BY_LABEL,
    MANAGED_LABEL,
    WORKSPACE_LABEL,
    managed_container,
)
from sandboxd.environment_manager import EnvironmentManager
from sandboxd.filesystem import AssetFileService, WorkspaceFileService
from sandboxd.lease_store import (
    LeaseSnapshot,
    LeaseStore,
    validate_controller_epoch,
    validate_lease_id,
    validate_process_id,
    validate_request_id,
)
from sandboxd.network_policy import (
    LeaseNetworkAttachment,
    NetworkPolicyManager,
)


LEASE_CONTAINER_PREFIX = "nanobot-sbx-lease-"
LEASE_LABEL = "com.nanobot.lease-id"
PROFILE_LABEL = "com.nanobot.profile-id"
LEASE_GENERATION_LABEL = "com.nanobot.lease-generation"
CONTROLLER_EPOCH_LABEL = "com.nanobot.controller-epoch"
POLICY_SHA_LABEL = "com.nanobot.policy-sha256"
CREATED_AT_LABEL = "com.nanobot.created-at-unix"
IDLE_EXPIRES_AT_LABEL = "com.nanobot.idle-expires-at-unix"
MAX_EXPIRES_AT_LABEL = "com.nanobot.max-expires-at-unix"
QUOTA_GENERATION_LABEL = "com.nanobot.quota-generation"


logger = logging.getLogger("nanobot.sandboxd.lease_backend")

# Docker 的 init=True 会把 tini 放在容器 PID 1；固定 keepalive 只是让
# Lease 容器保持存活，不接受任何调用方命令。
LEASE_KEEPALIVE_COMMAND = (
    "exec /bin/sh -c 'trap \"exit 0\" TERM INT; "
    "while :; do sleep 3600 & wait $!; done'"
)


def _labels(container: Any) -> dict[str, str]:
    try:
        container.reload()
        raw = container.attrs.get("Config", {}).get("Labels") or {}
        return {str(key): str(value) for key, value in dict(raw).items()}
    except Exception:
        return {}


def managed_lease_container(
    container: Any,
    *,
    expected_lease_id: str | None = None,
) -> bool:
    """名称、通用所有权标签和 Lease 标签必须同时成立。"""

    lease_id = owned_lease_container_id(
        container,
        expected_lease_id=expected_lease_id,
    )
    if lease_id is None:
        return False
    labels = _labels(container)
    return bool(
        labels.get(WORKSPACE_LABEL)
        and labels.get(PROFILE_LABEL)
        and labels.get(LEASE_GENERATION_LABEL)
        and labels.get(CONTROLLER_EPOCH_LABEL)
        and labels.get(POLICY_SHA_LABEL)
    )


def owned_lease_container_id(
    container: Any,
    *,
    expected_lease_id: str | None = None,
) -> str | None:
    """只用不可歧义的名称和所有权标签识别 Nanobot Lease。"""

    if not managed_container(container):
        return None
    labels = _labels(container)
    lease_id = str(labels.get(LEASE_LABEL) or "")
    try:
        lease_id = validate_lease_id(lease_id)
    except SandboxServiceError:
        return None
    name = str(getattr(container, "name", "") or "").lstrip("/")
    if name != f"{LEASE_CONTAINER_PREFIX}{lease_id}":
        return None
    if expected_lease_id is not None and lease_id != expected_lease_id:
        return None
    return lease_id


class LeaseBackend:
    """只接受服务端派生的 Lease 标识和 canonical Profile 策略。"""

    def __init__(
        self,
        config: SandboxdConfig,
        *,
        docker_client: Any,
        workspace_files: WorkspaceFileService,
        asset_files: AssetFileService,
        lease_store: LeaseStore,
        profile_image_resolver: Callable[[str], Any],
        network_policy: NetworkPolicyManager | None = None,
        environment_manager: EnvironmentManager | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config.validated()
        self.client = docker_client
        self.workspace_files = workspace_files
        self.asset_files = asset_files
        self.store = lease_store
        self.profile_image_resolver = profile_image_resolver
        self.network_policy = network_policy or NetworkPolicyManager(
            self.config,
            docker_client=self.client,
        )
        self.clock = clock
        self.environment_manager = environment_manager or EnvironmentManager(
            self.config,
            workspace_files=self.workspace_files,
            clock=self.clock,
        )
        self._lease_locks: dict[str, threading.Lock] = {}
        self._lease_locks_guard = threading.Lock()
        self._admin_lease_locks: dict[str, threading.Lock] = {}
        self._admin_lease_locks_guard = threading.Lock()
        self._recycle_observers: list[
            Callable[[str, str, tuple[str, ...]], None]
        ] = []
        self._recycle_observers_guard = threading.Lock()

    def _lease_lock(self, lease_id: str) -> threading.Lock:
        with self._lease_locks_guard:
            return self._lease_locks.setdefault(lease_id, threading.Lock())

    def _admin_lease_lock(self, lease_id: str) -> threading.Lock:
        """串行化同一 Lease 的管理员操作，不改变 Workspace/Lease 锁顺序。"""

        with self._admin_lease_locks_guard:
            return self._admin_lease_locks.setdefault(
                lease_id,
                threading.Lock(),
            )

    def add_recycle_observer(
        self,
        observer: Callable[[str, str, tuple[str, ...]], None],
    ) -> None:
        """注册 Lease 回收观察者；观察者只能收敛内存进程事实。"""

        if not callable(observer):
            raise TypeError("Sandbox Lease 回收观察者必须可调用")
        with self._recycle_observers_guard:
            if observer not in self._recycle_observers:
                self._recycle_observers.append(observer)

    def _notify_recycled(
        self,
        lease_id: str,
        reason: str,
        affected_process_ids: tuple[str, ...],
    ) -> None:
        with self._recycle_observers_guard:
            observers = tuple(self._recycle_observers)
        for observer in observers:
            try:
                observer(lease_id, reason, affected_process_ids)
            except Exception:
                logger.error(
                    "Sandbox Lease 回收观察者异常 lease_id=%s",
                    lease_id,
                    exc_info=True,
                )

    @staticmethod
    def _fingerprint(
        *,
        lease_id: str,
        workspace_id: str,
        profile_id: str,
        catalog_generation: str,
        policy_sha256: str,
        quota_generation: int,
    ) -> str:
        encoded = json.dumps(
            {
                "lease_id": lease_id,
                "workspace_id": workspace_id,
                "profile_id": profile_id,
                "catalog_generation": catalog_generation,
                "policy_sha256": policy_sha256,
                "quota_generation": int(quota_generation),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _container_name(self, lease_id: str) -> str:
        return f"{LEASE_CONTAINER_PREFIX}{validate_lease_id(lease_id)}"

    def _find_container(self, lease_id: str) -> Any | None:
        lease_id = validate_lease_id(lease_id)
        try:
            candidates = self.client.containers.list(
                all=True,
                filters={
                    "name": self._container_name(lease_id),
                    "label": [
                        f"{MANAGED_LABEL}=true",
                        f"{MANAGED_BY_LABEL}=sandboxd",
                        f"{LEASE_LABEL}={lease_id}",
                    ],
                },
            )
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 无法查询 Sandbox Lease",
                retryable=True,
                stop=False,
            ) from exc
        owned = [
            container
            for container in candidates
            if owned_lease_container_id(
                container,
                expected_lease_id=lease_id,
            )
            is not None
        ]
        if len(owned) > 1:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 中存在重复 Sandbox Lease",
            )
        return owned[0] if owned else None

    @staticmethod
    def _running(container: Any) -> bool:
        try:
            container.reload()
            return bool(container.attrs.get("State", {}).get("Running"))
        except Exception:
            return False

    def _require_requested_policy(
        self,
        *,
        profile_id: str,
        catalog_generation: str,
        policy_sha256: str,
    ) -> Any:
        catalog = self.config.profile_catalog
        if catalog is None:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Profile catalog 不可用",
            )
        if (
            str(catalog_generation or "") != catalog.catalog_generation
            or str(policy_sha256 or "").lower() != catalog.policy_sha256
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox Lease 策略与当前 canonical catalog 不一致",
            )
        try:
            profile = catalog.profile(str(profile_id or ""))
        except ValueError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox Lease Profile 无效",
            ) from exc
        if not profile.grantable or profile.execution_mode != "lease":
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "当前 Sandbox Profile 不允许创建 Lease",
            )
        return profile

    def _container_kwargs(
        self,
        *,
        lease_id: str,
        workspace_id: str,
        profile: Any,
        image: Any,
        inputs_path: Path,
        created_at_unix: float,
        idle_expires_at_unix: float,
        max_expires_at_unix: float,
        quota_generation: int,
        network_attachment: LeaseNetworkAttachment | None,
    ) -> dict[str, Any]:
        catalog = self.config.profile_catalog
        if catalog is None:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Profile catalog 不可用",
            )
        labels = {
            MANAGED_LABEL: "true",
            MANAGED_BY_LABEL: "sandboxd",
            LEASE_LABEL: lease_id,
            WORKSPACE_LABEL: workspace_id,
            PROFILE_LABEL: profile.profile_id,
            LEASE_GENERATION_LABEL: catalog.catalog_generation,
            CONTROLLER_EPOCH_LABEL: self.store.controller_epoch,
            POLICY_SHA_LABEL: catalog.policy_sha256,
            CREATED_AT_LABEL: f"{created_at_unix:.6f}",
            IDLE_EXPIRES_AT_LABEL: f"{idle_expires_at_unix:.6f}",
            MAX_EXPIRES_AT_LABEL: f"{max_expires_at_unix:.6f}",
            QUOTA_GENERATION_LABEL: str(int(quota_generation)),
        }
        return build_container_kwargs(
            self.config,
            profile,
            lifecycle="lease",
            image_id=str(image.id),
            name=self._container_name(lease_id),
            command=LEASE_KEEPALIVE_COMMAND,
            working_dir="/workspace",
            labels=labels,
            mounts=ContainerMountPaths(
                workspace=self.workspace_files.layout.workspace_data_dir(
                    workspace_id
                ),
                inputs=inputs_path,
                runtime=self.workspace_files.layout.ensure_runtime(workspace_id),
            ),
            network_attachment=network_attachment,
        )

    @staticmethod
    def _safe_remove(container: Any) -> bool:
        if owned_lease_container_id(container) is None:
            return False
        try:
            container.reload()
            if bool(container.attrs.get("State", {}).get("Running")):
                try:
                    container.stop(timeout=2)
                except Exception:
                    container.kill()
            container.remove(force=True, v=True)
            return True
        except Exception:
            return False

    def _fact(
        self,
        snapshot: LeaseSnapshot,
        *,
        present: bool,
        running: bool,
        container: Any | None = None,
        environment: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = "active" if snapshot.active_process_ids else "idle"
        if not present:
            status = "missing"
        elif not running:
            status = "stopped"
        network_ready = False
        if present and running and container is not None:
            try:
                self.network_policy.require_lease_topology(
                    self.config.profile(snapshot.profile_id),
                    lease_id=snapshot.lease_id,
                    controller_epoch=snapshot.controller_epoch,
                    sandbox_container=container,
                )
            except SandboxServiceError:
                network_ready = False
            else:
                network_ready = True
        fact = {
            "lease_id": snapshot.lease_id,
            "workspace_id": snapshot.workspace_id,
            "profile_id": snapshot.profile_id,
            "catalog_generation": snapshot.catalog_generation,
            "policy_sha256": snapshot.policy_sha256,
            "image_digest": snapshot.image_digest,
            "controller_epoch": snapshot.controller_epoch,
            "quota_generation": snapshot.quota_generation,
            "status": status,
            "present": bool(present),
            "running": bool(running),
            "network_ready": network_ready,
            "created_at_unix": snapshot.created_at_unix,
            "last_active_at_unix": snapshot.last_active_at_unix,
            "idle_expires_at_unix": snapshot.idle_expires_at_unix,
            "max_expires_at_unix": snapshot.max_expires_at_unix,
            "active_process_ids": list(snapshot.active_process_ids),
        }
        if environment is not None:
            fact["environment"] = dict(environment)
        return fact

    def _snapshot_from_container(self, container: Any) -> LeaseSnapshot:
        labels = _labels(container)
        try:
            profile = self.config.profile(str(labels.get(PROFILE_LABEL) or ""))
            created = float(labels.get(CREATED_AT_LABEL) or 0)
            idle_expires = float(labels.get(IDLE_EXPIRES_AT_LABEL) or 0)
            max_expires = float(labels.get(MAX_EXPIRES_AT_LABEL) or 0)
            quota_generation = int(
                labels.get(QUOTA_GENERATION_LABEL) or 0
            )
        except (TypeError, ValueError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease Docker 标签无效",
            ) from exc
        image_digest = str(
            container.attrs.get("Image")
            or container.attrs.get("Config", {}).get("Image")
            or ""
        ).lower()
        return LeaseSnapshot(
            lease_id=str(labels.get(LEASE_LABEL) or ""),
            workspace_id=str(labels.get(WORKSPACE_LABEL) or ""),
            profile_id=profile.profile_id,
            catalog_generation=str(
                labels.get(LEASE_GENERATION_LABEL) or ""
            ),
            policy_sha256=str(labels.get(POLICY_SHA_LABEL) or ""),
            image_digest=image_digest,
            controller_epoch=str(labels.get(CONTROLLER_EPOCH_LABEL) or ""),
            quota_generation=quota_generation,
            status="idle",
            created_at_unix=created,
            last_active_at_unix=created,
            idle_expires_at_unix=idle_expires,
            max_expires_at_unix=max_expires,
            active_process_ids=(),
        ).validated()

    def _invalid_fact(
        self,
        container: Any,
        *,
        lease_id: str,
    ) -> dict[str, Any]:
        labels = _labels(container)
        workspace_id = ""
        controller_epoch = ""
        try:
            workspace_id = validate_workspace_id(
                str(labels.get(WORKSPACE_LABEL) or "")
            )
        except SandboxServiceError:
            pass
        try:
            controller_epoch = validate_controller_epoch(
                str(labels.get(CONTROLLER_EPOCH_LABEL) or "")
            )
        except SandboxServiceError:
            pass
        return {
            "lease_id": lease_id,
            "workspace_id": workspace_id,
            "profile_id": "",
            "catalog_generation": "",
            "policy_sha256": "",
            "image_digest": "",
            "controller_epoch": controller_epoch,
            "quota_generation": 0,
            "status": "invalid",
            "present": True,
            "running": self._running(container),
            "network_ready": False,
            "created_at_unix": 0,
            "last_active_at_unix": 0,
            "idle_expires_at_unix": 0,
            "max_expires_at_unix": 0,
            "active_process_ids": [],
        }

    def ensure(
        self,
        *,
        request_id: str,
        lease_id: str,
        workspace_id: str,
        profile_id: str,
        catalog_generation: str,
        policy_sha256: str,
        quota_generation: int = 1,
    ) -> dict[str, Any]:
        workspace_id = validate_workspace_id(workspace_id)
        try:
            normalized_quota_generation = int(quota_generation)
        except (TypeError, ValueError):
            normalized_quota_generation = 0
        if isinstance(quota_generation, bool) or not (
            1 <= normalized_quota_generation <= 2_147_483_647
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox Lease quota generation 无效",
            )
        with self.workspace_files.maintenance.execution(
            workspace_id,
            quota_generation=normalized_quota_generation,
        ):
            return self._ensure_under_workspace_gate(
                request_id=request_id,
                lease_id=lease_id,
                workspace_id=workspace_id,
                profile_id=profile_id,
                catalog_generation=catalog_generation,
                policy_sha256=policy_sha256,
                quota_generation=normalized_quota_generation,
            )

    def _ensure_under_workspace_gate(
        self,
        *,
        request_id: str,
        lease_id: str,
        workspace_id: str,
        profile_id: str,
        catalog_generation: str,
        policy_sha256: str,
        quota_generation: int,
    ) -> dict[str, Any]:
        request_id = validate_request_id(request_id)
        lease_id = validate_lease_id(lease_id)
        workspace_id = validate_workspace_id(workspace_id)
        profile = self._require_requested_policy(
            profile_id=profile_id,
            catalog_generation=catalog_generation,
            policy_sha256=policy_sha256,
        )
        fingerprint = self._fingerprint(
            lease_id=lease_id,
            workspace_id=workspace_id,
            profile_id=profile.profile_id,
            catalog_generation=str(catalog_generation),
            policy_sha256=str(policy_sha256).lower(),
            quota_generation=quota_generation,
        )
        self.store.bind_request(
            request_id,
            lease_id=lease_id,
            fingerprint=fingerprint,
        )

        with self._lease_lock(lease_id):
            existing = self._find_container(lease_id)
            if existing is not None:
                labels = _labels(existing)
                expected = {
                    WORKSPACE_LABEL: workspace_id,
                    PROFILE_LABEL: profile.profile_id,
                    LEASE_GENERATION_LABEL: str(catalog_generation),
                    POLICY_SHA_LABEL: str(policy_sha256).lower(),
                    CONTROLLER_EPOCH_LABEL: self.store.controller_epoch,
                    QUOTA_GENERATION_LABEL: str(quota_generation),
                }
                if any(labels.get(key) != value for key, value in expected.items()):
                    raise SandboxServiceError(
                        SandboxErrorCode.AUTHORIZATION_FAILED,
                        "同名 Sandbox Lease 与请求归属或策略不一致",
                    )
                if self._running(existing):
                    snapshot = self.store.get(lease_id)
                    if snapshot is None:
                        snapshot = self.store.save(
                            self._snapshot_from_container(existing)
                        )
                    try:
                        self.network_policy.require_lease_topology(
                            profile,
                            lease_id=lease_id,
                            controller_epoch=self.store.controller_epoch,
                            sandbox_container=existing,
                        )
                    except SandboxServiceError:
                        if not self._safe_remove(existing):
                            raise SandboxServiceError(
                                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                                "漂移的 Sandbox Lease 无法安全回收",
                            )
                        self.network_policy.cleanup(lease_id)
                        self.store.delete(lease_id)
                    else:
                        try:
                            environment = self.environment_manager.prepare(
                                container=existing,
                                workspace_id=workspace_id,
                                profile_id=profile.profile_id,
                                catalog_generation=str(catalog_generation),
                                policy_sha256=str(policy_sha256).lower(),
                                image_digest=snapshot.image_digest,
                            )
                        except Exception:
                            affected_process_ids = (
                                snapshot.active_process_ids
                            )
                            if not self._safe_remove(existing):
                                raise SandboxServiceError(
                                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                                    "环境漂移的 Sandbox Lease 无法安全回收",
                                )
                            self.network_policy.cleanup(lease_id)
                            self.store.delete(lease_id)
                            self._notify_recycled(
                                lease_id,
                                "environment_prepare_failed",
                                affected_process_ids,
                            )
                            raise
                        return self._fact(
                            snapshot,
                            present=True,
                            running=True,
                            container=existing,
                            environment=environment,
                        )
                elif not self._safe_remove(existing):
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "停止态 Sandbox Lease 无法安全回收",
                    )
                else:
                    self.network_policy.cleanup(lease_id)

            self.workspace_files.ensure_workspace(workspace_id)
            self.workspace_files.disk_guard.ensure_available()
            inputs_path = self.asset_files.lease_stage_path(
                workspace_id,
                lease_id,
            )
            image = self.profile_image_resolver(profile.profile_id)
            now_unix = float(self.clock())
            idle_expires = now_unix + int(profile.idle_ttl_seconds)
            max_expires = now_unix + int(profile.max_ttl_seconds)
            network_attachment = self.network_policy.prepare(
                profile,
                lease_id=lease_id,
                controller_epoch=self.store.controller_epoch,
            )
            kwargs = self._container_kwargs(
                lease_id=lease_id,
                workspace_id=workspace_id,
                profile=profile,
                image=image,
                inputs_path=inputs_path,
                created_at_unix=now_unix,
                idle_expires_at_unix=idle_expires,
                max_expires_at_unix=max_expires,
                quota_generation=quota_generation,
                network_attachment=network_attachment,
            )
            container = None
            try:
                container = self.client.containers.create(**kwargs)
                if not managed_lease_container(
                    container,
                    expected_lease_id=lease_id,
                ):
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "Docker Lease 容器标签校验失败",
                    )
                container.start()
                if not self._running(container):
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "Docker Lease 容器未进入运行态",
                    )
                self.network_policy.require_lease_topology(
                    profile,
                    lease_id=lease_id,
                    controller_epoch=self.store.controller_epoch,
                    sandbox_container=container,
                )
                environment = self.environment_manager.prepare(
                    container=container,
                    workspace_id=workspace_id,
                    profile_id=profile.profile_id,
                    catalog_generation=str(catalog_generation),
                    policy_sha256=str(policy_sha256).lower(),
                    image_digest=str(image.id).lower(),
                )
                snapshot = self.store.save(LeaseSnapshot(
                    lease_id=lease_id,
                    workspace_id=workspace_id,
                    profile_id=profile.profile_id,
                    catalog_generation=str(catalog_generation),
                    policy_sha256=str(policy_sha256).lower(),
                    image_digest=str(image.id).lower(),
                    controller_epoch=self.store.controller_epoch,
                    quota_generation=quota_generation,
                    status="idle",
                    created_at_unix=now_unix,
                    last_active_at_unix=now_unix,
                    idle_expires_at_unix=idle_expires,
                    max_expires_at_unix=max_expires,
                    active_process_ids=(),
                ))
                return self._fact(
                    snapshot,
                    present=True,
                    running=True,
                    container=container,
                    environment=environment,
                )
            except SandboxServiceError:
                if container is not None:
                    self._safe_remove(container)
                if network_attachment is not None:
                    self.network_policy.cleanup(lease_id)
                raise
            except Exception as exc:
                if container is not None:
                    self._safe_remove(container)
                if network_attachment is not None:
                    self.network_policy.cleanup(lease_id)
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Docker 无法创建或启动 Sandbox Lease",
                    retryable=True,
                    stop=False,
                ) from exc

    def get(self, lease_id: str) -> dict[str, Any]:
        lease_id = validate_lease_id(lease_id)
        snapshot = self.store.get(lease_id)
        container = self._find_container(lease_id)
        if snapshot is None and container is not None:
            snapshot = self._snapshot_from_container(container)
            if snapshot.controller_epoch == self.store.controller_epoch:
                snapshot = self.store.save(snapshot)
        if snapshot is None:
            return {
                "lease_id": lease_id,
                "status": "missing",
                "present": False,
                "running": False,
                "active_process_ids": [],
            }
        return self._fact(
            snapshot,
            present=container is not None,
            running=container is not None and self._running(container),
            container=container,
        )

    def list(
        self,
        *,
        include_cached: bool = True,
    ) -> list[dict[str, Any]]:
        try:
            candidates = self.client.containers.list(
                all=True,
                filters={
                    "name": LEASE_CONTAINER_PREFIX,
                    "label": [
                        f"{MANAGED_LABEL}=true",
                        f"{MANAGED_BY_LABEL}=sandboxd",
                    ],
                },
            )
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 无法列出 Sandbox Lease",
                retryable=True,
                stop=False,
            ) from exc
        facts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for container in candidates:
            lease_id = owned_lease_container_id(container)
            if lease_id is None:
                continue
            seen.add(lease_id)
            try:
                snapshot = self._snapshot_from_container(container)
            except Exception:
                facts.append(self._invalid_fact(
                    container,
                    lease_id=lease_id,
                ))
                continue
            if (
                include_cached
                and snapshot.controller_epoch == self.store.controller_epoch
            ):
                try:
                    cached = self.store.get(snapshot.lease_id)
                except SandboxServiceError:
                    cached = None
                if cached is not None:
                    snapshot = cached
            facts.append(self._fact(
                snapshot,
                present=True,
                running=self._running(container),
                container=container,
            ))
        if include_cached:
            for snapshot in self.store.list(ignore_invalid=True):
                if snapshot.lease_id not in seen:
                    facts.append(
                        self._fact(
                            snapshot,
                            present=False,
                            running=False,
                            container=None,
                        )
                    )
        return sorted(facts, key=lambda item: str(item["lease_id"]))

    @contextmanager
    def starting_process(
        self,
        lease_id: str,
        process_id: str,
    ) -> Iterator[tuple[LeaseSnapshot, Any, Any]]:
        """在同一 Lease 锁内校验容器并登记新的活动进程。"""

        lease_id = validate_lease_id(lease_id)
        process_id = validate_process_id(process_id)
        with self._lease_lock(lease_id):
            snapshot = self.store.get(lease_id)
            container = self._find_container(lease_id)
            if (
                snapshot is None
                or snapshot.controller_epoch != self.store.controller_epoch
                or container is None
                or not self._running(container)
                or process_id in snapshot.active_process_ids
                or float(self.clock()) >= snapshot.max_expires_at_unix
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "Sandbox Lease 已失效或不能启动新进程",
                )
            labels = _labels(container)
            expected = {
                LEASE_LABEL: snapshot.lease_id,
                WORKSPACE_LABEL: snapshot.workspace_id,
                PROFILE_LABEL: snapshot.profile_id,
                LEASE_GENERATION_LABEL: snapshot.catalog_generation,
                POLICY_SHA_LABEL: snapshot.policy_sha256,
                CONTROLLER_EPOCH_LABEL: snapshot.controller_epoch,
                QUOTA_GENERATION_LABEL: str(snapshot.quota_generation),
            }
            if any(
                labels.get(key) != value
                for key, value in expected.items()
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "Sandbox Lease 容器归属或策略已失效",
                )
            profile = self.config.profile(snapshot.profile_id)
            self.network_policy.require_lease_topology(
                profile,
                lease_id=lease_id,
                controller_epoch=snapshot.controller_epoch,
                sandbox_container=container,
            )
            yield snapshot, container, profile
            self.store.touch(
                lease_id,
                now_unix=float(self.clock()),
                idle_ttl_seconds=int(profile.idle_ttl_seconds),
                active_process_ids=tuple(sorted({
                    *snapshot.active_process_ids,
                    process_id,
                })),
            )

    def finish_process(
        self,
        lease_id: str,
        process_id: str,
    ) -> LeaseSnapshot | None:
        """普通 Exec 退出后移除活动句柄；Lease 已回收时保持幂等。"""

        lease_id = validate_lease_id(lease_id)
        process_id = validate_process_id(process_id)
        with self._lease_lock(lease_id):
            snapshot = self.store.get(lease_id)
            if (
                snapshot is None
                or snapshot.controller_epoch != self.store.controller_epoch
            ):
                return None
            profile = self.config.profile(snapshot.profile_id)
            return self.store.touch(
                lease_id,
                now_unix=float(self.clock()),
                idle_ttl_seconds=int(profile.idle_ttl_seconds),
                active_process_ids=tuple(
                    item
                    for item in snapshot.active_process_ids
                    if item != process_id
                ),
            )

    def process_lease_health(self, lease_id: str) -> dict[str, bool]:
        """返回 Exec 退出时需要的最小 Lease 健康事实。"""

        lease_id = validate_lease_id(lease_id)
        with self._lease_lock(lease_id):
            container = self._find_container(lease_id)
            if container is None:
                return {
                    "present": False,
                    "running": False,
                    "oom_killed": False,
                }
            try:
                container.reload()
                state = dict(container.attrs.get("State") or {})
            except Exception:
                state = {}
            return {
                "present": True,
                "running": bool(state.get("Running")),
                "oom_killed": bool(state.get("OOMKilled")),
            }

    def recycle(
        self,
        lease_id: str,
        *,
        reason: str,
        cleanup_inputs: bool = True,
    ) -> dict[str, Any]:
        lease_id = validate_lease_id(lease_id)
        normalized_reason = str(reason or "")[:64]
        with self._lease_lock(lease_id):
            container = self._find_container(lease_id)
            try:
                snapshot = self.store.get(lease_id)
            except SandboxServiceError:
                snapshot = None
            if snapshot is None and container is not None:
                try:
                    snapshot = self._snapshot_from_container(container)
                except SandboxServiceError:
                    snapshot = None
            workspace_id = (
                snapshot.workspace_id
                if snapshot is not None
                else ""
            )
            if not workspace_id and container is not None:
                try:
                    workspace_id = validate_workspace_id(
                        str(
                            _labels(container).get(WORKSPACE_LABEL)
                            or ""
                        )
                    )
                except SandboxServiceError:
                    workspace_id = ""
            removed = False
            if container is not None:
                removed = self._safe_remove(container)
                if not removed:
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "Sandbox Lease 无法安全回收",
                        retryable=True,
                        stop=False,
                    )
            network_removed = self.network_policy.cleanup(lease_id)
            affected = (
                list(snapshot.active_process_ids)
                if snapshot is not None
                else []
            )
            self.store.delete(lease_id)
            if workspace_id and cleanup_inputs:
                try:
                    self.asset_files.cleanup_lease_stage(
                        workspace_id,
                        lease_id,
                    )
                except (OSError, SandboxServiceError):
                    pass
            result = {
                "lease_id": lease_id,
                "termination_scope": "lease",
                "lease_recycled": bool(
                    removed or network_removed or snapshot is not None
                ),
                "termination_reason": normalized_reason,
                "affected_process_ids": affected,
                "workspace_preserved": True,
                "runtime_preserved": True,
            }
        if result["lease_recycled"]:
            self._notify_recycled(
                lease_id,
                normalized_reason,
                tuple(affected),
            )
        return result

    def recreate(
        self,
        lease_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        """按已持久化策略重建同一个 Lease，并保留 Workspace/Runtime。"""

        lease_id = validate_lease_id(lease_id)
        request_id = validate_request_id(request_id)
        with self._admin_lease_lock(lease_id):
            with self._lease_lock(lease_id):
                container = self._find_container(lease_id)
                try:
                    snapshot = self.store.get(lease_id)
                except SandboxServiceError:
                    snapshot = None
                if snapshot is None and container is not None:
                    snapshot = self._snapshot_from_container(container)
                if snapshot is None:
                    raise SandboxServiceError(
                        SandboxErrorCode.AUTHORIZATION_FAILED,
                        "Sandbox Lease 不存在，无法重建",
                    )
                frozen = snapshot.validated()

            recycled = self.recycle(
                lease_id,
                reason="admin_lease_recreate",
                cleanup_inputs=False,
            )
            fact = self.ensure(
                request_id=request_id,
                lease_id=frozen.lease_id,
                workspace_id=frozen.workspace_id,
                profile_id=frozen.profile_id,
                catalog_generation=frozen.catalog_generation,
                policy_sha256=frozen.policy_sha256,
                quota_generation=frozen.quota_generation,
            )
            return {
                **fact,
                "termination_scope": "lease",
                "lease_recycled": bool(recycled["lease_recycled"]),
                "termination_reason": "admin_lease_recreate",
                "affected_process_ids": list(
                    recycled["affected_process_ids"]
                ),
                "workspace_preserved": True,
                "runtime_preserved": True,
            }

    def admin_recycle(
        self,
        lease_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """与管理员重建互斥地停止或销毁同一 Lease。"""

        lease_id = validate_lease_id(lease_id)
        if reason not in {"admin_lease_stop", "admin_lease_destroy"}:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox Lease 管理回收原因无效",
            )
        with self._admin_lease_lock(lease_id):
            return self.recycle(lease_id, reason=reason)

    def terminate_all(self, *, reason: str) -> dict[str, Any]:
        facts = self.list(include_cached=False)
        terminated: list[str] = []
        affected_process_ids: list[str] = []
        failed: list[str] = []
        for fact in facts:
            lease_id = str(fact.get("lease_id") or "")
            if not fact.get("present") and self.store.get(lease_id) is None:
                continue
            try:
                result = self.recycle(lease_id, reason=reason)
            except SandboxServiceError:
                failed.append(lease_id)
                continue
            if result["lease_recycled"]:
                terminated.append(lease_id)
            affected_process_ids.extend(result["affected_process_ids"])
        return {
            "controller_epoch": self.store.controller_epoch,
            "terminated_lease_ids": sorted(set(terminated)),
            "affected_process_ids": sorted(set(affected_process_ids)),
            "failed_lease_ids": sorted(set(failed)),
        }

    def terminate_workspace(
        self,
        workspace_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """仅回收精确 Workspace 下的 Nanobot Lease。"""

        workspace_id = validate_workspace_id(workspace_id)
        facts = [
            fact
            for fact in self.list()
            if str(fact.get("workspace_id") or "") == workspace_id
        ]
        terminated: list[str] = []
        affected_process_ids: list[str] = []
        failed: list[str] = []
        for fact in facts:
            lease_id = str(fact.get("lease_id") or "")
            try:
                result = self.recycle(
                    lease_id,
                    reason=reason,
                )
            except SandboxServiceError:
                failed.append(lease_id)
                continue
            if result["lease_recycled"]:
                terminated.append(lease_id)
            affected_process_ids.extend(result["affected_process_ids"])
        if failed:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "目标 Workspace 的 Sandbox Lease 未能全部停止",
                retryable=True,
                stop=False,
            )
        return {
            "workspace_id": workspace_id,
            "terminated_lease_ids": sorted(set(terminated)),
            "affected_process_ids": sorted(set(affected_process_ids)),
            "failed_lease_ids": [],
        }

    def cleanup_orphan_networks(self) -> list[str]:
        """按当前真实 Lease 容器集合清理孤儿代理和内部网络。"""

        active = {
            str(fact.get("lease_id") or "")
            for fact in self.list(include_cached=False)
            if fact.get("present")
        }
        return self.network_policy.cleanup_orphans(active)


__all__ = [
    "CONTROLLER_EPOCH_LABEL",
    "LEASE_CONTAINER_PREFIX",
    "LEASE_GENERATION_LABEL",
    "LEASE_LABEL",
    "LeaseBackend",
    "MAX_EXPIRES_AT_LABEL",
    "PROFILE_LABEL",
    "QUOTA_GENERATION_LABEL",
    "managed_lease_container",
    "owned_lease_container_id",
]
