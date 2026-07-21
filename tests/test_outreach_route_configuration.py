import pytest


OUTREACH_ROUTE_DEFAULTS = {
    "timing_proactive": {"timeout": 30, "temperature": 0.0, "max_tokens": 65536},
    "outreach_extract": {"timeout": 30, "temperature": 0.0, "max_tokens": 65536},
    "outreach_judge": {"timeout": 45, "temperature": 0.0, "max_tokens": 65536},
    "outreach_generate": {"timeout": 60, "temperature": 0.7, "max_tokens": 65536},
}


@pytest.mark.parametrize("route_key", OUTREACH_ROUTE_DEFAULTS)
def test_outreach_routes_are_registered_with_thinking_enabled(route_key):
    from core.config_registry import SETTING_DEFS
    from core.route_metadata import ROUTE_METADATA

    defaults = OUTREACH_ROUTE_DEFAULTS[route_key]
    assert route_key in ROUTE_METADATA
    assert SETTING_DEFS[f"model.route.{route_key}.enable_thinking"].default == "true"
    assert SETTING_DEFS[f"model.route.{route_key}.max_tokens"].max_value == 65536
    for field, expected in defaults.items():
        assert SETTING_DEFS[f"model.route.{route_key}.{field}"].default == expected


@pytest.mark.parametrize("route_key", OUTREACH_ROUTE_DEFAULTS)
def test_outreach_routes_inherit_reply_endpoint_but_keep_task_defaults(route_key, monkeypatch):
    from clients.classifier_client import resolve_model_route

    values = {
        "model.route.reply.provider": "newapi",
        "model.route.reply.model": "reply-model",
        "model.route.reply.timeout": 99,
        "model.route.reply.temperature": 1.1,
        "model.route.reply.max_tokens": 4096,
        "model.route.reply.enable_thinking": "true",
        "model.providers.newapi.base_url": "http://newapi:9000/v1",
        "model.providers.newapi.api_key": "secret",
        "model.providers.newapi.enabled": True,
    }
    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda key, default=None: values.get(key, default),
    )

    route = resolve_model_route(route_key)

    defaults = OUTREACH_ROUTE_DEFAULTS[route_key]
    assert route["provider_id"] == "newapi"
    assert route["base_url"] == "http://newapi:9000/v1"
    assert route["model"] == "reply-model"
    assert route["enable_thinking"] == "true"
    assert route["timeout"] == defaults["timeout"]
    assert route["temperature"] == defaults["temperature"]
    assert route["max_tokens"] == defaults["max_tokens"]


@pytest.mark.parametrize("route_key", OUTREACH_ROUTE_DEFAULTS)
def test_admin_can_edit_every_outreach_route(route_key, client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    response = client.put(
        f"/api/v1/admin/models/routes/{route_key}",
        headers={"Authorization": "Bearer test-token"},
        json={"enable_thinking": "true", "max_tokens": 65536},
    )

    assert response.status_code == 200, response.text
    assert response.json()["route_key"] == route_key
