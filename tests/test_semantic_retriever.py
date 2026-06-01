from core.semantic.adapters import SemanticChunk
from core.semantic.indexer import upsert_semantic_chunks


def _chunk(source_id: str, text: str, **metadata):
    return SemanticChunk(
        source_type="memory_digest",
        source_id=source_id,
        source_sub_id="card:0",
        title=text[:20],
        text=text,
        lexical_text=text,
        embedding_text=text,
        metadata=metadata,
    )


def test_fts_recall_filters_status_visibility(db_session):
    from core.database import SemanticIndexItem
    from core.semantic.retriever import fts_recall_hits

    upsert_semantic_chunks(
        db_session,
        [
            _chunk("active", "KohakuVQ active recall", user_id="u1", session_id="s1"),
            SemanticChunk(
                source_type="memory_digest",
                source_id="expand-only",
                source_sub_id="digest:level1",
                title="KohakuVQ expand only",
                text="KohakuVQ expand only",
                lexical_text="KohakuVQ expand only",
                embedding_text="KohakuVQ expand only",
                metadata={"user_id": "u1", "session_id": "s1"},
                visibility="expand_only",
            ),
            _chunk("disabled", "KohakuVQ disabled recall", user_id="u1", session_id="s1"),
        ],
        index_version="fake:v1:v1",
    )
    disabled = db_session.query(SemanticIndexItem).filter_by(source_id="disabled").one()
    disabled.status = "disabled"
    db_session.commit()

    hits = fts_recall_hits(
        db_session,
        "KohakuVQ",
        source_types={"memory_digest"},
        user_id="u1",
        session_id="s1",
    )
    rows = db_session.query(SemanticIndexItem).filter(SemanticIndexItem.id.in_([hit.item_id for hit in hits])).all()

    assert [row.source_id for row in rows] == ["active"]


def test_fts_recall_filters_user_and_session(db_session):
    from core.database import SemanticIndexItem
    from core.semantic.retriever import fts_recall_hits

    upsert_semantic_chunks(
        db_session,
        [
            _chunk("target", "KohakuVQ scoped recall", user_id="u1", session_id="s1"),
            _chunk("other-user", "KohakuVQ scoped recall", user_id="u2", session_id="s1"),
            _chunk("other-session", "KohakuVQ scoped recall", user_id="u1", session_id="s2"),
        ],
        index_version="fake:v1:v1",
    )

    hits = fts_recall_hits(
        db_session,
        "KohakuVQ",
        source_types={"memory_digest"},
        user_id="u1",
        session_id="s1",
    )
    rows = db_session.query(SemanticIndexItem).filter(SemanticIndexItem.id.in_([hit.item_id for hit in hits])).all()

    assert [row.source_id for row in rows] == ["target"]


def test_fts_recall_orders_by_bm25(db_session):
    from core.database import SemanticIndexItem
    from core.semantic.retriever import fts_recall_hits

    upsert_semantic_chunks(
        db_session,
        [
            _chunk("weak", "KohakuVQ " + "背景噪声 " * 80, user_id="u1", session_id="s1"),
            _chunk("strong", "KohakuVQ KohakuVQ KohakuVQ", user_id="u1", session_id="s1"),
        ],
        index_version="fake:v1:v1",
    )

    hits = fts_recall_hits(
        db_session,
        "KohakuVQ",
        source_types={"memory_digest"},
        user_id="u1",
        session_id="s1",
    )
    rows_by_id = {
        row.id: row
        for row in db_session.query(SemanticIndexItem).filter(
            SemanticIndexItem.id.in_([hit.item_id for hit in hits])
        )
    }

    assert rows_by_id[hits[0].item_id].source_id == "strong"
    assert hits[0].bm25_raw <= hits[-1].bm25_raw


def test_vector_recall_uses_stored_embeddings_and_filters_scope(db_session):
    import json

    from core.database import SemanticIndexItem
    from core.semantic.retriever import vector_recall_hits

    chunks = [
        SemanticChunk(
            source_type="memory_digest",
            source_id="target",
            source_sub_id="card:target",
            title="部署线索",
            text="uvicorn 端口占用",
            lexical_text="uvicorn 端口占用",
            embedding_text="uvicorn 端口占用",
            metadata={"user_id": "u1", "session_id": "s1"},
        ),
        SemanticChunk(
            source_type="memory_digest",
            source_id="other-user",
            source_sub_id="card:other-user",
            title="其他用户",
            text="uvicorn 端口占用",
            lexical_text="uvicorn 端口占用",
            embedding_text="uvicorn 端口占用",
            metadata={"user_id": "u2", "session_id": "s1"},
        ),
        SemanticChunk(
            source_type="memory_digest",
            source_id="missing-embedding",
            source_sub_id="card:missing",
            title="缺少向量",
            text="uvicorn 端口占用",
            lexical_text="uvicorn 端口占用",
            embedding_text="uvicorn 端口占用",
            metadata={"user_id": "u1", "session_id": "s1"},
        ),
        SemanticChunk(
            source_type="memory_digest",
            source_id="unrelated",
            source_sub_id="card:unrelated",
            title="无关",
            text="模型路由",
            lexical_text="模型路由",
            embedding_text="模型路由",
            metadata={"user_id": "u1", "session_id": "s1"},
        ),
    ]
    upsert_semantic_chunks(
        db_session,
        chunks,
        index_version="fake:v1:v1",
        embedding_model="fake",
        embeddings={
            "card:target": json.dumps([1.0, 0.0, 0.0]).encode("utf-8"),
            "card:other-user": json.dumps([1.0, 0.0, 0.0]).encode("utf-8"),
            "card:unrelated": json.dumps([0.0, 1.0, 0.0]).encode("utf-8"),
        },
    )

    hits = vector_recall_hits(
        db_session,
        query_vector=[1.0, 0.0, 0.0],
        source_types={"memory_digest"},
        user_id="u1",
        session_id="s1",
        limit=5,
    )
    rows_by_id = {
        row.id: row
        for row in db_session.query(SemanticIndexItem).filter(
            SemanticIndexItem.id.in_([hit.item_id for hit in hits])
        )
    }

    assert [rows_by_id[hit.item_id].source_id for hit in hits] == ["target"]
    assert hits[0].semantic_score > 0
