import base64
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy.orm import sessionmaker

from core.asset_tokens import AssetTokenError, AssetTokenSigner
from core.asset_transport import (
    build_asset_reply_token,
    expand_asset_download_refs_in_content,
    public_asset_download_url,
)


ASSET_SHA256 = "a" * 64
SECRET = "s" * 32


def _decode_payload(token: str) -> dict:
    encoded, _signature = token.split(".", 1)
    padding = "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded + padding))


def test_asset_token_binds_asset_recipient_and_expiry():
    signer = AssetTokenSigner(SECRET, default_ttl_seconds=300)

    token = signer.issue(
        ASSET_SHA256,
        recipient_type="user",
        recipient_id="10001",
        now=1_000,
    )
    claims = signer.verify(
        token,
        recipient_type="user",
        recipient_id="10001",
        now=1_299,
    )

    assert claims.asset_sha256 == ASSET_SHA256
    assert claims.recipient_type == "user"
    assert claims.recipient_id == "10001"
    assert claims.expires_at == 1_300
    assert _decode_payload(token)["exp"] == 1_300


@pytest.mark.parametrize(
    ("recipient_type", "recipient_id"),
    [("group", "10001"), ("user", "other")],
)
def test_asset_token_rejects_recipient_mismatch(recipient_type, recipient_id):
    signer = AssetTokenSigner(SECRET)
    token = signer.issue(
        ASSET_SHA256,
        recipient_type="user",
        recipient_id="10001",
        now=1_000,
    )

    with pytest.raises(AssetTokenError, match="收件人不匹配"):
        signer.verify(
            token,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            now=1_001,
        )


def test_asset_token_rejects_tampering_and_exact_expiry_boundary():
    signer = AssetTokenSigner(SECRET, default_ttl_seconds=60)
    token = signer.issue(
        ASSET_SHA256,
        recipient_type="user",
        recipient_id="10001",
        now=1_000,
    )
    encoded, signature = token.split(".", 1)

    with pytest.raises(AssetTokenError, match="无效"):
        signer.verify(f"{encoded}.{signature[:-1]}A", now=1_001)
    with pytest.raises(AssetTokenError, match="已过期"):
        signer.verify(token, now=1_060)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "missing-dot",
        "a.b.c",
        "非ASCII.签名",
        "a" * 8193,
        "@@.@@",
    ],
)
def test_asset_token_malformed_values_share_safe_error(token):
    signer = AssetTokenSigner(SECRET)

    with pytest.raises(AssetTokenError):
        signer.verify(token)


def test_asset_token_rejects_weak_secret_invalid_hash_and_zero_ttl():
    with pytest.raises(AssetTokenError, match="密钥未安全配置"):
        AssetTokenSigner("too-short")

    signer = AssetTokenSigner(SECRET)
    with pytest.raises(AssetTokenError, match="资源无效"):
        signer.issue(
            "not-a-hash",
            recipient_type="user",
            recipient_id="10001",
        )
    with pytest.raises(AssetTokenError, match="有效期配置无效"):
        signer.issue(
            ASSET_SHA256,
            recipient_type="user",
            recipient_id="10001",
            ttl_seconds=0,
        )


def test_asset_reply_token_expands_only_at_transport_boundary():
    signer = AssetTokenSigner(SECRET)
    transport_token = signer.issue(
        ASSET_SHA256,
        recipient_type="user",
        recipient_id="10001",
    )
    reply_token = build_asset_reply_token(transport_token)

    url = public_asset_download_url(
        transport_token,
        signer=signer,
        base_url="https://nanobot.example/base",
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)

    assert reply_token == f"[asset_download:{transport_token}]"
    assert parsed.path == f"/base/api/v1/assets/{ASSET_SHA256}/download"
    assert query == {
        "token": [transport_token],
        "recipient_type": ["user"],
        "recipient_id": ["10001"],
    }
    assert expand_asset_download_refs_in_content(
        f"下载：{reply_token}",
        signer=signer,
        base_url="https://nanobot.example",
    ).startswith(f"下载：https://nanobot.example/api/v1/assets/{ASSET_SHA256}/download?")


def test_asset_reply_token_hides_credential_when_transport_config_is_invalid():
    signer = AssetTokenSigner(SECRET)
    transport_token = signer.issue(
        ASSET_SHA256,
        recipient_type="user",
        recipient_id="10001",
    )
    reply_token = build_asset_reply_token(transport_token)

    for base_url in ("", "file:///tmp/assets", "https://user:pass@example.test"):
        expanded = expand_asset_download_refs_in_content(
            reply_token,
            signer=signer,
            base_url=base_url,
        )
        assert expanded == "（资产下载链接暂不可用）"
        assert transport_token not in expanded


def test_startup_token_validation_is_conditional_on_sandbox_enabled(
    db_session,
    monkeypatch,
):
    from bootstrap.lifespan import validate_sandbox_asset_token_config
    from core import database
    from core.database import SystemSetting

    db_session.add_all([
        SystemSetting(key="sandbox.enabled", value="false"),
        SystemSetting(key="sandbox.asset_token_secret", value=""),
    ])
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", factory)

    validate_sandbox_asset_token_config()

    enabled = db_session.get(SystemSetting, "sandbox.enabled")
    enabled.value = "true"
    db_session.commit()
    with pytest.raises(AssetTokenError, match="密钥未安全配置"):
        validate_sandbox_asset_token_config()

    secret = db_session.get(SystemSetting, "sandbox.asset_token_secret")
    secret.value = SECRET
    db_session.commit()
    validate_sandbox_asset_token_config()
