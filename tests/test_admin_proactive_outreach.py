from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import (
    Base,
    LLMApiRequestLog,
    ProactiveOutreachLog,
    SystemSetting,
    get_db,
)
from server import app


@pytest.fixture
def proactive_client(tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setenv("NANOBOT_SUPER_USER_IDS", "")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
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

    from core.settings_service import settings

    original_factory = settings._session_factory
    settings.set_session_factory(TestingSessionLocal)
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        settings.set_session_factory(original_factory)


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _route_entries():
    def _iter_routes(routes, prefix: str = ""):
        for route in routes:
            endpoint = getattr(route, "endpoint", None)
            route_path = getattr(route, "path", None)
            if endpoint is not None and route_path is not None:
                yield prefix + route_path, route
                continue
            original_router = getattr(route, "original_router", None)
            if original_router is None:
                continue
            include_context = getattr(route, "include_context", None)
            include_prefix = getattr(include_context, "prefix", "")
            yield from _iter_routes(original_router.routes, prefix + include_prefix)

    return list(_iter_routes(app.routes))


def _routes_for(path: str, method: str):
    return [
        route
        for route_path, route in _route_entries()
        if route_path == path and method in getattr(route, "methods", set())
    ]


def test_admin_proactive_outreach_routes_are_registered():
    expected = (
        ("GET", "/api/v1/admin/proactive-outreach/status"),
        ("GET", "/api/v1/admin/proactive-outreach/logs"),
        ("PUT", "/api/v1/admin/proactive-outreach/settings/{key:path}"),
        ("POST", "/api/v1/admin/proactive-outreach/settings/reload"),
        ("POST", "/api/v1/admin/proactive-outreach/run-once"),
        ("POST", "/api/v1/admin/proactive-outreach/simulate"),
    )

    for method, path in expected:
        routes = _routes_for(path, method)
        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {
            "api.admin.proactive_outreach_routes"
        }


def test_proactive_outreach_status_reports_settings_logs_and_llm(proactive_client):
    db = next(app.dependency_overrides[get_db]())
    now = datetime(2026, 7, 8, 12, 0, 0)
    db.add(SystemSetting(
        key="proactive_outreach.enabled",
        value="true",
        description="开关",
    ))
    db.add(SystemSetting(
        key="bot.super_user_ids",
        value="u-proactive",
        description="superuser",
    ))
    db.add(ProactiveOutreachLog(
        user_id="u-proactive",
        idempotency_key="outreach:u-proactive:pending",
        grounding_json='{"recent_threads":["项目进展"],"now":{"period":"午后"}}',
        judge_should=False,
        judge_reason="稍后再问",
        next_check_at=now + timedelta(hours=2),
        next_intent="问项目",
        status="pending",
        created_at=now - timedelta(hours=1),
    ))
    db.add(ProactiveOutreachLog(
        user_id="u-proactive",
        idempotency_key="outreach:u-proactive:sent",
        grounding_json='{"recent_threads":["夜跑"]}',
        judge_should=True,
        judge_reason="有具体话题",
        message="夜跑回来了吗？",
        status="sent",
        forced=False,
        created_at=now,
    ))
    db.add(LLMApiRequestLog(
        source="classifier.timing_proactive",
        provider="local",
        model="judge-model",
        status="success",
        request_preview="Judge 主动外呼",
        response_preview='{"should_reach_out": false}',
        latency_ms=123,
        created_at=now,
    ))
    db.commit()

    response = proactive_client.get(
        "/api/v1/admin/proactive-outreach/status",
        headers=_auth_header(),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["enabled"] is True
    assert data["super_user_ids"] == ["u-proactive"]
    settings_by_key = {item["key"]: item for item in data["settings"]}
    assert settings_by_key["proactive_outreach.enabled"]["value"] is True
    assert settings_by_key["bot.super_user_ids"]["value"] == "u-proactive"
    assert data["stats"]["total"] == 2
    assert data["stats"]["by_status"]["sent"] == 1
    assert data["latest_logs"][0]["status"] == "sent"
    assert data["latest_logs"][1]["grounding"]["recent_threads"] == ["项目进展"]
    assert data["llm_logs"][0]["source"] == "classifier.timing_proactive"
    assert data["llm_logs"][0]["latency_ms"] == 123


def test_proactive_outreach_logs_filter_by_status(proactive_client):
    db = next(app.dependency_overrides[get_db]())
    db.add(ProactiveOutreachLog(
        user_id="u1",
        idempotency_key="outreach:u1:pending",
        grounding_json="{}",
        status="pending",
    ))
    db.add(ProactiveOutreachLog(
        user_id="u1",
        idempotency_key="outreach:u1:sent",
        grounding_json="{}",
        status="sent",
    ))
    db.commit()

    response = proactive_client.get(
        "/api/v1/admin/proactive-outreach/logs",
        params={"status": "pending"},
        headers=_auth_header(),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "pending"
    assert data["items"][0]["user_id"] == "u1"


def test_proactive_outreach_setting_update_is_scoped(proactive_client):
    ok = proactive_client.put(
        "/api/v1/admin/proactive-outreach/settings/proactive_outreach.enabled",
        json={"value": True},
        headers=_auth_header(),
    )
    denied = proactive_client.put(
        "/api/v1/admin/proactive-outreach/settings/model.route.reply.model",
        json={"value": "x"},
        headers=_auth_header(),
    )

    assert ok.status_code == 200, ok.text
    assert ok.json()["value"] is True
    assert denied.status_code == 400


def test_proactive_outreach_run_once_uses_existing_runtime(proactive_client, monkeypatch):
    calls = []

    async def fake_due_once(user_id, **kwargs):
        calls.append(("due", user_id, kwargs))
        return {"status": "skipped_not_due", "surge_probability": 0.2}

    async def fake_once(user_id, **kwargs):
        calls.append(("check", user_id, kwargs))
        return {"status": "pending", "log_id": 7}

    monkeypatch.setattr(
        "api.admin.proactive_outreach_routes.run_outreach_due_once",
        fake_due_once,
    )
    monkeypatch.setattr(
        "api.admin.proactive_outreach_routes.run_outreach_once",
        fake_once,
    )

    due = proactive_client.post(
        "/api/v1/admin/proactive-outreach/run-once",
        json={"user_id": "u-proactive", "mode": "due"},
        headers=_auth_header(),
    )
    check = proactive_client.post(
        "/api/v1/admin/proactive-outreach/run-once",
        json={"user_id": "u-proactive", "mode": "check"},
        headers=_auth_header(),
    )

    assert due.status_code == 200, due.text
    assert check.status_code == 200, check.text
    assert due.json()["result"]["status"] == "skipped_not_due"
    assert check.json()["result"]["log_id"] == 7
    assert [item[0] for item in calls] == ["due", "check"]
    assert calls[0][1] == "u-proactive"


def test_proactive_outreach_scripted_simulation_never_external_publishes(proactive_client):
    response = proactive_client.post(
        "/api/v1/admin/proactive-outreach/simulate",
        json={"mode": "scripted"},
        headers=_auth_header(),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["mode"] == "scripted"
    assert data["report"]["metrics"]["external_push_count"] == 0
