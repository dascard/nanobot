from pathlib import Path

from sqlalchemy import inspect, text


class DebugRerankerProvider:
    def rerank(self, query, candidates, *, top_k=None):
        from core.semantic.reranker import RerankResult

        limited = candidates[:top_k] if top_k else candidates
        return [
            RerankResult(
                candidate_id=candidate.candidate_id,
                raw_score=2.0,
                score=0.88,
                model="debug-reranker",
                score_mode="identity",
            )
            for candidate in limited
        ]


def _auth_header():
    return {"Authorization": "Bearer test-token"}


def test_semantic_schema_creates_index_tables_and_fts_rowid(db_session):
    from core.semantic.schema import ensure_semantic_schema

    ensure_semantic_schema(db_session.bind)
    tables = set(inspect(db_session.bind).get_table_names())

    assert "semantic_index_items" in tables
    assert "semantic_index_jobs" in tables
    assert "rag_debug_runs" in tables
    assert "semantic_index_fts" in tables

    db_session.execute(text(
        "INSERT INTO semantic_index_fts(rowid, title, text, lexical_text, source_type, source_id, source_sub_id) "
        "VALUES (42, '标题', '正文', '标题 正文 标签', 'memory_digest', '1', 'card:0')"
    ))
    row = db_session.execute(text("SELECT rowid FROM semantic_index_fts WHERE rowid = 42")).first()
    assert row[0] == 42


def test_rag_debug_query_saves_run(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={"source_type": "memory", "query": "端口冲突", "limit": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] > 0
    assert data["trace_id"]
    assert data["response"]["score_breakdown"]["degraded"] is True

    runs = client.get("/api/v1/admin/rag/debug/runs", headers=_auth_header())
    assert runs.status_code == 200
    assert runs.json()["items"][0]["id"] == data["run_id"]

    detail = client.get(f"/api/v1/admin/rag/debug/runs/{data['run_id']}", headers=_auth_header())
    assert detail.status_code == 200
    assert detail.json()["query"] == "端口冲突"


def test_rag_debug_memory_uses_real_pipeline_trace(client, db_session, monkeypatch):
    import json

    from core.database import MemoryDigest
    from core.semantic.adapters import chunks_from_memory_digest
    from core.semantic.indexer import upsert_semantic_chunks
    import core.semantic.provider_factory as provider_factory

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(provider_factory, "get_reranker_provider", lambda: DebugRerankerProvider())
    digest = MemoryDigest(
        id=701,
        user_id="u1",
        session_id="s1",
        digest_date="2026-05-26",
        level=2,
        content="RAG Debug 端口冲突",
        meta_json=json.dumps({
            "schema_version": 2,
            "status": "active",
            "recall_cards": [
                {"title": "端口", "text": "RAG Debug 端口冲突排查", "keywords": ["RAG", "端口"]},
            ],
        }, ensure_ascii=False),
    )
    db_session.add(digest)
    db_session.commit()
    upsert_semantic_chunks(db_session, chunks_from_memory_digest(digest), index_version="fake:v1:v1")

    response = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={
            "source_type": "memory",
            "query": "RAG Debug 端口",
            "limit": 3,
            "filters": {"user_id": "u1", "session_id": "s1", "source": "digest"},
        },
    )

    assert response.status_code == 200
    payload = response.json()["response"]
    stages = payload["stages"]
    assert payload["score_breakdown"]["fallback_reason"] != "rag_debug_stub"
    assert stages["fts_hits"][0]["bm25_raw"] is not None
    assert stages["merged_candidates"][0]["candidate_id"] == "memory_digest:701:card:0"
    assert stages["reranker_input_pairs"][0]["candidate_id"] == "memory_digest:701:card:0"
    assert stages["final_candidates"][0]["score_breakdown"]["raw_reranker"] == 2.0


def test_rag_debug_run_persists_sanitized_payload(client, db_session, monkeypatch):
    import json

    from core.database import MemoryDigest
    from core.semantic.adapters import chunks_from_memory_digest
    from core.semantic.indexer import upsert_semantic_chunks
    import core.semantic.provider_factory as provider_factory

    secret_tail = "SECRET_DEBUG_PAYLOAD_SHOULD_NOT_BE_STORED"
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(provider_factory, "get_reranker_provider", lambda: DebugRerankerProvider())
    digest = MemoryDigest(
        id=702,
        user_id="u1",
        session_id="s1",
        digest_date="2026-05-26",
        level=2,
        content="RAG Debug 长文本",
        meta_json=json.dumps({
            "schema_version": 2,
            "status": "active",
            "recall_cards": [
                {
                    "title": "长文本",
                    "text": "RAG Debug " + ("上下文" * 400) + secret_tail,
                    "keywords": ["RAG", "Debug"],
                },
            ],
        }, ensure_ascii=False),
    )
    db_session.add(digest)
    db_session.commit()
    upsert_semantic_chunks(db_session, chunks_from_memory_digest(digest), index_version="fake:v1:v1")

    created = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={
            "source_type": "memory",
            "query": "RAG Debug",
            "limit": 3,
            "filters": {"user_id": "u1", "session_id": "s1", "source": "digest"},
        },
    )
    run_id = created.json()["run_id"]
    detail = client.get(f"/api/v1/admin/rag/debug/runs/{run_id}", headers=_auth_header())
    serialized = json.dumps(detail.json(), ensure_ascii=False)

    assert detail.status_code == 200
    assert secret_tail not in serialized
    assert "[truncated]" in serialized


def test_rag_debug_payload_sanitizer_redacts_sensitive_fields():
    import json

    from api.admin.rag_routes import _sanitize_debug_payload

    payload = {
        "headers": {
            "Authorization": "Bearer SECRET_TOKEN",
            "Cookie": "session=SECRET_COOKIE",
        },
        "filters": {
            "user_id": "user-secret",
            "group_id": "group-secret",
            "url": "https://example.com/debug?token=SECRET_URL_TOKEN&ok=1",
        },
        "messages": [
            {"no_context": True, "content": "私聊敏感内容"},
            {"role": "internal", "text": "内部敏感内容"},
        ],
        "content": "公开内容" * 200,
    }

    sanitized = _sanitize_debug_payload(payload)
    serialized = json.dumps(sanitized, ensure_ascii=False)

    assert sanitized["headers"]["Authorization"] == "[redacted]"
    assert sanitized["headers"]["Cookie"] == "[redacted]"
    assert sanitized["filters"]["user_id"] == "[redacted:id]"
    assert sanitized["filters"]["group_id"] == "[redacted:id]"
    assert "SECRET_URL_TOKEN" not in serialized
    assert sanitized["messages"][0]["content"] == "[redacted:no_context]"
    assert sanitized["messages"][1]["text"] == "[redacted:no_context]"
    assert "私聊敏感内容" not in serialized
    assert "[truncated]" in sanitized["content"]


def test_rag_debug_query_runs_sticker_search(client, db_session, monkeypatch):
    import json

    from core.database import StickerMemory
    from core.semantic.adapters import chunk_from_sticker
    from core.semantic.indexer import upsert_semantic_chunks

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    sticker = StickerMemory(
        chat_stream_id="qq:123:group",
        sticker_hash="debug-sticker",
        file_ref="https://example.com/debug-sticker.png",
        description="震惊猫猫看着屏幕",
        tags_json=json.dumps(["震惊", "猫"], ensure_ascii=False),
        emotions_json=json.dumps(["surprised"], ensure_ascii=False),
        status="active",
        dedupe_status="unique",
        describe_status="ok",
    )
    db_session.add(sticker)
    db_session.commit()
    db_session.refresh(sticker)
    upsert_semantic_chunks(
        db_session,
        [chunk_from_sticker(sticker)],
        index_version="fake:v1:sticker",
    )

    response = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={
            "source_type": "sticker",
            "query": "震惊猫猫",
            "limit": 3,
            "filters": {"group_id": "123"},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["response"]["candidates"][0]["reply_token"] == f"[sticker:{sticker.id}]"
    assert data["response"]["candidates"][0]["source_type"] == "sticker"
    assert data["response"]["score_breakdown"]["fallback_reason"] == "reranker_unavailable"


def test_rag_debug_query_runs_knowledge_search_with_citation(client, db_session, monkeypatch):
    from core.database import KnowledgeChunk
    from core.knowledge_library import create_manual_document
    from core.semantic.adapters import chunk_from_knowledge_chunk
    from core.semantic.indexer import upsert_semantic_chunks

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    doc = create_manual_document(
        db_session,
        filename="debug.md",
        content="# RAG\nRAG Debug 必须显示 citation。",
        title="Debug 知识",
        trust_level="medium",
        published_at="2026-05-26",
    )
    chunks = db_session.query(KnowledgeChunk).filter_by(document_id=doc.id).all()
    upsert_semantic_chunks(
        db_session,
        [chunk_from_knowledge_chunk(chunk, document=doc) for chunk in chunks],
        index_version="fake:v1:knowledge",
    )

    response = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={"source_type": "knowledge", "query": "RAG Debug", "limit": 3},
    )

    assert response.status_code == 200
    candidate = response.json()["response"]["candidates"][0]
    assert candidate["source_type"] == "knowledge"
    assert candidate["citation"]["title"] == "Debug 知识"
    assert candidate["citation"]["trust_level"] == "medium"


def test_rag_debug_group_memory_uses_retrieval_service_not_stub(client, db_session, monkeypatch):
    from datetime import datetime

    from core.database import GroupMemory

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    row = GroupMemory(
        group_id="group_1097666427",
        memory_type="topic",
        content="群里经常讨论本地模型部署和 RAG reranker。",
        content_hash="rag-debug-group-memory",
        confidence=0.86,
        evidence_count=3,
        evidence_log_ids_json="[1, 2, 3]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        last_seen=datetime.now(),
    )
    db_session.add(row)
    db_session.commit()

    response = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={
            "source_type": "group_memory",
            "query": "本地模型部署 RAG",
            "filters": {"group_id": "1097666427", "current_user_input": "本地模型部署怎么做？"},
        },
    )

    assert response.status_code == 200
    payload = response.json()["response"]
    assert payload["score_breakdown"]["fallback_reason"] != "rag_debug_stub"
    assert payload["score_breakdown"]["recall_mode"] == "sql_gate_reranker"
    assert payload["score_breakdown"]["fts_embedding_trace_available"] is False
    assert payload["stages"]["merged_candidates"][0]["candidate_id"] == f"group_memory:{row.id}:memory"
    assert payload["stages"]["final_candidates"][0]["id"] == row.id


def test_rag_debug_page_is_registered():
    app_source = Path("webui/src/App.jsx").read_text(encoding="utf-8")

    assert "RagDebugPage" in app_source
    assert "{ to: '/rag-debug', label: 'RAG Debug'" in app_source
    assert '<Route path="/rag-debug" element={<RagDebugPage />} />' in app_source

    page_source = Path("webui/src/features/rag/RagDebugPage.jsx").read_text(encoding="utf-8")
    assert "RagCandidateTable" in page_source
    assert "RagScoreBreakdown" in page_source
    assert "RagRerankerPanel" in page_source
