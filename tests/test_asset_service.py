import os

import pytest

from core.database import Asset, WorkspaceAsset
from core.sandbox.asset_service import AssetService, LocalAssetStore
from core.sandbox.contracts import (
    PublishedAsset,
    SandboxErrorCode,
    SandboxServiceError,
)
from core.sandbox.identity import Principal
from core.sandbox.paths import SafeWorkspaceFilesystem, SandboxStorageLayout
from core.sandbox.workspace_service import WorkspaceService


def _principal(owner_id: str) -> Principal:
    return Principal(platform="qq", owner_type="user", owner_id=owner_id)


def _prepare_workspace(db_session, layout, principal, content):
    workspace_service = WorkspaceService(db_session)
    workspace = workspace_service.ensure_default(principal)
    root = layout.ensure_workspace(workspace.id)
    SafeWorkspaceFilesystem(root).write_bytes(
        "result.bin",
        content,
        overwrite=False,
        max_bytes=1024 * 1024,
    )
    return workspace_service, workspace


def test_asset_publish_deduplicates_blob_but_keeps_owner_links(db_session, tmp_path):
    layout = SandboxStorageLayout(tmp_path / "sandbox-data")
    principal_a = _principal("owner-A")
    service_a, workspace_a = _prepare_workspace(db_session, layout, principal_a, b"same-content")
    principal_b = _principal("owner-B")
    service_b, workspace_b = _prepare_workspace(db_session, layout, principal_b, b"same-content")
    local_store = LocalAssetStore(layout, max_asset_bytes=1024 * 1024)

    asset_a, link_a = AssetService(
        db_session,
        workspace_service=service_a,
        local_store=local_store,
        max_asset_bytes=1024 * 1024,
    ).publish_local_file(principal_a, "result.bin")
    asset_b, link_b = AssetService(
        db_session,
        workspace_service=service_b,
        local_store=local_store,
        max_asset_bytes=1024 * 1024,
    ).publish_local_file(principal_b, "result.bin")

    assert asset_a.sha256 == asset_b.sha256
    assert link_a.workspace_id == workspace_a.id
    assert link_b.workspace_id == workspace_b.id
    assert db_session.query(Asset).count() == 1
    assert db_session.query(WorkspaceAsset).count() == 2
    blob_path = layout.assets_root / asset_a.storage_key
    assert blob_path.read_bytes() == b"same-content"
    assert "owner-A" not in asset_a.storage_key
    assert "owner-B" not in asset_a.storage_key


def test_asset_missing_and_foreign_asset_share_safe_authorization_error(db_session, tmp_path):
    layout = SandboxStorageLayout(tmp_path / "sandbox-data")
    principal_a = _principal("owner-A")
    workspace_service_a, _workspace_a = _prepare_workspace(db_session, layout, principal_a, b"A")
    principal_b = _principal("owner-B")
    workspace_service_b = WorkspaceService(db_session)
    workspace_service_b.ensure_default(principal_b)
    local_store = LocalAssetStore(layout, max_asset_bytes=1024)
    asset, _link = AssetService(
        db_session,
        workspace_service=workspace_service_a,
        local_store=local_store,
        max_asset_bytes=1024,
    ).publish_local_file(principal_a, "result.bin")
    service_b = AssetService(
        db_session,
        workspace_service=workspace_service_b,
        local_store=local_store,
        max_asset_bytes=1024,
    )

    codes = []
    for sha256 in (asset.sha256, "0" * 64):
        with pytest.raises(SandboxServiceError) as raised:
            service_b.require_authorized(principal_b, sha256)
        codes.append(raised.value.code)
        assert asset.sha256 not in str(raised.value)

    assert codes == [
        SandboxErrorCode.ASSET_NOT_AUTHORIZED,
        SandboxErrorCode.ASSET_NOT_AUTHORIZED,
    ]


def test_asset_registration_recovers_from_concurrent_sha_insert(db_session):
    principal = _principal("owner-A")
    workspace_service = WorkspaceService(db_session)
    workspace_service.ensure_default(principal)
    digest = "a" * 64
    storage_key = SandboxStorageLayout.asset_storage_key(digest)
    db_session.add(Asset(
        sha256=digest,
        size_bytes=4,
        media_type="text/plain",
        storage_key=storage_key,
    ))
    db_session.commit()
    service = AssetService(
        db_session,
        workspace_service=workspace_service,
        max_asset_bytes=1024,
    )
    real_get = service.asset_repository.get
    calls = {"count": 0}

    def raced_get(sha256):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return real_get(sha256)

    service.asset_repository.get = raced_get
    asset, link = service.register_published(
        principal,
        PublishedAsset(
            sha256=digest,
            size_bytes=4,
            media_type="text/plain",
            storage_key=storage_key,
        ),
        logical_name="input.txt",
    )

    assert calls["count"] == 2
    assert asset.sha256 == digest
    assert link.asset_sha256 == digest
    assert db_session.query(Asset).filter_by(sha256=digest).count() == 1


def test_asset_registration_maps_concurrent_logical_name_conflict(db_session):
    principal = _principal("owner-A")
    workspace_service = WorkspaceService(db_session)
    workspace = workspace_service.ensure_default(principal)
    first_digest = "a" * 64
    second_digest = "b" * 64
    for digest in (first_digest, second_digest):
        db_session.add(Asset(
            sha256=digest,
            size_bytes=4,
            media_type="text/plain",
            storage_key=SandboxStorageLayout.asset_storage_key(digest),
        ))
    db_session.add(WorkspaceAsset(
        workspace_id=workspace.id,
        asset_sha256=first_digest,
        logical_name="same.txt",
    ))
    db_session.commit()
    service = AssetService(
        db_session,
        workspace_service=workspace_service,
        max_asset_bytes=1024,
    )
    real_get = service.link_repository.get_by_logical_name
    calls = {"count": 0}

    def raced_get(workspace_id, logical_name):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return real_get(workspace_id, logical_name)

    service.link_repository.get_by_logical_name = raced_get
    with pytest.raises(SandboxServiceError) as conflict:
        service.register_published(
            principal,
            PublishedAsset(
                sha256=second_digest,
                size_bytes=4,
                media_type="text/plain",
                storage_key=SandboxStorageLayout.asset_storage_key(second_digest),
            ),
            logical_name="same.txt",
        )

    assert calls["count"] == 2
    assert conflict.value.code is SandboxErrorCode.ASSET_NAME_CONFLICT


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_asset_publish_rejects_non_regular_workspace_files(db_session, tmp_path, kind):
    layout = SandboxStorageLayout(tmp_path / "sandbox-data")
    principal = _principal("owner-A")
    workspace_service = WorkspaceService(db_session)
    workspace = workspace_service.ensure_default(principal)
    root = layout.ensure_workspace(workspace.id)
    if kind == "symlink":
        os.symlink(tmp_path / "outside", root / "unsafe")
    else:
        os.mkfifo(root / "unsafe")
    service = AssetService(
        db_session,
        workspace_service=workspace_service,
        local_store=LocalAssetStore(layout, max_asset_bytes=1024),
        max_asset_bytes=1024,
    )

    with pytest.raises(SandboxServiceError) as raised:
        service.publish_local_file(principal, "unsafe")

    assert raised.value.code in {
        SandboxErrorCode.INVALID_PATH,
        SandboxErrorCode.UNSUPPORTED_FILE_TYPE,
    }
    assert str(tmp_path) not in str(raised.value)
