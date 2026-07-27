from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from sandboxd.app import LeaseEnsureRequest, SandboxRuntime, create_app
from sandboxd.auth import TokenAuthenticator
from sandboxd.config import SandboxdConfig
from sandboxd.filesystem import AssetFileService, WorkspaceFileService
from sandboxd.lease_backend import (
    CONTROLLER_EPOCH_LABEL,
    LEASE_CONTAINER_PREFIX,
    LEASE_GENERATION_LABEL,
    LEASE_LABEL,
    POLICY_SHA_LABEL,
    PROFILE_LABEL,
    LeaseBackend,
)
from sandboxd.lease_store import LeaseStore
from sandboxd.lease_reconciler import LeaseReconciler
from sandboxd.network_policy import LeaseNetworkAttachment


IMAGE_ID = "sha256:" + "d" * 64
PROXY_IMAGE_ID = "sha256:" + "e" * 64
WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
LEASE_ID = "sbxlease_backend_test"


class _Image:
    id = IMAGE_ID
    attrs = {"Config": {"User": f"{os.getuid()}:{os.getgid()}"}}


class _Container:
    def __init__(self, owner: "_Containers", kwargs):
        self.owner = owner
        self.kwargs = kwargs
        self.name = kwargs["name"]
        self.attrs = {
            "Config": {
                "Labels": dict(kwargs["labels"]),
                "Image": kwargs["image"],
            },
            "Image": kwargs["image"],
            "State": {
                "Running": False,
                "OOMKilled": False,
            },
        }
        self.removed = False
        self.exec_calls: list[dict[str, object]] = []

    def reload(self):
        return None

    def start(self):
        self.attrs["State"]["Running"] = True

    def stop(self, **_kwargs):
        self.attrs["State"]["Running"] = False

    def kill(self):
        self.attrs["State"]["Running"] = False

    def remove(self, **_kwargs):
        self.attrs["State"]["Running"] = False
        self.removed = True

    def exec_run(self, command, **kwargs):
        self.exec_calls.append({
            "command": list(command),
            **dict(kwargs),
        })
        return 0, b""


class _Containers:
    def __init__(self):
        self.values: list[_Container] = []
        self.create_calls: list[dict] = []
        self._guard = threading.Lock()

    def create(self, **kwargs):
        with self._guard:
            if any(
                not item.removed and item.name == kwargs["name"]
                for item in self.values
            ):
                raise RuntimeError("duplicate container name")
            self.create_calls.append(kwargs)
            container = _Container(self, kwargs)
            self.values.append(container)
            return container

    def list(self, **_kwargs):
        with self._guard:
            return [item for item in self.values if not item.removed]


class _DockerClient:
    def __init__(self):
        self.containers = _Containers()


class _NetworkPolicy:
    def __init__(self, config):
        self.config = config

    def prepare(self, profile, *, lease_id, controller_epoch):
        assert profile.network_policy_id == "developer_allowlist_v1"
        return LeaseNetworkAttachment(
            lease_id=lease_id,
            policy_id=profile.network_policy_id,
            network_name=f"nanobot-sbx-net-{lease_id}",
            proxy_host=f"nanobot-sbx-proxy-{lease_id}",
            proxy_port=profile.network_proxy_port,
            controller_epoch=controller_epoch,
            policy_sha256=self.config.profile_catalog.policy_sha256,
        )

    def require_lease_topology(self, *_args, **_kwargs):
        return None

    def cleanup(self, _lease_id):
        return False

    def cleanup_orphans(self, _active_lease_ids):
        return []


class _RejectingNetworkPolicy(_NetworkPolicy):
    def __init__(self, config):
        super().__init__(config)
        self.cleaned: list[str] = []

    def require_lease_topology(self, *_args, **_kwargs):
        raise SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "模拟 Lease 网络拓扑漂移",
        )

    def cleanup(self, lease_id):
        self.cleaned.append(lease_id)
        return True


class _RejectingEnvironmentManager:
    def prepare(self, **_kwargs):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "模拟环境准备失败",
        )


def _manifest(tmp_path: Path) -> Path:
    source = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "sandbox-execution-profiles.v1.json"
    )
    raw = json.loads(source.read_text(encoding="utf-8"))
    for profile in raw["profiles"]:
        if profile["profile_id"] in {
            "developer",
            "trusted_developer",
        }:
            profile["image_reference"] = "nanobot-sandbox-developer:test"
            profile["image_allowlist"] = [IMAGE_ID]
        if profile["profile_id"] == "developer":
            profile["network_proxy_image_allowlist"] = [PROXY_IMAGE_ID]
    target = tmp_path / "profiles.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    return target


def _components(tmp_path, *, clock=lambda: 1000.0):
    config = SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=tmp_path / "token",
        client_token_path=tmp_path / "run" / "client.token",
        admin_token_file=tmp_path / "admin-token",
        admin_client_token_path=tmp_path / "run" / "admin-client.token",
        quota_helper_path=tmp_path / "quota-helper",
        profile_manifest_path=_manifest(tmp_path),
        workspace_uid=os.getuid(),
        workspace_gid=os.getgid(),
        disk_min_free_bytes=0,
    ).validated()
    workspace_files = WorkspaceFileService(config)
    asset_files = AssetFileService(config)
    workspace_files.layout.ensure_roots()
    docker = _DockerClient()
    store = LeaseStore(
        config.data_root / "runtime" / ".sandboxd-leases"
    )
    store.start_controller(now_unix=clock())
    backend = LeaseBackend(
        config,
        docker_client=docker,
        workspace_files=workspace_files,
        asset_files=asset_files,
        lease_store=store,
        profile_image_resolver=lambda profile_id: (
            _Image()
            if profile_id == "developer"
            else pytest.fail("不应解析其他 Profile 镜像")
        ),
        network_policy=_NetworkPolicy(config),
        clock=clock,
    )
    return config, workspace_files, asset_files, docker, store, backend


def _ensure(backend: LeaseBackend, config: SandboxdConfig, **overrides):
    catalog = config.profile_catalog
    assert catalog is not None
    payload = {
        "request_id": "lease_request_1",
        "lease_id": LEASE_ID,
        "workspace_id": WORKSPACE_ID,
        "profile_id": "developer",
        "catalog_generation": catalog.catalog_generation,
        "policy_sha256": catalog.policy_sha256,
        "quota_generation": 1,
    }
    payload.update(overrides)
    return backend.ensure(**payload)


def test_lease_builder_uses_fixed_security_labels_and_no_docker_injection(
    tmp_path,
):
    config, _workspace, _assets, docker, store, backend = _components(
        tmp_path
    )

    fact = _ensure(backend, config)

    assert fact["lease_id"] == LEASE_ID
    assert fact["controller_epoch"] == store.controller_epoch
    assert fact["status"] == "idle"
    assert fact["environment"]["ready"] is True
    assert fact["environment"]["action"] == "setup"
    assert len(docker.containers.create_calls) == 1
    kwargs = docker.containers.create_calls[0]
    assert kwargs["name"] == f"{LEASE_CONTAINER_PREFIX}{LEASE_ID}"
    assert kwargs["image"] == IMAGE_ID
    assert kwargs["command"][0:2] == ["/bin/bash", "-lc"]
    assert kwargs["read_only"] is True
    assert kwargs["init"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["privileged"] is False
    assert kwargs["network_mode"] == f"nanobot-sbx-net-{LEASE_ID}"
    assert kwargs["network_disabled"] is False
    assert kwargs["environment"]["HTTPS_PROXY"] == (
        f"http://nanobot-sbx-proxy-{LEASE_ID}:3128"
    )
    assert kwargs["labels"][LEASE_LABEL] == LEASE_ID
    assert kwargs["labels"][PROFILE_LABEL] == "developer"
    assert (
        kwargs["labels"][LEASE_GENERATION_LABEL]
        == config.profile_catalog.catalog_generation
    )
    assert (
        kwargs["labels"][POLICY_SHA_LABEL]
        == config.profile_catalog.policy_sha256
    )
    assert (
        kwargs["labels"][CONTROLLER_EPOCH_LABEL]
        == store.controller_epoch
    )
    assert set(
        item["bind"] for item in kwargs["volumes"].values()
    ) == {"/workspace", "/runtime", "/inputs"}
    assert "/var/run/docker.sock" not in json.dumps(kwargs)
    assert len(docker.containers.values[0].exec_calls) == 1

    repeated = _ensure(backend, config)
    assert repeated["environment"]["action"] == "unchanged"
    assert len(docker.containers.values[0].exec_calls) == 1

    with pytest.raises(ValidationError):
        LeaseEnsureRequest.model_validate({
            "request_id": "lease_request_2",
            "lease_id": "sbxlease_schema_test",
            "workspace_id": WORKSPACE_ID,
            "profile_id": "developer",
            "catalog_generation": config.profile_catalog.catalog_generation,
            "policy_sha256": config.profile_catalog.policy_sha256,
            "image": "attacker/image",
        })


def test_concurrent_ensure_creates_one_container_and_request_reuse_is_fenced(
    tmp_path,
):
    config, _workspace, _assets, docker, _store, backend = _components(
        tmp_path
    )
    results: list[dict] = []
    failures: list[BaseException] = []

    def worker():
        try:
            results.append(_ensure(backend, config))
        except BaseException as exc:
            failures.append(exc)

    first = threading.Thread(target=worker)
    second = threading.Thread(target=worker)
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert failures == []
    assert len(results) == 2
    assert {item["lease_id"] for item in results} == {LEASE_ID}
    assert len(docker.containers.create_calls) == 1

    with pytest.raises(SandboxServiceError) as reused:
        _ensure(
            backend,
            config,
            workspace_id=(
                "00000000-0000-0000-0000-000000000002"
            ),
        )
    assert reused.value.code is SandboxErrorCode.AUTHORIZATION_FAILED
    assert len(docker.containers.create_calls) == 1


def test_new_lease_network_drift_fails_creation_and_cleans_container(
    tmp_path,
):
    config, _workspace, _assets, docker, store, backend = _components(
        tmp_path
    )
    network_policy = _RejectingNetworkPolicy(config)
    backend.network_policy = network_policy

    with pytest.raises(SandboxServiceError) as error:
        _ensure(backend, config)

    assert error.value.code is SandboxErrorCode.AUTHORIZATION_FAILED
    assert len(docker.containers.values) == 1
    assert docker.containers.values[0].removed is True
    assert network_policy.cleaned == [LEASE_ID]
    assert store.get(LEASE_ID) is None


def test_environment_failure_cleans_new_lease_before_snapshot(tmp_path):
    config, _workspace, _assets, docker, store, backend = _components(
        tmp_path
    )
    backend.environment_manager = _RejectingEnvironmentManager()

    with pytest.raises(SandboxServiceError) as error:
        _ensure(backend, config)

    assert error.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE
    assert len(docker.containers.values) == 1
    assert docker.containers.values[0].removed is True
    assert store.get(LEASE_ID) is None


def test_environment_failure_recycles_existing_lease(tmp_path):
    config, _workspace, _assets, docker, store, backend = _components(
        tmp_path
    )
    _ensure(backend, config)
    backend.environment_manager = _RejectingEnvironmentManager()

    with pytest.raises(SandboxServiceError) as error:
        _ensure(backend, config)

    assert error.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE
    assert docker.containers.values[0].removed is True
    assert store.get(LEASE_ID) is None


def test_policy_mismatch_and_non_grantable_profile_fail_before_docker(
    tmp_path,
):
    config, _workspace, _assets, docker, _store, backend = _components(
        tmp_path
    )

    with pytest.raises(SandboxServiceError) as drift:
        _ensure(backend, config, policy_sha256="f" * 64)
    assert drift.value.code is SandboxErrorCode.AUTHORIZATION_FAILED

    with pytest.raises(SandboxServiceError) as trusted:
        _ensure(
            backend,
            config,
            request_id="lease_request_trusted",
            profile_id="trusted_developer",
        )
    assert trusted.value.code is SandboxErrorCode.AUTHORIZATION_FAILED
    assert docker.containers.create_calls == []


def test_lease_inputs_update_and_revoke_in_place_then_recycle_preserves_data(
    tmp_path,
):
    (
        config,
        workspace_files,
        asset_files,
        docker,
        _store,
        backend,
    ) = _components(tmp_path)
    _ensure(backend, config)
    workspace_path = workspace_files.layout.workspace_data_dir(WORKSPACE_ID)
    runtime_path = workspace_files.layout.ensure_runtime(WORKSPACE_ID)
    (workspace_path / "kept.txt").write_text("workspace", encoding="utf-8")
    (runtime_path / "kept.txt").write_text("runtime", encoding="utf-8")

    first_writer = asset_files.open_upload(media_type="text/plain")
    first_writer.write(b"first")
    first = first_writer.finish()
    second_writer = asset_files.open_upload(media_type="text/plain")
    second_writer.write(b"second")
    second = second_writer.finish()
    stage_path = asset_files.lease_stage_path(WORKSPACE_ID, LEASE_ID)
    original_inode = stage_path.stat().st_ino

    added = asset_files.sync_lease(
        WORKSPACE_ID,
        LEASE_ID,
        [{
            "sha256": first.sha256,
            "storage_key": first.storage_key,
            "logical_name": "docs/first.txt",
        }],
    )
    assert added["changed"] is True
    assert (stage_path / "docs" / "first.txt").read_bytes() == b"first"

    asset_files.sync_lease(
        WORKSPACE_ID,
        LEASE_ID,
        [{
            "sha256": first.sha256,
            "storage_key": first.storage_key,
            "logical_name": "docs/first.txt",
        }, {
            "sha256": second.sha256,
            "storage_key": second.storage_key,
            "logical_name": "second.txt",
        }],
    )
    revoked = asset_files.sync_lease(
        WORKSPACE_ID,
        LEASE_ID,
        [{
            "sha256": second.sha256,
            "storage_key": second.storage_key,
            "logical_name": "second.txt",
        }],
    )

    assert revoked["changed"] is True
    assert stage_path.stat().st_ino == original_inode
    assert not (stage_path / "docs" / "first.txt").exists()
    assert (stage_path / "second.txt").read_bytes() == b"second"

    os.chmod(stage_path, 0o700)
    (stage_path / "second.txt").unlink()
    repaired = asset_files.sync_lease(
        WORKSPACE_ID,
        LEASE_ID,
        [{
            "sha256": second.sha256,
            "storage_key": second.storage_key,
            "logical_name": "second.txt",
        }],
    )

    assert repaired["changed"] is True
    assert (stage_path / "second.txt").read_bytes() == b"second"

    result = backend.terminate_all(reason="admin_terminated")

    assert result["terminated_lease_ids"] == [LEASE_ID]
    assert result["failed_lease_ids"] == []
    assert docker.containers.values[0].removed is True
    assert (workspace_path / "kept.txt").read_text() == "workspace"
    assert (runtime_path / "kept.txt").read_text() == "runtime"
    assert not stage_path.exists()


def test_admin_recreate_keeps_lease_workspace_runtime_and_reprepares_environment(
    tmp_path,
):
    (
        config,
        workspace_files,
        _asset_files,
        docker,
        store,
        backend,
    ) = _components(tmp_path)
    _ensure(backend, config)
    workspace_path = workspace_files.layout.workspace_data_dir(
        WORKSPACE_ID
    )
    runtime_path = workspace_files.layout.ensure_runtime(WORKSPACE_ID)
    (workspace_path / "kept.txt").write_text(
        "workspace",
        encoding="utf-8",
    )
    (workspace_path / "requirements.txt").write_text(
        "pytest==8.4.1\n",
        encoding="utf-8",
    )
    (runtime_path / "kept.txt").write_text(
        "runtime",
        encoding="utf-8",
    )
    original = docker.containers.values[0]

    fact = backend.recreate(
        LEASE_ID,
        request_id="admin_recreate_request",
    )

    assert fact["lease_id"] == LEASE_ID
    assert fact["termination_reason"] == "admin_lease_recreate"
    assert fact["workspace_preserved"] is True
    assert fact["runtime_preserved"] is True
    assert fact["present"] is True
    assert fact["running"] is True
    assert fact["environment"]["ready"] is True
    assert fact["environment"]["action"] == "maintenance"
    assert original.removed is True
    assert len(docker.containers.create_calls) == 2
    assert store.get(LEASE_ID) is not None
    assert (workspace_path / "kept.txt").read_text() == "workspace"
    assert (runtime_path / "kept.txt").read_text() == "runtime"


def test_admin_recreate_and_recycle_are_serialized_per_lease(
    tmp_path,
    monkeypatch,
):
    config, _workspace, _assets, _docker, _store, backend = _components(
        tmp_path
    )
    _ensure(backend, config)
    original_ensure = backend.ensure
    original_recycle = backend.recycle
    ensure_entered = threading.Event()
    release_ensure = threading.Event()
    second_recycle_entered = threading.Event()
    recycle_calls = 0
    recycle_calls_guard = threading.Lock()
    errors: list[BaseException] = []

    def blocking_ensure(**kwargs):
        if kwargs["request_id"] == "admin_recreate_serialized":
            ensure_entered.set()
            assert release_ensure.wait(timeout=2)
        return original_ensure(**kwargs)

    def observed_recycle(lease_id, *, reason, cleanup_inputs=True):
        nonlocal recycle_calls
        with recycle_calls_guard:
            recycle_calls += 1
            if recycle_calls == 2:
                second_recycle_entered.set()
        return original_recycle(
            lease_id,
            reason=reason,
            cleanup_inputs=cleanup_inputs,
        )

    monkeypatch.setattr(backend, "ensure", blocking_ensure)
    monkeypatch.setattr(backend, "recycle", observed_recycle)

    def run_recreate():
        try:
            backend.recreate(
                LEASE_ID,
                request_id="admin_recreate_serialized",
            )
        except BaseException as exc:
            errors.append(exc)

    def run_stop():
        try:
            backend.admin_recycle(
                LEASE_ID,
                reason="admin_lease_stop",
            )
        except BaseException as exc:
            errors.append(exc)

    recreate_thread = threading.Thread(target=run_recreate)
    stop_thread = threading.Thread(target=run_stop)
    recreate_thread.start()
    assert ensure_entered.wait(timeout=2)
    stop_thread.start()

    assert not second_recycle_entered.wait(timeout=0.2)
    release_ensure.set()
    recreate_thread.join(timeout=2)
    stop_thread.join(timeout=2)

    assert not recreate_thread.is_alive()
    assert not stop_thread.is_alive()
    assert errors == []
    assert second_recycle_entered.is_set()


def test_lease_http_api_has_strict_fields_split_auth_and_safe_admin_facts(
    tmp_path,
):
    (
        config,
        workspace_files,
        asset_files,
        docker,
        store,
        backend,
    ) = _components(tmp_path)
    normal_token = "n" * 64
    admin_token = "a" * 64
    config.token_file.write_text(normal_token, encoding="ascii")
    config.token_file.chmod(0o600)
    config.admin_token_file.write_text(admin_token, encoding="ascii")
    config.admin_token_file.chmod(0o600)

    class _ControlDockerBackend:
        client = docker

        def ready(self):
            return {
                "docker": True,
                "catalog_generation": (
                    config.profile_catalog.catalog_generation
                ),
                "policy_sha256": config.profile_catalog.policy_sha256,
                "profiles": {},
            }

    reconciler = LeaseReconciler(
        backend,
        store,
        interval_seconds=3600,
    )
    runtime = SandboxRuntime(
        config=config,
        authenticator=TokenAuthenticator(
            config.token_file,
            config.client_token_path,
        ),
        workspace_files=workspace_files,
        asset_files=asset_files,
        docker_backend=_ControlDockerBackend(),
        admin_authenticator=TokenAuthenticator(
            config.admin_token_file,
            config.admin_client_token_path,
        ),
        lease_store=store,
        lease_backend=backend,
        lease_reconciler=reconciler,
    )
    catalog = config.profile_catalog
    assert catalog is not None
    payload = {
        "request_id": "lease_api_request",
        "lease_id": "sbxlease_api_test",
        "workspace_id": WORKSPACE_ID,
        "profile_id": "developer",
        "catalog_generation": catalog.catalog_generation,
        "policy_sha256": catalog.policy_sha256,
        "quota_generation": 1,
    }
    normal_headers = {
        "Authorization": f"Bearer {normal_token}",
        "X-Nanobot-Request-ID": payload["request_id"],
    }
    admin_headers = {
        "Authorization": f"Bearer {admin_token}",
    }

    with TestClient(create_app(runtime)) as client:
        assert client.post("/v1/leases/ensure", json=payload).status_code == 403
        injected = client.post(
            "/v1/leases/ensure",
            json={**payload, "image": "attacker/image"},
            headers=normal_headers,
        )
        assert injected.status_code == 400
        assert "attacker" not in injected.text

        ensured = client.post(
            "/v1/leases/ensure",
            json=payload,
            headers=normal_headers,
        )
        assert ensured.status_code == 200
        assert ensured.json()["data"]["lease_id"] == "sbxlease_api_test"

        assert (
            client.get(
                "/v1/admin/controller-state",
                headers={"Authorization": f"Bearer {normal_token}"},
            ).status_code
            == 403
        )
        controller = client.get(
            "/v1/admin/controller-state",
            headers=admin_headers,
        )
        assert controller.status_code == 200
        assert controller.json()["data"]["lease_count"] == 1
        ready = client.get(
            "/v1/readyz",
            headers={"Authorization": f"Bearer {normal_token}"},
        )
        assert ready.status_code == 200
        assert (
            ready.json()["data"]["controller_epoch"]
            == store.controller_epoch
        )

        leases = client.get(
            "/v1/admin/leases",
            headers=admin_headers,
        ).json()["data"]["leases"]
        assert len(leases) == 1
        assert leases[0]["lease_id"] == "sbxlease_api_test"
        serialized = json.dumps(leases, sort_keys=True)
        assert "/tmp/" not in serialized
        assert "command" not in serialized
        assert "stdout" not in serialized

        forbidden_recreate = client.post(
            "/v1/admin/leases/sbxlease_api_test/recreate",
            json={"request_id": "admin_recreate_forbidden"},
            headers={
                "Authorization": f"Bearer {normal_token}",
                "X-Nanobot-Request-ID": "admin_recreate_forbidden",
            },
        )
        assert forbidden_recreate.status_code == 403
        recreated = client.post(
            "/v1/admin/leases/sbxlease_api_test/recreate",
            json={"request_id": "admin_recreate_allowed"},
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Nanobot-Request-ID": "admin_recreate_allowed",
            },
        )
        assert recreated.status_code == 200
        assert recreated.json()["data"]["lease_id"] == "sbxlease_api_test"
        assert recreated.json()["data"]["workspace_preserved"] is True
        assert recreated.json()["data"]["runtime_preserved"] is True
        assert recreated.json()["data"]["present"] is True

        assets = client.put(
            "/v1/leases/sbxlease_api_test/assets",
            json={
                "request_id": "lease_asset_request",
                "workspace_id": WORKSPACE_ID,
                "assets": [],
            },
            headers={
                "Authorization": f"Bearer {normal_token}",
                "X-Nanobot-Request-ID": "lease_asset_request",
            },
        )
        assert assets.status_code == 200

        stopped = client.post(
            "/v1/leases/sbxlease_api_test/stop",
            json={"request_id": "lease_stop_request"},
            headers={
                "Authorization": f"Bearer {normal_token}",
                "X-Nanobot-Request-ID": "lease_stop_request",
            },
        )
        assert stopped.status_code == 200
        assert stopped.json()["data"]["workspace_preserved"] is True

        second_payload = {
            **payload,
            "request_id": "lease_api_request_second",
            "lease_id": "sbxlease_api_second",
        }
        second = client.post(
            "/v1/leases/ensure",
            json=second_payload,
            headers={
                "Authorization": f"Bearer {normal_token}",
                "X-Nanobot-Request-ID": second_payload["request_id"],
            },
        )
        assert second.status_code == 200
        admin_stopped = client.post(
            "/v1/admin/leases/sbxlease_api_second/stop",
            json={"request_id": "admin_stop_second"},
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Nanobot-Request-ID": "admin_stop_second",
            },
        )
        assert admin_stopped.status_code == 200
        assert (
            admin_stopped.json()["data"]["termination_reason"]
            == "admin_lease_stop"
        )

        third_payload = {
            **payload,
            "request_id": "lease_api_request_third",
            "lease_id": "sbxlease_api_third",
        }
        third = client.post(
            "/v1/leases/ensure",
            json=third_payload,
            headers={
                "Authorization": f"Bearer {normal_token}",
                "X-Nanobot-Request-ID": third_payload["request_id"],
            },
        )
        assert third.status_code == 200
        admin_destroyed = client.request(
            "DELETE",
            "/v1/admin/leases/sbxlease_api_third",
            json={"request_id": "admin_destroy_third"},
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Nanobot-Request-ID": "admin_destroy_third",
            },
        )
        assert admin_destroyed.status_code == 200
        assert (
            admin_destroyed.json()["data"]["termination_reason"]
            == "admin_lease_destroy"
        )

        fourth_payload = {
            **payload,
            "request_id": "lease_api_request_fourth",
            "lease_id": "sbxlease_api_fourth",
        }
        fourth = client.post(
            "/v1/leases/ensure",
            json=fourth_payload,
            headers={
                "Authorization": f"Bearer {normal_token}",
                "X-Nanobot-Request-ID": fourth_payload["request_id"],
            },
        )
        assert fourth.status_code == 200
        terminated = client.post(
            "/v1/admin/leases/terminate-all",
            json={
                "request_id": "lease_terminate_all",
                "reason": "admin_terminated",
            },
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Nanobot-Request-ID": "lease_terminate_all",
            },
        )
        assert terminated.status_code == 200
        assert terminated.json()["data"]["terminated_lease_ids"] == [
            "sbxlease_api_fourth"
        ]
