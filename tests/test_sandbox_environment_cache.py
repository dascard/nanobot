from __future__ import annotations

import json
import os

import pytest

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.environment_service import (
    ENVIRONMENT_FINGERPRINT_FIELDS,
    ENVIRONMENT_FINGERPRINT_NAME,
    SandboxEnvironmentService,
)
from sandboxd.config import SandboxdConfig
from sandboxd.environment_manager import EnvironmentManager
from sandboxd.filesystem import WorkspaceFileService


IMAGE_A = "sha256:" + "a" * 64
IMAGE_B = "sha256:" + "b" * 64
WORKSPACE_A = "00000000-0000-0000-0000-000000000001"
WORKSPACE_B = "00000000-0000-0000-0000-000000000002"


class _Container:
    def __init__(self, *, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.exec_calls: list[dict[str, object]] = []

    def exec_run(self, command, **kwargs):
        self.exec_calls.append({
            "command": list(command),
            **dict(kwargs),
        })
        return self.exit_code, "命令输出不得进入环境指纹".encode()


def _components(tmp_path, *, clock=lambda: 1_785_000_000.0):
    config = SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=tmp_path / "sandboxd.token",
        client_token_path=tmp_path / "run" / "client.token",
        workspace_uid=os.getuid(),
        workspace_gid=os.getgid(),
        disk_min_free_bytes=0,
    ).validated()
    workspace_files = WorkspaceFileService(config)
    workspace_files.layout.ensure_roots()
    manager = EnvironmentManager(
        config,
        workspace_files=workspace_files,
        clock=clock,
    )
    return config, workspace_files, manager


def _prepare(
    manager: EnvironmentManager,
    config: SandboxdConfig,
    container: _Container,
    *,
    workspace_id: str = WORKSPACE_A,
    image_digest: str = IMAGE_A,
):
    catalog = config.profile_catalog
    assert catalog is not None
    return manager.prepare(
        container=container,
        workspace_id=workspace_id,
        profile_id="developer",
        catalog_generation=catalog.catalog_generation,
        policy_sha256=catalog.policy_sha256,
        image_digest=image_digest,
    )


def test_matching_fingerprint_skips_repeated_setup(tmp_path):
    config, workspace_files, manager = _components(tmp_path)
    workspace_files.ensure_workspace(WORKSPACE_A)
    container = _Container()

    first = _prepare(manager, config, container)
    second = _prepare(manager, config, container)

    assert first["action"] == "setup"
    assert second["action"] == "unchanged"
    assert first["ready"] is second["ready"] is True
    assert len(container.exec_calls) == 1
    assert container.exec_calls[0]["stdout"] is True
    assert container.exec_calls[0]["stderr"] is True
    runtime = workspace_files.layout.ensure_runtime(WORKSPACE_A)
    for relative in (
        "cache",
        "home",
        "npm-cache",
        "pip-cache",
        "pycache",
        "venvs",
    ):
        assert (runtime / relative).is_dir()


def test_image_profile_and_lockfile_changes_trigger_maintenance(tmp_path):
    config, workspace_files, manager = _components(tmp_path)
    workspace_files.ensure_workspace(WORKSPACE_A)
    container = _Container()

    assert _prepare(manager, config, container)["action"] == "setup"
    assert _prepare(
        manager,
        config,
        container,
        image_digest=IMAGE_B,
    )["action"] == "maintenance"

    fingerprint_path = (
        workspace_files.layout.ensure_runtime(WORKSPACE_A)
        / ENVIRONMENT_FINGERPRINT_NAME
    )
    fingerprint = json.loads(fingerprint_path.read_text(encoding="utf-8"))
    fingerprint["profile_id"] = "restricted"
    fingerprint_path.write_text(
        json.dumps(fingerprint, sort_keys=True),
        encoding="utf-8",
    )
    assert _prepare(
        manager,
        config,
        container,
        image_digest=IMAGE_B,
    )["action"] == "maintenance"

    workspace = workspace_files.layout.workspace_data_dir(WORKSPACE_A)
    (workspace / "package-lock.json").write_text(
        '{"lockfileVersion": 3}',
        encoding="utf-8",
    )
    assert _prepare(
        manager,
        config,
        container,
        image_digest=IMAGE_B,
    )["action"] == "maintenance"
    assert len(container.exec_calls) == 4


def test_fingerprint_contains_only_bounded_non_sensitive_fields(tmp_path):
    config, workspace_files, manager = _components(tmp_path)
    workspace_files.ensure_workspace(WORKSPACE_A)
    secret = "GITHUB_TOKEN_SHOULD_NEVER_BE_PERSISTED"
    workspace = workspace_files.layout.workspace_data_dir(WORKSPACE_A)
    (workspace / "requirements.lock").write_text(
        f"private-package==1.0  # {secret}",
        encoding="utf-8",
    )

    result = _prepare(manager, config, _Container())
    fingerprint_path = (
        workspace_files.layout.ensure_runtime(WORKSPACE_A)
        / ENVIRONMENT_FINGERPRINT_NAME
    )
    raw = fingerprint_path.read_text(encoding="utf-8")
    fingerprint = json.loads(raw)

    assert set(fingerprint) == ENVIRONMENT_FINGERPRINT_FIELDS
    assert set(fingerprint["selected_lockfile_hashes"]) == {
        "requirements.lock",
    }
    assert result["selected_lockfile_hashes"] == (
        fingerprint["selected_lockfile_hashes"]
    )
    assert secret not in raw
    assert str(tmp_path) not in raw
    for forbidden in (
        "workspace_id",
        "token",
        "secret",
        "host_path",
        "command",
        "stdout",
        "stderr",
    ):
        assert forbidden not in fingerprint


def test_repository_scripts_are_not_environment_commands(tmp_path):
    config, workspace_files, manager = _components(tmp_path)
    workspace_files.ensure_workspace(WORKSPACE_A)
    workspace = workspace_files.layout.workspace_data_dir(WORKSPACE_A)
    for relative_path in (
        "setup.sh",
        "Makefile",
        "postinstall",
    ):
        (workspace / relative_path).write_text(
            "DO_NOT_RUN_REPOSITORY_SCRIPT",
            encoding="utf-8",
        )
    container = _Container()

    result = _prepare(manager, config, container)

    assert result["action"] == "setup"
    assert len(container.exec_calls) == 1
    serialized = json.dumps(
        container.exec_calls,
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "DO_NOT_RUN_REPOSITORY_SCRIPT" not in serialized
    assert "setup.sh" not in serialized
    assert "Makefile" not in serialized
    assert "postinstall" not in serialized


def test_corrupt_fingerprint_repeats_setup_without_echoing_content(tmp_path):
    config, workspace_files, manager = _components(tmp_path)
    workspace_files.ensure_workspace(WORKSPACE_A)
    container = _Container()
    assert _prepare(manager, config, container)["action"] == "setup"
    fingerprint_path = (
        workspace_files.layout.ensure_runtime(WORKSPACE_A)
        / ENVIRONMENT_FINGERPRINT_NAME
    )
    fingerprint_path.write_text(
        '{"token":"CORRUPT_SECRET"',
        encoding="utf-8",
    )

    refreshed = _prepare(manager, config, container)

    assert refreshed["action"] == "setup"
    assert len(container.exec_calls) == 2
    assert "CORRUPT_SECRET" not in json.dumps(
        refreshed,
        ensure_ascii=False,
    )


def test_lockfile_symlink_is_not_followed(tmp_path):
    config, workspace_files, manager = _components(tmp_path)
    workspace_files.ensure_workspace(WORKSPACE_A)
    outside = tmp_path / "outside.lock"
    outside.write_text("OUTSIDE_SECRET", encoding="utf-8")
    workspace = workspace_files.layout.workspace_data_dir(WORKSPACE_A)
    (workspace / "requirements.lock").symlink_to(outside)

    result = _prepare(manager, config, _Container())

    assert result["selected_lockfile_hashes"] == {}
    assert "OUTSIDE_SECRET" not in json.dumps(result)


def test_cleanup_is_workspace_scoped_and_preserves_workspace_and_home(
    tmp_path,
):
    config, workspace_files, manager = _components(tmp_path)
    for workspace_id in (WORKSPACE_A, WORKSPACE_B):
        workspace_files.ensure_workspace(workspace_id)
        _prepare(
            manager,
            config,
            _Container(),
            workspace_id=workspace_id,
        )
        workspace = workspace_files.layout.workspace_data_dir(workspace_id)
        runtime = workspace_files.layout.ensure_runtime(workspace_id)
        (workspace / "kept.txt").write_text("workspace", encoding="utf-8")
        (runtime / "home" / "kept.txt").write_text("home", encoding="utf-8")
        (runtime / "venvs" / "cache.bin").write_bytes(b"cache")
    outside = tmp_path / "outside-kept.txt"
    outside.write_text("outside", encoding="utf-8")
    (
        workspace_files.layout.ensure_runtime(WORKSPACE_A)
        / "venvs"
        / "outside-link"
    ).symlink_to(outside)

    result = manager.cleanup(WORKSPACE_A)

    runtime_a = workspace_files.layout.ensure_runtime(WORKSPACE_A)
    runtime_b = workspace_files.layout.ensure_runtime(WORKSPACE_B)
    assert result["workspace_id"] == WORKSPACE_A
    assert result["fingerprint_removed"] is True
    assert not (runtime_a / "venvs").exists()
    assert not (runtime_a / ENVIRONMENT_FINGERPRINT_NAME).exists()
    assert (runtime_a / "home" / "kept.txt").read_text() == "home"
    assert (
        workspace_files.layout.workspace_data_dir(WORKSPACE_A)
        / "kept.txt"
    ).read_text() == "workspace"
    assert (runtime_b / "venvs" / "cache.bin").read_bytes() == b"cache"
    assert (runtime_b / ENVIRONMENT_FINGERPRINT_NAME).is_file()
    assert outside.read_text(encoding="utf-8") == "outside"


def test_environment_definition_and_controller_fact_fail_closed(tmp_path):
    config, workspace_files, manager = _components(tmp_path)
    workspace_files.ensure_workspace(WORKSPACE_A)
    service = SandboxEnvironmentService()

    with pytest.raises(SandboxServiceError) as unavailable:
        service.template("trusted_developer")
    assert unavailable.value.code is SandboxErrorCode.AUTHORIZATION_FAILED

    fact = _prepare(manager, config, _Container())
    validated = service.require_ready(
        fact,
        profile_id="developer",
        catalog_generation=config.profile_catalog.catalog_generation,
        policy_sha256=config.profile_catalog.policy_sha256,
        image_digest=IMAGE_A,
    )
    assert validated["ready"] is True

    with pytest.raises(SandboxServiceError) as drift:
        service.require_ready(
            {
                **fact,
                "setup_definition_sha256": "f" * 64,
            },
            profile_id="developer",
            catalog_generation=config.profile_catalog.catalog_generation,
            policy_sha256=config.profile_catalog.policy_sha256,
            image_digest=IMAGE_A,
        )
    assert drift.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE


def test_failed_setup_does_not_publish_fingerprint(tmp_path):
    config, workspace_files, manager = _components(tmp_path)
    workspace_files.ensure_workspace(WORKSPACE_A)

    with pytest.raises(SandboxServiceError) as failed:
        _prepare(manager, config, _Container(exit_code=17))

    assert failed.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE
    assert not (
        workspace_files.layout.ensure_runtime(WORKSPACE_A)
        / ENVIRONMENT_FINGERPRINT_NAME
    ).exists()
