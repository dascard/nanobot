import json
from datetime import datetime, timedelta

from core.database import MemoryDigest, RollingSessionSummary


def _auth_header():
    return {"Authorization": "Bearer test-token"}


def test_admin_session_memory_sessions_list_returns_session_summaries(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = datetime(2026, 5, 28, 12, 0, 0)
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
    now = datetime(2026, 5, 28, 12, 0, 0)
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


def test_admin_session_memory_sessions_normalize_group_aliases_and_filter_system_rows(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = datetime(2026, 5, 28, 12, 0, 0)
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
    now = datetime(2026, 5, 28, 12, 0, 0)
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


def test_admin_session_memory_sessions_sql_paginates_beyond_scan_window(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    now = datetime(2026, 5, 28, 12, 0, 0)
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
    now = datetime(2026, 5, 28, 12, 0, 0)
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
