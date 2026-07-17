import json

from sqlalchemy import text

from core.database import GroupMemory, MemoryDigest, RollingSessionSummary, SemanticIndexItem, StickerMemory


def _recallable_digest_meta(**values):
    meta = {
        "schema_version": 2,
        "status": "active",
        "generator": "llm",
        "llm_status": "success",
        "quality": {"score": 0.9, "issues": []},
    }
    meta.update(values)
    return meta


def test_memory_digest_recall_cards_become_chunks():
    from core.semantic.adapters import chunks_from_memory_digest

    digest = MemoryDigest(
        id=11,
        user_id="u1",
        session_id="s1",
        level=2,
        content="总摘要",
        meta_json=json.dumps(_recallable_digest_meta(
            recall_cards=[
                {
                    "type": "fact",
                    "title": "端口冲突",
                    "text": "8000 端口被占用",
                    "keywords": ["端口", "uvicorn"],
                    "importance": 0.85,
                    "evidence_log_ids": [1],
                },
                {
                    "type": "decision",
                    "title": "模型路由",
                    "summary": "便宜模型优先",
                    "keywords": ["route"],
                    "evidence_log_ids": [2],
                },
            ]
        ), ensure_ascii=False),
    )

    chunks = chunks_from_memory_digest(digest)

    assert len({chunk.source_sub_id for chunk in chunks}) == 2
    assert all(chunk.source_sub_id.startswith("card:rc_") for chunk in chunks)
    assert "card:0" not in {chunk.source_sub_id for chunk in chunks}
    assert all(chunk.source_type == "memory_digest" for chunk in chunks)
    assert all(chunk.visibility == "recall" for chunk in chunks)
    assert "uvicorn" in chunks[0].lexical_text
    assert chunks[0].metadata["keywords"] == ["端口", "uvicorn"]
    assert chunks[0].metadata["importance"] == 0.85
    assert chunks[0].metadata["recall_card_type"] == "fact"
    assert chunks[0].metadata["evidence_log_ids"] == [1]


def test_recall_card_identity_is_order_stable_and_content_sensitive():
    from core.semantic.adapters import chunks_from_memory_digest

    cards = [
        {
            "type": "fact",
            "text": "8000 端口被占用",
            "keywords": ["端口"],
            "importance": 0.8,
            "evidence_log_ids": [9, 3],
        },
        {
            "type": "decision",
            "text": "使用 worker 原子重建索引",
            "keywords": ["worker"],
            "importance": 0.9,
            "evidence_log_ids": [12],
        },
    ]

    def build(raw_cards):
        return chunks_from_memory_digest(MemoryDigest(
            id=99,
            level=2,
            meta_json=json.dumps(_recallable_digest_meta(
                source_id="digest-source-stable",
                recall_cards=raw_cards,
            ), ensure_ascii=False),
        ))

    first = build(cards)
    reordered = build(list(reversed(cards)))
    presentation_changed = build([
        {**cards[0], "keywords": ["部署"], "importance": 0.1},
        cards[1],
    ])
    text_changed = build([
        {**cards[0], "text": "8001 端口被占用"},
        cards[1],
    ])

    assert {item.source_sub_id for item in first} == {
        item.source_sub_id for item in reordered
    }
    assert first[0].source_sub_id == presentation_changed[0].source_sub_id
    assert first[0].source_sub_id != text_changed[0].source_sub_id


def test_memory_digest_recall_card_chunks_keep_digest_source_metadata():
    from core.semantic.adapters import chunks_from_memory_digest

    digest = MemoryDigest(
        id=13,
        user_id="u1",
        session_id="s1",
        digest_date="2026-06-01",
        level=2,
        content="单张卡片内容",
        meta_json=json.dumps(_recallable_digest_meta(
            source_id="src-20260601-s1",
            source_type="date_session",
            source_range="log_id 100-120",
            summary_type="recall_card",
            generator="llm",
            quality={"score": 0.91, "issues": []},
            prompt_template="tasks/memory_digest_system + tasks/memory_digest_user",
            fallback_reason=None,
            message_count=18,
            recall_card_count=3,
            recall_cards=[
                {
                    "text": "memory_digests 的 level 2 是 RAG 主召回层。",
                    "keywords": ["memory_digests", "RAG"],
                }
            ],
        ), ensure_ascii=False),
    )

    chunks = chunks_from_memory_digest(digest)

    assert len(chunks) == 1
    assert chunks[0].metadata["digest_source_id"] == "src-20260601-s1"
    assert chunks[0].metadata["source_type"] == "date_session"
    assert chunks[0].metadata["source_range"] == "log_id 100-120"
    assert chunks[0].metadata["summary_type"] == "recall_card"
    assert chunks[0].metadata["generator"] == "llm"
    assert chunks[0].metadata["prompt_template"] == "tasks/memory_digest_system + tasks/memory_digest_user"
    assert chunks[0].metadata["message_count"] == 18
    assert chunks[0].metadata["recall_card_count"] == 3
    assert chunks[0].quality_score == 0.91


def test_memory_digest_level0_is_expand_only():
    from core.semantic.adapters import chunks_from_memory_digest

    digest = MemoryDigest(
        id=12,
        level=0,
        content="详细原文",
        meta_json=json.dumps(_recallable_digest_meta(), ensure_ascii=False),
    )

    chunks = chunks_from_memory_digest(digest)

    assert len(chunks) == 1
    assert chunks[0].source_sub_id == "digest:level0"
    assert chunks[0].visibility == "expand_only"


def test_session_summary_structured_fields_become_chunks():
    from core.semantic.adapters import (
        chunks_from_session_summary,
        session_summary_source_revision,
    )

    summary = RollingSessionSummary(
        id=21,
        session_id="s1",
        user_id="u1",
        status="active",
        summary_kind="llm_episode",
        summary_text="用户在排查部署问题。",
        summary_json=json.dumps({
            "summary": "用户在排查部署问题。",
            "open_threads": ["继续检查 RAG"],
            "decisions": [{"text": "使用 HTTP reranker"}],
            "important_user_requests": ["补测试报告"],
            "requests": ["旧别名不得重复索引"],
            "artifacts": [{"title": "计划", "content": ".Codex/plans/three-rag-implementation.md"}],
            "resolved_items": ["端口冲突已处理"],
            "resolved": ["旧 resolved 别名不得重复索引"],
            "participants": ["用户", "Nanobot"],
            "keywords": ["RAG", "部署"],
            "quality": {"score": 0.92, "issues": []},
        }, ensure_ascii=False),
        covered_from_turn_id=1,
        covered_until_turn_id=8,
        stable_hash="summary-revision-1",
    )

    chunks = chunks_from_session_summary(summary)
    sub_ids = {chunk.source_sub_id for chunk in chunks}

    assert "section:summary" in sub_ids
    assert any(item.startswith("section:open_threads:") for item in sub_ids)
    assert any(item.startswith("section:decisions:") for item in sub_ids)
    assert any(item.startswith("section:important_user_requests:") for item in sub_ids)
    assert any(item.startswith("section:artifacts:") for item in sub_ids)
    assert any(item.startswith("section:resolved_items:") for item in sub_ids)
    assert all(chunk.source_id == "s1" for chunk in chunks)
    assert all(chunk.metadata["document_id"] == 21 for chunk in chunks)
    assert all(
        chunk.metadata["source_revision"] == session_summary_source_revision(summary)
        for chunk in chunks
    )
    assert "旧别名不得重复索引" not in "\n".join(chunk.text for chunk in chunks)
    assert "旧 resolved 别名不得重复索引" not in "\n".join(chunk.text for chunk in chunks)
    assert all(chunk.source_type == "session_summary" for chunk in chunks)
    assert all(chunk.metadata["participants"] == ["用户", "Nanobot"] for chunk in chunks)
    assert all(chunk.metadata["keywords"] == ["RAG", "部署"] for chunk in chunks)
    assert all(chunk.metadata["quality"] == {"score": 0.92, "issues": []} for chunk in chunks)
    assert all("Nanobot" in chunk.lexical_text for chunk in chunks)
    assert all("RAG" in chunk.embedding_text for chunk in chunks)

    next_revision = RollingSessionSummary(
        id=22,
        session_id="s1",
        user_id="u1",
        status="active",
        summary_kind="llm_episode",
        summary_json=json.dumps({"summary": "下一版"}, ensure_ascii=False),
        stable_hash="summary-revision-2",
    )
    next_chunks = chunks_from_session_summary(next_revision)
    assert {chunk.source_id for chunk in next_chunks} == {"s1"}
    assert {chunk.metadata["document_id"] for chunk in next_chunks} == {22}


def test_session_summary_revision_is_json_format_stable_and_kind_sensitive():
    from core.semantic.adapters import session_summary_source_revision

    common = {
        "id": 31,
        "session_id": "revision-session",
        "status": "active",
        "covered_from_turn_id": 1,
        "covered_until_turn_id": 20,
        "stable_hash": "stable-content-hash",
    }
    compact = RollingSessionSummary(
        **common,
        summary_kind="llm_episode",
        summary_json='{"summary":"同一内容","keywords":["RAG"]}',
    )
    formatted = RollingSessionSummary(
        **common,
        summary_kind="llm_episode",
        summary_json='{\n  "keywords": ["RAG"],\n  "summary": "同一内容"\n}',
    )
    fallback = RollingSessionSummary(
        **common,
        summary_kind="deterministic_fallback",
        summary_json=compact.summary_json,
    )

    assert session_summary_source_revision(compact) == session_summary_source_revision(formatted)
    assert session_summary_source_revision(compact) != session_summary_source_revision(fallback)


def test_group_memory_one_row_one_chunk():
    from core.semantic.adapters import chunk_from_group_memory

    memory = GroupMemory(
        id=31,
        group_id="g1",
        memory_type="topic",
        content="群里经常讨论 RAG 和 reranker。",
        cluster_key="rag",
        meta_json=json.dumps({"keywords": ["RAG", "reranker"], "evidence_short_summary": "多次讨论检索"}, ensure_ascii=False),
    )

    chunk = chunk_from_group_memory(memory)

    assert chunk.source_type == "group_memory"
    assert chunk.source_sub_id == "memory"
    assert "RAG" in chunk.embedding_text
    assert "多次讨论检索" in chunk.embedding_text
    assert "31" not in chunk.embedding_text


def test_sticker_chunk_excludes_send_code_and_file_path():
    from core.semantic.adapters import chunk_from_sticker

    sticker = StickerMemory(
        id=41,
        chat_stream_id="group_1",
        sticker_hash="hash1",
        file_ref="/secret/file.gif",
        send_code="[CQ:image,file=secret]",
        local_path="/local/secret.gif",
        name="拍桌",
        description="表达震惊和催促。",
        tags_json=json.dumps(["震惊", "催更"], ensure_ascii=False),
        emotions_json=json.dumps(["惊讶"], ensure_ascii=False),
        status="active",
        dedupe_status="unique",
        describe_status="ok",
        meta_json=json.dumps({"qwen_summary": "适合吐槽进度"}, ensure_ascii=False),
    )

    chunk = chunk_from_sticker(sticker)

    assert chunk is not None
    assert "拍桌" in chunk.embedding_text
    assert "催更" in chunk.embedding_text
    assert "[CQ:image" not in chunk.embedding_text
    assert "/secret/file.gif" not in chunk.embedding_text
    assert "/local/secret.gif" not in chunk.embedding_text


def test_ai_daily_item_is_one_knowledge_chunk():
    from core.semantic.adapters import chunk_from_ai_daily_item

    chunk = chunk_from_ai_daily_item({
        "id": "news-1",
        "title": "Reranker 发布",
        "summary": "某模型发布了新的 reranker。",
        "source_name": "Example AI",
        "url": "https://example.com/news",
        "published_at": "2026-05-26",
    })

    assert chunk.source_type == "knowledge"
    assert chunk.source_sub_id == "ai_daily:news-1"
    assert chunk.text == "某模型发布了新的 reranker。"
    assert chunk.metadata["citation"]["url"] == "https://example.com/news"


def test_index_version_changes_when_chunk_strategy_changes():
    from core.semantic.indexer import build_index_version

    first = build_index_version("fake-embedding", "template-v1", "digest-v1")
    second = build_index_version("fake-embedding", "template-v1", "digest-v2")

    assert first != second
    assert first == "fake-embedding:template-v1:digest-v1"


def test_source_hash_uses_canonical_json():
    from core.semantic.indexer import stable_hash

    left = stable_hash({"b": 2, "a": 1, "tags": [" RAG ", "", "reranker"]})
    right = stable_hash({"tags": ["RAG", "reranker"], "a": 1, "b": 2})

    assert left == right


def test_reindex_does_not_duplicate_source_sub_id(db_session):
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks

    chunk = SemanticChunk(
        source_type="memory_digest",
        source_id="11",
        source_sub_id="card:0",
        title="端口冲突",
        text="8000 端口被占用",
        lexical_text="端口冲突 8000 uvicorn",
        embedding_text="端口冲突 8000 uvicorn",
        metadata={"user_id": "u1", "session_id": "s1"},
    )

    upsert_semantic_chunks(db_session, [chunk], index_version="fake:v1:v1")
    upsert_semantic_chunks(db_session, [chunk], index_version="fake:v1:v1")

    assert db_session.query(SemanticIndexItem).count() == 1
    row = db_session.query(SemanticIndexItem).one()
    assert row.source_sub_id == "card:0"
    assert row.lexical_text == "端口冲突 8000 uvicorn"
    fts_count = db_session.execute(text("SELECT COUNT(*) FROM semantic_index_fts")).scalar()
    assert fts_count == 1
