import hashlib

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import asset_routes, routes
from api.common_auth import verify_token
from core.database import Asset, SystemSetting, Workspace, WorkspaceAsset, get_db
from core.sandbox.client import AsyncSandboxdAssetClient
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError, success_result
from core.sandbox.paths import SandboxStorageLayout


ASSET_CONTENT = b"streamed-asset-content"
ASSET_SHA256 = hashlib.sha256(ASSET_CONTENT).hexdigest()
WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"


def _asset_settings(db_session):
    values = {
        "sandbox.enabled": "1",
        "sandbox.group_enabled": "0",
        "sandbox.workspace_quota_bytes": str(2 * 1024 * 1024 * 1024),
        "sandbox.total_quota_bytes": str(10 * 1024 * 1024 * 1024),
        "sandbox.disk_max_percent": "80",
        "sandbox.disk_min_free_bytes": "0",
        "sandbox.asset_max_bytes": str(512 * 1024 * 1024),
        "sandbox.asset_token_secret": "s" * 32,
        "sandbox.asset_token_ttl_seconds": "900",
    }
    for key, value in values.items():
        db_session.add(SystemSetting(key=key, value=value))
    db_session.commit()


class _TrackedAsyncStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.closed = False

    async def __aiter__(self):
        midpoint = max(1, len(self.content) // 2)
        yield self.content[:midpoint]
        if midpoint < len(self.content):
            yield self.content[midpoint:]

    async def aclose(self) -> None:
        self.closed = True


class _OversizedErrorStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.chunks_sent = 0
        self.closed = False

    async def __aiter__(self):
        for _index in range(100):
            self.chunks_sent += 1
            yield b"x" * 1024

    async def aclose(self) -> None:
        self.closed = True


class _HandlerTransport(httpx.AsyncBaseTransport):
    """不预读请求体，便于验证上传 AsyncIterable 逐块到达传输层。"""

    def __init__(self, handler) -> None:
        self.handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.handler(request)


class _FakeAssetClient:
    def __init__(self) -> None:
        self.upload_chunks: list[bytes] = []
        self.upload_calls = 0
        self.open_calls: list[tuple[str, str]] = []
        self.close_calls = 0
        self.last_stream: _TrackedAsyncStream | None = None

    async def upload_asset(
        self,
        *,
        workspace_id,
        media_type,
        content,
        content_length,
        request_id,
    ):
        assert len(workspace_id) == 36
        assert media_type == "text/plain"
        assert content_length == len(ASSET_CONTENT)
        assert request_id.startswith("assetup_")
        self.upload_calls += 1
        async for chunk in content:
            self.upload_chunks.append(bytes(chunk))
        return success_result(
            "上传完成",
            data={
                "sha256": ASSET_SHA256,
                "size_bytes": len(ASSET_CONTENT),
                "media_type": "text/plain",
                "storage_key": SandboxStorageLayout.asset_storage_key(ASSET_SHA256),
            },
        )

    async def open_asset(self, sha256, *, range_header=""):
        self.open_calls.append((sha256, range_header))
        if range_header == "bytes=2-5":
            body = ASSET_CONTENT[2:6]
            status_code = 206
            headers = {
                "Content-Length": str(len(body)),
                "Content-Range": f"bytes 2-5/{len(ASSET_CONTENT)}",
            }
        elif range_header == "bytes=999-1000":
            body = b""
            status_code = 416
            headers = {
                "Content-Length": "0",
                "Content-Range": f"bytes */{len(ASSET_CONTENT)}",
            }
        else:
            body = ASSET_CONTENT
            status_code = 200
            headers = {"Content-Length": str(len(body))}
        self.last_stream = _TrackedAsyncStream(body)
        return httpx.Response(
            status_code,
            headers=headers,
            stream=self.last_stream,
            request=httpx.Request("GET", f"http://sandboxd/v1/assets/{sha256}"),
        )

    async def close(self):
        self.close_calls += 1


def _asset_api_client(db_session, monkeypatch, fake_client, *, bearer_override=True):
    app = FastAPI()
    app.include_router(asset_routes.router, prefix="/api/v1")

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    if bearer_override:
        app.dependency_overrides[verify_token] = lambda: None
    monkeypatch.setattr(asset_routes, "is_super_user_id", lambda _user_id: True)
    monkeypatch.setattr(asset_routes, "_asset_client", lambda _db: fake_client)
    return TestClient(app)


@pytest.mark.asyncio
async def test_async_asset_client_streams_request_and_accepts_range_statuses(tmp_path):
    token_file = tmp_path / "client.token"
    token_file.write_text("t" * 64, encoding="utf-8")
    token_file.chmod(0o640)
    observed_chunks = []

    async def handler(request: httpx.Request):
        assert request.headers["authorization"] == f"Bearer {'t' * 64}"
        if request.method == "POST":
            assert request.headers["content-length"] == "6"
            async for chunk in request.stream:
                observed_chunks.append(bytes(chunk))
            return httpx.Response(
                200,
                json=success_result("ok", data={"sha256": ASSET_SHA256}),
            )
        range_header = request.headers.get("range", "")
        status_code = 416 if range_header == "bytes=9-10" else 206
        return httpx.Response(
            status_code,
            headers={"Content-Range": "bytes */6"},
            content=b"" if status_code == 416 else b"bc",
        )

    async_client = httpx.AsyncClient(
        transport=_HandlerTransport(handler),
        base_url="http://sandboxd",
    )
    client = AsyncSandboxdAssetClient(
        socket_path="/not-used.sock",
        token_file=str(token_file),
        client=async_client,
    )

    async def chunks():
        yield b"ab"
        yield b"cd"
        yield b"ef"

    uploaded = await client.upload_asset(
        workspace_id=WORKSPACE_ID,
        media_type="text/plain",
        content=chunks(),
        content_length=6,
        request_id="assetup_test",
    )
    partial = await client.open_asset(ASSET_SHA256, range_header="bytes=1-2")
    unsatisfied = await client.open_asset(ASSET_SHA256, range_header="bytes=9-10")

    assert uploaded["status"] == "success"
    assert observed_chunks == [b"ab", b"cd", b"ef"]
    assert partial.status_code == 206
    assert await partial.aread() == b"bc"
    assert unsatisfied.status_code == 416
    assert await unsatisfied.aread() == b""
    await partial.aclose()
    await unsatisfied.aclose()
    await async_client.aclose()


@pytest.mark.asyncio
async def test_async_asset_client_maps_error_json_and_transport_failure(tmp_path):
    token_file = tmp_path / "client.token"
    token_file.write_text("t" * 64, encoding="utf-8")
    token_file.chmod(0o640)

    async def error_handler(_request):
        return httpx.Response(
            404,
            json=SandboxServiceError(
                SandboxErrorCode.ASSET_NOT_FOUND,
                "资产不存在",
            ).to_result(),
        )

    error_http = httpx.AsyncClient(
        transport=httpx.MockTransport(error_handler),
        base_url="http://sandboxd",
    )
    error_client = AsyncSandboxdAssetClient(
        socket_path="/not-used.sock",
        token_file=str(token_file),
        client=error_http,
    )
    with pytest.raises(SandboxServiceError) as missing:
        await error_client.open_asset(ASSET_SHA256)
    assert missing.value.code is SandboxErrorCode.ASSET_NOT_FOUND
    await error_http.aclose()

    async def transport_handler(request):
        raise httpx.ConnectError("内部 UDS 路径不得回显", request=request)

    failing_http = httpx.AsyncClient(
        transport=httpx.MockTransport(transport_handler),
        base_url="http://sandboxd",
    )
    failing_client = AsyncSandboxdAssetClient(
        socket_path="/not-used.sock",
        token_file=str(token_file),
        client=failing_http,
    )
    with pytest.raises(SandboxServiceError) as unavailable:
        await failing_client.open_asset(ASSET_SHA256)
    assert unavailable.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE
    assert "UDS" not in unavailable.value.summary
    await failing_http.aclose()


@pytest.mark.asyncio
async def test_async_asset_client_caps_non_success_error_body(tmp_path):
    token_file = tmp_path / "client.token"
    token_file.write_text("t" * 64, encoding="utf-8")
    token_file.chmod(0o640)
    stream = _OversizedErrorStream()

    async def handler(request):
        return httpx.Response(500, stream=stream, request=request)

    http = httpx.AsyncClient(
        transport=_HandlerTransport(handler),
        base_url="http://sandboxd",
    )
    client = AsyncSandboxdAssetClient(
        socket_path="/not-used.sock",
        token_file=str(token_file),
        client=http,
    )

    with pytest.raises(SandboxServiceError) as unavailable:
        await client.open_asset(ASSET_SHA256)

    assert unavailable.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE
    assert stream.chunks_sent == 4
    assert stream.closed is True
    await http.aclose()


@pytest.mark.asyncio
async def test_chunked_upload_stream_stops_before_forwarding_oversized_chunk():
    forwarded = []

    async def chunks():
        yield b"ab"
        yield b"cd"
        yield b"must-not-be-read"

    with pytest.raises(SandboxServiceError) as too_large:
        async for chunk in asset_routes._bounded_upload_stream(
            chunks(),
            max_bytes=3,
        ):
            forwarded.append(chunk)

    assert too_large.value.code is SandboxErrorCode.ASSET_TOO_LARGE
    assert forwarded == [b"ab"]


def test_external_asset_api_uploads_registers_authorization_and_streams_download(
    db_session,
    monkeypatch,
):
    _asset_settings(db_session)
    fake = _FakeAssetClient()
    client = _asset_api_client(db_session, monkeypatch, fake)

    upload = client.post(
        "/api/v1/assets/upload",
        params={
            "user_id": "10001",
            "logical_name": "inputs/report.txt",
            "platform": "qq",
            "chat_type": "private",
        },
        headers={"Content-Type": "text/plain"},
        content=ASSET_CONTENT,
    )

    assert upload.status_code == 200
    payload = upload.json()["data"]
    assert payload["source_ref"] == f"asset://sha256/{ASSET_SHA256}"
    assert payload["logical_name"] == "inputs/report.txt"
    assert payload["recipient_type"] == "user"
    assert payload["recipient_id"] == "10001"
    assert payload["transport_token"]
    assert payload["reply_token"] == (
        f"[asset_download:{payload['transport_token']}]"
    )
    assert fake.upload_calls == 1
    assert b"".join(fake.upload_chunks) == ASSET_CONTENT

    workspace = db_session.query(Workspace).one()
    asset = db_session.query(Asset).one()
    link = db_session.query(WorkspaceAsset).one()
    assert workspace.owner_id == "10001"
    assert asset.sha256 == ASSET_SHA256
    assert link.workspace_id == workspace.id
    assert link.asset_sha256 == asset.sha256

    download_params = {
        "token": payload["transport_token"],
        "recipient_type": "user",
        "recipient_id": "10001",
    }
    download = client.get(
        f"/api/v1/assets/{ASSET_SHA256}/download",
        params=download_params,
        headers={"Range": "bytes=2-5"},
    )

    assert download.status_code == 206
    assert download.content == ASSET_CONTENT[2:6]
    assert download.headers["content-range"] == (
        f"bytes 2-5/{len(ASSET_CONTENT)}"
    )
    assert download.headers["cache-control"] == "private, no-store"
    assert download.headers["referrer-policy"] == "no-referrer"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-type"].startswith("text/plain")
    assert fake.last_stream is not None and fake.last_stream.closed is True
    assert fake.close_calls == 2
    assert "/run/" not in download.text

    wrong_recipient = client.get(
        f"/api/v1/assets/{ASSET_SHA256}/download",
        params={**download_params, "recipient_id": "10002"},
    )
    wrong_asset = client.get(
        f"/api/v1/assets/{'f' * 64}/download",
        params=download_params,
    )
    tampered = client.get(
        f"/api/v1/assets/{ASSET_SHA256}/download",
        params={**download_params, "token": payload["transport_token"] + "x"},
    )
    for response in (wrong_recipient, wrong_asset, tampered):
        assert response.status_code == 404
        assert response.json()["detail"] == "资产不存在或下载凭据无效"
    assert fake.open_calls == [(ASSET_SHA256, "bytes=2-5")]


def test_external_asset_api_preserves_416_and_requires_trusted_bearer(
    db_session,
    monkeypatch,
):
    _asset_settings(db_session)
    fake = _FakeAssetClient()
    monkeypatch.setattr(routes, "NANOBOT_API_TOKEN", "gateway-secret")
    client = _asset_api_client(
        db_session,
        monkeypatch,
        fake,
        bearer_override=False,
    )
    params = {
        "user_id": "10001",
        "logical_name": "input.txt",
        "platform": "qq",
        "chat_type": "private",
    }

    unauthorized = client.post(
        "/api/v1/assets/upload",
        params=params,
        content=ASSET_CONTENT,
    )
    assert unauthorized.status_code == 401
    assert fake.upload_calls == 0

    upload = client.post(
        "/api/v1/assets/upload",
        params=params,
        headers={
            "Authorization": "Bearer gateway-secret",
            "Content-Type": "text/plain",
        },
        content=ASSET_CONTENT,
    )
    assert upload.status_code == 200
    payload = upload.json()["data"]
    unsatisfied = client.get(
        f"/api/v1/assets/{ASSET_SHA256}/download",
        params={
            "token": payload["transport_token"],
            "recipient_type": "user",
            "recipient_id": "10001",
        },
        headers={"Range": "bytes=999-1000"},
    )
    assert unsatisfied.status_code == 416
    assert unsatisfied.content == b""
    assert unsatisfied.headers["content-range"] == (
        f"bytes */{len(ASSET_CONTENT)}"
    )


def test_external_asset_api_rejects_oversized_content_length_before_upstream(
    db_session,
    monkeypatch,
):
    _asset_settings(db_session)
    row = db_session.get(SystemSetting, "sandbox.asset_max_bytes")
    row.value = "1024"
    db_session.commit()
    fake = _FakeAssetClient()
    client = _asset_api_client(db_session, monkeypatch, fake)

    response = client.post(
        "/api/v1/assets/upload",
        params={
            "user_id": "10001",
            "logical_name": "large.bin",
        },
        headers={"Content-Length": "1025"},
        content=b"",
    )

    assert response.status_code == 413
    assert response.json()["detail"]["error"]["code"] == "asset_too_large"
    assert fake.upload_calls == 0
