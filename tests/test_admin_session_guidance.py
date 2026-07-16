"""Admin 会话指导配置、发现与脱敏审计合同测试。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from urllib.parse import quote

import pytest
from sqlalchemy import event


@pytest.fixture
def auth_header(monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    return {"Authorization": "Bearer test-token"}


def _detail_path(chat_stream_id: str) -> str:
    return f"/api/v1/admin/configs/{quote(chat_stream_id, safe='')}"


def _assert_guidance_body_absent(value, body: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    assert body not in serialized

    def walk(node):
        if isinstance(node, dict):
            assert "session_guidance" not in node
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)


def test_admin_upsert_session_guidance_canonicalizes_private_identity(
    client,
    auth_header,
    db_session,
):
    from core.database import ChatStreamConfig

    response = client.put(
        "/api/v1/admin/configs",
        headers=auth_header,
        json={
            "platform": " QQ ",
            "chat_type": "private",
            "session_id": "private_user-a",
            "session_guidance": "  回答简洁。\r\n使用中文。  ",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["chat_stream_id"] == "qq:user-a:private"
    assert data["platform"] == "qq"
    assert data["chat_type"] == "private"
    assert data["session_guidance"] == "回答简洁。\n使用中文。"
    assert data["session_guidance_configured"] is True
    assert data["session_guidance_chars"] == len(data["session_guidance"])
    assert len(data["session_guidance_sha256"]) == 64

    row = db_session.get(ChatStreamConfig, "qq:user-a:private")
    assert row is not None
    assert row.session_guidance == data["session_guidance"]
    assert row.session_guidance_updated_at is not None


def test_admin_static_upsert_requires_session_guidance(client, auth_header, db_session):
    from core.database import ChatStreamConfig

    response = client.put(
        "/api/v1/admin/configs",
        headers=auth_header,
        json={
            "platform": "qq",
            "chat_type": "private",
            "session_id": "private_missing-guidance",
        },
    )

    assert response.status_code == 422
    assert db_session.query(ChatStreamConfig).count() == 0


@pytest.mark.parametrize("query", ["", "?effective=1"])
def test_admin_config_lists_never_serialize_guidance_body(
    client,
    auth_header,
    db_session,
    query,
):
    from core.database import ChatStreamConfig

    body = "GUIDANCE_BODY_MUST_NOT_APPEAR_7f0f"
    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:list-private:private",
        session_guidance=body,
    ))
    db_session.commit()

    response = client.get(f"/api/v1/admin/configs{query}", headers=auth_header)

    assert response.status_code == 200, response.text
    _assert_guidance_body_absent(response.json(), body)
    item = next(
        value
        for value in response.json()["items"]
        if value["chat_stream_id"] == "qq:list-private:private"
    )
    assert item["session_guidance_configured"] is True
    assert item["session_guidance_chars"] == len(body)
    assert item["session_guidance_sha256"] == hashlib.sha256(body.encode()).hexdigest()


def test_admin_config_detail_requires_auth_and_returns_guidance(
    client,
    auth_header,
    db_session,
):
    from core.database import ChatStreamConfig

    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:detail-private:private",
        session_guidance="详情正文",
    ))
    db_session.commit()
    path = _detail_path("qq:detail-private:private")

    authenticated = client.get(path, headers=auth_header)
    unauthenticated = client.get(path)

    assert authenticated.status_code == 200, authenticated.text
    assert authenticated.json()["session_guidance"] == "详情正文"
    assert unauthenticated.status_code == 401
    assert "详情正文" not in unauthenticated.text


def test_admin_dynamic_alias_is_canonicalized_and_bare_path_is_rejected(
    client,
    auth_header,
    db_session,
):
    from core.database import ChatStreamConfig

    updated = client.put(
        "/api/v1/admin/configs/private_alias-user",
        headers=auth_header,
        json={"session_guidance": "别名写入"},
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["chat_stream_id"] == "qq:alias-user:private"
    assert db_session.get(ChatStreamConfig, "private_alias-user") is None
    assert db_session.get(ChatStreamConfig, "qq:alias-user:private") is not None

    for method in (client.get, client.delete):
        response = method("/api/v1/admin/configs/ambiguous", headers=auth_header)
        assert response.status_code == 422, response.text
    response = client.put(
        "/api/v1/admin/configs/ambiguous",
        headers=auth_header,
        json={"talk_value": 0.7},
    )
    assert response.status_code == 422, response.text


def test_admin_dynamic_null_keeps_guidance_and_empty_string_clears_only_guidance(
    client,
    auth_header,
    db_session,
):
    from core.database import ChatStreamConfig

    stream_id = "qq:clear-private:private"
    db_session.add(ChatStreamConfig(
        chat_stream_id=stream_id,
        talk_value=0.85,
        session_guidance="保持原值",
    ))
    db_session.commit()

    keep = client.put(
        _detail_path(stream_id),
        headers=auth_header,
        json={"talk_value": 0.75, "session_guidance": None},
    )
    assert keep.status_code == 200, keep.text
    assert keep.json()["session_guidance"] == "保持原值"

    cleared = client.put(
        _detail_path(stream_id),
        headers=auth_header,
        json={"session_guidance": ""},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["session_guidance"] == ""
    assert cleared.json()["session_guidance_chars"] == 0
    assert cleared.json()["session_guidance_sha256"] == ""

    db_session.expire_all()
    row = db_session.get(ChatStreamConfig, stream_id)
    assert row is not None
    assert row.talk_value == 0.75
    assert row.session_guidance == ""
    assert row.session_guidance_updated_at is not None


def test_admin_delete_removes_entire_config_row(client, auth_header, db_session):
    from core.database import AdminAuditLog, ChatStreamConfig

    stream_id = "qq:delete-private:private"
    body = "即将删除"
    db_session.add(ChatStreamConfig(
        chat_stream_id=stream_id,
        talk_value=0.9,
        session_guidance=body,
    ))
    db_session.commit()

    response = client.delete(_detail_path(stream_id), headers=auth_header)

    assert response.status_code == 200, response.text
    assert db_session.get(ChatStreamConfig, stream_id) is None

    audit = (
        db_session.query(AdminAuditLog)
        .filter(AdminAuditLog.action == "delete_config")
        .order_by(AdminAuditLog.id.desc())
        .first()
    )
    assert audit is not None
    assert body not in audit.detail_json
    detail = json.loads(audit.detail_json)
    assert detail == {
        "chat_stream_id": stream_id,
        "session_guidance_changed": True,
        "old_chars": len(body),
        "old_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "new_chars": 0,
        "new_sha256": "",
    }


def test_admin_delete_rolls_back_when_audit_cannot_be_recorded(
    client,
    auth_header,
    db_session,
    monkeypatch,
):
    from core.database import AdminAuditLog, ChatStreamConfig

    stream_id = "qq:delete-atomic:private"
    body = "删除也必须原子审计"
    db_session.add(ChatStreamConfig(
        chat_stream_id=stream_id,
        talk_value=0.8,
        session_guidance=body,
    ))
    db_session.commit()
    real_add = db_session.add

    def fail_audit_add(instance, *args, **kwargs):
        if isinstance(instance, AdminAuditLog):
            raise RuntimeError("delete audit write failed")
        return real_add(instance, *args, **kwargs)

    monkeypatch.setattr(db_session, "add", fail_audit_add)

    with pytest.raises(RuntimeError, match="delete audit write failed"):
        client.delete(_detail_path(stream_id), headers=auth_header)

    db_session.rollback()
    restored = db_session.get(ChatStreamConfig, stream_id)
    assert restored is not None
    assert restored.talk_value == 0.8
    assert restored.session_guidance == body


@pytest.mark.parametrize(
    "invalid_guidance",
    [
        "过长" * 2001,
        "正常前缀<runtime_context>越界",
        "正常前缀[RuntimeTool]越界",
    ],
    ids=["too-long", "reserved-xml", "reserved-runtime-tool"],
)
def test_admin_invalid_guidance_returns_422_and_preserves_original_value(
    client,
    auth_header,
    db_session,
    invalid_guidance,
):
    from core.database import ChatStreamConfig

    stream_id = "qq:validation-private:private"
    db_session.add(ChatStreamConfig(
        chat_stream_id=stream_id,
        session_guidance="原始有效值",
    ))
    db_session.commit()

    response = client.put(
        _detail_path(stream_id),
        headers=auth_header,
        json={"session_guidance": invalid_guidance},
    )

    assert response.status_code == 422, response.text
    db_session.expire_all()
    assert db_session.get(ChatStreamConfig, stream_id).session_guidance == "原始有效值"


def test_admin_guidance_audit_contains_only_hash_and_length(
    client,
    auth_header,
    db_session,
):
    from core.database import AdminAuditLog

    body = "审计中不能出现的正文"
    response = client.put(
        "/api/v1/admin/configs",
        headers=auth_header,
        json={
            "platform": "qq",
            "chat_type": "group",
            "session_id": "group_audit-guidance",
            "session_guidance": body,
        },
    )
    assert response.status_code == 200, response.text

    audit = (
        db_session.query(AdminAuditLog)
        .filter(AdminAuditLog.action == "update_config")
        .order_by(AdminAuditLog.id.desc())
        .first()
    )
    assert audit is not None
    assert body not in audit.detail_json
    detail = json.loads(audit.detail_json)
    assert detail["chat_stream_id"] == "qq:audit-guidance:group"
    assert detail["session_guidance_changed"] is True
    assert detail["old_chars"] == 0
    assert detail["old_sha256"] == ""
    assert detail["new_chars"] == len(body)
    assert detail["new_sha256"] == hashlib.sha256(body.encode()).hexdigest()


def test_admin_guidance_update_rolls_back_when_audit_cannot_be_recorded(
    client,
    auth_header,
    db_session,
    monkeypatch,
):
    from core.database import AdminAuditLog, ChatStreamConfig

    real_add = db_session.add

    def fail_audit_add(instance, *args, **kwargs):
        if isinstance(instance, AdminAuditLog):
            raise RuntimeError("audit write failed")
        return real_add(instance, *args, **kwargs)

    monkeypatch.setattr(db_session, "add", fail_audit_add)

    with pytest.raises(RuntimeError, match="audit write failed"):
        client.put(
            "/api/v1/admin/configs",
            headers=auth_header,
            json={
                "platform": "qq",
                "chat_type": "private",
                "session_id": "private_atomic-audit",
                "session_guidance": "必须与审计原子提交",
            },
        )

    db_session.rollback()
    assert db_session.get(ChatStreamConfig, "qq:atomic-audit:private") is None


def test_admin_effective_discovery_covers_group_private_agent_and_runtime(
    client,
    auth_header,
    db_session,
    monkeypatch,
):
    from core.database import AgentRun, ChatLog, ConversationTurn

    db_session.add(ChatLog(
        session_id="group_discovery-group",
        user_id="group_discovery-group",
        session_name="发现群聊",
        role="user",
        content="群消息",
    ))
    db_session.add(ConversationTurn(
        session_id="private_discovery-private",
        user_id="discovery-private",
        role="user",
        content="私聊消息",
    ))
    db_session.add(AgentRun(
        run_id="discovery-agent-run",
        session_id="private_agent-private",
        user_id="agent-private",
        chat_type="private",
        meta_json=json.dumps({"platform": "web"}),
    ))
    db_session.commit()
    monkeypatch.setattr(
        "api.admin.chat_config_routes._runtime_snapshot",
        lambda: {"group_runtime-group": {"session_name": "运行时群聊"}},
    )

    response = client.get(
        "/api/v1/admin/configs?effective=1&limit=100",
        headers=auth_header,
    )

    assert response.status_code == 200, response.text
    items = {item["chat_stream_id"]: item for item in response.json()["items"]}
    assert "qq:discovery-group:group" in items
    assert "qq:discovery-private:private" in items
    assert "web:agent-private:private" in items
    assert "qq:runtime-group:group" in items
    assert items["qq:discovery-group:group"]["session_name"] == "发现群聊"
    assert (
        items["qq:discovery-group:group"]["runtime_session_id"]
        == "group_discovery-group"
    )
    assert (
        items["qq:discovery-private:private"]["runtime_session_id"]
        == "private_discovery-private"
    )
    assert (
        items["web:agent-private:private"]["runtime_session_id"]
        == "private_agent-private"
    )
    assert (
        items["qq:runtime-group:group"]["runtime_session_id"]
        == "group_runtime-group"
    )
    assert "chat_log" in items["qq:discovery-group:group"]["sources"]
    assert "conversation_turn" in items["qq:discovery-private:private"]["sources"]
    assert "agent_run" in items["web:agent-private:private"]["sources"]
    assert "runtime" in items["qq:runtime-group:group"]["sources"]

    streams = client.get("/api/v1/admin/chat-streams", headers=auth_header)
    assert streams.status_code == 200, streams.text
    assert "qq:discovery-group:group" in streams.json()["items"]
    assert "qq:discovery-private:private" in streams.json()["items"]


def test_admin_group_effective_preview_rolls_summary_without_database_writes(
    client,
    auth_header,
    db_session,
):
    from core.database import (
        ConversationTurn,
        RollingSessionSummary,
        SessionSummaryJob,
        User,
    )

    session_id = "group_preview-read-only"
    user_id = "preview-read-only-user"
    base_time = datetime(2026, 7, 13, 10, 0, 0)
    clear_at = base_time + timedelta(minutes=1)
    stale_summary = RollingSessionSummary(
        session_id=session_id,
        user_id=user_id,
        chat_type="group",
        status="active",
        summary_kind="deterministic_fallback",
        summary_text="清除前摘要不得被预览归档",
        covered_until_turn_id=0,
        created_at=base_time,
        updated_at=base_time,
    )
    db_session.add_all([
        User(id=user_id, history_clear_at=clear_at),
        stale_summary,
    ])
    for index in range(24):
        role = "user" if index % 2 == 0 else "assistant"
        sender_name = "甲" if index % 4 < 2 else "乙"
        db_session.add(ConversationTurn(
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=f"群聊滚动摘要只读预览 {index + 1}",
            created_at=clear_at + timedelta(seconds=index + 1),
            meta_json=json.dumps({
                "kind": "chat",
                "sender_name": sender_name,
            }, ensure_ascii=False),
        ))
    db_session.commit()

    writes: list[str] = []

    def capture_writes(_conn, _cursor, statement, _params, _context, _many):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith(("insert ", "update ", "delete ")):
            writes.append(normalized)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_writes)
    try:
        response = client.post(
            "/api/v1/admin/prompt/effective-preview",
            headers=auth_header,
            json={
                "engine": "prompt",
                "platform": "qq",
                "chat_type": "group",
                "session_id": session_id,
                "group_id": "preview-read-only",
                "user_id": user_id,
                "user_input": "继续群聊摘要话题",
            },
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture_writes)

    assert response.status_code == 200, response.text
    assert "<rolling_session_summary" in json.dumps(
        response.json()["messages"],
        ensure_ascii=False,
    )
    assert writes == []
    db_session.expire_all()
    assert db_session.get(RollingSessionSummary, stale_summary.id).status == "active"
    assert db_session.query(RollingSessionSummary).count() == 1
    assert db_session.query(SessionSummaryJob).count() == 0


def test_admin_private_effective_preview_rolls_summary_without_database_writes(
    client,
    auth_header,
    db_session,
):
    from core.database import (
        ConversationTurn,
        RollingSessionSummary,
        SessionSummaryJob,
        User,
    )

    session_id = "private_preview-read-only"
    user_id = "preview-private-user"
    base_time = datetime(2026, 7, 13, 11, 0, 0)
    clear_at = base_time + timedelta(minutes=1)
    stale_summary = RollingSessionSummary(
        session_id=session_id,
        user_id=user_id,
        chat_type="private",
        status="active",
        summary_kind="deterministic_fallback",
        summary_text="清除前私聊摘要不得被预览归档",
        covered_until_turn_id=0,
        created_at=base_time,
        updated_at=base_time,
    )
    db_session.add_all([
        User(id=user_id, history_clear_at=clear_at),
        stale_summary,
    ])
    for index in range(40):
        db_session.add(ConversationTurn(
            session_id=session_id,
            user_id=user_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"私聊滚动摘要只读预览 {index + 1}",
            created_at=clear_at + timedelta(seconds=index + 1),
            meta_json=json.dumps({"kind": "chat"}, ensure_ascii=False),
        ))
    db_session.commit()

    writes: list[str] = []

    def capture_writes(_conn, _cursor, statement, _params, _context, _many):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith(("insert ", "update ", "delete ")):
            writes.append(normalized)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_writes)
    try:
        response = client.post(
            "/api/v1/admin/prompt/effective-preview",
            headers=auth_header,
            json={
                "engine": "prompt",
                "platform": "qq",
                "chat_type": "private",
                "session_id": session_id,
                "user_id": user_id,
                "user_input": "继续私聊摘要话题",
            },
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture_writes)

    assert response.status_code == 200, response.text
    assert "<rolling_session_summary" in json.dumps(
        response.json()["messages"],
        ensure_ascii=False,
    )
    assert writes == []
    db_session.expire_all()
    assert db_session.get(RollingSessionSummary, stale_summary.id).status == "active"
    assert db_session.query(RollingSessionSummary).count() == 1
    assert db_session.query(SessionSummaryJob).count() == 0


def test_admin_group_effective_preview_does_not_call_reranker_model(
    client,
    auth_header,
    db_session,
    monkeypatch,
):
    from app.group_memory.injection_service import GROUP_MEMORY_RAG_CACHE
    from core.database import ChatStreamConfig, GroupMemory
    from core.semantic.reranker import RerankResult
    from core.settings_service import settings
    import core.semantic.provider_factory as provider_factory

    reranker_calls: list[str] = []

    class SpyReranker:
        def rerank(self, query, candidates, *, top_k=None):
            reranker_calls.append(query)
            limited = candidates[:top_k] if top_k else candidates
            return [
                RerankResult(
                    candidate_id=candidate.candidate_id,
                    raw_score=0.9,
                    score=0.9,
                    model="preview-spy-reranker",
                    score_mode="identity",
                )
                for candidate in limited
            ]

    GROUP_MEMORY_RAG_CACHE.clear()
    original_get_bool = settings.get_bool
    monkeypatch.setattr(
        settings,
        "get_bool",
        lambda key, default=False: (
            True
            if key == "group_memory.injection_enabled"
            else original_get_bool(key, default)
        ),
    )
    db_session.add_all([
        ChatStreamConfig(
            chat_stream_id="qq:preview-no-model:group",
            group_profile_mode="on",
        ),
        GroupMemory(
            group_id="group_preview-no-model",
            memory_type="style",
            content="群聊回答偏好直接给出结论",
            content_hash="preview-no-model-memory",
            confidence=0.9,
            evidence_count=3,
            evidence_log_ids_json="[1, 2, 3]",
            decay_score=1.0,
            status="active",
            inject_policy="auto",
        ),
    ])
    db_session.commit()
    monkeypatch.setattr(
        provider_factory,
        "get_reranker_provider",
        lambda: SpyReranker(),
    )

    response = client.post(
        "/api/v1/admin/prompt/effective-preview",
        headers=auth_header,
        json={
            "engine": "prompt",
            "platform": "qq",
            "chat_type": "group",
            "session_id": "group_preview-no-model",
            "group_id": "preview-no-model",
            "user_input": "这个群应该怎么回答问题",
        },
    )

    assert response.status_code == 200, response.text
    assert reranker_calls == []
    assert response.json()["history_debug"]["model_calls_allowed"] is False
    assert {"reason": "model_calls_forbidden"} in response.json()[
        "group_memory_skipped"
    ]
    assert response.json()["preview_exact"] is False
    assert response.json()["preview_degraded_reasons"] == [
        "group_memory_model_calls_forbidden"
    ]
    assert "group_memory_model_calls_forbidden" in response.json()["warnings"]


def test_admin_discovery_reduces_archive_rows_in_sql_without_loading_content(
    client,
    auth_header,
    db_session,
):
    from core.database import AgentRun, ChatLog

    base_time = datetime(2026, 7, 13, 1, 0, 0)
    for index in range(5):
        db_session.add(ChatLog(
            session_id="group_sql-reduced",
            user_id=f"archive-user-{index}",
            session_name=f"历史群名-{index}" if index < 4 else "最新群名",
            role="user",
            content=f"ARCHIVE_CONTENT_MUST_NOT_BE_SELECTED_{index}",
            created_at=base_time + timedelta(seconds=index),
        ))
    for index in range(3):
        db_session.add(AgentRun(
            run_id=f"sql-reduced-run-{index}",
            session_id="private_sql-agent",
            user_id="sql-agent",
            chat_type="private",
            input_preview=f"INPUT_MUST_NOT_BE_SELECTED_{index}",
            output_preview=f"OUTPUT_MUST_NOT_BE_SELECTED_{index}",
            meta_json=json.dumps({
                "platform": "web",
                "session_name": f"Agent-{index}",
            }),
            started_at=base_time + timedelta(seconds=index),
        ))
    db_session.add(AgentRun(
        run_id="sql-reduced-invalid-meta",
        session_id="private_sql-invalid-meta",
        user_id="invalid-meta",
        chat_type="private",
        meta_json="{invalid-json",
        started_at=base_time,
    ))
    db_session.commit()

    statements: list[str] = []

    def capture_sql(_conn, _cursor, statement, _params, _context, _many):
        normalized = " ".join(statement.lower().split())
        if "chat_logs" in normalized or "agent_runs" in normalized:
            statements.append(normalized)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_sql)
    try:
        response = client.get(
            "/api/v1/admin/configs?effective=1&search=sql-&limit=100",
            headers=auth_header,
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture_sql)

    assert response.status_code == 200, response.text
    items = {item["chat_stream_id"]: item for item in response.json()["items"]}
    assert items["qq:sql-reduced:group"]["session_name"] == "最新群名"
    assert items["web:sql-agent:private"]["session_name"] == "Agent-2"
    assert "qq:sql-invalid-meta:private" in items

    archive_sql = "\n".join(statements)
    assert "chat_logs.content" not in archive_sql
    assert "agent_runs.input_preview" not in archive_sql
    assert "agent_runs.output_preview" not in archive_sql
    assert "max(chat_logs.id)" in archive_sql
    assert "group by chat_logs.session_id" in archive_sql
    assert "row_number() over" in archive_sql
    assert "partition by agent_runs.session_id" in archive_sql


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "limit=0",
        "limit=-1",
        "limit=101",
    ],
)
def test_admin_config_list_rejects_invalid_pagination(
    client,
    auth_header,
    query,
):
    response = client.get(
        f"/api/v1/admin/configs?{query}",
        headers=auth_header,
    )

    assert response.status_code == 422, response.text


def test_admin_effective_filters_platform_type_and_configured(
    client,
    auth_header,
    db_session,
):
    from core.database import ChatStreamConfig

    db_session.add_all([
        ChatStreamConfig(
            chat_stream_id="qq:filter-group:group",
            session_guidance="群指导",
        ),
        ChatStreamConfig(
            chat_stream_id="web:filter-private:private",
            session_guidance="",
        ),
    ])
    db_session.commit()

    response = client.get(
        "/api/v1/admin/configs"
        "?effective=1&platform=web&chat_type=private&configured=0&limit=100",
        headers=auth_header,
    )

    assert response.status_code == 200, response.text
    assert [item["chat_stream_id"] for item in response.json()["items"]] == [
        "web:filter-private:private",
    ]


def test_admin_effective_canonical_row_wins_legacy_alias_conflict(
    client,
    auth_header,
    db_session,
):
    from core.database import ChatStreamConfig

    canonical_body = "CANONICAL_BODY_MUST_NOT_APPEAR"
    alias_body = "ALIAS_BODY_MUST_NOT_APPEAR"
    db_session.add_all([
        ChatStreamConfig(
            chat_stream_id="qq:conflict-group:group",
            talk_value=0.9,
            session_guidance=canonical_body,
        ),
        ChatStreamConfig(
            chat_stream_id="group_conflict-group",
            talk_value=0.1,
            session_guidance=alias_body,
        ),
    ])
    db_session.commit()

    response = client.get(
        "/api/v1/admin/configs?effective=1&search=conflict-group&limit=100",
        headers=auth_header,
    )

    assert response.status_code == 200, response.text
    _assert_guidance_body_absent(response.json(), canonical_body)
    assert alias_body not in response.text
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["chat_stream_id"] == "qq:conflict-group:group"
    assert item["identity_status"] == "canonical"
    assert item["identity_conflict"] is True
    assert item["legacy_aliases"] == ["group_conflict-group"]
    assert item["talk_value"] == 0.9
    assert item["session_guidance_chars"] == len(canonical_body)


def test_admin_effective_marks_bare_config_identity_unresolved(
    client,
    auth_header,
    db_session,
):
    from core.database import ChatStreamConfig

    db_session.add(ChatStreamConfig(
        chat_stream_id="bare-unresolved",
        session_guidance="不会作为有效 canonical 配置",
    ))
    db_session.commit()

    response = client.get(
        "/api/v1/admin/configs?effective=1&search=bare-unresolved&limit=100",
        headers=auth_header,
    )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["chat_stream_id"] == "bare-unresolved"
    assert item["identity_status"] == "unresolved"
    assert item["identity_conflict"] is False
