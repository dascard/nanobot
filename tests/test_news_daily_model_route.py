def test_news_daily_quality_route_is_registered_for_web_management():
    from api.admin.model_routes import _CLASSIFIER_ROUTE_KEYS
    from core.config_registry import SETTING_DEFS
    from core.route_metadata import ROUTE_METADATA

    route_key = "news_daily_quality"

    assert ROUTE_METADATA[route_key] == {
        "type": "task",
        "label": "AI 日报质量摘要",
    }
    assert route_key in _CLASSIFIER_ROUTE_KEYS
    assert SETTING_DEFS[f"model.route.{route_key}.timeout"].default == 20
    assert SETTING_DEFS[f"model.route.{route_key}.temperature"].default == 0.1
    assert SETTING_DEFS[f"model.route.{route_key}.max_tokens"].default == 3200
    assert SETTING_DEFS[f"model.route.{route_key}.enable_thinking"].default == "false"


def test_news_daily_quality_route_inherits_reply_and_allows_web_override(
    monkeypatch,
):
    from clients.classifier_client import resolve_model_route

    values = {
        "model.route.reply.provider": "newapi",
        "model.route.reply.model": "reply-model",
        "model.route.reply.enable_thinking": "true",
        "model.providers.newapi.base_url": "http://newapi:9000/v1",
        "model.providers.newapi.api_key": "provider-key",
        "model.providers.newapi.enabled": True,
    }
    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda key, default=None: values.get(key, default),
    )

    inherited = resolve_model_route("news_daily_quality")

    assert inherited["provider_id"] == "newapi"
    assert inherited["base_url"] == "http://newapi:9000/v1"
    assert inherited["api_key"] == "provider-key"
    assert inherited["model"] == "reply-model"
    assert inherited["timeout"] == 20
    assert inherited["temperature"] == 0.1
    assert inherited["max_tokens"] == 3200
    assert inherited["enable_thinking"] == "false"
    assert inherited["inherited_from"] == "reply"

    values.update({
        "model.route.news_daily_quality.provider": "daily-provider",
        "model.route.news_daily_quality.model": "daily-model",
        "model.route.news_daily_quality.enable_thinking": "true",
        "model.providers.daily-provider.base_url": "http://daily:9001/v1",
        "model.providers.daily-provider.api_key": "daily-key",
        "model.providers.daily-provider.enabled": True,
    })

    overridden = resolve_model_route("news_daily_quality")

    assert overridden["provider_id"] == "daily-provider"
    assert overridden["base_url"] == "http://daily:9001/v1"
    assert overridden["api_key"] == "daily-key"
    assert overridden["model"] == "daily-model"
    assert overridden["enable_thinking"] == "true"
    assert overridden["overridden_fields"]["provider_id"] == "daily-provider"
    assert overridden["overridden_fields"]["model"] == "daily-model"


def test_admin_can_edit_news_daily_quality_route(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.put(
        "/api/v1/admin/models/routes/news_daily_quality",
        headers={"Authorization": "Bearer test-token"},
        json={
            "provider": "newapi",
            "model": "web-selected-model",
            "timeout": 25,
            "temperature": 0.2,
            "max_tokens": 4096,
            "enable_thinking": "false",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["route_key"] == "news_daily_quality"
