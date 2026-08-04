from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from core.registry import RegistrySnapshot


def _built_runtime_artifact():
    from core.release.artifacts import (
        ArtifactSource,
        build_artifact_manifest,
    )

    return build_artifact_manifest(
        profile_id="nanobot-runtime",
        provenance="built",
        source=ArtifactSource(
            git_full_commit="a" * 40,
            git_dirty=False,
        ),
        input_hashes={
            "prompt_defaults": "1" * 64,
            "python_lock": "2" * 64,
            "web_lock": "3" * 64,
        },
        schema_migration_head="20260723_release_manifest",
        oci_image_reference=(
            "registry.example/nanobot@sha256:" + "c" * 64
        ),
        oci_image_id="sha256:" + "d" * 64,
        sbom_path="artifacts/nanobot-runtime.spdx.json",
        sbom_sha256="4" * 64,
        dependency_manifest_path="requirements-prod.lock",
        dependency_manifest_sha256="5" * 64,
        verification_suites=(
            "backend-full",
            "frontend-lint-build",
        ),
        verification_results_path="artifacts/verification-results.json",
        verification_results_sha256="e" * 64,
        built_at="2026-07-23T12:00:00+00:00",
        builder_version="release-manifest-v1",
    )


def test_build_profiles_are_frozen_in_shared_registry():
    from core.release.artifacts import BUILD_PROFILE_REGISTRY

    assert isinstance(BUILD_PROFILE_REGISTRY, RegistrySnapshot)
    assert BUILD_PROFILE_REGISTRY.namespace == "build_profile"
    assert BUILD_PROFILE_REGISTRY.ordered_ids == (
        "nanobot-runtime",
        "nanobot-sandbox-python",
        "sandboxd",
        "webui",
    )
    runtime = BUILD_PROFILE_REGISTRY.require("nanobot-runtime")
    assert runtime.fixed_services == (
        "nanobot-server",
        "session-summary-worker",
        "outbound-delivery-worker",
        "semantic-index-worker",
    )


def test_artifact_manifest_is_immutable_and_deterministic():
    first = _built_runtime_artifact()
    second = _built_runtime_artifact()

    assert first == second
    assert first.sha256 == second.sha256
    assert first.canonical_json == second.canonical_json
    assert first.input_hashes == {
        "prompt_defaults": "1" * 64,
        "python_lock": "2" * 64,
        "web_lock": "3" * 64,
    }
    with pytest.raises(TypeError):
        first.input_hashes["new"] = "f" * 64
    with pytest.raises(FrozenInstanceError):
        first.profile_id = "webui"


def test_release_manifest_binds_all_fixed_services_to_one_runtime():
    from core.release.artifacts import build_release_manifest

    runtime = _built_runtime_artifact()
    release = build_release_manifest(
        artifacts=(runtime,),
        created_at="2026-07-23T12:01:00+00:00",
    )

    assert release.release_id.startswith("release_")
    assert release.runtime_artifact == runtime
    assert release.fixed_service_artifacts == {
        "nanobot-server": "nanobot-runtime",
        "session-summary-worker": "nanobot-runtime",
        "outbound-delivery-worker": "nanobot-runtime",
        "semantic-index-worker": "nanobot-runtime",
    }
    assert release.sha256


def test_release_manifest_round_trip_rejects_tampering(tmp_path: Path):
    from core.release.artifacts import (
        ReleaseManifestError,
        build_release_manifest,
        dump_release_manifest,
        load_release_manifest,
    )

    release = build_release_manifest(
        artifacts=(_built_runtime_artifact(),),
        created_at="2026-07-23T12:01:00+00:00",
    )
    path = tmp_path / "release.json"
    dump_release_manifest(path, release)

    loaded = load_release_manifest(path)
    assert loaded == release

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["artifacts"][0]["source"]["git_full_commit"] = "f" * 40
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReleaseManifestError, match="Hash"):
        load_release_manifest(path)


def test_release_manifest_rejects_unknown_fields(tmp_path: Path):
    from core.release.artifacts import (
        ReleaseManifestError,
        build_release_manifest,
        dump_release_manifest,
        load_release_manifest,
    )

    release = build_release_manifest(
        artifacts=(_built_runtime_artifact(),),
        created_at="2026-07-23T12:01:00+00:00",
    )
    path = tmp_path / "release.json"
    dump_release_manifest(path, release)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["untrusted_extension"] = {"skip_verification": True}
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReleaseManifestError, match="未知字段"):
        load_release_manifest(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("oci_image_reference", "nanobot-runtime:latest"),
        ("oci_image_id", "sha256:short"),
        ("sbom_sha256", "short"),
        ("verification_results_path", "/tmp/results.json"),
        ("verification_results_sha256", "short"),
        ("built_at", "2026-07-23T12:00:00"),
    ],
)
def test_built_artifact_rejects_incomplete_or_mutable_evidence(
    field: str,
    value: str,
):
    from core.release.artifacts import (
        ArtifactManifestError,
        ArtifactSource,
        build_artifact_manifest,
    )

    kwargs = {
        "profile_id": "nanobot-runtime",
        "provenance": "built",
        "source": ArtifactSource(
            git_full_commit="a" * 40,
            git_dirty=False,
        ),
        "input_hashes": {"python_lock": "2" * 64},
        "schema_migration_head": "20260723_release_manifest",
        "oci_image_reference": (
            "registry.example/nanobot@sha256:" + "c" * 64
        ),
        "oci_image_id": "sha256:" + "d" * 64,
        "sbom_path": "artifacts/runtime.spdx.json",
        "sbom_sha256": "4" * 64,
        "dependency_manifest_path": "requirements-prod.lock",
        "dependency_manifest_sha256": "5" * 64,
        "verification_suites": ("backend-full",),
        "verification_results_path": "artifacts/verification-results.json",
        "verification_results_sha256": "e" * 64,
        "built_at": "2026-07-23T12:00:00+00:00",
        "builder_version": "release-manifest-v1",
    }
    kwargs[field] = value

    with pytest.raises(ArtifactManifestError):
        build_artifact_manifest(**kwargs)


def test_runtime_artifact_requires_python_web_and_prompt_hashes():
    from core.release.artifacts import (
        ArtifactManifestError,
        ArtifactSource,
        build_artifact_manifest,
    )

    with pytest.raises(
        ArtifactManifestError,
        match="prompt_defaults.*web_lock",
    ):
        build_artifact_manifest(
            profile_id="nanobot-runtime",
            provenance="built",
            source=ArtifactSource(
                git_full_commit="a" * 40,
                git_dirty=False,
            ),
            input_hashes={"python_lock": "2" * 64},
            schema_migration_head="20260723_release_manifest",
            oci_image_reference=(
                "registry.example/nanobot@sha256:" + "c" * 64
            ),
            oci_image_id="sha256:" + "d" * 64,
            sbom_path="artifacts/runtime.spdx.json",
            sbom_sha256="4" * 64,
            dependency_manifest_path="requirements-prod.lock",
            dependency_manifest_sha256="5" * 64,
            verification_suites=("backend-full",),
            verification_results_path="artifacts/verification-results.json",
            verification_results_sha256="e" * 64,
            built_at="2026-07-23T12:00:00+00:00",
            builder_version="release-manifest-v1",
        )


def test_observed_runtime_can_only_be_used_as_rollback_provenance():
    from core.release.artifacts import (
        build_observed_runtime_artifact,
        build_release_manifest,
    )

    artifact = build_observed_runtime_artifact(
        image_reference=(
            "registry.example/nanobot@sha256:" + "1" * 64
        ),
        image_id="sha256:" + "2" * 64,
        revision="3" * 40,
        observed_at="2026-07-23T12:00:00+00:00",
    )
    release = build_release_manifest(
        artifacts=(artifact,),
        created_at="2026-07-23T12:00:00+00:00",
    )

    assert artifact.provenance == "observed"
    assert artifact.verification_suites == ()
    assert release.runtime_artifact.source.git_full_commit == "3" * 40


def test_observed_floating_image_reference_is_pinned_to_image_id():
    from core.release.artifacts import (
        build_observed_runtime_artifact,
    )

    image_id = "sha256:" + "2" * 64
    artifact = build_observed_runtime_artifact(
        image_reference="nanobot-runtime:latest",
        image_id=image_id,
        revision="3" * 40,
        observed_at="2026-07-23T12:00:00+00:00",
    )

    assert artifact.oci_image_reference == image_id
    assert artifact.oci_image_digest == image_id


def test_runtime_schema_verifier_requires_every_known_migration():
    from sqlalchemy import create_engine, text

    from core.release.runtime_verify import (
        RuntimeSchemaVerificationError,
        current_schema_migration_head,
        verify_schema_migrations,
    )
    from core.schema_migrations import MIGRATIONS

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "applied_at DATETIME"
            ")"
        ))
        for version, name, _function in MIGRATIONS[:-1]:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations(version, name) "
                    "VALUES (:version, :name)"
                ),
                {"version": version, "name": name},
            )

    with pytest.raises(
        RuntimeSchemaVerificationError,
        match=MIGRATIONS[-1][0],
    ):
        verify_schema_migrations(
            engine,
            expected_head=current_schema_migration_head(),
        )

    with engine.begin() as connection:
        version, name, _function = MIGRATIONS[-1]
        connection.execute(
            text(
                "INSERT INTO schema_migrations(version, name) "
                "VALUES (:version, :name)"
            ),
            {"version": version, "name": name},
        )

    verify_schema_migrations(
        engine,
        expected_head=current_schema_migration_head(),
    )
