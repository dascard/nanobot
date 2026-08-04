from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from core.agent_runtime import (
    RuntimeActor,
    RuntimeActorType,
    RuntimeArtifactPublishRequest,
    RuntimeArtifactResolveRequest,
    RuntimeOwnerType,
    RuntimePrincipal,
    RuntimeRunIdentity,
)
from core.artifact_port import SqlAlchemyArtifactPort
from core.asset_tokens import AssetTokenSigner
from core.asset_transport import artifact_preview_url, expand_artifact_refs_in_content
from core.database import WorkspaceAsset
from core.sandbox.backend import FakeSandboxBackend
from core.sandbox.paths import SandboxStorageLayout
from core.sandbox.workspace_service import WorkspaceService
from foundation.identity import Principal


def _owner(owner_id: str = "artifact-owner") -> RuntimePrincipal:
    return RuntimePrincipal(
        platform="qq",
        owner_type=RuntimeOwnerType.USER,
        owner_id=owner_id,
    )


def _identity(run_id: str, *, owner_id: str = "artifact-owner") -> RuntimeRunIdentity:
    return RuntimeRunIdentity(
        run_id=run_id,
        turn_id=f"turn-{run_id}",
        correlation_id=f"correlation-{run_id}",
        actor=RuntimeActor(RuntimeActorType.TOOL, "asset_publish"),
        owner=_owner(owner_id),
    )


def _publish_response(content: bytes) -> dict:
    digest = hashlib.sha256(content).hexdigest()
    return {
        "status": "success",
        "summary": "发布完成",
        "next_actions": [],
        "artifacts": [],
        "data": {
            "sha256": digest,
            "size_bytes": len(content),
            "media_type": "text/plain",
            "storage_key": SandboxStorageLayout.asset_storage_key(digest),
        },
    }


def test_sqlalchemy_artifact_port_versions_source_and_owner_acl(db_session):
    principal = Principal("qq", "user", "artifact-owner")
    workspace = WorkspaceService(db_session).ensure_default(principal)
    backend = FakeSandboxBackend()
    backend.set_response("publish_asset", _publish_response(b"version-one"))
    port = SqlAlchemyArtifactPort(
        db_session,
        backend=backend,
        asset_client_factory=None,
        max_asset_bytes=1024,
    )
    first = port.publish_sync(RuntimeArtifactPublishRequest(
        identity=_identity("run-artifact-one"),
        workspace_id=workspace.id,
        virtual_path="reports/result.txt",
        media_type="text/plain",
    ))
    backend.set_response("publish_asset", _publish_response(b"version-two"))
    second = port.publish_sync(RuntimeArtifactPublishRequest(
        identity=_identity("run-artifact-two"),
        workspace_id=workspace.id,
        virtual_path="reports/result.txt",
        media_type="text/plain",
    ))

    assert first.artifact_id.startswith("art_")
    assert first.uri == f"artifact://{first.artifact_id}"
    assert first.version == 1
    assert first.source_run_id == "run-artifact-one"
    assert second.version == 2
    assert second.source_run_id == "run-artifact-two"
    assert second.artifact_id != first.artifact_id
    assert port.resolve_sync(RuntimeArtifactResolveRequest(
        artifact_id=first.artifact_id,
        owner=_owner(),
    )) == first
    assert port.resolve_for_workspace_sync(
        workspace_id=workspace.id,
        artifact_id=first.artifact_id,
    ) == first
    assert port.resolve_sha_for_workspace_sync(
        workspace_id=workspace.id,
        sha256=first.sha256,
    ) == first
    with pytest.raises(PermissionError, match="owner 未授权"):
        port.resolve_for_workspace_sync(
            workspace_id="missing-workspace",
            artifact_id=first.artifact_id,
        )
    trusted, trusted_owner = port.resolve_trusted_sync(second.artifact_id)
    assert trusted == second
    assert trusted_owner == _owner()
    with pytest.raises(PermissionError, match="owner 未授权"):
        port.resolve_sync(RuntimeArtifactResolveRequest(
            artifact_id=first.artifact_id,
            owner=_owner("other-owner"),
        ))

    links = (
        db_session.query(WorkspaceAsset)
        .filter_by(workspace_id=workspace.id, logical_name="reports/result.txt")
        .order_by(WorkspaceAsset.version)
        .all()
    )
    assert [link.version for link in links] == [1, 2]
    assert [link.source_kind for link in links] == ["tool", "tool"]
    assert all(len(link.acl_sha256) == 64 for link in links)

    expanded = expand_artifact_refs_in_content(
        f"下载：[artifact:{second.artifact_id}]",
        db=db_session,
        signer=AssetTokenSigner("s" * 32),
        base_url="https://nanobot.example/base",
    )
    download_url = expanded.removeprefix("下载：")
    assert urlsplit(download_url).path == (
        f"/base/api/v1/assets/artifacts/{second.artifact_id}/download"
    )
    assert second.artifact_id not in urlsplit(download_url).query

    preview_url = artifact_preview_url(
        second.artifact_id,
        db=db_session,
        signer=AssetTokenSigner("s" * 32),
    )
    preview = urlsplit(preview_url)
    preview_query = parse_qs(preview.query)
    assert preview.path == (
        f"/api/v1/assets/artifacts/{second.artifact_id}/preview"
    )
    assert preview_query["recipient_id"] == ["qq:user:artifact-owner"]
    preview_claims = AssetTokenSigner("s" * 32).verify(
        preview_query["token"][0],
        recipient_type="session",
        recipient_id="qq:user:artifact-owner",
    )
    assert preview_claims.artifact_id == second.artifact_id


def test_sqlalchemy_artifact_port_rejects_acl_snapshot_tampering(db_session):
    principal = Principal("qq", "user", "artifact-owner")
    workspace = WorkspaceService(db_session).ensure_default(principal)
    backend = FakeSandboxBackend()
    backend.set_response("publish_asset", _publish_response(b"content"))
    port = SqlAlchemyArtifactPort(
        db_session,
        backend=backend,
        asset_client_factory=None,
        max_asset_bytes=1024,
    )
    artifact = port.publish_sync(RuntimeArtifactPublishRequest(
        identity=_identity("run-artifact-acl"),
        workspace_id=workspace.id,
        virtual_path="reports/acl.txt",
        media_type="text/plain",
    ))
    link = db_session.query(WorkspaceAsset).filter_by(
        artifact_id=artifact.artifact_id
    ).one()
    link.acl_sha256 = "0" * 64
    db_session.flush()

    with pytest.raises(PermissionError, match="owner 未授权"):
        port.resolve_sync(RuntimeArtifactResolveRequest(
            artifact_id=artifact.artifact_id,
            owner=_owner(),
        ))
