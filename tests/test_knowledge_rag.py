class IdentityRerankerProvider:
    def __init__(self, scores):
        self.scores = scores

    def rerank(self, query, candidates, *, top_k=None):
        from core.semantic.reranker import RerankResult

        limited = candidates[:top_k] if top_k else candidates
        return sorted(
            [
                RerankResult(
                    candidate_id=candidate.candidate_id,
                    raw_score=self.scores.get(candidate.candidate_id, 0.0),
                    score=self.scores.get(candidate.candidate_id, 0.0),
                    model="identity-reranker",
                    score_mode="identity",
                )
                for candidate in limited
            ],
            key=lambda item: item.score or 0.0,
            reverse=True,
        )


def test_knowledge_orm_classes_are_importable():
    from core.database import KnowledgeChunk, KnowledgeDocument, KnowledgeSource

    assert KnowledgeSource.__tablename__ == "knowledge_sources"
    assert KnowledgeDocument.__tablename__ == "knowledge_documents"
    assert KnowledgeChunk.__tablename__ == "knowledge_chunks"


def _manual_doc(db_session, filename, content, **kwargs):
    from core.knowledge_library import create_manual_document

    doc = create_manual_document(
        db_session,
        filename=filename,
        content=content,
        **kwargs,
    )
    db_session.refresh(doc)
    return doc


def _index_doc(db_session, doc):
    from core.database import KnowledgeChunk
    from core.semantic.adapters import chunk_from_knowledge_chunk
    from core.semantic.indexer import upsert_semantic_chunks

    rows = (
        db_session.query(KnowledgeChunk)
        .filter(KnowledgeChunk.document_id == doc.id)
        .order_by(KnowledgeChunk.order_index.asc())
        .all()
    )
    upsert_semantic_chunks(
        db_session,
        [chunk_from_knowledge_chunk(row, document=doc) for row in rows],
        index_version="fake:v1:knowledge",
    )
    return rows


def test_knowledge_query_uses_reranker_before_final_score(db_session):
    from core.knowledge_rag import KnowledgeRagService

    popular = _manual_doc(
        db_session,
        "popular.md",
        "# 向量数据库\n向量数据库 常见概念和泛泛介绍。",
        title="泛泛介绍",
        trust_level="high",
        published_at="2026-05-20",
    )
    exact = _manual_doc(
        db_session,
        "exact.md",
        "# 向量数据库\n向量数据库 排查方案：先看索引版本和 reranker 分数。",
        title="排查方案",
        trust_level="medium",
        published_at="2026-05-21",
    )
    _index_doc(db_session, popular)
    _index_doc(db_session, exact)

    service = KnowledgeRagService(
        db_session,
        reranker_provider=IdentityRerankerProvider({
            f"knowledge:{popular.id}:chunk:0": 0.1,
            f"knowledge:{exact.id}:chunk:0": 0.9,
        }),
    )
    result = service.query("向量数据库 排查", limit=5)

    assert [item["document_id"] for item in result["items"]] == [exact.id]
    assert result["items"][0]["score_breakdown"]["reranker"] == 0.9


def test_knowledge_rag_uses_vector_recall_before_recent_row_limit(db_session):
    import json

    from core.knowledge_rag import KnowledgeRagService
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks

    class ConstantEmbeddingProvider:
        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    chunks = [
        SemanticChunk(
            source_type="knowledge",
            source_id="old-vector-doc",
            source_sub_id="chunk:old-vector",
            title="旧知识",
            text="索引版本和 reranker 分数排查。",
            lexical_text="索引版本和 reranker 分数排查。",
            embedding_text="索引版本和 reranker 分数排查。",
            metadata={
                "citation": {
                    "url": "https://example.com/old-vector",
                    "title": "旧知识",
                    "trust_level": "medium",
                }
            },
        )
    ]
    chunks.extend(
        SemanticChunk(
            source_type="knowledge",
            source_id=f"noise-doc-{index}",
            source_sub_id=f"chunk:noise-{index}",
            title=f"噪声知识 {index}",
            text="午饭咖啡闲聊。",
            lexical_text="午饭咖啡闲聊。",
            embedding_text="午饭咖啡闲聊。",
            metadata={
                "citation": {
                    "url": f"https://example.com/noise-{index}",
                    "title": f"噪声知识 {index}",
                    "trust_level": "medium",
                }
            },
        )
        for index in range(605)
    )
    embeddings = {
        chunk.source_sub_id: json.dumps(
            [1.0, 0.0, 0.0] if chunk.source_id == "old-vector-doc" else [0.0, 1.0, 0.0]
        ).encode("utf-8")
        for chunk in chunks
    }
    upsert_semantic_chunks(
        db_session,
        chunks,
        index_version="fake:v1:knowledge",
        embedding_model="fake",
        embeddings=embeddings,
    )

    result = KnowledgeRagService(
        db_session,
        embedding_provider=ConstantEmbeddingProvider(),
        reranker_provider=IdentityRerankerProvider({"knowledge:old-vector-doc:chunk:old-vector": 0.9}),
    ).query(
        "向量召回",
        limit=3,
        include_debug=True,
    )

    assert result["items"][0]["document_id"] == "old-vector-doc"
    assert result["stats"]["vector_candidates"] >= 1
    assert result["debug_trace"]["vector_hits"][0]["candidate_id"] == "knowledge:old-vector-doc:chunk:old-vector"


def test_knowledge_query_returns_citations(db_session):
    from core.knowledge_rag import KnowledgeRagService

    doc = _manual_doc(
        db_session,
        "citation.md",
        "# RAG\nRAG 召回必须保留 citation。",
        title="RAG 规范",
        trust_level="medium",
        published_at="2026-05-22",
    )
    _index_doc(db_session, doc)

    result = KnowledgeRagService(db_session).query("citation", limit=3)

    item = result["items"][0]
    assert item["citation"]["document_id"] == str(doc.id)
    assert item["citation"]["chunk_id"] == "chunk:0"
    assert item["citation"]["title"] == "RAG 规范"
    assert item["citation"]["trust_level"] == "medium"


def test_knowledge_query_filters_by_trust_and_date(db_session):
    from core.knowledge_rag import KnowledgeRagService

    old_low = _manual_doc(
        db_session,
        "old.md",
        "RAG 召回策略旧文档。",
        title="旧文档",
        trust_level="low",
        published_at="2025-01-01",
    )
    recent = _manual_doc(
        db_session,
        "recent.md",
        "RAG 召回策略近期文档。",
        title="近期文档",
        trust_level="medium",
        published_at="2026-05-20",
    )
    _index_doc(db_session, old_low)
    _index_doc(db_session, recent)

    result = KnowledgeRagService(db_session).query(
        "RAG 召回策略",
        min_trust_level="medium",
        published_after="2026-01-01",
        limit=5,
    )

    assert [item["document_id"] for item in result["items"]] == [recent.id]


def test_expand_returns_document_chunk_not_raw_unbounded_text(db_session):
    from core.database import KnowledgeChunk
    from core.knowledge_rag import KnowledgeRagService

    content = "# 目标\n目标 chunk 内容。\n\n# 其他\n" + ("RAW-TAIL " * 800)
    doc = _manual_doc(
        db_session,
        "expand.md",
        content,
        title="展开测试",
        trust_level="medium",
    )
    chunks = _index_doc(db_session, doc)
    first = chunks[0]

    expanded = KnowledgeRagService(db_session).expand(
        document_id=doc.id,
        chunk_id=first.chunk_id,
        max_chars=1200,
    )

    assert expanded["text"] == first.text[:1200]
    assert len(expanded["text"]) <= 1200
    assert expanded["text"] != db_session.query(KnowledgeChunk).filter_by(document_id=doc.id).all()[-1].text


def test_knowledge_result_without_citation_is_dropped(db_session):
    from core.knowledge_rag import KnowledgeRagService
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks

    chunk = SemanticChunk(
        source_type="knowledge",
        source_id="doc-without-citation",
        source_sub_id="chunk:0",
        title="无 citation",
        text="RAG 无 citation 内容",
        lexical_text="RAG 无 citation 内容",
        embedding_text="RAG 无 citation 内容",
        metadata={},
    )
    upsert_semantic_chunks(db_session, [chunk], index_version="fake:v1:knowledge")

    result = KnowledgeRagService(db_session).query("RAG", limit=5)

    assert result["items"] == []
    assert result["stats"]["skipped_no_citation"] == 1


def test_knowledge_query_filters_by_source_type_domain_and_date(db_session):
    from core.knowledge_rag import KnowledgeRagService

    ai_doc = _manual_doc(
        db_session,
        "ai.md",
        "# RAG\nRAG 每日摘要内容。",
        title="AI 日报",
        published_at="2026-05-25",
    )
    ai_doc.document_kind = "ai_daily"
    ai_doc.domain = "ai.example.com"
    manual_doc = _manual_doc(
        db_session,
        "manual.md",
        "# RAG\nRAG 手工文档内容。",
        title="手工文档",
        published_at="2026-05-20",
    )
    manual_doc.document_kind = "manual_markdown"
    manual_doc.domain = "docs.example.com"
    db_session.commit()
    _index_doc(db_session, ai_doc)
    _index_doc(db_session, manual_doc)

    result = KnowledgeRagService(db_session).query(
        "RAG",
        source_type="ai_daily",
        domain="ai.example.com",
        date_start="2026-05-24",
        date_end="2026-05-26",
        limit=5,
    )

    assert [item["document_id"] for item in result["items"]] == [ai_doc.id]


def test_knowledge_query_tool_schema_declares_citation_boundary():
    from creatures.nanobot.prompts.skills.knowledge_query.tool import KnowledgeQueryTool

    schema = KnowledgeQueryTool().get_parameters_schema()

    assert schema["properties"]["mode"]["enum"] == ["search", "expand"]
    assert "min_trust_level" in schema["properties"]
    assert "source_type" in schema["properties"]
    assert "domain" in schema["properties"]
    assert "date_start" in schema["properties"]
    assert "date_end" in schema["properties"]
    assert "citation" in KnowledgeQueryTool().description


def test_knowledge_query_tool_blocks_when_reranker_required_unavailable(db_session, monkeypatch):
    import asyncio

    from core import database
    from core.semantic.provider_factory import get_reranker_provider
    from creatures.nanobot.prompts.skills.knowledge_query.tool import KnowledgeQueryTool

    doc = _manual_doc(
        db_session,
        "blocked.md",
        "# RAG\nRAG citation test",
        title="阻断测试",
        published_at="2026-05-26",
    )
    _index_doc(db_session, doc)

    monkeypatch.setenv("RAG_ALLOW_DEGRADED", "0")
    monkeypatch.setenv("RAG_RERANKER_ENABLED", "1")
    monkeypatch.setenv("RAG_RERANKER_URL", "")
    monkeypatch.setenv("RAG_LOCAL_RERANKER_MODEL", "./models/not-present-reranker")
    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)

    get_reranker_provider.cache_clear()
    result = asyncio.run(KnowledgeQueryTool()._execute({
        "mode": "search",
        "query": "RAG",
        "limit": 3,
    }))
    get_reranker_provider.cache_clear()

    assert result.error
    assert "reranker_unavailable" in result.error
