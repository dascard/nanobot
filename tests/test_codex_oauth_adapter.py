"""WebUI Codex Device OAuth 适配器测试。"""

from types import SimpleNamespace

import pytest

from nanobot_kt.codex_oauth_adapter import CodexDeviceLoginManager


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _OAuthClient:
    token_responses = []

    def __init__(self, *args, **kwargs):
        del args, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, json):
        del json
        if url.endswith("/deviceauth/usercode"):
            return _Response(200, {
                "device_auth_id": "device-auth-secret",
                "user_code": "ABCD-EFGH",
                "expires_in": 900,
                "interval": 2,
            })
        return self.token_responses.pop(0)


async def _no_sleep(_seconds):
    return None


def _mock_account_storage(monkeypatch, saved=None):
    account_id = "ca_test_account_0001"
    monkeypatch.setattr(
        "nanobot_kt.codex_accounts.ensure_codex_credential_encryption_ready",
        lambda: None,
    )
    monkeypatch.setattr(
        "nanobot_kt.codex_accounts.get_codex_account",
        lambda _account_id: SimpleNamespace(id=account_id),
    )
    monkeypatch.setattr(
        "nanobot_kt.codex_accounts.save_codex_account_tokens",
        lambda saved_account_id, tokens: (
            saved.append((saved_account_id, tokens)) if saved is not None else None
        ),
    )
    return account_id


@pytest.mark.asyncio
async def test_device_login_exposes_only_browser_safe_fields_and_saves_token(
    monkeypatch,
):
    _OAuthClient.token_responses = [
        _Response(200, {
            "access_token": "access-token-secret",
            "refresh_token": "refresh-token-secret",
            "expires_in": 3600,
            "account_id": "account-id",
        }),
    ]
    monkeypatch.setattr(
        "nanobot_kt.codex_oauth_adapter.httpx.AsyncClient",
        _OAuthClient,
    )
    monkeypatch.setattr(
        "nanobot_kt.codex_oauth_adapter.asyncio.sleep",
        _no_sleep,
    )
    saved = []
    account_id = _mock_account_storage(monkeypatch, saved)
    manager = CodexDeviceLoginManager()

    started = await manager.start(account_id)
    await manager._sessions[started["login_id"]].task
    completed = await manager.get(started["login_id"])

    assert started["status"] == "pending"
    assert started["account_id"] == account_id
    assert started["user_code"] == "ABCD-EFGH"
    assert "device_auth_id" not in started
    assert "access_token" not in started
    assert completed["status"] == "authenticated"
    assert completed["token_expires_at"] is not None
    assert len(saved) == 1
    assert saved[0][0] == account_id
    assert saved[0][1].account_id == "account-id"


@pytest.mark.asyncio
async def test_device_login_keeps_pending_then_reports_denial(monkeypatch):
    _OAuthClient.token_responses = [
        _Response(400, {"error": "authorization_pending"}),
        _Response(403, {
            "error": {
                "code": "access_denied",
                "message": "用户拒绝授权",
            },
        }),
    ]
    monkeypatch.setattr(
        "nanobot_kt.codex_oauth_adapter.httpx.AsyncClient",
        _OAuthClient,
    )
    monkeypatch.setattr(
        "nanobot_kt.codex_oauth_adapter.asyncio.sleep",
        _no_sleep,
    )
    account_id = _mock_account_storage(monkeypatch)
    manager = CodexDeviceLoginManager()

    started = await manager.start(account_id)
    await manager._sessions[started["login_id"]].task
    completed = await manager.get(started["login_id"])

    assert completed["status"] == "denied"
    assert completed["error"] == "用户拒绝授权"


@pytest.mark.asyncio
async def test_device_login_accepts_current_codex_pending_responses(monkeypatch):
    _OAuthClient.token_responses = [
        _Response(403, {
            "error": {
                "code": "deviceauth_authorization_pending",
                "message": "Device authorization is pending. Please try again.",
                "type": "invalid_request_error",
            },
        }),
        _Response(404, {}),
        _Response(200, {
            "access_token": "access-token-secret",
            "refresh_token": "refresh-token-secret",
            "expires_in": 3600,
        }),
    ]
    monkeypatch.setattr(
        "nanobot_kt.codex_oauth_adapter.httpx.AsyncClient",
        _OAuthClient,
    )
    monkeypatch.setattr(
        "nanobot_kt.codex_oauth_adapter.asyncio.sleep",
        _no_sleep,
    )
    account_id = _mock_account_storage(monkeypatch)
    manager = CodexDeviceLoginManager()

    started = await manager.start(account_id)
    await manager._sessions[started["login_id"]].task
    completed = await manager.get(started["login_id"])

    assert completed["status"] == "authenticated"
    assert completed["error"] == ""
