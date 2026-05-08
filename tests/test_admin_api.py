"""Admin API 集成测试——Sticker CRUD + Block Rules + status 枚举验证。"""
import pytest
from fastapi.testclient import TestClient

from server import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _set_test_token(monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_API_TOKEN", "test-token")


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def setup_db():
    from core.database import init_db
    init_db()


class TestStickerCRUD:
    def test_create_sticker_active(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://example.com/test.png",
            "name": "test_sticker", "status": "active",
        }, headers=auth_header)
        assert r.status_code == 200
        assert r.json()["status"] == "active"

    def test_create_sticker_group_id_normalizes(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://example.com/g.png",
            "group_id": "623690872", "status": "active",
        }, headers=auth_header)
        assert r.status_code == 200
        assert r.json()["chat_stream_id"] == "qq:623690872:group"

    def test_create_sticker_empty_file_ref(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "", "name": "bad",
        }, headers=auth_header)
        assert r.status_code == 400

    def test_create_sticker_invalid_status_422(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/a.png", "status": "invalid_status",
        }, headers=auth_header)
        assert r.status_code == 422

    def test_list_stickers_filter_by_status(self, client, auth_header):
        client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/d.png", "status": "disabled",
        }, headers=auth_header)
        r = client.get("/api/v1/admin/stickers?status=active", headers=auth_header)
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["status"] == "active"

    def test_update_status_to_deleted_and_list(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/del.png", "status": "active",
        }, headers=auth_header)
        sid = r.json()["id"]
        r2 = client.put(f"/api/v1/admin/stickers/{sid}", json={
            "status": "deleted",
        }, headers=auth_header)
        assert r2.status_code == 200
        assert r2.json()["status"] == "deleted"
        r3 = client.get("/api/v1/admin/stickers?status=deleted", headers=auth_header)
        assert any(s["id"] == sid for s in r3.json()["items"])

    def test_restore_deleted_to_disabled(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/restore.png", "status": "active",
        }, headers=auth_header)
        sid = r.json()["id"]
        client.put(f"/api/v1/admin/stickers/{sid}", json={"status": "deleted"}, headers=auth_header)
        r2 = client.put(f"/api/v1/admin/stickers/{sid}", json={
            "status": "disabled",
        }, headers=auth_header)
        assert r2.status_code == 200
        assert r2.json()["status"] == "disabled"

    def test_update_invalid_status_422(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/inv.png", "status": "active",
        }, headers=auth_header)
        sid = r.json()["id"]
        r2 = client.put(f"/api/v1/admin/stickers/{sid}", json={
            "status": "xxx",
        }, headers=auth_header)
        assert r2.status_code == 422

    def test_enable_disable_cycle(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/ed.png", "status": "active",
        }, headers=auth_header)
        sid = r.json()["id"]
        client.post(f"/api/v1/admin/stickers/{sid}/disable", headers=auth_header)
        client.post(f"/api/v1/admin/stickers/{sid}/enable", headers=auth_header)
        r4 = client.get(f"/api/v1/admin/stickers/{sid}", headers=auth_header)
        assert r4.json()["status"] == "active"

    def test_soft_delete_still_getable(self, client, auth_header):
        r = client.post("/api/v1/admin/stickers", json={
            "file_ref": "http://x.com/soft.png", "status": "active",
        }, headers=auth_header)
        sid = r.json()["id"]
        client.delete(f"/api/v1/admin/stickers/{sid}", headers=auth_header)
        r2 = client.get(f"/api/v1/admin/stickers/{sid}", headers=auth_header)
        assert r2.status_code == 200
        assert r2.json()["status"] == "deleted"


class TestBlockRule:
    def test_create_and_list(self, client, auth_header):
        r = client.post("/api/v1/admin/block-rules", json={
            "user_id": "12345", "target_type": "private", "reason": "test",
        }, headers=auth_header)
        assert r.status_code == 200
        assert r.json()["user_id"] == "12345"
        r2 = client.get("/api/v1/admin/block-rules", headers=auth_header)
        assert any(b["user_id"] == "12345" for b in r2.json()["items"])

    def test_toggle_enabled(self, client, auth_header):
        r = client.post("/api/v1/admin/block-rules", json={
            "user_id": "99999", "target_type": "private",
        }, headers=auth_header)
        bid = r.json()["id"]
        r2 = client.put(f"/api/v1/admin/block-rules/{bid}", json={
            "enabled": 0}, headers=auth_header)
        assert r2.json()["enabled"] == 0
        r3 = client.put(f"/api/v1/admin/block-rules/{bid}", json={
            "enabled": 1}, headers=auth_header)
        assert r3.json()["enabled"] == 1


class TestHealth:
    def test_health_ok(self, client, auth_header):
        r = client.get("/api/v1/admin/health", headers=auth_header)
        assert r.status_code == 200
        assert r.json()["ok"]
