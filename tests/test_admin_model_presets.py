"""Model Preset、Route Binding 与 KT/Codex 管理 API 集成测试。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import get_db
from core.db import get_db as canonical_get_db
from server import app
from tests.sqlite_test_utils import install_base_schema


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    """使用独立 SQLite，避免模型控制面设置污染其他测试。"""

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'model-presets.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    install_base_schema(engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    from core.settings_service import settings

    original_factory = settings._session_factory
    settings.set_session_factory(session_factory)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[canonical_get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        settings.set_session_factory(original_factory)


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer test-token"}


def _provider_payload(secret: str = "provider-secret") -> dict:
    return {
        "id": "team_gateway",
        "display_name": "团队网关",
        "driver_type": "openai",
        "base_url": "https://gateway.example.com/v1",
        "enabled": True,
        "registry_provider": "openai",
        "model_discovery_enabled": True,
        "provider_name": "openai",
        "provider_native_tools": [],
        "credential_action": "replace",
        "api_key": secret,
    }


def _preset_payload(preset_id: str, model: str) -> dict:
    return {
        "id": preset_id,
        "display_name": f"预设 {preset_id}",
        "provider_id": "team_gateway",
        "model": model,
        "enabled": True,
        "max_context": 200_000,
        "max_output": 8_000,
        "temperature": 0.2,
        "reasoning_effort": "medium",
        "service_tier": "priority",
        "cost_input_1m": 2.5,
        "cost_output_1m": 10.0,
        "timeout": 90,
        "enable_thinking": "auto",
        "capabilities": {
            "supports_stream": True,
            "supports_tools": True,
            "supports_image": False,
        },
        "extra_headers": {"X-Nanobot-Trace": "enabled"},
        "extra_body": {"parallel_tool_calls": True},
        "retry_policy": {
            "max_retries": 4,
            "base_delay": 0.5,
            "max_delay": 20,
            "jitter": 0.2,
            "retry_classes": ["rate_limit", "server", "transient"],
        },
        "variation_groups": {
            "reasoning": {
                "high": {
                    "reasoning_effort": "high",
                    "max_output": 12_000,
                },
                "fast": {
                    "reasoning_effort": "low",
                    "max_output": 4_000,
                },
            },
        },
        "driver_options": {"echo_reasoning": True},
    }


def test_preset_binding_crud_preserves_fallback_order_and_hides_secret(
    admin_client,
    auth_header,
):
    secret = "provider-secret-never-return"
    provider_response = admin_client.post(
        "/api/v1/admin/models/providers",
        json=_provider_payload(secret),
        headers=auth_header,
    )
    assert provider_response.status_code == 201, provider_response.text
    assert secret not in provider_response.text

    for preset_id, model in (
        ("reasoning-main", "gpt-main"),
        ("reasoning-fallback", "gpt-fallback"),
    ):
        response = admin_client.post(
            "/api/v1/admin/models/presets",
            json=_preset_payload(preset_id, model),
            headers=auth_header,
        )
        assert response.status_code == 201, response.text
        assert secret not in response.text
        assert response.json()["preset"]["cost_input_1m"] == 2.5
        assert response.json()["preset"]["cost_output_1m"] == 10.0
        assert response.json()["preset"]["price_tags"] == ["paid"]

    resolve_response = admin_client.post(
        "/api/v1/admin/models/presets/reasoning-main/resolve",
        json={"selected_variations": {"reasoning": "high"}},
        headers=auth_header,
    )
    assert resolve_response.status_code == 200, resolve_response.text
    resolved = resolve_response.json()
    assert resolved["resolved"]["reasoning_effort"] == "high"
    assert resolved["resolved"]["max_output"] == 12_000
    assert resolved["request_preview"]["body"]["reasoning_effort"] == "high"
    assert resolved["request_preview"]["headers"] == ["X-Nanobot-Trace"]
    assert resolved["request_preview"]["runtime"]["pricing"] == {
        "currency": "USD",
        "unit": "1M tokens",
        "input": 2.5,
        "output": 10.0,
    }
    assert secret not in resolve_response.text

    candidates = [
        {
            "preset_id": "reasoning-main",
            "selected_variations": {"reasoning": "high"},
        },
        {
            "preset_id": "reasoning-fallback",
            "selected_variations": {"reasoning": "fast"},
        },
    ]
    binding_response = admin_client.put(
        "/api/v1/admin/models/bindings/reply",
        json={"candidates": candidates},
        headers=auth_header,
    )
    assert binding_response.status_code == 200, binding_response.text
    assert binding_response.json()["binding"]["candidates"] == candidates

    bindings_response = admin_client.get(
        "/api/v1/admin/models/bindings",
        headers=auth_header,
    )
    assert bindings_response.status_code == 200, bindings_response.text
    reply = next(
        item
        for item in bindings_response.json()["bindings"]
        if item["route_key"] == "reply"
    )
    assert [
        item["preset_id"] for item in reply["resolved_candidates"]
    ] == ["reasoning-main", "reasoning-fallback"]

    protected_preset = admin_client.delete(
        "/api/v1/admin/models/presets/reasoning-main",
        headers=auth_header,
    )
    assert protected_preset.status_code == 409
    protected_provider = admin_client.delete(
        "/api/v1/admin/models/providers/team_gateway",
        headers=auth_header,
    )
    assert protected_provider.status_code == 409

    assert admin_client.delete(
        "/api/v1/admin/models/bindings/reply",
        headers=auth_header,
    ).status_code == 200
    for preset_id in ("reasoning-main", "reasoning-fallback"):
        assert admin_client.delete(
            f"/api/v1/admin/models/presets/{preset_id}",
            headers=auth_header,
        ).status_code == 200
    assert admin_client.delete(
        "/api/v1/admin/models/providers/team_gateway",
        headers=auth_header,
    ).status_code == 200


def test_model_defaults_bind_models_with_route_overrides_and_order_fallbacks(
    admin_client,
    auth_header,
):
    provider_response = admin_client.post(
        "/api/v1/admin/models/providers",
        json=_provider_payload(),
        headers=auth_header,
    )
    assert provider_response.status_code == 201, provider_response.text

    models = [
        ("free-strong", 11, 0.0, False, False),
        ("paid-text", 12, 0.2, False, False),
        ("paid-vision", 12, 0.2, True, False),
        ("free-weak", 8, 0.0, True, True),
    ]
    for model, intelligence, price, vision, fallback_only in models:
        payload = _preset_payload("unused", model)
        payload.pop("id")
        payload.update({
            "display_name": model,
            "intelligence": intelligence,
            "fallback_only": fallback_only,
            "cost_input_1m": price,
            "cost_output_1m": price,
            "capabilities": {
                "supports_stream": True,
                "supports_tools": True,
                "supports_image": vision,
            },
            "input_modalities": ["text", "image"] if vision else ["text"],
            "output_modalities": ["text"],
            "supported_endpoints": ["chat/completions"],
        })
        response = admin_client.put(
            "/api/v1/admin/models/defaults",
            json=payload,
            headers=auth_header,
        )
        assert response.status_code == 200, response.text

    candidates = [
        {"provider_id": "team_gateway", "model": "free-weak"},
        {"provider_id": "team_gateway", "model": "paid-vision"},
        {
            "provider_id": "team_gateway",
            "model": "paid-text",
            "overrides": {"temperature": 0.7, "max_output": 321},
        },
        {"provider_id": "team_gateway", "model": "free-strong"},
    ]
    response = admin_client.put(
        "/api/v1/admin/models/bindings/reply",
        json={
            "candidates": candidates,
            "min_intelligence": 10,
            "sort_policy": "cost_modality_quality",
        },
        headers=auth_header,
    )
    assert response.status_code == 200, response.text
    resolved = response.json()["resolved_candidates"]
    assert [item["model"] for item in resolved] == [
        "free-strong",
        "paid-text",
        "paid-vision",
        "free-weak",
    ]
    paid_text = resolved[1]
    assert paid_text["route_overrides"] == {
        "temperature": 0.7,
        "max_output": 321,
    }
    assert resolved[-1]["fallback_only"] is True

    incompatible_vision = admin_client.put(
        "/api/v1/admin/models/bindings/sticker_describe",
        json={
            "candidates": [
                {"provider_id": "team_gateway", "model": "paid-text"},
            ],
        },
        headers=auth_header,
    )
    assert incompatible_vision.status_code == 422
    assert "不支持图像输入" in incompatible_vision.text

    compatible_vision = admin_client.put(
        "/api/v1/admin/models/bindings/sticker_describe",
        json={
            "candidates": [
                {"provider_id": "team_gateway", "model": "paid-vision"},
            ],
        },
        headers=auth_header,
    )
    assert compatible_vision.status_code == 200, compatible_vision.text

    invalid_update = _preset_payload("unused", "paid-vision")
    invalid_update.pop("id")
    invalid_update.update({
        "display_name": "paid-vision",
        "intelligence": 12,
        "cost_input_1m": 0.2,
        "cost_output_1m": 0.2,
        "capabilities": {
            "supports_stream": True,
            "supports_tools": True,
            "supports_image": False,
        },
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "supported_endpoints": ["chat/completions"],
    })
    rejected_update = admin_client.put(
        "/api/v1/admin/models/defaults",
        json=invalid_update,
        headers=auth_header,
    )
    assert rejected_update.status_code == 422
    assert "不支持图像输入" in rejected_update.text

    listed = admin_client.get(
        "/api/v1/admin/models/defaults",
        headers=auth_header,
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["defaults"]) == 4
    protected = admin_client.delete(
        "/api/v1/admin/models/defaults",
        params={"provider_id": "team_gateway", "model": "paid-text"},
        headers=auth_header,
    )
    assert protected.status_code == 409


def test_driver_specific_validation_and_reply_only_codex_binding(
    admin_client,
    auth_header,
):
    provider_response = admin_client.post(
        "/api/v1/admin/models/providers",
        json=_provider_payload(),
        headers=auth_header,
    )
    assert provider_response.status_code == 201, provider_response.text

    sensitive = _preset_payload("bad-header", "gpt-main")
    sensitive["extra_headers"] = {"Authorization": "must-not-be-stored"}
    sensitive_response = admin_client.post(
        "/api/v1/admin/models/presets",
        json=sensitive,
        headers=auth_header,
    )
    assert sensitive_response.status_code == 422
    assert "认证 Header" in sensitive_response.text
    assert "must-not-be-stored" not in sensitive_response.text

    enable_codex = admin_client.put(
        "/api/v1/admin/models/providers/codex",
        json={"enabled": True},
        headers=auth_header,
    )
    assert enable_codex.status_code == 200, enable_codex.text
    codex_preset = {
        "id": "codex-high",
        "display_name": "Codex High",
        "provider_id": "codex",
        "model": "gpt-5.4",
        "max_context": 400_000,
        "max_output": 32_000,
        "temperature": None,
        "reasoning_effort": "max",
        "service_tier": "priority",
        "timeout": 300,
        "retry_policy": {
            "max_retries": 3,
            "base_delay": 1,
            "max_delay": 30,
            "jitter": 0.25,
            "retry_classes": ["rate_limit", "server", "transient"],
        },
    }
    create_codex = admin_client.post(
        "/api/v1/admin/models/presets",
        json=codex_preset,
        headers=auth_header,
    )
    assert create_codex.status_code == 201, create_codex.text
    assert create_codex.json()["preset"]["reasoning_effort"] == "max"

    reply_binding = admin_client.put(
        "/api/v1/admin/models/bindings/reply",
        json={"candidates": [{"preset_id": "codex-high"}]},
        headers=auth_header,
    )
    assert reply_binding.status_code == 200, reply_binding.text
    sync_binding = admin_client.put(
        "/api/v1/admin/models/bindings/fast",
        json={"candidates": [{"preset_id": "codex-high"}]},
        headers=auth_header,
    )
    assert sync_binding.status_code == 422
    assert "只支持 OpenAI-compatible" in sync_binding.text


def test_preset_defaults_temperature_and_exposes_reasoning_max(
    admin_client,
    auth_header,
):
    provider_response = admin_client.post(
        "/api/v1/admin/models/providers",
        json=_provider_payload(),
        headers=auth_header,
    )
    assert provider_response.status_code == 201, provider_response.text

    create_response = admin_client.post(
        "/api/v1/admin/models/presets",
        json={
            "id": "default-temperature",
            "provider_id": "team_gateway",
            "model": "gpt-default",
            "cost_input_1m": 0,
            "cost_output_1m": 0,
        },
        headers=auth_header,
    )
    assert create_response.status_code == 201, create_response.text
    preset = create_response.json()["preset"]
    assert preset["temperature"] == 1.0
    assert preset["price_tags"] == ["free"]

    list_response = admin_client.get(
        "/api/v1/admin/models/presets",
        headers=auth_header,
    )
    assert list_response.status_code == 200, list_response.text
    schemas = {
        item["id"]: item
        for item in list_response.json()["driver_schemas"]
    }
    assert "max" in schemas["openai"]["reasoning_efforts"]
    assert "max" in schemas["codex"]["reasoning_efforts"]


def test_stored_preset_temperature_and_price_defaults():
    from core.model_provider.preset_config import ModelPreset

    legacy = ModelPreset.from_storage(
        "legacy-preset",
        {"provider_id": "team_gateway", "model": "legacy-model"},
    )
    explicit_null = ModelPreset.from_storage(
        "codex-preset",
        {
            "provider_id": "codex",
            "model": "gpt-5.4",
            "temperature": None,
            "cost_input_1m": -1,
            "cost_output_1m": "invalid",
        },
    )

    assert legacy.temperature == 1.0
    assert legacy.cost_input_1m is None
    assert legacy.cost_output_1m is None
    assert explicit_null.temperature is None
    assert explicit_null.cost_input_1m is None
    assert explicit_null.cost_output_1m is None


def test_kt_management_endpoints_return_public_metadata(
    admin_client,
    auth_header,
    monkeypatch,
):
    monkeypatch.setattr(
        "nanobot_kt.codex_oauth_adapter.codex_status",
        lambda: {
            "authenticated": False,
            "expired": None,
            "expires_at": None,
            "account_configured": False,
        },
    )
    status = admin_client.get(
        "/api/v1/admin/models/codex/status",
        headers=auth_header,
    )
    assert status.status_code == 200, status.text
    assert status.json() == {
        "authenticated": False,
        "expired": None,
        "expires_at": None,
        "account_configured": False,
    }
    assert "token" not in status.text.lower()

    tools = admin_client.get(
        "/api/v1/admin/models/kt/native-tools",
        headers=auth_header,
    )
    assert tools.status_code == 200, tools.text
    assert isinstance(tools.json()["tools"], list)


def test_codex_account_pool_admin_api_manages_public_metadata_only(
    admin_client,
    auth_header,
    monkeypatch,
):
    monkeypatch.setenv(
        "NANOBOT_CODEX_CREDENTIAL_SECRET",
        "codex-admin-test-secret-0123456789abcdef",
    )

    async def fake_start(account_id):
        return {
            "login_id": "login-safe-id",
            "account_id": account_id,
            "user_code": "ABCD-EFGH",
            "verification_url": "https://auth.openai.com/codex/device",
            "status": "pending",
            "error": "",
            "created_at": 1,
            "expires_at": 901,
            "token_expires_at": None,
            "poll_after_seconds": 2,
        }

    monkeypatch.setattr(
        "nanobot_kt.codex_oauth_adapter.codex_device_login_manager.start",
        fake_start,
    )
    started = admin_client.post(
        "/api/v1/admin/models/codex/device-login",
        headers=auth_header,
        json={"name": "工作账号"},
    )
    assert started.status_code == 200, started.text
    account_id = started.json()["account_id"]

    listed = admin_client.get(
        "/api/v1/admin/models/codex/accounts",
        headers=auth_header,
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["accounts"] == [
        {
            "id": account_id,
            "name": "工作账号",
            "enabled": True,
            "weight": 1,
            "status": "login_required",
            "credential_configured": False,
            "expired": None,
            "expires_at": None,
            "account_configured": False,
            "created_at": listed.json()["accounts"][0]["created_at"],
            "updated_at": listed.json()["accounts"][0]["updated_at"],
        }
    ]
    assert "access_token" not in listed.text
    assert "refresh_token" not in listed.text

    updated = admin_client.patch(
        f"/api/v1/admin/models/codex/accounts/{account_id}",
        headers=auth_header,
        json={"name": "备用账号", "enabled": False, "weight": 2},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["account"]["name"] == "备用账号"
    assert updated.json()["account"]["enabled"] is False
    assert updated.json()["account"]["weight"] == 2

    deleted = admin_client.delete(
        f"/api/v1/admin/models/codex/accounts/{account_id}",
        headers=auth_header,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"ok": True, "account_id": account_id}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/v1/admin/models/codex/accounts", None),
        ("post", "/api/v1/admin/models/codex/device-login", {}),
        (
            "patch",
            "/api/v1/admin/models/codex/accounts/ca_account_test_001",
            {"enabled": False},
        ),
        (
            "delete",
            "/api/v1/admin/models/codex/accounts/ca_account_test_001",
            None,
        ),
    ],
)
def test_codex_account_admin_endpoints_require_authentication(
    admin_client,
    method,
    path,
    body,
):
    response = admin_client.request(method, path, json=body)

    assert response.status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": "x" * 101},
        {"weight": 0},
        {"weight": 101},
    ],
)
def test_codex_account_update_rejects_invalid_input(
    admin_client,
    auth_header,
    payload,
):
    response = admin_client.patch(
        "/api/v1/admin/models/codex/accounts/ca_account_test_001",
        headers=auth_header,
        json=payload,
    )

    assert response.status_code == 422
    assert "access_token" not in response.text
    assert "refresh_token" not in response.text


def test_codex_login_refuses_to_start_without_encryption_secret(
    admin_client,
    auth_header,
    monkeypatch,
):
    monkeypatch.delenv("NANOBOT_CODEX_CREDENTIAL_SECRET", raising=False)
    monkeypatch.delenv("NANOBOT_ASSET_TOKEN_SECRET", raising=False)
    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda _key, _default=None: "",
    )

    response = admin_client.post(
        "/api/v1/admin/models/codex/device-login",
        headers=auth_header,
        json={"name": "不能落明文"},
    )

    assert response.status_code == 503
    assert "至少 32 字节" in response.text
