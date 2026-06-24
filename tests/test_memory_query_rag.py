import json
from datetime import datetime, timedelta

from tests.async_helpers import run_async

from core.database import ChatLog, MemoryDigest, RollingSessionSummary, SemanticIndexItem
from core.semantic.adapters import (
    chunks_from_memory_digest,
    chunks_from_session_summary,
)
from core.semantic.indexer import upsert_semantic_chunks


def _local_now() -> datetime:
    # SQLite ORM DateTime fixture 保持 naive 本地墙钟时间语义。
    return datetime.now()  # noqa: DTZ005


class KeywordEmbeddingProvider:
    def embed(self, texts):
        vectors = []
        for text in texts:
            value = str(text)
            if any(token in value for token in ("端口", "部署", "uvicorn")):
                vectors.append([1.0, 0.0, 0.0])
            elif any(token in value for token in ("模型", "路由")):
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


class FixedRerankerProvider:
    def __init__(self, scores):
        self.scores = scores

    def rerank(self, query, candidates, *, top_k=None):
        from core.semantic.reranker import RerankResult

        limited = candidates[:top_k] if top_k else candidates
        results = [
            RerankResult(
                candidate_id=candidate.candidate_id,
                raw_score=self.scores.get(candidate.candidate_id, 0.0),
                score=self.scores.get(candidate.candidate_id, 0.0),
                model="fixed-reranker",
                score_mode="identity",
            )
            for candidate in limited
        ]
        return sorted(results, key=lambda item: item.score or 0.0, reverse=True)


class RecordingRerankerProvider(FixedRerankerProvider):
    def __init__(self, scores):
        super().__init__(scores)
        self.seen_candidate_ids = []

    def rerank(self, query, candidates, *, top_k=None):
        self.seen_candidate_ids = [candidate.candidate_id for candidate in candidates]
        return super().rerank(query, candidates, top_k=top_k)


def _digest_row(digest_id, *, cards):
    return MemoryDigest(
        id=digest_id,
        user_id="u1",
        session_id="s1",
        digest_date="2026-05-26",
        level=2,
        content="; ".join(card["text"] for card in cards),
        meta_json=json.dumps({"schema_version": 2, "status": "active", "recall_cards": cards}, ensure_ascii=False),
    )


def _index_chunks(db, chunks):
    embeddings = {
        chunk.source_sub_id: json.dumps(KeywordEmbeddingProvider().embed([chunk.embedding_text])[0]).encode("utf-8")
        for chunk in chunks
    }
    return upsert_semantic_chunks(
        db,
        chunks,
        index_version="fake:v1:v1",
        embedding_model="fake",
        embeddings=embeddings,
    )


def test_memory_query_uses_reranker_after_recall(db_session):
    from core.memory_rag import MemoryRagService

    digest = _digest_row(101, cards=[
        {"title": "端口", "text": "uvicorn 8000 端口冲突。", "keywords": ["端口"]},
        {"title": "模型", "text": "模型路由按价格排序。", "keywords": ["模型"]},
    ])
    chunks = chunks_from_memory_digest(digest)
    _index_chunks(db_session, chunks)

    service = MemoryRagService(
        db_session,
        embedding_provider=KeywordEmbeddingProvider(),
        reranker_provider=FixedRerankerProvider({
            "memory_digest:101:card:0": 0.2,
            "memory_digest:101:card:1": 0.9,
        }),
    )
    result = service.query("端口 模型", source="digest", limit=5)

    assert result["degraded"] is False
    assert result["items"][0]["matched_cards"][0]["source_sub_id"] == "card:1"
    assert result["items"][0]["score_breakdown"]["best_card"]["reranker"] == 0.9


def test_memory_query_score_breakdown_uses_index_recency(db_session):
    from core.memory_rag import MemoryRagService

    now = _local_now()
    digest = _digest_row(106, cards=[
        {"title": "旧端口", "text": "端口冲突 recency 旧记录。", "keywords": ["端口"]},
        {"title": "新端口", "text": "端口冲突 recency 新记录。", "keywords": ["端口"]},
    ])
    _index_chunks(db_session, chunks_from_memory_digest(digest))
    rows = db_session.query(SemanticIndexItem).filter(
        SemanticIndexItem.source_type == "memory_digest",
        SemanticIndexItem.source_id == "106",
    ).all()
    for row in rows:
        row.source_updated_at = now if row.source_sub_id == "card:1" else now - timedelta(days=90)
    db_session.commit()

    service = MemoryRagService(
        db_session,
        embedding_provider=KeywordEmbeddingProvider(),
        reranker_provider=FixedRerankerProvider({
            "memory_digest:106:card:0": 0.9,
            "memory_digest:106:card:1": 0.9,
        }),
    )
    result = service.query("端口 recency", source="digest", limit=5)
    cards = {
        card["source_sub_id"]: card
        for card in result["items"][0]["matched_cards"]
    }

    assert cards["card:1"]["score_breakdown"]["recency"] > cards["card:0"]["score_breakdown"]["recency"]


def test_memory_query_debug_contract_keys(db_session):
    from core.memory_rag import MemoryRagService

    digest = _digest_row(601, cards=[
        {"title": "端口预算", "text": "KohakuVQ 端口预算需要固定。", "keywords": ["KohakuVQ", "端口", "预算"]},
    ])
    _index_chunks(db_session, chunks_from_memory_digest(digest))

    service = MemoryRagService(
        db_session,
        embedding_provider=KeywordEmbeddingProvider(),
        reranker_provider=FixedRerankerProvider({"memory_digest:601:card:0": 0.9}),
    )
    result = service.query("KohakuVQ 端口预算", source="digest", limit=5, include_debug=True)

    assert set(result) == {"query", "source", "degraded", "fallback_reason", "stats", "items", "debug_trace"}
    assert set(result["stats"]) == {
        "fts_candidates",
        "vector_candidates",
        "lexical_candidates",
        "embedding_candidates",
        "merged_candidates",
        "reranker_candidates",
        "reranker_latency_ms",
        "final_items",
    }
    assert set(result["debug_trace"]) >= {
        "sql_filters",
        "fts_hits",
        "vector_hits",
        "embedding_hits",
        "merged_candidates",
        "reranker_input_pairs",
        "final_candidates",
        "relevance_gate",
        "timings",
    }
    assert set(result["debug_trace"]["sql_filters"]) == {
        "source_types",
        "user_id",
        "session_id",
        "status",
        "visibility",
    }

    parent = result["items"][0]
    assert set(parent) >= {
        "source_type",
        "source",
        "source_id",
        "parent_score",
        "source_prior",
        "matched_cards",
        "score_breakdown",
        "digest_id",
        "digest_source_id",
        "matched_digest_row_ids",
    }
    assert set(parent["score_breakdown"]) == {"best_card", "matched_cards"}

    card = parent["matched_cards"][0]
    assert set(card) == {
        "candidate_id",
        "source_type",
        "source_id",
        "source_sub_id",
        "title",
        "text",
        "lexical",
        "semantic",
        "reranker",
        "final_score",
        "score_breakdown",
    }
    assert set(card["score_breakdown"]) == {"lexical", "semantic", "reranker", "recency", "final"}
    assert set(parent["score_breakdown"]["best_card"]) == {"lexical", "semantic", "reranker", "recency", "final"}


def test_memory_query_source_all_returns_digest_and_session_summary(db_session):
    from core.memory_rag import MemoryRagService

    digest = _digest_row(602, cards=[
        {"title": "摘要端口预算", "text": "KohakuVQ 端口预算来自长期摘要。", "keywords": ["KohakuVQ", "端口", "预算"]},
    ])
    other_digest = MemoryDigest(
        id=703,
        user_id="u2",
        session_id="s2",
        digest_date="2026-05-26",
        level=2,
        content="KohakuVQ 端口预算来自其他用户摘要。",
        meta_json=json.dumps({
            "schema_version": 2,
            "status": "active",
            "recall_cards": [
                {"title": "其他摘要", "text": "KohakuVQ 端口预算来自其他用户摘要。", "keywords": ["KohakuVQ", "端口", "预算"]},
            ],
        }, ensure_ascii=False),
    )
    summary = RollingSessionSummary(
        id=702,
        session_id="s1",
        user_id="u1",
        status="active",
        summary_kind="deterministic_fallback",
        summary_text="KohakuVQ 端口预算来自会话摘要。",
        summary_json=json.dumps({"summary": "KohakuVQ 端口预算来自会话摘要。"}, ensure_ascii=False),
    )
    other_summary = RollingSessionSummary(
        id=704,
        session_id="s2",
        user_id="u2",
        status="active",
        summary_kind="deterministic_fallback",
        summary_text="KohakuVQ 端口预算来自其他用户会话摘要。",
        summary_json=json.dumps({"summary": "KohakuVQ 端口预算来自其他用户会话摘要。"}, ensure_ascii=False),
    )
    _index_chunks(db_session, chunks_from_memory_digest(digest))
    _index_chunks(db_session, chunks_from_memory_digest(other_digest))
    _index_chunks(db_session, chunks_from_session_summary(summary))
    _index_chunks(db_session, chunks_from_session_summary(other_summary))

    service = MemoryRagService(
        db_session,
        embedding_provider=KeywordEmbeddingProvider(),
        reranker_provider=FixedRerankerProvider({
            "memory_digest:602:card:0": 0.9,
            "session_summary:702:section:summary": 0.8,
            "memory_digest:703:card:0": 0.95,
            "session_summary:704:section:summary": 0.95,
        }),
    )
    result = service.query(
        "KohakuVQ 端口预算",
        source="all",
        user_id="u1",
        session_id="s1",
        limit=5,
        include_debug=True,
    )

    assert result["debug_trace"]["sql_filters"]["source_types"] == ["memory_digest", "session_summary"]
    assert {item["source_type"] for item in result["items"]} == {"memory_digest", "session_summary"}
    assert {item["source_id"] for item in result["items"]} == {"602", "702"}


def test_digest_semantic_recall_without_exact_keyword(db_session):
    from core.memory_rag import MemoryRagService

    digest = _digest_row(102, cards=[
        {"title": "端口", "text": "uvicorn 8000 端口冲突。", "keywords": ["uvicorn"]},
    ])
    _index_chunks(db_session, chunks_from_memory_digest(digest))

    service = MemoryRagService(
        db_session,
        embedding_provider=KeywordEmbeddingProvider(),
        reranker_provider=FixedRerankerProvider({"memory_digest:102:card:0": 0.8}),
    )
    result = service.query("部署失败", source="digest", limit=5)

    assert result["items"]
    assert result["items"][0]["digest_id"] == 102
    assert result["stats"]["embedding_candidates"] >= 1


def test_memory_query_does_not_return_raw_chatlog(db_session):
    from core.memory_rag import MemoryRagService

    db_session.add(ChatLog(user_id="u1", session_id="s1", role="user", content="raw secret should not leak"))
    digest = _digest_row(103, cards=[
        {"title": "端口", "text": "安全摘要：端口冲突。", "keywords": ["端口"]},
    ])
    _index_chunks(db_session, chunks_from_memory_digest(digest))
    db_session.commit()

    service = MemoryRagService(
        db_session,
        embedding_provider=KeywordEmbeddingProvider(),
        reranker_provider=FixedRerankerProvider({"memory_digest:103:card:0": 0.8}),
    )
    result = service.query("端口", source="digest", limit=5)

    assert "raw secret" not in json.dumps(result, ensure_ascii=False)
    assert "安全摘要" in json.dumps(result, ensure_ascii=False)


def test_fallback_summary_can_be_indexed_with_lower_prior(db_session):
    from core.memory_rag import MemoryRagService

    row = RollingSessionSummary(
        id=201,
        session_id="s1",
        user_id="u1",
        status="active",
        summary_kind="deterministic_fallback",
        summary_text="部署失败时检查端口占用。",
        summary_json=json.dumps({"summary": "部署失败时检查端口占用。"}, ensure_ascii=False),
    )
    chunks = chunks_from_session_summary(row)
    _index_chunks(db_session, chunks)

    service = MemoryRagService(
        db_session,
        embedding_provider=KeywordEmbeddingProvider(),
        reranker_provider=FixedRerankerProvider({"session_summary:201:section:summary": 0.8}),
    )
    result = service.query("部署失败", source="session_summary", limit=5)

    assert result["items"][0]["summary_id"] == 201
    assert result["items"][0]["source_prior"] < 0.5


def test_memory_query_merges_multiple_cards_from_same_digest(db_session):
    from core.memory_rag import MemoryRagService

    digest = _digest_row(104, cards=[
        {"title": "端口 A", "text": "端口冲突第一条。", "keywords": ["端口"]},
        {"title": "端口 B", "text": "uvicorn 占用端口第二条。", "keywords": ["uvicorn"]},
        {"title": "端口 C", "text": "部署端口第三条。", "keywords": ["部署"]},
    ])
    _index_chunks(db_session, chunks_from_memory_digest(digest))

    service = MemoryRagService(
        db_session,
        embedding_provider=KeywordEmbeddingProvider(),
        reranker_provider=FixedRerankerProvider({
            "memory_digest:104:card:0": 0.7,
            "memory_digest:104:card:1": 0.9,
            "memory_digest:104:card:2": 0.8,
        }),
    )
    result = service.query("端口", source="digest", limit=5)

    assert len(result["items"]) == 1
    assert result["items"][0]["digest_id"] == 104
    assert result["items"][0]["parent_score"] == result["items"][0]["matched_cards"][0]["final_score"]
    assert len(result["items"][0]["matched_cards"]) == 2


def test_memory_rag_uses_fts_recall_before_recent_row_limit(db_session):
    from core.memory_rag import MemoryRagService
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks

    chunks = [
        SemanticChunk(
            source_type="memory_digest",
            source_id="old",
            source_sub_id="card:0",
            title="旧相关摘要",
            text="KohakuVQ 端口冲突排查",
            lexical_text="KohakuVQ 端口冲突排查",
            embedding_text="KohakuVQ 端口冲突排查",
            metadata={"user_id": "u1", "session_id": "s1"},
        )
    ]
    chunks.extend(
        SemanticChunk(
            source_type="memory_digest",
            source_id=f"noise-{idx}",
            source_sub_id="card:0",
            title=f"噪声 {idx}",
            text="午饭 咖啡 天气",
            lexical_text="午饭 咖啡 天气",
            embedding_text="午饭 咖啡 天气",
            metadata={"user_id": "u1", "session_id": "s1"},
        )
        for idx in range(405)
    )
    upsert_semantic_chunks(db_session, chunks, index_version="fake:v1:v1")

    result = MemoryRagService(db_session).query(
        "KohakuVQ",
        source="digest",
        user_id="u1",
        session_id="s1",
        limit=3,
    )

    assert result["items"][0]["source_id"] == "old"


def test_memory_rag_uses_vector_recall_before_recent_row_limit(db_session):
    from core.memory_rag import MemoryRagService
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks

    chunks = [
        SemanticChunk(
            source_type="memory_digest",
            source_id="old-vector",
            source_sub_id="card:old-vector",
            title="旧向量摘要",
            text="uvicorn 8000 端口冲突排查。",
            lexical_text="uvicorn 8000 端口冲突排查。",
            embedding_text="uvicorn 8000 端口冲突排查。",
            metadata={"user_id": "u1", "session_id": "s1"},
        )
    ]
    chunks.extend(
        SemanticChunk(
            source_type="memory_digest",
            source_id=f"noise-vector-{idx}",
            source_sub_id=f"card:noise-vector-{idx}",
            title=f"向量噪声 {idx}",
            text="午饭 咖啡 天气",
            lexical_text="午饭 咖啡 天气",
            embedding_text="午饭 咖啡 天气",
            metadata={"user_id": "u1", "session_id": "s1"},
        )
        for idx in range(405)
    )
    embeddings = {
        chunk.source_sub_id: json.dumps(KeywordEmbeddingProvider().embed([chunk.embedding_text])[0]).encode("utf-8")
        for chunk in chunks
    }
    upsert_semantic_chunks(
        db_session,
        chunks,
        index_version="fake:v1:v1",
        embedding_model="fake",
        embeddings=embeddings,
    )

    result = MemoryRagService(
        db_session,
        embedding_provider=KeywordEmbeddingProvider(),
        reranker_provider=FixedRerankerProvider({"memory_digest:old-vector:card:old-vector": 0.8}),
    ).query(
        "部署失败",
        source="digest",
        user_id="u1",
        session_id="s1",
        limit=3,
        include_debug=True,
    )

    assert result["items"][0]["source_id"] == "old-vector"
    assert result["stats"]["vector_candidates"] >= 1
    assert result["debug_trace"]["vector_hits"][0]["candidate_id"] == "memory_digest:old-vector:card:old-vector"


def test_memory_rag_does_not_embed_when_index_has_no_vectors(db_session):
    from core.memory_rag import MemoryRagService
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks

    class CountingEmbeddingProvider(KeywordEmbeddingProvider):
        def __init__(self):
            self.text_batches = []

        def embed(self, texts):
            self.text_batches.append(list(texts))
            return super().embed(texts)

    upsert_semantic_chunks(
        db_session,
        [
            SemanticChunk(
                source_type="memory_digest",
                source_id="no-row-embedding",
                source_sub_id="card:0",
                title="端口记录",
                text="端口记录",
                lexical_text="端口记录",
                embedding_text="端口记录",
                metadata={"user_id": "u1", "session_id": "s1"},
            )
        ],
        index_version="fake:v1:v1",
    )

    provider = CountingEmbeddingProvider()
    result = MemoryRagService(db_session, embedding_provider=provider).query(
        "端口",
        source="digest",
        user_id="u1",
        session_id="s1",
        limit=3,
    )

    assert provider.text_batches == []
    assert result["stats"]["embedding_candidates"] == 0


def test_memory_rag_does_not_rerank_generic_lexical_fallback(db_session):
    from core.memory_rag import MemoryRagService
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks

    chunks = [
        SemanticChunk(
            source_type="memory_digest",
            source_id="relevant",
            source_sub_id="card:0",
            title="端口冲突",
            text="uvicorn 8000 端口冲突排查。",
            lexical_text="uvicorn 8000 端口冲突排查。",
            embedding_text="uvicorn 8000 端口冲突排查。",
            metadata={"user_id": "u1", "session_id": "s1"},
        ),
        SemanticChunk(
            source_type="memory_digest",
            source_id="generic",
            source_sub_id="card:0",
            title="泛化问法",
            text="这个问题怎么解决，需要再看看。",
            lexical_text="这个问题怎么解决，需要再看看。",
            embedding_text="这个问题怎么解决，需要再看看。",
            metadata={"user_id": "u1", "session_id": "s1"},
        ),
    ]
    upsert_semantic_chunks(db_session, chunks, index_version="fake:v1:v1")

    reranker = RecordingRerankerProvider({
        "memory_digest:relevant:card:0": 0.9,
        "memory_digest:generic:card:0": 0.9,
    })
    result = MemoryRagService(db_session, reranker_provider=reranker).query(
        "端口冲突怎么解决",
        source="digest",
        user_id="u1",
        session_id="s1",
        limit=5,
        include_debug=True,
    )

    assert "memory_digest:relevant:card:0" in reranker.seen_candidate_ids
    assert "memory_digest:generic:card:0" not in reranker.seen_candidate_ids
    assert result["stats"]["reranker_candidates"] == 1


def test_memory_rag_marks_reranker_budget_skipped_candidates(db_session):
    from core.memory_rag import MemoryRagService

    digest = _digest_row(603, cards=[
        {
            "title": f"KohakuVQ 端口预算 {idx}",
            "text": f"KohakuVQ 端口预算候选 {idx}。",
            "keywords": ["KohakuVQ", "端口", "预算"],
        }
        for idx in range(55)
    ])
    _index_chunks(db_session, chunks_from_memory_digest(digest))

    service = MemoryRagService(
        db_session,
        embedding_provider=KeywordEmbeddingProvider(),
        reranker_provider=FixedRerankerProvider({
            f"memory_digest:603:card:{idx}": 0.9
            for idx in range(50)
        }),
    )
    result = service.query("KohakuVQ 端口预算", source="digest", limit=5, include_debug=True)
    skipped = [
        item
        for item in result["debug_trace"]["merged_candidates"]
        if item["skipped_reason"] == "reranker_budget"
    ]

    assert result["stats"]["merged_candidates"] == 55
    assert result["stats"]["reranker_candidates"] == 50
    assert len(skipped) == 5


def test_memory_rag_skips_low_overlap_fallback_before_rerank(db_session, monkeypatch):
    from core.memory_rag import MemoryRagService
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks

    monkeypatch.setattr("core.memory_rag.fts_recall_hits", lambda *args, **kwargs: [])
    chunks = [
        SemanticChunk(
            source_type="memory_digest",
            source_id="strong",
            source_sub_id="card:0",
            title="端口冲突部署失败",
            text="端口冲突导致部署失败。",
            lexical_text="端口冲突导致部署失败。",
            embedding_text="端口冲突导致部署失败。",
            metadata={"user_id": "u1", "session_id": "s1"},
        ),
        SemanticChunk(
            source_type="memory_digest",
            source_id="weak",
            source_sub_id="card:0",
            title="端口记录",
            text="这里仅记录端口配置。",
            lexical_text="这里仅记录端口配置。",
            embedding_text="这里仅记录端口配置。",
            metadata={"user_id": "u1", "session_id": "s1"},
        ),
    ]
    upsert_semantic_chunks(db_session, chunks, index_version="fake:v1:v1")

    reranker = RecordingRerankerProvider({
        "memory_digest:strong:card:0": 0.9,
        "memory_digest:weak:card:0": 0.9,
    })
    result = MemoryRagService(db_session, reranker_provider=reranker).query(
        "端口冲突部署失败",
        source="digest",
        user_id="u1",
        session_id="s1",
        limit=5,
        include_debug=True,
    )

    assert "memory_digest:strong:card:0" in reranker.seen_candidate_ids
    assert "memory_digest:weak:card:0" not in reranker.seen_candidate_ids
    assert result["stats"]["merged_candidates"] == 2
    assert result["stats"]["reranker_candidates"] == 1
    by_id = {
        item["candidate_id"]: item
        for item in result["debug_trace"]["merged_candidates"]
    }
    assert by_id["memory_digest:weak:card:0"]["skipped_reason"] == "weak_lexical_fallback"


def test_memory_query_degraded_contract_without_reranker(db_session):
    from core.memory_rag import MemoryRagService

    digest = _digest_row(604, cards=[
        {"title": "端口预算", "text": "KohakuVQ 端口预算在无 reranker 时退化查询。", "keywords": ["KohakuVQ", "端口", "预算"]},
    ])
    _index_chunks(db_session, chunks_from_memory_digest(digest))

    service = MemoryRagService(
        db_session,
        embedding_provider=KeywordEmbeddingProvider(),
    )
    result = service.query("KohakuVQ 端口预算", source="digest", limit=5, include_debug=True)

    assert result["degraded"] is True
    assert result["fallback_reason"] == "reranker_unavailable"
    assert result["stats"]["reranker_candidates"] == 0
    assert result["debug_trace"]["reranker_input_pairs"] == []


def test_memory_query_tool_schema_supports_all_source():
    from creatures.nanobot.prompts.skills.memory_query.tool import MemoryQueryTool

    schema = MemoryQueryTool().get_parameters_schema()

    assert "all" in schema["properties"]["source"]["enum"]


def test_memory_query_tool_search_routes_all_sources_through_memory_rag(db_session, monkeypatch):
    from core import database
    import core.memory_rag as memory_rag
    from creatures.nanobot.prompts.skills.memory_query.tool import MemoryQueryTool

    calls = []

    class FakeMemoryRagService:
        def __init__(self, db, **kwargs):
            self.db = db
            self.kwargs = kwargs

        def query(self, query, *, source, user_id="", session_id="", limit=5):
            calls.append({
                "query": query,
                "source": source,
                "user_id": user_id,
                "session_id": session_id,
                "limit": limit,
                "has_reranker_kwarg": "reranker_provider" in self.kwargs,
            })
            return {
                "query": query,
                "source": source,
                "degraded": False,
                "stats": {"final_items": 1},
                "items": [{
                    "source": source,
                    "source_id": "fake-1",
                    "digest_id": 301 if source == "digest" else None,
                    "summary_id": 401 if source == "session_summary" else None,
                    "parent_score": 0.91,
                    "matched_cards": [{"text": f"{source} 命中内容"}],
                }],
            }

    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(memory_rag, "MemoryRagService", FakeMemoryRagService)
    monkeypatch.setattr(MemoryQueryTool, "_has_rag_index", staticmethod(lambda _db, _source: True))

    tool = MemoryQueryTool()
    for source in ("digest", "session_summary", "all"):
        result = run_async(tool._execute({
            "source": source,
            "mode": "search",
            "query": "端口",
            "user_id": "u1",
            "session_id": "s1",
            "limit": 3,
        }))
        assert result.exit_code == 0
        assert f"{source} 命中内容" in result.output

    assert [call["source"] for call in calls] == ["digest", "session_summary", "all"]
    assert all(call["has_reranker_kwarg"] for call in calls)


def test_memory_query_tool_blocks_when_reranker_required_unavailable(db_session, monkeypatch):
    from core import database
    from core.semantic.adapters import chunks_from_memory_digest
    from core.semantic.indexer import upsert_semantic_chunks
    from core.semantic.provider_factory import get_reranker_provider
    from creatures.nanobot.prompts.skills.memory_query.tool import MemoryQueryTool

    digest = _digest_row(501, cards=[
        {"title": "端口", "text": "KohakuVQ 端口冲突。", "keywords": ["KohakuVQ", "端口"]},
    ])
    db_session.add(digest)
    db_session.commit()
    upsert_semantic_chunks(db_session, chunks_from_memory_digest(digest), index_version="fake:v1:v1")

    monkeypatch.setenv("RAG_ALLOW_DEGRADED", "0")
    monkeypatch.setenv("RAG_RERANKER_ENABLED", "1")
    monkeypatch.setenv("RAG_RERANKER_URL", "")
    monkeypatch.setenv("RAG_LOCAL_RERANKER_MODEL", "./models/not-present-reranker")
    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)

    get_reranker_provider.cache_clear()
    result = run_async(MemoryQueryTool()._execute({
        "source": "digest",
        "mode": "search",
        "query": "KohakuVQ",
        "session_id": "s1",
        "limit": 3,
    }))
    get_reranker_provider.cache_clear()

    assert result.error
    assert "reranker_unavailable" in result.error
