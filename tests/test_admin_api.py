"""Admin API 集成测试——Sticker CRUD + Block Rules + status 枚举 + 认证。"""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ChatLog, ChatStreamConfig, StickerMemory, User, get_db
from server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """独立 SQLite 测试库——不污染真实数据库。"""
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "test-token")
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

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer test-token"}


def _ok(r, msg=""):
    assert r.status_code == 200, f"{msg}: {r.text}"
    return r.json()


class TestAuth:
    def test_no_token_returns_401(self, client):
        assert client.get("/api/v1/admin/stickers").status_code == 401

    def test_wrong_token_returns_401(self, client):
        r = client.get("/api/v1/admin/stickers",
                       headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_me_ok(self, client, auth_header):
        r = client.get("/api/v1/admin/me", headers=auth_header)
        assert r.status_code == 200
        assert r.json()["ok"]

    def test_no_token_configured_returns_503(self, client, monkeypatch):
        monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "")
        assert client.get("/api/v1/admin/stickers").status_code == 503


class TestStickerCRUD:
    def test_create_active(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://example.com/test.png",
            "name": "test", "status": "active",
        }, headers=auth_header)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"

    def test_group_id_normalizes_stream(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/g.png",
            "group_id": "623690872", "status": "active",
        }, headers=auth_header)
        assert r.status_code == 200, r.text
        assert r.json()["chat_stream_id"] == "qq:623690872:group"

    def test_empty_file_ref_400(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "", "name": "bad",
        }, headers=auth_header)
        assert r.status_code == 400

    def test_invalid_create_status_422(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/a.png", "status": "invalid",
        }, headers=auth_header)
        assert r.status_code == 422

    def test_invalid_update_status_422(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/inv.png", "status": "active",
        }, headers=auth_header)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        r2 = client.put(f"/api/v1/admin/stickers/{sid}", json={
            "status": "xxx",
        }, headers=auth_header)
        assert r2.status_code == 422

    def test_list_filter_by_status(self, client, auth_header):
        client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/d.png", "status": "disabled",
        }, headers=auth_header)
        r = client.get("/api/v1/admin/stickers?status=active", headers=auth_header)
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["status"] == "active"

    def test_soft_delete_and_restore(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/del.png", "status": "active",
        }, headers=auth_header)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        client.delete(f"/api/v1/admin/stickers/{sid}", headers=auth_header)
        r2 = client.get(f"/api/v1/admin/stickers/{sid}", headers=auth_header)
        assert r2.status_code == 200
        assert r2.json()["status"] == "deleted"
        r3 = client.put(f"/api/v1/admin/stickers/{sid}", json={
            "status": "disabled",
        }, headers=auth_header)
        assert r3.status_code == 200
        assert r3.json()["status"] == "disabled"

    def test_deleted_sticker_in_list(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/vis.png", "status": "active",
        }, headers=auth_header)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        client.delete(f"/api/v1/admin/stickers/{sid}", headers=auth_header)
        r2 = client.get("/api/v1/admin/stickers?status=deleted", headers=auth_header)
        assert r2.status_code == 200
        assert any(s["id"] == sid for s in r2.json()["items"])

    def test_enable_disable_cycle(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/ed.png", "status": "active",
        }, headers=auth_header)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        client.post(f"/api/v1/admin/stickers/{sid}/disable", headers=auth_header)
        client.post(f"/api/v1/admin/stickers/{sid}/enable", headers=auth_header)
        r4 = client.get(f"/api/v1/admin/stickers/{sid}", headers=auth_header)
        assert r4.status_code == 200
        assert r4.json()["status"] == "active"

    def test_deleted_not_found_by_search(self, client, auth_header):
        """deleted sticker 不应被 sticker_search 返回。"""
        # 通过 DB browser 创建 + 验证 deleted 后 search 不可见
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/searchtest.png", "status": "active",
            "name": "searchme", "description": "searchme",
        }, headers=auth_header)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        # 通过 Admin list 确认 active
        r1 = client.get(f"/api/v1/admin/stickers?search=searchme&status=active",
                        headers=auth_header)
        assert any(s["id"] == sid for s in r1.json()["items"])
        # 删除
        client.delete(f"/api/v1/admin/stickers/{sid}", headers=auth_header)
        # active 列表不再可见
        r2 = client.get(f"/api/v1/admin/stickers?search=searchme&status=active",
                        headers=auth_header)
        assert not any(s["id"] == sid for s in r2.json()["items"]), \
            f"deleted sticker {sid} should not appear in active list"


class TestBlockRule:
    def test_create_and_list(self, client, auth_header):
        client.post("/api/v1/admin/block-rules", json={
            "user_id": "777", "target_type": "private", "reason": "test",
        }, headers=auth_header)
        r = client.get("/api/v1/admin/block-rules", headers=auth_header)
        assert r.status_code == 200
        assert any(b["user_id"] == "777" for b in r.json()["items"])

    def test_toggle_enabled(self, client, auth_header):
        r = client.post("/api/v1/admin/block-rules", json={
            "user_id": "888", "target_type": "private",
        }, headers=auth_header)
        assert r.status_code == 200, r.text
        bid = r.json()["id"]
        r2 = client.put(f"/api/v1/admin/block-rules/{bid}", json={
            "enabled": 0}, headers=auth_header)
        assert r2.status_code == 200
        assert r2.json()["enabled"] == 0
        r3 = client.put(f"/api/v1/admin/block-rules/{bid}", json={
            "enabled": 1}, headers=auth_header)
        assert r3.status_code == 200
        assert r3.json()["enabled"] == 1


class TestPrivateBlockFlow:
    def test_blocked_user_chat_writes_log_with_files(self, client, auth_header):
        """私聊 block 命中后写 ChatLog + 保留附件摘要。"""
        client.post("/api/v1/admin/block-rules", json={
            "user_id": "blocked_usr", "target_type": "private",
        }, headers=auth_header)

        r = client.post("/api/v1/chat", json={
            "user_id": "blocked_usr", "session_id": "private_blocked_usr",
            "query": "hello", "sender_name": "test",
            "files": ["http://x.com/img.png"],
        }, headers={"Authorization": "Bearer test-token"})
        assert r.status_code == 200, r.text
        assert r.json().get("status") == "silent"

        # 通过 DB browser 验证 ChatLog 内容
        r2 = client.get("/api/v1/admin/db/tables/chat_logs?limit=5",
                        headers=auth_header)
        assert r2.status_code == 200
        rows = r2.json()["rows"]
        row = next((r for r in rows if r.get("user_id") == "blocked_usr"), None)
        assert row is not None, f"ChatLog should contain blocked user: {rows}"
        assert "hello" in (row.get("content") or "")
        assert "[图片附件" in (row.get("content") or ""), \
            f"content should contain image attachment: {row.get('content')}"
        assert "img.png" in (row.get("content") or "")


class TestObservabilityAPI:
    def test_overview_counts_recent_runtime_signals(self, client, auth_header):
        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(User(id="group_1001", name="测试群"))
            db.add(ChatStreamConfig(chat_stream_id="qq:1001:group", talk_value=0.35))
            db.add(ChatLog(
                user_id="group_1001", session_id="group_1001",
                role="ambient", content="[A]: 你好", session_name="测试群",
                created_at=now - timedelta(minutes=5),
                meta_json=json.dumps({
                    "timing_gate": {
                        "action": "wait",
                        "reason": "talk_value gate",
                        "generation": 3,
                        "latency_ms": 42,
                        "error_type": None,
                    }
                }, ensure_ascii=False),
            ))
            db.add(ChatLog(
                user_id="group_1001", session_id="group_1001",
                role="assistant", content="你好", sender_name="nanobot",
                created_at=now - timedelta(minutes=4),
            ))
            db.add(StickerMemory(
                chat_stream_id="qq:1001:group",
                sticker_hash="h1",
                file_ref="http://x.com/a.png",
                preview_status="fetch_failed",
                describe_status="failed",
                status="active",
            ))
            db.commit()

        r = client.get("/api/v1/admin/overview", headers=auth_header)
        data = _ok(r)
        assert data["counters"]["requests_1h"] >= 2
        assert data["counters"]["group_messages_1h"] == 1
        assert data["counters"]["replies_1h"] == 1
        assert data["counters"]["sticker_cache_failures"] == 1
        assert data["counters"]["sticker_describe_failures"] == 1
        assert any(item["name"] == "数据库可用" and item["ok"] for item in data["health"])

    def test_group_list_and_detail_expose_recent_decision(self, client, auth_header):
        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(User(id="group_2002", name="运行群"))
            db.add(ChatStreamConfig(chat_stream_id="qq:2002:group", talk_value=0.6))
            db.add(ChatLog(
                user_id="group_2002", session_id="group_2002",
                role="ambient", content="[B]: 刚才为什么不回", sender_name="B",
                session_name="运行群", message_id="m-1",
                created_at=now - timedelta(seconds=30),
                meta_json=json.dumps({
                    "timing_gate": {
                        "action": "no_reply",
                        "reason": "普通群聊不插话",
                        "generation": 8,
                        "latency_ms": 120,
                        "raw": "节奏普通 {\"action\":\"no_reply\"}",
                    }
                }, ensure_ascii=False),
            ))
            db.add(ChatLog(
                user_id="group_2002", session_id="group_2002",
                role="assistant", content="我在", sender_name="nanobot",
                created_at=now - timedelta(seconds=20),
            ))
            db.commit()

        groups = _ok(client.get("/api/v1/admin/groups", headers=auth_header))
        row = next(item for item in groups["items"] if item["group_id"] == "2002")
        assert row["session_name"] == "运行群"
        assert row["talk_value"] == 0.6
        assert row["recent_action"] == "no_reply"
        assert row["recent_reason"] == "普通群聊不插话"

        detail = _ok(client.get("/api/v1/admin/groups/2002", headers=auth_header))
        assert detail["group"]["group_id"] == "2002"
        assert detail["ambient_messages"][0]["message_id"] == "m-1"
        assert detail["timing_events"][0]["raw"].startswith("节奏普通")

    def test_timing_gate_events_returns_stats(self, client, auth_header):
        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(ChatLog(
                user_id="group_3003", session_id="group_3003",
                role="ambient", content="[C]: 测试", session_name="统计群",
                created_at=now,
                meta_json=json.dumps({
                    "timing_gate": {
                        "action": "no_reply",
                        "reason": "非法输出",
                        "latency_ms": 90,
                        "error_type": "parse_error",
                        "fallback_action": "no_reply",
                    }
                }, ensure_ascii=False),
            ))
            db.commit()

        data = _ok(client.get("/api/v1/admin/timing-gate/events", headers=auth_header))
        assert data["stats"]["parse_error"] >= 1
        assert data["stats"]["actions"]["no_reply"] >= 1
        assert data["items"][0]["parse_error"] is True
        assert data["items"][0]["fallback_action"] == "no_reply"
