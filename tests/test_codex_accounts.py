"""Codex 多账号加密存储、轮询与账号绑定 Provider 测试。"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kohakuterrarium.llm.codex_auth import CodexTokens

from core.database import SystemSetting
from nanobot_kt.codex_accounts import (
    AccountBoundCodexOAuthProvider,
    CodexAccountPool,
    CodexCredentialConfigurationError,
    create_codex_account,
    delete_codex_account,
    list_codex_account_views,
    load_codex_account_tokens,
    refresh_codex_account_tokens,
    save_codex_account_tokens,
    update_codex_account,
)


@pytest.fixture(autouse=True)
def codex_credential_secret(monkeypatch):
    monkeypatch.setenv(
        "NANOBOT_CODEX_CREDENTIAL_SECRET",
        "codex-test-credential-secret-0123456789abcdef",
    )


def _tokens(label: str, *, expires_at: float | None = None) -> CodexTokens:
    return CodexTokens(
        access_token=f"access-{label}-secret",
        refresh_token=f"refresh-{label}-secret",
        expires_at=expires_at or time.time() + 3600,
        id_token=f"id-{label}-secret",
        account_id=f"openai-{label}",
    )


def test_account_creation_refuses_unencrypted_credential_storage(
    db_session,
    monkeypatch,
):
    monkeypatch.delenv("NANOBOT_CODEX_CREDENTIAL_SECRET", raising=False)
    monkeypatch.delenv("NANOBOT_ASSET_TOKEN_SECRET", raising=False)
    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda _key, _default=None: "",
    )

    with pytest.raises(CodexCredentialConfigurationError, match="至少 32 字节"):
        create_codex_account("不应落库", db=db_session)

    assert list_codex_account_views(db_session) == []


def test_codex_account_tokens_are_encrypted_and_never_exposed(db_session):
    account = create_codex_account("主账号", db=db_session)
    save_codex_account_tokens(account.id, _tokens("primary"), db=db_session)

    row = db_session.get(SystemSetting, account.setting_key)
    loaded = load_codex_account_tokens(account.id, db=db_session)
    views = list_codex_account_views(db_session)

    assert "access-primary-secret" not in row.value
    assert "refresh-primary-secret" not in row.value
    assert loaded.access_token == "access-primary-secret"
    assert views == [
        {
            "id": account.id,
            "name": "主账号",
            "enabled": True,
            "weight": 1,
            "status": "ready",
            "credential_configured": True,
            "expired": False,
            "expires_at": loaded.expires_at,
            "account_configured": True,
            "created_at": account.created_at,
            "updated_at": views[0]["updated_at"],
        }
    ]
    assert "access_token" not in views[0]
    assert "refresh_token" not in views[0]


def test_codex_account_pool_round_robins_new_sessions_and_keeps_sticky(db_session):
    first = create_codex_account("账号 A", db=db_session)
    second = create_codex_account("账号 B", db=db_session)
    save_codex_account_tokens(first.id, _tokens("a"), db=db_session)
    save_codex_account_tokens(second.id, _tokens("b"), db=db_session)
    pool = CodexAccountPool(max_sticky_sessions=10)

    first_order = pool.ordered_account_ids("session-a", db=db_session)
    second_order = pool.ordered_account_ids("session-b", db=db_session)
    sticky_order = pool.ordered_account_ids("session-a", db=db_session)
    pool.mark_success("session-a", second.id)
    failover_sticky_order = pool.ordered_account_ids("session-a", db=db_session)

    assert first_order == (first.id, second.id)
    assert second_order == (second.id, first.id)
    assert sticky_order == first_order
    assert failover_sticky_order == (second.id, first.id)


def test_codex_account_can_be_updated_disabled_and_deleted(db_session):
    account = create_codex_account("旧名称", db=db_session)
    updated = update_codex_account(
        account.id,
        name="新名称",
        enabled=False,
        weight=3,
        db=db_session,
    )

    assert updated.name == "新名称"
    assert updated.enabled is False
    assert updated.weight == 3
    assert delete_codex_account(account.id, db_session) is True
    assert list_codex_account_views(db_session) == []


@pytest.mark.asyncio
async def test_expired_account_refresh_rotates_encrypted_tokens(
    db_session,
    monkeypatch,
):
    account = create_codex_account("刷新账号", db=db_session)
    save_codex_account_tokens(
        account.id,
        _tokens("old", expires_at=time.time() - 60),
        db=db_session,
    )

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "access_token": "access-new-secret",
                "refresh_token": "refresh-new-secret",
                "expires_in": 7200,
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, data, headers):
            assert data["refresh_token"] == "refresh-old-secret"
            assert headers["Content-Type"] == "application/x-www-form-urlencoded"
            return _Response()

    monkeypatch.setattr("nanobot_kt.codex_accounts.httpx.AsyncClient", _Client)

    refreshed = await refresh_codex_account_tokens(account.id)
    db_session.expire_all()
    row = db_session.get(SystemSetting, account.setting_key)

    assert refreshed.access_token == "access-new-secret"
    assert load_codex_account_tokens(account.id, db=db_session).access_token == (
        "access-new-secret"
    )
    assert "access-new-secret" not in row.value


@pytest.mark.asyncio
async def test_account_bound_provider_never_starts_interactive_oauth(
    db_session,
    monkeypatch,
):
    account = create_codex_account("Provider 账号", db=db_session)
    tokens = _tokens("provider")
    save_codex_account_tokens(account.id, tokens, db=db_session)
    monkeypatch.setattr(
        "nanobot_kt.codex_accounts.load_codex_account_tokens",
        lambda _account_id: tokens,
    )
    provider = AccountBoundCodexOAuthProvider(
        model="gpt-5.6-codex",
        account_id=account.id,
    )
    rebuild = AsyncMock(return_value=None)
    monkeypatch.setattr(provider, "_rebuild_codex_connection", rebuild)

    await provider.ensure_authenticated()
    clone = provider.with_model("gpt-5.6-codex-mini")

    rebuild.assert_awaited_once()
    assert provider.is_authenticated is True
    assert clone.codex_account_id == account.id
    assert clone.is_authenticated is True


@pytest.mark.asyncio
async def test_account_bound_provider_uses_public_chat_contract(
    db_session,
    monkeypatch,
):
    from kohakuterrarium.llm.base import ToolSchema

    class _Stream:
        def __init__(self):
            self.events = iter(
                [
                    SimpleNamespace(
                        type="response.output_text.delta",
                        delta="收到",
                    ),
                    SimpleNamespace(
                        type="response.output_item.done",
                        item=SimpleNamespace(
                            type="function_call",
                            call_id="call-reply",
                            name="reply",
                            arguments='{"content":"收到"}',
                        ),
                    ),
                    SimpleNamespace(
                        type="response.completed",
                        response=SimpleNamespace(
                            usage=SimpleNamespace(
                                input_tokens=12,
                                output_tokens=3,
                                total_tokens=15,
                                input_tokens_details=SimpleNamespace(
                                    cached_tokens=4,
                                ),
                            ),
                        ),
                    ),
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.events)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    account = create_codex_account("公开合同账号", db=db_session)
    tokens = _tokens("public-contract")
    save_codex_account_tokens(account.id, tokens, db=db_session)
    monkeypatch.setattr(
        "nanobot_kt.codex_accounts.load_codex_account_tokens",
        lambda _account_id: tokens,
    )
    create_response = AsyncMock(return_value=_Stream())
    client_kwargs = []

    def build_client(**kwargs):
        client_kwargs.append(kwargs)

        async def close():
            await kwargs["http_client"].aclose()

        return SimpleNamespace(
            responses=SimpleNamespace(create=create_response),
            close=close,
        )

    monkeypatch.setattr(
        "nanobot_kt.codex_provider.AsyncOpenAI",
        build_client,
    )
    provider = AccountBoundCodexOAuthProvider(
        model="gpt-5.6-codex",
        account_id=account.id,
        reasoning_effort="high",
    )

    chunks = [
        chunk
        async for chunk in provider.chat(
            [
                {"role": "system", "content": "系统规则"},
                {"role": "user", "content": "你好"},
            ],
            tools=[
                ToolSchema(
                    name="reply",
                    description="回复",
                    parameters={"type": "object", "properties": {}},
                )
            ],
        )
    ]

    assert chunks == ["收到"]
    assert client_kwargs[0]["api_key"] == tokens.access_token
    request = create_response.await_args.kwargs
    assert request["model"] == "gpt-5.6-codex"
    assert request["instructions"] == "系统规则"
    assert request["tools"][0]["name"] == "reply"
    assert request["reasoning"] == {"effort": "high"}
    assert [(call.id, call.name) for call in provider.last_tool_calls] == [
        ("call-reply", "reply")
    ]
    assert provider.last_usage == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
        "total_tokens": 15,
        "cached_tokens": 4,
    }
    await provider.close()
