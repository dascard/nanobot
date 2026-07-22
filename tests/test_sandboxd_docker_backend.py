import json

import pytest

from sandboxd.config import SandboxdConfig
from sandboxd.docker_backend import (
    LocalDockerBackend,
    MANAGED_BY_LABEL,
    MANAGED_LABEL,
    RUN_LABEL,
    WORKSPACE_LABEL,
)
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from sandboxd.output_limiter import OutputLimiter
from sandboxd.reconciler import OrphanReconciler


IMAGE_ID = "sha256:" + "a" * 64
WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"


class _Image:
    id = IMAGE_ID
    attrs = {"Config": {"User": "10001:10001"}}


class _Images:
    def get(self, reference):
        assert reference == "nanobot-sandbox-python:test"
        return _Image()


class _Containers:
    def __init__(self, values=None):
        self.values = list(values or [])

    def list(self, **_kwargs):
        return self.values


class _DockerClient:
    def __init__(self, containers=None):
        self.images = _Images()
        self.containers = containers or _Containers()

    def ping(self):
        return True

    def info(self):
        return {"SecurityOptions": ["name=seccomp,profile=builtin", "name=apparmor"]}


def _config(tmp_path):
    return SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=tmp_path / "token",
        client_token_path=tmp_path / "run" / "client.token",
        image_reference="nanobot-sandbox-python:test",
        image_allowlist=(IMAGE_ID,),
        disk_min_free_bytes=0,
    ).validated()


def test_docker_backend_builds_only_fixed_security_parameters(tmp_path):
    from docker.models.containers import _create_container_args

    config = _config(tmp_path)
    backend = LocalDockerBackend(config, docker_client=_DockerClient())
    backend.workspace_files.layout.ensure_workspace(WORKSPACE_ID)
    runtime = backend.workspace_files.layout.ensure_runtime(WORKSPACE_ID)
    inputs = tmp_path / "inputs"
    inputs.mkdir()

    kwargs = backend._container_kwargs(
        image=_Image(),
        run_id="sbxrun_test1",
        workspace_id=WORKSPACE_ID,
        command="python task.py",
        cwd="results",
        inputs_path=inputs,
    )

    assert kwargs["image"] == IMAGE_ID
    assert kwargs["command"] == ["/bin/sh", "-lc", "python task.py"]
    assert kwargs["user"] == "10001:10001"
    assert kwargs["working_dir"] == "/workspace/results"
    assert kwargs["network_mode"] == "none"
    assert kwargs["read_only"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges", "apparmor=nanobot-sandbox"]
    assert kwargs["privileged"] is False
    assert kwargs["init"] is True
    assert kwargs["pids_limit"] == 128
    assert kwargs["mem_limit"] == 512 * 1024 * 1024
    assert kwargs["memswap_limit"] == 512 * 1024 * 1024
    assert kwargs["nano_cpus"] == 1_000_000_000
    assert "stop_timeout" not in kwargs
    assert kwargs["log_config"] == {
        "type": "local",
        "config": {
            "max-size": "1m",
            "max-file": "1",
            "compress": "false",
        },
    }
    assert set(mount["bind"] for mount in kwargs["volumes"].values()) == {
        "/workspace",
        "/inputs",
        "/runtime",
    }
    assert "/var/run/docker.sock" not in json.dumps(kwargs)
    assert kwargs["labels"] == {
        MANAGED_LABEL: "true",
        MANAGED_BY_LABEL: "sandboxd",
        WORKSPACE_LABEL: WORKSPACE_ID,
        RUN_LABEL: "sbxrun_test1",
    }
    assert runtime.exists()
    _create_container_args({"version": "1.45", **kwargs})


def test_ready_requires_exact_loaded_apparmor_profile(tmp_path, monkeypatch):
    from sandboxd import docker_backend as docker_backend_module

    profiles = tmp_path / "apparmor-profiles"
    profiles.write_text("docker-default (enforce)\n", encoding="utf-8")
    monkeypatch.setattr(
        docker_backend_module,
        "APPARMOR_PROFILES_PATH",
        profiles,
    )
    backend = LocalDockerBackend(_config(tmp_path), docker_client=_DockerClient())
    backend.workspace_files.layout.ensure_roots()

    with pytest.raises(SandboxServiceError) as missing:
        backend.ready()
    assert missing.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE
    assert "profile 未加载" in missing.value.summary

    profiles.write_text(
        "docker-default (enforce)\nnanobot-sandbox (enforce)\n",
        encoding="utf-8",
    )
    ready = backend.ready()
    assert ready["apparmor_profile"] == "nanobot-sandbox"


class _Container:
    def __init__(self, name, labels):
        self.name = name
        self.attrs = {"Config": {"Labels": labels}, "State": {"Running": False}}
        self.removed = False

    def reload(self):
        return None

    def remove(self, **_kwargs):
        self.removed = True


def test_reconciler_requires_name_and_both_labels():
    managed = _Container(
        "nanobot-sbx-sbxrun_owned",
        {MANAGED_LABEL: "true", MANAGED_BY_LABEL: "sandboxd"},
    )
    wrong_name = _Container(
        "unrelated-container",
        {MANAGED_LABEL: "true", MANAGED_BY_LABEL: "sandboxd"},
    )
    missing_label = _Container(
        "nanobot-sbx-sbxrun_other",
        {MANAGED_LABEL: "true"},
    )
    containers = _Containers([managed, wrong_name, missing_label])

    result = OrphanReconciler(_DockerClient(containers)).reconcile()

    assert result == {"inspected": 3, "removed": 1}
    assert managed.removed is True
    assert wrong_name.removed is False
    assert missing_label.removed is False


def test_output_limiter_tracks_full_counts_but_returns_bounded_text():
    limiter = OutputLimiter(
        stdout_limit_bytes=4,
        stderr_limit_bytes=3,
        hard_limit_bytes=8,
    )

    assert limiter.feed(b"abcdef", b"xy") is False
    assert limiter.feed(None, b"zq") is True
    snapshot = limiter.snapshot()

    assert snapshot.stdout == "abcd"
    assert snapshot.stderr == "xyz"
    assert snapshot.stdout_bytes == 6
    assert snapshot.stderr_bytes == 4
    assert snapshot.stdout_truncated is True
    assert snapshot.stderr_truncated is True
    assert snapshot.hard_limit_exceeded is True
