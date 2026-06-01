import json
from datetime import datetime

import pytest

from core.database import StickerMemory
from core.semantic.adapters import chunk_from_sticker
from core.semantic.indexer import upsert_semantic_chunks


class IdentityRerankerProvider:
    def __init__(self, scores, *, default_score=0.9):
        self.scores = scores
        self.default_score = float(default_score)

    def rerank(self, query, candidates, *, top_k=None):
        from core.semantic.reranker import RerankResult

        limited = candidates[:top_k] if top_k else candidates
        return sorted(
            [
                RerankResult(
                    candidate_id=candidate.candidate_id,
                    raw_score=self.scores.get(candidate.candidate_id, self.default_score),
                    score=self.scores.get(candidate.candidate_id, self.default_score),
                    model="identity-reranker",
                    score_mode="identity",
                )
                for candidate in limited
            ],
            key=lambda item: item.score or 0.0,
            reverse=True,
        )


class CountingRerankerProvider(IdentityRerankerProvider):
    def __init__(self, scores=None, *, default_score=0.9):
        super().__init__(scores or {}, default_score=default_score)
        self.batch_sizes = []
        self.candidate_ids = []

    def rerank(self, query, candidates, *, top_k=None):
        self.batch_sizes.append(len(candidates))
        self.candidate_ids.append([candidate.candidate_id for candidate in candidates])
        return super().rerank(query, candidates, top_k=top_k)


def _add_sticker(
    db_session,
    sticker_hash,
    *,
    description,
    tags=None,
    emotions=None,
    file_ref=None,
    send_code="",
    status="active",
    dedupe_status="unique",
    duplicate_of_id=None,
    describe_status="ok",
    usage_count=0,
    chat_stream_id="qq:123:group",
    meta=None,
):
    row = StickerMemory(
        chat_stream_id=chat_stream_id,
        sticker_hash=sticker_hash,
        file_ref=file_ref if file_ref is not None else f"https://example.com/{sticker_hash}.png",
        send_code=send_code,
        name=sticker_hash,
        description=description,
        tags_json=json.dumps(tags or [], ensure_ascii=False),
        emotions_json=json.dumps(emotions or [], ensure_ascii=False),
        status=status,
        dedupe_status=dedupe_status,
        duplicate_of_id=duplicate_of_id,
        describe_status=describe_status,
        usage_count=usage_count,
        last_seen=datetime(2026, 5, 26, 12, 0, 0),
        meta_json=json.dumps(meta or {}, ensure_ascii=False),
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _index_stickers(db_session, rows):
    chunks = [chunk for row in rows if (chunk := chunk_from_sticker(row)) is not None]
    if chunks:
        upsert_semantic_chunks(db_session, chunks, index_version="fake:v1:sticker")
    return chunks


def test_sticker_rag_uses_text_tags_not_image_embedding(db_session):
    from core.sticker_memory import search_stickers

    path_only_match = _add_sticker(
        db_session,
        "path-only-angry",
        file_ref="https://example.com/生气拍桌.png",
        description="猫猫疑惑看着屏幕",
        tags=["疑惑"],
        emotions=["confused"],
    )
    text_match = _add_sticker(
        db_session,
        "text-angry",
        file_ref="https://example.com/neutral.png",
        description="小人非常生气正在拍桌",
        tags=["生气", "拍桌"],
        emotions=["angry"],
    )
    _index_stickers(db_session, [path_only_match, text_match])

    results = search_stickers(
        db_session,
        "生气",
        group_id="123",
        limit=5,
        reranker_provider=IdentityRerankerProvider({}),
    )

    assert [item["id"] for item in results] == [text_match.id]
    assert "生气拍桌.png" not in json.dumps(results, ensure_ascii=False)


def test_sticker_rag_returns_reply_token(db_session):
    from core.sticker_memory import search_stickers

    sticker = _add_sticker(
        db_session,
        "reply-token",
        description="适合表达震惊的猫猫表情",
        tags=["震惊", "猫"],
        emotions=["surprised"],
    )
    _index_stickers(db_session, [sticker])

    results = search_stickers(
        db_session,
        "震惊",
        group_id="123",
        limit=3,
        reranker_provider=IdentityRerankerProvider({}),
    )

    assert results[0]["reply_token"] == f"[sticker:{sticker.id}]"
    assert results[0]["send_code"] == "[CQ:image,file=https://example.com/reply-token.png]"
    assert results[0]["score_breakdown"]["lexical"] > 0


def test_duplicate_or_inactive_sticker_is_filtered(db_session):
    from core.sticker_memory import search_stickers

    active = _add_sticker(
        db_session,
        "active-sticker",
        description="拍桌催更",
        tags=["拍桌"],
        emotions=["angry"],
    )
    duplicate = _add_sticker(
        db_session,
        "duplicate-sticker",
        description="拍桌催更",
        tags=["拍桌"],
        dedupe_status="duplicate",
    )
    disabled = _add_sticker(
        db_session,
        "disabled-sticker",
        description="拍桌催更",
        tags=["拍桌"],
        status="disabled",
    )
    _index_stickers(db_session, [active, duplicate, disabled])

    results = search_stickers(
        db_session,
        "拍桌",
        group_id="123",
        limit=5,
        reranker_provider=IdentityRerankerProvider({}),
    )

    assert [item["id"] for item in results] == [active.id]


def test_sticker_search_uses_reranker_before_usage_boost(db_session):
    from core.sticker_memory import search_stickers

    popular_low_relevance = _add_sticker(
        db_session,
        "popular-low",
        description="拍桌催更",
        tags=["拍桌"],
        usage_count=999,
    )
    exact_high_relevance = _add_sticker(
        db_session,
        "exact-high",
        description="拍桌表示非常生气",
        tags=["拍桌", "生气"],
        usage_count=0,
    )
    _index_stickers(db_session, [popular_low_relevance, exact_high_relevance])

    reranker = IdentityRerankerProvider({
        f"sticker:{popular_low_relevance.id}:sticker": 0.1,
        f"sticker:{exact_high_relevance.id}:sticker": 0.9,
    })
    results = search_stickers(
        db_session,
        "拍桌生气",
        group_id="123",
        limit=5,
        reranker_provider=reranker,
    )

    assert [item["id"] for item in results] == [exact_high_relevance.id]
    assert results[0]["score_breakdown"]["reranker"] == 0.9


def test_sticker_search_does_not_rerank_generic_sticker_matches(db_session):
    from core.sticker_memory import search_stickers

    happy = [
        _add_sticker(
            db_session,
            f"happy-{index}",
            description=f"开心笑脸表情包 {index}",
            tags=["开心", "笑脸"],
            emotions=["happy"],
        )
        for index in range(3)
    ]
    generic = [
        _add_sticker(
            db_session,
            f"generic-{index}",
            description=f"普通表情包 {index}",
            tags=["表情包"],
            emotions=["neutral"],
        )
        for index in range(40)
    ]
    _index_stickers(db_session, happy + generic)
    reranker = CountingRerankerProvider({})

    results = search_stickers(
        db_session,
        "开心 表情包",
        group_id="123",
        limit=5,
        reranker_provider=reranker,
    )

    assert reranker.batch_sizes == [3]
    assert {item["id"] for item in results} == {item.id for item in happy}


def test_sticker_search_caps_reranker_batch_size(db_session):
    from core.sticker_memory import search_stickers

    stickers = [
        _add_sticker(
            db_session,
            f"angry-{index}",
            description=f"生气拍桌表情包 {index}",
            tags=["生气", "拍桌"],
            emotions=["angry"],
        )
        for index in range(30)
    ]
    _index_stickers(db_session, stickers)
    reranker = CountingRerankerProvider({})

    search_stickers(
        db_session,
        "生气拍桌",
        group_id="123",
        limit=5,
        reranker_provider=reranker,
    )

    assert reranker.batch_sizes == [10]


def test_sticker_rag_uses_vector_recall_before_recent_row_limit(db_session):
    from core.database import SemanticIndexItem
    from core.sticker_rag import StickerRagService

    class ConstantEmbeddingProvider:
        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    target = _add_sticker(
        db_session,
        "old-vector-sticker",
        description="猫猫挥手欢迎",
        tags=["欢迎"],
        emotions=["friendly"],
    )
    noise = [
        _add_sticker(
            db_session,
            f"sticker-vector-noise-{index}",
            description="午饭咖啡闲聊",
            tags=["闲聊"],
            emotions=["neutral"],
        )
        for index in range(405)
    ]
    _index_stickers(db_session, [target] + noise)
    for row in db_session.query(SemanticIndexItem).filter(SemanticIndexItem.source_type == "sticker").all():
        row.embedding = json.dumps(
            [1.0, 0.0, 0.0] if str(row.source_id) == str(target.id) else [0.0, 1.0, 0.0]
        ).encode("utf-8")
        row.embedding_status = "ok"
        row.embedding_model = "fake"
    db_session.commit()

    result = StickerRagService(
        db_session,
        embedding_provider=ConstantEmbeddingProvider(),
        reranker_provider=IdentityRerankerProvider({f"sticker:{target.id}:sticker": 0.9}),
    ).query(
        "开心",
        group_id="123",
        limit=3,
        include_debug=True,
    )

    assert result["items"][0]["id"] == target.id
    assert result["stats"]["vector_candidates"] >= 1
    assert result["debug_trace"]["vector_hits"][0]["candidate_id"] == f"sticker:{target.id}:sticker"


def test_sticker_search_blocks_when_reranker_required_unavailable(db_session, monkeypatch):
    from core.semantic.provider_factory import RagDegradedBlockedError, get_reranker_provider
    from core.sticker_memory import search_stickers

    sticker = _add_sticker(
        db_session,
        "blocked-sticker",
        description="震惊猫猫",
        tags=["震惊"],
        emotions=["surprised"],
    )
    _index_stickers(db_session, [sticker])

    monkeypatch.setenv("RAG_ALLOW_DEGRADED", "0")
    monkeypatch.setenv("RAG_RERANKER_ENABLED", "1")
    monkeypatch.setenv("RAG_RERANKER_URL", "")
    monkeypatch.setenv("RAG_LOCAL_RERANKER_MODEL", "./models/not-present-reranker")

    get_reranker_provider.cache_clear()
    with pytest.raises(RagDegradedBlockedError) as exc_info:
        search_stickers(db_session, "震惊", group_id="123", limit=5)
    get_reranker_provider.cache_clear()

    assert exc_info.value.fallback_reason == "reranker_unavailable"


def test_undescribed_sticker_is_not_text_rag_candidate(db_session):
    from core.sticker_memory import search_stickers

    pending = _add_sticker(
        db_session,
        "pending-describe",
        description="震惊猫猫",
        tags=["震惊"],
        describe_status="pending",
    )
    active = _add_sticker(
        db_session,
        "described",
        description="震惊猫猫",
        tags=["震惊"],
    )
    _index_stickers(db_session, [pending, active])

    results = search_stickers(
        db_session,
        "震惊",
        group_id="123",
        limit=5,
        reranker_provider=IdentityRerankerProvider({}),
    )

    assert [item["id"] for item in results] == [active.id]


def test_sticker_rag_filters_unreplyable_sticker(db_session):
    from core.sticker_memory import search_stickers

    unreplyable = _add_sticker(
        db_session,
        "unreplyable",
        file_ref="",
        send_code="",
        description="震惊猫猫",
        tags=["震惊"],
    )
    replyable = _add_sticker(
        db_session,
        "replyable",
        description="震惊猫猫",
        tags=["震惊"],
    )
    _index_stickers(db_session, [unreplyable, replyable])

    results = search_stickers(
        db_session,
        "震惊",
        group_id="123",
        limit=5,
        reranker_provider=IdentityRerankerProvider({}),
    )

    assert [item["id"] for item in results] == [replyable.id]
