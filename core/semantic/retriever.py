"""统一语义索引检索辅助函数。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import SemanticIndexItem
from core.semantic.fts import build_fts5_match_query
from core.semantic.fts import strip_cjk_query_stopwords
from core.semantic.scoring import normalize_semantic_cosine
from core.semantic.scoring import normalize_sqlite_bm25
from core.semantic.schema import ensure_semantic_schema


@dataclass(frozen=True)
class FtsRecallHit:
    item_id: int
    bm25_raw: float
    lexical_score: float


def parse_embedding(value: bytes | None) -> list[float] | None:
    if not value:
        return None
    try:
        data = json.loads(value.decode("utf-8"))
        if isinstance(data, list):
            return [float(item) for item in data]
    except Exception:
        return None
    return None


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return None
    return dot / (left_norm * right_norm)


def lexical_overlap_score(query: str, text: str) -> float:
    query_text = str(query or "").lower()
    haystack = str(text or "").lower()
    ascii_tokens = set(re.findall(r"[a-z0-9_]{2,}", query_text))
    cjk_tokens = set(re.findall(r"[\u3400-\u9fff]{2}", strip_cjk_query_stopwords(query_text)))
    tokens = ascii_tokens | cjk_tokens
    if not tokens:
        return 0.0
    hits = sum(1 for token in tokens if token in haystack)
    return min(1.0, hits / max(1, len(tokens)))


def load_recall_rows(
    db: Session,
    *,
    source_types: set[str],
    user_id: str = "",
    session_id: str = "",
    limit: int = 200,
    ensure_schema: bool = True,
) -> list[SemanticIndexItem]:
    if ensure_schema:
        ensure_semantic_schema(db.bind)
    query = (
        db.query(SemanticIndexItem)
        .filter(SemanticIndexItem.status == "active")
        .filter(SemanticIndexItem.visibility == "recall")
        .filter(SemanticIndexItem.source_type.in_(sorted(source_types)))
    )
    if user_id:
        query = query.filter(SemanticIndexItem.user_id == user_id)
    if session_id:
        query = query.filter(SemanticIndexItem.session_id == session_id)
    return query.order_by(SemanticIndexItem.id.desc()).limit(max(1, int(limit))).all()


def load_recall_rows_by_ids(
    db: Session,
    item_ids: list[int],
    *,
    ensure_schema: bool = True,
) -> dict[int, SemanticIndexItem]:
    ids = [int(item_id) for item_id in item_ids if int(item_id) > 0]
    if not ids:
        return {}
    if ensure_schema:
        ensure_semantic_schema(db.bind)
    rows = (
        db.query(SemanticIndexItem)
        .filter(SemanticIndexItem.id.in_(ids))
        .filter(SemanticIndexItem.status == "active")
        .filter(SemanticIndexItem.visibility == "recall")
        .all()
    )
    return {int(row.id): row for row in rows}


def fts_rowids_for_query(db: Session, query: str) -> set[int]:
    match_query = build_fts5_match_query(query)
    if not match_query:
        return set()
    try:
        rows = db.execute(
            text("SELECT rowid FROM semantic_index_fts WHERE semantic_index_fts MATCH :match_query"),
            {"match_query": match_query},
        ).fetchall()
    except Exception:
        return set()
    return {int(row[0]) for row in rows}


def fts_recall_hits(
    db: Session,
    query: str,
    *,
    source_types: set[str],
    user_id: str = "",
    session_id: str = "",
    limit: int = 200,
    ensure_schema: bool = True,
) -> list[FtsRecallHit]:
    match_query = build_fts5_match_query(query)
    if not match_query:
        return []
    if ensure_schema:
        ensure_semantic_schema(db.bind)
    params: dict[str, Any] = {
        "match_query": match_query,
        "limit": max(1, int(limit)),
    }
    clauses = [
        "semantic_index_fts MATCH :match_query",
        "i.status = 'active'",
        "i.visibility = 'recall'",
    ]
    if source_types:
        placeholders: list[str] = []
        for index, source_type in enumerate(sorted(source_types)):
            key = f"source_type_{index}"
            params[key] = source_type
            placeholders.append(f":{key}")
        clauses.append(f"i.source_type IN ({', '.join(placeholders)})")
    if user_id:
        params["user_id"] = user_id
        clauses.append("i.user_id = :user_id")
    if session_id:
        params["session_id"] = session_id
        clauses.append("i.session_id = :session_id")
    sql = (
        "SELECT semantic_index_fts.rowid AS item_id, "
        "bm25(semantic_index_fts) AS bm25_raw "
        "FROM semantic_index_fts "
        "JOIN semantic_index_items AS i ON i.id = semantic_index_fts.rowid "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY bm25_raw ASC "
        "LIMIT :limit"
    )
    try:
        rows = db.execute(text(sql), params).fetchall()
    except Exception:
        return []
    if not rows:
        return []
    raw_scores = [float(row.bm25_raw) for row in rows]
    best = min(raw_scores)
    worst = max(raw_scores)
    return [
        FtsRecallHit(
            item_id=int(row.item_id),
            bm25_raw=float(row.bm25_raw),
            lexical_score=max(
                0.10,
                normalize_sqlite_bm25(float(row.bm25_raw), best=best, worst=worst) or 0.0,
            ),
        )
        for row in rows
    ]


def semantic_score_for_row(
    row: SemanticIndexItem,
    *,
    query_vector: list[float] | None,
    embedding_provider: Any = None,
    floor: float = 0.25,
) -> float | None:
    if query_vector is None:
        return None
    row_vector = parse_embedding(row.embedding)
    if row_vector is None and embedding_provider is not None:
        try:
            row_vector = [float(item) for item in embedding_provider.embed([row.embedding_text or row.lexical_text or row.text])[0]]
        except Exception:
            row_vector = None
    cosine = cosine_similarity(query_vector, row_vector)
    return normalize_semantic_cosine(cosine, floor=floor)
