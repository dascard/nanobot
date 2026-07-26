from __future__ import annotations

from dataclasses import replace
import threading

import pytest

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.profile_catalog import load_profile_catalog
from sandboxd.concurrency import ProfileConcurrencyLimiter
from sandboxd.filesystem import WorkspaceFileService
from tests.test_sandboxd_api import WORKSPACE_ID, _runtime
from tests.test_sandboxd_docker_backend import IMAGE_ID, _config


def _catalog_with_limits(
    *,
    restricted_global: int = 2,
    developer_global: int = 4,
    developer_processes: int = 2,
):
    catalog = load_profile_catalog()
    profiles = tuple(
        replace(
            profile,
            global_concurrency=(
                restricted_global
                if profile.profile_id == "restricted"
                else developer_global
                if profile.profile_id == "developer"
                else profile.global_concurrency
            ),
            max_processes=(
                developer_processes
                if profile.profile_id == "developer"
                else profile.max_processes
            ),
        )
        for profile in catalog.profiles
    )
    return replace(catalog, profiles=profiles)


def test_profile_limiter_enforces_profile_and_lease_limits_independently():
    limiter = ProfileConcurrencyLimiter(_catalog_with_limits(
        restricted_global=1,
        developer_global=3,
        developer_processes=2,
    ))

    restricted = limiter.acquire("restricted")
    with pytest.raises(SandboxServiceError) as profile_full:
        limiter.acquire("restricted")
    assert profile_full.value.code is SandboxErrorCode.SANDBOX_BUSY

    first = limiter.acquire("developer", lease_id="lease-a")
    second = limiter.acquire("developer", lease_id="lease-a")
    with pytest.raises(SandboxServiceError) as lease_full:
        limiter.acquire("developer", lease_id="lease-a")
    assert lease_full.value.code is SandboxErrorCode.SANDBOX_BUSY

    third = limiter.acquire("developer", lease_id="lease-b")
    with pytest.raises(SandboxServiceError) as developer_full:
        limiter.acquire("developer", lease_id="lease-c")
    assert developer_full.value.code is SandboxErrorCode.SANDBOX_BUSY

    third.release()
    second.release()
    first.release()
    restricted.release()
    assert limiter.snapshot() == {"profiles": {}, "leases": {}}


def test_execution_permit_does_not_hold_workspace_file_write_lock(tmp_path):
    _token, runtime = _runtime(tmp_path)
    service = runtime.workspace_files
    service.layout.ensure_roots()
    service.ensure_workspace(WORKSPACE_ID)
    limiter = ProfileConcurrencyLimiter(_catalog_with_limits())

    permit = limiter.acquire("restricted")
    try:
        written = service.write_file(
            WORKSPACE_ID,
            path="parallel.txt",
            content="ok",
            overwrite=False,
            quota_bytes=1024 * 1024,
        )
    finally:
        permit.release()

    assert written["size_bytes"] == 2


def test_two_file_writes_keep_nonblocking_workspace_mutex(tmp_path):
    _token, runtime = _runtime(tmp_path)
    service: WorkspaceFileService = runtime.workspace_files
    service.layout.ensure_roots()
    service.ensure_workspace(WORKSPACE_ID)

    lock = service.acquire_workspace_write(WORKSPACE_ID)
    try:
        with pytest.raises(SandboxServiceError) as busy:
            service.write_file(
                WORKSPACE_ID,
                path="blocked.txt",
                content="x",
                overwrite=False,
                quota_bytes=1024 * 1024,
            )
    finally:
        lock.release()

    assert busy.value.code is SandboxErrorCode.SANDBOX_BUSY


def test_two_commands_in_same_workspace_can_run_concurrently(
    tmp_path,
    monkeypatch,
):
    from sandboxd.docker_backend import LocalDockerBackend

    release = threading.Event()
    both_started = threading.Barrier(3)

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
            if self.started.is_set() and release.is_set():
                self.attrs["State"]["Running"] = False

        def attach(self, **_kwargs):
            def stream():
                assert self.started.wait(timeout=2)
                assert release.wait(timeout=2)
                yield b"done", None

            return stream()

        def start(self):
            self.attrs["State"]["Running"] = True
            self.started.set()
            both_started.wait(timeout=2)

        def stats(self, **_kwargs):
            return {}

        def remove(self, **_kwargs):
            return None

    class Containers:
        def create(self, **kwargs):
            return Container(kwargs)

    class Client:
        images = Images()
        containers = Containers()

    class Assets:
        def __init__(self, data_root):
            self.root = data_root / "runtime" / ".inputs" / WORKSPACE_ID
            self.root.mkdir(parents=True)

        def stage_path(self, _workspace_id, run_id):
            path = self.root / run_id
            path.mkdir(exist_ok=True)
            return path

        def cleanup_stage(self, _workspace_id, _run_id):
            return None

    monkeypatch.setattr(
        WorkspaceFileService,
        "_secure_workspace_directory",
        staticmethod(lambda *_args: None),
    )
    config = _config(tmp_path)
    service = WorkspaceFileService(config)
    service.layout.ensure_roots()
    service.ensure_workspace(WORKSPACE_ID)
    backend = LocalDockerBackend(
        config,
        docker_client=Client(),
        workspace_files=service,
        asset_files=Assets(config.data_root),
    )
    results: list[dict] = []
    failures: list[BaseException] = []

    def execute(index: int) -> None:
        try:
            results.append(backend.execute(
                request_id=f"sbxreq_parallel_{index}",
                run_id=f"sbxrun_parallel_{index}",
                workspace_id=WORKSPACE_ID,
                command="wait-for-release",
            ))
        except BaseException as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=execute, args=(index,))
        for index in (1, 2)
    ]
    for thread in threads:
        thread.start()
    both_started.wait(timeout=2)
    release.set()
    for thread in threads:
        thread.join(timeout=3)

    assert failures == []
    assert len(results) == 2
    assert all(
        result["data"]["termination_reason"] == "completed"
        for result in results
    )
