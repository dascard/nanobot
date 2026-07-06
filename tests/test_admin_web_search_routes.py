from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import AdminAuditLog, Base, SystemSetting, WebSearchProviderUsage, get_db
from server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'web-search-test.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        from fastapi.testclient import TestClient

        test_client = TestClient(app)
        test_client.testing_session_factory = TestingSessionLocal
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer test-token"}


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _provider(payload: dict, provider_id: str) -> dict:
    return next(item for item in payload["providers"] if item["id"] == provider_id)


def test_list_providers_requires_admin(client):
    response = client.get("/api/v1/admin/web-search/providers")

    assert response.status_code == 401


def test_list_providers_returns_catalog_and_config_state(client, auth_header):
    data = _ok(client.get("/api/v1/admin/web-search/providers", headers=auth_header))

    assert [item["id"] for item in data["providers"]] == [
        "searxng",
        "serper",
        "brave",
        "tavily",
        "ddgs",
        "exa",
        "firecrawl",
        "linkup",
        "you",
        "jina",
    ]
    serper = _provider(data, "serper")
    assert serper["requires_api_key"] is True
    assert serper["supports_base_url"] is True
    assert serper["default_base_url"] == "https://google.serper.dev"
    assert serper["docs_url"] == "https://serper.dev/signup"
    assert serper["api_key_configured"] is False
    assert serper["api_key_source"] is None
    assert serper["last_test"] is None
    assert serper["usage"]["total_calls"] == 0
    assert serper["usage"]["success_calls"] == 0
    assert serper["usage"]["failure_calls"] == 0


def test_all_web_search_providers_are_testable(client, auth_header):
    data = _ok(client.get("/api/v1/admin/web-search/providers", headers=auth_header))

    assert all(item["testable"] is True for item in data["providers"])


def test_update_provider_saves_enabled_and_base_url(client, auth_header):
    response = client.put(
        "/api/v1/admin/web-search/providers/searxng",
        headers=auth_header,
        json={"enabled": True, "base_url": "https://search.example.test"},
    )
    data = _ok(response)

    assert data["provider"]["id"] == "searxng"
    assert data["provider"]["enabled"] is True
    assert data["provider"]["base_url"] == "https://search.example.test"
    listed = _ok(client.get("/api/v1/admin/web-search/providers", headers=auth_header))
    assert _provider(listed, "searxng")["base_url"] == "https://search.example.test"


def test_update_provider_replaces_api_key_without_echoing_secret(client, auth_header):
    secret = "serper-secret-value"
    response = client.put(
        "/api/v1/admin/web-search/providers/serper",
        headers=auth_header,
        json={"enabled": True, "api_key": secret},
    )
    text = response.text
    data = _ok(response)

    assert secret not in text
    assert data["provider"]["api_key_configured"] is True
    assert data["provider"]["api_key_source"] == "db"

    listed_response = client.get("/api/v1/admin/web-search/providers", headers=auth_header)
    assert secret not in listed_response.text
    assert _provider(listed_response.json(), "serper")["api_key_configured"] is True


def test_clear_api_key_removes_db_secret(client, auth_header):
    client.put(
        "/api/v1/admin/web-search/providers/serper",
        headers=auth_header,
        json={"api_key": "temporary-secret"},
    )

    data = _ok(client.put(
        "/api/v1/admin/web-search/providers/serper",
        headers=auth_header,
        json={"clear_api_key": True},
    ))

    assert data["provider"]["api_key_configured"] is False
    assert data["provider"]["api_key_source"] is None


def test_env_api_key_reported_configured_source_env_without_value(client, auth_header, monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_SERPER_API_KEY", "env-secret-value")

    response = client.get("/api/v1/admin/web-search/providers", headers=auth_header)
    data = _ok(response)
    serper = _provider(data, "serper")

    assert "env-secret-value" not in response.text
    assert serper["api_key_configured"] is True
    assert serper["api_key_source"] == "env"


def test_db_key_overrides_env_source_db(client, auth_header, monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_SERPER_API_KEY", "env-secret-value")

    data = _ok(client.put(
        "/api/v1/admin/web-search/providers/serper",
        headers=auth_header,
        json={"api_key": "db-secret-value"},
    ))

    assert data["provider"]["api_key_configured"] is True
    assert data["provider"]["api_key_source"] == "db"


def test_clear_db_key_falls_back_to_env_source(client, auth_header, monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_SERPER_API_KEY", "env-secret-value")
    client.put(
        "/api/v1/admin/web-search/providers/serper",
        headers=auth_header,
        json={"api_key": "db-secret-value"},
    )

    data = _ok(client.put(
        "/api/v1/admin/web-search/providers/serper",
        headers=auth_header,
        json={"clear_api_key": True},
    ))

    assert data["provider"]["api_key_configured"] is True
    assert data["provider"]["api_key_source"] == "env"


def test_unknown_provider_returns_404(client, auth_header):
    response = client.put(
        "/api/v1/admin/web-search/providers/nope",
        headers=auth_header,
        json={"enabled": True},
    )

    assert response.status_code == 404


def test_base_url_rejected_when_not_supported(client, auth_header):
    response = client.put(
        "/api/v1/admin/web-search/providers/ddgs",
        headers=auth_header,
        json={"base_url": "https://duck.example.test"},
    )

    assert response.status_code == 422


def test_invalid_base_url_scheme_rejected(client, auth_header):
    response = client.put(
        "/api/v1/admin/web-search/providers/searxng",
        headers=auth_header,
        json={"base_url": "file:///etc/passwd"},
    )

    assert response.status_code == 422


def test_clear_and_set_api_key_conflict_rejected(client, auth_header):
    response = client.put(
        "/api/v1/admin/web-search/providers/serper",
        headers=auth_header,
        json={"api_key": "secret", "clear_api_key": True},
    )

    assert response.status_code == 422


def test_test_provider_missing_key_returns_ok_false_without_http_call(client, auth_header, monkeypatch):
    calls = []

    async def fake_serper(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("missing key should short-circuit before provider HTTP")

    monkeypatch.setattr("core.web_search.provider_tests._test_serper", fake_serper, raising=False)

    data = _ok(client.post(
        "/api/v1/admin/web-search/providers/serper/test",
        headers=auth_header,
        json={"query": "nanobot"},
    ))

    assert data["ok"] is False
    assert data["error_code"] == "missing_api_key"
    assert calls == []


def test_test_provider_masks_secret_in_error_message(client, auth_header, monkeypatch):
    secret = "serper-secret-value"
    client.put(
        "/api/v1/admin/web-search/providers/serper",
        headers=auth_header,
        json={"api_key": secret},
    )

    @dataclass
    class FakeResult:
        ok: bool = False
        provider_id: str = "serper"
        duration_ms: int = 1
        message: str = f"Authorization Bearer {secret} failed"
        sample_count: int = 0
        error_code: str = "provider_auth_failed"

        def to_dict(self):
            return {
                "ok": self.ok,
                "provider_id": self.provider_id,
                "duration_ms": self.duration_ms,
                "message": self.message.replace(secret, "***"),
                "sample_count": self.sample_count,
                "error_code": self.error_code,
            }

    async def fake_test_provider(provider_id, config, query, db=None):
        return FakeResult()

    monkeypatch.setattr("api.admin.web_search_routes.test_provider", fake_test_provider)

    response = client.post(
        "/api/v1/admin/web-search/providers/serper/test",
        headers=auth_header,
        json={"query": "nanobot"},
    )

    assert secret not in response.text
    data = _ok(response)
    assert data["ok"] is False
    assert data["message"] == "Authorization Bearer *** failed"


def test_preview_web_search_returns_results_and_model_message(client, auth_header, monkeypatch):
    from core.web_search.search_runtime import WebSearchProviderResult, WebSearchResult

    client.put(
        "/api/v1/admin/web-search/providers/searxng",
        headers=auth_header,
        json={"enabled": True},
    )

    async def fake_search_provider(config, query, limit=5):
        assert config.provider_id == "searxng"
        assert limit == 2
        assert query == "nanobot web search"
        return WebSearchProviderResult(
            provider_id="searxng",
            elapsed_ms=12,
            results=[
                WebSearchResult(
                    provider="searxng",
                    title="Nanobot Search",
                    url="https://example.test/search",
                    snippet="搜索结果摘要",
                    published_at="2026-07-06",
                )
            ],
        )

    monkeypatch.setattr("core.web_search.search_runtime.search_provider", fake_search_provider)

    data = _ok(client.post(
        "/api/v1/admin/web-search/preview",
        headers=auth_header,
        json={"query": "nanobot web search", "limit": 2},
    ))

    assert data["ok"] is True
    assert data["provider_id"] == "searxng"
    assert data["elapsed_ms"] == 12
    assert data["quality"] == "ok"
    assert data["quality_score"] >= 0.5
    assert data["attempted_providers"][0]["provider_id"] == "searxng"
    assert data["results"][0]["title"] == "Nanobot Search"
    assert "QUERY: nanobot web search" in data["message"]
    assert "PROVIDER: searxng" in data["message"]
    assert "RESULT_COUNT: 1" in data["message"]
    assert "QUALITY: ok" in data["message"]
    assert "WEB_SEARCH_RESULTS_END" in data["message"]
    assert "https://example.test/search" in data["message"]

    listed = _ok(client.get("/api/v1/admin/web-search/providers", headers=auth_header))
    usage = _provider(listed, "searxng")["usage"]
    assert usage["total_calls"] == 1
    assert usage["success_calls"] == 1
    assert usage["failure_calls"] == 0


def test_preview_web_search_passes_provider_and_limit(client, auth_header, monkeypatch):
    from core.web_search.search_runtime import WebSearchProviderResult

    client.put(
        "/api/v1/admin/web-search/providers/searxng",
        headers=auth_header,
        json={"enabled": True},
    )
    calls = []

    async def fake_search_provider(config, query, limit=5):
        calls.append({"query": query, "limit": limit, "provider_id": config.provider_id})
        return WebSearchProviderResult(provider_id=config.provider_id, elapsed_ms=1, results=[])

    monkeypatch.setattr("core.web_search.search_runtime.search_provider", fake_search_provider)

    data = _ok(client.post(
        "/api/v1/admin/web-search/preview",
        headers=auth_header,
        json={"query": "  python 3.10 datetime UTC  ", "limit": 3, "provider": "searxng"},
    ))

    assert data["ok"] is True
    assert calls == [{"query": "python 3.10 datetime UTC", "limit": 3, "provider_id": "searxng"}]


def test_preview_web_search_falls_back_when_first_provider_is_low_relevance(client, auth_header, monkeypatch):
    from core.web_search.search_runtime import WebSearchProviderResult, WebSearchResult

    client.put(
        "/api/v1/admin/web-search/providers/searxng",
        headers=auth_header,
        json={"enabled": True},
    )
    client.put(
        "/api/v1/admin/web-search/providers/brave",
        headers=auth_header,
        json={"enabled": True},
    )

    calls = []

    async def fake_search_provider(config, query, limit=5):
        calls.append(config.provider_id)
        if config.provider_id == "searxng":
            return WebSearchProviderResult(
                provider_id="searxng",
                elapsed_ms=3,
                results=[
                    WebSearchResult(
                        provider="searxng",
                        title="Proton VPN Download",
                        url="https://protonvpn.com/download",
                        snippet="Download Proton VPN for Windows, macOS, Linux, Android and iOS.",
                    )
                ],
            )
        if config.provider_id == "brave":
            return WebSearchProviderResult(
                provider_id="brave",
                elapsed_ms=4,
                results=[
                    WebSearchResult(
                        provider="brave",
                        title="上海天气预报",
                        url="https://www.weather.com.cn/weather/101020100.shtml",
                        snippet="上海今日天气和未来一周天气预报。",
                    )
                ],
            )
        return WebSearchProviderResult(provider_id=config.provider_id, elapsed_ms=1, results=[])

    monkeypatch.setattr("core.web_search.search_runtime.search_provider", fake_search_provider)

    data = _ok(client.post(
        "/api/v1/admin/web-search/preview",
        headers=auth_header,
        json={"query": "上海天气", "limit": 5},
    ))

    assert data["ok"] is True
    assert data["provider_id"] == "brave"
    assert data["quality"] == "ok"
    assert [item["provider_id"] for item in data["attempted_providers"]] == ["searxng", "brave"]
    assert data["attempted_providers"][0]["quality"] == "low_relevance"
    assert calls == ["searxng", "brave"]


def test_preview_web_search_returns_ok_false_on_runtime_error(client, auth_header, monkeypatch):
    from core.web_search.search_runtime import WebSearchError

    async def fake_search_enabled_providers(db, query, limit=5, provider_id=""):
        raise WebSearchError("no_enabled_provider", "没有启用的搜索 provider")

    monkeypatch.setattr("api.admin.web_search_routes.search_enabled_providers", fake_search_enabled_providers)

    data = _ok(client.post(
        "/api/v1/admin/web-search/preview",
        headers=auth_header,
        json={"query": "nanobot"},
    ))

    assert data["ok"] is False
    assert data["error_code"] == "no_enabled_provider"
    assert data["message"] == "没有启用的搜索 provider"


def test_preview_web_search_records_provider_failure_usage(client, auth_header, monkeypatch):
    from core.web_search.search_runtime import WebSearchError

    client.put(
        "/api/v1/admin/web-search/providers/brave",
        headers=auth_header,
        json={"enabled": True},
    )

    async def fake_search_provider(config, query, limit=5):
        assert config.provider_id == "brave"
        raise WebSearchError("provider_rate_limited", "Provider 限流", provider_id="brave")

    monkeypatch.setattr("core.web_search.search_runtime.search_provider", fake_search_provider)

    data = _ok(client.post(
        "/api/v1/admin/web-search/preview",
        headers=auth_header,
        json={"query": "nanobot", "provider": "brave"},
    ))

    assert data["ok"] is False
    listed = _ok(client.get("/api/v1/admin/web-search/providers", headers=auth_header))
    usage = _provider(listed, "brave")["usage"]
    assert usage["total_calls"] == 1
    assert usage["success_calls"] == 0
    assert usage["failure_calls"] == 1
    assert usage["last_error_code"] == "provider_rate_limited"


@pytest.mark.asyncio
async def test_provider_test_records_usage_when_route_passes_db(client, auth_header, monkeypatch):
    from core.web_search.search_runtime import WebSearchProviderResult

    async def fake_search_provider(config, query, limit=3):
        assert config.provider_id == "ddgs"
        return WebSearchProviderResult(provider_id="ddgs", elapsed_ms=1, results=[])

    monkeypatch.setattr("core.web_search.provider_tests.search_provider", fake_search_provider)

    data = _ok(client.post(
        "/api/v1/admin/web-search/providers/ddgs/test",
        headers=auth_header,
        json={"query": "nanobot"},
    ))

    assert data["ok"] is True
    session = client.testing_session_factory()
    try:
        row = session.query(WebSearchProviderUsage).filter_by(provider_id="ddgs").one()
        assert row.total_calls == 1
        assert row.success_calls == 1
        assert row.failure_calls == 0
    finally:
        session.close()


def test_web_search_settings_excluded_from_generic_settings_list(client, auth_header):
    response = client.get("/api/v1/admin/settings", headers=auth_header)
    data = _ok(response)

    keys = {item["key"] for item in data["settings"]}
    assert "web_search.providers.serper.api_key" not in keys
    assert all(not key.startswith("web_search.providers.") for key in keys)


def test_audit_log_does_not_store_api_key(client, auth_header):
    secret = "audit-secret-value"
    client.put(
        "/api/v1/admin/web-search/providers/serper",
        headers=auth_header,
        json={"enabled": True, "api_key": secret},
    )

    session = client.testing_session_factory()
    try:
        logs = session.query(AdminAuditLog).all()
        details = "\n".join(row.detail_json or "" for row in logs)
    finally:
        session.close()

    assert secret not in details
    assert "api_key_changed" in details


def test_config_registry_contains_sensitive_web_search_keys():
    from core.config_registry import SETTING_DEFS

    key = "web_search.providers.serper.api_key"
    assert key in SETTING_DEFS
    assert SETTING_DEFS[key].category == "web_search"
    assert SETTING_DEFS[key].sensitive is True


def test_api_key_is_persisted_to_system_setting(client, auth_header):
    secret = "persisted-secret"
    client.put(
        "/api/v1/admin/web-search/providers/serper",
        headers=auth_header,
        json={"api_key": secret},
    )

    session = client.testing_session_factory()
    try:
        row = session.query(SystemSetting).filter_by(key="web_search.providers.serper.api_key").one()
        assert row.value == secret
    finally:
        session.close()


# ═══════════════════════════════════════════
# provider smoke test 内部行为(mock aiohttp)
# ═══════════════════════════════════════════

class _FakeResponse:
    """模拟 aiohttp.ClientResponse:可控 status 与 body 类型。"""

    def __init__(self, status: int, json_data=None, text_data: str = ""):
        self.status = status
        self._json_data = json_data
        self._text_data = text_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        if self._json_data is None:
            raise ValueError("非 JSON 响应")
        return self._json_data

    async def text(self):
        return self._text_data


class _FakeSession:
    """模拟 aiohttp.ClientSession,固定返回给定 response。"""

    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, *args, **kwargs):
        return self._response

    def post(self, *args, **kwargs):
        return self._response


def _patch_session(monkeypatch, response: _FakeResponse):
    import core.web_search.search_runtime as runtime

    def fake_session(*args, **kwargs):
        return _FakeSession(response)

    monkeypatch.setattr(runtime.aiohttp, "ClientSession", fake_session)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_id", "json_data"),
    [
        ("exa", {"results": [{"title": "Exa", "url": "https://example.test/exa", "text": "exa snippet"}]}),
        ("firecrawl", {"data": {"web": [{"title": "Firecrawl", "url": "https://example.test/firecrawl", "description": "firecrawl snippet"}]}}),
        ("linkup", {"results": [{"title": "Linkup", "url": "https://example.test/linkup", "content": "linkup snippet"}]}),
        ("you", {"results": {"web": [{"title": "You", "url": "https://example.test/you", "description": "you snippet"}]}}),
        ("jina", {"data": [{"title": "Jina", "url": "https://example.test/jina", "content": "jina snippet"}]}),
    ],
)
async def test_all_added_provider_smoke_tests_can_succeed(monkeypatch, provider_id, json_data):
    from core.web_search.provider_catalog import get_provider_catalog
    from core.web_search.provider_settings import ProviderResolvedConfig
    from core.web_search.provider_tests import test_provider

    _patch_session(monkeypatch, _FakeResponse(200, json_data=json_data))
    item = get_provider_catalog(provider_id)
    config = ProviderResolvedConfig(
        provider_id=provider_id,
        enabled=True,
        base_url=item.default_base_url,
        api_key="provider-secret" if item.requires_api_key else "",
        api_key_configured=item.requires_api_key,
        api_key_source="db" if item.requires_api_key else None,
    )

    result = await test_provider(provider_id, config, "nanobot")

    assert result.ok is True
    assert result.sample_count == 1


@pytest.mark.asyncio
async def test_serper_auth_failure_with_html_body_returns_auth_failed(monkeypatch):
    """401 + 非 JSON body(网关 HTML)应报 provider_auth_failed,而非 provider_bad_response。"""
    from core.web_search.provider_settings import ProviderResolvedConfig
    from core.web_search.provider_tests import test_provider

    _patch_session(monkeypatch, _FakeResponse(401, json_data=None, text_data="<html>401 Unauthorized</html>"))

    config = ProviderResolvedConfig(
        provider_id="serper",
        enabled=True,
        base_url="",
        api_key="bad-key",
        api_key_configured=True,
        api_key_source="db",
    )
    result = await test_provider("serper", config, "nanobot")

    assert result.ok is False
    assert result.error_code == "provider_auth_failed"


@pytest.mark.asyncio
async def test_serper_rate_limited_with_html_body_returns_rate_limited(monkeypatch):
    """429 + 非 JSON body 应报 provider_rate_limited。"""
    from core.web_search.provider_settings import ProviderResolvedConfig
    from core.web_search.provider_tests import test_provider

    _patch_session(monkeypatch, _FakeResponse(429, json_data=None, text_data="rate limited"))

    config = ProviderResolvedConfig(
        provider_id="serper",
        enabled=True,
        base_url="",
        api_key="some-key",
        api_key_configured=True,
        api_key_source="db",
    )
    result = await test_provider("serper", config, "nanobot")

    assert result.ok is False
    assert result.error_code == "provider_rate_limited"


@pytest.mark.asyncio
async def test_brave_auth_failure_with_html_body_returns_auth_failed(monkeypatch):
    """brave 走 GET 分支,同样应先判 status 再解析 body。"""
    from core.web_search.provider_settings import ProviderResolvedConfig
    from core.web_search.provider_tests import test_provider

    _patch_session(monkeypatch, _FakeResponse(403, json_data=None, text_data="<html>forbidden</html>"))

    config = ProviderResolvedConfig(
        provider_id="brave",
        enabled=True,
        base_url="",
        api_key="bad-key",
        api_key_configured=True,
        api_key_source="db",
    )
    result = await test_provider("brave", config, "nanobot")

    assert result.ok is False
    assert result.error_code == "provider_auth_failed"


@pytest.mark.asyncio
async def test_jina_provider_test_uses_search_runtime(monkeypatch):
    from core.web_search.provider_settings import ProviderResolvedConfig
    from core.web_search.provider_tests import test_provider
    from core.web_search.search_runtime import WebSearchProviderResult, WebSearchResult

    async def fake_search_provider(config, query, limit=3):
        assert config.provider_id == "jina"
        assert query == "nanobot"
        assert limit == 3
        return WebSearchProviderResult(
            provider_id="jina",
            results=[
                WebSearchResult(provider="jina", title="A", url="https://example.test/a"),
                WebSearchResult(provider="jina", title="B", url="https://example.test/b"),
            ],
        )

    monkeypatch.setattr("core.web_search.provider_tests.search_provider", fake_search_provider)
    config = ProviderResolvedConfig(
        provider_id="jina",
        enabled=True,
        base_url="https://s.jina.ai",
        api_key="jina-secret",
        api_key_configured=True,
        api_key_source="db",
    )

    result = await test_provider("jina", config, "nanobot")

    assert result.ok is True
    assert result.sample_count == 2
