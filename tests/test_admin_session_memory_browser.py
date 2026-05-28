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
