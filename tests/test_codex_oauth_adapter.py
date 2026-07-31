"""WebUI Codex Device OAuth 适配器测试。"""

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
    monkeypatch.setattr(
        "nanobot_kt.codex_oauth_adapter.CodexTokens.save",
        lambda tokens: saved.append(tokens),
    )
    manager = CodexDeviceLoginManager()

    started = await manager.start()
    await manager._sessions[started["login_id"]].task
    completed = await manager.get(started["login_id"])

    assert started["status"] == "pending"
    assert started["user_code"] == "ABCD-EFGH"
    assert "device_auth_id" not in started
    assert "access_token" not in started
    assert completed["status"] == "authenticated"
    assert completed["token_expires_at"] is not None
    assert len(saved) == 1
    assert saved[0].account_id == "account-id"


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
    manager = CodexDeviceLoginManager()

    started = await manager.start()
    await manager._sessions[started["login_id"]].task
    completed = await manager.get(started["login_id"])

    assert completed["status"] == "denied"
    assert completed["error"] == "用户拒绝授权"
