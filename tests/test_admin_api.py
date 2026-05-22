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

    def test_version_ok(self, client, auth_header):
        r = client.get("/api/v1/admin/version", headers=auth_header)
        data = _ok(r)
        assert data["commit"]
        assert "display" in data

    def test_no_token_configured_returns_503(self, client, monkeypatch):
        monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "")
        assert client.get("/api/v1/admin/stickers").status_code == 503


class TestWebUIStatic:
    def test_spa_route_returns_webui_index(self, client):
        r = client.get("/tools")

        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert '<div id="root"></div>' in r.text

    def test_missing_api_route_keeps_404(self, client):
        r = client.get("/api/v1/admin/not-a-real-route")

        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")


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


class TestModelCatalog:
    """模型目录 + 路由 API 测试"""

    def test_get_catalog_returns_models(self, client, auth_header):
        r = client.get("/api/v1/admin/model-catalog", headers=auth_header)
        assert r.status_code == 200
        data = r.json()
        assert "models" in data
        assert "last_updated" in data

    def test_patch_catalog_not_found(self, client, auth_header):
        r = client.patch("/api/v1/admin/model-catalog/nonexistent-model-xyz",
                          json={"intelligence": 5}, headers=auth_header)
        assert r.status_code == 404

    def test_get_routes_returns_stages(self, client, auth_header):
        r = client.get("/api/v1/admin/model-routes", headers=auth_header)
        assert r.status_code == 200
        data = r.json()
        assert "main_chat" in data["routes"]
        r0 = data["routes"]["main_chat"]
        assert "model" in r0
        assert "editable" in r0

    def test_get_model_replies(self, client, auth_header):
        r = client.get("/api/v1/admin/model-replies", headers=auth_header)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "count" in data

    def test_patch_catalog_updates_fields(self, client, auth_header, monkeypatch):
        """PATCH 成功修改后 GET 能读到新值"""
        from clients.model_registry import registry

        # 隔离：禁止写入真实 models.json
        monkeypatch.setattr(registry, "save_registry", lambda: None)

        test_model = {
            "id": "test-patch-model", "model": "test-patch-model",
            "provider": "openai-compatible", "tier": "fast",
            "intelligence": 5, "cost_input_1m": 0.5, "cost_output_1m": 2.0,
            "enabled": True, "tags": ["test"],
        }
        registry.add_or_update_model(test_model)
        try:
            r = client.patch("/api/v1/admin/model-catalog/test-patch-model", json={
                "intelligence": 9, "cost_input_1m": 0.123, "enabled": False,
            }, headers=auth_header)
            assert r.status_code == 200
            assert r.json()["ok"] is True

            # 验证 GET
            r2 = client.get("/api/v1/admin/model-catalog", headers=auth_header)
            models = {m["id"]: m for m in r2.json()["models"]}
            m = models.get("test-patch-model")
            assert m is not None
            assert m["intelligence"] == 9
            assert m["cost_input_1m"] == 0.123
            assert m["enabled"] is False
        finally:
            # 清理内存中的测试模型，避免污染后续测试
            registry.data["models"] = [
                x for x in registry.data.get("models", [])
                if x.get("id") != "test-patch-model"
            ]

    def test_patch_catalog_invalid_tier_returns_422(self, client, auth_header, monkeypatch):
        """PATCH 传非法 tier 返回 422"""
        from clients.model_registry import registry

        monkeypatch.setattr(registry, "save_registry", lambda: None)

        test_model = {
            "id": "test-invalid-tier", "model": "test-invalid-tier",
            "provider": "openai-compatible", "tier": "fast",
            "intelligence": 5, "cost_input_1m": 0.5, "cost_output_1m": 2.0,
            "enabled": True,
        }
        registry.add_or_update_model(test_model)
        try:
            r = client.patch("/api/v1/admin/model-catalog/test-invalid-tier", json={
                "tier": "invalid-tier-name",
            }, headers=auth_header)
            assert r.status_code == 422
        finally:
            registry.data["models"] = [
                x for x in registry.data.get("models", [])
                if x.get("id") != "test-invalid-tier"
            ]

    def test_patch_catalog_tags_dedup_and_lowercase(self, client, auth_header, monkeypatch):
        """PATCH tags 去重、小写、去空格——空字符串被过滤"""
        from clients.model_registry import registry

        monkeypatch.setattr(registry, "save_registry", lambda: None)

        test_model = {
            "id": "test-tags-sanitize", "model": "test-tags-sanitize",
            "provider": "openai-compatible", "tier": "fast",
            "intelligence": 5, "cost_input_1m": 0.5, "cost_output_1m": 2.0,
            "enabled": True, "tags": [],
        }
        registry.add_or_update_model(test_model)
        try:
            r = client.patch("/api/v1/admin/model-catalog/test-tags-sanitize", json={
                "tags": ["  Free  ", "FREE", "New", ""],
            }, headers=auth_header)
            assert r.status_code == 200
            updates = r.json()["updates"]
            assert updates["tags"] == ["free", "new"], f"unexpected tags: {updates['tags']}"

            # GET 验证
            r2 = client.get("/api/v1/admin/model-catalog", headers=auth_header)
            models = {m["id"]: m for m in r2.json()["models"]}
            m = models.get("test-tags-sanitize")
            assert m["tags"] == ["free", "new"]
        finally:
            registry.data["models"] = [
                x for x in registry.data.get("models", [])
                if x.get("id") != "test-tags-sanitize"
            ]


class TestToolAdmin:
    def test_tools_separate_config_enabled_from_runtime_preview(self, client, auth_header):
        r = client.get(
            "/api/v1/admin/tools",
            params={"chat_type": "group", "runtime_preset": "lightweight"},
            headers=auth_header,
        )

        data = _ok(r)
        tools = {item["name"]: item for item in data["tools"]}

        assert data["runtime_preset"] == "lightweight"
        assert tools["ai_daily"]["configured_enabled"] is True
        assert tools["ai_daily"]["effective"] is True
        assert tools["ai_daily"]["runtime_effective"] is False
        assert tools["ai_daily"]["runtime_disabled_reason"] == "运行时轻量预设"
        assert tools["reply"]["configured_enabled"] is True
        assert tools["reply"]["runtime_effective"] is True

    def test_tools_have_separate_superuser_private_default_template(self, client, auth_header):
        r = client.get(
            "/api/v1/admin/tools",
            params={"chat_type": "private_superuser", "runtime_preset": "full"},
            headers=auth_header,
        )

        data = _ok(r)
        tools = {item["name"]: item for item in data["tools"]}
        assert tools["group_analysis"]["private_default"] is False
        assert tools["group_analysis"]["private_superuser_default"] is True
        assert tools["group_analysis"]["configured_enabled"] is True

        r2 = client.put(
            "/api/v1/admin/tools/group_analysis",
            json={"private_superuser_default": False},
            headers=auth_header,
        )
        _ok(r2)

        r3 = client.get(
            "/api/v1/admin/tools",
            params={"chat_type": "private_superuser", "runtime_preset": "full"},
            headers=auth_header,
        )
        tools_after = {item["name"]: item for item in _ok(r3)["tools"]}
        assert tools_after["group_analysis"]["private_superuser_default"] is False
        assert tools_after["group_analysis"]["configured_enabled"] is False

        audit = _ok(client.get(
            "/api/v1/admin/audit-logs",
            params={"target_type": "tool", "limit": 10},
            headers=auth_header,
        ))
        assert any(
            item["action"] == "tool_default_update"
            and item["target_id"] == "group_analysis"
            and item["detail_json"].get("private_superuser_default") is False
            for item in audit["items"]
        )

    def test_tools_lightweight_profile_is_configurable_preset(self, client, auth_header):
        r = client.get(
            "/api/v1/admin/tools",
            params={"chat_type": "group"},
            headers=auth_header,
        )
        tools = {item["name"]: item for item in _ok(r)["tools"]}
        assert tools["ai_daily"]["lightweight_default"] is False
        assert tools["reply"]["lightweight_default"] is True

        _ok(client.put(
            "/api/v1/admin/tools/ai_daily",
            json={"lightweight_default": True},
            headers=auth_header,
        ))

        r2 = client.get(
            "/api/v1/admin/tools",
            params={"chat_type": "group", "runtime_preset": "lightweight"},
            headers=auth_header,
        )
        tools_after = {item["name"]: item for item in _ok(r2)["tools"]}
        assert tools_after["ai_daily"]["lightweight_default"] is True
        assert tools_after["ai_daily"]["runtime_effective"] is True

        effective = _ok(client.get(
            "/api/v1/admin/tools/effective",
            params={"chat_type": "group", "runtime_preset": "lightweight"},
            headers=auth_header,
        ))
        assert "ai_daily" in effective["enabled"]

    def test_user_override_can_enable_tool_under_lightweight_preset(self, client, auth_header):
        before = _ok(client.get(
            "/api/v1/admin/tools/effective",
            params={
                "chat_type": "private",
                "user_id": "0000000000",
                "runtime_preset": "lightweight",
            },
            headers=auth_header,
        ))
        assert "ai_daily" not in before["enabled"]
        assert before["disabled"]["ai_daily"] == "运行时轻量预设"

        _ok(client.put(
            "/api/v1/admin/tools/ai_daily/override",
            json={
                "scope_type": "user",
                "scope_id": "0000000000",
                "enabled": True,
            },
            headers=auth_header,
        ))

        after = _ok(client.get(
            "/api/v1/admin/tools/effective",
            params={
                "chat_type": "private",
                "user_id": "0000000000",
                "runtime_preset": "lightweight",
            },
            headers=auth_header,
        ))
        assert "ai_daily" in after["enabled"]
        assert "ai_daily" not in after["disabled"]

        none_preset = _ok(client.get(
            "/api/v1/admin/tools/effective",
            params={
                "chat_type": "private",
                "user_id": "0000000000",
                "runtime_preset": "none",
            },
            headers=auth_header,
        ))
        assert "ai_daily" not in none_preset["enabled"]
        assert none_preset["disabled"]["ai_daily"] == "运行时预设=none"

    def test_tools_report_explicit_override_state(self, client, auth_header):
        _ok(client.put(
            "/api/v1/admin/tools/ai_daily/override",
            json={
                "scope_type": "user",
                "scope_id": "0000000000",
                "enabled": True,
            },
            headers=auth_header,
        ))

        data = _ok(client.get(
            "/api/v1/admin/tools",
            params={"chat_type": "private", "user_id": "0000000000"},
            headers=auth_header,
        ))
        tools = {item["name"]: item for item in data["tools"]}
        assert tools["ai_daily"]["override_present"] is True
        assert tools["ai_daily"]["override_enabled"] is True
        assert tools["python_sandbox"]["override_present"] is False
        assert tools["python_sandbox"]["override_enabled"] is None

    def test_tool_targets_list_real_groups_and_users(self, client, auth_header):
        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(User(id="group_2002", name="真实群"))
            db.add(User(id="group_test", name="测试群"))
            db.add(User(id="0000000000", name="雀"))
            db.add(User(id="local_test", name="本地测试"))
            db.add(User(id="private_0000000000", name="临时私聊 session"))
            db.add(User(id="admin", name="管理测试"))
            db.add(ChatLog(
                user_id="30001", session_id="group_3003",
                role="ambient", content="hello", sender_name="A",
                session_name="日志群", created_at=now,
            ))
            db.add(ChatLog(
                user_id="test-user", session_id="private_test-user",
                role="user", content="test", sender_name="测试用户",
                created_at=now,
            ))
            db.commit()

        groups = _ok(client.get(
            "/api/v1/admin/tools/targets",
            params={"scope_type": "group"},
            headers=auth_header,
        ))
        group_ids = {item["id"] for item in groups["items"]}
        assert {"2002", "3003"} <= group_ids
        assert "test" not in group_ids
        assert all(not item["id"].startswith("group_") for item in groups["items"])

        users = _ok(client.get(
            "/api/v1/admin/tools/targets",
            params={"scope_type": "user", "search": "雀"},
            headers=auth_header,
        ))
        assert [item["id"] for item in users["items"]] == ["0000000000"]
        assert users["items"][0]["label"] == "雀 (0000000000)"


class TestModelRoutes:
    def test_patch_route_updates_and_reads_back(self, client, auth_header, monkeypatch):
        """PATCH /model-routes/{stage} 成功写入后 GET 能读到新值"""
        from core.settings_service import settings
        from clients.model_registry import registry

        # 让 settings service 用测试 DB（通过 FastAPI get_db override）
        def test_session_factory():
            return next(app.dependency_overrides[get_db]())

        monkeypatch.setattr(settings, "_session_factory", test_session_factory)
        monkeypatch.setattr(registry, "save_registry", lambda: None)

        # 先在 registry 里注册一个测试模型（否则 PATCH 会 404）
        test_model = {
            "id": "test-override-model", "model": "test-override-model",
            "provider": "new-api", "tier": "smart",
            "intelligence": 8, "cost_input_1m": 1.0, "cost_output_1m": 2.0,
            "enabled": True,
        }
        registry.add_or_update_model(test_model)

        # 保存原值以便恢复
        r0 = client.get("/api/v1/admin/model-routes", headers=auth_header)
        original = r0.json()["routes"]["main_chat"]["model"]

        try:
            r = client.patch("/api/v1/admin/model-routes/main_chat", json={
                "value": "test-override-model",
            }, headers=auth_header)
            assert r.status_code == 200, r.text
            assert r.json()["ok"] is True

            # GET 使用 settings service → 测试 DB
            settings.invalidate()
            r2 = client.get("/api/v1/admin/model-routes", headers=auth_header)
            assert r2.json()["routes"]["main_chat"]["model"] == "test-override-model"
            assert r2.json()["routes"]["main_chat"]["source"] == "db_override"
        finally:
            # 清理
            registry.data["models"] = [
                x for x in registry.data.get("models", [])
                if x.get("id") != "test-override-model"
            ]
            # 恢复原值
            client.patch("/api/v1/admin/model-routes/main_chat", json={
                "value": original,
            }, headers=auth_header)

    def test_patch_route_unknown_stage_404(self, client, auth_header):
        r = client.patch("/api/v1/admin/model-routes/unknown_stage", json={
            "value": "x",
        }, headers=auth_header)
        assert r.status_code == 404

    def test_patch_route_empty_value_allowed(self, client, auth_header):
        """空 value 允许（相当于恢复默认）"""
        r = client.patch("/api/v1/admin/model-routes/smart_chat", json={
            "value": "",
        }, headers=auth_header)
        assert r.status_code == 200, r.text
        assert r.json()["value"] == ""

    def test_patch_route_nonexistent_model_404(self, client, auth_header):
        """field=model 的 stage，不存在的模型 ID 返回 404"""
        r = client.patch("/api/v1/admin/model-routes/main_chat", json={
            "value": "nonexistent-model-xyz-999",
        }, headers=auth_header)
        assert r.status_code == 404

    def test_patch_route_api_url_no_validation(self, client, auth_header):
        """field=api_url 的 stage 不校验模型存在性"""
        r = client.patch("/api/v1/admin/model-routes/timing_gate", json={
            "value": "http://custom-classifier:9999/v1",
        }, headers=auth_header)
        assert r.status_code == 200, r.text
        assert r.json()["ok"]


class TestModelHealthCheck:
    """模型连通性健康检查"""

    def test_health_check_returns_all_endpoints(self, client, auth_header, monkeypatch):
        """健康检查返回三个端点（new_api/classifier/image_summary）"""
        import aiohttp

        class FakeResponse:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        class FakeSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            def get(self, url, **kwargs): return FakeResponse()

        monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: FakeSession())

        r = client.post("/api/v1/admin/models/health-check", headers=auth_header)
        assert r.status_code == 200, r.text
        data = r.json()
        eps = data["endpoints"]
        assert "new_api" in eps
        assert "classifier" in eps
        assert "image_summary" in eps
        assert eps["new_api"]["reachable"] is True
        assert eps["new_api"]["usable"] is True
        assert eps["new_api"]["auth_error"] is False
        assert eps["new_api"]["status"] == 200

    def test_health_check_401_is_reachable_not_usable(self, client, auth_header, monkeypatch):
        """401 可达但不可用，且 auth_error=true"""
        import aiohttp

        class FakeResponse:
            status = 401
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        class FakeSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            def get(self, url, **kwargs): return FakeResponse()

        monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: FakeSession())

        r = client.post("/api/v1/admin/models/health-check", headers=auth_header)
        data = r.json()
        ep = data["endpoints"]["new_api"]
        assert ep["reachable"] is True
        assert ep["usable"] is False
        assert ep["auth_error"] is True

    def test_health_check_unreachable(self, client, auth_header, monkeypatch):
        """不可达端点返回 reachable=False + usable=False"""
        import aiohttp

        class FakeSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            def get(self, url, **kwargs):
                raise Exception("Connection refused")

        monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: FakeSession())

        r = client.post("/api/v1/admin/models/health-check", headers=auth_header)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["endpoints"]["new_api"]["reachable"] is False
        assert data["endpoints"]["new_api"]["usable"] is False
        assert "Connection refused" in data["endpoints"]["new_api"]["error"]


class TestModelRouteV2:
    def test_reply_route_params_update_and_read_back(self, client, auth_header, monkeypatch):
        from core.settings_service import settings

        def test_session_factory():
            gen = app.dependency_overrides[get_db]()
            db = next(gen)
            db._test_generator = gen
            return db

        monkeypatch.setattr(settings, "_session_factory", test_session_factory)
        settings.invalidate()

        r = client.put(
            "/api/v1/admin/models/routes/reply",
            json={
                "provider": "newapi",
                "model": "param-test-model",
                "timeout": 88,
                "temperature": 0.2,
                "max_tokens": 1234,
                "enable_thinking": "true",
            },
            headers=auth_header,
        )

        assert r.status_code == 200, r.text
        settings.invalidate()
        status = client.get("/api/v1/admin/models/status", headers=auth_header)
        assert status.status_code == 200, status.text
        reply = status.json()["routes"]["reply"]
        assert reply["model"] == "param-test-model"
        assert reply["timeout"] == 88
        assert reply["temperature"] == 0.2
        assert reply["max_tokens"] == 1234
        assert reply["enable_thinking"] == "true"

        r = client.put(
            "/api/v1/admin/models/routes/reply",
            json={"max_tokens": 0, "enable_thinking": "auto"},
            headers=auth_header,
        )

        assert r.status_code == 200, r.text
        settings.invalidate()
        status = client.get("/api/v1/admin/models/status", headers=auth_header)
        assert status.status_code == 200, status.text
        reply = status.json()["routes"]["reply"]
        assert reply["max_tokens"] == 0
        assert reply["enable_thinking"] == "auto"

    def test_sticker_describe_vision_test_sends_multimodal_payload(self, client, auth_header, monkeypatch):
        captured = {}

        def fake_call_model_route(**kwargs):
            captured.update(kwargs)
            return "ok"

        monkeypatch.setattr("clients.classifier_client.call_model_route", fake_call_model_route)

        r = client.post(
            "/api/v1/admin/models/routes/sticker_describe/test?mode=vision",
            headers=auth_header,
        )

        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["vision_payload_ok"] is True
        assert captured["route_key"] == "sticker_describe"
        user_content = captured["messages"][1]["content"]
        assert isinstance(user_content, list)
        assert user_content[0]["type"] == "text"
        assert user_content[1]["type"] == "image_url"
        assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_available_override_does_not_reuse_route_api_key(self, client, auth_header, monkeypatch):
        captured = {}

        def fake_resolve_model_route(route_key):
            return {
                "route_key": route_key,
                "provider_id": "newapi",
                "base_url": "http://provider-a:9000/v1",
                "api_key": "provider-a-key",
                "provider_enabled": True,
            }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"data":[]}'

        class FakeOpener:
            def open(self, req, timeout=10):
                captured["authorization"] = req.headers.get("Authorization")
                captured["url"] = req.full_url
                return FakeResponse()

        monkeypatch.setattr("clients.classifier_client.resolve_model_route", fake_resolve_model_route)
        monkeypatch.setattr("urllib.request.build_opener", lambda *args, **kwargs: FakeOpener())

        r = client.get(
            "/api/v1/admin/models/available?route_key=reply&base_url_override=http://provider-b:9000/v1",
            headers=auth_header,
        )

        assert r.status_code == 200, r.text
        assert captured["authorization"] is None
        assert captured["url"] == "http://provider-b:9000/v1/models"

    def test_available_rejects_disabled_provider(self, client, auth_header, monkeypatch):
        def fake_resolve_model_route(route_key):
            return {
                "route_key": route_key,
                "provider_id": "newapi",
                "base_url": "http://provider-a:9000/v1",
                "api_key": "provider-a-key",
                "provider_enabled": False,
            }

        monkeypatch.setattr("clients.classifier_client.resolve_model_route", fake_resolve_model_route)

        r = client.get(
            "/api/v1/admin/models/available?route_key=reply",
            headers=auth_header,
        )

        assert r.status_code == 200, r.text
        data = r.json()
        assert data["models"] == []
        assert data["error"] == "provider disabled: newapi"
