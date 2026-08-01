"""WebUI 管理员触发器 API 测试。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.admin import scheduled_task_routes
from api.admin.common import verify_admin
from core.database import (
    AdminAuditLog,
    OutboundDeliveryControl,
    ScheduledTask,
    ScheduledTaskExecution,
    get_db,
)


def _client(db_session) -> TestClient:
    if db_session.get(OutboundDeliveryControl, "scheduled_task") is None:
        db_session.add(OutboundDeliveryControl(source_type="scheduled_task"))
        db_session.commit()
    app = FastAPI()
    app.include_router(
        scheduled_task_routes.router,
        prefix="/api/v1/admin",
    )

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_admin] = lambda: "admin"
    return TestClient(app)


def _prompt_payload(**overrides):
    payload = {
        "name": "每日资讯",
        "schedule": "0 9 * * *",
        "target_type": "private",
        "target_id": "10001",
        "prompt_template": "汇总今天最重要的三条 AI 资讯",
    }
    payload.update(overrides)
    return payload


def test_admin_trigger_create_list_detail_and_update(db_session):
    client = _client(db_session)

    created = client.post(
        "/api/v1/admin/triggers",
        json=_prompt_payload(),
    )

    assert created.status_code == 201
    created_body = created.json()
    task_id = created_body["id"]
    assert created_body["definition"]["mode"] == "prompt"
    assert created_body["schedule"] == "0 9 * * *"
    assert created_body["schedule_display"] == "0 9 * * *"
    assert created_body["owner_chat_stream_id"]
    assert created_body["owner_migration_required"] is False
    assert created_body["definition_version"] == 1

    listed = client.get("/api/v1/admin/triggers")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == task_id

    detail = client.get(f"/api/v1/admin/triggers/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["definition"]["prompt_template"].startswith("汇总")

    updated = client.put(
        f"/api/v1/admin/triggers/{task_id}",
        json={
            "name": "半小时提醒",
            "schedule": "every 30m",
            "target_type": "group",
            "target_id": "7788",
            "content": "该休息一下了",
            "expected_version": 1,
        },
    )

    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["definition_version"] == 2
    assert updated_body["definition"]["mode"] == "content"
    assert updated_body["definition"]["content"] == "该休息一下了"
    assert updated_body["schedule_display"] == "每30分钟"
    assert updated_body["target_type"] == "group"
    assert db_session.get(ScheduledTask, task_id).owner_chat_type == "group"

    stale = client.put(
        f"/api/v1/admin/triggers/{task_id}",
        json={
            **_prompt_payload(name="过期更新"),
            "expected_version": 1,
        },
    )
    assert stale.status_code == 409
    assert "当前版本 2" in stale.json()["detail"]


def test_admin_trigger_toggle_and_manual_run_are_versioned_and_audited(
    db_session,
):
    client = _client(db_session)
    created = client.post(
        "/api/v1/admin/triggers",
        json=_prompt_payload(),
    ).json()
    task_id = created["id"]

    toggled = client.post(
        f"/api/v1/admin/triggers/{task_id}/toggle",
        json={"expected_version": created["definition_version"]},
    )

    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False
    assert toggled.json()["definition_version"] == 2

    run = client.post(
        f"/api/v1/admin/triggers/{task_id}/run",
        json={
            "expected_version": 2,
            "request_id": "trigger-run-test-0001",
        },
    )

    assert run.status_code == 202
    assert run.json()["status"] == "pending"
    execution = db_session.get(
        ScheduledTaskExecution,
        run.json()["execution_id"],
    )
    assert execution.task_id == task_id
    assert execution.trigger_type == "manual"
    actions = {
        row.action
        for row in db_session.query(AdminAuditLog).all()
    }
    assert {"trigger_create", "trigger_toggle", "trigger_run"} <= actions


def test_admin_trigger_rejects_ambiguous_definition_source(db_session):
    client = _client(db_session)

    response = client.post(
        "/api/v1/admin/triggers",
        json={
            **_prompt_payload(),
            "content": "不能与 prompt_template 同时提交",
        },
    )

    assert response.status_code == 422
    assert "必须且只能填写一个" in response.text
