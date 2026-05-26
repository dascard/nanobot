import json

from sqlalchemy import text


def test_preview_reports_empty_memory_index_and_buildable_chunks(db_session):
    from core.database import MemoryDigest
    from core.semantic.backfill import preview_semantic_index_backfill

    digest = MemoryDigest(
        user_id="u1",
        session_id="s1",
        digest_date="2026-05-26",
        level=2,
        content="端口冲突排查",
        meta_json=json.dumps({
            "recall_cards": [
                {"title": "端口", "text": "8000 端口被占用", "keywords": ["uvicorn"]},
            ],
        }, ensure_ascii=False),
    )
    db_session.add(digest)
    db_session.commit()

    preview = preview_semantic_index_backfill(db_session, source_type="memory")

    assert preview["source_type"] == "memory"
    assert preview["indexed_items"] == 0
    assert preview["buildable_chunks"] == 1
    assert preview["needs_build"] is True
    assert preview["sources"]["memory_digest"]["source_rows"] == 1


def test_build_index_from_existing_memory_digest_writes_items_and_fts(db_session):
    from core.database import MemoryDigest, SemanticIndexItem
    from core.semantic.backfill import build_semantic_index_from_existing_data

    digest = MemoryDigest(
        user_id="u1",
        session_id="s1",
        digest_date="2026-05-26",
        level=2,
        content="端口冲突排查",
        meta_json=json.dumps({
            "recall_cards": [
                {"title": "端口", "text": "8000 端口被占用", "keywords": ["uvicorn"]},
            ],
        }, ensure_ascii=False),
    )
    db_session.add(digest)
    db_session.commit()

    result = build_semantic_index_from_existing_data(db_session, source_type="memory")

    assert result["indexed_chunks"] == 1
    row = db_session.query(SemanticIndexItem).one()
    assert row.source_type == "memory_digest"
    assert row.embedding_status == "disabled"
    assert "8000" in row.lexical_text
    assert db_session.execute(text("SELECT COUNT(*) FROM semantic_index_fts")).scalar() == 1


def test_build_index_from_existing_sticker_and_knowledge(db_session):
    from core.database import KnowledgeChunk, KnowledgeDocument, SemanticIndexItem, StickerMemory
    from core.semantic.backfill import build_semantic_index_from_existing_data, preview_semantic_index_backfill

    sticker = StickerMemory(
        chat_stream_id="qq:123:group",
        sticker_hash="hash-1",
        file_ref="https://example.com/a.png",
        name="震惊",
        description="震惊表情包",
        tags_json=json.dumps(["震惊"], ensure_ascii=False),
        emotions_json=json.dumps(["surprised"], ensure_ascii=False),
        status="active",
        dedupe_status="unique",
        describe_status="ok",
    )
    document = KnowledgeDocument(
        document_kind="manual_markdown",
        title="端口文档",
        domain="ops",
        status="active",
        trust_level="high",
    )
    db_session.add_all([sticker, document])
    db_session.commit()
    chunk = KnowledgeChunk(
        document_id=document.id,
        chunk_id="chunk-1",
        order_index=0,
        title="端口冲突",
        text="端口冲突时使用 lsof 排查。",
        status="active",
        trust_level="high",
    )
    db_session.add(chunk)
    db_session.commit()

    preview = preview_semantic_index_backfill(db_session, source_type="all")
    result = build_semantic_index_from_existing_data(db_session, source_type="all")

    assert preview["buildable_chunks"] >= 2
    assert result["sources"]["sticker"]["indexed_chunks"] == 1
    assert result["sources"]["knowledge"]["indexed_chunks"] == 1
    assert {row.source_type for row in db_session.query(SemanticIndexItem).all()} >= {"sticker", "knowledge"}
