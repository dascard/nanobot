"""RAG benchmark fixture 数据。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from core.database import Base, GroupMemory, KnowledgeChunk, KnowledgeDocument, StickerMemory
from core.semantic.adapters import SemanticChunk, chunk_from_knowledge_chunk, chunk_from_sticker
from core.semantic.indexer import upsert_semantic_chunks
from core.sticker_memory import GLOBAL_STICKER_STREAM_ID, normalize_sticker_stream_id
from evals.rag_benchmark.schema import BenchmarkCase


FIXTURE_PRESET = "positive_v1"
MEMORY_CASE_ID = "memory_fixture_positive_001"
MEMORY_SOURCE_ID = "fixture-memory-positive-001"
MEMORY_SOURCE_SUB_ID = "card:0"
MEMORY_CANDIDATE_ID = f"memory_digest:{MEMORY_SOURCE_ID}:{MEMORY_SOURCE_SUB_ID}"
MEMORY_USER_ID = "rag_fixture_user"
MEMORY_SESSION_ID = "rag_fixture_session"
MEMORY_OTHER_USER_ID = "rag_fixture_other_user"
MEMORY_OTHER_SESSION_ID = "rag_fixture_other_session"
MEMORY_OTHER_USER_SOURCE_ID = "fixture-memory-decoy-other-user"
MEMORY_OTHER_SESSION_SOURCE_ID = "fixture-memory-decoy-other-session"
MEMORY_SESSION_SUMMARY_SOURCE_ID = "fixture-memory-decoy-session-summary"
MEMORY_OTHER_USER_CANDIDATE_ID = f"memory_digest:{MEMORY_OTHER_USER_SOURCE_ID}:{MEMORY_SOURCE_SUB_ID}"
MEMORY_OTHER_SESSION_CANDIDATE_ID = f"memory_digest:{MEMORY_OTHER_SESSION_SOURCE_ID}:{MEMORY_SOURCE_SUB_ID}"
MEMORY_SESSION_SUMMARY_SOURCE_SUB_ID = "digest:level2"
MEMORY_SESSION_SUMMARY_CANDIDATE_ID = (
    f"session_summary:{MEMORY_SESSION_SUMMARY_SOURCE_ID}:{MEMORY_SESSION_SUMMARY_SOURCE_SUB_ID}"
)
MEMORY_QUERY = "KohakuVQ 端口冲突"
MEMORY_INDEX_VERSION = "fixture:v1:memory"
KNOWLEDGE_CASE_ID = "knowledge_fixture_positive_001"
KNOWLEDGE_DOCUMENT_ID = 9001
KNOWLEDGE_LOW_TRUST_DOCUMENT_ID = 9002
KNOWLEDGE_WRONG_SOURCE_DOCUMENT_ID = 9003
KNOWLEDGE_OLD_PUBLISHED_DOCUMENT_ID = 9004
KNOWLEDGE_CHUNK_ID = "chunk:0"
KNOWLEDGE_CANDIDATE_ID = f"knowledge:{KNOWLEDGE_DOCUMENT_ID}:{KNOWLEDGE_CHUNK_ID}"
KNOWLEDGE_LOW_TRUST_CANDIDATE_ID = f"knowledge:{KNOWLEDGE_LOW_TRUST_DOCUMENT_ID}:{KNOWLEDGE_CHUNK_ID}"
KNOWLEDGE_WRONG_SOURCE_CANDIDATE_ID = f"knowledge:{KNOWLEDGE_WRONG_SOURCE_DOCUMENT_ID}:{KNOWLEDGE_CHUNK_ID}"
KNOWLEDGE_OLD_PUBLISHED_CANDIDATE_ID = f"knowledge:{KNOWLEDGE_OLD_PUBLISHED_DOCUMENT_ID}:{KNOWLEDGE_CHUNK_ID}"
KNOWLEDGE_QUERY = "RAG 引用门禁"
KNOWLEDGE_INDEX_VERSION = "fixture:v1:knowledge"
STICKER_CASE_ID = "sticker_fixture_positive_001"
STICKER_ID = 9101
STICKER_OTHER_STREAM_ID = 9102
STICKER_GLOBAL_ID = 9103
STICKER_CANDIDATE_ID = f"sticker:{STICKER_ID}:sticker"
STICKER_OTHER_STREAM_CANDIDATE_ID = f"sticker:{STICKER_OTHER_STREAM_ID}:sticker"
STICKER_GLOBAL_CANDIDATE_ID = f"sticker:{STICKER_GLOBAL_ID}:sticker"
STICKER_CHAT_STREAM_ID = "group:rag-fixture-sticker"
STICKER_OTHER_STREAM_CHAT_STREAM_ID = "group:rag-fixture-sticker-other"
STICKER_QUERY = "开心拍桌表情包"
STICKER_INDEX_VERSION = "fixture:v1:sticker"
GROUP_MEMORY_CASE_ID = "group_memory_fixture_positive_001"
GROUP_MEMORY_ID = 9201
GROUP_MEMORY_DECOY_ID = 9202
GROUP_MEMORY_CANDIDATE_ID = f"group_memory:{GROUP_MEMORY_ID}:memory"
GROUP_MEMORY_DECOY_CANDIDATE_ID = f"group_memory:{GROUP_MEMORY_DECOY_ID}:memory"
GROUP_MEMORY_GROUP_ID = "group_rag_fixture_memory"
GROUP_MEMORY_DECOY_GROUP_ID = "group_rag_fixture_other"
GROUP_MEMORY_QUERY = "群体记忆 RAG fixture 正例"


def _recallable_digest_metadata(*, user_id: str, session_id: str) -> dict:
    """构造通过生产召回门禁的 LLM digest fixture 元数据。"""

    return {
        "schema_version": 2,
        "status": "active",
        "generator": "llm",
        "llm_status": "success",
        "quality_score": 0.9,
        "quality_issues": [],
        "user_id": user_id,
        "session_id": session_id,
        "fixture": FIXTURE_PRESET,
    }


def _db_time(year: int, month: int, day: int, hour: int, minute: int, second: int) -> datetime:
    # RAG benchmark fixture 写入 SQLite ORM DateTime，保持 naive 本地墙钟时间语义。
    return datetime(year, month, day, hour, minute, second)  # noqa: DTZ001


def _ensure_supported_preset(preset: str) -> None:
    if preset != FIXTURE_PRESET:
        raise ValueError(f"unsupported rag benchmark fixture preset: {preset}")


def _memory_positive_case() -> BenchmarkCase:
    return BenchmarkCase(
        id=MEMORY_CASE_ID,
        suite="rag_benchmark",
        source_type="memory",
        case_type="positive",
        query=MEMORY_QUERY,
        filters={
            "source": "digest",
            "user_id": MEMORY_USER_ID,
            "session_id": MEMORY_SESSION_ID,
        },
        expected={
            "candidate_ids": [MEMORY_CANDIDATE_ID],
            "forbidden_candidate_ids": [
                MEMORY_OTHER_USER_CANDIDATE_ID,
                MEMORY_OTHER_SESSION_CANDIDATE_ID,
                MEMORY_SESSION_SUMMARY_CANDIDATE_ID,
            ],
            "hit_at": 5,
            "expected_source_type": "memory_digest",
        },
        meta={
            "origin": "fixture_exact",
            "sensitivity": "safe",
            "fixture": FIXTURE_PRESET,
        },
    )


def _knowledge_positive_case() -> BenchmarkCase:
    return BenchmarkCase(
        id=KNOWLEDGE_CASE_ID,
        suite="rag_benchmark",
        source_type="knowledge",
        case_type="positive",
        query=KNOWLEDGE_QUERY,
        filters={
            "min_trust_level": "high",
            "source_type": "manual_file",
            "published_after": "2026-01-01",
        },
        expected={
            "candidate_ids": [KNOWLEDGE_CANDIDATE_ID],
            "forbidden_candidate_ids": [
                KNOWLEDGE_LOW_TRUST_CANDIDATE_ID,
                KNOWLEDGE_WRONG_SOURCE_CANDIDATE_ID,
                KNOWLEDGE_OLD_PUBLISHED_CANDIDATE_ID,
            ],
            "hit_at": 5,
            "expected_source_type": "knowledge",
            "requires_citation": True,
        },
        meta={
            "origin": "fixture_exact",
            "sensitivity": "safe",
            "fixture": FIXTURE_PRESET,
        },
    )


def _sticker_positive_case() -> BenchmarkCase:
    return BenchmarkCase(
        id=STICKER_CASE_ID,
        suite="rag_benchmark",
        source_type="sticker",
        case_type="positive",
        query=STICKER_QUERY,
        filters={
            "chat_stream_id": STICKER_CHAT_STREAM_ID,
            "include_global": False,
        },
        expected={
            "candidate_ids": [STICKER_CANDIDATE_ID],
            "forbidden_candidate_ids": [
                STICKER_OTHER_STREAM_CANDIDATE_ID,
                STICKER_GLOBAL_CANDIDATE_ID,
            ],
            "hit_at": 5,
            "expected_source_type": "sticker",
            "requires_sendable": True,
        },
        meta={
            "origin": "fixture_exact",
            "sensitivity": "safe",
            "fixture": FIXTURE_PRESET,
        },
    )


def _group_memory_positive_case() -> BenchmarkCase:
    return BenchmarkCase(
        id=GROUP_MEMORY_CASE_ID,
        suite="rag_benchmark",
        source_type="group_memory",
        case_type="positive",
        query=GROUP_MEMORY_QUERY,
        filters={
            "group_id": GROUP_MEMORY_GROUP_ID,
            "recent_messages": [],
            "max_chars": 1200,
        },
        expected={
            "candidate_ids": [GROUP_MEMORY_CANDIDATE_ID],
            "forbidden_candidate_ids": [GROUP_MEMORY_DECOY_CANDIDATE_ID],
            "hit_at": 5,
            "expected_source_type": "group_memory",
            "requires_group_id": True,
        },
        meta={
            "origin": "fixture_exact",
            "sensitivity": "safe",
            "fixture": FIXTURE_PRESET,
        },
    )


def fixture_cases(preset: str = FIXTURE_PRESET) -> list[BenchmarkCase]:
    """返回 fixture preset 对应的 case 描述，不写数据库。"""

    _ensure_supported_preset(str(preset))
    return [
        _memory_positive_case(),
        _knowledge_positive_case(),
        _sticker_positive_case(),
        _group_memory_positive_case(),
    ]


def _seed_knowledge_positive_fixture(db: Session) -> None:
    now = _db_time(2026, 6, 20, 0, 0, 0)
    semantic_chunks: list[SemanticChunk] = []

    def add_knowledge_doc(
        *,
        document_id: int,
        title: str,
        text: str,
        document_kind: str,
        trust_level: str,
        published_at: str,
    ) -> None:
        document = KnowledgeDocument(
            id=document_id,
            document_kind=document_kind,
            title=title,
            published_at=published_at,
            status="active",
            trust_level=trust_level,
            created_by="fixture",
            updated_by="fixture",
            latest_seen=now,
            meta_json=json.dumps({"fixture": FIXTURE_PRESET}, ensure_ascii=False, sort_keys=True),
            created_at=now,
            updated_at=now,
        )
        db.add(document)
        db.flush()

        citation = {
            "document_id": str(document_id),
            "chunk_id": KNOWLEDGE_CHUNK_ID,
            "title": title,
            "trust_level": trust_level,
            "published_at": published_at,
        }
        chunk = KnowledgeChunk(
            document_id=document_id,
            chunk_id=KNOWLEDGE_CHUNK_ID,
            order_index=0,
            title=title,
            text=text,
            citation_json=json.dumps(citation, ensure_ascii=False, sort_keys=True),
            status="active",
            trust_level=trust_level,
            meta_json=json.dumps({"fixture": FIXTURE_PRESET}, ensure_ascii=False, sort_keys=True),
            created_at=now,
            updated_at=now,
        )
        db.add(chunk)
        db.flush()
        semantic_chunks.append(chunk_from_knowledge_chunk(chunk, document=document))

    add_knowledge_doc(
        document_id=KNOWLEDGE_DOCUMENT_ID,
        title="RAG 引用门禁说明",
        text=(
            "RAG 引用门禁要求 knowledge 检索返回项必须携带 citation。"
            "固定 fixture 用于验证 high trust 正例。"
        ),
        document_kind="manual_file",
        trust_level="high",
        published_at="2026-06-20",
    )
    add_knowledge_doc(
        document_id=KNOWLEDGE_LOW_TRUST_DOCUMENT_ID,
        title="RAG 低信任 decoy",
        text="RAG 引用门禁 decoy：低 trust 文档不应通过 high trust 过滤。",
        document_kind="manual_file",
        trust_level="low",
        published_at="2026-06-20",
    )
    add_knowledge_doc(
        document_id=KNOWLEDGE_WRONG_SOURCE_DOCUMENT_ID,
        title="RAG 错误来源 decoy",
        text="RAG 引用门禁 decoy：ai_daily 来源不应通过 manual_file 过滤。",
        document_kind="ai_daily",
        trust_level="high",
        published_at="2026-06-20",
    )
    add_knowledge_doc(
        document_id=KNOWLEDGE_OLD_PUBLISHED_DOCUMENT_ID,
        title="RAG 旧发布时间 decoy",
        text="RAG 引用门禁 decoy：旧发布时间不应通过 published_after 过滤。",
        document_kind="manual_file",
        trust_level="high",
        published_at="2025-01-01",
    )
    upsert_semantic_chunks(db, semantic_chunks, index_version=KNOWLEDGE_INDEX_VERSION)


def _seed_sticker_positive_fixture(db: Session) -> None:
    now = _db_time(2026, 6, 20, 0, 0, 0)
    semantic_chunks: list[SemanticChunk] = []

    def add_sticker(
        *,
        sticker_id: int,
        chat_stream_id: str,
        sticker_hash: str,
        name: str,
        description: str,
    ) -> None:
        sticker = StickerMemory(
            id=sticker_id,
            chat_stream_id=normalize_sticker_stream_id(chat_stream_id=chat_stream_id),
            sticker_hash=sticker_hash,
            file_ref=f"https://example.com/{sticker_hash}.png",
            send_code=f"[CQ:image,file=https://example.com/{sticker_hash}.png]",
            name=name,
            description=description,
            tags_json=json.dumps(["开心", "拍桌", "表情包"], ensure_ascii=False),
            emotions_json=json.dumps(["happy"], ensure_ascii=False),
            source_type="fixture",
            source_count=1,
            status="active",
            usage_count=0,
            first_seen=now,
            last_seen=now,
            meta_json=json.dumps({"fixture": FIXTURE_PRESET}, ensure_ascii=False, sort_keys=True),
            preview_status="pending",
            content_hash=sticker_hash,
            dedupe_status="unique",
            describe_status="ok",
            described_at=now,
            created_at=now,
        )
        db.add(sticker)
        db.flush()
        semantic_chunk = chunk_from_sticker(sticker)
        assert semantic_chunk is not None
        semantic_chunks.append(semantic_chunk)

    add_sticker(
        sticker_id=STICKER_ID,
        chat_stream_id=STICKER_CHAT_STREAM_ID,
        sticker_hash="fixture-sticker-positive-001",
        name="开心拍桌",
        description="开心拍桌表情包，适合表达高兴、赞同和突然兴奋。",
    )
    add_sticker(
        sticker_id=STICKER_OTHER_STREAM_ID,
        chat_stream_id=STICKER_OTHER_STREAM_CHAT_STREAM_ID,
        sticker_hash="fixture-sticker-decoy-other-stream-001",
        name="开心拍桌其他群",
        description="开心拍桌表情包 decoy：其他 stream 不应在目标 stream 查询中返回。",
    )
    add_sticker(
        sticker_id=STICKER_GLOBAL_ID,
        chat_stream_id=GLOBAL_STICKER_STREAM_ID,
        sticker_hash="fixture-sticker-decoy-global-001",
        name="开心拍桌全局",
        description="开心拍桌表情包 decoy：include_global=false 时全局表情不应返回。",
    )
    upsert_semantic_chunks(db, semantic_chunks, index_version=STICKER_INDEX_VERSION)


def _seed_group_memory_positive_fixture(db: Session) -> None:
    now = _db_time(2026, 6, 20, 0, 0, 0)
    meta = {"fixture": FIXTURE_PRESET, "evidence_short_summary": GROUP_MEMORY_QUERY}
    rows = [
        GroupMemory(
            id=GROUP_MEMORY_ID,
            group_id=GROUP_MEMORY_GROUP_ID,
            memory_type="topic",
            content="群体记忆 RAG fixture 正例：本群固定用来验证 group_memory 检索命中。",
            content_hash="fixture-group-memory-positive-001",
            cluster_key="rag fixture group memory",
            evidence_log_ids_json=json.dumps([920101, 920102]),
            confidence=0.9,
            evidence_count=2,
            first_seen=now,
            last_seen=now,
            updated_at=now,
            decay_score=1.0,
            status="active",
            inject_policy="auto",
            source="fixture",
            meta_json=json.dumps(meta, ensure_ascii=False, sort_keys=True),
            created_at=now,
        ),
        GroupMemory(
            id=GROUP_MEMORY_DECOY_ID,
            group_id=GROUP_MEMORY_DECOY_GROUP_ID,
            memory_type="topic",
            content="群体记忆 RAG fixture 正例：其他群的 decoy 用来验证 group filter 不泄漏。",
            content_hash="fixture-group-memory-decoy-001",
            cluster_key="rag fixture group memory decoy",
            evidence_log_ids_json=json.dumps([920201, 920202, 920203]),
            confidence=0.95,
            evidence_count=3,
            first_seen=now,
            last_seen=now,
            updated_at=now,
            decay_score=1.0,
            status="active",
            inject_policy="auto",
            source="fixture",
            meta_json=json.dumps(meta, ensure_ascii=False, sort_keys=True),
            created_at=now,
        ),
    ]
    db.add_all(rows)
    db.flush()


def seed_positive_fixture_db(db: Session) -> list[BenchmarkCase]:
    """向已创建 schema 的数据库写入 positive fixture 数据。"""

    text = (
        "KohakuVQ 服务部署时出现 uvicorn 8000 端口冲突，"
        "处理方式是检查占用进程、释放端口或切换启动端口。"
    )
    lexical = f"{MEMORY_QUERY} uvicorn 8000 端口占用 排查"
    memory_chunks = [
        SemanticChunk(
            source_type="memory_digest",
            source_id=MEMORY_SOURCE_ID,
            source_sub_id=MEMORY_SOURCE_SUB_ID,
            title="KohakuVQ 端口冲突排查",
            text=text,
            lexical_text=lexical,
            embedding_text=lexical,
            metadata=_recallable_digest_metadata(
                user_id=MEMORY_USER_ID,
                session_id=MEMORY_SESSION_ID,
            ),
            visibility="recall",
            quality_score=0.9,
            trust_level="medium",
            source_prior=0.65,
        ),
        SemanticChunk(
            source_type="memory_digest",
            source_id=MEMORY_OTHER_USER_SOURCE_ID,
            source_sub_id=MEMORY_SOURCE_SUB_ID,
            title="KohakuVQ 其他用户端口冲突",
            text=f"{text} 这是其他用户 decoy，不允许被目标用户召回。",
            lexical_text=lexical,
            embedding_text=lexical,
            metadata=_recallable_digest_metadata(
                user_id=MEMORY_OTHER_USER_ID,
                session_id=MEMORY_SESSION_ID,
            ),
            visibility="recall",
            quality_score=0.95,
            trust_level="medium",
            source_prior=0.70,
        ),
        SemanticChunk(
            source_type="memory_digest",
            source_id=MEMORY_OTHER_SESSION_SOURCE_ID,
            source_sub_id=MEMORY_SOURCE_SUB_ID,
            title="KohakuVQ 其他会话端口冲突",
            text=f"{text} 这是其他 session decoy，不允许被目标 session 召回。",
            lexical_text=lexical,
            embedding_text=lexical,
            metadata=_recallable_digest_metadata(
                user_id=MEMORY_USER_ID,
                session_id=MEMORY_OTHER_SESSION_ID,
            ),
            visibility="recall",
            quality_score=0.95,
            trust_level="medium",
            source_prior=0.70,
        ),
        SemanticChunk(
            source_type="session_summary",
            source_id=MEMORY_SESSION_SUMMARY_SOURCE_ID,
            source_sub_id=MEMORY_SESSION_SUMMARY_SOURCE_SUB_ID,
            title="KohakuVQ session summary decoy",
            text=f"{text} 这是 session_summary decoy，source=digest 时不允许返回。",
            lexical_text=lexical,
            embedding_text=lexical,
            metadata={
                "user_id": MEMORY_USER_ID,
                "session_id": MEMORY_SESSION_ID,
                "fixture": FIXTURE_PRESET,
            },
            visibility="recall",
            quality_score=0.95,
            trust_level="medium",
            source_prior=0.70,
        ),
    ]
    upsert_semantic_chunks(db, memory_chunks, index_version=MEMORY_INDEX_VERSION)
    _seed_knowledge_positive_fixture(db)
    _seed_sticker_positive_fixture(db)
    _seed_group_memory_positive_fixture(db)
    db.commit()
    return fixture_cases(FIXTURE_PRESET)


def _unlink_sqlite_files(path: Path) -> None:
    for raw in (str(path), f"{path}-wal", f"{path}-shm"):
        target = Path(raw)
        if target.exists():
            target.unlink()


def build_fixture_db(path: str | Path, *, preset: str = FIXTURE_PRESET) -> list[BenchmarkCase]:
    """覆盖创建 fixture SQLite 文件库，并返回 fixture cases。"""

    _ensure_supported_preset(str(preset))
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _unlink_sqlite_files(db_path)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _configure_fixture_build(dbapi_connection, _connection_record):
        # fixture 可随时重建，不为数十条建表 DDL 支付逐条磁盘同步成本。
        dbapi_connection.execute("PRAGMA journal_mode=MEMORY")
        dbapi_connection.execute("PRAGMA synchronous=OFF")

    try:
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        db = SessionLocal()
        try:
            return seed_positive_fixture_db(db)
        finally:
            db.close()
    finally:
        engine.dispose()
