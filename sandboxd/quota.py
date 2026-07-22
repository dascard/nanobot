"""sandboxd 独占的 Workspace project quota 宿主适配器。"""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.paths import SandboxStorageLayout, SafeWorkspaceFilesystem, validate_workspace_id
from sandboxd.filesystem import directory_usage


PROJECT_ID_MIN = 10000
PROJECT_ID_MAX = 2_147_483_647
QUOTA_MIN_BYTES = 1024 * 1024
QUOTA_MAX_BYTES = 1024 * 1024 * 1024 * 1024


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
    ) -> tuple[str, int, int, int, Path]:
        try:
            workspace = validate_workspace_id(str(workspace_id or ""))
            project = _validated_project_id(project_id)
            quota = _validated_quota(quota_bytes)
            normalized_generation = _validated_generation(generation)
        except (SandboxServiceError, TypeError, ValueError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Workspace project quota 参数无效",
            ) from exc
        path = self.layout.workspace_data_dir(workspace)
        try:
            metadata = path.lstat()
            if not path.is_dir() or path.is_symlink() or metadata.st_nlink < 1:
                raise OSError
        except OSError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Workspace 数据目录尚未就绪",
                retryable=True,
                stop=False,
            ) from exc
        return workspace, project, quota, normalized_generation, path

    def apply(
        self,
        *,
        workspace_id: object,
        project_id: object,
        quota_bytes: object,
        generation: object,
    ) -> dict[str, Any]:
        workspace, project, quota, normalized_generation, path = self._values(
            workspace_id=workspace_id,
            project_id=project_id,
            quota_bytes=quota_bytes,
            generation=generation,
        )
        helper_path = self._validated_helper()
        argv = (
            os.fspath(helper_path),
            "--workspace-id",
            workspace,
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
                "Workspace project quota 控制面不可用",
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
                "Workspace project quota 应用失败",
                retryable=True,
                stop=False,
            )
        used_bytes = directory_usage(SafeWorkspaceFilesystem(path))
        return {
            "workspace_id": workspace,
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
    ) -> dict[str, Any]:
        workspace, project, quota, normalized_generation, path = self._values(
            workspace_id=workspace_id,
            project_id=project_id,
            quota_bytes=quota_bytes,
            generation=generation,
        )
        # 只读取目录 project ID；硬限制的最终确认仍以 apply helper 的 limit+verify
        # 同步成功为准，避免解析不同发行版不稳定的 quota 表格输出。
        try:
            result = self.command(
                ("/usr/bin/lsattr", "-d", "-p", "--", os.fspath(path)),
                timeout=min(self.timeout_seconds, 15.0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Workspace project quota 检查不可用",
                retryable=True,
                stop=False,
            ) from exc
        tokens = str(result.stdout or "").split()
        observed = next(
            (int(token) for token in tokens if token.isascii() and token.isdigit()),
            0,
        )
        used_bytes = directory_usage(SafeWorkspaceFilesystem(path))
        return {
            "workspace_id": workspace,
            "project_id": project,
            "observed_project_id": observed,
            "quota_bytes": quota,
            "generation": normalized_generation,
            "used_bytes": int(used_bytes),
            "project_id_matches": result.returncode == 0 and observed == project,
        }


__all__ = ["ProjectQuotaManager"]
