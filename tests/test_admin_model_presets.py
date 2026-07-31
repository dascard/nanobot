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
        "reasoning_effort": "high",
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
