from __future__ import annotations

import threading

import pytest

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from sandboxd.config import SandboxdConfig
from sandboxd.docker_backend import LocalDockerBackend
from sandboxd.filesystem import WorkspaceFileService
from tests.test_sandboxd_api import WORKSPACE_ID
from tests.test_sandboxd_docker_backend import IMAGE_ID


def _config(tmp_path, *, workspace_quota_bytes=1024 * 1024):
    return SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=tmp_path / "token",
        client_token_path=tmp_path / "run" / "client.token",
        image_reference="nanobot-sandbox-python:test",
        image_allowlist=(IMAGE_ID,),
        workspace_uid=10001,
        workspace_gid=10001,
        workspace_quota_bytes=workspace_quota_bytes,
        total_quota_bytes=workspace_quota_bytes * 4,
        disk_min_free_bytes=0,
    ).validated()


def test_exact_file_writes_and_run_reservation_do_not_rescan_directories(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path)
    service = WorkspaceFileService(config)
    monkeypatch.setattr(
        service,
        "_secure_workspace_directory",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "core.sandbox.paths.os.fchown",
        lambda *_args: None,
    )
    service.layout.ensure_roots()
    service.ensure_workspace(WORKSPACE_ID)

    def reject_scan(*_args, **_kwargs):
        raise AssertionError("写入和执行预留热路径不得递归扫描目录")

    monkeypatch.setattr("sandboxd.filesystem.directory_usage", reject_scan)
    monkeypatch.setattr(
        service,
        "total_workspace_usage",
        reject_scan,
    )

    first = service.write_file(
        WORKSPACE_ID,
        path="cache.txt",
        content="1234",
        overwrite=False,
        quota_bytes=1024,
    )
    second = service.write_file(
        WORKSPACE_ID,
        path="cache.txt",
        content="12",
        overwrite=True,
        quota_bytes=1024,
    )
    used_before, effective_quota = service.reserve_run_capacity(
        "sbxrun_cached",
        WORKSPACE_ID,
        workspace_quota_bytes=1024,
    )
    service.release_run_capacity("sbxrun_cached")

    assert first["used_bytes"] == 4
    assert second["used_bytes"] == 2
    assert second["usage_delta_bytes"] == -2
    assert used_before == 2
    assert effective_quota == 1024


def test_periodic_reconciliation_corrects_workspace_and_runtime_cache(
    tmp_path,
    monkeypatch,
):
    service = WorkspaceFileService(_config(tmp_path))
    monkeypatch.setattr(
        service,
        "_secure_workspace_directory",
        lambda *_args: None,
    )
    service.layout.ensure_roots()
    service.ensure_workspace(WORKSPACE_ID)
    workspace_path = service.layout.workspace_data_dir(WORKSPACE_ID)
    runtime_path = service.layout.runtime_root / WORKSPACE_ID

    (workspace_path / "external.bin").write_bytes(b"workspace")
    (runtime_path / "cache.bin").write_bytes(b"runtime")
    stale = service.usage_snapshot(WORKSPACE_ID)
    corrected = service.reconcile_workspace_usage(WORKSPACE_ID)

    assert stale.workspace_bytes == 0
    assert stale.runtime_bytes == 0
    assert corrected.workspace_bytes == len(b"workspace")
    assert corrected.runtime_bytes == len(b"runtime")
    assert corrected.dirty is False


def test_cached_file_write_still_rejects_known_workspace_quota(tmp_path, monkeypatch):
    service = WorkspaceFileService(_config(
        tmp_path,
        workspace_quota_bytes=4,
    ))
    monkeypatch.setattr(
        service,
        "_secure_workspace_directory",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "core.sandbox.paths.os.fchown",
        lambda *_args: None,
    )
    service.layout.ensure_roots()
    service.ensure_workspace(WORKSPACE_ID)
    service.write_file(
        WORKSPACE_ID,
        path="full.bin",
        content="1234",
        overwrite=False,
        quota_bytes=4,
    )

    with pytest.raises(SandboxServiceError) as full:
        service.write_file(
            WORKSPACE_ID,
            path="overflow.bin",
            content="x",
            overwrite=False,
            quota_bytes=4,
        )

    assert full.value.code is SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED


def test_docker_execute_hot_path_does_not_call_directory_usage(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path)
    service = WorkspaceFileService(config)
    monkeypatch.setattr(
        service,
        "_secure_workspace_directory",
        lambda *_args: None,
    )
    service.layout.ensure_roots()

    class Image:
        id = IMAGE_ID
        attrs = {"Config": {"User": "10001:10001"}}

    class Images:
        def get(self, _reference):
            return Image()

    class Container:
        def __init__(self, kwargs):
            self.name = kwargs["name"]
            self.attrs = {
                "Config": {"Labels": kwargs["labels"]},
                "State": {
                    "Running": False,
                    "ExitCode": 0,
                    "OOMKilled": False,
                },
            }
            self.started = threading.Event()

        def reload(self):
            return None

        def attach(self, **_kwargs):
            def stream():
                assert self.started.wait(timeout=2)
                yield b"ok", None

            return stream()

        def start(self):
            self.started.set()

        def remove(self, **_kwargs):
            return None

    class Containers:
        def create(self, **kwargs):
            return Container(kwargs)

    class Client:
        images = Images()
        containers = Containers()

    class Assets:
        def __init__(self):
            self.path = (
                config.data_root
                / "runtime"
                / ".inputs"
                / WORKSPACE_ID
                / "sbxrun_no_scan"
            )
            self.path.mkdir(parents=True)

        def stage_path(self, _workspace_id, _run_id):
            return self.path

        def cleanup_stage(self, _workspace_id, _run_id):
            return None

    def reject_scan(*_args, **_kwargs):
        raise AssertionError("Docker 命令热路径不得递归扫描目录")

    monkeypatch.setattr("sandboxd.filesystem.directory_usage", reject_scan)
    backend = LocalDockerBackend(
        config,
        docker_client=Client(),
        workspace_files=service,
        asset_files=Assets(),
    )

    result = backend.execute(
        request_id="sbxreq_no_scan",
        run_id="sbxrun_no_scan",
        workspace_id=WORKSPACE_ID,
        command="true",
    )

    assert result["data"]["termination_reason"] == "completed"
    assert result["data"]["workspace_usage_reconciliation_pending"] is True
