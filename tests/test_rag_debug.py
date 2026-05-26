from pathlib import Path

from sqlalchemy import inspect, text


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


def test_rag_debug_page_is_registered():
    app_source = Path("webui/src/App.jsx").read_text(encoding="utf-8")

    assert "RagDebugPage" in app_source
    assert "{ to: '/rag-debug', label: 'RAG Debug'" in app_source
    assert '<Route path="/rag-debug" element={<RagDebugPage />} />' in app_source

    page_source = Path("webui/src/features/rag/RagDebugPage.jsx").read_text(encoding="utf-8")
    assert "RagCandidateTable" in page_source
    assert "RagScoreBreakdown" in page_source
    assert "RagRerankerPanel" in page_source
