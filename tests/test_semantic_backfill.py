import json

import pytest
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
                "schema_version": 2,
                "status": "active",
                "generator": "llm",
                "llm_status": "success",
                "quality": {"score": 0.9, "issues": []},
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


def test_legacy_build_helper_only_enqueues_memory_digest_job(db_session):
    from core.database import MemoryDigest, SemanticIndexItem, SemanticIndexJob
    from core.semantic.backfill import build_semantic_index_from_existing_data

    digest = MemoryDigest(
        user_id="u1",
        session_id="s1",
        digest_date="2026-05-26",
        level=2,
            content="端口冲突排查",
            meta_json=json.dumps({
                "schema_version": 2,
                "status": "active",
                "generator": "llm",
                "llm_status": "success",
                "quality": {"score": 0.9, "issues": []},
                "recall_cards": [
                {"title": "端口", "text": "8000 端口被占用", "keywords": ["uvicorn"]},
            ],
        }, ensure_ascii=False),
    )
    db_session.add(digest)
    db_session.commit()

    result = build_semantic_index_from_existing_data(db_session, source_type="memory")

    assert result["enqueued"] == 1
    assert result["indexed_chunks"] == 0
    assert db_session.query(SemanticIndexItem).count() == 0
    job = db_session.query(SemanticIndexJob).one()
    assert job.source_type == "memory_digest"
    assert job.job_type == "replace"
    assert job.status == "pending"
    assert db_session.execute(text("SELECT COUNT(*) FROM semantic_index_fts")).scalar() == 0


def test_legacy_build_helper_enqueues_sticker_and_knowledge(db_session):
    from core.database import (
        KnowledgeChunk,
        KnowledgeDocument,
        SemanticIndexItem,
        SemanticIndexJob,
        StickerMemory,
    )
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
    assert result["enqueued"] >= 2
    assert result["indexed_chunks"] == 0
    assert db_session.query(SemanticIndexItem).count() == 0
    assert {row.source_type for row in db_session.query(SemanticIndexJob).all()} >= {
        "sticker",
        "knowledge",
    }


def test_backfill_cursor_uses_high_water_without_duplicates(db_session):
    from core.database import RollingSessionSummary
    from core.semantic.backfill import preview_semantic_index_backfill_page

    for index in range(5):
        db_session.add(RollingSessionSummary(
            session_id=f"cursor-session-{index}",
            user_id="u1",
            status="active",
            summary_kind="llm_episode",
            summary_text=f"游标摘要 {index}",
            summary_json=json.dumps({"summary": f"游标摘要 {index}"}, ensure_ascii=False),
            stable_hash=f"cursor-revision-{index}",
        ))
    db_session.commit()

    seen = []
    cursor = ""
    inserted_id = None
    while True:
        page = preview_semantic_index_backfill_page(
            db_session,
            source_type="session_summary",
            limit=2,
            cursor=cursor,
            index_version="target:v2",
        )
        assert set(page) == {
            "scanned",
            "current",
            "missing",
            "stale",
            "orphan",
            "enqueued",
            "next_cursor",
            "done",
            "reasons",
        }
        seen.extend(
            item["source_id"]
            for item in page["reasons"]
            if item["source_type"] == "session_summary"
            and item["category"] != "orphan"
        )
        if inserted_id is None:
            inserted = RollingSessionSummary(
                session_id="cursor-session-inserted-late",
                user_id="u1",
                status="active",
                summary_kind="llm_episode",
                summary_text="第一页后新增",
                summary_json=json.dumps({"summary": "第一页后新增"}, ensure_ascii=False),
                stable_hash="cursor-revision-late",
            )
            db_session.add(inserted)
            db_session.commit()
            inserted_id = inserted.id
        if page["done"]:
            break
        cursor = page["next_cursor"]

    assert seen == [f"cursor-session-{index}" for index in range(5)]
    assert len(seen) == len(set(seen))
    assert "cursor-session-inserted-late" not in seen


def test_backfill_page_adapts_only_limited_session_sources(
    db_session,
    monkeypatch,
):
    from core.database import RollingSessionSummary
    from core.semantic import backfill

    for index in range(100):
        db_session.add(RollingSessionSummary(
            session_id=f"bounded-session-{index:03d}",
            status="active",
            summary_kind="llm_episode",
            summary_text=f"有界摘要 {index}",
            summary_json=json.dumps({"summary": f"有界摘要 {index}"}),
            stable_hash=f"bounded-stable-{index}",
        ))
    db_session.commit()
    calls = []
    original = backfill.chunks_from_session_summary

    def recording_adapter(row):
        calls.append(int(row.id))
        return original(row)

    monkeypatch.setattr(backfill, "chunks_from_session_summary", recording_adapter)

    page = backfill.preview_semantic_index_backfill_page(
        db_session,
        source_type="session_summary",
        limit=2,
        index_version="target:v2",
    )

    assert page["scanned"] == 2
    assert len(calls) == 2


def test_memory_digest_logical_source_is_not_split_across_pages(db_session):
    from core.database import MemoryDigest
    from core.semantic.backfill import preview_semantic_index_backfill_page

    def digest(source_id: str, text_value: str, evidence_id: int):
        return MemoryDigest(
            user_id="u1",
            session_id="s1",
            digest_date="2026-07-17",
            level=2,
            content=text_value,
            meta_json=json.dumps({
                "schema_version": 2,
                "status": "active",
                "generator": "llm",
                "llm_status": "success",
                "quality": {"score": 0.9, "issues": []},
                "source_id": source_id,
                "recall_cards": [{
                    "type": "fact",
                    "text": text_value,
                    "evidence_log_ids": [evidence_id],
                }],
            }, ensure_ascii=False),
        )

    first_a = digest("logical-a", "A 的第一张卡", 1)
    only_b = digest("logical-b", "B 的卡", 2)
    second_a = digest("logical-a", "A 的第二张卡", 3)
    db_session.add_all([first_a, only_b, second_a])
    db_session.commit()

    first = preview_semantic_index_backfill_page(
        db_session,
        source_type="memory_digest",
        limit=1,
        index_version="target:v2",
    )
    second = preview_semantic_index_backfill_page(
        db_session,
        source_type="memory_digest",
        limit=1,
        cursor=first["next_cursor"],
        index_version="target:v2",
    )

    assert first["reasons"][0]["source_id"] == "logical-a"
    assert first["reasons"][0]["document_ids"] == [first_a.id, second_a.id]
    assert first["reasons"][0]["expected_chunk_count"] == 2
    assert second["reasons"][0]["source_id"] == "logical-b"


def test_orphan_stage_uses_captured_business_high_water(db_session):
    from core.database import GroupMemory
    from core.semantic.adapters import SemanticChunk
    from core.semantic.backfill import preview_semantic_index_backfill_page
    from core.semantic.indexer import upsert_semantic_chunks

    upsert_semantic_chunks(
        db_session,
        [SemanticChunk(
            source_type="group_memory",
            source_id="1",
            source_sub_id="memory",
            title="快照孤儿",
            text="本轮开始时不存在业务源",
            lexical_text="快照孤儿",
            embedding_text="快照孤儿",
        )],
        index_version="target:v2",
        source_revision="orphan-before-business",
    )
    first = preview_semantic_index_backfill_page(
        db_session,
        source_type="group_memory",
        limit=10,
        index_version="target:v2",
    )
    late = GroupMemory(
        id=1,
        group_id="g1",
        memory_type="topic",
        content="第一页之后新增的业务源",
        content_hash="late-business",
        status="active",
    )
    db_session.add(late)
    db_session.commit()

    orphan_page = preview_semantic_index_backfill_page(
        db_session,
        source_type="group_memory",
        limit=10,
        cursor=first["next_cursor"],
        index_version="target:v2",
    )

    assert orphan_page["orphan"] == 1
    assert orphan_page["reasons"][0]["source_id"] == "1"


def test_backfill_cursor_rejects_index_or_adapter_drift(db_session):
    from core.database import RollingSessionSummary
    from core.semantic.backfill import preview_semantic_index_backfill_page

    for index in range(2):
        db_session.add(RollingSessionSummary(
            session_id=f"drift-session-{index}",
            status="active",
            summary_text=f"drift {index}",
            summary_json=json.dumps({"summary": f"drift {index}"}),
        ))
    db_session.commit()
    first = preview_semantic_index_backfill_page(
        db_session,
        source_type="session_summary",
        limit=1,
        index_version="target:v2",
    )

    with pytest.raises(ValueError, match="cursor_index_version_mismatch"):
        preview_semantic_index_backfill_page(
            db_session,
            source_type="session_summary",
            limit=1,
            cursor=first["next_cursor"],
            index_version="target:v3",
        )
    with pytest.raises(ValueError, match="cursor_adapter_manifest_mismatch"):
        preview_semantic_index_backfill_page(
            db_session,
            source_type="session_summary",
            limit=1,
            cursor=first["next_cursor"],
            index_version="target:v2",
            adapter_manifest="different-manifest",
        )


def test_backfill_cursor_rejects_tampered_business_high_water(db_session):
    import base64

    from core.database import GroupMemory, SemanticIndexJob
    from core.semantic.adapters import chunk_from_group_memory
    from core.semantic.backfill import (
        enqueue_semantic_index_backfill,
        preview_semantic_index_backfill_page,
    )
    from core.semantic.indexer import (
        source_revision_for_chunks,
        upsert_semantic_chunks,
    )

    memory = GroupMemory(
        id=1,
        group_id="cursor-integrity-group",
        memory_type="topic",
        content="仍然有效的群记忆",
        content_hash="cursor-integrity-memory",
        status="active",
    )
    db_session.add(memory)
    db_session.commit()
    chunk = chunk_from_group_memory(memory)
    upsert_semantic_chunks(
        db_session,
        [chunk],
        index_version="target:v2",
        source_revision=source_revision_for_chunks(
            [chunk],
            document_ids=[memory.id],
        ),
    )

    first = preview_semantic_index_backfill_page(
        db_session,
        source_type="group_memory",
        limit=10,
        index_version="target:v2",
    )
    assert first["next_cursor"]
    parts = first["next_cursor"].split(".", 1)
    payload_part = parts[0]
    padding = "=" * (-len(payload_part) % 4)
    payload = json.loads(
        base64.urlsafe_b64decode(payload_part + padding).decode("utf-8")
    )
    payload["high_waters"]["group_memory"] = 0
    tampered_payload = base64.urlsafe_b64encode(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).decode("ascii").rstrip("=")
    tampered_cursor = (
        f"{tampered_payload}.{parts[1]}"
        if len(parts) == 2
        else tampered_payload
    )

    with pytest.raises(ValueError, match="invalid_backfill_cursor_signature"):
        enqueue_semantic_index_backfill(
            db_session,
            source_type="group_memory",
            limit=10,
            cursor=tampered_cursor,
            index_version="target:v2",
        )

    assert db_session.query(SemanticIndexJob).count() == 0


def test_backfill_prefers_llm_summary_over_newer_fallback_at_equal_coverage(
    db_session,
):
    from core.database import RollingSessionSummary
    from core.semantic.adapters import session_summary_source_revision
    from core.semantic.backfill import preview_semantic_index_backfill_page

    llm = RollingSessionSummary(
        session_id="equal-coverage-session",
        status="active",
        summary_kind="llm_episode",
        summary_text="LLM 摘要",
        summary_json=json.dumps({"summary": "LLM 摘要"}, ensure_ascii=False),
        covered_from_turn_id=1,
        covered_until_turn_id=20,
        stable_hash="llm-stable-hash",
    )
    db_session.add(llm)
    db_session.flush()
    fallback = RollingSessionSummary(
        session_id=llm.session_id,
        status="active",
        summary_kind="deterministic_fallback",
        summary_text="较晚创建的 fallback",
        summary_json=json.dumps({"summary": "较晚创建的 fallback"}, ensure_ascii=False),
        covered_from_turn_id=1,
        covered_until_turn_id=20,
        stable_hash="fallback-stable-hash",
    )
    db_session.add(fallback)
    db_session.commit()

    page = preview_semantic_index_backfill_page(
        db_session,
        source_type="session_summary",
        limit=10,
        index_version="target:v2",
    )

    record = next(
        item for item in page["reasons"]
        if item["source_id"] == llm.session_id
    )
    assert record["document_ids"] == [llm.id]
    assert record["source_revision"] == session_summary_source_revision(llm)


def test_backfill_classifies_current_missing_stale_and_orphan(db_session):
    from core.database import RollingSessionSummary, SemanticIndexJob
    from core.semantic.adapters import SemanticChunk, chunks_from_session_summary
    from core.semantic.backfill import preview_semantic_index_backfill_page
    from core.semantic.indexer import upsert_semantic_chunks

    current = RollingSessionSummary(
        session_id="classification-current",
        status="active",
        summary_text="当前摘要",
        summary_json=json.dumps({"summary": "当前摘要"}, ensure_ascii=False),
    )
    missing = RollingSessionSummary(
        session_id="classification-missing",
        status="active",
        summary_text="缺失摘要",
        summary_json=json.dumps({"summary": "缺失摘要"}, ensure_ascii=False),
    )
    stale = RollingSessionSummary(
        session_id="classification-stale",
        status="active",
        summary_text="新版摘要",
        summary_json=json.dumps({"summary": "新版摘要"}, ensure_ascii=False),
    )
    db_session.add_all([current, missing, stale])
    db_session.commit()
    current_chunks = chunks_from_session_summary(current)
    stale_chunks = chunks_from_session_summary(stale)
    upsert_semantic_chunks(
        db_session,
        current_chunks,
        index_version="target:v2",
        source_revision=current_chunks[0].metadata["source_revision"],
        embedding_enabled=True,
    )
    upsert_semantic_chunks(
        db_session,
        stale_chunks,
        index_version="target:v2",
        source_revision="outdated-revision",
    )
    upsert_semantic_chunks(
        db_session,
        [SemanticChunk(
            source_type="session_summary",
            source_id="classification-orphan",
            source_sub_id="section:summary",
            title="孤儿",
            text="孤儿索引",
            lexical_text="孤儿索引",
            embedding_text="孤儿索引",
        )],
        index_version="target:v2",
        source_revision="orphan-revision",
    )
    job_count_before = db_session.query(SemanticIndexJob).count()

    totals = {"current": 0, "missing": 0, "stale": 0, "orphan": 0}
    records = []
    cursor = ""
    while True:
        page = preview_semantic_index_backfill_page(
            db_session,
            source_type="session_summary",
            limit=10,
            cursor=cursor,
            index_version="target:v2",
        )
        for key in totals:
            totals[key] += page[key]
        records.extend(page["reasons"])
        if page["done"]:
            break
        cursor = page["next_cursor"]

    assert totals == {"current": 1, "missing": 1, "stale": 1, "orphan": 1}
    by_id = {item["source_id"]: item for item in records}
    assert by_id["classification-current"]["category"] == "current"
    assert "embedding_incomplete" in by_id["classification-current"]["reasons"]
    assert by_id["classification-missing"]["category"] == "missing"
    assert by_id["classification-stale"]["category"] == "stale"
    assert by_id["classification-orphan"]["category"] == "orphan"
    assert db_session.query(SemanticIndexJob).count() == job_count_before


def test_backfill_marks_source_hash_mismatch_as_stale(db_session):
    from core.database import RollingSessionSummary, SemanticIndexItem
    from core.semantic.adapters import chunks_from_session_summary
    from core.semantic.backfill import preview_semantic_index_backfill_page
    from core.semantic.indexer import upsert_semantic_chunks

    summary = RollingSessionSummary(
        session_id="source-hash-mismatch",
        status="active",
        summary_kind="llm_episode",
        summary_text="内容 hash 必须精确比较",
        summary_json=json.dumps({
            "summary": "内容 hash 必须精确比较",
        }, ensure_ascii=False),
        stable_hash="source-hash-stable",
    )
    db_session.add(summary)
    db_session.commit()
    chunks = chunks_from_session_summary(summary)
    upsert_semantic_chunks(
        db_session,
        chunks,
        index_version="target:v2",
        source_revision=chunks[0].metadata["source_revision"],
    )
    item = db_session.query(SemanticIndexItem).one()
    item.source_hash = "tampered-source-hash"
    db_session.commit()

    page = preview_semantic_index_backfill_page(
        db_session,
        source_type="session_summary",
        limit=10,
        index_version="target:v2",
    )

    record = next(
        item for item in page["reasons"]
        if item["source_id"] == summary.session_id
    )
    assert record["category"] == "stale"
    assert "source_hash_mismatch" in record["reasons"]


def test_backfill_detects_memory_digest_visibility_policy_change(db_session):
    from dataclasses import replace

    from core.database import MemoryDigest, SemanticIndexItem
    from core.semantic.adapters import chunks_from_memory_digest
    from core.semantic.backfill import preview_semantic_index_backfill_page
    from core.semantic.indexer import upsert_semantic_chunks

    digest = MemoryDigest(
        user_id="visibility-user",
        session_id="visibility-session",
        digest_date="2026-07-17",
        level=0,
        content="L0 详细内容只能用于展开",
        meta_json=json.dumps({
            "schema_version": 2,
            "status": "active",
            "generator": "llm",
            "llm_status": "success",
            "quality": {"score": 0.9, "issues": []},
            "source_id": "visibility-source",
        }, ensure_ascii=False),
    )
    db_session.add(digest)
    db_session.commit()
    expected_chunk = chunks_from_memory_digest(digest)[0]
    legacy_chunk = replace(expected_chunk, visibility="recall")
    upsert_semantic_chunks(
        db_session,
        [legacy_chunk],
        index_version="target:v2",
        source_revision=expected_chunk.metadata["source_revision"],
    )

    page = preview_semantic_index_backfill_page(
        db_session,
        source_type="memory_digest",
        limit=10,
        index_version="target:v2",
    )

    record = next(
        item for item in page["reasons"]
        if item["source_id"] == "visibility-source"
    )
    assert db_session.query(SemanticIndexItem).one().visibility == "recall"
    assert record["category"] == "stale"
    assert "source_hash_mismatch" in record["reasons"]


def test_knowledge_backfill_worker_filters_archived_chunks_and_converges(
    db_session,
):
    from core.database import (
        KnowledgeChunk,
        KnowledgeDocument,
        SemanticIndexItem,
    )
    from core.semantic.backfill import (
        enqueue_semantic_index_backfill,
        preview_semantic_index_backfill_page,
    )
    from core.semantic.jobs import claim_next_job
    from workers.semantic_index_worker import (
        _default_chunk_loader,
        process_semantic_index_job,
    )

    document = KnowledgeDocument(
        document_kind="manual_markdown",
        title="知识过滤合同",
        domain="ops",
        status="active",
        trust_level="high",
    )
    db_session.add(document)
    db_session.commit()
    active_chunk = KnowledgeChunk(
        document_id=document.id,
        chunk_id="active-chunk",
        order_index=0,
        title="有效内容",
        text="这条内容应进入索引。",
        status="active",
        trust_level="high",
    )
    archived_chunk = KnowledgeChunk(
        document_id=document.id,
        chunk_id="archived-chunk",
        order_index=1,
        title="已归档内容",
        text="这条内容不得重新暴露。",
        status="archived",
        trust_level="high",
    )
    db_session.add_all([active_chunk, archived_chunk])
    db_session.commit()

    enqueued = enqueue_semantic_index_backfill(
        db_session,
        source_type="knowledge",
        limit=10,
        index_version="target:v2",
    )
    assert enqueued["enqueued"] == 1
    db_session.commit()
    claimed = claim_next_job(db_session, worker_id="knowledge-worker")
    processed = process_semantic_index_job(
        db_session,
        claimed,
        chunk_loader=_default_chunk_loader(db_session),
    )

    assert processed is not None
    assert processed.status == "done"
    active_items = db_session.query(SemanticIndexItem).filter(
        SemanticIndexItem.source_type == "knowledge",
        SemanticIndexItem.source_id == str(document.id),
        SemanticIndexItem.status == "active",
    ).all()
    assert [item.source_sub_id for item in active_items] == ["active-chunk"]

    preview = preview_semantic_index_backfill_page(
        db_session,
        source_type="knowledge",
        limit=10,
        index_version="target:v2",
    )
    record = next(
        item for item in preview["reasons"]
        if item["source_id"] == str(document.id)
    )
    assert record["category"] == "current"
    assert record["reasons"] == []
