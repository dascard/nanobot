from __future__ import annotations

from copy import deepcopy
import json
import os
import socket
import time
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.profile_catalog import (
    DEFAULT_PROFILE_MANIFEST_PATH,
    DEVELOPER_NETWORK_POLICY_ID,
    DEVELOPER_REQUIRED_DENIED_CIDRS,
    DEVELOPER_REQUIRED_DOMAINS,
    load_profile_catalog,
    parse_profile_catalog,
)
from sandboxd.config import SandboxdConfig
from sandboxd.network_policy import (
    LEASE_LABEL,
    LEASE_NETWORK_ROLE,
    MANAGED_BY_LABEL,
    MANAGED_LABEL,
    NETWORK_ROLE_LABEL,
    POLICY_SHA_LABEL,
    PROXY_DNS_SERVERS,
    PROXY_MEMORY_BYTES,
    PROXY_NANO_CPUS,
    PROXY_PIDS_LIMIT,
    PROXY_TMPFS,
    PROXY_USER,
    PROXY_SYSCTLS,
    UPLINK_NETWORK_ROLE,
    NetworkPolicyManager,
)


ROOT = Path(__file__).resolve().parents[1]
PROXY_IMAGE_ID = (
    "sha256:05a5bbc3966ef22b15a0fa708722cf5a"
    "d730e8266473ab35ecfc932fb1b4e2ed"
)
DEVELOPER_IMAGE_ID = (
    "sha256:ac848b5823e5435115d3a1be0e6467b"
    "67e3b701ccd0fc6e89430ae820f0c44ba"
)


def _config(
    tmp_path: Path,
    *,
    network_allowed: bool,
    uplink_name: str = "nanobot-sbx-egress-uplink-test",
) -> SandboxdConfig:
    manifest_path = Path(os.environ.get(
        "NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE",
        os.fspath(DEFAULT_PROFILE_MANIFEST_PATH),
    ))
    catalog = load_profile_catalog(manifest_path)
    if not catalog.profile("developer").network_proxy_image_allowlist:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        developer = next(
            profile
            for profile in raw["profiles"]
            if profile["profile_id"] == "developer"
        )
        developer["network_proxy_image_allowlist"] = [PROXY_IMAGE_ID]
        catalog = parse_profile_catalog(raw)
    return SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=tmp_path / "token",
        client_token_path=tmp_path / "run" / "client.token",
        admin_token_file=tmp_path / "admin-token",
        admin_client_token_path=tmp_path / "run" / "admin-client.token",
        quota_helper_path=tmp_path / "quota-helper",
        profile_manifest_path=manifest_path,
        profile_catalog=catalog,
        developer_network_allowed=network_allowed,
        egress_uplink_network_name=uplink_name,
        disk_min_free_bytes=0,
    ).validated()


def test_canonical_developer_policy_binds_proxy_domains_ports_and_cidrs():
    catalog = load_profile_catalog()
    profile = catalog.profile("developer")

    assert profile.network_policy_id == DEVELOPER_NETWORK_POLICY_ID
    assert set(profile.network_allowlist) == DEVELOPER_REQUIRED_DOMAINS
    assert DEVELOPER_REQUIRED_DENIED_CIDRS <= set(
        profile.network_denied_cidrs
    )
    assert set(profile.network_destination_ports) == {80, 443}
    assert set(profile.network_connect_ports) == {443}
    assert profile.network_proxy_image_reference == (
        "nanobot-sandbox-egress-proxy:2026.07.25"
    )
    assert profile.network_proxy_image_allowlist == ()
    assert profile.network_proxy_port == 3128

    restricted = catalog.profile("restricted")
    assert restricted.network_policy_id == "none"
    assert restricted.network_allowlist == ()
    assert restricted.network_proxy_image_allowlist == ()


def test_squid_policy_matches_catalog_and_checks_domain_before_resolved_ip():
    profile = load_profile_catalog().profile("developer")
    config = (
        ROOT
        / "docker"
        / "sandbox"
        / "egress-proxy"
        / "squid.conf"
    ).read_text(encoding="utf-8")

    for domain in profile.network_allowlist:
        assert f"    {domain}" in config
    for cidr in profile.network_denied_cidrs:
        assert f"    {cidr}" in config
    assert "acl Safe_ports port 80 443" in config
    assert "acl SSL_ports port 443" in config
    assert config.index("http_access deny !allowed_domains") < config.index(
        "http_access deny denied_ipv4"
    )
    assert config.index("http_access deny denied_ipv4") < config.index(
        "http_access allow all"
    )
    assert config.index("http_access deny denied_ipv6") < config.index(
        "http_access allow all"
    )
    assert "http_access deny manager" in config


def test_proxy_dockerfile_uses_fixed_squid_digest_and_non_root_user():
    dockerfile = (
        ROOT
        / "docker"
        / "sandbox"
        / "egress-proxy"
        / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert (
        "FROM ubuntu/squid:6.6-24.04_beta@"
        "sha256:6a097f68bae708cedbabd6188d68c7e2"
        "e7a38cedd05a176e1cc0ba29e3bbe029"
    ) in dockerfile
    assert "USER 13:13" in dockerfile
    assert 'ENTRYPOINT ["/usr/sbin/squid"]' in dockerfile
    assert ":latest" not in dockerfile


class _NoDockerAccess:
    class _Images:
        @staticmethod
        def get(_reference):
            pytest.fail("硬开关关闭时不得读取代理镜像")

    images = _Images()


def test_sandboxd_network_hard_switch_fails_before_docker_access(tmp_path):
    config = _config(tmp_path, network_allowed=False)
    manager = NetworkPolicyManager(
        config,
        docker_client=_NoDockerAccess(),
    )

    with pytest.raises(SandboxServiceError) as error:
        manager.require_profile_ready(config.profile("developer"))

    assert error.value.code is SandboxErrorCode.SANDBOX_NOT_ENABLED


def _network_mock(name: str, attrs: dict[str, object]) -> Mock:
    network = Mock()
    network.name = name
    network.attrs = attrs
    return network


def _manager_with_network(
    tmp_path: Path,
    network: Mock,
) -> NetworkPolicyManager:
    client = Mock()
    client.networks.list.return_value = [network]
    return NetworkPolicyManager(
        _config(tmp_path, network_allowed=True),
        docker_client=client,
    )


@pytest.mark.parametrize(
    ("option_name", "drifted_value"),
    [
        ("com.docker.network.bridge.enable_icc", "true"),
        ("com.docker.network.bridge.enable_ip_masquerade", "false"),
        ("com.docker.network.driver.mtu", "1500"),
    ],
)
def test_reused_uplink_rejects_bridge_security_option_drift(
    tmp_path,
    option_name,
    drifted_value,
):
    name = "nanobot-sbx-egress-uplink-test"
    network = _network_mock(
        name,
        {
            "Labels": {
                MANAGED_LABEL: "true",
                MANAGED_BY_LABEL: "sandboxd",
                NETWORK_ROLE_LABEL: UPLINK_NETWORK_ROLE,
            },
            "Internal": False,
            "Driver": "bridge",
            "Attachable": False,
            "EnableIPv6": False,
            "Options": {
                "com.docker.network.bridge.enable_icc": "false",
                "com.docker.network.bridge.enable_ip_masquerade": "true",
                "com.docker.network.driver.mtu": "1450",
            },
            "IPAM": {"Config": [{"Gateway": "172.18.0.1"}]},
        },
    )
    manager = _manager_with_network(tmp_path, network)
    assert (
        manager._owned_network(name, role=UPLINK_NETWORK_ROLE)
        is network
    )

    network.attrs["Options"][option_name] = drifted_value

    with pytest.raises(SandboxServiceError) as error:
        manager._owned_network(name, role=UPLINK_NETWORK_ROLE)

    assert error.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    [
        ("com.docker.network.bridge.enable_ip_masquerade", "true"),
        ("com.docker.network.bridge.inhibit_ipv4", "false"),
        ("Gateway", "172.19.0.1"),
    ],
)
def test_reused_lease_network_rejects_host_route_drift(
    tmp_path,
    field,
    drifted_value,
):
    lease_id = "sbxlease_network_policy_test"
    name = f"nanobot-sbx-net-{lease_id}"
    manager = NetworkPolicyManager(
        _config(tmp_path, network_allowed=True),
        docker_client=Mock(),
    )
    network = _network_mock(
        name,
        {
            "Labels": {
                MANAGED_LABEL: "true",
                MANAGED_BY_LABEL: "sandboxd",
                NETWORK_ROLE_LABEL: LEASE_NETWORK_ROLE,
                LEASE_LABEL: lease_id,
                POLICY_SHA_LABEL: manager._policy_sha256,
            },
            "Internal": True,
            "Driver": "bridge",
            "Attachable": False,
            "EnableIPv6": False,
            "Options": {
                "com.docker.network.bridge.enable_ip_masquerade": "false",
                "com.docker.network.bridge.inhibit_ipv4": "true",
            },
            "IPAM": {"Config": [{}]},
        },
    )
    manager.client.networks.list.return_value = [network]
    assert (
        manager._owned_network(
            name,
            role=LEASE_NETWORK_ROLE,
            lease_id=lease_id,
        )
        is network
    )

    if field == "Gateway":
        network.attrs["IPAM"]["Config"][0][field] = drifted_value
    else:
        network.attrs["Options"][field] = drifted_value

    with pytest.raises(SandboxServiceError) as error:
        manager._owned_network(
            name,
            role=LEASE_NETWORK_ROLE,
            lease_id=lease_id,
        )

    assert error.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE


def _valid_proxy_attrs(
    manager: NetworkPolicyManager,
    lease_id: str,
) -> dict[str, object]:
    lease_network = manager._lease_network_name(lease_id)
    return {
        "Image": PROXY_IMAGE_ID,
        "State": {"Running": True},
        "Config": {
            "User": PROXY_USER,
            "Entrypoint": ["/usr/sbin/squid"],
            "Cmd": ["-N", "-f", "/etc/squid/squid.conf"],
        },
        "HostConfig": {
            "NetworkMode": lease_network,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "CapAdd": [],
            "SecurityOpt": ["no-new-privileges"],
            "PortBindings": {},
            "PublishAllPorts": False,
            "Privileged": False,
            "Binds": [],
            "Devices": [],
            "DeviceRequests": [],
            "ExtraHosts": [],
            "Dns": list(PROXY_DNS_SERVERS),
            "PidMode": "",
            "IpcMode": "private",
            "Sysctls": PROXY_SYSCTLS,
            "PidsLimit": PROXY_PIDS_LIMIT,
            "Memory": PROXY_MEMORY_BYTES,
            "MemorySwap": PROXY_MEMORY_BYTES,
            "NanoCpus": PROXY_NANO_CPUS,
            "OomKillDisable": False,
            "Init": False,
            "Tmpfs": PROXY_TMPFS,
            "RestartPolicy": {"Name": "no"},
        },
        "NetworkSettings": {
            "Networks": {
                lease_network: {},
                manager.config.egress_uplink_network_name: {},
            },
        },
        "Mounts": [],
    }


def test_reused_proxy_rejects_runtime_security_drift(tmp_path):
    manager = NetworkPolicyManager(
        _config(tmp_path, network_allowed=True),
        docker_client=Mock(),
    )
    profile = manager.config.profile("developer")
    lease_id = "sbxlease_proxy_policy_test"
    proxy = Mock()
    original = _valid_proxy_attrs(manager, lease_id)
    proxy.attrs = deepcopy(original)

    assert manager._proxy_topology_valid(
        proxy,
        profile=profile,
        lease_id=lease_id,
    )

    host_drift = [
        ("ReadonlyRootfs", False),
        ("CapDrop", []),
        ("SecurityOpt", []),
        ("Sysctls", {"net.ipv4.ip_forward": "1"}),
        ("Binds", ["/var/run/docker.sock:/var/run/docker.sock"]),
        ("Dns", ["8.8.8.8"]),
        ("Memory", 0),
        ("PidsLimit", 0),
    ]
    for field, value in host_drift:
        proxy.attrs = deepcopy(original)
        proxy.attrs["HostConfig"][field] = value
        assert not manager._proxy_topology_valid(
            proxy,
            profile=profile,
            lease_id=lease_id,
        ), field

    proxy.attrs = deepcopy(original)
    proxy.attrs["Config"]["User"] = "0:0"
    assert not manager._proxy_topology_valid(
        proxy,
        profile=profile,
        lease_id=lease_id,
    )

    proxy.attrs = deepcopy(original)
    proxy.attrs["Mounts"] = [{
        "Source": "/var/run/docker.sock",
        "Destination": "/var/run/docker.sock",
    }]
    assert not manager._proxy_topology_valid(
        proxy,
        profile=profile,
        lease_id=lease_id,
    )


def test_lease_topology_waits_for_docker_endpoint_membership_convergence(
    tmp_path,
):
    sleeper = Mock()
    manager = NetworkPolicyManager(
        _config(tmp_path, network_allowed=True),
        docker_client=Mock(),
        sleeper=sleeper,
    )
    profile = manager.config.profile("developer")
    lease_id = "sbxlease_topology_convergence_test"
    epoch = "sbxctl_" + "1" * 32
    lease_network_name = manager._lease_network_name(lease_id)

    sandbox = Mock()
    sandbox.id = "sandbox-container-id"
    sandbox.attrs = {
        "HostConfig": {"NetworkMode": lease_network_name},
        "NetworkSettings": {
            "Networks": {lease_network_name: {}},
        },
    }
    proxy = Mock()
    proxy.id = "proxy-container-id"
    lease_network = Mock()
    lease_network.attrs = {
        "Labels": {"com.nanobot.controller-epoch": epoch},
        "Containers": {},
    }
    reload_count = 0

    def reload_lease_network():
        nonlocal reload_count
        reload_count += 1
        container_ids = {proxy.id}
        if reload_count >= 3:
            container_ids.add(sandbox.id)
        lease_network.attrs["Containers"] = {
            container_id: {}
            for container_id in container_ids
        }

    lease_network.reload.side_effect = reload_lease_network
    uplink = Mock()
    manager._require_proxy_image = Mock(return_value=Mock())
    manager._owned_network = Mock(side_effect=lambda name, **_kwargs: (
        lease_network if name == lease_network_name else uplink
    ))
    manager._owned_proxy = Mock(return_value=proxy)
    manager._proxy_topology_valid = Mock(return_value=True)

    manager.require_lease_topology(
        profile,
        lease_id=lease_id,
        controller_epoch=epoch,
        sandbox_container=sandbox,
    )

    assert reload_count == 3
    sleeper.assert_called_once_with(0.05)


def test_server_network_hard_switch_cannot_be_overridden_by_database(
    db_session,
    monkeypatch,
):
    from core.database import SystemSetting
    from core.sandbox.access_policy import SandboxAccessPolicy
    from core.settings_service import settings
    from tests.test_sandbox_access_contracts import _grant

    _grant(
        db_session,
        session_id="network-hard-ceiling",
        execution_profile="developer",
    )
    db_session.add(SystemSetting(
        key="sandbox.developer_network_allowed",
        value="true",
    ))
    db_session.commit()
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_SESSION_EXECUTION_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED",
        "false",
    )
    settings.invalidate()

    denied = SandboxAccessPolicy(
        db_session,
        infrastructure_allowed=True,
    ).evaluate(
        "sandbox_exec",
        platform="qq",
        chat_type="private",
        session_id="private_network-hard-ceiling",
    )

    assert denied.allowed is False
    assert denied.code == "sandbox_not_enabled"

    workspace_allowed = SandboxAccessPolicy(
        db_session,
        infrastructure_allowed=True,
    ).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="private",
        session_id="private_network-hard-ceiling",
    )
    assert workspace_allowed.allowed is True

    monkeypatch.setenv(
        "NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED",
        "true",
    )
    settings.invalidate()
    allowed = SandboxAccessPolicy(
        db_session,
        infrastructure_allowed=True,
    ).evaluate(
        "sandbox_exec",
        platform="qq",
        chat_type="private",
        session_id="private_network-hard-ceiling",
    )
    assert allowed.allowed is True
    settings.invalidate()


@pytest.mark.skipif(
    os.environ.get("NANOBOT_RUN_DOCKER_TESTS") != "1",
    reason="需要显式启用真实 Docker 网络拒绝矩阵",
)
def test_real_docker_developer_egress_and_rejection_matrix(tmp_path):
    import docker
    from docker.types import IPAMConfig, IPAMPool

    client = docker.from_env()
    assert client.ping() is True

    suffix = uuid4().hex[:10]
    uplink_name = f"nanobot-sbx-egress-uplink-{suffix}"
    config = _config(
        tmp_path,
        network_allowed=True,
        uplink_name=uplink_name,
    )
    manager = NetworkPolicyManager(config, docker_client=client)
    profile = config.profile("developer")
    assert len(profile.image_allowlist) == 1
    assert len(profile.network_proxy_image_allowlist) == 1
    developer_image_id = profile.image_allowlist[0]
    proxy_image_id = profile.network_proxy_image_allowlist[0]
    assert client.images.get(profile.image_reference).id == developer_image_id
    assert (
        client.images.get(profile.network_proxy_image_reference).id
        == proxy_image_id
    )
    epoch = "sbxctl_" + suffix.ljust(32, "0")
    lease_a = f"sbxlease_p11a_{suffix}"
    lease_b = f"sbxlease_p11b_{suffix}"
    sandbox_name = f"nanobot-sbx-p11-client-{suffix}"
    other_name = f"nanobot-sbx-p11-service-{suffix}"
    rebind_name = f"nanobot-sbx-p11-rebind-{suffix}"
    redirect_name = f"nanobot-sbx-p11-redirect-{suffix}"
    other_network_name = f"nanobot-sbx-p11-other-{suffix}"
    public_network_name = f"nanobot-sbx-p11-public-{suffix}"

    containers: list[object] = []
    extra_networks: list[object] = []
    attachment_a = None
    attachment_b = None
    sandbox = None

    def remove_container(container):
        if container is None:
            return
        try:
            container.remove(force=True, v=True)
        except Exception:
            pass

    def exec_command(container, command: str):
        result = container.exec_run(
            ["/bin/bash", "-lc", command],
            demux=False,
        )
        output = result.output
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return int(result.exit_code), str(output)

    def assert_success(container, command: str):
        code, output = exec_command(container, command)
        if code != 0 and attachment_a is not None:
            try:
                proxy_logs = client.containers.get(
                    attachment_a.proxy_host
                ).logs(tail=100)
                if isinstance(proxy_logs, bytes):
                    proxy_logs = proxy_logs.decode(
                        "utf-8",
                        errors="replace",
                    )
                output += f"\n代理日志：\n{proxy_logs}"
            except Exception:
                pass
        assert code == 0, output
        return output

    def assert_failure(container, command: str):
        code, output = exec_command(container, command)
        assert code != 0, output
        return output

    try:
        attachment_a = manager.prepare(
            profile,
            lease_id=lease_a,
            controller_epoch=epoch,
        )
        attachment_b = manager.prepare(
            profile,
            lease_id=lease_b,
            controller_epoch=epoch,
        )
        assert attachment_a is not None
        assert attachment_b is not None

        workspace = tmp_path / "workspace"
        workspace.mkdir(mode=0o777)
        workspace.chmod(0o777)
        proxy_environment = {
            "HOME": "/runtime/home",
            "http_proxy": attachment_a.proxy_url,
            "https_proxy": attachment_a.proxy_url,
            "HTTP_PROXY": attachment_a.proxy_url,
            "HTTPS_PROXY": attachment_a.proxy_url,
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "no_proxy": "localhost,127.0.0.1,::1",
        }
        sandbox = client.containers.create(
            image=developer_image_id,
            name=sandbox_name,
            command=[
                "/bin/bash",
                "-lc",
                (
                    "trap 'exit 0' TERM INT; "
                    "while :; do sleep 3600 & wait $!; done"
                ),
            ],
            user="10001:10001",
            working_dir="/workspace",
            network=attachment_a.network_name,
            environment=proxy_environment,
            volumes={
                os.fspath(workspace): {
                    "bind": "/workspace",
                    "mode": "rw",
                },
            },
            read_only=True,
            cap_drop=["ALL"],
            security_opt=[
                "no-new-privileges",
                "apparmor=nanobot-sandbox-developer",
            ],
            privileged=False,
            init=True,
            pids_limit=256,
            mem_limit=1024 * 1024 * 1024,
            memswap_limit=1024 * 1024 * 1024,
            nano_cpus=1_000_000_000,
            tmpfs={
                "/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=64m",
                "/runtime": "rw,nosuid,nodev,mode=0777,size=256m",
            },
            labels={
                "com.nanobot.sandbox": "true",
                "com.nanobot.managed-by": "sandboxd-test",
            },
        )
        containers.append(sandbox)
        sandbox.start()
        sandbox.reload()
        assert sandbox.attrs["AppArmorProfile"] == (
            "nanobot-sandbox-developer"
        )
        manager.require_lease_topology(
            profile,
            lease_id=lease_a,
            controller_epoch=epoch,
            sandbox_container=sandbox,
        )
        assert set(sandbox.attrs["NetworkSettings"]["Networks"]) == {
            attachment_a.network_name
        }

        proxy = client.containers.get(attachment_a.proxy_host)
        proxy.reload()
        assert proxy.attrs["Config"]["User"] == "13:13"
        assert proxy.attrs["HostConfig"]["ReadonlyRootfs"] is True
        assert proxy.attrs["HostConfig"]["CapDrop"] == ["ALL"]
        assert proxy.attrs["HostConfig"]["PortBindings"] == {}
        assert proxy.attrs["HostConfig"]["Dns"] == list(
            PROXY_DNS_SERVERS
        )
        assert proxy.attrs["HostConfig"]["Sysctls"] == {
            "net.ipv4.ip_forward": "0",
            "net.ipv6.conf.all.forwarding": "0",
        }
        assert set(proxy.attrs["NetworkSettings"]["Networks"]) == {
            attachment_a.network_name,
            uplink_name,
        }
        uplink = client.networks.get(uplink_name)
        uplink.reload()
        assert uplink.attrs["Options"][
            "com.docker.network.driver.mtu"
        ] == "1450"

        lease_network = client.networks.get(attachment_a.network_name)
        lease_network.reload()
        assert lease_network.attrs["Internal"] is True
        assert (
            lease_network.attrs["Options"][
                "com.docker.network.bridge.inhibit_ipv4"
            ]
            == "true"
        )
        assert (
            lease_network.attrs["IPAM"]["Config"][0].get("Gateway", "")
            == ""
        )

        assert_success(
            sandbox,
            (
                "for attempt in 1 2 3; do "
                "rm -rf /tmp/repo; "
                "if timeout 75 git -c http.version=HTTP/1.1 "
                "-c http.lowSpeedLimit=1 -c http.lowSpeedTime=30 "
                "clone --depth 1 --single-branch --branch master "
                "https://github.com/octocat/Hello-World.git "
                "/tmp/repo; then "
                "git -C /tmp/repo status --short --branch && exit 0; "
                "fi; "
                "done; "
                "exit 1"
            ),
        )
        assert_success(
            sandbox,
            (
                "timeout 60 curl -fsSL "
                "https://codeload.github.com/octocat/Hello-World/"
                "tar.gz/refs/heads/master -o /tmp/repo.tar.gz && "
                "test -s /tmp/repo.tar.gz"
            ),
        )
        objects_status = assert_success(
            sandbox,
            (
                "timeout 30 curl -sS -o /dev/null "
                "-w '%{http_code}' https://objects.githubusercontent.com/"
            ),
        ).strip()
        assert objects_status != "000"
        assert_success(
            sandbox,
            (
                "timeout 60 curl -fsSL "
                "https://pypi.org/simple/pip/ -o /tmp/pip-index && "
                "test -s /tmp/pip-index"
            ),
        )
        assert_success(
            sandbox,
            (
                "mkdir -p /tmp/pip-download && "
                "timeout 120 python -m pip download --no-deps "
                "--dest /tmp/pip-download charset-normalizer==3.4.2 && "
                "test -n \"$(find /tmp/pip-download -type f -print -quit)\""
            ),
        )
        assert_success(
            sandbox,
            (
                "timeout 60 npm view npm version "
                "--registry=https://registry.npmjs.org "
                "> /tmp/npm-version && test -s /tmp/npm-version"
            ),
        )

        proxy_b = client.containers.get(attachment_b.proxy_host)
        proxy_b.reload()
        proxy_b_ip = proxy_b.attrs["NetworkSettings"]["Networks"][
            attachment_b.network_name
        ]["IPAddress"]

        other_network = client.networks.create(
            other_network_name,
            driver="bridge",
            internal=False,
            check_duplicate=True,
        )
        extra_networks.append(other_network)
        other = client.containers.create(
            image=developer_image_id,
            name=other_name,
            command=[
                "/usr/local/bin/python",
                "-m",
                "http.server",
                "8080",
            ],
            network=other_network_name,
            working_dir="/tmp",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            tmpfs={
                "/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=8m",
            },
        )
        containers.append(other)
        other.start()
        other.reload()
        other_ip = other.attrs["NetworkSettings"]["Networks"][
            other_network_name
        ]["IPAddress"]
        proxy.reload()
        proxy_ip = proxy.attrs["NetworkSettings"]["Networks"][
            attachment_a.network_name
        ]["IPAddress"]

        assert_failure(
            sandbox,
            (
                "env -u http_proxy -u https_proxy -u HTTP_PROXY "
                "-u HTTPS_PROXY -u NO_PROXY -u no_proxy "
                "timeout 8 curl -fsS --connect-timeout 3 "
                "https://github.com/"
            ),
        )
        github_ip = socket.gethostbyname("github.com")
        assert_failure(
            sandbox,
            (
                "env -u http_proxy -u https_proxy -u HTTP_PROXY "
                "-u HTTPS_PROXY -u NO_PROXY -u no_proxy "
                f"timeout 8 curl -kfsS --connect-timeout 3 "
                f"https://{github_ip}/"
            ),
        )
        assert_failure(
            sandbox,
            "timeout 8 curl -fsS https://example.com/",
        )
        assert_failure(
            sandbox,
            "timeout 8 curl -kfsS https://github.com:22/",
        )
        assert_failure(
            sandbox,
            (
                "env -u http_proxy -u https_proxy -u HTTP_PROXY "
                "-u HTTPS_PROXY -u NO_PROXY -u no_proxy "
                f"timeout 8 curl -fsS http://{proxy_b_ip}:3128/"
            ),
        )
        assert_failure(
            sandbox,
            (
                "env -u http_proxy -u https_proxy -u HTTP_PROXY "
                "-u HTTPS_PROXY -u NO_PROXY -u no_proxy "
                f"timeout 8 curl -fsS http://{other_ip}:8080/"
            ),
        )
        assert_failure(
            sandbox,
            (
                "env -u http_proxy -u https_proxy -u HTTP_PROXY "
                "-u HTTPS_PROXY -u NO_PROXY -u no_proxy "
                f"timeout 8 curl -fsS http://{proxy_ip}:3130/"
            ),
        )
        assert_failure(
            sandbox,
            (
                "env -u http_proxy -u https_proxy -u HTTP_PROXY "
                "-u HTTPS_PROXY -u NO_PROXY -u no_proxy "
                "timeout 8 curl -fsS http://127.0.0.1:9/"
            ),
        )
        assert_failure(
            sandbox,
            (
                "env -u http_proxy -u https_proxy -u HTTP_PROXY "
                "-u HTTPS_PROXY -u NO_PROXY -u no_proxy "
                "timeout 8 curl -fsS http://172.17.0.1:8000/"
            ),
        )
        for target in (
            "10.0.0.1",
            "172.16.0.1",
            "192.168.0.1",
            "169.254.169.254",
            "127.0.0.1",
            "2130706433",
            "0x7f000001",
            "[::1]",
            "[::ffff:127.0.0.1]",
        ):
            assert_failure(
                sandbox,
                (
                    "timeout 8 curl -gfsS --connect-timeout 3 "
                    f"http://{target}/"
                ),
            )
        loopback_trace = assert_failure(
            sandbox,
            (
                "timeout 8 curl --noproxy '' -x \"$http_proxy\" "
                "-fsv --connect-timeout 3 http://127.0.0.1/ "
                "-o /dev/null 2>&1"
            ),
        )
        assert "403" in loopback_trace

        rebind = client.containers.create(
            image=developer_image_id,
            name=rebind_name,
            command=[
                "/usr/local/bin/python",
                "-m",
                "http.server",
                "80",
            ],
            user="0:0",
            working_dir="/tmp",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            tmpfs={
                "/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=8m",
            },
        )
        containers.append(rebind)
        uplink.connect(rebind, aliases=["api.github.com"])
        rebind.start()
        rebind_trace = assert_failure(
            sandbox,
            (
                "timeout 8 curl -fsv --connect-timeout 3 "
                "http://api.github.com/ -o /dev/null 2>&1"
            ),
        )
        assert "403" in rebind_trace
        remove_container(rebind)
        containers.remove(rebind)

        public_network = client.networks.create(
            public_network_name,
            driver="bridge",
            internal=True,
            check_duplicate=True,
            ipam=IPAMConfig(pool_configs=[
                IPAMPool(
                    subnet="1.1.0.0/16",
                    gateway="1.1.0.1",
                ),
            ]),
            options={
                "com.docker.network.bridge.inhibit_ipv4": "true",
            },
        )
        extra_networks.append(public_network)
        redirect_program = r"""
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def serve_dns():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("0.0.0.0", 53))
    while True:
        query, address = server.recvfrom(512)
        offset = 12
        while query[offset]:
            offset += query[offset] + 1
        question_end = offset + 5
        query_type = int.from_bytes(
            query[offset + 1:offset + 3],
            "big",
        )
        answer_count = 1 if query_type == 1 else 0
        response = (
            query[:2]
            + b"\x81\x80\x00\x01"
            + answer_count.to_bytes(2, "big")
            + b"\x00\x00\x00\x00"
            + query[12:question_end]
        )
        if answer_count:
            response += (
                b"\xc0\x0c\x00\x01\x00\x01"
                + (0).to_bytes(4, "big")
                + b"\x00\x04"
                + socket.inet_aton("1.1.1.1")
            )
        server.sendto(response, address)


class RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://10.0.0.1/metadata")
        self.end_headers()

    def log_message(self, *_args):
        return


threading.Thread(target=serve_dns, daemon=True).start()
HTTPServer(("0.0.0.0", 80), RedirectHandler).serve_forever()
"""
        redirect = client.containers.create(
            image=developer_image_id,
            name=redirect_name,
            command=["/usr/local/bin/python", "-c", redirect_program],
            user="0:0",
            working_dir="/tmp",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            tmpfs={
                "/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=8m",
            },
        )
        containers.append(redirect)
        public_network.connect(
            redirect,
            ipv4_address="1.1.1.1",
        )
        public_network.connect(proxy)
        redirect.start()
        time.sleep(1.0)
        redirect.reload()
        assert redirect.attrs["State"]["Running"] is True, redirect.logs(
            tail=50
        )
        redirect_trace = assert_failure(
            sandbox,
            (
                "timeout 15 curl -fsvL --max-redirs 2 "
                "http://raw.githubusercontent.com/redirect "
                "-o /dev/null 2>&1"
            ),
        )
        assert "302" in redirect_trace
        assert "403" in redirect_trace
    finally:
        for container in reversed(containers):
            remove_container(container)
        if attachment_a is not None:
            try:
                manager.cleanup(lease_a)
            except Exception:
                pass
        if attachment_b is not None:
            try:
                manager.cleanup(lease_b)
            except Exception:
                pass
        for network in reversed(extra_networks):
            try:
                network.remove()
            except Exception:
                pass
        try:
            uplink = client.networks.get(uplink_name)
            uplink.reload()
            labels = dict(uplink.attrs.get("Labels") or {})
            if (
                labels.get("com.nanobot.sandbox") == "true"
                and labels.get("com.nanobot.managed-by") == "sandboxd"
                and not dict(uplink.attrs.get("Containers") or {})
            ):
                uplink.remove()
        except Exception:
            pass
