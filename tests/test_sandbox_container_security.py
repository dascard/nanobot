import json

import pytest

from sandboxd.config import SandboxdConfig
from sandboxd.container_security import (
    CONTAINER_SECURITY_FIELDS,
    ContainerMountPaths,
    ContainerSecurityError,
    build_container_kwargs,
    security_projection,
)
from sandboxd.network_policy import LeaseNetworkAttachment


IMAGE_ID = "sha256:" + "a" * 64
WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"


def _config(tmp_path):
    return SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=tmp_path / "token",
        client_token_path=tmp_path / "run" / "client.token",
        disk_min_free_bytes=0,
    ).validated()


def _mounts(config):
    workspace = (
        config.data_root
        / "workspaces"
        / WORKSPACE_ID[:2]
        / WORKSPACE_ID
        / "data"
    )
    inputs = (
        config.data_root
        / "runtime"
        / ".inputs"
        / WORKSPACE_ID
        / "sbxrun_test"
    )
    runtime = config.data_root / "runtime" / WORKSPACE_ID
    for path in (workspace, inputs, runtime):
        path.mkdir(parents=True, exist_ok=True)
    return ContainerMountPaths(
        workspace=workspace,
        inputs=inputs,
        runtime=runtime,
    )


def _kwargs(config, profile_id, *, lifecycle):
    network_attachment = None
    if profile_id == "developer":
        network_attachment = LeaseNetworkAttachment(
            lease_id="sbxlease_security_test",
            policy_id="developer_allowlist_v1",
            network_name="nanobot-sbx-net-sbxlease_security_test",
            proxy_host="nanobot-sbx-proxy-sbxlease_security_test",
            proxy_port=3128,
            controller_epoch="sbxepoch_security_test",
            policy_sha256=config.profile_catalog.policy_sha256,
        )
    return build_container_kwargs(
        config,
        config.profile(profile_id),
        lifecycle=lifecycle,
        image_id=IMAGE_ID,
        name=f"nanobot-sbx-{lifecycle}-test",
        command="python -m pytest -q",
        working_dir="/workspace/project",
        labels={
            "com.nanobot.sandbox": "true",
            "com.nanobot.managed-by": "sandboxd",
        },
        mounts=_mounts(config),
        network_attachment=network_attachment,
    )


def test_oneshot_and_lease_use_one_complete_security_field_contract(tmp_path):
    config = _config(tmp_path)

    oneshot = _kwargs(config, "restricted", lifecycle="oneshot")
    lease = _kwargs(config, "developer", lifecycle="lease")

    assert set(security_projection(oneshot)) == CONTAINER_SECURITY_FIELDS
    assert set(security_projection(lease)) == CONTAINER_SECURITY_FIELDS
    assert set(oneshot) - {
        "command",
        "environment",
        "image",
        "labels",
        "name",
        "volumes",
        "working_dir",
    } == set(lease) - {
        "command",
        "environment",
        "image",
        "labels",
        "name",
        "volumes",
        "working_dir",
    }
    assert oneshot["read_only"] is lease["read_only"] is True
    assert oneshot["cap_drop"] == lease["cap_drop"] == ["ALL"]
    assert oneshot["privileged"] is lease["privileged"] is False
    assert oneshot["ipc_mode"] == lease["ipc_mode"] == "private"


def test_profile_controls_shell_network_resources_and_offline_pip(tmp_path):
    config = _config(tmp_path)

    restricted = _kwargs(config, "restricted", lifecycle="oneshot")
    developer = _kwargs(config, "developer", lifecycle="lease")

    assert restricted["command"] == [
        "/bin/sh",
        "-lc",
        "python -m pytest -q",
    ]
    assert developer["command"] == [
        "/bin/bash",
        "-lc",
        "python -m pytest -q",
    ]
    assert restricted["network_disabled"] is True
    assert restricted["network_mode"] == "none"
    assert restricted["environment"]["PIP_NO_INDEX"] == "1"
    assert developer["network_disabled"] is False
    assert developer["network_mode"] == (
        "nanobot-sbx-net-sbxlease_security_test"
    )
    assert "PIP_NO_INDEX" not in developer["environment"]
    assert developer["environment"]["HTTPS_PROXY"] == (
        "http://nanobot-sbx-proxy-sbxlease_security_test:3128"
    )
    assert restricted["pids_limit"] == 128
    assert developer["pids_limit"] == 512
    assert restricted["mem_limit"] == 512 * 1024 * 1024
    assert developer["mem_limit"] == 2 * 1024 * 1024 * 1024
    assert restricted["security_opt"][-1] == (
        "apparmor=nanobot-sandbox-restricted"
    )
    assert developer["security_opt"][-1] == (
        "apparmor=nanobot-sandbox-developer"
    )


def test_builder_has_only_fixed_mount_targets_and_no_host_escape(tmp_path):
    config = _config(tmp_path)
    kwargs = _kwargs(config, "developer", lifecycle="lease")
    serialized = json.dumps(kwargs, sort_keys=True)

    assert set(
        mount["bind"]
        for mount in kwargs["volumes"].values()
    ) == {"/workspace", "/inputs", "/runtime"}
    assert "/var/run/docker.sock" not in serialized
    assert kwargs["network_mode"] != "host"
    assert kwargs.get("pid_mode") != "host"
    assert "devices" not in kwargs
    assert "cap_add" not in kwargs

    outside = tmp_path / "outside"
    outside.mkdir()
    mounts = _mounts(config)
    with pytest.raises(ContainerSecurityError, match="固定数据目录"):
        build_container_kwargs(
            config,
            config.profile("developer"),
            lifecycle="lease",
            image_id=IMAGE_ID,
            name="nanobot-sbx-lease-test",
            command="true",
            working_dir="/workspace",
            labels={},
            mounts=ContainerMountPaths(
                workspace=outside,
                inputs=mounts.inputs,
                runtime=mounts.runtime,
            ),
            network_attachment=LeaseNetworkAttachment(
                lease_id="sbxlease_security_test",
                policy_id="developer_allowlist_v1",
                network_name="nanobot-sbx-net-sbxlease_security_test",
                proxy_host="nanobot-sbx-proxy-sbxlease_security_test",
                proxy_port=3128,
                controller_epoch="sbxepoch_security_test",
                policy_sha256=config.profile_catalog.policy_sha256,
            ),
        )


def test_developer_profile_does_not_inject_repository_credentials(tmp_path):
    config = _config(tmp_path)
    kwargs = _kwargs(config, "developer", lifecycle="lease")

    environment_names = {
        str(name).upper()
        for name in kwargs["environment"]
    }
    forbidden_names = {
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "SSH_AUTH_SOCK",
        "GIT_CONFIG_COUNT",
    }
    assert not forbidden_names & environment_names
    assert not any(
        marker in name
        for name in environment_names
        for marker in ("CREDENTIAL", "PRIVATE_KEY", "ACCESS_TOKEN")
    )
    assert set(
        mount["bind"]
        for mount in kwargs["volumes"].values()
    ) == {"/workspace", "/inputs", "/runtime"}


def test_builder_fails_closed_for_lifecycle_or_network_policy_drift(
    tmp_path,
):
    config = _config(tmp_path)
    mounts = _mounts(config)

    with pytest.raises(ContainerSecurityError, match="生命周期"):
        build_container_kwargs(
            config,
            config.profile("developer"),
            lifecycle="oneshot",
            image_id=IMAGE_ID,
            name="nanobot-sbx-test",
            command="true",
            working_dir="/workspace",
            labels={},
            mounts=mounts,
        )

    with pytest.raises(ContainerSecurityError, match="受信 Lease 网络附件"):
        build_container_kwargs(
            config,
            config.profile("developer"),
            lifecycle="lease",
            image_id=IMAGE_ID,
            name="nanobot-sbx-test",
            command="true",
            working_dir="/workspace",
            labels={},
            mounts=mounts,
        )

    with pytest.raises(ContainerSecurityError, match="当前不可创建"):
        build_container_kwargs(
            config,
            config.profile("trusted_developer"),
            lifecycle="lease",
            image_id=IMAGE_ID,
            name="nanobot-sbx-test",
            command="true",
            working_dir="/workspace",
            labels={},
            mounts=mounts,
        )
