from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact():
    from core.release.artifacts import ArtifactSource, build_artifact_manifest

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
        schema_migration_head="20260724_admin_idempotency_records",
        oci_image_reference="ghcr.io/example/nanobot@sha256:" + "4" * 64,
        oci_image_id="sha256:" + "5" * 64,
        sbom_path="evidence/runtime.spdx.json",
        sbom_sha256="7" * 64,
        dependency_manifest_path="requirements-prod.lock",
        dependency_manifest_sha256="8" * 64,
        verification_suites=("backend-full",),
        verification_results_path="evidence/verification-results.json",
        verification_results_sha256="6" * 64,
        built_at=NOW.isoformat(),
        builder_version="test",
    )


def _prepare_path_layout(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "release"
    production = tmp_path / "production"
    prompt = tmp_path / "prompt"
    state = tmp_path / "release-state"
    for path in (
        source,
        production / "data",
        production / "models",
        production / "sentinel",
        prompt,
        state,
    ):
        path.mkdir(parents=True, exist_ok=True)
    (production / ".env").write_text("\n", encoding="utf-8")
    return {
        "source": source,
        "production": production,
        "prompt": prompt,
        "state": state,
    }


def test_production_paths_require_independent_release_and_prompt_roots(tmp_path):
    from core.release.production_preflight import (
        ProductionPreflightError,
        validate_production_paths,
    )

    paths = _prepare_path_layout(tmp_path)
    production = paths["production"]
    arguments = {
        "source_root": paths["source"],
        "production_root": production,
        "environment_file": production / ".env",
        "data_dir": production / "data",
        "models_dir": production / "models",
        "sentinel_dir": production / "sentinel",
        "prompt_host_root": paths["prompt"],
        "release_state_dir": paths["state"],
    }

    validated = validate_production_paths(**arguments)

    assert validated["prompt_host_root"] == paths["prompt"].resolve()
    with pytest.raises(ProductionPreflightError, match="独立发布树"):
        validate_production_paths(
            **{**arguments, "source_root": production}
        )
    with pytest.raises(ProductionPreflightError, match="checkout 之外"):
        validate_production_paths(
            **{
                **arguments,
                "prompt_host_root": production / "data",
            }
        )


def test_release_source_identity_requires_clean_matching_head(
    tmp_path,
    monkeypatch,
):
    import core.release.production_preflight as preflight

    source = tmp_path / "release"
    source.mkdir(parents=True)
    artifact = _artifact()

    def clean_git(root, *arguments):
        if arguments[:2] == ("status", "--porcelain"):
            return ""
        return artifact.source.git_full_commit

    monkeypatch.setattr(preflight, "_git", clean_git)
    preflight.validate_release_source_identity(source, artifact)

    monkeypatch.setattr(
        preflight,
        "_git",
        lambda _root, *_arguments: "f" * 40,
    )
    with pytest.raises(preflight.ProductionPreflightError, match="HEAD"):
        preflight.validate_release_source_identity(source, artifact)


def test_release_artifact_evidence_recomputes_all_bound_hashes(tmp_path):
    import core.release.production_preflight as preflight
    from core.release.artifacts import ArtifactSource, build_artifact_manifest

    source = tmp_path / "release"
    evidence = source / "evidence"
    prompt = source / "prompts.v2.default"
    web = source / "webui"
    evidence.mkdir(parents=True)
    prompt.mkdir()
    web.mkdir()
    dependency = source / "requirements-prod.lock"
    dependency.write_text("httpx==1\n", encoding="utf-8")
    (web / "package-lock.json").write_text(
        '{"lockfileVersion":3}\n',
        encoding="utf-8",
    )
    (prompt / "system.md").write_text("默认模板\n", encoding="utf-8")
    sbom = evidence / "runtime.spdx.json"
    sbom.write_text('{"spdxVersion":"SPDX-2.3"}\n', encoding="utf-8")
    verification = evidence / "verification-results.json"
    verification.write_text(
        json.dumps({
            "schema_version": 2,
            "source_sha": "a" * 40,
            "suites": {
                "backend-full": {
                    "run_id": "123",
                    "job": "backend",
                    "conclusion": "success",
                }
            },
        }),
        encoding="utf-8",
    )
    dependency_hash = _sha256(dependency)
    artifact = build_artifact_manifest(
        profile_id="nanobot-runtime",
        provenance="built",
        source=ArtifactSource(
            git_full_commit="a" * 40,
            git_dirty=False,
        ),
        input_hashes={
            "python_lock": dependency_hash,
            "web_lock": preflight._hash_repository_path(
                web / "package-lock.json"
            ),
            "prompt_defaults": preflight._hash_repository_path(prompt),
        },
        schema_migration_head="20260724_admin_idempotency_records",
        oci_image_reference="ghcr.io/example/nanobot@sha256:" + "4" * 64,
        oci_image_id="sha256:" + "5" * 64,
        sbom_path="evidence/runtime.spdx.json",
        sbom_sha256=_sha256(sbom),
        dependency_manifest_path="requirements-prod.lock",
        dependency_manifest_sha256=dependency_hash,
        verification_suites=("backend-full",),
        verification_results_path="evidence/verification-results.json",
        verification_results_sha256=_sha256(verification),
        built_at=NOW.isoformat(),
        builder_version="test",
    )

    preflight.validate_release_artifact_evidence(source, artifact)

    sbom.write_text('{"spdxVersion":"SPDX-2.2"}\n', encoding="utf-8")
    with pytest.raises(preflight.ProductionPreflightError, match="SBOM Hash"):
        preflight.validate_release_artifact_evidence(source, artifact)


def _create_backup(
    tmp_path: Path,
    *,
    database: Path,
    data_root: Path,
) -> Path:
    backup = tmp_path / "nanobot-sandbox-20260725T120000Z"
    backup.mkdir()
    with sqlite3.connect(backup / "nanobot.db") as connection:
        connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY)")
    (backup / "workspaces.tar").write_bytes(b"workspace-evidence")
    (backup / "assets.tar").write_bytes(b"asset-evidence")
    (backup / "manifest.txt").write_text(
        "\n".join((
            "created_at=20260725T120000Z",
            "hostname=acceptance-host",
            f"data_root={data_root}",
            "data_source=/dev/loop-test",
            f"database={database}",
            "backup_mode=local_same_disk",
            "backup_risk_marker=single_disk_logical_rollback_only",
            "backup_max_bytes=17179869184",
            "system_min_free_bytes=64424509440",
            "quiesced=true",
            "runtime_included=false",
            "input_staging_included=false",
        ))
        + "\n",
        encoding="utf-8",
    )
    names = ("nanobot.db", "workspaces.tar", "assets.tar", "manifest.txt")
    (backup / "manifest.sha256").write_text(
        "".join(f"{_sha256(backup / name)}  {name}\n" for name in names),
        encoding="utf-8",
    )
    return backup


def test_coordinated_backup_validates_hashes_and_sqlite(tmp_path):
    from core.release.production_preflight import (
        ProductionPreflightError,
        validate_coordinated_backup,
    )

    production = tmp_path / "production"
    data_root = tmp_path / "sandbox-data"
    production.mkdir()
    data_root.mkdir()
    database = production / "nanobot.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE runtime (id INTEGER PRIMARY KEY)")
    backup = _create_backup(tmp_path, database=database, data_root=data_root)

    result = validate_coordinated_backup(
        backup_dir=backup,
        database_path=database,
        data_root=data_root,
        expected_risk_marker="single_disk_logical_rollback_only",
        now=NOW,
    )

    assert result["created_at"] == "20260725T120000Z"
    (backup / "assets.tar").write_bytes(b"tampered")
    with pytest.raises(ProductionPreflightError, match="SHA-256"):
        validate_coordinated_backup(
            backup_dir=backup,
            database_path=database,
            data_root=data_root,
            expected_risk_marker="single_disk_logical_rollback_only",
            now=NOW,
        )


def _write_prompt_receipt(
    prompt_root: Path,
    *,
    artifact,
    passed: bool = True,
) -> Path:
    from core.registry.validation import canonical_json

    receipts = prompt_root / "receipts"
    receipts.mkdir()
    payload = {
        "schema_version": 1,
        "created_at": NOW.isoformat(),
        "image_reference": artifact.oci_image_reference,
        "git_full_commit": artifact.source.git_full_commit,
        "prompt_defaults_sha256": artifact.input_hashes["prompt_defaults"],
        "host_prompt_root_sha256": hashlib.sha256(
            str(prompt_root.resolve()).encode("utf-8")
        ).hexdigest(),
        "accepted_local_overrides": ["chat/identity_context"],
        "findings": [
            {
                "template_key": "chat/identity_context",
                "drift_status": "local_override",
                "default_sha256": "7" * 64,
                "runtime_sha256": "8" * 64,
                "baseline_sha256": "9" * 64,
                "baseline_version": "v1",
                "invalid_component": None,
            }
        ],
        "passed": passed,
    }
    payload["sha256"] = hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    receipt = receipts / "audit.json"
    receipt.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    receipt.chmod(0o440)
    return receipt


def test_prompt_receipt_is_bound_to_digest_prompt_hash_and_host_root(tmp_path):
    from core.release.production_preflight import (
        ProductionPreflightError,
        validate_prompt_audit_receipt,
    )

    prompt = tmp_path / "prompt"
    prompt.mkdir()
    artifact = _artifact()
    receipt = _write_prompt_receipt(prompt, artifact=artifact)

    validated = validate_prompt_audit_receipt(
        receipt_path=receipt,
        prompt_host_root=prompt,
        artifact=artifact,
        now=NOW,
    )

    assert validated["passed"] is True
    other_prompt = tmp_path / "other-prompt"
    other_prompt.mkdir()
    (other_prompt / "receipts").mkdir()
    copied = other_prompt / "receipts/audit.json"
    copied.write_bytes(receipt.read_bytes())
    copied.chmod(0o440)
    with pytest.raises(ProductionPreflightError, match="其他宿主路径"):
        validate_prompt_audit_receipt(
            receipt_path=copied,
            prompt_host_root=other_prompt,
            artifact=artifact,
            now=NOW,
        )


def test_database_feature_kill_switches_fail_closed(tmp_path):
    from core.release.production_preflight import (
        ProductionPreflightError,
        validate_database_feature_kill_switches,
    )

    database = tmp_path / "nanobot.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        connection.execute(
            "INSERT INTO system_settings (key, value) VALUES (?, ?)",
            ("sandbox.exec_enabled", "true"),
        )

    with pytest.raises(ProductionPreflightError, match="exec_enabled"):
        validate_database_feature_kill_switches(database)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE system_settings SET value='false' "
            "WHERE key='sandbox.exec_enabled'"
        )
    validate_database_feature_kill_switches(database)


def test_prompt_release_verifier_requires_explicit_local_override_acceptance(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.template_migration import TemplateMigrationService
    from core.release.artifacts import (
        ArtifactSource,
        build_artifact_manifest,
        build_release_manifest,
        dump_release_manifest,
    )
    from scripts.build_release_manifest import hash_repository_path
    from scripts.verify_prompt_runtime_release import main

    prompt_hash = hash_repository_path(
        Path(__file__).resolve().parents[1],
        "prompts.v2.default",
    )
    artifact = build_artifact_manifest(
        profile_id="nanobot-runtime",
        provenance="built",
        source=ArtifactSource(
            git_full_commit="a" * 40,
            git_dirty=False,
        ),
        input_hashes={
            "prompt_defaults": prompt_hash,
            "python_lock": "2" * 64,
            "web_lock": "3" * 64,
        },
        schema_migration_head="20260724_admin_idempotency_records",
        oci_image_reference="ghcr.io/example/nanobot@sha256:" + "4" * 64,
        oci_image_id="sha256:" + "5" * 64,
        sbom_path="evidence/runtime.spdx.json",
        sbom_sha256="7" * 64,
        dependency_manifest_path="requirements-prod.lock",
        dependency_manifest_sha256="8" * 64,
        verification_suites=("backend-full",),
        verification_results_path="evidence/verification-results.json",
        verification_results_sha256="6" * 64,
        built_at=NOW.isoformat(),
        builder_version="test",
    )
    release = build_release_manifest(
        artifacts=(artifact,),
        created_at=NOW.isoformat(),
    )
    manifest = tmp_path / "release.json"
    dump_release_manifest(manifest, release)

    class _AuditService:
        def audit(self):
            return [{
                "template_key": "chat/identity_context",
                "drift_status": "local_override",
                "default_sha256": "7" * 64,
                "runtime_sha256": "8" * 64,
                "baseline_sha256": "9" * 64,
                "baseline_version": "v1",
                "invalid_component": None,
                "private_prompt_body": "不得写入回执",
            }]

    monkeypatch.setattr(
        TemplateMigrationService,
        "from_environment",
        classmethod(lambda _cls: _AuditService()),
    )
    monkeypatch.setenv("NANOBOT_PROMPT_HOST_ROOT_SHA256", "d" * 64)
    output = tmp_path / "receipt.json"

    blocked = main([
        "--manifest",
        str(manifest),
        "--image-reference",
        artifact.oci_image_reference,
        "--output",
        str(output),
    ])
    passed = main([
        "--manifest",
        str(manifest),
        "--image-reference",
        artifact.oci_image_reference,
        "--accept-local-override",
        "chat/identity_context",
        "--output",
        str(output),
    ])

    assert blocked == 1
    assert passed == 0
    receipt_text = output.read_text(encoding="utf-8")
    assert "不得写入回执" not in receipt_text
    assert "private_prompt_body" not in receipt_text
