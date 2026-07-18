import hashlib
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from core.database import (
    AdminAuditLog,
    Base,
    LLMApiRequestLog,
    OutboundDeliveryControl,
    ProactiveOutreachLog,
    SystemSetting,
    get_db,
)
from server import app
from tests.async_helpers import run_async


@pytest.fixture
def proactive_client(tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    from api.admin import proactive_outreach_routes

    monkeypatch.setattr(
        proactive_outreach_routes,
        "get_super_user_ids",
        lambda: {"u-proactive"},
        raising=False,
    )
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


def test_proactive_outreach_status_reports_redacted_target_and_runtime(
    proactive_client,
):
    db = next(app.dependency_overrides[get_db]())
    now = datetime(2026, 7, 8, 12, 0, 0)
    db.add(SystemSetting(
        key="proactive_outreach.enabled",
        value="true",
        description="开关",
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
    assert data["super_user_configured"] is True
    assert data["super_user_count"] == 1
    assert "super_user_ids" not in data
    assert "u-proactive" not in response.text
    settings_by_key = {item["key"]: item for item in data["settings"]}
    assert settings_by_key["proactive_outreach.enabled"]["value"] is True
    assert settings_by_key["proactive_outreach.ambiguous_hold_min"]["value"] == 120
    assert settings_by_key["proactive_outreach.repeat_topic_cooldown_min"]["value"] == 1440
    assert settings_by_key["proactive_outreach.allow_early_surge"]["value"] is False
    assert "bot.super_user_ids" not in settings_by_key
    assert data["stats"]["total"] == 2
    assert data["stats"]["by_status"]["sent"] == 1
    assert data["latest_logs"][0]["status"] == "sent"
    assert data["latest_logs"][0]["created_at_local"] == (
        "2026-07-08T12:00:00+08:00"
    )
    assert data["latest_logs"][0]["created_at_utc"] == "2026-07-08T04:00:00Z"
    assert "target_fingerprint" in data["latest_logs"][0]
    assert "user_id" not in data["latest_logs"][0]
    assert "idempotency_key" not in data["latest_logs"][0]
    assert data["latest_logs"][1]["grounding"]["recent_threads"] == ["项目进展"]
    assert data["llm_logs"][0]["source"] == "classifier.timing_proactive"
    assert data["llm_logs"][0]["latency_ms"] == 123


def test_proactive_outreach_logs_filter_by_status_and_target_fingerprint(
    proactive_client,
):
    db = next(app.dependency_overrides[get_db]())
    db.add(ProactiveOutreachLog(
        user_id="u-proactive",
        idempotency_key="outreach:u-proactive:pending",
        grounding_json="{}",
        status="pending",
    ))
    db.add(ProactiveOutreachLog(
        user_id="other-user",
        idempotency_key="outreach:other-user:pending",
        grounding_json="{}",
        status="pending",
    ))
    db.commit()

    target_fingerprint = "sha256:" + hashlib.sha256(
        b"u-proactive"
    ).hexdigest()[:12]

    response = proactive_client.get(
        "/api/v1/admin/proactive-outreach/logs",
        params={
            "status": "pending",
            "target_fingerprint": target_fingerprint,
        },
        headers=_auth_header(),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "pending"
    assert "user_id" not in data["items"][0]
    assert data["items"][0]["target_fingerprint"] == target_fingerprint


def test_proactive_outreach_status_exposes_safe_outbox_linkage(
    proactive_client,
    monkeypatch,
):
    from core.proactive_outreach import deliver_outreach_once

    db = next(app.dependency_overrides[get_db]())
    db.add(OutboundDeliveryControl(
        source_type="proactive_outreach",
        mode="outbox_active",
        cutover_epoch=1,
        effective_from=datetime(1970, 1, 1),
        protocol_version=2,
        writer_version=0,
    ))
    db.commit()
    monkeypatch.setenv(
        "NANOBOT_QQ_PUSH_CONFIG_REVISION",
        "admin-proactive-revision",
    )
    queued = run_async(deliver_outreach_once(
        user_id="u-proactive",
        idempotency_key="outreach:u-proactive:queued",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="管理页关联测试",
        next_check_at=None,
        next_intent="",
        message="只用于管理页关联测试的正文",
        forced=False,
        db=db,
    ))

    status_response = proactive_client.get(
        "/api/v1/admin/proactive-outreach/status",
        headers=_auth_header(),
    )
    logs_response = proactive_client.get(
        "/api/v1/admin/proactive-outreach/logs",
        headers=_auth_header(),
    )

    assert status_response.status_code == 200, status_response.text
    assert logs_response.status_code == 200, logs_response.text
    status_data = status_response.json()
    status_item = status_data["latest_logs"][0]
    logs_item = logs_response.json()["items"][0]
    assert status_data["delivery_stats"]["runs"]["by_status"] == {"queued": 1}
    assert status_data["delivery_stats"]["active_outboxes"]["by_status"] == {
        "pending": 1
    }
    for item in (status_item, logs_item):
        assert item["outbound_run_id"] == queued["run_id"]
        assert item["run_status"] == "queued"
        assert item["active_outbox_id"] == queued["outbox_id"]
        assert item["outbox_status"] == "pending"
        assert item["payload_sha256_prefix"]
        assert item["delivery_error_type"] == ""
        assert item["created_at_local"].endswith("+08:00")
        assert item["created_at_utc"].endswith("Z")
        assert item["delivery_updated_at_utc"].endswith("Z")
        for forbidden in (
            "payload_json",
            "destination_snapshot_json",
            "source_snapshot_json",
            "lease_token",
            "idempotency_key",
        ):
            assert forbidden not in item


def test_proactive_outreach_status_marks_changed_source_as_fenced(
    proactive_client,
    monkeypatch,
):
    from core.proactive_outreach import deliver_outreach_once

    db = next(app.dependency_overrides[get_db]())
    db.add(OutboundDeliveryControl(
        source_type="proactive_outreach",
        mode="outbox_active",
        cutover_epoch=1,
        effective_from=datetime(1970, 1, 1),
        protocol_version=2,
        writer_version=0,
    ))
    db.commit()
    monkeypatch.setenv(
        "NANOBOT_QQ_PUSH_CONFIG_REVISION",
        "admin-proactive-revision",
    )
    queued = run_async(deliver_outreach_once(
        user_id="u-proactive",
        idempotency_key="outreach:u-proactive:fenced",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="管理页 fenced 关联测试",
        next_check_at=None,
        next_intent="",
        message="入队时的正文",
        forced=False,
        db=db,
    ))
    row = db.get(ProactiveOutreachLog, queued["log_id"])
    row.message = "入队后已变化的正文"
    db.commit()

    status_response = proactive_client.get(
        "/api/v1/admin/proactive-outreach/status",
        headers=_auth_header(),
    )
    logs_response = proactive_client.get(
        "/api/v1/admin/proactive-outreach/logs",
        headers=_auth_header(),
    )

    assert status_response.status_code == 200, status_response.text
    assert logs_response.status_code == 200, logs_response.text
    for item in (
        status_response.json()["latest_logs"][0],
        logs_response.json()["items"][0],
    ):
        assert item["outbound_run_id"] == queued["run_id"]
        assert item["run_status"] == "fenced"
        assert item["active_outbox_id"] is None
        assert item["outbox_status"] == ""
        assert item["payload_sha256_prefix"] == ""


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
    ambiguity_hold = proactive_client.put(
        "/api/v1/admin/proactive-outreach/settings/"
        "proactive_outreach.ambiguous_hold_min",
        json={"value": 90},
        headers=_auth_header(),
    )
    invalid_ambiguity_hold = proactive_client.put(
        "/api/v1/admin/proactive-outreach/settings/"
        "proactive_outreach.ambiguous_hold_min",
        json={"value": 0},
        headers=_auth_header(),
    )
    repeat_cooldown = proactive_client.put(
        "/api/v1/admin/proactive-outreach/settings/"
        "proactive_outreach.repeat_topic_cooldown_min",
        json={"value": 720},
        headers=_auth_header(),
    )
    early_surge = proactive_client.put(
        "/api/v1/admin/proactive-outreach/settings/"
        "proactive_outreach.allow_early_surge",
        json={"value": False},
        headers=_auth_header(),
    )
    removed_super_user_setting = proactive_client.put(
        "/api/v1/admin/proactive-outreach/settings/bot.super_user_ids",
        json={"value": "u-proactive"},
        headers=_auth_header(),
    )

    assert ok.status_code == 200, ok.text
    assert repeat_cooldown.status_code == 200, repeat_cooldown.text
    assert early_surge.status_code == 200, early_surge.text
    assert ok.json()["value"] is True
    assert denied.status_code == 400
    assert ambiguity_hold.status_code == 200, ambiguity_hold.text
    assert ambiguity_hold.json()["value"] == 90
    assert invalid_ambiguity_hold.status_code == 400
    assert removed_super_user_setting.status_code == 400


def test_proactive_outreach_run_once_uses_existing_runtime(proactive_client, monkeypatch):
    calls = []

    async def fake_due_once(user_id, **kwargs):
        calls.append(("due", user_id, kwargs))
        return {"status": "skipped_not_due", "surge_probability": 0.2}

    async def fake_once(user_id, **kwargs):
        calls.append(("check", user_id, kwargs))
        return {
            "status": "queued",
            "log_id": 7,
            "run_id": 8,
            "outbox_id": 9,
            "deduplicated": False,
            "payload_json": "不得返回的 payload",
            "message": "不得返回的正文",
            "idempotency_key": "不得返回的业务键",
        }

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
        json={"user_id": "other-user", "mode": "due"},
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
    assert check.json()["result"] == {
        "status": "queued",
        "log_id": 7,
        "run_id": 8,
        "outbox_id": 9,
        "deduplicated": False,
    }
    assert "user_id" not in due.json()
    assert due.json()["target_fingerprint"]
    assert "u-proactive" not in due.text
    assert "other-user" not in due.text
    assert [item[0] for item in calls] == ["due", "check"]
    assert calls[0][1] == "u-proactive"
    db = next(app.dependency_overrides[get_db]())
    audit_row = (
        db.query(AdminAuditLog)
        .filter(AdminAuditLog.action == "run_proactive_outreach_once")
        .order_by(AdminAuditLog.id.desc())
        .first()
    )
    assert audit_row is not None
    assert audit_row.target_id == check.json()["target_fingerprint"]
    assert "u-proactive" not in audit_row.detail_json
    assert "payload" not in audit_row.detail_json
    assert "不得返回" not in audit_row.detail_json


@pytest.mark.parametrize("failure_point", ["add", "commit"])
def test_proactive_outreach_run_once_requires_durable_request_audit(
    proactive_client,
    monkeypatch,
    failure_point,
):
    from api.admin import proactive_outreach_routes

    runtime_calls = []

    async def fake_once(*args, **kwargs):
        runtime_calls.append((args, kwargs))
        return {"status": "queued"}

    class FailingAuditSession:
        def add(self, _row):
            if failure_point == "add":
                raise RuntimeError("审计写入失败")

        def commit(self):
            if failure_point == "commit":
                raise RuntimeError("审计提交失败")

        def rollback(self):
            return None

    monkeypatch.setattr(proactive_outreach_routes, "run_outreach_once", fake_once)
    request = Request({
        "type": "http",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    })

    with pytest.raises(RuntimeError, match="审计"):
        run_async(proactive_outreach_routes.proactive_outreach_run_once(
            proactive_outreach_routes.ProactiveRunOnceRequest(mode="check"),
            request,
            db=FailingAuditSession(),
            _auth="admin",
        ))

    assert runtime_calls == []


def test_proactive_outreach_run_once_records_failed_runtime_attempt(
    proactive_client,
    monkeypatch,
):
    from api.admin import proactive_outreach_routes

    async def fail_once(*_args, **_kwargs):
        raise RuntimeError("不得进入审计的运行时异常正文")

    monkeypatch.setattr(proactive_outreach_routes, "run_outreach_once", fail_once)
    db = next(app.dependency_overrides[get_db]())
    request = Request({
        "type": "http",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    })

    with pytest.raises(RuntimeError, match="运行时异常正文"):
        run_async(proactive_outreach_routes.proactive_outreach_run_once(
            proactive_outreach_routes.ProactiveRunOnceRequest(mode="check"),
            request,
            db=db,
            _auth="admin",
        ))

    db.expire_all()
    rows = (
        db.query(AdminAuditLog)
        .filter(AdminAuditLog.target_type == "proactive_outreach")
        .order_by(AdminAuditLog.id.asc())
        .all()
    )
    assert [row.action for row in rows] == [
        "run_proactive_outreach_once_requested",
        "run_proactive_outreach_once_failed",
    ]
    assert all("u-proactive" not in row.detail_json for row in rows)
    assert all("运行时异常正文" not in row.detail_json for row in rows)
    assert '\"error_type\": \"runtime_error\"' in rows[-1].detail_json


def test_proactive_outreach_run_once_does_not_commit_runtime_dirty_state(
    proactive_client,
    monkeypatch,
):
    from api.admin import proactive_outreach_routes

    async def leave_dirty_state(*_args, **kwargs):
        kwargs["db"].add(SystemSetting(
            key="test.run_once.runtime_pollution",
            value="should-not-persist",
            description="运行时未提交脏状态",
        ))
        return {"status": "queued"}

    monkeypatch.setattr(
        proactive_outreach_routes,
        "run_outreach_once",
        leave_dirty_state,
    )
    db = next(app.dependency_overrides[get_db]())
    request = Request({
        "type": "http",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    })

    response = run_async(proactive_outreach_routes.proactive_outreach_run_once(
        proactive_outreach_routes.ProactiveRunOnceRequest(mode="check"),
        request,
        db=db,
        _auth="admin",
    ))

    verify = next(app.dependency_overrides[get_db]())
    assert response["result"]["status"] == "queued"
    assert (
        verify.query(SystemSetting)
        .filter(SystemSetting.key == "test.run_once.runtime_pollution")
        .count()
        == 0
    )
    assert (
        verify.query(AdminAuditLog)
        .filter(AdminAuditLog.target_type == "proactive_outreach")
        .count()
        == 2
    )


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
