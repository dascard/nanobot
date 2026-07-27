from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

import pytest


SERVICES = (
    "nanobot-server",
    "session-summary-worker",
    "outbound-delivery-worker",
    "semantic-index-worker",
)
CONTAINERS = {
    "nanobot-server": "nanobot-server",
    "session-summary-worker": "nanobot-session-summary-worker",
    "outbound-delivery-worker": "nanobot-outbound-delivery-worker",
    "semantic-index-worker": "nanobot-semantic-index-worker",
}


def _release(
    *,
    marker: str,
    image_id_marker: str | None = None,
    provenance: str = "built",
    created_at: str = "2026-07-23T12:00:00+00:00",
):
    from core.release.artifacts import (
        ArtifactSource,
        build_artifact_manifest,
        build_observed_runtime_artifact,
        build_release_manifest,
    )

    image_reference = (
        f"registry.example/nanobot-{marker}@sha256:" + marker * 64
    )
    image_id = "sha256:" + (image_id_marker or marker) * 64
    revision = marker * 40
    if provenance == "observed":
        artifact = build_observed_runtime_artifact(
            image_reference=image_reference,
            image_id=image_id,
            revision=revision,
            observed_at=created_at,
        )
    else:
        artifact = build_artifact_manifest(
            profile_id="nanobot-runtime",
            provenance="built",
            source=ArtifactSource(
                git_full_commit=revision,
                git_dirty=False,
                kt_commit="f" * 40,
            ),
            input_hashes={
                "prompt_defaults": "1" * 64,
                "python_lock": "2" * 64,
                "web_lock": "3" * 64,
            },
            schema_migration_head="20260723_release_manifest",
            oci_image_reference=image_reference,
            oci_image_id=image_id,
            sbom_path="artifacts/runtime.spdx.json",
            sbom_sha256="5" * 64,
            dependency_manifest_path="requirements-prod.lock",
            dependency_manifest_sha256="6" * 64,
            verification_suites=("backend-full",),
            verification_results_path="artifacts/verification-results.json",
            verification_results_sha256="4" * 64,
            built_at=created_at,
            builder_version="release-manifest-v1",
        )
    return build_release_manifest(
        artifacts=(artifact,),
        created_at=created_at,
    )


@dataclass
class _Command:
    args: tuple[str, ...]
    environment: dict[str, str]


class _FakeRunner:
    def __init__(
        self,
        *,
        previous,
        target,
        fail_service: str | None = None,
        mixed_current: bool = False,
        feature_environment: tuple[str, ...] = (),
        nonfixed_snapshot: tuple[str, ...] = (
            "abc123\tdatabase\tpostgres:16\tUp 2 hours",
        ),
        nonfixed_snapshot_after_switch: tuple[str, ...] | None = None,
        active_sandbox_names: tuple[str, ...] = (),
        pulled_image_id: str | None = None,
        containerd_image_store: bool = False,
        insecure_target_runtime: bool = False,
    ):
        from core.release.deployment import CommandResult

        self._result_type = CommandResult
        self.previous = previous.runtime_artifact
        self.target = target.runtime_artifact
        self.current = self.previous
        self.fail_service = fail_service
        self.mixed_current = mixed_current
        self.feature_environment = feature_environment
        self.nonfixed_snapshot = nonfixed_snapshot
        self.nonfixed_snapshot_after_switch = nonfixed_snapshot_after_switch
        self.active_sandbox_names = active_sandbox_names
        self.pulled_image_id = pulled_image_id
        self.containerd_image_store = containerd_image_store
        self.insecure_target_runtime = insecure_target_runtime
        self.commands: list[_Command] = []

    def run(self, args, *, environment=None):
        command = tuple(args)
        env = dict(environment or {})
        self.commands.append(_Command(command, env))

        if (
            command[:3] == ("docker", "inspect", "--format")
            and command[3] == "{{json .Config.Env}}"
        ):
            import json

            return self._result_type(
                0,
                json.dumps(list(self.feature_environment)) + "\n",
                "",
            )

        if (
            command[:3] == ("docker", "inspect", "--format")
            and "ReadonlyRootfs" in command[3]
        ):
            container = command[-1]
            mounts = "/app/data=true;"
            if container == "nanobot-server":
                mounts += (
                    "/run/nanobot-sandboxd=false;"
                    "/var/lib/nanobot/prompt-runtime/live=true;"
                    "/var/lib/nanobot/prompt-runtime/state=true;"
                    "/var/lib/nanobot/prompt-runtime/backups=true;"
                )
            elif container == "nanobot-session-summary-worker":
                mounts += (
                    "/var/lib/nanobot/prompt-runtime/live=false;"
                    "/var/lib/nanobot/prompt-runtime/state=false;"
                )
            output = "|".join((
                (
                    "0:0"
                    if self.insecure_target_runtime and self.current == self.target
                    else "10001:10001"
                ),
                "true",
                '["ALL"]',
                '["no-new-privileges:true"]',
                "false",
                "128",
                "536870912",
                "500000000",
                mounts,
            ))
            return self._result_type(0, output + "\n", "")

        if command[:3] == ("docker", "inspect", "--format"):
            container = command[-1]
            service = next(
                name
                for name, expected in CONTAINERS.items()
                if expected == container
            )
            artifact = self.current
            if self.mixed_current and service == "semantic-index-worker":
                artifact = self.target
            health = "healthy"
            if (
                artifact == self.target
                and service == self.fail_service
            ):
                health = "unhealthy"
            output = "|".join((
                artifact.oci_image_reference,
                (
                    artifact.oci_image_digest
                    if self.containerd_image_store
                    else artifact.oci_image_id
                ),
                artifact.source.git_full_commit,
                health,
            ))
            return self._result_type(0, output + "\n", "")

        if command[:3] == ("docker", "image", "inspect"):
            artifact = self.target
            output = "|".join((
                self.pulled_image_id
                or (
                    artifact.oci_image_digest
                    if self.containerd_image_store
                    else artifact.oci_image_id
                ),
                artifact.source.git_full_commit,
                f'["{artifact.oci_image_reference}"]',
            ))
            return self._result_type(0, output + "\n", "")

        if "compose" in command and "up" in command:
            reference = env["NANOBOT_RUNTIME_IMAGE"]
            if reference == self.target.oci_image_reference:
                self.current = self.target
            elif reference == self.previous.oci_image_reference:
                self.current = self.previous
            else:
                return self._result_type(9, "", "unexpected image")
            return self._result_type(0, "", "")

        if command and command[0] == "curl":
            return self._result_type(0, "", "")
        if command[:3] == ("docker", "ps", "-a"):
            snapshot = self.nonfixed_snapshot
            if (
                self.current == self.target
                and self.nonfixed_snapshot_after_switch is not None
            ):
                snapshot = self.nonfixed_snapshot_after_switch
            return self._result_type(
                0,
                "\n".join(snapshot) + "\n",
                "",
            )
        if command[:2] == ("docker", "ps") and "--format" in command:
            output = "\n".join(self.active_sandbox_names)
            return self._result_type(0, output + ("\n" if output else ""), "")
        if command[:3] == ("docker", "ps", "-aq"):
            return self._result_type(0, "", "")
        if command[:3] == ("docker", "image", "rm"):
            return self._result_type(0, "", "")
        if "compose" in command and "exec" in command:
            return self._result_type(0, "", "")
        if "compose" in command:
            return self._result_type(0, "", "")
        return self._result_type(99, "", "unexpected command")


def _deployer(tmp_path: Path, runner: _FakeRunner):
    from core.release.deployment import (
        AtomicRuntimeDeployer,
        ReleaseStateStore,
    )

    return AtomicRuntimeDeployer(
        runner=runner,
        state_store=ReleaseStateStore(tmp_path / "release-state"),
        compose_env_file=tmp_path / "production.env",
        ready_url="http://127.0.0.1:8000/api/v1/ready",
        disk_free_bytes=lambda: 100 * 1024 * 1024 * 1024,
        health_attempts=1,
        sleep=lambda _seconds: None,
        now=lambda: "2026-07-23T12:00:00+00:00",
    )


def test_successful_release_switches_all_services_and_rotates_state(
    tmp_path: Path,
):
    from core.release.artifacts import load_release_manifest

    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b", image_id_marker="c")
    runner = _FakeRunner(previous=previous, target=target)
    deployer = _deployer(tmp_path, runner)

    result = deployer.deploy(target)

    assert result.changed is True
    assert result.previous_release_id
    state = tmp_path / "release-state"
    assert load_release_manifest(state / "current.json") == target
    assert (
        load_release_manifest(state / "rollback.json")
        .runtime_artifact
        .oci_image_reference
        == previous.runtime_artifact.oci_image_reference
    )
    assert not (state / "pending.json").exists()
    up_commands = [
        command
        for command in runner.commands
        if "compose" in command.args and "up" in command.args
    ]
    assert len(up_commands) == 1
    assert up_commands[0].args[-4:] == SERVICES
    assert up_commands[0].args[:4] == (
        "docker",
        "compose",
        "--project-name",
        "nanobot",
    )
    assert up_commands[0].environment["NANOBOT_RUNTIME_IMAGE"] == (
        target.runtime_artifact.oci_image_reference
    )


def test_idempotent_target_adopts_full_built_manifest_without_recreate(
    tmp_path: Path,
):
    from core.release.artifacts import load_release_manifest

    target = _release(marker="b")
    runner = _FakeRunner(previous=target, target=target)
    deployer = _deployer(tmp_path, runner)

    result = deployer.deploy(target)

    assert result.changed is False
    current = load_release_manifest(
        tmp_path / "release-state/current.json"
    )
    assert current == target
    assert not any(
        "compose" in command.args and "up" in command.args
        for command in runner.commands
    )


def test_containerd_index_id_switches_and_reconciles_without_recreate(
    tmp_path: Path,
):
    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b", image_id_marker="c")
    first_runner = _FakeRunner(
        previous=previous,
        target=target,
        containerd_image_store=True,
    )

    first_result = _deployer(tmp_path, first_runner).deploy(target)

    assert first_result.changed is True
    second_runner = _FakeRunner(
        previous=target,
        target=target,
        containerd_image_store=True,
    )

    second_result = _deployer(tmp_path, second_runner).deploy(target)

    assert second_result.changed is False
    assert not any(
        "compose" in command.args and "up" in command.args
        for command in second_runner.commands
    )


def test_successful_release_removes_only_superseded_immutable_reference(
    tmp_path: Path,
):
    from core.release.artifacts import load_release_manifest

    first = _release(
        marker="a",
        image_id_marker="e",
        provenance="observed",
    )
    second = _release(marker="b", image_id_marker="f")
    first_runner = _FakeRunner(previous=first, target=second)
    _deployer(tmp_path, first_runner).deploy(second)

    third = _release(
        marker="c",
        created_at="2026-07-23T13:00:00+00:00",
    )
    second_runner = _FakeRunner(previous=second, target=third)
    _deployer(tmp_path, second_runner).deploy(third)

    state = tmp_path / "release-state"
    rollback = load_release_manifest(state / "rollback.json")
    assert rollback == second
    image_rm = [
        command.args
        for command in second_runner.commands
        if command.args[:3] == ("docker", "image", "rm")
    ]
    assert image_rm == [
        (
            "docker",
            "image",
            "rm",
            first.runtime_artifact.oci_image_reference,
        )
    ]
    ancestor_checks = [
        command.args[-1]
        for command in second_runner.commands
        if command.args[:3] == ("docker", "ps", "-aq")
    ]
    assert ancestor_checks == [
        f"ancestor={first.runtime_artifact.oci_image_reference}"
    ]
    assert not any(
        "prune" in argument
        for command in second_runner.commands
        for argument in command.args
    )


@pytest.mark.parametrize(
    "fail_service",
    [
        "session-summary-worker",
        "outbound-delivery-worker",
        "semantic-index-worker",
    ],
)
def test_any_worker_failure_rolls_back_all_four_services(
    tmp_path: Path,
    fail_service: str,
):
    from core.release.artifacts import load_release_manifest
    from core.release.deployment import AtomicDeploymentError

    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b")
    runner = _FakeRunner(
        previous=previous,
        target=target,
        fail_service=fail_service,
    )
    deployer = _deployer(tmp_path, runner)

    with pytest.raises(AtomicDeploymentError) as caught:
        deployer.deploy(target)

    assert caught.value.rollback_succeeded is True
    assert runner.current == previous.runtime_artifact
    state = tmp_path / "release-state"
    current = load_release_manifest(state / "current.json")
    assert current.runtime_artifact.oci_image_reference == (
        previous.runtime_artifact.oci_image_reference
    )
    assert not (state / "pending.json").exists()
    up_commands = [
        command
        for command in runner.commands
        if "compose" in command.args and "up" in command.args
    ]
    assert len(up_commands) == 2
    assert all(command.args[-4:] == SERVICES for command in up_commands)
    assert up_commands[1].environment["NANOBOT_RUNTIME_IMAGE"] == (
        previous.runtime_artifact.oci_image_reference
    )


def test_mixed_current_service_revisions_fail_before_any_mutation(
    tmp_path: Path,
):
    from core.release.deployment import DeploymentVerificationError

    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b")
    runner = _FakeRunner(
        previous=previous,
        target=target,
        mixed_current=True,
    )
    deployer = _deployer(tmp_path, runner)

    with pytest.raises(
        DeploymentVerificationError,
        match="不一致",
    ):
        deployer.deploy(target)

    assert not any(
        "compose" in command.args and "up" in command.args
        for command in runner.commands
    )


def test_observed_manifest_cannot_be_used_as_new_target(tmp_path: Path):
    from core.release.deployment import DeploymentVerificationError

    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b", provenance="observed")
    runner = _FakeRunner(previous=previous, target=target)
    deployer = _deployer(tmp_path, runner)

    with pytest.raises(
        DeploymentVerificationError,
        match="built",
    ):
        deployer.deploy(target)

    assert runner.commands == []


def test_enabled_runtime_infrastructure_permission_allows_release_switch(
    tmp_path,
):
    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b")
    runner = _FakeRunner(
        previous=previous,
        target=target,
        feature_environment=(
            "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED=true",
        ),
    )

    result = _deployer(tmp_path, runner).deploy(target)

    assert result.changed is True
    assert runner.current == target.runtime_artifact


def test_enabled_runtime_business_feature_blocks_before_any_compose_mutation(
    tmp_path,
):
    from core.release.deployment import DeploymentVerificationError

    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b")
    runner = _FakeRunner(
        previous=previous,
        target=target,
        feature_environment=(
            "NANOBOT_SANDBOX_ENABLED=true",
        ),
    )

    with pytest.raises(DeploymentVerificationError, match="硬开关"):
        _deployer(tmp_path, runner).deploy(target)

    assert not any("compose" in command.args for command in runner.commands)


def test_invalid_runtime_infrastructure_permission_blocks_before_compose(
    tmp_path,
):
    from core.release.deployment import DeploymentVerificationError

    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b")
    runner = _FakeRunner(
        previous=previous,
        target=target,
        feature_environment=(
            "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED=invalid",
        ),
    )

    with pytest.raises(DeploymentVerificationError, match="不是合法布尔值"):
        _deployer(tmp_path, runner).deploy(target)

    assert not any("compose" in command.args for command in runner.commands)


def test_active_sandbox_blocks_before_any_compose_mutation(tmp_path):
    from core.release.deployment import DeploymentVerificationError

    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b")
    runner = _FakeRunner(
        previous=previous,
        target=target,
        active_sandbox_names=("nanobot-sandbox-active",),
    )

    with pytest.raises(DeploymentVerificationError, match="活动 Sandbox"):
        _deployer(tmp_path, runner).deploy(target)

    assert not any("compose" in command.args for command in runner.commands)


def test_nonfixed_container_change_rolls_back_all_runtime_services(tmp_path):
    from core.release.deployment import AtomicDeploymentError

    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b")
    runner = _FakeRunner(
        previous=previous,
        target=target,
        nonfixed_snapshot_after_switch=(
            "changed\tdatabase\tpostgres:16\tUp 2 hours",
        ),
    )

    with pytest.raises(AtomicDeploymentError) as caught:
        _deployer(tmp_path, runner).deploy(target)

    assert caught.value.rollback_succeeded is True
    assert runner.current == previous.runtime_artifact


def test_pulled_image_id_mismatch_fails_before_container_switch(tmp_path):
    from core.release.deployment import DeploymentVerificationError

    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b", image_id_marker="c")
    runner = _FakeRunner(
        previous=previous,
        target=target,
        pulled_image_id="sha256:" + "f" * 64,
    )

    with pytest.raises(DeploymentVerificationError, match="镜像存储 ID"):
        _deployer(tmp_path, runner).deploy(target)

    assert runner.current == previous.runtime_artifact
    assert not any(
        "compose" in command.args and "up" in command.args
        for command in runner.commands
    )


def test_runtime_security_boundary_failure_rolls_back_all_services(tmp_path):
    from core.release.deployment import AtomicDeploymentError

    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b")
    runner = _FakeRunner(
        previous=previous,
        target=target,
        insecure_target_runtime=True,
    )

    with pytest.raises(AtomicDeploymentError) as caught:
        _deployer(tmp_path, runner).deploy(target)

    assert caught.value.rollback_succeeded is True
    assert runner.current == previous.runtime_artifact


def test_deploy_cli_requires_manifest_to_match_runtime_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from core.release.artifacts import dump_release_manifest
    from scripts.deploy_release import main

    target = _release(marker="b")
    manifest = tmp_path / "release.json"
    dump_release_manifest(manifest, target)
    monkeypatch.setenv(
        "NANOBOT_RUNTIME_IMAGE",
        "registry.example/other@sha256:" + "e" * 64,
    )

    assert main([
        "--manifest",
        str(manifest),
        "--state-dir",
        str(tmp_path / "state"),
        *(_required_cli_arguments(tmp_path)),
    ]) == 2


def test_deploy_cli_invokes_atomic_deployer_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from core.release.artifacts import dump_release_manifest
    from core.release.deployment import (
        AtomicRuntimeDeployer,
        DeploymentResult,
    )
    from scripts.deploy_release import main
    import core.release.production_preflight as preflight

    target = _release(marker="b")
    manifest = tmp_path / "release.json"
    dump_release_manifest(manifest, target)
    monkeypatch.setenv(
        "NANOBOT_RUNTIME_IMAGE",
        target.runtime_artifact.oci_image_reference,
    )
    observed: list[str] = []
    production_root = tmp_path / "production"
    production_root.mkdir()
    for directory in ("data", "models", "sentinel"):
        (production_root / directory).mkdir()
    (production_root / ".env").write_text("\n", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    monkeypatch.setattr(
        preflight,
        "validate_production_paths",
        lambda **_kwargs: {
            "environment_file": production_root / ".env",
            "data_dir": production_root / "data",
            "models_dir": production_root / "models",
            "sentinel_dir": production_root / "sentinel",
            "prompt_host_root": tmp_path / "prompt",
        },
    )
    monkeypatch.setattr(preflight, "validate_pull_disk_gate", lambda **_kwargs: 1)
    monkeypatch.setattr(
        preflight,
        "validate_release_source_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        preflight,
        "validate_release_artifact_evidence",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(preflight, "validate_coordinated_backup", lambda **_kwargs: {})
    monkeypatch.setattr(preflight, "validate_prompt_audit_receipt", lambda **_kwargs: {})
    monkeypatch.setattr(
        preflight,
        "validate_database_feature_kill_switches",
        lambda _path: None,
    )

    def fake_deploy(_self, release):
        observed.append(release.release_id)
        return DeploymentResult(
            release_id=release.release_id,
            previous_release_id="release_previous",
            changed=True,
        )

    monkeypatch.setattr(AtomicRuntimeDeployer, "deploy", fake_deploy)

    assert main([
        "--manifest",
        str(manifest),
        "--state-dir",
        str(state_dir),
        *(_required_cli_arguments(tmp_path, production_root=production_root)),
        "--health-timeout-seconds",
        "1",
        "--health-interval-seconds",
        "1",
    ]) == 0
    assert observed == [target.release_id]


def test_subprocess_runner_merges_only_explicit_environment_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts.deploy_release import SubprocessCommandRunner

    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="ok",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = SubprocessCommandRunner(
        root=tmp_path,
        timeout_seconds=9,
    )

    result = runner.run(
        ("docker", "compose", "config"),
        environment={"NANOBOT_RUNTIME_IMAGE": "immutable"},
    )

    assert result.returncode == 0
    assert captured["args"] == (
        "docker",
        "compose",
        "config",
    )
    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env"]["NANOBOT_RUNTIME_IMAGE"] == "immutable"
    assert kwargs["timeout"] == 9
    assert "shell" not in kwargs


def _required_cli_arguments(
    tmp_path: Path,
    *,
    production_root: Path | None = None,
) -> tuple[str, ...]:
    root = production_root or tmp_path / "production"
    return (
        "--production-root",
        str(root),
        "--compose-env-file",
        str(root / ".env"),
        "--database",
        str(root / "data/nanobot.db"),
        "--sandbox-data-root",
        str(tmp_path / "sandbox-data"),
        "--backup-dir",
        str(tmp_path / "backup"),
        "--backup-risk-marker",
        "single_disk_logical_rollback_only",
        "--prompt-host-root",
        str(tmp_path / "prompt"),
        "--prompt-audit-receipt",
        str(tmp_path / "prompt/receipts/audit.json"),
    )
