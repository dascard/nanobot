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
    image_id = "sha256:" + marker * 64
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
            dependency_manifest_path="requirements-prod.lock",
            verification_suites=("backend-full",),
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
    ):
        from core.release.deployment import CommandResult

        self._result_type = CommandResult
        self.previous = previous.runtime_artifact
        self.target = target.runtime_artifact
        self.current = self.previous
        self.fail_service = fail_service
        self.mixed_current = mixed_current
        self.commands: list[_Command] = []

    def run(self, args, *, environment=None):
        command = tuple(args)
        env = dict(environment or {})
        self.commands.append(_Command(command, env))

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
                artifact.oci_image_id,
                artifact.source.git_full_commit,
                health,
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
        ready_url="http://127.0.0.1:8000/api/v1/ready",
        health_attempts=1,
        sleep=lambda _seconds: None,
        now=lambda: "2026-07-23T12:00:00+00:00",
    )


def test_successful_release_switches_all_services_and_rotates_state(
    tmp_path: Path,
):
    from core.release.artifacts import load_release_manifest

    previous = _release(marker="a", provenance="observed")
    target = _release(marker="b")
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


def test_successful_release_removes_only_superseded_exact_image_id(
    tmp_path: Path,
):
    from core.release.artifacts import load_release_manifest

    first = _release(marker="a", provenance="observed")
    second = _release(marker="b")
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
            first.runtime_artifact.oci_image_id,
        )
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

    target = _release(marker="b")
    manifest = tmp_path / "release.json"
    dump_release_manifest(manifest, target)
    monkeypatch.setenv(
        "NANOBOT_RUNTIME_IMAGE",
        target.runtime_artifact.oci_image_reference,
    )
    observed: list[str] = []

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
        str(tmp_path / "state"),
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
