"""Server 侧只读执行 Profile 注册表。"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

from core.sandbox.contracts import (
    SandboxErrorCode,
    SandboxServiceError,
)
from core.sandbox.profile_catalog import (
    DEFAULT_PROFILE_MANIFEST_PATH,
    ExecutionProfileDefinition,
    ProfileCatalog,
    ProfileCatalogError,
    load_profile_catalog,
)


@dataclass(frozen=True, slots=True)
class ExecutionProfileDescriptor:
    profile_id: str
    execution_mode: Literal["oneshot", "lease"]
    grantable: bool
    image_reference: str
    image_allowlist: tuple[str, ...]
    network_policy_id: str
    network_allowlist: tuple[str, ...]
    network_denied_cidrs: tuple[str, ...]
    network_destination_ports: tuple[int, ...]
    network_connect_ports: tuple[int, ...]
    network_proxy_image_reference: str
    network_proxy_image_allowlist: tuple[str, ...]
    network_proxy_port: int
    shell_path: str
    idle_ttl_seconds: int
    max_ttl_seconds: int
    default_timeout_seconds: int
    max_timeout_seconds: int
    max_processes: int
    global_concurrency: int
    cpu_nano: int
    memory_bytes: int
    pids_limit: int
    tmpfs_bytes: int
    workspace_quota_ceiling_bytes: int
    runtime_quota_bytes: int
    allow_stdin: bool
    allow_long_running_processes: bool
    allow_detached_processes: bool
    apparmor_profile: str

    @classmethod
    def from_definition(
        cls,
        definition: ExecutionProfileDefinition,
    ) -> "ExecutionProfileDescriptor":
        return cls(
            **{
                field_name: getattr(definition, field_name)
                for field_name in cls.__dataclass_fields__
            }
        )


@dataclass(frozen=True, slots=True)
class ControllerProfileState:
    profile_id: str
    execution_mode: str
    image_id: str
    apparmor_profile: str
    network_policy_id: str
    network_proxy_image_id: str


class ExecutionProfileRegistry:
    """catalog 的只读投影，并负责完整 controller 策略握手。"""

    def __init__(self, catalog: ProfileCatalog) -> None:
        self._catalog = catalog
        self._descriptors = MappingProxyType({
            profile.profile_id: ExecutionProfileDescriptor.from_definition(
                profile
            )
            for profile in catalog.profiles
        })

    @property
    def catalog_generation(self) -> str:
        return self._catalog.catalog_generation

    @property
    def policy_sha256(self) -> str:
        return self._catalog.policy_sha256

    @property
    def descriptors(self) -> Mapping[str, ExecutionProfileDescriptor]:
        return self._descriptors

    def descriptor(
        self,
        profile_id: str,
        *,
        require_grantable: bool = True,
    ) -> ExecutionProfileDescriptor:
        try:
            descriptor = self._descriptors[profile_id]
        except KeyError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 执行 Profile 无效",
            ) from exc
        if require_grantable and not descriptor.grantable:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 执行 Profile 当前不可授权",
            )
        return descriptor

    def policy_matches_controller(
        self,
        controller_data: Mapping[str, Any],
    ) -> bool:
        generation = str(controller_data.get("catalog_generation") or "")
        policy_sha256 = str(controller_data.get("policy_sha256") or "")
        return (
            hmac.compare_digest(generation, self.catalog_generation)
            and hmac.compare_digest(policy_sha256, self.policy_sha256)
        )

    def require_controller_profile(
        self,
        controller_data: Mapping[str, Any],
        profile_id: str,
    ) -> ControllerProfileState:
        descriptor = self.descriptor(profile_id)
        if not self.policy_matches_controller(controller_data):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Server 与 sandboxd 的完整 Profile 策略不一致",
            )
        raw_profiles = controller_data.get("profiles")
        if not isinstance(raw_profiles, Mapping):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "sandboxd 未返回 Profile readiness",
            )
        raw_state = raw_profiles.get(profile_id)
        if not isinstance(raw_state, Mapping):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "sandboxd 未返回目标 Profile readiness",
            )
        if raw_state.get("ready") is not True:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "目标 Sandbox Profile 尚未就绪",
            )
        execution_mode = str(raw_state.get("execution_mode") or "")
        image_id = str(raw_state.get("image_id") or "").lower()
        apparmor_profile = str(raw_state.get("apparmor_profile") or "")
        network_policy_id = str(
            raw_state.get("network_policy_id") or ""
        )
        network_proxy_image_id = str(
            raw_state.get("network_proxy_image_id") or ""
        ).lower()
        proxy_image_matches = (
            network_proxy_image_id
            in descriptor.network_proxy_image_allowlist
            if descriptor.network_proxy_image_allowlist
            else network_proxy_image_id == ""
        )
        if (
            execution_mode != descriptor.execution_mode
            or image_id not in descriptor.image_allowlist
            or apparmor_profile != descriptor.apparmor_profile
            or network_policy_id != descriptor.network_policy_id
            or not proxy_image_matches
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "sandboxd Profile 运行时状态与 canonical catalog 不一致",
            )
        return ControllerProfileState(
            profile_id=profile_id,
            execution_mode=execution_mode,
            image_id=image_id,
            apparmor_profile=apparmor_profile,
            network_policy_id=network_policy_id,
            network_proxy_image_id=network_proxy_image_id,
        )


def load_execution_profile_registry(
    path: str | Path | None = None,
) -> ExecutionProfileRegistry:
    selected = path
    if selected is None:
        selected = os.environ.get(
            "NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE",
            os.fspath(DEFAULT_PROFILE_MANIFEST_PATH),
        )
    try:
        catalog = load_profile_catalog(selected)
    except ProfileCatalogError as exc:
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "Sandbox Profile catalog 不可用",
        ) from exc
    return ExecutionProfileRegistry(catalog)


__all__ = [
    "ControllerProfileState",
    "ExecutionProfileDescriptor",
    "ExecutionProfileRegistry",
    "load_execution_profile_registry",
]
