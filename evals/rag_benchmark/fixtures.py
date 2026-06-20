"""RAG benchmark fixture 数据。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.database import Base
from core.semantic.adapters import SemanticChunk
from core.semantic.indexer import upsert_semantic_chunks
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


def fixture_cases(preset: str = FIXTURE_PRESET) -> list[BenchmarkCase]:
    """返回 fixture preset 对应的 case 描述，不写数据库。"""

    _ensure_supported_preset(str(preset))
    return [_memory_positive_case()]


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
