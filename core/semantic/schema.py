"""语义索引 schema 初始化。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from core.database import Base, RagDebugRun, SemanticIndexItem, SemanticIndexJob


def ensure_semantic_schema(bind: Any) -> None:
    Base.metadata.create_all(
        bind=bind,
        tables=[
            SemanticIndexItem.__table__,
            SemanticIndexJob.__table__,
            RagDebugRun.__table__,
        ],
    )

    if hasattr(bind, "begin"):
        with bind.begin() as conn:
            _ensure_fts(conn)
    else:
        _ensure_fts(bind)


def _ensure_fts(conn: Any) -> None:
    conn.execute(text(
        "CREATE VIRTUAL TABLE IF NOT EXISTS semantic_index_fts USING fts5("
        "title, "
        "text, "
        "lexical_text, "
        "source_type UNINDEXED, "
        "source_id UNINDEXED, "
        "source_sub_id UNINDEXED, "
        "tokenize = 'trigram'"
        ")"
    ))
