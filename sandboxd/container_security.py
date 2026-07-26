"""为一次性容器和 Lease 容器生成同源的 Docker 安全参数。"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping

from core.sandbox.profile_catalog import ExecutionProfileDefinition
from sandboxd.config import SandboxdConfig
from sandboxd.network_policy import LeaseNetworkAttachment


ContainerLifecycle = Literal["oneshot", "lease"]

# 该集合是一次性容器与 Lease 容器必须共享的安全契约。值可按 Profile
# 不同，但字段不得由各 backend 各自维护。
CONTAINER_SECURITY_FIELDS = frozenset({
    "network_disabled",
    "network_mode",
    "read_only",
    "cap_drop",
    "security_opt",
    "privileged",
    "init",
    "pids_limit",
    "mem_limit",
    "memswap_limit",
    "nano_cpus",
    "oom_kill_disable",
    "ipc_mode",
    "tmpfs",
    "log_config",
    "device_write_bps",
})


class ContainerSecurityError(ValueError):
    """固定容器安全参数无法安全生成。"""


@dataclass(frozen=True, slots=True)
class ContainerMountPaths:
    """仅允许映射到三个固定容器路径的宿主目录。"""

    workspace: Path
    inputs: Path
    runtime: Path


def _require_managed_directory(
    path: Path,
    *,
    allowed_root: Path,
    name: str,
) -> Path:
    if not path.is_absolute():
        raise ContainerSecurityError(f"{name} 挂载源必须是绝对路径")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        root = allowed_root.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise ContainerSecurityError(f"{name} 挂载源不在固定数据目录内") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ContainerSecurityError(f"{name} 挂载源必须是非符号链接目录")
    return resolved


def _fixed_volumes(
    config: SandboxdConfig,
    mounts: ContainerMountPaths,
) -> dict[str, dict[str, str]]:
    data_root = config.data_root
    workspace = _require_managed_directory(
        mounts.workspace,
        allowed_root=data_root / "workspaces",
        name="Workspace",
    )
    inputs = _require_managed_directory(
        mounts.inputs,
        allowed_root=data_root / "runtime" / ".inputs",
        name="Inputs",
    )
    runtime = _require_managed_directory(
        mounts.runtime,
        allowed_root=data_root / "runtime",
        name="Runtime",
    )
    if len({workspace, inputs, runtime}) != 3:
        raise ContainerSecurityError("固定挂载源不得互相复用")
    return {
        os.fspath(workspace): {"bind": "/workspace", "mode": "rw"},
        os.fspath(inputs): {"bind": "/inputs", "mode": "ro"},
        os.fspath(runtime): {"bind": "/runtime", "mode": "rw"},
    }


def _working_dir(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value.startswith("/workspace")
        or value != os.fspath(path)
        or ".." in path.parts
        or path.parts[:2] != ("/", "workspace")
    ):
        raise ContainerSecurityError("容器工作目录必须位于 /workspace")
    return value


def _network_parameters(
    config: SandboxdConfig,
    profile: ExecutionProfileDefinition,
    attachment: LeaseNetworkAttachment | None,
) -> dict[str, object]:
    if profile.network_policy_id == "none":
        if attachment is not None:
            raise ContainerSecurityError(
                "无网络 Profile 不得携带 Lease 网络附件"
            )
        return {
            "network_disabled": True,
            "network_mode": "none",
        }
    catalog = config.profile_catalog
    if (
        catalog is None
        or attachment is None
        or attachment.policy_id != profile.network_policy_id
        or attachment.policy_sha256 != catalog.policy_sha256
        or attachment.controller_epoch == ""
        or attachment.proxy_port != profile.network_proxy_port
    ):
        raise ContainerSecurityError(
            f"Profile {profile.profile_id} 缺少受信 Lease 网络附件"
        )
    return {
        "network_disabled": False,
        "network_mode": attachment.network_name,
    }


def _environment(
    profile: ExecutionProfileDefinition,
    attachment: LeaseNetworkAttachment | None,
) -> dict[str, str]:
    environment = {
        "HOME": "/runtime/home",
        "XDG_CACHE_HOME": "/runtime/cache",
        "PYTHONPYCACHEPREFIX": "/runtime/pycache",
        "PIP_CACHE_DIR": "/runtime/pip-cache",
        "TMPDIR": "/tmp",
    }
    if profile.network_policy_id == "none":
        environment["PIP_NO_INDEX"] = "1"
    elif attachment is not None:
        environment.update({
            "http_proxy": attachment.proxy_url,
            "https_proxy": attachment.proxy_url,
            "HTTP_PROXY": attachment.proxy_url,
            "HTTPS_PROXY": attachment.proxy_url,
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "no_proxy": "localhost,127.0.0.1,::1",
        })
    return environment


def build_container_security_kwargs(
    config: SandboxdConfig,
    profile: ExecutionProfileDefinition,
    *,
    network_attachment: LeaseNetworkAttachment | None = None,
) -> dict[str, Any]:
    """按 canonical Profile 生成不可由调用方覆写的 Docker 安全字段。"""

    if not profile.grantable:
        raise ContainerSecurityError(
            f"Profile {profile.profile_id} 当前不可创建容器"
        )
    if not profile.apparmor_profile or profile.apparmor_profile == "unconfined":
        raise ContainerSecurityError("AppArmor Profile 不得为空或 unconfined")

    kwargs: dict[str, Any] = {
        **_network_parameters(config, profile, network_attachment),
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": [
            "no-new-privileges",
            f"apparmor={profile.apparmor_profile}",
        ],
        "privileged": False,
        "init": True,
        "pids_limit": profile.pids_limit,
        "mem_limit": profile.memory_bytes,
        "memswap_limit": profile.memory_bytes,
        "nano_cpus": profile.cpu_nano,
        "oom_kill_disable": False,
        "ipc_mode": "private",
        "tmpfs": {
            "/tmp": (
                "rw,nosuid,nodev,noexec,mode=1777,"
                f"size={profile.tmpfs_bytes}"
            ),
        },
        "log_config": {
            "type": "local",
            "config": {
                "max-size": "1m",
                "max-file": "1",
                "compress": "false",
            },
        },
    }
    if config.io_device_path:
        kwargs["device_write_bps"] = [{
            "Path": config.io_device_path,
            "Rate": config.io_write_bps,
        }]
    return kwargs


def build_container_kwargs(
    config: SandboxdConfig,
    profile: ExecutionProfileDefinition,
    *,
    lifecycle: ContainerLifecycle,
    image_id: str,
    name: str,
    command: str,
    working_dir: str,
    labels: Mapping[str, str],
    mounts: ContainerMountPaths,
    network_attachment: LeaseNetworkAttachment | None = None,
) -> dict[str, Any]:
    """生成完整容器创建参数，调用方不能注入 Docker 安全选项。"""

    if lifecycle != profile.execution_mode:
        raise ContainerSecurityError(
            f"Profile {profile.profile_id} 与容器生命周期不匹配"
        )
    if not command or "\x00" in command:
        raise ContainerSecurityError("容器命令无效")
    if not image_id.startswith("sha256:") or len(image_id) != 71:
        raise ContainerSecurityError("容器镜像必须使用完整 IMAGE ID")
    if not name or "\x00" in name:
        raise ContainerSecurityError("容器名称无效")

    return {
        "image": image_id,
        "name": name,
        "command": [profile.shell_path, "-lc", command],
        "user": f"{config.workspace_uid}:{config.workspace_gid}",
        "working_dir": _working_dir(working_dir),
        "detach": True,
        "stdin_open": False,
        "tty": False,
        "auto_remove": False,
        **build_container_security_kwargs(
            config,
            profile,
            network_attachment=network_attachment,
        ),
        "volumes": _fixed_volumes(config, mounts),
        "labels": dict(labels),
        "environment": _environment(profile, network_attachment),
    }


def security_projection(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """返回稳定安全字段投影，供 backend 漂移测试和审计使用。"""

    return {
        field: kwargs.get(field)
        for field in sorted(CONTAINER_SECURITY_FIELDS)
    }


__all__ = [
    "CONTAINER_SECURITY_FIELDS",
    "ContainerLifecycle",
    "ContainerMountPaths",
    "ContainerSecurityError",
    "build_container_kwargs",
    "build_container_security_kwargs",
    "security_projection",
]
