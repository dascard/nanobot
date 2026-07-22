"""sandboxd 的 root 管理固定策略配置。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@dataclass(frozen=True)
class SandboxdConfig:
    data_root: Path = Path("/srv/nanobot")
    socket_path: Path = Path("/run/nanobot-sandboxd/sandboxd.sock")
    token_file: Path = Path("/etc/nanobot/sandboxd.token")
    client_token_path: Path = Path("/run/nanobot-sandboxd/client.token")
    admin_token_file: Path = Path("/etc/nanobot/sandboxd-admin.token")
    admin_client_token_path: Path = Path(
        "/run/nanobot-sandboxd/admin-client.token"
    )
    quota_helper_path: Path = Path(
        "/opt/nanobot-server/scripts/assign-sandbox-project-quota.sh"
    )
    docker_socket: str = "unix:///var/run/docker.sock"
    image_reference: str = ""
    image_allowlist: tuple[str, ...] = ()
    apparmor_profile: str = "nanobot-sandbox"
    workspace_uid: int = 10001
    workspace_gid: int = 10001
    cpu_nano: int = 1_000_000_000
    memory_bytes: int = 512 * 1024 * 1024
    pids_limit: int = 128
    global_concurrency: int = 2
    default_timeout_seconds: int = 60
    max_timeout_seconds: int = 120
    stdout_limit_bytes: int = 256 * 1024
    stderr_limit_bytes: int = 256 * 1024
    hard_output_limit_bytes: int = 1024 * 1024
    tmpfs_bytes: int = 128 * 1024 * 1024
    runtime_quota_bytes: int = 512 * 1024 * 1024
    workspace_quota_bytes: int = 2 * 1024 * 1024 * 1024
    asset_max_bytes: int = 512 * 1024 * 1024
    total_quota_bytes: int = 10 * 1024 * 1024 * 1024
    disk_max_percent: int = 80
    disk_min_free_bytes: int = 50 * 1024 * 1024 * 1024
    max_write_bytes: int = 256 * 1024
    max_read_bytes: int = 256 * 1024
    max_command_bytes: int = 16 * 1024
    io_device_path: str = ""
    io_write_bps: int = 16 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "SandboxdConfig":
        allowlist = tuple(
            item.strip().lower()
            for item in os.environ.get("NANOBOT_SANDBOX_IMAGE_ALLOWLIST", "").split(",")
            if item.strip()
        )
        return cls(
            data_root=Path(os.environ.get("NANOBOT_SANDBOX_DATA_ROOT", "/srv/nanobot")),
            socket_path=Path(os.environ.get(
                "NANOBOT_SANDBOXD_SOCKET",
                "/run/nanobot-sandboxd/sandboxd.sock",
            )),
            token_file=Path(os.environ.get(
                "NANOBOT_SANDBOXD_TOKEN_FILE",
                "/etc/nanobot/sandboxd.token",
            )),
            client_token_path=Path(os.environ.get(
                "NANOBOT_SANDBOXD_CLIENT_TOKEN_FILE",
                "/run/nanobot-sandboxd/client.token",
            )),
            admin_token_file=Path(os.environ.get(
                "NANOBOT_SANDBOXD_ADMIN_TOKEN_FILE",
                "/etc/nanobot/sandboxd-admin.token",
            )),
            admin_client_token_path=Path(os.environ.get(
                "NANOBOT_SANDBOXD_ADMIN_CLIENT_TOKEN_FILE",
                "/run/nanobot-sandboxd/admin-client.token",
            )),
            quota_helper_path=Path(os.environ.get(
                "NANOBOT_SANDBOXD_QUOTA_HELPER",
                "/opt/nanobot-server/scripts/assign-sandbox-project-quota.sh",
            )),
            docker_socket=os.environ.get(
                "NANOBOT_SANDBOXD_DOCKER_SOCKET",
                "unix:///var/run/docker.sock",
            ),
            image_reference=os.environ.get("NANOBOT_SANDBOX_IMAGE", ""),
            image_allowlist=allowlist,
            apparmor_profile=os.environ.get(
                "NANOBOT_SANDBOX_APPARMOR_PROFILE",
                "nanobot-sandbox",
            ),
            workspace_uid=_env_int("NANOBOT_SANDBOX_UID", 10001),
            workspace_gid=_env_int("NANOBOT_SANDBOX_GID", 10001),
            global_concurrency=_env_int("NANOBOT_SANDBOX_GLOBAL_CONCURRENCY", 2),
            default_timeout_seconds=_env_int("NANOBOT_SANDBOX_DEFAULT_TIMEOUT", 60),
            max_timeout_seconds=_env_int("NANOBOT_SANDBOX_MAX_TIMEOUT", 120),
            workspace_quota_bytes=_env_int(
                "NANOBOT_SANDBOX_WORKSPACE_QUOTA_BYTES",
                2 * 1024 * 1024 * 1024,
            ),
            asset_max_bytes=_env_int(
                "NANOBOT_SANDBOX_ASSET_MAX_BYTES",
                512 * 1024 * 1024,
            ),
            total_quota_bytes=_env_int(
                "NANOBOT_SANDBOX_TOTAL_QUOTA_BYTES",
                10 * 1024 * 1024 * 1024,
            ),
            disk_max_percent=_env_int("NANOBOT_SANDBOX_DISK_MAX_PERCENT", 80),
            disk_min_free_bytes=_env_int(
                "NANOBOT_SANDBOX_DISK_MIN_FREE_BYTES",
                50 * 1024 * 1024 * 1024,
            ),
            io_device_path=os.environ.get("NANOBOT_SANDBOX_IO_DEVICE", ""),
            io_write_bps=_env_int(
                "NANOBOT_SANDBOX_IO_WRITE_BPS",
                16 * 1024 * 1024,
            ),
        ).validated()

    def validated(self) -> "SandboxdConfig":
        if not self.data_root.is_absolute() or not self.socket_path.is_absolute():
            raise ValueError("sandboxd 数据目录和 Socket 必须是绝对路径")
        if not self.token_file.is_absolute() or not self.client_token_path.is_absolute():
            raise ValueError("sandboxd Token 文件必须是绝对路径")
        if (
            not self.admin_token_file.is_absolute()
            or not self.admin_client_token_path.is_absolute()
            or not self.quota_helper_path.is_absolute()
        ):
            raise ValueError("sandboxd 管理凭据和 quota helper 必须是绝对路径")
        if (
            self.token_file == self.admin_token_file
            or self.client_token_path == self.admin_client_token_path
        ):
            raise ValueError("sandboxd 普通凭据与管理凭据路径必须分离")
        if not self.image_reference or self.image_reference.endswith(":latest"):
            raise ValueError("必须配置非 latest 的 Sandbox 镜像")
        if not self.image_allowlist or any(
            not _IMAGE_ID_RE.fullmatch(item)
            for item in self.image_allowlist
        ):
            raise ValueError("Sandbox 镜像 allowlist 必须是完整 sha256 IMAGE ID")
        if not self.apparmor_profile or self.apparmor_profile == "unconfined":
            raise ValueError("Sandbox AppArmor profile 不得为空或 unconfined")
        if not 1 <= self.global_concurrency <= 32:
            raise ValueError("Sandbox 全局并发配置无效")
        if not 1 <= self.default_timeout_seconds <= self.max_timeout_seconds <= 600:
            raise ValueError("Sandbox 超时配置无效")
        if not 1 <= self.disk_max_percent <= 99:
            raise ValueError("Sandbox 磁盘水位配置无效")
        return self
