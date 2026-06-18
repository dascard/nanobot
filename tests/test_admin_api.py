"""Admin API 集成测试——Sticker CRUD + Block Rules + status 枚举 + 认证。"""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ChatLog, ChatStreamConfig, ConversationTurn, GroupMemory, PersonaFact, StickerMemory, User, get_db
from server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """独立 SQLite 测试库——不污染真实数据库。"""
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "test-token")
    monkeypatch.setattr("api.admin.system_routes._VERSION_CACHE", None)
    monkeypatch.setenv("NANOBOT_GIT_COMMIT", "testcommit")
    monkeypatch.setenv("NANOBOT_GIT_BRANCH", "test-branch")
    monkeypatch.setenv("NANOBOT_GIT_COMMIT_DATE", "2026-06-16T00:00:00+08:00")
    monkeypatch.setenv("NANOBOT_GIT_DIRTY", "false")
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


class TestGeneratedImagesAdmin:
    def test_list_and_image_response(self, client, auth_header, monkeypatch, tmp_path):
        import base64

        from core import generated_images

        monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))
        saved = generated_images.save_generated_image(
            base64.b64encode(b"fake-png").decode("ascii"),
            prompt="画一只红熊猫喝奶茶",
            metadata={
                "model": "gpt-image",
                "size": "1024x1024",
                "quality": "high",
                "background": "auto",
            },
        )

        r = client.get(
            "/api/v1/admin/generated-images?search=红熊猫&page=1&limit=10",
            headers=auth_header,
        )
        data = _ok(r)
        assert data["total"] == 1
        assert data["items"][0]["id"] == saved["id"]
        assert data["items"][0]["prompt"] == "画一只红熊猫喝奶茶"
        assert data["items"][0]["model"] == "gpt-image"
        assert data["items"][0]["image_url"] == (
            f"/api/v1/admin/generated-images/{saved['id']}/image"
        )

        image = client.get(
            f"/api/v1/admin/generated-images/{saved['id']}/image",
            headers=auth_header,
        )
        assert image.status_code == 200
        assert image.headers["content-type"].startswith("image/png")
        assert image.content == b"fake-png"

    def test_missing_image_response_returns_404(self, client, auth_header):
        r = client.get(
            "/api/v1/admin/generated-images/not-present/image",
            headers=auth_header,
        )
        assert r.status_code == 404

    def test_create_generated_image_response(self, client, auth_header, monkeypatch, tmp_path):
        import base64

        from kohakuterrarium.modules.tool.base import ToolResult

        from core import generated_images

        seen = {}
        monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))

        async def fake_execute(self, args):
            seen["args"] = dict(args)
            saved = generated_images.save_generated_image(
                base64.b64encode(b"fake-png").decode("ascii"),
                prompt=args["prompt"],
                metadata={
                    "model": "gpt-image",
                    "size": args["size"],
                    "quality": args["quality"],
                    "background": args["background"],
                },
            )
            return ToolResult(output=json.dumps({
                "reply_token": saved["reply_token"],
                "mime": "image/png",
                "model": "gpt-image",
                "size": args["size"],
                "quality": args["quality"],
                "background": args["background"],
            }, ensure_ascii=False), exit_code=0)

        monkeypatch.setattr(
            "creatures.nanobot.prompts.skills.image_generation.tool.ImageGenerationTool.execute",
            fake_execute,
        )

        r = client.post(
            "/api/v1/admin/generated-images",
            json={
                "prompt": "画一只红熊猫喝奶茶",
                "size": "1536x1024",
                "quality": "medium",
                "background": "transparent",
            },
            headers=auth_header,
        )
        data = _ok(r)

        assert data["ok"] is True
        assert seen["args"] == {
            "prompt": "画一只红熊猫喝奶茶",
            "size": "1536x1024",
            "quality": "medium",
            "background": "transparent",
        }
        assert data["item"]["prompt"] == "画一只红熊猫喝奶茶"
        assert data["item"]["model"] == "gpt-image"
        assert data["item"]["image_url"] == (
            f"/api/v1/admin/generated-images/{data['item']['id']}/image"
        )
        assert data["tool_output"]["reply_token"] == data["item"]["reply_token"]

        image = client.get(data["item"]["image_url"], headers=auth_header)
        assert image.status_code == 200
        assert image.content == b"fake-png"

    def test_create_generated_image_tool_failure_returns_502(self, client, auth_header, monkeypatch):
        from kohakuterrarium.modules.tool.base import ToolResult

        async def fake_execute(self, args):
            return ToolResult(error="Image generation failed: upstream unavailable")

        monkeypatch.setattr(
            "creatures.nanobot.prompts.skills.image_generation.tool.ImageGenerationTool.execute",
            fake_execute,
        )

        r = client.post(
            "/api/v1/admin/generated-images",
            json={"prompt": "画一只猫"},
            headers=auth_header,
        )

        assert r.status_code == 502
        assert "upstream unavailable" in r.text


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

    def test_near_duplicate_candidates_route_not_shadowed_by_sticker_id(self, client, auth_header):
        r = client.get(
            "/api/v1/admin/stickers/near-duplicate-candidates?limit=100",
            headers=auth_header,
        )

        assert r.status_code == 200, r.text
        assert r.json() == {"items": [], "total": 0}


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
    def test_group_memory_overview_includes_groups_without_memories(self, client, auth_header):
        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(ChatLog(
                user_id="group_7788", session_id="group_7788",
                role="ambient", content="[A]: 群里在聊模型部署",
                sender_name="A", session_name="记忆测试群",
                created_at=now,
            ))
            db.commit()

        data = _ok(client.get("/api/v1/admin/group-memories/overview", headers=auth_header))

        row = next(item for item in data["items"] if item["group_id"] == "group_7788")
        assert row["session_name"] == "记忆测试群"
        assert row["log_count"] == 1
        assert row["memory_count"] == 0
        assert row["injectable_count"] == 0

    def test_group_memory_items_endpoint_returns_memories_without_group_route_shadow(self, client, auth_header):
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(GroupMemory(
                group_id="group_7788",
                memory_type="topic",
                content="模型部署: 群里经常讨论本地模型部署",
                content_hash="hash-test",
                confidence=0.65,
                evidence_count=1,
                evidence_log_ids_json="[1, 2]",
                status="active",
                source="manual_group_memory_extract",
            ))
            db.commit()

        data = _ok(client.get(
            "/api/v1/admin/group-memories/group_7788/items",
            headers=auth_header,
        ))

        assert len(data["memories"]) == 1
        assert data["memories"][0]["content"].startswith("模型部署")
        assert data["memories"][0]["source"] == "manual_group_memory_extract"

    def test_group_memory_extract_endpoint_returns_service_stats(self, client, auth_header, monkeypatch):
        class FakeResult:
            def to_dict(self):
                return {
                    "ok": True,
                    "group_id": "group_7788",
                    "group_name": "记忆测试群",
                    "window_hours": 24,
                    "raw_count": 3,
                    "eligible_count": 3,
                    "deduped_count": 3,
                    "message_count": 3,
                    "source_log_count": 3,
                    "stats": {"new": 1, "updated": 0, "skipped": 0},
                    "memory_count": 1,
                    "active_count": 1,
                    "injectable_count": 1,
                }

        async def fake_extract(db, group_id, *, window_hours=24, instructions=""):
            assert group_id == "group_7788"
            assert window_hours == 24
            assert instructions == "只提取稳定事实"
            return FakeResult()

        monkeypatch.setattr(
            "app.group_memory.extraction_service.extract_group_memories",
            fake_extract,
        )

        data = _ok(client.post(
            "/api/v1/admin/groups/group_7788/memories/extract",
            json={"window_hours": 24, "instructions": "只提取稳定事实"},
            headers=auth_header,
        ))

        assert data["ok"] is True
        assert data["stats"]["new"] == 1
        assert data["injectable_count"] == 1

    def test_group_memory_extract_alias_avoids_group_detail_shadow(self, client, auth_header, monkeypatch):
        class FakeResult:
            def to_dict(self):
                return {"ok": True, "group_id": "group_7788", "stats": {"new": 1}}

        async def fake_extract(db, group_id, *, window_hours=24, instructions=""):
            assert group_id == "group_7788"
            return FakeResult()

        monkeypatch.setattr(
            "app.group_memory.extraction_service.extract_group_memories",
            fake_extract,
        )

        data = _ok(client.post(
            "/api/v1/admin/group-memories/group_7788/extract",
            json={"window_hours": 24, "instructions": ""},
            headers=auth_header,
        ))

        assert data["ok"] is True
        assert data["stats"]["new"] == 1

    def test_group_memory_injection_config_writes_canonical_stream_id(self, client, auth_header):
        data = _ok(client.put(
            "/api/v1/admin/group-memories/group_7788/injection-config",
            json={"group_profile_mode": "on"},
            headers=auth_header,
        ))

        assert data["group_profile_mode"] == "on"
        assert data["chat_stream_id"] == "qq:7788:group"
        with next(app.dependency_overrides[get_db]()) as db:
            row = db.query(ChatStreamConfig).filter(
                ChatStreamConfig.chat_stream_id == "qq:7788:group"
            ).first()
            assert row is not None
            assert row.group_profile_mode == "on"

    def test_group_memory_injection_preview_returns_selected_and_skipped(self, client, auth_header):
        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(ChatStreamConfig(chat_stream_id="qq:7788:group", group_profile_mode="on"))
            db.add(GroupMemory(
                group_id="group_7788",
                memory_type="topic",
                content="模型部署: 群里经常讨论本地模型部署",
                content_hash="preview-topic",
                confidence=0.8,
                evidence_count=2,
                evidence_log_ids_json="[1, 2]",
                decay_score=1.0,
                status="active",
                inject_policy="auto",
                last_seen=now,
            ))
            db.add(GroupMemory(
                group_id="group_7788",
                memory_type="relationship",
                content="Alice 和 Bob 经常互相开玩笑",
                content_hash="preview-manual",
                confidence=0.9,
                evidence_count=3,
                evidence_log_ids_json="[3, 4, 5]",
                decay_score=1.0,
                status="active",
                inject_policy="manual_only",
                last_seen=now,
            ))
            db.commit()

        data = _ok(client.post(
            "/api/v1/admin/group-memories/7788/injection-preview",
            json={"user_input": "本地模型部署怎么弄？"},
            headers=auth_header,
        ))

        assert data["group_profile_mode"] == "on"
        assert data["group_memory_ids"] == [1]
        assert "<group_memory_context" in data["group_memory_context"]
        assert data["group_memory_skipped"][0]["reason"] == "manual_only"

    def test_group_memory_update_item_changes_status_policy_and_content(self, client, auth_header):
        from core.group_memory import _cluster_key

        with next(app.dependency_overrides[get_db]()) as db:
            db.add(GroupMemory(
                group_id="group_7788",
                memory_type="topic",
                content="旧内容",
                content_hash="old-content",
                cluster_key="旧内容",
                confidence=0.8,
                evidence_count=2,
                evidence_log_ids_json="[1, 2]",
                status="active",
                inject_policy="auto",
            ))
            db.commit()

        data = _ok(client.patch(
            "/api/v1/admin/group-memories/items/1",
            json={
                "content": "新内容",
                "status": "disabled",
                "inject_policy": "never",
                "disabled_reason": "人工确认污染",
            },
            headers=auth_header,
        ))

        memory = data["memory"]
        assert memory["content"] == "新内容"
        assert memory["status"] == "disabled"
        assert memory["inject_policy"] == "never"
        assert memory["disabled_reason"] == "人工确认污染"
        assert memory["cluster_key"] == _cluster_key("新内容")

    def test_group_memory_update_item_duplicate_content_returns_409(self, client, auth_header):
        from core.group_memory import _content_hash

        with next(app.dependency_overrides[get_db]()) as db:
            db.add(GroupMemory(
                group_id="group_7788",
                memory_type="topic",
                content="第一条",
                content_hash=_content_hash("第一条"),
                confidence=0.8,
                evidence_count=2,
                evidence_log_ids_json="[1, 2]",
                status="active",
                inject_policy="auto",
            ))
            db.add(GroupMemory(
                group_id="group_7788",
                memory_type="topic",
                content="重复内容",
                content_hash=_content_hash("重复内容"),
                confidence=0.8,
                evidence_count=2,
                evidence_log_ids_json="[3, 4]",
                status="active",
                inject_policy="auto",
            ))
            db.commit()

        r = client.patch(
            "/api/v1/admin/group-memories/items/1",
            json={"content": "重复内容"},
            headers=auth_header,
        )

        assert r.status_code == 409
        assert "已有相同记忆" in r.text


class TestPersonaAdmin:
    def test_persona_users_facts_and_injection_preview(self, client, auth_header):
        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(User(id="u-persona", name="画像用户"))
            db.add(PersonaFact(
                user_id="u-persona",
                content="用户偏好先给结论，再给必要步骤",
                domain_primary="协作方式",
                confidence="确认",
                fact_type="preference",
                memory_type="stable_preference",
                status="active",
                inject_policy="auto",
                content_hash="persona-hash-1",
                evidence_count=3,
                evidence_log_ids_json="[1, 2, 3]",
                first_seen=now,
                last_seen=now,
            ))
            db.add(PersonaFact(
                user_id="u-persona",
                content="用户当前在临时调试脚本",
                domain_primary="临时任务",
                confidence="可能",
                fact_type="preference",
                memory_type="stable_preference",
                status="review",
                inject_policy="manual_only",
                content_hash="persona-hash-2",
                evidence_count=1,
                evidence_log_ids_json="[4]",
                first_seen=now,
                last_seen=now,
            ))
            db.commit()

        users = _ok(client.get("/api/v1/admin/persona/users?q=u-persona", headers=auth_header))
        assert users["items"][0]["user_id"] == "u-persona"
        assert users["items"][0]["injectable_count"] == 1

        facts = _ok(client.get("/api/v1/admin/persona/users/u-persona/facts", headers=auth_header))
        assert facts["total"] == 2
        assert facts["items"][0]["memory_type"] == "stable_preference"

        preview = _ok(client.post(
            "/api/v1/admin/persona/users/u-persona/injection-preview",
            json={"user_input": "请先给结论"},
            headers=auth_header,
        ))
        assert preview["persona_fact_ids"] == [1]
        assert "<persona_profile" in preview["persona_context"]
        assert preview["persona_skipped"][0]["reason"] == "not_active_auto"
        with next(app.dependency_overrides[get_db]()) as db:
            row = db.query(PersonaFact).filter(PersonaFact.id == 1).one()
            assert row.injected_count == 0
            assert row.last_injected_at is None

    def test_persona_extract_returns_502_when_llm_content_is_empty(self, client, auth_header, monkeypatch):
        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(User(id="u-persona", name="画像用户"))
            db.add(ChatLog(
                user_id="u-persona",
                session_id="private_u-persona",
                role="user",
                content="以后回答先给结论",
                created_at=now,
            ))
            db.commit()

        async def fake_chat_completion(self, **kwargs):
            return {"choices": [{"message": {"content": None}}]}

        monkeypatch.setattr(
            "clients.new_api_client.NewAPIClient.chat_completion",
            fake_chat_completion,
        )

        r = client.post(
            "/api/v1/admin/persona/users/u-persona/extract",
            json={"window_hours": 168, "limit": 10},
            headers=auth_header,
        )

        assert r.status_code == 502
        assert "空内容" in r.text

    def test_persona_update_fact_rejects_duplicate_content(self, client, auth_header):
        from core.persona_preprocess import content_hash

        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add_all([
                PersonaFact(
                    user_id="u-persona",
                    content="用户偏好短回复",
                    memory_type="stable_preference",
                    content_hash=content_hash("用户偏好短回复"),
                    confidence="确认",
                    evidence_count=3,
                    status="active",
                    inject_policy="auto",
                    first_seen=now,
                    last_seen=now,
                ),
                PersonaFact(
                    user_id="u-persona",
                    content="用户偏好详细回复",
                    memory_type="stable_preference",
                    content_hash=content_hash("用户偏好详细回复"),
                    confidence="确认",
                    evidence_count=3,
                    status="active",
                    inject_policy="auto",
                    first_seen=now,
                    last_seen=now,
                ),
            ])
            db.commit()

        ok = _ok(client.patch(
            "/api/v1/admin/persona/facts/1",
            json={"status": "disabled", "inject_policy": "never", "disabled_reason": "测试禁用"},
            headers=auth_header,
        ))
        assert ok["fact"]["status"] == "disabled"
        assert ok["fact"]["inject_policy"] == "never"

        dup = client.patch(
            "/api/v1/admin/persona/facts/2",
            json={"content": "用户偏好短回复"},
            headers=auth_header,
        )
        assert dup.status_code == 409

    def test_persona_update_fact_rejects_duplicate_when_memory_type_changes(self, client, auth_header):
        from core.persona_preprocess import content_hash

        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add_all([
                PersonaFact(
                    user_id="u-persona",
                    content="用户偏好先给结论",
                    memory_type="stable_preference",
                    content_hash=content_hash("用户偏好先给结论"),
                    confidence="确认",
                    evidence_count=3,
                    status="active",
                    inject_policy="auto",
                    first_seen=now,
                    last_seen=now,
                ),
                PersonaFact(
                    user_id="u-persona",
                    content="用户偏好先给结论",
                    memory_type="interaction_style",
                    content_hash=content_hash("用户偏好先给结论"),
                    confidence="确认",
                    evidence_count=3,
                    status="active",
                    inject_policy="auto",
                    first_seen=now,
                    last_seen=now,
                ),
            ])
            db.commit()

        dup = client.patch(
            "/api/v1/admin/persona/facts/2",
            json={"memory_type": "stable_preference"},
            headers=auth_header,
        )
        assert dup.status_code == 409

    def test_persona_update_fact_rejects_unknown_memory_type(self, client, auth_header):
        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(PersonaFact(
                user_id="u-persona",
                content="用户偏好先给结论",
                memory_type="stable_preference",
                content_hash="known-type",
                confidence="确认",
                evidence_count=3,
                status="active",
                inject_policy="auto",
                first_seen=now,
                last_seen=now,
            ))
            db.commit()

        r = client.patch(
            "/api/v1/admin/persona/facts/1",
            json={"memory_type": "temporary_task"},
            headers=auth_header,
        )
        assert r.status_code == 422

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

    def test_timing_gate_events_returns_scoring(self, client, auth_header):
        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(ChatLog(
                user_id="group_3004", session_id="group_3004",
                role="ambient", content="[D]: @B 你看", session_name="评分群",
                created_at=now,
                meta_json=json.dumps({
                    "timing_gate": {
                        "mode": "message",
                        "action": "no_reply",
                        "reason": "directed_to_other_no_bot_target",
                        "hard_rule": "directed_to_other_no_bot_target",
                        "scoring": {
                            "stage": "rule_shortcut",
                            "action": "no_reply",
                            "signals": {"sub_signals": {"s_other": 0.75}},
                        },
                    }
                }, ensure_ascii=False),
            ))
            db.commit()

        data = _ok(client.get("/api/v1/admin/timing-gate/events", headers=auth_header))
        item = data["items"][0]
        assert item["action"] == "no_reply"
        assert item["hard_rule"] == "directed_to_other_no_bot_target"
        assert item["scoring"]["stage"] == "rule_shortcut"
        assert item["scoring"]["signals"]["sub_signals"]["s_other"] == 0.75

    def test_timing_gate_test_route_is_async(self):
        import inspect
        from api.admin_routes import timing_gate_test

        assert inspect.iscoroutinefunction(timing_gate_test)

    def test_timing_gate_test_repeats_is_capped_to_five(self):
        from pydantic import ValidationError
        from api.admin_routes import TimingGateTestRequest

        with pytest.raises(ValidationError):
            TimingGateTestRequest(context="测试", repeats=6)


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

    def test_tool_schema_override_updates_effective_tools(self, client, auth_header):
        schema = {
            "type": "function",
            "function": {
                "name": "reply",
                "description": "Web 可配置 reply schema",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "tone": {"type": "string", "enum": ["plain", "warm"]},
                    },
                    "required": ["content"],
                },
            },
        }

        saved = client.put(
            "/api/v1/admin/tools/reply/schema",
            json={"schema": schema},
            headers=auth_header,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["override_present"] is True

        effective = client.get(
            "/api/v1/admin/tools/effective",
            params={"chat_type": "private", "runtime_preset": "full"},
            headers=auth_header,
        )
        assert effective.status_code == 200, effective.text
        schemas = effective.json()["tool_schemas"]
        reply_schema = next(item for item in schemas if item["function"]["name"] == "reply")
        assert "tone" in reply_schema["function"]["parameters"]["properties"]
        assert reply_schema["source"] == "runtime_override"

        reset = client.delete("/api/v1/admin/tools/reply/schema", headers=auth_header)
        assert reset.status_code == 200, reset.text

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

    def test_tool_platform_override_can_be_created_and_previewed(self, client, auth_header):
        r = client.put(
            "/api/v1/admin/tools/image_generation/override",
            json={
                "scope_type": "platform",
                "scope_id": "web",
                "enabled": False,
                "reason": "Web 禁用图片生成",
            },
            headers=auth_header,
        )
        assert r.status_code == 200, r.text

        effective = client.get(
            "/api/v1/admin/tools/effective",
            params={"chat_type": "private", "platform": "web"},
            headers=auth_header,
        )
        assert effective.status_code == 200, effective.text
        data = effective.json()
        assert data["platform"] == "web"
        assert data["enabled"].get("image_generation") is None
        assert data["disabled"]["image_generation"] == "Web 禁用图片生成"

    def test_tools_list_reports_platform_override_and_targets(self, client, auth_header):
        _ok(client.put(
            "/api/v1/admin/tools/image_generation/override",
            json={
                "scope_type": "platform",
                "scope_id": "web",
                "enabled": False,
                "reason": "Web 禁用图片生成",
            },
            headers=auth_header,
        ))

        tools = client.get(
            "/api/v1/admin/tools",
            params={"platform": "web"},
            headers=auth_header,
        )
        assert tools.status_code == 200, tools.text
        data = tools.json()
        assert data["platform"] == "web"
        item = next(x for x in data["tools"] if x["name"] == "image_generation")
        assert item["override_present"] is True
        assert item["override_enabled"] is False
        assert item["runtime_effective"] is False

        targets = client.get(
            "/api/v1/admin/tools/targets",
            params={"scope_type": "platform"},
            headers=auth_header,
        )
        target_ids = {item["id"] for item in targets.json()["items"]}
        assert {"qq", "web", "synergy"}.issubset(target_ids)

    def test_tool_targets_list_real_groups_and_users(self, client, auth_header):
        now = datetime.now()
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(User(id="group_2002", name="真实群"))
            db.add(User(id="group_test", name="测试群"))
            db.add(User(id="0000000000", name="雀"))
            db.add(User(id="local_test", name="本地测试"))
            db.add(User(id="private_0000000000", name="临时私聊 session"))
            db.add(User(id="admin", name="管理测试"))
            db.add(ChatStreamConfig(chat_stream_id="qq:4004:group"))
            db.add(ChatLog(
                user_id="30001", session_id="group_3003",
                role="ambient", content="hello", sender_name="A",
                session_name="日志群", created_at=now,
            ))
            db.add(ConversationTurn(
                user_id="8888", session_id="private_8888",
                role="user", content="hi", created_at=now,
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
        assert {"2002", "3003", "4004"} <= group_ids
        assert "test" not in group_ids
        assert all(not item["id"].startswith("group_") for item in groups["items"])

        users = _ok(client.get(
            "/api/v1/admin/tools/targets",
            params={"scope_type": "user", "search": "雀"},
            headers=auth_header,
        ))
        assert [item["id"] for item in users["items"]] == ["0000000000"]
        assert users["items"][0]["label"] == "雀 (0000000000)"

        all_users = _ok(client.get(
            "/api/v1/admin/tools/targets",
            params={"scope_type": "user"},
            headers=auth_header,
        ))
        user_ids = {item["id"] for item in all_users["items"]}
        assert "8888" in user_ids
        assert "30001" not in user_ids

    def test_tool_decisions_returns_platform(self, client, auth_header):
        from core.runtime_tool_service import record_runtime_tool_decision

        with next(app.dependency_overrides[get_db]()) as db:
            record_runtime_tool_decision(
                session_id="s-platform",
                message_id="m1",
                chat_type="private",
                platform="web",
                runtime_preset="full",
                enabled={"reply": True},
                disabled={},
                effective_tools=["reply"],
                db=db,
            )
            db.commit()

        r = client.get("/api/v1/admin/tools/decisions", headers=auth_header)
        assert r.status_code == 200, r.text
        item = next(x for x in r.json()["items"] if x["session_id"] == "s-platform")
        assert item["platform"] == "web"


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
    def test_models_status_includes_local_rag_reranker_component(self, client, auth_header, monkeypatch):
        from core.settings_service import settings

        monkeypatch.delenv("RAG_LOCAL_RERANKER_MODEL", raising=False)
        monkeypatch.delenv("RAG_RERANKER_HF_MODEL", raising=False)
        settings.invalidate()

        r = client.get("/api/v1/admin/models/status", headers=auth_header)

        assert r.status_code == 200, r.text
        local_components = r.json()["local_components"]
        assert "rag_reranker" in local_components
        assert local_components["rag_reranker"]["loader"] == "sentence-transformers CrossEncoder"
        assert local_components["rag_reranker"]["model_path"] == "./models/bge-reranker-v2-m3"
        assert local_components["rag_reranker"]["download_repo_id"] == "BAAI/bge-reranker-v2-m3"

    def test_models_status_adds_local_llama_provider_from_classifier_url(self, client, auth_header, monkeypatch):
        monkeypatch.setattr("config.CLASSIFIER_API_URL", "http://local-classifier:9999/v1")
        monkeypatch.setattr(
            "clients.classifier_client.list_providers",
            lambda: [{
                "id": "newapi",
                "base_url": "http://new-api:9000/v1",
                "api_key": "new-api-key",
                "enabled": True,
            }],
        )

        r = client.get("/api/v1/admin/models/status", headers=auth_header)

        assert r.status_code == 200, r.text
        providers = {item["id"]: item for item in r.json()["providers"]}
        assert providers["local_llama"]["base_url"] == "http://local-classifier:9999/v1"
        assert providers["local_llama"]["enabled"] is True

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

    def test_local_rag_reranker_test_uses_local_provider(self, client, auth_header, monkeypatch):
        from core.semantic.reranker import RerankResult

        class FakeRerankerProvider:
            model_name = "bge-reranker-v2-m3"

            def rerank(self, query, candidates, *, top_k=None):
                return [
                    RerankResult(
                        candidate_id=candidates[0].candidate_id,
                        raw_score=0.9,
                        score=0.9,
                        model=self.model_name,
                        score_mode="identity",
                    )
                ]

        monkeypatch.setattr("core.semantic.provider_factory.get_reranker_provider", lambda: FakeRerankerProvider())

        r = client.post("/api/v1/admin/models/local/rag_reranker/test", headers=auth_header)

        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["component"] == "rag_reranker"
        assert data["model"] == "bge-reranker-v2-m3"
        assert data["best_candidate_id"] == "test:1"
        assert data["best_score"] == 0.9

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
