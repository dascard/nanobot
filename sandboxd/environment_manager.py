"""按 Workspace 管理 Sandbox 环境准备指纹与可重建缓存。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.environment_service import (
    ENVIRONMENT_FINGERPRINT_FIELDS,
    ENVIRONMENT_FINGERPRINT_NAME,
    EnvironmentTemplate,
    SandboxEnvironmentService,
)
from core.sandbox.paths import (
    SafeWorkspaceFilesystem,
    validate_workspace_id,
)
from sandboxd.config import SandboxdConfig
from sandboxd.filesystem import WorkspaceFileService


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IMAGE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_CATALOG_GENERATION_RE = re.compile(
    r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}"
)

_FINGERPRINT_MAX_BYTES = 64 * 1024
_LOCKFILE_MAX_BYTES = 16 * 1024 * 1024
_MAX_CLEANUP_ENTRIES = 100_000
_MAX_CLEANUP_DEPTH = 64
_RUNTIME_DIRECTORIES = (
    "cache",
    "home",
    "npm-cache",
    "pip-cache",
    "pycache",
    "uv-cache",
    "venvs",
)
_CLEANABLE_RUNTIME_DIRECTORIES = (
    "cache",
    "npm-cache",
    "pip-cache",
    "pycache",
    "uv-cache",
    "venvs",
)


def _runtime_unavailable(summary: str) -> SandboxServiceError:
    return SandboxServiceError(
        SandboxErrorCode.RUNTIME_UNAVAILABLE,
        summary,
        retryable=True,
        stop=False,
    )


def _unique_json_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON 字段重复")
        value[key] = item
    return value


def _timestamp(clock: Callable[[], float]) -> str:
    try:
        value = float(clock())
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    except (OSError, OverflowError, TypeError, ValueError) as exc:
        raise _runtime_unavailable("Sandbox 环境时钟不可用") from exc
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid_timestamp(value: object, *, optional: bool) -> bool:
    if value is None:
        return optional
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None


class EnvironmentManager:
    """执行审定 Setup/Maintenance，并维护非敏感环境指纹。"""

    def __init__(
        self,
        config: SandboxdConfig,
        *,
        workspace_files: WorkspaceFileService | None = None,
        environment_service: SandboxEnvironmentService | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config.validated()
        self.workspace_files = workspace_files or WorkspaceFileService(
            self.config
        )
        self.environment_service = (
            environment_service or SandboxEnvironmentService()
        )
        self.clock = clock
        self._workspace_locks: dict[str, threading.Lock] = {}
        self._workspace_locks_guard = threading.Lock()

    def _workspace_lock(self, workspace_id: str) -> threading.Lock:
        with self._workspace_locks_guard:
            return self._workspace_locks.setdefault(
                workspace_id,
                threading.Lock(),
            )

    def _open_runtime(self, workspace_id: str) -> tuple[int, Path]:
        runtime_path = self.workspace_files.layout.ensure_runtime(
            workspace_id
        )
        runtime_fd: int | None = None
        try:
            runtime_fd = os.open(
                runtime_path,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            )
            metadata = os.fstat(runtime_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                raise OSError("runtime 不是目录")
            os.fchown(
                runtime_fd,
                self.config.workspace_uid,
                self.config.workspace_gid,
            )
            os.fchmod(runtime_fd, 0o700)
        except OSError as exc:
            if runtime_fd is not None:
                try:
                    os.close(runtime_fd)
                except OSError:
                    pass
            raise _runtime_unavailable(
                "Workspace Runtime 目录不可用"
            ) from exc
        return runtime_fd, runtime_path

    def _ensure_runtime_directories(self, workspace_id: str) -> Path:
        runtime_fd, runtime_path = self._open_runtime(workspace_id)
        try:
            for name in _RUNTIME_DIRECTORIES:
                try:
                    os.mkdir(name, 0o700, dir_fd=runtime_fd)
                except FileExistsError:
                    pass
                child_fd = os.open(
                    name,
                    os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                    dir_fd=runtime_fd,
                )
                try:
                    metadata = os.fstat(child_fd)
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise OSError("runtime cache 不是目录")
                    os.fchown(
                        child_fd,
                        self.config.workspace_uid,
                        self.config.workspace_gid,
                    )
                    os.fchmod(child_fd, 0o700)
                finally:
                    os.close(child_fd)
        except OSError as exc:
            raise _runtime_unavailable(
                "Sandbox Runtime 缓存目录不可用"
            ) from exc
        finally:
            os.close(runtime_fd)
        return runtime_path

    def _selected_lockfile_hashes(
        self,
        workspace_id: str,
        template: EnvironmentTemplate,
    ) -> dict[str, str]:
        filesystem = self.workspace_files.filesystem(workspace_id)
        entries = {
            entry.path: entry
            for entry in filesystem.list_entries("")
            if entry.type == "file"
        }
        selected: dict[str, str] = {}
        for relative_path in template.lockfile_paths:
            entry = entries.get(relative_path)
            if entry is None:
                continue
            if entry.size_bytes > _LOCKFILE_MAX_BYTES:
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "Sandbox 环境 lockfile 超过安全读取上限",
                )
            with filesystem.open_regular_file(relative_path) as (
                file_fd,
                size_bytes,
            ):
                if size_bytes > _LOCKFILE_MAX_BYTES:
                    raise SandboxServiceError(
                        SandboxErrorCode.AUTHORIZATION_FAILED,
                        "Sandbox 环境 lockfile 超过安全读取上限",
                    )
                before = os.fstat(file_fd)
                digest = hashlib.sha256()
                position = 0
                while position < size_bytes:
                    try:
                        chunk = os.pread(
                            file_fd,
                            min(64 * 1024, size_bytes - position),
                            position,
                        )
                    except OSError as exc:
                        raise _runtime_unavailable(
                            "Sandbox 环境 lockfile 暂时不可读"
                        ) from exc
                    if not chunk:
                        raise SandboxServiceError(
                            SandboxErrorCode.SANDBOX_BUSY,
                            "Sandbox 环境 lockfile 正在变化",
                            retryable=True,
                            stop=False,
                        )
                    digest.update(chunk)
                    position += len(chunk)
                after = os.fstat(file_fd)
                if (
                    before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                ):
                    raise SandboxServiceError(
                        SandboxErrorCode.SANDBOX_BUSY,
                        "Sandbox 环境 lockfile 正在变化",
                        retryable=True,
                        stop=False,
                    )
                selected[relative_path] = digest.hexdigest()
        return selected

    @staticmethod
    def _valid_fingerprint(value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        if set(value) != ENVIRONMENT_FINGERPRINT_FIELDS:
            return False
        profile_id = value.get("profile_id")
        catalog_generation = value.get("catalog_generation")
        policy_sha256 = value.get("policy_sha256")
        image_digest = value.get("image_digest")
        if (
            not isinstance(profile_id, str)
            or not profile_id
            or len(profile_id) > 64
            or not isinstance(catalog_generation, str)
            or _CATALOG_GENERATION_RE.fullmatch(catalog_generation) is None
            or not isinstance(policy_sha256, str)
            or _SHA256_RE.fullmatch(policy_sha256) is None
            or not isinstance(image_digest, str)
            or _IMAGE_DIGEST_RE.fullmatch(image_digest) is None
        ):
            return False
        for field_name in (
            "setup_definition_sha256",
            "maintenance_definition_sha256",
        ):
            digest = value.get(field_name)
            if (
                not isinstance(digest, str)
                or _SHA256_RE.fullmatch(digest) is None
            ):
                return False
        selected = value.get("selected_lockfile_hashes")
        if not isinstance(selected, Mapping) or len(selected) > 16:
            return False
        for path, digest in selected.items():
            if (
                not isinstance(path, str)
                or not path
                or "/" in path
                or "\\" in path
                or len(path.encode("utf-8")) > 255
                or not isinstance(digest, str)
                or _SHA256_RE.fullmatch(digest) is None
            ):
                return False
        return _valid_timestamp(
            value.get("last_setup_at"),
            optional=False,
        ) and _valid_timestamp(
            value.get("last_maintenance_at"),
            optional=True,
        )

    def _load_fingerprint(
        self,
        runtime_path: Path,
    ) -> dict[str, Any] | None:
        filesystem = SafeWorkspaceFilesystem(runtime_path)
        entries = {
            entry.path: entry
            for entry in filesystem.list_entries("")
        }
        entry = entries.get(ENVIRONMENT_FINGERPRINT_NAME)
        if (
            entry is None
            or entry.type != "file"
            or entry.size_bytes > _FINGERPRINT_MAX_BYTES
        ):
            return None
        try:
            raw = filesystem.read_bytes(
                ENVIRONMENT_FINGERPRINT_NAME,
                offset=0,
                limit=_FINGERPRINT_MAX_BYTES,
            )
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
            )
        except (
            json.JSONDecodeError,
            SandboxServiceError,
            UnicodeDecodeError,
            ValueError,
        ):
            return None
        if not self._valid_fingerprint(value):
            return None
        return dict(value)

    @staticmethod
    def _matches(
        fingerprint: Mapping[str, Any],
        expected: Mapping[str, Any],
    ) -> bool:
        return all(
            fingerprint.get(field_name) == value
            for field_name, value in expected.items()
        )

    def _run_commands(
        self,
        container: Any,
        *,
        template: EnvironmentTemplate,
        action: str,
    ) -> None:
        commands = (
            template.setup_commands
            if action == "setup"
            else template.maintenance_commands
        )
        for command in commands:
            try:
                result = container.exec_run(
                    [template.shell_path, "-lc", command],
                    user=(
                        f"{self.config.workspace_uid}:"
                        f"{self.config.workspace_gid}"
                    ),
                    workdir="/workspace",
                    privileged=False,
                    stdin=False,
                    tty=False,
                    # Docker Engine 29 配合 Docker SDK 7.1 时，如果两个
                    # 输出流都关闭，exec_run 会返回 exit_code=None。
                    # 命令来自固定模板且输出不会持久化，保持流开启以等待
                    # 并取得可信退出码。
                    stdout=True,
                    stderr=True,
                )
                if hasattr(result, "exit_code"):
                    exit_code = int(result.exit_code)
                else:
                    exit_code = int(result[0])
            except Exception as exc:
                raise _runtime_unavailable(
                    "Sandbox 环境准备命令无法执行"
                ) from exc
            if exit_code != 0:
                raise _runtime_unavailable(
                    "Sandbox 环境准备命令执行失败"
                )

    @staticmethod
    def _write_fingerprint(
        runtime_path: Path,
        fingerprint: Mapping[str, Any],
    ) -> None:
        content = json.dumps(
            dict(fingerprint),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        SafeWorkspaceFilesystem(runtime_path).write_bytes(
            ENVIRONMENT_FINGERPRINT_NAME,
            content,
            overwrite=True,
            max_bytes=_FINGERPRINT_MAX_BYTES,
        )

    def prepare(
        self,
        *,
        container: Any,
        workspace_id: str,
        profile_id: str,
        catalog_generation: str,
        policy_sha256: str,
        image_digest: str,
    ) -> dict[str, Any]:
        workspace_id = validate_workspace_id(workspace_id)
        template = self.environment_service.template(profile_id)
        normalized_catalog = str(catalog_generation or "")
        normalized_policy = str(policy_sha256 or "").lower()
        normalized_image = str(image_digest or "").lower()
        if (
            _CATALOG_GENERATION_RE.fullmatch(normalized_catalog) is None
            or _SHA256_RE.fullmatch(normalized_policy) is None
            or _IMAGE_DIGEST_RE.fullmatch(normalized_image) is None
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 环境策略或镜像事实无效",
            )

        with self._workspace_lock(workspace_id):
            runtime_path = self._ensure_runtime_directories(workspace_id)
            selected_hashes = self._selected_lockfile_hashes(
                workspace_id,
                template,
            )
            expected = {
                "profile_id": template.profile_id,
                "catalog_generation": normalized_catalog,
                "policy_sha256": normalized_policy,
                "image_digest": normalized_image,
                "setup_definition_sha256": (
                    template.setup_definition_sha256
                ),
                "maintenance_definition_sha256": (
                    template.maintenance_definition_sha256
                ),
                "selected_lockfile_hashes": selected_hashes,
            }
            previous = self._load_fingerprint(runtime_path)
            if previous is None:
                action = "setup"
            elif self._matches(previous, expected):
                action = "unchanged"
            else:
                action = "maintenance"

            if action != "unchanged":
                self._run_commands(
                    container,
                    template=template,
                    action=action,
                )
                now = _timestamp(self.clock)
                fingerprint = {
                    **expected,
                    "last_setup_at": (
                        now
                        if action == "setup"
                        else previous["last_setup_at"]
                    ),
                    "last_maintenance_at": (
                        now
                        if action == "maintenance"
                        else None
                    ),
                }
                self._write_fingerprint(runtime_path, fingerprint)
            else:
                fingerprint = dict(previous)

            return {
                "ready": True,
                "action": action,
                **fingerprint,
            }

    @classmethod
    def _remove_entry(
        cls,
        parent_fd: int,
        name: str,
        *,
        depth: int,
        counter: list[int],
    ) -> bool:
        if depth > _MAX_CLEANUP_DEPTH:
            raise _runtime_unavailable(
                "Sandbox Runtime 缓存目录层级超过清理上限"
            )
        counter[0] += 1
        if counter[0] > _MAX_CLEANUP_ENTRIES:
            raise _runtime_unavailable(
                "Sandbox Runtime 缓存条目超过清理上限"
            )
        try:
            metadata = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise _runtime_unavailable(
                "Sandbox Runtime 缓存无法检查"
            ) from exc
        if not stat.S_ISDIR(metadata.st_mode):
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError as exc:
                raise _runtime_unavailable(
                    "Sandbox Runtime 缓存无法删除"
                ) from exc
            return True

        try:
            child_fd = os.open(
                name,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise _runtime_unavailable(
                "Sandbox Runtime 缓存目录无法打开"
            ) from exc
        try:
            try:
                children = os.listdir(child_fd)
            except OSError as exc:
                raise _runtime_unavailable(
                    "Sandbox Runtime 缓存目录无法列出"
                ) from exc
            for child_name in children:
                cls._remove_entry(
                    child_fd,
                    child_name,
                    depth=depth + 1,
                    counter=counter,
                )
        finally:
            os.close(child_fd)
        try:
            os.rmdir(name, dir_fd=parent_fd)
        except OSError as exc:
            raise _runtime_unavailable(
                "Sandbox Runtime 缓存目录无法删除"
            ) from exc
        return True

    def cleanup(self, workspace_id: str) -> dict[str, Any]:
        """只删除目标 Workspace 的可重建缓存，保留 Workspace 与 home。"""

        workspace_id = validate_workspace_id(workspace_id)
        with self._workspace_lock(workspace_id):
            runtime_fd, _runtime_path = self._open_runtime(workspace_id)
            removed: list[str] = []
            counter = [0]
            try:
                for name in _CLEANABLE_RUNTIME_DIRECTORIES:
                    if self._remove_entry(
                        runtime_fd,
                        name,
                        depth=0,
                        counter=counter,
                    ):
                        removed.append(name)
                fingerprint_removed = self._remove_entry(
                    runtime_fd,
                    ENVIRONMENT_FINGERPRINT_NAME,
                    depth=0,
                    counter=counter,
                )
            finally:
                os.close(runtime_fd)
        return {
            "workspace_id": workspace_id,
            "removed_entries": removed,
            "fingerprint_removed": fingerprint_removed,
        }

__all__ = ["EnvironmentManager"]
