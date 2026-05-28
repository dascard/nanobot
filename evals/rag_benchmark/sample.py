"""从真实 DB 只读抽取 RAG benchmark case。"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.database import GroupMemory, KnowledgeChunk, KnowledgeDocument, SemanticIndexItem, StickerMemory
from core.sticker_memory import is_sticker_replyable, sticker_has_text_index_payload
from evals.rag_benchmark.schema import BenchmarkCase


MEMORY_SCAN_MULTIPLIER = 20
MEMORY_MAX_SCAN = 400
MEMORY_PROBE_QUERIES = 12
GENERATOR_VERSION = "rag_benchmark:v1"


def _readonly_session(db_path: str | Path) -> Session:
    path = Path(db_path).resolve().as_posix()
    engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true", connect_args={"check_same_thread": False})
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _query_text(*parts: str) -> str:
    text = " ".join(str(part or "").strip() for part in parts if str(part or "").strip())
    text = " ".join(text.split())
    return text[:40] or "RAG"


def _table_exists(db: Session, table_name: str) -> bool:
    row = db.execute(
        text("SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = :name LIMIT 1"),
        {"name": table_name},
    ).fetchone()
    return row is not None


def db_fingerprint(db: Session, db_path: str | Path) -> dict:
    path = Path(db_path).resolve()
    stat = path.stat()
    semantic_index_count = 0
    if _table_exists(db, "semantic_index_items"):
        semantic_index_count = int(db.execute(text("SELECT COUNT(*) FROM semantic_index_items")).scalar() or 0)
    return {
        "path": path.as_posix(),
        "size": int(stat.st_size),
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "mtime_ns": int(stat.st_mtime_ns),
        "semantic_index_count": semantic_index_count,
    }


def attach_generation_meta(cases: Iterable[BenchmarkCase], fingerprint: dict) -> list[BenchmarkCase]:
    generated_at = datetime.now().isoformat()
    result = list(cases)
    for case in result:
        case.meta["db_fingerprint"] = fingerprint
        case.meta["generated_at"] = generated_at
        case.meta["generator_version"] = GENERATOR_VERSION
    return result


def _memory_query_candidates(text: str, title: str = "") -> list[str]:
    cleaned = re.sub(r"\[System: Older context truncated\.\.\.\]", " ", str(text or ""))
    cleaned = re.sub(r"\[\d{1,2}:\d{2}\]", " ", cleaned)
    cleaned = re.sub(r"\b(?:ambient|user|assistant|tool)\([^)]*\):", " ", cleaned)
    cleaned = re.sub(r"\[[^\]]{1,40}\]:", " ", cleaned)
    lines = [" ".join(line.split()) for line in cleaned.splitlines()]
    lines = [
        line
        for line in lines
        if len(line) >= 8 and "[图片" not in line and line not in {"签到", "打卡"}
    ]
    candidates: list[str] = []
    candidates.extend(line[:40] for line in lines[:20])
    compact = " ".join(lines)
    for start in range(0, min(len(compact), 1200), 120):
        snippet = compact[start:start + 40].strip()
        if len(snippet) >= 8:
            candidates.append(snippet)
    if title:
        candidates.append(_query_text(title, compact))
    seen: set[str] = set()
    deduped: list[str] = []
    for candidate in candidates:
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped[:MEMORY_PROBE_QUERIES] or [_query_text(title, text)]


def _case(
    *,
    case_id: str,
    source_type: str,
    query: str,
    candidate_id: str,
    filters: dict | None = None,
    meta: dict | None = None,
    requires_citation: bool = False,
    requires_sendable: bool = False,
    requires_group_id: bool = False,
) -> BenchmarkCase:
    merged_meta = {"origin": "generated_exact", "sensitivity": "local_db"}
    if meta:
        merged_meta.update(meta)
    return BenchmarkCase(
        id=case_id,
        source_type=source_type,
        case_type="positive",
        query=query,
        filters=filters or {},
        expected={
            "candidate_ids": [candidate_id],
            "hit_at": 5,
            "expected_source_type": source_type if source_type != "memory" else "",
            "requires_citation": requires_citation,
            "requires_sendable": requires_sendable,
            "requires_group_id": requires_group_id,
        },
        meta=merged_meta,
    )


def _memory_probe_rank(db: Session, *, query: str, row: SemanticIndexItem, candidate_id: str) -> int | None:
    from evals.rag_benchmark.adapters import run_case_with_adapter

    case = BenchmarkCase(
        id=f"memory_probe_{row.id}",
        source_type="memory",
        case_type="positive",
        query=query,
        filters={"source": "all", "user_id": row.user_id or "", "session_id": row.session_id or ""},
        expected={"candidate_ids": [candidate_id], "hit_at": 5},
    )
    try:
        result = run_case_with_adapter(db, case, provider_mode="deterministic", readonly=True)
    except Exception:
        return None
    try:
        return result.candidate_ids.index(candidate_id) + 1
    except ValueError:
        return None


def _best_memory_query(db: Session, row: SemanticIndexItem, candidate_id: str) -> str | None:
    if not _table_exists(db, "semantic_index_fts"):
        return None
    for query in _memory_query_candidates(row.lexical_text or row.text or "", row.title or ""):
        rank = _memory_probe_rank(db, query=query, row=row, candidate_id=candidate_id)
        if rank is not None and rank <= 5:
            return query
    return None


def _sample_memory(db: Session, limit: int) -> list[BenchmarkCase]:
    base_query = (
        db.query(SemanticIndexItem)
        .filter(SemanticIndexItem.source_type == "memory_digest")
        .filter(SemanticIndexItem.status == "active")
    )
    if hasattr(SemanticIndexItem, "visibility"):
        base_query = base_query.filter(SemanticIndexItem.visibility == "recall")
    scan_limit = min(MEMORY_MAX_SCAN, max(40, int(limit) * MEMORY_SCAN_MULTIPLIER))
    rows = (
        base_query
        .filter(SemanticIndexItem.source_sub_id == "digest:level2")
        .order_by(SemanticIndexItem.indexed_at.desc(), SemanticIndexItem.id.desc())
        .limit(scan_limit)
        .all()
    )
    if len(rows) < max(1, limit):
        seen_ids = {int(row.id) for row in rows}
        fallback_rows = (
            base_query
            .order_by(SemanticIndexItem.indexed_at.desc(), SemanticIndexItem.id.desc())
            .limit(scan_limit)
            .all()
        )
        rows.extend(row for row in fallback_rows if int(row.id) not in seen_ids)

    cases: list[BenchmarkCase] = []
    for row in rows:
        if len(cases) >= limit:
            break
        candidate_id = f"{row.source_type}:{row.source_id}:{row.source_sub_id}"
        query = _best_memory_query(db, row, candidate_id)
        if not query:
            continue
        cases.append(_case(
            case_id=f"memory_generated_exact_{row.id}",
            source_type="memory",
            query=query,
            candidate_id=candidate_id,
            filters={"source": "all", "user_id": row.user_id or "", "session_id": row.session_id or ""},
            meta={"source_table": "semantic_index_items", "source_id": str(row.source_id)},
        ))
    return cases


def _sample_group_memory(db: Session, limit: int) -> list[BenchmarkCase]:
    rows = (
        db.query(GroupMemory)
        .filter(GroupMemory.status == "active")
        .filter(GroupMemory.inject_policy == "auto")
        .filter(GroupMemory.confidence >= 0.55)
        .filter(GroupMemory.decay_score >= 0.3)
        .filter(GroupMemory.evidence_log_ids_json.isnot(None))
        .filter(GroupMemory.evidence_log_ids_json != "[]")
        .order_by(GroupMemory.id.asc())
        .limit(max(1, limit))
        .all()
    )
    return [
        _case(
            case_id=f"group_memory_generated_exact_{row.id}",
            source_type="group_memory",
            query=_query_text(row.content),
            candidate_id=f"group_memory:{row.id}:memory",
            filters={"group_id": row.group_id},
            meta={"origin": "generated_exact", "source_table": "group_memories", "source_id": str(row.id)},
            requires_group_id=True,
        )
        for row in rows
    ]


def _sample_sticker(db: Session, limit: int) -> list[BenchmarkCase]:
    rows = (
        db.query(StickerMemory)
        .filter(StickerMemory.status == "active")
        .filter(StickerMemory.duplicate_of_id.is_(None))
        .filter(StickerMemory.dedupe_status != "duplicate")
        .filter(StickerMemory.describe_status == "ok")
        .order_by(StickerMemory.id.asc())
        .limit(max(1, limit * 4))
        .all()
    )
    cases: list[BenchmarkCase] = []
    for row in rows:
        if len(cases) >= limit:
            break
        if not sticker_has_text_index_payload(row) or not is_sticker_replyable(row):
            continue
        cases.append(_case(
            case_id=f"sticker_generated_exact_{row.id}",
            source_type="sticker",
            query=_query_text(row.description or "", row.tags_json or "", row.emotions_json or ""),
            candidate_id=f"sticker:{row.id}:sticker",
            filters={"chat_stream_id": row.chat_stream_id, "include_global": True},
            meta={"origin": "generated_exact", "source_table": "sticker_memories", "source_id": str(row.id)},
            requires_sendable=True,
        ))
    return cases


def _valid_citation(raw: str | None) -> bool:
    try:
        meta = json.loads(raw or "{}")
    except Exception:
        return False
    citation = meta.get("citation") if isinstance(meta, dict) else None
    if not isinstance(citation, dict) or not citation:
        return False
    return bool(citation.get("title") and citation.get("trust_level") and (
        citation.get("url") or (citation.get("document_id") and citation.get("chunk_id"))
    ))


def _sample_knowledge(db: Session, limit: int) -> list[BenchmarkCase]:
    rows = (
        db.query(SemanticIndexItem)
        .filter(SemanticIndexItem.source_type == "knowledge")
        .filter(SemanticIndexItem.status == "active")
        .order_by(SemanticIndexItem.id.asc())
        .limit(max(1, limit * 3))
        .all()
    )
    cases: list[BenchmarkCase] = []
    for row in rows:
        if len(cases) >= limit:
            break
        if not _valid_citation(row.meta_json):
            continue
        document = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == int(row.source_id)).first()
        chunk = (
            db.query(KnowledgeChunk)
            .filter(KnowledgeChunk.document_id == int(row.source_id))
            .filter(KnowledgeChunk.chunk_id == str(row.source_sub_id))
            .first()
        )
        if document is None or chunk is None or document.status != "active" or chunk.status != "active":
            continue
        cases.append(_case(
            case_id=f"knowledge_generated_exact_{row.id}",
            source_type="knowledge",
            query=_query_text(row.title or "", row.lexical_text or row.text or ""),
            candidate_id=f"knowledge:{row.source_id}:{row.source_sub_id}",
            filters={"min_trust_level": "low"},
            meta={"origin": "generated_exact", "source_table": "semantic_index_items", "source_id": str(row.source_id)},
            requires_citation=True,
        ))
    return cases


def sample_cases(db: Session, *, per_source: int = 30) -> list[BenchmarkCase]:
    return [
        *_sample_memory(db, per_source),
        *_sample_group_memory(db, per_source),
        *_sample_sticker(db, per_source),
        *_sample_knowledge(db, per_source),
    ]


def sample_cases_from_db(db_path: str | Path, *, per_source: int = 30) -> list[BenchmarkCase]:
    db = _readonly_session(db_path)
    try:
        return attach_generation_meta(sample_cases(db, per_source=per_source), db_fingerprint(db, db_path))
    finally:
        db.close()


def write_generated_cases(cases: Iterable[BenchmarkCase], out_dir: str | Path) -> dict[str, Path]:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    by_source: dict[str, list[BenchmarkCase]] = {}
    for case in cases:
        by_source.setdefault(case.source_type, []).append(case)
    paths: dict[str, Path] = {}
    for source_type, items in sorted(by_source.items()):
        path = root / f"{source_type}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for case in items:
                fh.write(json.dumps(case.model_dump(), ensure_ascii=False) + "\n")
        paths[source_type] = path
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/nanobot.db")
    parser.add_argument("--out", default="tmp/rag_benchmark/generated")
    parser.add_argument("--per-source", type=int, default=30)
    args = parser.parse_args()
    cases = sample_cases_from_db(args.db, per_source=args.per_source)
    paths = write_generated_cases(cases, args.out)
    print(f"generated_cases={len(cases)}")
    for source_type, path in paths.items():
        print(f"{source_type}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
