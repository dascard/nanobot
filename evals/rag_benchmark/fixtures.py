"""RAG benchmark fixture 数据。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.database import Base, KnowledgeChunk, KnowledgeDocument, StickerMemory
from core.semantic.adapters import SemanticChunk, chunk_from_knowledge_chunk, chunk_from_sticker
from core.semantic.indexer import upsert_semantic_chunks
from core.sticker_memory import normalize_sticker_stream_id
from evals.rag_benchmark.schema import BenchmarkCase


FIXTURE_PRESET = "positive_v1"
MEMORY_CASE_ID = "memory_fixture_positive_001"
MEMORY_SOURCE_ID = "fixture-memory-positive-001"
MEMORY_SOURCE_SUB_ID = "card:0"
MEMORY_CANDIDATE_ID = f"memory_digest:{MEMORY_SOURCE_ID}:{MEMORY_SOURCE_SUB_ID}"
MEMORY_USER_ID = "rag_fixture_user"
MEMORY_SESSION_ID = "rag_fixture_session"
MEMORY_QUERY = "KohakuVQ 端口冲突"
MEMORY_INDEX_VERSION = "fixture:v1:memory"
KNOWLEDGE_CASE_ID = "knowledge_fixture_positive_001"
KNOWLEDGE_DOCUMENT_ID = 9001
KNOWLEDGE_CHUNK_ID = "chunk:0"
KNOWLEDGE_CANDIDATE_ID = f"knowledge:{KNOWLEDGE_DOCUMENT_ID}:{KNOWLEDGE_CHUNK_ID}"
KNOWLEDGE_QUERY = "RAG 引用门禁"
KNOWLEDGE_INDEX_VERSION = "fixture:v1:knowledge"
STICKER_CASE_ID = "sticker_fixture_positive_001"
STICKER_ID = 9101
STICKER_CANDIDATE_ID = f"sticker:{STICKER_ID}:sticker"
STICKER_CHAT_STREAM_ID = "group:rag-fixture-sticker"
STICKER_QUERY = "开心拍桌表情包"
STICKER_INDEX_VERSION = "fixture:v1:sticker"


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
            "min_trust_level": "low",
            "source_type": "manual_file",
        },
        expected={
            "candidate_ids": [KNOWLEDGE_CANDIDATE_ID],
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


def fixture_cases(preset: str = FIXTURE_PRESET) -> list[BenchmarkCase]:
    """返回 fixture preset 对应的 case 描述，不写数据库。"""

    _ensure_supported_preset(str(preset))
    return [_memory_positive_case(), _knowledge_positive_case(), _sticker_positive_case()]


def _seed_knowledge_positive_fixture(db: Session) -> None:
    now = datetime(2026, 6, 20, 0, 0, 0)
    document = KnowledgeDocument(
        id=KNOWLEDGE_DOCUMENT_ID,
        document_kind="manual_file",
        title="RAG 引用门禁说明",
        published_at="2026-06-20",
        status="active",
        trust_level="medium",
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
        "document_id": str(KNOWLEDGE_DOCUMENT_ID),
        "chunk_id": KNOWLEDGE_CHUNK_ID,
        "title": "RAG 引用门禁说明",
        "trust_level": "medium",
        "published_at": "2026-06-20",
    }
    chunk = KnowledgeChunk(
        document_id=KNOWLEDGE_DOCUMENT_ID,
        chunk_id=KNOWLEDGE_CHUNK_ID,
        order_index=0,
        title="RAG 引用门禁说明",
        text=(
            "RAG 引用门禁要求 knowledge 检索返回项必须携带 citation。"
            "固定 fixture 用于验证 requires_citation 评分不会被空结果绕过。"
        ),
        citation_json=json.dumps(citation, ensure_ascii=False, sort_keys=True),
        status="active",
        trust_level="medium",
        meta_json=json.dumps({"fixture": FIXTURE_PRESET}, ensure_ascii=False, sort_keys=True),
        created_at=now,
        updated_at=now,
    )
    db.add(chunk)
    db.flush()

    semantic_chunk = chunk_from_knowledge_chunk(chunk, document=document)
    upsert_semantic_chunks(
        db,
        [semantic_chunk],
        index_version=KNOWLEDGE_INDEX_VERSION,
    )


def _seed_sticker_positive_fixture(db: Session) -> None:
    now = datetime(2026, 6, 20, 0, 0, 0)
    sticker = StickerMemory(
        id=STICKER_ID,
        chat_stream_id=normalize_sticker_stream_id(chat_stream_id=STICKER_CHAT_STREAM_ID),
        sticker_hash="fixture-sticker-positive-001",
        file_ref="https://example.com/fixture-sticker-positive-001.png",
        send_code="[CQ:image,file=https://example.com/fixture-sticker-positive-001.png]",
        name="开心拍桌",
        description="开心拍桌表情包，适合表达高兴、赞同和突然兴奋。",
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
        content_hash="fixture-sticker-positive-001",
        dedupe_status="unique",
        describe_status="ok",
        described_at=now,
        created_at=now,
    )
    db.add(sticker)
    db.flush()

    semantic_chunk = chunk_from_sticker(sticker)
    assert semantic_chunk is not None
    upsert_semantic_chunks(
        db,
        [semantic_chunk],
        index_version=STICKER_INDEX_VERSION,
    )


def seed_positive_fixture_db(db: Session) -> list[BenchmarkCase]:
    """向已创建 schema 的数据库写入 positive fixture 数据。"""

    text = (
        "KohakuVQ 服务部署时出现 uvicorn 8000 端口冲突，"
        "处理方式是检查占用进程、释放端口或切换启动端口。"
    )
    lexical = f"{MEMORY_QUERY} uvicorn 8000 端口占用 排查"
    chunk = SemanticChunk(
        source_type="memory_digest",
        source_id=MEMORY_SOURCE_ID,
        source_sub_id=MEMORY_SOURCE_SUB_ID,
        title="KohakuVQ 端口冲突排查",
        text=text,
        lexical_text=lexical,
        embedding_text=lexical,
        metadata={
            "user_id": MEMORY_USER_ID,
            "session_id": MEMORY_SESSION_ID,
            "fixture": FIXTURE_PRESET,
        },
        visibility="recall",
        quality_score=0.9,
        trust_level="medium",
        source_prior=0.65,
    )
    upsert_semantic_chunks(db, [chunk], index_version=MEMORY_INDEX_VERSION)
    _seed_knowledge_positive_fixture(db)
    _seed_sticker_positive_fixture(db)
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
