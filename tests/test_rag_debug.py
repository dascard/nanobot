import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import inspect, text


def _local_now() -> datetime:
    # GroupMemory 测试 DB fixture 保持 naive 本地墙钟时间语义。
    return datetime.now()  # noqa: DTZ005


class DebugRerankerProvider:
    def rerank(self, query, candidates, *, top_k=None):
        import time

        from core.semantic.reranker import RerankResult

        time.sleep(0.002)
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
        meta_json=json.dumps(_recallable_digest_meta(
            recall_cards=[
                {"title": "端口", "text": "RAG Debug 端口冲突排查", "keywords": ["RAG", "端口"]},
            ],
        ), ensure_ascii=False),
    )
    db_session.add(digest)
    db_session.commit()
    chunks = chunks_from_memory_digest(digest)
    upsert_semantic_chunks(db_session, chunks, index_version="fake:v1:v1")

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
    assert payload["score_breakdown"]["source_weights_mode"] == "display_only_no_quota"
    assert stages["fts_hits"][0]["bm25_raw"] is not None
    expected_candidate_id = (
        f"memory_digest:701:{chunks[0].source_sub_id}"
    )
    assert stages["merged_candidates"][0]["candidate_id"] == expected_candidate_id
    assert stages["reranker_input_pairs"][0]["candidate_id"] == expected_candidate_id
    assert stages["final_candidates"][0]["score_breakdown"]["raw_reranker"] == 2.0
    assert payload["score_breakdown"]["reranker_latency_ms"] > 0


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
        meta_json=json.dumps(_recallable_digest_meta(
            recall_cards=[
                {
                    "title": "长文本",
                    "text": "RAG Debug " + ("上下文" * 400) + secret_tail,
                    "keywords": ["RAG", "Debug"],
                },
            ],
        ), ensure_ascii=False),
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


def test_rag_debug_status_reports_empty_index_and_reranker_route(client, db_session, monkeypatch):
    import json

    from core.database import MemoryDigest
    import core.semantic.provider_factory as provider_factory

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setenv("RAG_RERANKER_ENABLED", "1")
    monkeypatch.delenv("RAG_LOCAL_RERANKER_MODEL", raising=False)
    values = {
        "rag.reranker.model_path": "./models/bge-reranker-v2-m3",
        "rag.reranker.hf_model": "",
        "rag.reranker.score_mode": "sigmoid",
        "rag.reranker.max_text_chars": 1200,
    }
    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda key, default=None: values.get(key, default),
    )
    provider_factory.get_reranker_provider.cache_clear()

    digest = MemoryDigest(
        user_id="u1",
        session_id="s1",
        digest_date="2026-05-26",
        level=2,
        content="RAG Debug 端口冲突",
        meta_json=json.dumps(_recallable_digest_meta(
            recall_cards=[
                {"title": "端口", "text": "RAG Debug 端口冲突排查"},
            ],
        ), ensure_ascii=False),
    )
    db_session.add(digest)
    db_session.commit()

    response = client.get(
        "/api/v1/admin/rag/debug/status",
        headers=_auth_header(),
        params={"source_type": "memory"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["index"]["indexed_items"] == 0
    assert data["index"]["buildable_chunks"] == 1
    assert data["index"]["needs_build"] is True
    assert data["reranker"]["configured"] is True
    assert data["reranker"]["source"] == "local_model"
    assert data["reranker"]["model"] == "BAAI/bge-reranker-v2-m3"
    assert data["reranker"]["model_path"] == "./models/bge-reranker-v2-m3"
    assert data["reranker"]["download_repo_id"] == "BAAI/bge-reranker-v2-m3"


def test_rag_debug_build_index_from_existing_data(client, db_session, monkeypatch):
    import json

    from core.database import (
        AdminAuditLog,
        MemoryDigest,
        SemanticIndexItem,
        SemanticIndexJob,
    )

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    digest = MemoryDigest(
        user_id="u1",
        session_id="s1",
        digest_date="2026-05-26",
        level=2,
        content="RAG Debug 端口冲突",
        meta_json=json.dumps(_recallable_digest_meta(
            recall_cards=[
                {"title": "端口", "text": "RAG Debug 端口冲突排查"},
            ],
        ), ensure_ascii=False),
    )
    db_session.add(digest)
    db_session.commit()

    response = client.post(
        "/api/v1/admin/rag/debug/build-index",
        headers=_auth_header(),
        json={"source_type": "memory"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["result"]["enqueued"] == 1
    assert data["index"]["indexed_items"] == 0
    assert db_session.query(SemanticIndexItem).count() == 0
    job = db_session.query(SemanticIndexJob).one()
    assert job.job_type == "replace"
    assert job.status == "pending"
    assert db_session.query(AdminAuditLog).filter_by(
        action="enqueue_semantic_index_backfill_legacy_debug",
    ).count() == 1


def test_legacy_debug_build_index_never_enqueues_orphan_delete(
    client,
    db_session,
    monkeypatch,
):
    from core.database import SemanticIndexJob
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    upsert_semantic_chunks(
        db_session,
        [SemanticChunk(
            source_type="memory_digest",
            source_id="orphan-source",
            source_sub_id="card:orphan",
            title="孤儿索引",
            text="旧 debug 构建入口不得删除该索引",
            lexical_text="孤儿索引",
            embedding_text="孤儿索引",
        )],
        index_version="legacy:v1",
        source_revision="orphan-revision",
    )

    response = client.post(
        "/api/v1/admin/rag/debug/build-index",
        headers=_auth_header(),
        json={"source_type": "memory_digest", "limit_per_source": 1},
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["orphan"] == 0
    assert db_session.query(SemanticIndexJob).filter_by(job_type="delete").count() == 0


def test_legacy_debug_build_index_honors_limit_without_full_scan(
    client,
    db_session,
    monkeypatch,
):
    from core.database import MemoryDigest, SemanticIndexJob

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    for index in range(3):
        db_session.add(MemoryDigest(
            user_id=f"u{index}",
            session_id=f"s{index}",
            digest_date="2026-07-17",
            level=2,
            content=f"有界回填 {index}",
            meta_json=json.dumps(_recallable_digest_meta(
                source_id=f"digest-source-{index}",
                summary_type="recall_card",
                recall_cards=[{
                    "type": "fact",
                    "text": f"有界回填事实 {index}",
                    "evidence_log_ids": [index + 1],
                }],
            ), ensure_ascii=False),
        ))
    db_session.commit()

    response = client.post(
        "/api/v1/admin/rag/debug/build-index",
        headers=_auth_header(),
        json={"source_type": "memory_digest", "limit_per_source": 1},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["scanned"] == 1
    assert result["enqueued"] == 1
    assert result["done"] is False
    assert result["truncated"] is True
    assert db_session.query(SemanticIndexJob).count() == 1


def test_legacy_debug_build_index_rolls_back_job_and_audit_on_preview_failure(
    client,
    db_session,
    monkeypatch,
):
    from core.database import AdminAuditLog, MemoryDigest, SemanticIndexJob

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(client._transport, "raise_server_exceptions", False)
    db_session.add(MemoryDigest(
        user_id="rollback-user",
        session_id="rollback-session",
        digest_date="2026-07-17",
        level=2,
        content="事务回滚",
        meta_json=json.dumps(_recallable_digest_meta(
            source_id="rollback-source",
            summary_type="recall_card",
            recall_cards=[{
                "type": "fact",
                "text": "preview 失败时任务与审计必须一起回滚",
                "evidence_log_ids": [1],
            }],
        ), ensure_ascii=False),
    ))
    db_session.commit()

    def broken_preview(*_args, **_kwargs):
        raise RuntimeError("preview failed")

    monkeypatch.setattr(
        "core.semantic.backfill.preview_semantic_index_backfill",
        broken_preview,
    )
    response = client.post(
        "/api/v1/admin/rag/debug/build-index",
        headers=_auth_header(),
        json={"source_type": "memory_digest", "limit_per_source": 1},
    )

    assert response.status_code == 500
    assert db_session.query(SemanticIndexJob).count() == 0
    assert db_session.query(AdminAuditLog).filter_by(
        action="enqueue_semantic_index_backfill_legacy_debug",
    ).count() == 0


def test_admin_semantic_index_job_retry_uses_status_and_updated_at_cas(
    client,
    db_session,
    monkeypatch,
):
    from core.database import AdminAuditLog, SemanticIndexJob
    from core.time_utils import db_naive_to_utc, to_db_naive

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    updated_at = _local_now()
    job = SemanticIndexJob(
        source_type="session_summary",
        source_id="retry-session",
        source_revision="revision-1",
        status="failed",
        retry_count=3,
        manual_retry_count=0,
        updated_at=updated_at,
        finished_at=updated_at,
        error="provider timeout",
    )
    db_session.add(job)
    db_session.commit()

    payload = {
        "expected_status": "failed",
        "expected_updated_at": db_naive_to_utc(updated_at).isoformat(),
        "reason": "已修复 provider，允许显式重试",
    }
    first = client.post(
        f"/api/v1/admin/rag/index-jobs/{job.id}/retry",
        headers=_auth_header(),
        json=payload,
    )
    second = client.post(
        f"/api/v1/admin/rag/index-jobs/{job.id}/retry",
        headers=_auth_header(),
        json=payload,
    )

    assert first.status_code == 200, first.text
    returned = first.json()["job"]
    assert returned["status"] == "pending"
    assert returned["manual_retry_count"] == 1
    returned_updated_at = datetime.fromisoformat(
        returned["updated_at"].replace("Z", "+00:00")
    )
    returned_next_retry_at = datetime.fromisoformat(
        returned["next_retry_at"].replace("Z", "+00:00")
    )
    assert returned_updated_at.utcoffset() == timedelta(0)
    assert returned_next_retry_at.utcoffset() == timedelta(0)
    assert "lease_token" not in returned
    assert "meta_json" not in returned
    assert second.status_code == 409
    db_session.refresh(job)
    assert job.retry_count == 3
    assert job.source_revision == "revision-1"
    assert job.locked_by == ""
    assert job.lease_token == ""
    assert to_db_naive(returned_updated_at) == job.updated_at
    assert to_db_naive(returned_next_retry_at) == job.next_retry_at
    audit = db_session.query(AdminAuditLog).filter_by(
        action="retry_semantic_index_job",
    ).one()
    assert json.loads(audit.detail_json)["expected_updated_at"].endswith("Z")


def test_admin_semantic_index_job_retry_rejects_active_lease_and_missing_job(
    client,
    db_session,
    monkeypatch,
):
    from core.database import SemanticIndexJob
    from core.time_utils import db_naive_to_utc

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    updated_at = _local_now()
    job = SemanticIndexJob(
        source_type="memory_digest",
        source_id="running-source",
        source_revision="revision-running",
        status="running",
        locked_by="worker-a",
        lease_token="a" * 64,
        lease_expires_at=updated_at + timedelta(minutes=5),
        updated_at=updated_at,
    )
    db_session.add(job)
    db_session.commit()
    payload = {
        "expected_status": "running",
        "expected_updated_at": db_naive_to_utc(updated_at).isoformat(),
        "reason": "尝试抢占仍有效租约",
    }

    active = client.post(
        f"/api/v1/admin/rag/index-jobs/{job.id}/retry",
        headers=_auth_header(),
        json=payload,
    )
    missing = client.post(
        "/api/v1/admin/rag/index-jobs/999999/retry",
        headers=_auth_header(),
        json={**payload, "expected_status": "failed"},
    )

    assert active.status_code == 409
    assert missing.status_code == 404


def test_admin_semantic_index_job_retry_rejects_naive_expected_time(
    client,
    db_session,
    monkeypatch,
):
    from core.database import SemanticIndexJob

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    updated_at = _local_now()
    job = SemanticIndexJob(
        source_type="session_summary",
        source_id="naive-time-retry",
        source_revision="revision-naive",
        status="failed",
        updated_at=updated_at,
        finished_at=updated_at,
    )
    db_session.add(job)
    db_session.commit()

    response = client.post(
        f"/api/v1/admin/rag/index-jobs/{job.id}/retry",
        headers=_auth_header(),
        json={
            "expected_status": "failed",
            "expected_updated_at": updated_at.isoformat(),
            "reason": "无时区时间必须拒绝",
        },
    )

    assert response.status_code == 422
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.manual_retry_count == 0


def test_admin_semantic_backfill_preview_is_readonly_and_enqueue_is_audited(
    client,
    db_session,
    monkeypatch,
):
    import json

    from core.database import AdminAuditLog, RollingSessionSummary, SemanticIndexJob

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_session.add(RollingSessionSummary(
        session_id="admin-backfill-session",
        user_id="u1",
        status="active",
        summary_kind="llm_episode",
        summary_text="管理端回填只允许正式入队。",
        summary_json=json.dumps({
            "summary": "管理端回填只允许正式入队。",
        }, ensure_ascii=False),
    ))
    db_session.commit()
    body = {
        "source_type": "session_summary",
        "limit": 10,
        "cursor": "",
        "index_version": "target:v2",
    }

    preview = client.post(
        "/api/v1/admin/rag/index-backfill/preview",
        headers=_auth_header(),
        json=body,
    )

    assert preview.status_code == 200, preview.text
    assert set(preview.json()) == {
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
    assert preview.json()["missing"] == 1
    assert preview.json()["enqueued"] == 0
    assert db_session.query(SemanticIndexJob).count() == 0
    assert db_session.query(AdminAuditLog).count() == 0

    enqueue = client.post(
        "/api/v1/admin/rag/index-backfill/enqueue",
        headers=_auth_header(),
        json=body,
    )

    assert enqueue.status_code == 200, enqueue.text
    assert enqueue.json()["enqueued"] == 1
    job = db_session.query(SemanticIndexJob).one()
    assert job.status == "pending"
    assert job.job_type == "replace"
    assert job.source_id == "admin-backfill-session"
    assert "管理端回填" not in job.meta_json
    assert db_session.query(AdminAuditLog).filter_by(
        action="enqueue_semantic_index_backfill",
    ).count() == 1


def test_rag_debug_query_runs_sticker_search(client, db_session, monkeypatch):
    import json

    from core.database import StickerMemory
    from core.semantic.adapters import chunk_from_sticker
    from core.semantic.indexer import upsert_semantic_chunks
    import core.semantic.provider_factory as provider_factory

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(provider_factory, "get_reranker_provider", lambda: DebugRerankerProvider())
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
            "query": "surprised",
            "limit": 3,
            "filters": {"group_id": "123"},
        },
    )

    assert response.status_code == 200
    data = response.json()
    payload = data["response"]
    stages = payload["stages"]
    assert payload["candidates"][0]["reply_token"] == f"[sticker:{sticker.id}]"
    assert payload["candidates"][0]["source_type"] == "sticker"
    assert payload["score_breakdown"]["fallback_reason"] == ""
    assert stages["fts_hits"][0]["bm25_raw"] is not None
    assert stages["hard_gate"][0]["replyable"] is True
    assert stages["hard_gate"][0]["passed"] is True
    assert stages["reranker_input_pairs"][0]["candidate_id"] == f"sticker:{sticker.id}:sticker"
    assert stages["final_candidates"][0]["score_breakdown"]["raw_reranker"] == 2.0


def test_rag_debug_query_runs_knowledge_search_with_citation(client, db_session, monkeypatch):
    from core.database import KnowledgeChunk
    from core.knowledge_library import create_manual_document
    from core.semantic.adapters import chunk_from_knowledge_chunk
    from core.semantic.indexer import upsert_semantic_chunks
    import core.semantic.provider_factory as provider_factory

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(provider_factory, "get_reranker_provider", lambda: DebugRerankerProvider())
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
    payload = response.json()["response"]
    stages = payload["stages"]
    candidate = payload["candidates"][0]
    assert candidate["source_type"] == "knowledge"
    assert candidate["citation"]["title"] == "Debug 知识"
    assert candidate["citation"]["trust_level"] == "medium"
    assert stages["fts_hits"][0]["bm25_raw"] is not None
    assert stages["reranker_input_pairs"][0]["candidate_id"] == candidate["candidate_id"]
    assert stages["final_candidates"][0]["score_breakdown"]["raw_reranker"] == 2.0
    assert "no_citation" in stages["skipped"]


def test_rag_debug_group_analysis_exposes_bundle_trace(client, monkeypatch):
    import core.semantic.provider_factory as provider_factory

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(provider_factory, "get_reranker_provider", lambda: DebugRerankerProvider())
    messages = [
        {"log_id": 1, "time": "10:00", "user_id": "u1", "content": "本地模型部署需要检查显存"},
        {"log_id": 2, "time": "10:01", "user_id": "u2", "content": "量化参数可以先试 q4_k_m"},
        {"log_id": 3, "time": "10:02", "user_id": "u3", "content": "今天午饭吃什么"},
    ]

    response = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={
            "source_type": "group_analysis",
            "query": "本地模型部署量化",
            "filters": {
                "messages": messages,
                "bundle_size": 1,
                "neighbor_radius": 1,
                "reranker_top_k": 2,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()["response"]
    stages = payload["stages"]
    assert stages["fts_hits"][0]["candidate_id"].startswith("bundle:")
    assert stages["reranker_input_pairs"][0]["candidate_id"].startswith("bundle:")
    assert stages["prompt_logs"]["neighbor_expansion"][0]["selected_indexes"]
    assert stages["final_candidates"][0]["candidate_id"].startswith("bundle:")


def test_rag_debug_group_analysis_loads_real_group_logs(
    client,
    db_session,
    monkeypatch,
):
    from core.database import ChatLog
    import core.semantic.provider_factory as provider_factory

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(
        provider_factory,
        "get_reranker_provider",
        lambda: DebugRerankerProvider(),
    )
    now = _local_now()
    db_session.add_all([
        ChatLog(
            user_id="group_4242",
            session_id="group_4242",
            sender_name="甲",
            role="ambient",
            content="本地模型部署需要先检查显存容量",
            created_at=now - timedelta(minutes=3),
        ),
        ChatLog(
            user_id="group_4242",
            session_id="group_4242",
            sender_name="乙",
            role="ambient",
            content="量化参数可以从 q4_k_m 开始测试",
            created_at=now - timedelta(minutes=2),
        ),
        ChatLog(
            user_id="group_4242",
            session_id="group_4242",
            sender_name="丙",
            role="ambient",
            content="部署后还要验证真实推理延迟",
            created_at=now - timedelta(minutes=1),
        ),
    ])
    db_session.commit()

    response = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={
            "source_type": "group_analysis",
            "query": "本地模型部署量化",
            "filters": {
                "group_id": "4242",
                "window_hours": 24,
                "message_limit": 100,
                "bundle_size": 1,
            },
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()["response"]
    assert payload["context"]["group_id"] == "group_4242"
    assert payload["context"]["message_source"] == "database"
    assert payload["stages"]["stats_logs"]["total_messages"] == 3
    assert payload["stages"]["final_candidates"]


def test_rag_debug_group_sources_require_group_context(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(client._transport, "raise_server_exceptions", False)

    for source_type in ("group_memory", "group_analysis"):
        response = client.post(
            "/api/v1/admin/rag/debug/query",
            headers=_auth_header(),
            json={"source_type": source_type, "query": "检查群上下文"},
        )

        assert response.status_code == 422, (source_type, response.text)


def test_rag_debug_rejects_unknown_source_type(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={"source_type": "unknown_source", "query": "不应进入 stub"},
    )

    assert response.status_code == 422, response.text


def test_rag_debug_all_aggregates_real_source_results(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    def fake_response(source_type):
        candidate = {
            "candidate_id": f"{source_type}:1",
            "source_type": source_type,
            "title": source_type,
            "text": source_type,
            "final_score": 0.8,
        }
        return {
            "query": "聚合测试",
            "source_type": source_type,
            "stages": {
                "reranker_input_pairs": [],
                "final_candidates": [candidate],
            },
            "score_breakdown": {
                "degraded": False,
                "fallback_reason": "",
                "latency_ms": 1,
            },
            "candidates": [candidate],
        }

    monkeypatch.setattr(
        "api.admin.rag_routes._build_memory_debug_response",
        lambda body, db, latency_ms: fake_response("memory"),
    )
    monkeypatch.setattr(
        "api.admin.rag_routes._build_group_memory_debug_response",
        lambda body, db, latency_ms: fake_response("group_memory"),
    )
    monkeypatch.setattr(
        "api.admin.rag_routes._build_sticker_debug_response",
        lambda body, db, latency_ms: fake_response("sticker"),
    )
    monkeypatch.setattr(
        "api.admin.rag_routes._build_knowledge_debug_response",
        lambda body, db, latency_ms: fake_response("knowledge"),
    )
    monkeypatch.setattr(
        "api.admin.rag_routes._build_group_analysis_debug_response",
        lambda body, db, latency_ms: fake_response("group_analysis"),
    )

    response = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={
            "source_type": "all",
            "query": "聚合测试",
            "limit": 10,
            "filters": {"group_id": "4242"},
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()["response"]
    assert payload["score_breakdown"]["fallback_reason"] != "rag_debug_stub"
    assert payload["score_breakdown"]["overall_status"] == "passed"
    assert set(payload["source_results"]) == {
        "memory",
        "group_memory",
        "sticker",
        "knowledge",
        "group_analysis",
    }
    assert {item["source_type"] for item in payload["candidates"]} == {
        "memory",
        "group_memory",
        "sticker",
        "knowledge",
        "group_analysis",
    }


def test_rag_debug_group_memory_uses_retrieval_service_not_stub(client, db_session, monkeypatch):
    from core.database import GroupMemory

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    content = "群里经常讨论本地模型部署和 RAG reranker。"
    reviewed_at = _local_now()
    row = GroupMemory(
        group_id="group_1097666427",
        memory_type="topic",
        content=content,
        content_hash="rag-debug-group-memory",
        confidence=0.86,
        evidence_count=3,
        evidence_log_ids_json="[1, 2, 3]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        last_seen=reviewed_at,
        approval_source="human",
        governance_mode="human_managed",
        approved_content_hash=hashlib.sha256(
            f"{content}\0".encode("utf-8")
        ).hexdigest(),
        human_reviewer_id="rag-debug-reviewer",
        human_reviewed_at=reviewed_at,
        human_action="accept",
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
