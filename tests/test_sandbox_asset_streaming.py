import errno
import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from core.sandbox.asset_store import LocalAssetStore, safe_media_type
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.paths import SandboxStorageLayout
from sandboxd.app import SandboxRuntime, create_app
from sandboxd.auth import TokenAuthenticator
from sandboxd.config import SandboxdConfig
from sandboxd.filesystem import AssetFileService, WorkspaceFileService


IMAGE_ID = "sha256:" + "a" * 64
WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"


class _EmptyContainers:
    def list(self, **_kwargs):
        return []


class _FakeDockerBackend:
    class _Client:
        containers = _EmptyContainers()

    client = _Client()

    def ready(self):
        return {
            "docker": True,
            "image_id": IMAGE_ID,
            "apparmor_profile": "nanobot-sandbox",
            "disk_used_percent": 1.0,
            "disk_free_bytes": 10**12,
        }


def _runtime(tmp_path, *, asset_max_bytes=1024 * 1024):
    token = "t" * 64
    token_file = tmp_path / "sandboxd.token"
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o600)
    config = SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=token_file,
        client_token_path=tmp_path / "run" / "client.token",
        image_reference="nanobot-sandbox-python:test",
        image_allowlist=(IMAGE_ID,),
        workspace_uid=os.getuid(),
        workspace_gid=os.getgid(),
        disk_min_free_bytes=0,
        asset_max_bytes=asset_max_bytes,
    ).validated()
    workspace_files = WorkspaceFileService(config)
    asset_files = AssetFileService(config)
    return token, SandboxRuntime(
        config=config,
        authenticator=TokenAuthenticator(token_file, config.client_token_path),
        workspace_files=workspace_files,
        asset_files=asset_files,
        docker_backend=_FakeDockerBackend(),
    )


def _blob_path(layout: SandboxStorageLayout, content: bytes):
    digest = hashlib.sha256(content).hexdigest()
    return layout.assets_root / layout.asset_storage_key(digest)


def test_stream_writer_supports_empty_files_and_content_deduplication(tmp_path):
    layout = SandboxStorageLayout(tmp_path / "data")
    store = LocalAssetStore(layout, max_asset_bytes=1024)

    empty_writer = store.open_upload(media_type="text/plain")
    empty = empty_writer.finish()
    assert empty.sha256 == hashlib.sha256(b"").hexdigest()
    assert empty.size_bytes == 0

    first_writer = store.open_upload(media_type="text/plain")
    first_writer.write(b"same-content")
    first = first_writer.finish()
    first_path = layout.assets_root / first.storage_key
    first_inode = first_path.stat().st_ino

    second_writer = store.open_upload(media_type="text/plain")
    second_writer.write(b"same-")
    second_writer.write(b"content")
    second = second_writer.finish()

    assert second == first
    assert first_path.stat().st_ino == first_inode
    blobs = [
        path
        for path in layout.asset_blobs_root.rglob("*")
        if path.is_file()
    ]
    assert sorted(path.name for path in blobs) == sorted([empty.sha256, first.sha256])


def test_stream_writer_cleans_temporary_file_after_limit_and_disk_errors(
    tmp_path,
    monkeypatch,
):
    layout = SandboxStorageLayout(tmp_path / "data")
    store = LocalAssetStore(layout, max_asset_bytes=3)

    writer = store.open_upload()
    writer.write(b"ab")
    with pytest.raises(SandboxServiceError) as too_large:
        writer.write(b"cd")
    assert too_large.value.code is SandboxErrorCode.ASSET_TOO_LARGE
    writer.abort()

    disk_writer = store.open_upload()

    def fail_write(_fd, _value):
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(os, "write", fail_write)
    with pytest.raises(SandboxServiceError) as disk_pressure:
        disk_writer.write(b"x")
    assert disk_pressure.value.code is SandboxErrorCode.DISK_PRESSURE
    disk_writer.abort()

    temp_dir = layout.ensure_asset_temp()
    assert list(temp_dir.iterdir()) == []


def test_stream_writer_rejects_tampered_existing_blob(tmp_path):
    layout = SandboxStorageLayout(tmp_path / "data")
    store = LocalAssetStore(layout, max_asset_bytes=1024)
    original = b"same"

    writer = store.open_upload()
    writer.write(original)
    published = writer.finish()
    target = layout.assets_root / published.storage_key
    target.chmod(0o640)
    target.write_bytes(b"evil")

    duplicate = store.open_upload()
    duplicate.write(original)
    with pytest.raises(SandboxServiceError) as corrupted:
        duplicate.finish()
    assert corrupted.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE
    assert list(layout.ensure_asset_temp().iterdir()) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("text/plain", "text/plain"),
        ("application/vnd.api+json", "application/vnd.api+json"),
        ("text/plain; charset=utf-8", "application/octet-stream"),
        ("text/plain\r\nx-unsafe: yes", "application/octet-stream"),
        ("not-a-media-type", "application/octet-stream"),
    ],
)
def test_media_type_is_restricted_to_safe_type_and_subtype(value, expected):
    assert safe_media_type(value) == expected


def test_sandboxd_stream_upload_and_single_range_download(tmp_path):
    token, runtime = _runtime(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    content = b"0123456789"
    digest = hashlib.sha256(content).hexdigest()

    with TestClient(create_app(runtime)) as client:
        unauthorized = client.post(
            "/v1/assets/upload",
            params={"workspace_id": WORKSPACE_ID},
            content=content,
        )
        assert unauthorized.status_code == 403

        first = client.post(
            "/v1/assets/upload",
            params={
                "workspace_id": WORKSPACE_ID,
                "media_type": "text/plain",
            },
            headers=headers,
            content=content,
        )
        second = client.post(
            "/v1/assets/upload",
            params={"workspace_id": WORKSPACE_ID},
            headers=headers,
            content=content,
        )
        full = client.get(f"/v1/assets/{digest}", headers=headers)
        bounded = client.get(
            f"/v1/assets/{digest}",
            headers={**headers, "Range": "bytes=2-5"},
        )
        open_ended = client.get(
            f"/v1/assets/{digest}",
            headers={**headers, "Range": "bytes=6-"},
        )
        suffix = client.get(
            f"/v1/assets/{digest}",
            headers={**headers, "Range": "bytes=-3"},
        )
        invalid = client.get(
            f"/v1/assets/{digest}",
            headers={**headers, "Range": "bytes=20-30"},
        )
        multiple = client.get(
            f"/v1/assets/{digest}",
            headers={**headers, "Range": "bytes=0-1,3-4"},
        )

    assert first.status_code == 200
    assert first.json()["data"]["sha256"] == digest
    assert second.status_code == 200
    assert second.json()["data"]["sha256"] == digest
    assert full.status_code == 200
    assert full.content == content
    assert full.headers["content-length"] == str(len(content))
    assert bounded.status_code == 206
    assert bounded.content == b"2345"
    assert bounded.headers["content-range"] == "bytes 2-5/10"
    assert open_ended.content == b"6789"
    assert suffix.content == b"789"
    for response in (invalid, multiple):
        assert response.status_code == 416
        assert response.headers["content-range"] == "bytes */10"
        assert response.content == b""

    assert _blob_path(runtime.asset_files.layout, content).is_file()
    blobs = [
        path
        for path in runtime.asset_files.layout.asset_blobs_root.rglob("*")
        if path.is_file()
    ]
    assert [path.name for path in blobs] == [digest]


def test_sandboxd_handles_empty_asset_size_limit_and_not_found(tmp_path):
    token, runtime = _runtime(tmp_path, asset_max_bytes=4)
    headers = {"Authorization": f"Bearer {token}"}
    empty_digest = hashlib.sha256(b"").hexdigest()

    with TestClient(create_app(runtime)) as client:
        too_large = client.post(
            "/v1/assets/upload",
            params={"workspace_id": WORKSPACE_ID},
            headers=headers,
            content=b"12345",
        )
        empty_upload = client.post(
            "/v1/assets/upload",
            params={"workspace_id": WORKSPACE_ID},
            headers=headers,
            content=b"",
        )
        empty_download = client.get(
            f"/v1/assets/{empty_digest}",
            headers=headers,
        )
        empty_range = client.get(
            f"/v1/assets/{empty_digest}",
            headers={**headers, "Range": "bytes=0-0"},
        )
        missing = client.get(f"/v1/assets/{'f' * 64}", headers=headers)

    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "asset_too_large"
    assert empty_upload.status_code == 200
    assert empty_download.status_code == 200
    assert empty_download.headers["content-length"] == "0"
    assert empty_download.content == b""
    assert empty_range.status_code == 416
    assert empty_range.headers["content-range"] == "bytes */0"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "asset_not_found"
