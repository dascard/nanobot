"""为 developer Lease 管理独立内部网络与固定 Squid 出口代理。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.profile_catalog import (
    DEVELOPER_NETWORK_POLICY_ID,
    ExecutionProfileDefinition,
)
from sandboxd.config import SandboxdConfig
from sandboxd.lease_store import (
    validate_controller_epoch,
    validate_lease_id,
)


MANAGED_LABEL = "com.nanobot.sandbox"
MANAGED_BY_LABEL = "com.nanobot.managed-by"
LEASE_LABEL = "com.nanobot.lease-id"
POLICY_ID_LABEL = "com.nanobot.network-policy-id"
POLICY_SHA_LABEL = "com.nanobot.policy-sha256"
CONTROLLER_EPOCH_LABEL = "com.nanobot.controller-epoch"
NETWORK_ROLE_LABEL = "com.nanobot.network-role"

LEASE_NETWORK_PREFIX = "nanobot-sbx-net-"
PROXY_CONTAINER_PREFIX = "nanobot-sbx-proxy-"
PROXY_ROLE = "egress-proxy"
LEASE_NETWORK_ROLE = "lease-internal"
UPLINK_NETWORK_ROLE = "egress-uplink"

PROXY_USER = "13:13"
PROXY_MEMORY_BYTES = 256 * 1024 * 1024
PROXY_NANO_CPUS = 500_000_000
PROXY_PIDS_LIMIT = 128
PROXY_TMPFS = {
    "/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=16m",
    "/run": "rw,nosuid,nodev,noexec,mode=0755,size=4m",
    "/var/log/squid": "rw,nosuid,nodev,noexec,mode=0755,size=16m",
    "/var/spool/squid": "rw,nosuid,nodev,noexec,mode=0755,size=16m",
}
PROXY_SYSCTLS = {
    "net.ipv4.ip_forward": "0",
    "net.ipv6.conf.all.forwarding": "0",
}


@dataclass(frozen=True, slots=True)
class LeaseNetworkAttachment:
    """只能由 NetworkPolicyManager 生成的容器网络参数。"""

    lease_id: str
    policy_id: str
    network_name: str
    proxy_host: str
    proxy_port: int
    controller_epoch: str
    policy_sha256: str

    @property
    def proxy_url(self) -> str:
        return f"http://{self.proxy_host}:{self.proxy_port}"


def _labels(value: Any) -> dict[str, str]:
    try:
        value.reload()
        raw = value.attrs.get("Labels")
        if raw is None:
            raw = value.attrs.get("Config", {}).get("Labels")
        return {
            str(key): str(item)
            for key, item in dict(raw or {}).items()
        }
    except Exception:
        return {}


def _container_running(container: Any) -> bool:
    try:
        container.reload()
        return bool(container.attrs.get("State", {}).get("Running"))
    except Exception:
        return False


class NetworkPolicyManager:
    """只按 canonical Profile 创建固定网络拓扑，不接受调用方 Docker 参数。"""

    def __init__(
        self,
        config: SandboxdConfig,
        *,
        docker_client: Any,
        clock: Any = time.time,
    ) -> None:
        self.config = config.validated()
        self.client = docker_client
        self.clock = clock

    @staticmethod
    def _lease_network_name(lease_id: str) -> str:
        return f"{LEASE_NETWORK_PREFIX}{validate_lease_id(lease_id)}"

    @staticmethod
    def _proxy_name(lease_id: str) -> str:
        return f"{PROXY_CONTAINER_PREFIX}{validate_lease_id(lease_id)}"

    @property
    def _policy_sha256(self) -> str:
        catalog = self.config.profile_catalog
        if catalog is None:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Profile catalog 不可用",
            )
        return catalog.policy_sha256

    def _require_proxy_image(
        self,
        profile: ExecutionProfileDefinition,
    ) -> Any:
        if not self.config.developer_network_allowed:
            raise SandboxServiceError(
                SandboxErrorCode.SANDBOX_NOT_ENABLED,
                "开发型 Sandbox 网络出口硬上限未允许",
            )
        if (
            profile.network_policy_id != DEVELOPER_NETWORK_POLICY_ID
            or not profile.network_proxy_image_reference
            or not profile.network_proxy_image_allowlist
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 网络代理策略未完整配置",
            )
        try:
            image = self.client.images.get(
                profile.network_proxy_image_reference
            )
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "固定 Sandbox 出口代理镜像不存在，sandboxd 不会自动拉取",
            ) from exc
        image_id = str(getattr(image, "id", "") or "").lower()
        image_config = dict(
            getattr(image, "attrs", {}).get("Config") or {}
        )
        image_labels = {
            str(key): str(value)
            for key, value in dict(image_config.get("Labels") or {}).items()
        }
        exposed_ports = set(
            str(key)
            for key in dict(image_config.get("ExposedPorts") or {})
        )
        if (
            image_id not in profile.network_proxy_image_allowlist
            or str(image_config.get("User") or "") != "13:13"
            or list(image_config.get("Entrypoint") or [])
            != ["/usr/sbin/squid"]
            or image_labels.get("com.nanobot.egress-policy-id")
            != profile.network_policy_id
            or exposed_ports - {f"{profile.network_proxy_port}/tcp"}
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 出口代理镜像与 canonical 网络策略不一致",
            )
        return image

    def require_profile_ready(
        self,
        profile: ExecutionProfileDefinition,
    ) -> dict[str, str]:
        """验证 Profile 的网络依赖；不创建任何 Docker 对象。"""

        if profile.network_policy_id == "none":
            return {
                "network_policy_id": "none",
                "proxy_image_id": "",
            }
        if profile.network_policy_id != DEVELOPER_NETWORK_POLICY_ID:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 网络策略尚无安全实现",
            )
        image = self._require_proxy_image(profile)
        return {
            "network_policy_id": profile.network_policy_id,
            "proxy_image_id": str(image.id).lower(),
        }

    def _network_candidates(self, name: str) -> list[Any]:
        try:
            candidates = list(self.client.networks.list(names=[name]))
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 无法查询 Sandbox 网络",
                retryable=True,
                stop=False,
            ) from exc
        return [
            network
            for network in candidates
            if str(getattr(network, "name", "") or "") == name
        ]

    def _owned_network(
        self,
        name: str,
        *,
        role: str,
        lease_id: str = "",
        require_current_policy: bool = True,
    ) -> Any | None:
        candidates = self._network_candidates(name)
        if len(candidates) > 1:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 中存在重复 Sandbox 网络",
            )
        if not candidates:
            return None
        network = candidates[0]
        labels = _labels(network)
        expected = {
            MANAGED_LABEL: "true",
            MANAGED_BY_LABEL: "sandboxd",
            NETWORK_ROLE_LABEL: role,
        }
        if lease_id:
            expected[LEASE_LABEL] = validate_lease_id(lease_id)
            if require_current_policy:
                expected[POLICY_SHA_LABEL] = self._policy_sha256
        if any(labels.get(key) != value for key, value in expected.items()):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "同名 Docker 网络不属于当前 Sandbox 策略",
            )
        try:
            network.reload()
            attrs = dict(network.attrs or {})
            internal = bool(attrs.get("Internal"))
            options = {
                str(key): str(value)
                for key, value in dict(attrs.get("Options") or {}).items()
            }
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 无法读取 Sandbox 网络状态",
            ) from exc
        if internal is not (role == LEASE_NETWORK_ROLE):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 网络 internal 属性漂移",
            )
        expected_options = (
            {
                "com.docker.network.bridge.enable_ip_masquerade": "false",
                "com.docker.network.bridge.inhibit_ipv4": "true",
            }
            if role == LEASE_NETWORK_ROLE
            else {
                "com.docker.network.bridge.enable_icc": "false",
                "com.docker.network.bridge.enable_ip_masquerade": "true",
                "com.docker.network.driver.mtu": str(
                    self.config.egress_network_mtu
                ),
            }
        )
        ipam_configs = list(
            dict(attrs.get("IPAM") or {}).get("Config") or []
        )
        lease_has_gateway = (
            role == LEASE_NETWORK_ROLE
            and any(
                str(dict(item or {}).get("Gateway") or "")
                for item in ipam_configs
            )
        )
        if (
            str(attrs.get("Driver") or "") != "bridge"
            or bool(attrs.get("Attachable"))
            or bool(attrs.get("EnableIPv6"))
            or any(
                options.get(key) != value
                for key, value in expected_options.items()
            )
            or lease_has_gateway
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 网络安全参数漂移",
            )
        return network

    def _ensure_uplink(self) -> Any:
        name = self.config.egress_uplink_network_name
        existing = self._owned_network(
            name,
            role=UPLINK_NETWORK_ROLE,
        )
        if existing is not None:
            return existing
        try:
            return self.client.networks.create(
                name,
                driver="bridge",
                internal=False,
                attachable=False,
                check_duplicate=True,
                enable_ipv6=False,
                labels={
                    MANAGED_LABEL: "true",
                    MANAGED_BY_LABEL: "sandboxd",
                    NETWORK_ROLE_LABEL: UPLINK_NETWORK_ROLE,
                },
                options={
                    "com.docker.network.bridge.enable_icc": "false",
                    "com.docker.network.bridge.enable_ip_masquerade": "true",
                    "com.docker.network.driver.mtu": str(
                        self.config.egress_network_mtu
                    ),
                },
            )
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 无法创建 Sandbox 出口网络",
                retryable=True,
                stop=False,
            ) from exc

    def _ensure_lease_network(
        self,
        *,
        lease_id: str,
        controller_epoch: str,
    ) -> Any:
        name = self._lease_network_name(lease_id)
        existing = self._owned_network(
            name,
            role=LEASE_NETWORK_ROLE,
            lease_id=lease_id,
        )
        if existing is not None:
            labels = _labels(existing)
            if (
                labels.get(CONTROLLER_EPOCH_LABEL) != controller_epoch
                or labels.get(POLICY_ID_LABEL)
                != DEVELOPER_NETWORK_POLICY_ID
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "Sandbox Lease 网络 epoch 或策略已失效",
                )
            return existing
        try:
            return self.client.networks.create(
                name,
                driver="bridge",
                internal=True,
                attachable=False,
                check_duplicate=True,
                enable_ipv6=False,
                labels={
                    MANAGED_LABEL: "true",
                    MANAGED_BY_LABEL: "sandboxd",
                    LEASE_LABEL: lease_id,
                    POLICY_ID_LABEL: DEVELOPER_NETWORK_POLICY_ID,
                    POLICY_SHA_LABEL: self._policy_sha256,
                    CONTROLLER_EPOCH_LABEL: controller_epoch,
                    NETWORK_ROLE_LABEL: LEASE_NETWORK_ROLE,
                },
                options={
                    "com.docker.network.bridge.enable_ip_masquerade": "false",
                    "com.docker.network.bridge.inhibit_ipv4": "true",
                },
            )
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 无法创建 Sandbox Lease 内部网络",
                retryable=True,
                stop=False,
            ) from exc

    def _proxy_candidates(self, lease_id: str) -> list[Any]:
        lease_id = validate_lease_id(lease_id)
        try:
            candidates = self.client.containers.list(
                all=True,
                filters={
                    "name": self._proxy_name(lease_id),
                    "label": [
                        f"{MANAGED_LABEL}=true",
                        f"{MANAGED_BY_LABEL}=sandboxd",
                        f"{LEASE_LABEL}={lease_id}",
                        f"{NETWORK_ROLE_LABEL}={PROXY_ROLE}",
                    ],
                },
            )
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 无法查询 Sandbox 出口代理",
                retryable=True,
                stop=False,
            ) from exc
        return [
            container
            for container in candidates
            if str(getattr(container, "name", "") or "").lstrip("/")
            == self._proxy_name(lease_id)
        ]

    def _owned_proxy(
        self,
        lease_id: str,
        *,
        controller_epoch: str | None = None,
        require_current_policy: bool = True,
    ) -> Any | None:
        candidates = self._proxy_candidates(lease_id)
        if len(candidates) > 1:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 中存在重复 Sandbox 出口代理",
            )
        if not candidates:
            return None
        proxy = candidates[0]
        labels = _labels(proxy)
        expected = {
            MANAGED_LABEL: "true",
            MANAGED_BY_LABEL: "sandboxd",
            LEASE_LABEL: validate_lease_id(lease_id),
            NETWORK_ROLE_LABEL: PROXY_ROLE,
        }
        if require_current_policy:
            expected[POLICY_ID_LABEL] = DEVELOPER_NETWORK_POLICY_ID
            expected[POLICY_SHA_LABEL] = self._policy_sha256
        if controller_epoch is not None:
            expected[CONTROLLER_EPOCH_LABEL] = validate_controller_epoch(
                controller_epoch
            )
        if any(labels.get(key) != value for key, value in expected.items()):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "同名 Sandbox 出口代理归属或策略不一致",
            )
        return proxy

    @staticmethod
    def _container_networks(container: Any) -> set[str]:
        try:
            container.reload()
            return set(
                str(name)
                for name in dict(
                    container.attrs.get("NetworkSettings", {}).get(
                        "Networks"
                    )
                    or {}
                )
            )
        except Exception:
            return set()

    @staticmethod
    def _container_id(container: Any) -> str:
        try:
            container.reload()
            return str(
                getattr(container, "id", "")
                or container.attrs.get("Id")
                or ""
            )
        except Exception:
            return ""

    @staticmethod
    def _network_container_ids(network: Any) -> set[str]:
        try:
            network.reload()
            return {
                str(container_id)
                for container_id in dict(
                    network.attrs.get("Containers") or {}
                )
            }
        except Exception:
            return set()

    def _proxy_topology_valid(
        self,
        proxy: Any,
        *,
        profile: ExecutionProfileDefinition,
        lease_id: str,
    ) -> bool:
        try:
            proxy.reload()
            image_id = str(proxy.attrs.get("Image") or "").lower()
            config = dict(proxy.attrs.get("Config") or {})
            host_config = dict(proxy.attrs.get("HostConfig") or {})
            network_names = self._container_networks(proxy)
            mounts = list(proxy.attrs.get("Mounts") or [])
        except Exception:
            return False
        security_options = {
            str(value)
            for value in list(host_config.get("SecurityOpt") or [])
        }
        cap_drop = {
            str(value).upper()
            for value in list(host_config.get("CapDrop") or [])
        }
        restart_policy = dict(
            host_config.get("RestartPolicy") or {}
        )
        return bool(
            _container_running(proxy)
            and image_id in profile.network_proxy_image_allowlist
            and str(config.get("User") or "") == PROXY_USER
            and list(config.get("Entrypoint") or [])
            == ["/usr/sbin/squid"]
            and list(config.get("Cmd") or [])
            == ["-N", "-f", "/etc/squid/squid.conf"]
            and network_names
            == {
                self._lease_network_name(lease_id),
                self.config.egress_uplink_network_name,
            }
            and str(host_config.get("NetworkMode") or "")
            == self._lease_network_name(lease_id)
            and host_config.get("ReadonlyRootfs") is True
            and cap_drop == {"ALL"}
            and not list(host_config.get("CapAdd") or [])
            and security_options == {"no-new-privileges"}
            and not dict(host_config.get("PortBindings") or {})
            and host_config.get("PublishAllPorts") is not True
            and host_config.get("Privileged") is not True
            and not list(host_config.get("Binds") or [])
            and not mounts
            and not list(host_config.get("Devices") or [])
            and not list(host_config.get("DeviceRequests") or [])
            and not list(host_config.get("ExtraHosts") or [])
            and not list(host_config.get("Dns") or [])
            and str(host_config.get("PidMode") or "") == ""
            and str(host_config.get("IpcMode") or "") == "private"
            and dict(host_config.get("Sysctls") or {}) == PROXY_SYSCTLS
            and int(host_config.get("PidsLimit") or 0)
            == PROXY_PIDS_LIMIT
            and int(host_config.get("Memory") or 0)
            == PROXY_MEMORY_BYTES
            and int(host_config.get("MemorySwap") or 0)
            == PROXY_MEMORY_BYTES
            and int(host_config.get("NanoCpus") or 0)
            == PROXY_NANO_CPUS
            and host_config.get("OomKillDisable") is not True
            and host_config.get("Init") is not True
            and dict(host_config.get("Tmpfs") or {}) == PROXY_TMPFS
            and str(restart_policy.get("Name") or "") == "no"
        )

    @staticmethod
    def _safe_remove_proxy(proxy: Any) -> bool:
        labels = _labels(proxy)
        if (
            labels.get(MANAGED_LABEL) != "true"
            or labels.get(MANAGED_BY_LABEL) != "sandboxd"
            or labels.get(NETWORK_ROLE_LABEL) != PROXY_ROLE
        ):
            return False
        try:
            proxy.remove(force=True, v=True)
            return True
        except Exception:
            return False

    def _create_proxy(
        self,
        *,
        profile: ExecutionProfileDefinition,
        image: Any,
        lease_id: str,
        controller_epoch: str,
        lease_network: Any,
        uplink: Any,
    ) -> Any:
        name = self._proxy_name(lease_id)
        proxy = None
        try:
            proxy = self.client.containers.create(
                image=str(image.id).lower(),
                name=name,
                command=["-N", "-f", "/etc/squid/squid.conf"],
                entrypoint=["/usr/sbin/squid"],
                user="13:13",
                hostname=name,
                network=str(lease_network.name),
                detach=True,
                stdin_open=False,
                tty=False,
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                privileged=False,
                pids_limit=PROXY_PIDS_LIMIT,
                mem_limit=PROXY_MEMORY_BYTES,
                memswap_limit=PROXY_MEMORY_BYTES,
                nano_cpus=PROXY_NANO_CPUS,
                oom_kill_disable=False,
                init=False,
                ipc_mode="private",
                tmpfs=PROXY_TMPFS,
                sysctls=PROXY_SYSCTLS,
                restart_policy={"Name": "no"},
                log_config={
                    "type": "local",
                    "config": {
                        "max-size": "1m",
                        "max-file": "1",
                        "compress": "false",
                    },
                },
                labels={
                    MANAGED_LABEL: "true",
                    MANAGED_BY_LABEL: "sandboxd",
                    LEASE_LABEL: lease_id,
                    POLICY_ID_LABEL: profile.network_policy_id,
                    POLICY_SHA_LABEL: self._policy_sha256,
                    CONTROLLER_EPOCH_LABEL: controller_epoch,
                    NETWORK_ROLE_LABEL: PROXY_ROLE,
                },
            )
            uplink.connect(proxy)
            proxy.start()
            if not self._proxy_topology_valid(
                proxy,
                profile=profile,
                lease_id=lease_id,
            ):
                raise RuntimeError("proxy security topology invalid")
            return proxy
        except Exception as exc:
            if proxy is not None:
                self._safe_remove_proxy(proxy)
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 无法启动固定 Sandbox 出口代理",
                retryable=True,
                stop=False,
            ) from exc

    def prepare(
        self,
        profile: ExecutionProfileDefinition,
        *,
        lease_id: str,
        controller_epoch: str,
    ) -> LeaseNetworkAttachment | None:
        """在创建 Sandbox 前准备代理和 Lease 内部网络。"""

        lease_id = validate_lease_id(lease_id)
        controller_epoch = validate_controller_epoch(controller_epoch)
        if profile.network_policy_id == "none":
            return None
        image = self._require_proxy_image(profile)
        uplink = self._ensure_uplink()
        lease_network = self._ensure_lease_network(
            lease_id=lease_id,
            controller_epoch=controller_epoch,
        )
        proxy = self._owned_proxy(
            lease_id,
            controller_epoch=controller_epoch,
        )
        if proxy is not None and not self._proxy_topology_valid(
            proxy,
            profile=profile,
            lease_id=lease_id,
        ):
            if not self._safe_remove_proxy(proxy):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "失效的 Sandbox 出口代理无法安全回收",
                )
            proxy = None
        if proxy is None:
            proxy = self._create_proxy(
                profile=profile,
                image=image,
                lease_id=lease_id,
                controller_epoch=controller_epoch,
                lease_network=lease_network,
                uplink=uplink,
            )
        return LeaseNetworkAttachment(
            lease_id=lease_id,
            policy_id=profile.network_policy_id,
            network_name=str(lease_network.name),
            proxy_host=self._proxy_name(lease_id),
            proxy_port=profile.network_proxy_port,
            controller_epoch=controller_epoch,
            policy_sha256=self._policy_sha256,
        )

    def require_lease_topology(
        self,
        profile: ExecutionProfileDefinition,
        *,
        lease_id: str,
        controller_epoch: str,
        sandbox_container: Any,
    ) -> None:
        """在复用 Lease 或启动 Exec 前重新验证完整网络拓扑。"""

        lease_id = validate_lease_id(lease_id)
        controller_epoch = validate_controller_epoch(controller_epoch)
        try:
            sandbox_container.reload()
            host_config = dict(
                sandbox_container.attrs.get("HostConfig") or {}
            )
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "无法读取 Sandbox Lease 网络事实",
            ) from exc
        sandbox_networks = self._container_networks(sandbox_container)
        if profile.network_policy_id == "none":
            if (
                str(host_config.get("NetworkMode") or "") != "none"
                or sandbox_networks - {"none"}
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "无网络 Sandbox Lease 的网络事实已漂移",
                )
            return
        self._require_proxy_image(profile)
        network = self._owned_network(
            self._lease_network_name(lease_id),
            role=LEASE_NETWORK_ROLE,
            lease_id=lease_id,
        )
        uplink = self._owned_network(
            self.config.egress_uplink_network_name,
            role=UPLINK_NETWORK_ROLE,
        )
        proxy = self._owned_proxy(
            lease_id,
            controller_epoch=controller_epoch,
        )
        network_container_ids = (
            self._network_container_ids(network)
            if network is not None
            else set()
        )
        expected_container_ids = {
            self._container_id(sandbox_container),
            self._container_id(proxy) if proxy is not None else "",
        }
        if (
            network is None
            or uplink is None
            or _labels(network).get(CONTROLLER_EPOCH_LABEL)
            != controller_epoch
            or sandbox_networks != {self._lease_network_name(lease_id)}
            or "" in expected_container_ids
            or network_container_ids != expected_container_ids
            or proxy is None
            or not self._proxy_topology_valid(
                proxy,
                profile=profile,
                lease_id=lease_id,
            )
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox Lease 网络拓扑不满足隔离策略",
            )

    def cleanup(self, lease_id: str) -> bool:
        """只删除精确 Lease 的代理与内部网络，保留共享出口网络。"""

        lease_id = validate_lease_id(lease_id)
        changed = False
        proxy = self._owned_proxy(
            lease_id,
            require_current_policy=False,
        )
        if proxy is not None:
            if not self._safe_remove_proxy(proxy):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Sandbox 出口代理无法安全回收",
                    retryable=True,
                    stop=False,
                )
            changed = True
        network = self._owned_network(
            self._lease_network_name(lease_id),
            role=LEASE_NETWORK_ROLE,
            lease_id=lease_id,
            require_current_policy=False,
        )
        if network is not None:
            try:
                network.remove()
            except Exception as exc:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Sandbox Lease 内部网络无法安全回收",
                    retryable=True,
                    stop=False,
                ) from exc
            changed = True
        return changed

    def cleanup_orphans(self, active_lease_ids: set[str]) -> list[str]:
        """周期清理没有对应 Lease 容器的受管代理和内部网络。"""

        active = {
            validate_lease_id(lease_id)
            for lease_id in active_lease_ids
        }
        candidates: set[str] = set()
        try:
            proxies = self.client.containers.list(
                all=True,
                filters={
                    "label": [
                        f"{MANAGED_LABEL}=true",
                        f"{MANAGED_BY_LABEL}=sandboxd",
                        f"{NETWORK_ROLE_LABEL}={PROXY_ROLE}",
                    ],
                },
            )
            networks = self.client.networks.list(
                names=[LEASE_NETWORK_PREFIX],
            )
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 无法枚举孤儿 Sandbox 网络对象",
                retryable=True,
                stop=False,
            ) from exc
        for value in [*proxies, *networks]:
            labels = _labels(value)
            if (
                labels.get(MANAGED_LABEL) == "true"
                and labels.get(MANAGED_BY_LABEL) == "sandboxd"
                and labels.get(NETWORK_ROLE_LABEL)
                in {PROXY_ROLE, LEASE_NETWORK_ROLE}
            ):
                lease_id = str(labels.get(LEASE_LABEL) or "")
                try:
                    candidates.add(validate_lease_id(lease_id))
                except SandboxServiceError:
                    continue
        cleaned: list[str] = []
        for lease_id in sorted(candidates - active):
            if self.cleanup(lease_id):
                cleaned.append(lease_id)
        return cleaned


__all__ = [
    "CONTROLLER_EPOCH_LABEL",
    "LEASE_NETWORK_PREFIX",
    "LeaseNetworkAttachment",
    "NetworkPolicyManager",
    "PROXY_CONTAINER_PREFIX",
]
