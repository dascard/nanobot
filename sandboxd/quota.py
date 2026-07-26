"""sandboxd 独占的 Workspace/Runtime project quota 宿主适配器。"""

from __future__ import annotations

import os
import stat
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.paths import SandboxStorageLayout, SafeWorkspaceFilesystem, validate_workspace_id
from sandboxd.filesystem import directory_usage


PROJECT_ID_MIN = 10000
PROJECT_ID_MAX = 2_147_483_647
QUOTA_MIN_BYTES = 1024 * 1024
QUOTA_MAX_BYTES = 1024 * 1024 * 1024 * 1024
QuotaScope = Literal["workspace", "runtime"]


class QuotaCommand(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


def _run_command(
    argv: Sequence[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
        env={
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        },
    )


def _validated_project_id(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError
    project_id = int(value)
    if not PROJECT_ID_MIN <= project_id <= PROJECT_ID_MAX:
        raise ValueError
    return project_id


def _validated_quota(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError
    quota = int(value)
    if not QUOTA_MIN_BYTES <= quota <= QUOTA_MAX_BYTES:
        raise ValueError
    return quota


def _validated_generation(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError
    generation = int(value)
    if not 1 <= generation <= 2_147_483_647:
        raise ValueError
    return generation


def _validated_scope(value: object) -> QuotaScope:
    normalized = str(value or "")
    if normalized not in {"workspace", "runtime"}:
        raise ValueError
    return normalized  # type: ignore[return-value]


def _verified_confirmation(
    result: subprocess.CompletedProcess[str],
    *,
    scope: QuotaScope,
    project_id: int,
    quota_bytes: int,
) -> bool:
    lines = {
        line.strip()
        for line in str(result.stdout or "").splitlines()
    }
    return (
        result.returncode == 0
        and {
            "project_quota_verified=true",
            f"scope={scope}",
            f"project_id={project_id}",
            f"quota_bytes={quota_bytes}",
        }.issubset(lines)
    )


class ProjectQuotaManager:
    """只接受 UUID 与数值策略，宿主路径和命令均来自 root 配置。"""

    def __init__(
        self,
        *,
        data_root: Path,
        helper_path: Path,
        command: QuotaCommand = _run_command,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.data_root = Path(data_root)
        self.helper_path = Path(helper_path)
        self.command = command
        self.timeout_seconds = float(timeout_seconds)
        self.layout = SandboxStorageLayout(self.data_root)
        self._capability_guard = threading.Lock()
        self._capability_checked_at = 0.0
        self._capability_cache: dict[str, Any] | None = None

    def _validated_helper(self) -> Path:
        if not self.helper_path.is_absolute():
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Workspace project quota helper 未安全配置",
            )
        try:
            metadata = self.helper_path.lstat()
        except OSError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Workspace project quota helper 不可用",
                retryable=True,
                stop=False,
            ) from exc
        mode = stat.S_IMODE(metadata.st_mode)
        expected_owner = 0 if os.geteuid() == 0 else os.geteuid()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or self.helper_path.is_symlink()
            or metadata.st_uid != expected_owner
            or mode & 0o022
            or not mode & stat.S_IXUSR
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Workspace project quota helper 未安全配置",
            )
        return self.helper_path

    def _values(
        self,
        *,
        workspace_id: object,
        project_id: object,
        quota_bytes: object,
        generation: object,
        scope: object = "workspace",
    ) -> tuple[str, int, int, int, QuotaScope, Path]:
        try:
            workspace = validate_workspace_id(str(workspace_id or ""))
            project = _validated_project_id(project_id)
            quota = _validated_quota(quota_bytes)
            normalized_generation = _validated_generation(generation)
            normalized_scope = _validated_scope(scope)
        except (SandboxServiceError, TypeError, ValueError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox project quota 参数无效",
            ) from exc
        path = (
            self.layout.workspace_data_dir(workspace)
            if normalized_scope == "workspace"
            else self.layout.runtime_root / workspace
        )
        try:
            metadata = path.lstat()
            if not path.is_dir() or path.is_symlink() or metadata.st_nlink < 1:
                raise OSError
        except OSError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 配额目录尚未就绪",
                retryable=True,
                stop=False,
            ) from exc
        return (
            workspace,
            project,
            quota,
            normalized_generation,
            normalized_scope,
            path,
        )

    def capability(self, *, max_age_seconds: float = 30.0) -> dict[str, Any]:
        """只读验证底层文件系统与 quota helper 能力，并短时缓存结果。"""

        now = time.monotonic()
        with self._capability_guard:
            if (
                self._capability_cache is not None
                and now - self._capability_checked_at
                <= max(0.0, float(max_age_seconds))
            ):
                return dict(self._capability_cache)
            helper_path = self._validated_helper()
            argv = (
                os.fspath(helper_path),
                "--check-capability",
                "--data-root",
                os.fspath(self.data_root),
            )
            try:
                result = self.command(
                    argv,
                    timeout=min(self.timeout_seconds, 30.0),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Sandbox project quota 能力检查不可用",
                    retryable=True,
                    stop=False,
                ) from exc
            capability_lines = {
                line.strip()
                for line in str(result.stdout or "").splitlines()
            }
            required_lines = {
                "project_quota_ready=true",
                "workspace_scope=true",
                "runtime_scope=true",
            }
            if (
                result.returncode != 0
                or not required_lines.issubset(capability_lines)
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "宿主 project quota 尚未就绪",
                    retryable=True,
                    stop=False,
                )
            capability = {
                "project_quota": True,
                "workspace_scope": True,
                "runtime_scope": True,
            }
            self._capability_cache = capability
            self._capability_checked_at = now
            return dict(capability)

    def apply(
        self,
        *,
        workspace_id: object,
        project_id: object,
        quota_bytes: object,
        generation: object,
        scope: object = "workspace",
    ) -> dict[str, Any]:
        (
            workspace,
            project,
            quota,
            normalized_generation,
            normalized_scope,
            path,
        ) = self._values(
            workspace_id=workspace_id,
            project_id=project_id,
            quota_bytes=quota_bytes,
            generation=generation,
            scope=scope,
        )
        helper_path = self._validated_helper()
        argv = (
            os.fspath(helper_path),
            "--workspace-id",
            workspace,
            "--scope",
            normalized_scope,
            "--project-id",
            str(project),
            "--quota-bytes",
            str(quota),
            "--data-root",
            os.fspath(self.data_root),
            "--quiesced",
            "--apply",
        )
        try:
            result = self.command(argv, timeout=self.timeout_seconds)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox project quota 控制面不可用",
                retryable=True,
                stop=False,
            ) from exc
        if result.returncode != 0:
            diagnostic = f"{result.stdout}\n{result.stderr}"
            if "仍有活动 Sandbox 容器" in diagnostic:
                raise SandboxServiceError(
                    SandboxErrorCode.SANDBOX_BUSY,
                    "仍有 Sandbox 运行，配额修改稍后重试",
                    retryable=True,
                    stop=False,
                )
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox project quota 应用失败",
                retryable=True,
                stop=False,
            )
        if not _verified_confirmation(
            result,
            scope=normalized_scope,
            project_id=project,
            quota_bytes=quota,
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox project quota 应用结果未通过验证",
                retryable=True,
                stop=False,
            )
        used_bytes = directory_usage(SafeWorkspaceFilesystem(path))
        return {
            "workspace_id": workspace,
            "scope": normalized_scope,
            "project_id": project,
            "quota_bytes": quota,
            "generation": normalized_generation,
            "used_bytes": int(used_bytes),
            "applied": True,
        }

    def inspect(
        self,
        *,
        workspace_id: object,
        project_id: object,
        quota_bytes: object,
        generation: object,
        scope: object = "workspace",
    ) -> dict[str, Any]:
        (
            workspace,
            project,
            quota,
            normalized_generation,
            normalized_scope,
            path,
        ) = self._values(
            workspace_id=workspace_id,
            project_id=project_id,
            quota_bytes=quota_bytes,
            generation=generation,
            scope=scope,
        )
        helper_path = self._validated_helper()
        try:
            result = self.command(
                (
                    os.fspath(helper_path),
                    "--workspace-id",
                    workspace,
                    "--scope",
                    normalized_scope,
                    "--project-id",
                    str(project),
                    "--quota-bytes",
                    str(quota),
                    "--data-root",
                    os.fspath(self.data_root),
                    "--quiesced",
                    "--verify",
                ),
                timeout=min(self.timeout_seconds, 15.0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox project quota 检查不可用",
                retryable=True,
                stop=False,
            ) from exc
        verified = _verified_confirmation(
            result,
            scope=normalized_scope,
            project_id=project,
            quota_bytes=quota,
        )
        used_bytes = directory_usage(SafeWorkspaceFilesystem(path))
        return {
            "workspace_id": workspace,
            "scope": normalized_scope,
            "project_id": project,
            "observed_project_id": project if verified else 0,
            "quota_bytes": quota,
            "generation": normalized_generation,
            "used_bytes": int(used_bytes),
            "project_id_matches": verified,
            "quota_bytes_matches": verified,
            "verified": verified,
        }


__all__ = ["ProjectQuotaManager", "QuotaScope"]
