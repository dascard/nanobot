import json
from datetime import datetime, timedelta

from core.database import (
    ConversationTurn,
    MemoryDigest,
    RollingSessionSummary,
    SessionSummaryJob,
)


def _auth_header():
    return {"Authorization": "Bearer test-token"}


# SQLite ORM DateTime 列当前使用 naive 本地墙钟时间；这些测试 fixture 保持同一语义。
def _db_time(year: int, month: int, day: int, hour: int, minute: int, second: int) -> datetime:
    return datetime(year, month, day, hour, minute, second)  # noqa: DTZ001


def test_admin_session_summary_retry_rejects_non_failed_job(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    job = SessionSummaryJob(
        session_id="retry-conflict-session",
        status="obsolete",
        meta_json=json.dumps({
            "obsolete": {
                "blocking_summary_id": 88,
                "blocking_coverage": 120,
                "proposed_coverage": 80,
                "reason": "higher_active_coverage",
            },
            "private_text": "不得进入管理响应",
        }, ensure_ascii=False),
    )
    db_session.add(job)
    db_session.commit()

    response = client.post(
        f"/api/v1/admin/session-memory/jobs/{job.id}/retry",
        headers=_auth_header(),
    )

    assert response.status_code == 409
    assert "不得进入管理响应" not in response.text
    db_session.refresh(job)
    assert job.status == "obsolete"


def test_admin_session_summary_get_redacts_obsolete_meta(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    job = SessionSummaryJob(
        session_id="obsolete-redaction-session",
        status="obsolete",
        meta_json=json.dumps({
            "obsolete": {
                "blocking_summary_id": 88,
                "blocking_coverage": 120,
                "proposed_coverage": 80,
                "reason": "higher_active_coverage",
                "private_nested_text": "不得进入管理响应",
            },
            "private_text": "也不得进入管理响应",
        }, ensure_ascii=False),
    )
    db_session.add(job)
    db_session.commit()

    response = client.get(
        "/api/v1/admin/session-memory/obsolete-redaction-session/rolling-summary",
        headers=_auth_header(),
    )

    assert response.status_code == 200, response.text
    returned = response.json()["jobs"][0]
    assert returned["obsolete"] == {
        "blocking_summary_id": 88,
        "blocking_coverage": 120,
        "proposed_coverage": 80,
        "reason": "higher_active_coverage",
    }
    assert "meta_json" not in returned
    assert "不得进入管理响应" not in response.text


def test_admin_archive_enqueues_session_summary_semantic_delete(
    client,
    db_session,
    monkeypatch,
):
    from core.database import SemanticIndexJob

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    summary = RollingSessionSummary(
        session_id="admin-archive-index-session",
        user_id="u1",
        status="active",
        summary_kind="llm_episode",
        summary_text="待归档摘要",
        stable_hash="admin-archive-index-revision",
    )
    db_session.add(summary)
    db_session.commit()

    response = client.post(
        "/api/v1/admin/session-memory/"
        "admin-archive-index-session/rolling-summary/archive",
        headers=_auth_header(),
    )

    assert response.status_code == 200, response.text
    db_session.refresh(summary)
    job = db_session.query(SemanticIndexJob).one()
    assert summary.status == "archived"
    assert job.source_type == "session_summary"
    assert job.source_id == summary.session_id
    assert job.job_type == "delete"
    job_meta = json.loads(job.meta_json)
    assert job_meta["job_origin"] == "business"
    assert str(summary.id) in job_meta["delete_source_ids"]


def test_admin_archive_rolls_back_when_semantic_enqueue_fails(
    client,
    db_session,
    monkeypatch,
):
    from core.database import SemanticIndexJob

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(client._transport, "raise_server_exceptions", False)
    summary = RollingSessionSummary(
        session_id="admin-archive-rollback",
        user_id="u1",
        status="active",
        summary_kind="llm_episode",
        summary_text="归档失败后必须保持 active",
    )
    db_session.add(summary)
    db_session.commit()
    rollback_calls = []
    original_rollback = db_session.rollback

    def recording_rollback():
        rollback_calls.append(True)
        return original_rollback()

    monkeypatch.setattr(db_session, "rollback", recording_rollback)
    monkeypatch.setattr(
        "core.semantic.jobs.enqueue_index_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("semantic enqueue failed")
        ),
    )

    response = client.post(
        "/api/v1/admin/session-memory/"
        "admin-archive-rollback/rolling-summary/archive",
        headers=_auth_header(),
    )

    assert response.status_code == 500
    assert rollback_calls == [True]
    db_session.expire_all()
    assert db_session.get(RollingSessionSummary, summary.id).status == "active"
    assert db_session.query(SemanticIndexJob).count() == 0


def test_admin_session_summary_retry_accepts_failed_job(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    job = SessionSummaryJob(
        session_id="retry-failed-session",
        status="failed",
        error="json_parse_failed",
        retry_count=3,
    )
    db_session.add(job)
    db_session.commit()

    response = client.post(
        f"/api/v1/admin/session-memory/jobs/{job.id}/retry",
        headers=_auth_header(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["job"]["status"] == "pending"
    db_session.refresh(job)
    assert job.status == "pending"
    assert job.retry_count == 3


def test_admin_session_memory_sessions_list_returns_session_summaries(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = _db_time(2026, 5, 28, 12, 0, 0)
    db_session.add_all([
        RollingSessionSummary(
            id=1,
            session_id="group_1",
            user_id="group_1",
            chat_type="group",
            status="archived",
            summary_text="旧近期摘要",
            covered_from_turn_id=1,
            covered_until_turn_id=10,
            quality_score=0.4,
            llm_status="fallback",
            created_at=now - timedelta(hours=3),
            updated_at=now - timedelta(hours=3),
        ),
        RollingSessionSummary(
            id=2,
            session_id="group_1",
            user_id="group_1",
            chat_type="group",
            status="active",
            summary_text="活跃近期摘要，覆盖最新对话",
            covered_from_turn_id=11,
            covered_until_turn_id=25,
            quality_score=0.92,
            llm_status="success",
            created_at=now - timedelta(hours=1),
            updated_at=now,
        ),
        MemoryDigest(
            id=20,
            session_id="group_1",
            user_id="group_1",
            digest_date="2026-05-28",
            level=2,
            content="长期摘要",
            source_start_log_id=100,
            source_end_log_id=160,
            meta_json=json.dumps({"status": "active"}, ensure_ascii=False),
            created_at=now,
        ),
    ])
    db_session.commit()

    response = client.get(
        "/api/v1/admin/session-memory/sessions",
        headers=_auth_header(),
        params={"session_limit": 10},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    item = payload["items"][0]
    assert item["session_id"] == "group_1"
    assert item["summary_count"] == 2
    assert item["digest_count"] == 1
    assert item["active_summary_id"] == 2
    assert item["active_summary_preview"] == "活跃近期摘要，覆盖最新对话"
    assert item["latest_turn_index"] == 25
    assert item["oldest_turn_index"] == 1
    assert item["has_archived"] is True
    assert item["llm_status"] == "success"
    assert item["quality_score"] == 0.92


def test_admin_session_memory_summary_and_digest_details_are_per_session(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = _db_time(2026, 5, 28, 12, 0, 0)
    db_session.add(RollingSessionSummary(
        id=3,
        session_id="private_1",
        user_id="u1",
        chat_type="private",
        status="active",
        summary_kind="llm_episode",
        summary_text="完整近期摘要内容",
        summary_json=json.dumps({"topics": ["RAG"]}, ensure_ascii=False),
        covered_from_turn_id=5,
        covered_until_turn_id=18,
        quality_score=0.88,
        llm_status="success",
        created_at=now,
        updated_at=now,
    ))
    db_session.add(MemoryDigest(
        id=30,
        session_id="private_1",
        user_id="u1",
        digest_date="2026-05-27",
        level=1,
        parent_id=None,
        content="完整长期摘要内容",
        source_start_log_id=200,
        source_end_log_id=260,
        meta_json=json.dumps({"status": "active", "preview": {"text": "长期预览"}}, ensure_ascii=False),
        created_at=now,
    ))
    db_session.commit()

    summary_preview = client.get(
        "/api/v1/admin/session-memory/sessions/private_1/summaries",
        headers=_auth_header(),
        params={"summary_limit_per_session": 5},
    )
    summary_full = client.get(
        "/api/v1/admin/session-memory/sessions/private_1/summaries",
        headers=_auth_header(),
        params={"include_content": "true"},
    )
    digests = client.get(
        "/api/v1/admin/session-memory/sessions/private_1/digests",
        headers=_auth_header(),
        params={"include_content": "true"},
    )

    assert summary_preview.status_code == 200, summary_preview.text
    summary_item = summary_preview.json()["items"][0]
    assert summary_item["summary_id"] == 3
    assert summary_item["turn_start"] == 5
    assert summary_item["turn_end"] == 18
    assert summary_item["is_active"] is True
    assert summary_item["is_archived"] is False
    assert "content" not in summary_item

    assert summary_full.json()["items"][0]["content"] == "完整近期摘要内容"
    digest_item = digests.json()["items"][0]
    assert digest_item["digest_id"] == 30
    assert digest_item["source_start_log_id"] == 200
    assert digest_item["source_end_log_id"] == 260
    assert digest_item["content"] == "完整长期摘要内容"


def test_admin_session_memory_digest_details_expose_generation_metadata(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = _db_time(2026, 5, 28, 12, 0, 0)
    meta = {
        "schema_version": 2,
        "status": "active",
        "source_id": "src-20260528-group1",
        "source_type": "date_session",
        "source_range": "log_id 10-30",
        "summary_type": "recall_card",
        "generator": "llm",
        "quality": {"score": 0.91},
        "prompt_template": "tasks/memory_digest_system + tasks/memory_digest_user",
        "prompt_version": {"system_sha256": "abc"},
        "fallback_reason": None,
        "recall_card_count": 3,
        "message_count": 18,
        "recall_cards": [{"text": "memory_digests 的 level 2 是 RAG 主召回层。"}],
    }
    db_session.add(MemoryDigest(
        id=31,
        session_id="group_1",
        user_id="group_1",
        digest_date="2026-05-28",
        level=2,
        parent_id=30,
        content="[card] memory_digests：level 2 是 RAG 主召回层。",
        source_start_log_id=10,
        source_end_log_id=30,
        meta_json=json.dumps(meta, ensure_ascii=False),
        created_at=now,
    ))
    db_session.commit()

    response = client.get(
        "/api/v1/admin/session-memory/sessions/group_1/digests",
        headers=_auth_header(),
        params={"include_content": "true"},
    )

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["source_id"] == "src-20260528-group1"
    assert item["source_type"] == "date_session"
    assert item["source_range"] == "log_id 10-30"
    assert item["summary_type"] == "recall_card"
    assert item["generator"] == "llm"
    assert item["quality_score"] == 0.91
    assert item["prompt_template"] == "tasks/memory_digest_system + tasks/memory_digest_user"
    assert item["prompt_version"] == {"system_sha256": "abc"}
    assert item["fallback_reason"] is None
    assert item["recall_card_count"] == 3
    assert item["message_count"] == 18


def test_admin_session_memory_sessions_prefer_level1_digest_preview_over_latest_card(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = _db_time(2026, 5, 28, 12, 0, 0)
    shared = {
        "schema_version": 2,
        "status": "active",
        "source_id": "src-1",
        "generator": "llm",
    }
    db_session.add_all([
        MemoryDigest(
            id=50,
            session_id="group_1",
            user_id="group_1",
            digest_date="2026-05-28",
            level=1,
            content="WebUI 应展示这条 source 级预览摘要。",
            meta_json=json.dumps({**shared, "summary_type": "preview_digest"}, ensure_ascii=False),
            created_at=now,
        ),
        MemoryDigest(
            id=51,
            session_id="group_1",
            user_id="group_1",
            digest_date="2026-05-28",
            level=2,
            parent_id=50,
            content="[card] 这是更晚写入的召回卡片，不应覆盖列表预览。",
            meta_json=json.dumps({**shared, "summary_type": "recall_card"}, ensure_ascii=False),
            created_at=now,
        ),
    ])
    db_session.commit()

    response = client.get(
        "/api/v1/admin/session-memory/sessions",
        headers=_auth_header(),
        params={"session_limit": 10, "kind": "long"},
    )

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["latest_digest_id"] == 50
    assert item["latest_digest_preview"] == "WebUI 应展示这条 source 级预览摘要。"


def test_admin_session_memory_sessions_normalize_group_aliases_and_filter_system_rows(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = _db_time(2026, 5, 28, 12, 0, 0)
    db_session.add_all([
        RollingSessionSummary(
            id=10,
            session_id="group_42",
            user_id="group_42",
            chat_type="group",
            status="active",
            summary_text="群 42 的近期摘要",
            covered_from_turn_id=3,
            covered_until_turn_id=12,
            created_at=now,
            updated_at=now,
        ),
        MemoryDigest(
            id=40,
            session_id="42",
            user_id="group_42",
            digest_date="2026-05-28",
            level=2,
            content="旧裸群号长期摘要",
            meta_json=json.dumps({
                "schema_version": 2,
                "status": "active",
                "preview": {"brief": "旧裸群号摘要预览", "keywords": ["群号归一"]},
            }, ensure_ascii=False),
            created_at=now - timedelta(minutes=5),
        ),
        MemoryDigest(
            id=41,
            session_id="private_smoke",
            user_id="smoke",
            digest_date="2026-05-28",
            level=2,
            content="烟测摘要",
            meta_json=json.dumps({"status": "active"}, ensure_ascii=False),
            created_at=now,
        ),
    ])
    db_session.commit()

    response = client.get(
        "/api/v1/admin/session-memory/sessions",
        headers=_auth_header(),
        params={"session_limit": 10},
    )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["session_id"] for item in items] == ["group_42"]
    item = items[0]
    assert item["chat_type"] == "group"
    assert item["summary_count"] == 1
    assert item["digest_count"] == 1
    assert item["session_aliases"] == ["42", "group_42"]
    assert item["latest_digest_preview"] == "旧裸群号摘要预览 关键词：群号归一"

    include_system = client.get(
        "/api/v1/admin/session-memory/sessions",
        headers=_auth_header(),
        params={"include_system_sessions": "true", "session_limit": 10},
    )
    assert {item["session_id"] for item in include_system.json()["items"]} == {"group_42", "private_smoke"}


def test_admin_session_memory_sessions_kind_filters_current_tab(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = _db_time(2026, 5, 28, 12, 0, 0)
    db_session.add(RollingSessionSummary(
        id=50,
        session_id="private_recent",
        user_id="recent",
        chat_type="private",
        status="active",
        summary_text="只有近期摘要",
        created_at=now,
        updated_at=now,
    ))
    db_session.add(MemoryDigest(
        id=51,
        session_id="private_long",
        user_id="long",
        digest_date="2026-05-28",
        level=1,
        content="只有长期摘要",
        meta_json=json.dumps({"status": "active", "preview": {"brief": "长期预览"}}, ensure_ascii=False),
        created_at=now,
    ))
    db_session.commit()

    recent = client.get(
        "/api/v1/admin/session-memory/sessions",
        headers=_auth_header(),
        params={"kind": "recent"},
    )
    long = client.get(
        "/api/v1/admin/session-memory/sessions",
        headers=_auth_header(),
        params={"kind": "long"},
    )

    assert [item["session_id"] for item in recent.json()["items"]] == ["private_recent"]
    assert [item["session_id"] for item in long.json()["items"]] == ["private_long"]


def test_admin_session_memory_recent_sessions_include_conversation_turn_only_groups(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = _db_time(2026, 6, 1, 12, 0, 0)
    db_session.add_all([
        ConversationTurn(
            session_id="group_1",
            user_id="group_1",
            role="user",
            content="[用户名]甲\n[发言内容]今晚讨论摘要重生成",
            meta_json=json.dumps({"kind": "chat", "source": "group_message"}, ensure_ascii=False),
            created_at=now - timedelta(minutes=2),
        ),
        ConversationTurn(
            session_id="group_1",
            user_id="group_1",
            role="assistant",
            content="收到，我会检查摘要链路。",
            meta_json=json.dumps({"kind": "chat"}, ensure_ascii=False),
            created_at=now - timedelta(minutes=1),
        ),
    ])
    db_session.commit()

    response = client.get(
        "/api/v1/admin/session-memory/sessions",
        headers=_auth_header(),
        params={"kind": "recent"},
    )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["session_id"] for item in items] == ["group_1"]
    item = items[0]
    assert item["chat_type"] == "group"
    assert item["summary_count"] == 0
    assert item["turn_count"] == 2
    assert item["oldest_turn_index"] > 0
    assert item["latest_turn_index"] > 0


def test_admin_session_memory_sessions_sql_paginates_beyond_scan_window(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = _db_time(2026, 5, 28, 12, 0, 0)
    db_session.bulk_save_objects([
        RollingSessionSummary(
            session_id=f"private_{i:04d}",
            user_id=f"user_{i:04d}",
            chat_type="private",
            status="active",
            summary_text=f"摘要 {i}",
            covered_from_turn_id=i * 10 + 1,
            covered_until_turn_id=i * 10 + 9,
            created_at=now - timedelta(seconds=i),
            updated_at=now - timedelta(seconds=i),
        )
        for i in range(5050)
    ])
    db_session.commit()

    response = client.get(
        "/api/v1/admin/session-memory/sessions",
        headers=_auth_header(),
        params={
            "session_limit": 25,
            "cursor": "5000",
            "include_system_sessions": "true",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 5050
    assert len(payload["items"]) == 25
    assert payload["items"][0]["session_id"] == "private_5000"
    assert payload["items"][-1]["session_id"] == "private_5024"


def test_admin_session_memory_digest_details_search_group_aliases_and_render_v2_content(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = _db_time(2026, 5, 28, 12, 0, 0)
    meta = {
        "schema_version": 2,
        "status": "active",
        "digest_date": "2026-05-28",
        "session_id": "group_42",
        "preview": {
            "brief": "群聊围绕摘要浏览修复展开。",
            "keywords": ["摘要", "浏览"],
            "participants": ["Alice"],
        },
        "long_summary": {
            "topic_flow": "当天主要讨论摘要浏览默认展示。",
            "important_details": ["长期摘要默认应有可读内容。"],
            "conclusions": [],
            "open_loops": [],
        },
        "recall_cards": [{
            "type": "episode_topic",
            "text": "摘要浏览修复。",
            "keywords": ["摘要", "浏览"],
        }],
    }
    db_session.add(MemoryDigest(
        id=60,
        session_id="42",
        user_id="group_42",
        digest_date="2026-05-28",
        level=0,
        content="",
        meta_json=json.dumps(meta, ensure_ascii=False),
        source_start_log_id=1,
        source_end_log_id=9,
        created_at=now,
    ))
    db_session.commit()

    preview = client.get(
        "/api/v1/admin/session-memory/sessions/group_42/digests",
        headers=_auth_header(),
    )
    full = client.get(
        "/api/v1/admin/session-memory/sessions/group_42/digests",
        headers=_auth_header(),
        params={"include_content": "true"},
    )

    assert preview.status_code == 200, preview.text
    item = preview.json()["items"][0]
    assert item["digest_id"] == 60
    assert item["preview"] == "群聊围绕摘要浏览修复展开。 关键词：摘要、浏览"
    assert "content" not in item
    full_item = full.json()["items"][0]
    assert "当天主要讨论摘要浏览默认展示" in full_item["content"]
    assert full.json()["session_aliases"] == ["42", "group_42"]


def test_admin_session_memory_long_digest_run_endpoint_regenerates_selected_session(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = _db_time(2026, 6, 1, 12, 0, 0)
    db_session.add(MemoryDigest(
        id=70,
        session_id="group_1",
        user_id="group_1",
        digest_date="2026-05-31",
        level=1,
        content="旧长期摘要",
        meta_json=json.dumps({"status": "active"}, ensure_ascii=False),
        created_at=now,
    ))
    db_session.commit()
    calls = []

    def fake_generate_daily_digest_for_date(*, target_date, user_id=None, session_id=None, force=False, **kwargs):
        calls.append({
            "target_date": target_date,
            "user_id": user_id,
            "session_id": session_id,
            "force": force,
            "kwargs": kwargs,
        })
        return 1

    monkeypatch.setattr(
        "api.admin.session_memory_routes.generate_daily_digest_for_date",
        fake_generate_daily_digest_for_date,
    )

    response = client.post(
        "/api/v1/admin/session-memory/group_1/digests/run",
        headers=_auth_header(),
        json={"force": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["created_sessions"] == 1
    assert calls == [{
        "target_date": "2026-05-31",
        "user_id": "group_1",
        "session_id": "group_1",
        "force": True,
        "kwargs": {},
    }]
