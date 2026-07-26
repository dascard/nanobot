"""Sandbox 服务之间共享的稳定数据契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class SandboxErrorCode(str, Enum):
    SANDBOX_NOT_ENABLED = "sandbox_not_enabled"
    AUTHORIZATION_FAILED = "authorization_failed"
    INVALID_PATH = "invalid_path"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    WORKSPACE_QUOTA_EXCEEDED = "workspace_quota_exceeded"
    RUNTIME_QUOTA_EXCEEDED = "runtime_quota_exceeded"
    DISK_PRESSURE = "disk_pressure"
    SANDBOX_BUSY = "sandbox_busy"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    EXECUTION_TIMEOUT = "execution_timeout"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    PROCESS_OOM_KILLED = "process_oom_killed"
    ASSET_NOT_FOUND = "asset_not_found"
    ASSET_NOT_AUTHORIZED = "asset_not_authorized"
    ASSET_TOO_LARGE = "asset_too_large"
    ASSET_NAME_CONFLICT = "asset_name_conflict"


class SandboxServiceError(RuntimeError):
    """只携带可安全返回给调用方的信息，不保存宿主路径。"""

    def __init__(
        self,
        code: SandboxErrorCode | str,
        summary: str,
        *,
        retryable: bool = False,
        hint: str = "",
        stop: bool = True,
    ) -> None:
        self.code = SandboxErrorCode(code)
        self.summary = str(summary)
        self.retryable = bool(retryable)
        self.hint = str(hint)
        self.stop = bool(stop)
        super().__init__(self.summary)

    def to_result(self) -> dict[str, Any]:
        return {
            "status": "error",
            "summary": self.summary,
            "next_actions": [],
            "artifacts": [],
            "error": {
                "code": self.code.value,
                "retryable": self.retryable,
                "hint": self.hint,
                "stop": self.stop,
            },
        }


@dataclass(frozen=True)
class FileEntry:
    path: str
    type: str
    size_bytes: int
    modified_at_ns: int


@dataclass(frozen=True)
class WrittenFile:
    path: str
    size_bytes: int
    previous_size_bytes: int

    @property
    def growth_bytes(self) -> int:
        return max(0, self.size_bytes - self.previous_size_bytes)


@dataclass(frozen=True)
class PublishedAsset:
    sha256: str
    size_bytes: int
    media_type: str
    storage_key: str


def success_result(
    summary: str,
    *,
    data: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    next_actions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """生成所有 Sandbox 工具共用的成功信封。"""

    return {
        "status": "success",
        "summary": str(summary),
        "next_actions": list(next_actions or []),
        "artifacts": list(artifacts or []),
        "data": dict(data or {}),
    }


def dataclass_dict(value: Any) -> dict[str, Any]:
    """仅用于已知安全的数据类返回值。"""

    return asdict(value)
