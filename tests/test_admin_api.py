"""Admin API 集成测试——Sticker CRUD + Block Rules + status 枚举 + 认证。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, get_db
from server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """独立 SQLite 测试库——不污染真实数据库。"""
    monkeypatch.setattr("api.admin_routes.NANOBOT_API_TOKEN", "test-token")
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
        monkeypatch.setattr("api.admin_routes.NANOBOT_API_TOKEN", "")
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
