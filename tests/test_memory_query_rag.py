import json

from core.database import ChatLog, MemoryDigest, RollingSessionSummary
from core.semantic.adapters import (
    chunks_from_memory_digest,
    chunks_from_session_summary,
)
from core.semantic.indexer import upsert_semantic_chunks


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


def test_memory_query_tool_schema_supports_all_source():
    from creatures.nanobot.prompts.skills.memory_query.tool import MemoryQueryTool

    schema = MemoryQueryTool().get_parameters_schema()

    assert "all" in schema["properties"]["source"]["enum"]
