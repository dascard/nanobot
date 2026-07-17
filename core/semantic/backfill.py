"""从现有业务表回填统一语义索引。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, case, cast, func, tuple_
from sqlalchemy.orm import Session

from core.database import (
    GroupMemory,
    KnowledgeChunk,
    KnowledgeDocument,
    MemoryDigest,
    RollingSessionSummary,
    SemanticIndexItem,
    SemanticIndexJob,
    StickerMemory,
)
from core.semantic.adapters import (
    SemanticChunk,
    chunk_from_group_memory,
    chunk_from_knowledge_chunk,
    chunk_from_sticker,
    chunks_from_memory_digest,
    chunks_from_session_summary,
    is_recallable_memory_digest_meta,
    is_recallable_knowledge_chunk,
    is_recallable_knowledge_document,
    memory_digest_source_id,
    memory_digest_source_revision,
    session_summary_kind_rank,
    session_summary_source_revision,
)
from core.semantic.indexer import (
    source_hash_for_chunk,
    source_revision_for_chunks,
    stable_hash,
)
from core.semantic.schema import ensure_semantic_schema
from core.semantic.jobs import semantic_job_origin


DEFAULT_BACKFILL_INDEX_VERSION = ""
_SOURCE_ALIASES = {
    "memory": ["memory_digest", "session_summary"],
    "all": ["memory_digest", "session_summary", "group_memory", "sticker", "knowledge"],
}
_SOURCE_TYPES = {"memory_digest", "session_summary", "group_memory", "sticker", "knowledge"}
_BACKFILL_STAGE_ORDER = (
    "memory_digest",
    "session_summary",
    "group_memory",
    "sticker",
    "knowledge",
)
BACKFILL_CURSOR_VERSION = 2
SEMANTIC_ADAPTER_MANIFEST = hashlib.sha256(
    b"semantic-adapters:v4:policy-hash:knowledge-recallability"
).hexdigest()[:24]
_BACKFILL_CURSOR_EPHEMERAL_KEY = secrets.token_bytes(32)
_BACKFILL_CURSOR_KEY_DOMAIN = b"nanobot-semantic-backfill-cursor-v2\x00"


@dataclass(frozen=True, slots=True)
class SemanticBackfillCursor:
    version: int
    source_type: str
    after_anchor: int
    high_water: int
    target_index_version: str
    adapter_manifest: str
    high_waters: dict[str, int]


@dataclass(frozen=True, slots=True)
class _BackfillSourceSnapshot:
    source_type: str
    source_id: str
    anchor: int
    source_revision: str
    chunks: tuple[SemanticChunk, ...]
    document_ids: tuple[int, ...]
    delete_source_ids: tuple[str, ...]
    index_version: str = ""


@dataclass(frozen=True, slots=True)
class _BackfillClassifiedSource:
    snapshot: _BackfillSourceSnapshot
    category: str
    reasons: tuple[str, ...]
    active_item_ids: tuple[int, ...]
    active_index_versions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _BackfillSnapshotPage:
    snapshots: tuple[_BackfillSourceSnapshot, ...]
    last_anchor: int
    has_more: bool


def source_types_for_backfill(source_type: str) -> list[str]:
    source = str(source_type or "all").strip() or "all"
    if source in _SOURCE_ALIASES:
        return list(_SOURCE_ALIASES[source])
    if source in _SOURCE_TYPES:
        return [source]
    return [source]


def _load_memory_digest_chunks(db: Session, limit: int) -> tuple[int, list[SemanticChunk]]:
    # 新数据已经过 LLM 审计；先扫描最新行，避免旧 fallback 占满回填预算。
    rows = db.query(MemoryDigest).order_by(MemoryDigest.id.desc()).limit(limit).all()
    chunks: list[SemanticChunk] = []
    for row in rows:
        chunks.extend(chunk for chunk in chunks_from_memory_digest(row) if chunk.text or chunk.lexical_text)
    return len(rows), chunks


def _load_session_summary_chunks(db: Session, limit: int) -> tuple[int, list[SemanticChunk]]:
    rows = (
        db.query(RollingSessionSummary)
        .filter(RollingSessionSummary.status == "active")
        .order_by(RollingSessionSummary.id.asc())
        .limit(limit)
        .all()
    )
    chunks: list[SemanticChunk] = []
    for row in rows:
        chunks.extend(chunk for chunk in chunks_from_session_summary(row) if chunk.text or chunk.lexical_text)
    return len(rows), chunks


def _load_group_memory_chunks(db: Session, limit: int) -> tuple[int, list[SemanticChunk]]:
    rows = (
        db.query(GroupMemory)
        .filter(GroupMemory.status == "active")
        .order_by(GroupMemory.id.asc())
        .limit(limit)
        .all()
    )
    chunks = [chunk_from_group_memory(row) for row in rows if row.content]
    return len(rows), chunks


def _load_sticker_chunks(db: Session, limit: int) -> tuple[int, list[SemanticChunk]]:
    rows = (
        db.query(StickerMemory)
        .filter(StickerMemory.status == "active")
        .order_by(StickerMemory.id.asc())
        .limit(limit)
        .all()
    )
    chunks = []
    for row in rows:
        chunk = chunk_from_sticker(row)
        if chunk is not None:
            chunks.append(chunk)
    return len(rows), chunks


def _load_knowledge_chunks(db: Session, limit: int) -> tuple[int, list[SemanticChunk]]:
    rows = (
        db.query(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .filter(KnowledgeDocument.status == "active")
        .filter(KnowledgeChunk.status == "active")
        .order_by(KnowledgeDocument.id.asc(), KnowledgeChunk.order_index.asc(), KnowledgeChunk.id.asc())
        .limit(limit)
        .all()
    )
    chunks = [
        chunk_from_knowledge_chunk(chunk, document=document)
        for chunk, document in rows
        if is_recallable_knowledge_document(document)
        and is_recallable_knowledge_chunk(chunk)
    ]
    return len(rows), chunks


def _load_source_chunks(db: Session, source_type: str, limit: int) -> tuple[int, list[SemanticChunk]]:
    if source_type == "memory_digest":
        return _load_memory_digest_chunks(db, limit)
    if source_type == "session_summary":
        return _load_session_summary_chunks(db, limit)
    if source_type == "group_memory":
        return _load_group_memory_chunks(db, limit)
    if source_type == "sticker":
        return _load_sticker_chunks(db, limit)
    if source_type == "knowledge":
        return _load_knowledge_chunks(db, limit)
    return 0, []


def _active_index_count(db: Session, source_types: list[str]) -> int:
    if not source_types:
        return 0
    return (
        db.query(SemanticIndexItem)
        .filter(SemanticIndexItem.source_type.in_(source_types))
        .filter(SemanticIndexItem.status == "active")
        .count()
    )


def preview_semantic_index_backfill(
    db: Session,
    *,
    source_type: str = "all",
    limit_per_source: int = 500,
) -> dict[str, Any]:
    ensure_semantic_schema(db.bind)
    source_types = source_types_for_backfill(source_type)
    sources: dict[str, dict[str, Any]] = {}
    total_rows = 0
    total_chunks = 0
    for item_source_type in source_types:
        source_rows, chunks = _load_source_chunks(db, item_source_type, int(limit_per_source))
        indexed_items = _active_index_count(db, [item_source_type])
        sources[item_source_type] = {
            "source_rows": source_rows,
            "buildable_chunks": len(chunks),
            "indexed_items": indexed_items,
            "needs_build": indexed_items == 0 and len(chunks) > 0,
        }
        total_rows += source_rows
        total_chunks += len(chunks)
    indexed_items = _active_index_count(db, source_types)
    return {
        "source_type": str(source_type or "all"),
        "source_types": source_types,
        "source_rows": total_rows,
        "buildable_chunks": total_chunks,
        "indexed_items": indexed_items,
        "needs_build": indexed_items == 0 and total_chunks > 0,
        "limit_per_source": int(limit_per_source),
        "sources": sources,
    }


def build_semantic_index_from_existing_data(
    db: Session,
    *,
    source_type: str = "all",
    limit_per_source: int = 500,
    index_version: str = DEFAULT_BACKFILL_INDEX_VERSION,
    commit: bool = True,
) -> dict[str, Any]:
    """兼容旧入口；每类只入队一个有界业务页，绝不执行 orphan 删除。"""

    aggregate: dict[str, Any] = {
        "scanned": 0,
        "current": 0,
        "missing": 0,
        "stale": 0,
        "orphan": 0,
        "enqueued": 0,
        "reasons": [],
    }
    truncated = False
    source_types = source_types_for_backfill(source_type)
    for item_source_type in source_types:
        page = enqueue_semantic_index_backfill(
            db,
            source_type=item_source_type,
            limit=limit_per_source,
            cursor="",
            index_version=index_version,
        )
        for key in (
            "scanned",
            "current",
            "missing",
            "stale",
            "orphan",
            "enqueued",
        ):
            aggregate[key] += int(page[key])
        aggregate["reasons"].extend(page["reasons"])
        next_cursor = str(page.get("next_cursor") or "")
        if int(page.get("orphan") or 0) != 0:
            raise RuntimeError("legacy_backfill_must_not_scan_orphans")
        if next_cursor:
            next_state = _decode_backfill_cursor(next_cursor)
            if next_state.source_type == item_source_type:
                truncated = True
            elif next_state.source_type != "orphan_sweep":
                raise RuntimeError("legacy_backfill_cursor_stage_unexpected")
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        **aggregate,
        "source_type": str(source_type or "all"),
        "source_types": source_types,
        "indexed_chunks": 0,
        "index_version": index_version,
        "next_cursor": "",
        "done": not truncated,
        "truncated": truncated,
    }


def _encode_backfill_cursor(cursor: SemanticBackfillCursor) -> str:
    payload = {
        "adapter_manifest": cursor.adapter_manifest,
        "after_anchor": cursor.after_anchor,
        "high_water": cursor.high_water,
        "high_waters": cursor.high_waters,
        "source_type": cursor.source_type,
        "target_index_version": cursor.target_index_version,
        "version": cursor.version,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).decode("ascii")
    payload_token = encoded.rstrip("=")
    signature = hmac.new(
        _backfill_cursor_signing_key(),
        payload_token.encode("ascii"),
        hashlib.sha256,
    ).digest()
    signature_token = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{payload_token}.{signature_token}"


def _backfill_cursor_signing_key() -> bytes:
    admin_token = os.environ.get("NANOBOT_ADMIN_TOKEN", "")
    if not admin_token:
        return _BACKFILL_CURSOR_EPHEMERAL_KEY
    return hashlib.sha256(
        _BACKFILL_CURSOR_KEY_DOMAIN + admin_token.encode("utf-8")
    ).digest()


def _decode_backfill_cursor(raw: str) -> SemanticBackfillCursor:
    parts = str(raw or "").split(".")
    if len(parts) != 2 or not all(parts):
        raise ValueError("invalid_backfill_cursor_signature")
    payload_token, signature_token = parts
    expected_signature = hmac.new(
        _backfill_cursor_signing_key(),
        payload_token.encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        signature_padding = "=" * (-len(signature_token) % 4)
        supplied_signature = base64.urlsafe_b64decode(
            signature_token + signature_padding
        )
    except Exception as exc:
        raise ValueError("invalid_backfill_cursor_signature") from exc
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError("invalid_backfill_cursor_signature")
    try:
        padding = "=" * (-len(payload_token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(
            payload_token + padding
        ).decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid_backfill_cursor") from exc
    expected_keys = {
        "adapter_manifest",
        "after_anchor",
        "high_water",
        "high_waters",
        "source_type",
        "target_index_version",
        "version",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError("invalid_backfill_cursor_fields")
    try:
        cursor = SemanticBackfillCursor(
            version=int(payload["version"]),
            source_type=str(payload["source_type"]),
            after_anchor=int(payload["after_anchor"]),
            high_water=int(payload["high_water"]),
            target_index_version=str(payload["target_index_version"]),
            adapter_manifest=str(payload["adapter_manifest"]),
            high_waters={
                str(key): int(value)
                for key, value in dict(payload["high_waters"]).items()
            },
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_backfill_cursor_values") from exc
    if (
        cursor.version != BACKFILL_CURSOR_VERSION
        or cursor.after_anchor < 0
        or cursor.high_water < 0
        or cursor.after_anchor > cursor.high_water
        or not cursor.high_waters
        or any(value < 0 for value in cursor.high_waters.values())
    ):
        raise ValueError("invalid_backfill_cursor_values")
    return cursor


def _backfill_stages(source_type: str) -> tuple[str, ...]:
    requested = source_types_for_backfill(source_type)
    stages = tuple(item for item in _BACKFILL_STAGE_ORDER if item in requested)
    if not stages:
        raise ValueError(f"unsupported_backfill_source_type:{source_type}")
    return (*stages, "orphan_sweep")


def _safe_meta(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _revision_for_chunks(chunks: list[SemanticChunk], *, document_ids: list[int]) -> str:
    return source_revision_for_chunks(chunks, document_ids=document_ids)


def _memory_digest_source_expr():
    safe_meta = case(
        (func.json_valid(MemoryDigest.meta_json) == 1, MemoryDigest.meta_json),
        else_="{}",
    )
    json_source_id = cast(func.json_extract(safe_meta, "$.source_id"), String)
    return func.coalesce(
        func.nullif(json_source_id, ""),
        cast(MemoryDigest.id, String),
    )


def _session_summary_source_expr():
    return func.coalesce(
        func.nullif(RollingSessionSummary.session_id, ""),
        cast(RollingSessionSummary.id, String),
    )


def _logical_source_anchors(
    db: Session,
    *,
    model: Any,
    source_expr: Any,
    high_water: int,
    after_anchor: int,
    limit: int,
    filters: tuple[Any, ...] = (),
) -> tuple[list[tuple[str, int]], bool]:
    anchor_expr = func.min(model.id)
    rows = (
        db.query(source_expr.label("source_id"), anchor_expr.label("anchor"))
        .filter(model.id <= int(high_water), *filters)
        .group_by(source_expr)
        .having(anchor_expr > int(after_anchor))
        .order_by(anchor_expr.asc(), source_expr.asc())
        .limit(max(1, int(limit)) + 1)
        .all()
    )
    selected = [
        (str(row[0] or ""), int(row[1] or 0))
        for row in rows[:max(1, int(limit))]
    ]
    return selected, len(rows) > len(selected)


def _snapshot_page(
    snapshots: list[_BackfillSourceSnapshot],
    *,
    anchors: list[tuple[str, int]],
    has_more: bool,
    after_anchor: int,
) -> _BackfillSnapshotPage:
    return _BackfillSnapshotPage(
        snapshots=tuple(sorted(
            snapshots,
            key=lambda item: (item.anchor, item.source_type, item.source_id),
        )),
        last_anchor=(anchors[-1][1] if anchors else int(after_anchor)),
        has_more=bool(has_more),
    )


def _memory_digest_snapshot_page(
    db: Session,
    *,
    high_water: int,
    after_anchor: int,
    limit: int,
) -> _BackfillSnapshotPage:
    source_expr = _memory_digest_source_expr()
    anchors, has_more = _logical_source_anchors(
        db,
        model=MemoryDigest,
        source_expr=source_expr,
        high_water=high_water,
        after_anchor=after_anchor,
        limit=limit,
    )
    selected_ids = [source_id for source_id, _anchor in anchors]
    rows = []
    if selected_ids:
        rows = (
            db.query(MemoryDigest)
            .filter(
                MemoryDigest.id <= int(high_water),
                source_expr.in_(selected_ids),
            )
            .order_by(MemoryDigest.id.asc())
            .all()
        )
    grouped: dict[str, list[MemoryDigest]] = {}
    for row in rows:
        meta = _safe_meta(row.meta_json)
        if is_recallable_memory_digest_meta(meta):
            grouped.setdefault(memory_digest_source_id(row), []).append(row)
    snapshots: list[_BackfillSourceSnapshot] = []
    anchor_by_source = dict(anchors)
    for source_id in selected_ids:
        source_rows = grouped.get(source_id, [])
        chunks = chunks_from_memory_digest(source_rows)
        if not chunks:
            continue
        document_ids = sorted(int(row.id) for row in source_rows)
        snapshots.append(_BackfillSourceSnapshot(
            source_type="memory_digest",
            source_id=source_id,
            anchor=anchor_by_source[source_id],
            source_revision=memory_digest_source_revision(source_rows),
            chunks=tuple(chunks),
            document_ids=tuple(document_ids),
            delete_source_ids=tuple(str(item) for item in document_ids),
        ))
    return _snapshot_page(
        snapshots,
        anchors=anchors,
        has_more=has_more,
        after_anchor=after_anchor,
    )


def _session_summary_snapshot_page(
    db: Session,
    *,
    high_water: int,
    after_anchor: int,
    limit: int,
) -> _BackfillSnapshotPage:
    source_expr = _session_summary_source_expr()
    anchors, has_more = _logical_source_anchors(
        db,
        model=RollingSessionSummary,
        source_expr=source_expr,
        high_water=high_water,
        after_anchor=after_anchor,
        limit=limit,
        filters=(RollingSessionSummary.status == "active",),
    )
    selected_ids = [source_id for source_id, _anchor in anchors]
    rows = []
    if selected_ids:
        rows = (
            db.query(RollingSessionSummary)
            .filter(
                RollingSessionSummary.id <= int(high_water),
                RollingSessionSummary.status == "active",
                source_expr.in_(selected_ids),
            )
            .order_by(RollingSessionSummary.id.asc())
            .all()
        )
    grouped: dict[str, list[RollingSessionSummary]] = {}
    for row in rows:
        grouped.setdefault(str(row.session_id or row.id or ""), []).append(row)
    snapshots: list[_BackfillSourceSnapshot] = []
    anchor_by_source = dict(anchors)
    for source_id in selected_ids:
        source_rows = grouped.get(source_id, [])
        if not source_rows:
            continue
        selected = max(
            source_rows,
            key=lambda row: (
                int(row.covered_until_turn_id or 0),
                session_summary_kind_rank(row.summary_kind),
                int(row.id or 0),
            ),
        )
        chunks = chunks_from_session_summary(selected)
        if not chunks:
            continue
        document_ids = sorted(int(row.id) for row in source_rows)
        snapshots.append(_BackfillSourceSnapshot(
            source_type="session_summary",
            source_id=source_id,
            anchor=anchor_by_source[source_id],
            source_revision=session_summary_source_revision(selected),
            chunks=tuple(chunks),
            document_ids=(int(selected.id),),
            delete_source_ids=tuple(str(item) for item in document_ids),
        ))
    return _snapshot_page(
        snapshots,
        anchors=anchors,
        has_more=has_more,
        after_anchor=after_anchor,
    )


def _single_row_snapshot_page(
    db: Session,
    *,
    model: Any,
    source_type: str,
    high_water: int,
    after_anchor: int,
    limit: int,
    filters: tuple[Any, ...],
    adapter: Any,
) -> _BackfillSnapshotPage:
    rows = (
        db.query(model)
        .filter(
            model.id > int(after_anchor),
            model.id <= int(high_water),
            *filters,
        )
        .order_by(model.id.asc())
        .limit(max(1, int(limit)) + 1)
        .all()
    )
    selected = rows[:max(1, int(limit))]
    snapshots: list[_BackfillSourceSnapshot] = []
    for row in selected:
        chunk = adapter(row)
        if chunk is None:
            continue
        snapshots.append(_BackfillSourceSnapshot(
            source_type=source_type,
            source_id=str(row.id),
            anchor=int(row.id),
            source_revision=_revision_for_chunks([chunk], document_ids=[int(row.id)]),
            chunks=(chunk,),
            document_ids=(int(row.id),),
            delete_source_ids=(),
        ))
    anchors = [(str(row.id), int(row.id)) for row in selected]
    return _snapshot_page(
        snapshots,
        anchors=anchors,
        has_more=len(rows) > len(selected),
        after_anchor=after_anchor,
    )


def _knowledge_snapshot_page(
    db: Session,
    *,
    high_water: int,
    after_anchor: int,
    limit: int,
) -> _BackfillSnapshotPage:
    documents = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.id > int(after_anchor),
            KnowledgeDocument.id <= int(high_water),
            KnowledgeDocument.status == "active",
        )
        .order_by(KnowledgeDocument.id.asc())
        .limit(max(1, int(limit)) + 1)
        .all()
    )
    selected = documents[:max(1, int(limit))]
    document_ids = [int(document.id) for document in selected]
    rows_by_document: dict[int, list[KnowledgeChunk]] = {}
    if document_ids:
        chunk_rows = (
            db.query(KnowledgeChunk)
            .filter(
                KnowledgeChunk.document_id.in_(document_ids),
                KnowledgeChunk.status == "active",
            )
            .order_by(
                KnowledgeChunk.document_id.asc(),
                KnowledgeChunk.order_index.asc(),
                KnowledgeChunk.id.asc(),
            )
            .all()
        )
        for row in chunk_rows:
            rows_by_document.setdefault(int(row.document_id), []).append(row)
    snapshots: list[_BackfillSourceSnapshot] = []
    for document in selected:
        document_id = int(document.id)
        chunks = [
            chunk_from_knowledge_chunk(row, document=document)
            for row in rows_by_document.get(document_id, [])
            if is_recallable_knowledge_document(document)
            and is_recallable_knowledge_chunk(row)
        ]
        if not chunks:
            continue
        snapshots.append(_BackfillSourceSnapshot(
            source_type="knowledge",
            source_id=str(document_id),
            anchor=document_id,
            source_revision=_revision_for_chunks(chunks, document_ids=[document_id]),
            chunks=tuple(chunks),
            document_ids=(document_id,),
            delete_source_ids=(),
        ))
    anchors = [(str(document.id), int(document.id)) for document in selected]
    return _snapshot_page(
        snapshots,
        anchors=anchors,
        has_more=len(documents) > len(selected),
        after_anchor=after_anchor,
    )


def _stage_high_water(db: Session, stage: str) -> int:
    model = {
        "memory_digest": MemoryDigest,
        "session_summary": RollingSessionSummary,
        "group_memory": GroupMemory,
        "sticker": StickerMemory,
        "knowledge": KnowledgeDocument,
        "orphan_sweep": SemanticIndexItem,
    }[stage]
    return int(db.query(func.max(model.id)).scalar() or 0)


def _business_snapshot_page(
    db: Session,
    stage: str,
    *,
    high_water: int,
    after_anchor: int,
    limit: int,
) -> _BackfillSnapshotPage:
    if stage == "memory_digest":
        return _memory_digest_snapshot_page(
            db,
            high_water=high_water,
            after_anchor=after_anchor,
            limit=limit,
        )
    if stage == "session_summary":
        return _session_summary_snapshot_page(
            db,
            high_water=high_water,
            after_anchor=after_anchor,
            limit=limit,
        )
    if stage == "group_memory":
        return _single_row_snapshot_page(
            db,
            model=GroupMemory,
            source_type="group_memory",
            high_water=high_water,
            after_anchor=after_anchor,
            limit=limit,
            filters=(GroupMemory.status == "active",),
            adapter=chunk_from_group_memory,
        )
    if stage == "sticker":
        return _single_row_snapshot_page(
            db,
            model=StickerMemory,
            source_type="sticker",
            high_water=high_water,
            after_anchor=after_anchor,
            limit=limit,
            filters=(StickerMemory.status == "active",),
            adapter=chunk_from_sticker,
        )
    if stage == "knowledge":
        return _knowledge_snapshot_page(
            db,
            high_water=high_water,
            after_anchor=after_anchor,
            limit=limit,
        )
    return _BackfillSnapshotPage((), int(after_anchor), False)


def _business_keys_for_candidates(
    db: Session,
    *,
    candidates: set[tuple[str, str]],
    high_waters: dict[str, int],
) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    by_type: dict[str, set[str]] = {}
    for source_type, source_id in candidates:
        by_type.setdefault(source_type, set()).add(source_id)

    memory_ids = by_type.get("memory_digest", set())
    if memory_ids and int(high_waters.get("memory_digest", 0)) > 0:
        source_expr = _memory_digest_source_expr()
        rows = (
            db.query(MemoryDigest)
            .filter(
                MemoryDigest.id <= int(high_waters["memory_digest"]),
                source_expr.in_(sorted(memory_ids)),
            )
            .order_by(MemoryDigest.id.asc())
            .all()
        )
        grouped: dict[str, list[MemoryDigest]] = {}
        for row in rows:
            meta = _safe_meta(row.meta_json)
            if is_recallable_memory_digest_meta(meta):
                grouped.setdefault(memory_digest_source_id(row), []).append(row)
        keys.update(
            ("memory_digest", source_id)
            for source_id, source_rows in grouped.items()
            if chunks_from_memory_digest(source_rows)
        )

    summary_ids = by_type.get("session_summary", set())
    if summary_ids and int(high_waters.get("session_summary", 0)) > 0:
        source_expr = _session_summary_source_expr()
        rows = (
            db.query(RollingSessionSummary)
            .filter(
                RollingSessionSummary.id <= int(high_waters["session_summary"]),
                RollingSessionSummary.status == "active",
                source_expr.in_(sorted(summary_ids)),
            )
            .order_by(RollingSessionSummary.id.asc())
            .all()
        )
        grouped: dict[str, list[RollingSessionSummary]] = {}
        for row in rows:
            grouped.setdefault(str(row.session_id or row.id or ""), []).append(row)
        for source_id, source_rows in grouped.items():
            selected = max(
                source_rows,
                key=lambda row: (
                    int(row.covered_until_turn_id or 0),
                    session_summary_kind_rank(row.summary_kind),
                    int(row.id or 0),
                ),
            )
            if chunks_from_session_summary(selected):
                keys.add(("session_summary", source_id))

    for source_type, model, status_filter, adapter in (
        ("group_memory", GroupMemory, GroupMemory.status == "active", chunk_from_group_memory),
        ("sticker", StickerMemory, StickerMemory.status == "active", chunk_from_sticker),
    ):
        source_ids = by_type.get(source_type, set())
        numeric_ids = sorted(int(item) for item in source_ids if str(item).isdigit())
        high_water = int(high_waters.get(source_type, 0))
        if not numeric_ids or high_water <= 0:
            continue
        rows = (
            db.query(model)
            .filter(model.id.in_(numeric_ids), model.id <= high_water, status_filter)
            .all()
        )
        keys.update(
            (source_type, str(row.id))
            for row in rows
            if adapter(row) is not None
        )

    knowledge_ids = by_type.get("knowledge", set())
    numeric_knowledge_ids = sorted(
        int(item) for item in knowledge_ids if str(item).isdigit()
    )
    knowledge_high_water = int(high_waters.get("knowledge", 0))
    if numeric_knowledge_ids and knowledge_high_water > 0:
        documents = (
            db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.id.in_(numeric_knowledge_ids),
                KnowledgeDocument.id <= knowledge_high_water,
                KnowledgeDocument.status == "active",
            )
            .all()
        )
        document_ids = [int(document.id) for document in documents]
        if document_ids:
            present_ids = {
                int(row[0])
                for row in (
                    db.query(KnowledgeChunk.document_id)
                    .filter(
                        KnowledgeChunk.document_id.in_(document_ids),
                        KnowledgeChunk.status == "active",
                        KnowledgeChunk.text != "",
                    )
                    .distinct()
                    .all()
                )
            }
            keys.update(("knowledge", str(item)) for item in present_ids)
    return keys


def _orphan_snapshot_page(
    db: Session,
    *,
    high_water: int,
    after_anchor: int,
    limit: int,
    allowed_source_types: set[str],
    business_high_waters: dict[str, int],
) -> _BackfillSnapshotPage:
    anchor_expr = func.min(SemanticIndexItem.id)
    anchor_rows = (
        db.query(
            SemanticIndexItem.source_type,
            SemanticIndexItem.source_id,
            anchor_expr.label("anchor"),
        )
        .filter(
            SemanticIndexItem.id <= high_water,
            SemanticIndexItem.status == "active",
            SemanticIndexItem.source_type.in_(sorted(allowed_source_types)),
        )
        .group_by(SemanticIndexItem.source_type, SemanticIndexItem.source_id)
        .having(anchor_expr > int(after_anchor))
        .order_by(
            anchor_expr.asc(),
            SemanticIndexItem.source_type.asc(),
            SemanticIndexItem.source_id.asc(),
        )
        .limit(max(1, int(limit)) + 1)
        .all()
    )
    selected = anchor_rows[:max(1, int(limit))]
    selected_keys = {
        (str(row[0] or ""), str(row[1] or ""))
        for row in selected
    }
    business_keys = _business_keys_for_candidates(
        db,
        candidates=selected_keys,
        high_waters=business_high_waters,
    )
    rows = []
    if selected_keys:
        rows = (
            db.query(SemanticIndexItem)
            .filter(
                SemanticIndexItem.id <= int(high_water),
                SemanticIndexItem.status == "active",
                tuple_(
                    SemanticIndexItem.source_type,
                    SemanticIndexItem.source_id,
                ).in_(sorted(selected_keys)),
            )
            .order_by(SemanticIndexItem.id.asc())
            .all()
        )
    grouped: dict[tuple[str, str], list[SemanticIndexItem]] = {}
    for row in rows:
        key = (str(row.source_type), str(row.source_id))
        if key in business_keys:
            continue
        grouped.setdefault(key, []).append(row)
    anchor_by_key = {
        (str(row[0] or ""), str(row[1] or "")): int(row[2] or 0)
        for row in selected
    }
    snapshots: list[_BackfillSourceSnapshot] = []
    for (source_type, source_id), source_rows in grouped.items():
        item_ids = sorted(int(row.id) for row in source_rows)
        index_versions = sorted({str(row.index_version or "") for row in source_rows})
        revision = stable_hash({
            "v": 1,
            "orphan_item_ids": item_ids,
            "source_revisions": sorted(str(row.source_revision or "") for row in source_rows),
        })
        snapshots.append(_BackfillSourceSnapshot(
            source_type=source_type,
            source_id=source_id,
            anchor=anchor_by_key[(source_type, source_id)],
            source_revision="orphan_" + revision[:32],
            chunks=(),
            document_ids=tuple(item_ids),
            delete_source_ids=(source_id,),
            index_version=index_versions[0] if index_versions else "",
        ))
    anchors = [
        (f"{row[0]}:{row[1]}", int(row[2] or 0))
        for row in selected
    ]
    return _snapshot_page(
        snapshots,
        anchors=anchors,
        has_more=len(anchor_rows) > len(selected),
        after_anchor=after_anchor,
    )


def _classify_snapshot(
    db: Session,
    snapshot: _BackfillSourceSnapshot,
    *,
    target_index_version: str,
    orphan: bool = False,
) -> _BackfillClassifiedSource:
    rows = (
        db.query(SemanticIndexItem)
        .filter(
            SemanticIndexItem.source_type == snapshot.source_type,
            SemanticIndexItem.source_id == snapshot.source_id,
            SemanticIndexItem.status == "active",
        )
        .order_by(SemanticIndexItem.id.asc())
        .all()
    )
    if orphan:
        return _BackfillClassifiedSource(
            snapshot=snapshot,
            category="orphan",
            reasons=("business_source_not_recallable",),
            active_item_ids=tuple(int(row.id) for row in rows),
            active_index_versions=tuple(sorted({str(row.index_version or "") for row in rows})),
        )
    if not rows:
        return _BackfillClassifiedSource(
            snapshot=snapshot,
            category="missing",
            reasons=("active_index_missing",),
            active_item_ids=(),
            active_index_versions=(),
        )

    target_rows = [row for row in rows if str(row.index_version or "") == target_index_version]
    reasons: list[str] = []
    expected = {
        (chunk.source_sub_id, target_index_version): source_hash_for_chunk(chunk)
        for chunk in snapshot.chunks
    }
    actual = {
        (str(row.source_sub_id or ""), str(row.index_version or "")): str(
            row.source_hash or ""
        )
        for row in rows
    }
    expected_keys = set(expected)
    actual_keys = set(actual)
    if any(str(row.index_version or "") != target_index_version for row in rows):
        reasons.append("index_version_mismatch")
    if expected_keys - actual_keys:
        reasons.append("expected_chunks_missing")
    if actual_keys - expected_keys:
        reasons.append("unexpected_chunks_present")
    if any(
        expected[key] != actual[key]
        for key in expected_keys & actual_keys
    ):
        reasons.append("source_hash_mismatch")
    if target_rows and any(
        str(row.source_revision or "") != snapshot.source_revision
        for row in target_rows
    ):
        reasons.append("source_revision_mismatch")
    category = "current" if not reasons else "stale"
    if any(str(row.embedding_status or "") not in {"ok", "disabled"} for row in target_rows):
        reasons.append("embedding_incomplete")
    return _BackfillClassifiedSource(
        snapshot=snapshot,
        category=category,
        reasons=tuple(reasons),
        active_item_ids=tuple(int(row.id) for row in rows),
        active_index_versions=tuple(sorted({str(row.index_version or "") for row in rows})),
    )


def _classified_record(item: _BackfillClassifiedSource) -> dict[str, Any]:
    snapshot = item.snapshot
    return {
        "source_type": snapshot.source_type,
        "source_id": snapshot.source_id,
        "anchor": snapshot.anchor,
        "category": item.category,
        "reasons": list(item.reasons),
        "source_revision": snapshot.source_revision,
        "document_ids": list(snapshot.document_ids),
        "expected_chunk_count": len(snapshot.chunks),
        "active_item_ids": list(item.active_item_ids),
        "active_index_versions": list(item.active_index_versions),
    }


def _observed_business_head(
    db: Session,
    *,
    source_type: str,
    source_id: str,
) -> dict[str, Any]:
    candidates = (
        db.query(SemanticIndexJob)
        .filter(
            SemanticIndexJob.source_type == str(source_type or ""),
            SemanticIndexJob.source_id == str(source_id or ""),
        )
        .order_by(SemanticIndexJob.id.desc())
        .all()
    )
    head = next(
        (item for item in candidates if semantic_job_origin(item) == "business"),
        None,
    )
    return {
        "job_id": int(getattr(head, "id", 0) or 0),
        "source_revision": str(getattr(head, "source_revision", "") or ""),
        "job_type": str(getattr(head, "job_type", "") or ""),
    }


def _scan_backfill_page(
    db: Session,
    *,
    source_type: str,
    limit: int,
    cursor: str,
    index_version: str,
    adapter_manifest: str,
) -> tuple[list[_BackfillClassifiedSource], str, bool]:
    stages = _backfill_stages(source_type)
    normalized_limit = max(1, min(5000, int(limit)))
    if cursor:
        state = _decode_backfill_cursor(cursor)
        if state.target_index_version != str(index_version or ""):
            raise ValueError("cursor_index_version_mismatch")
        if state.adapter_manifest != str(adapter_manifest or ""):
            raise ValueError("cursor_adapter_manifest_mismatch")
        if state.source_type not in stages:
            raise ValueError("cursor_source_type_mismatch")
    else:
        first_stage = stages[0]
        high_waters = {
            stage: _stage_high_water(db, stage)
            for stage in stages
        }
        state = SemanticBackfillCursor(
            version=BACKFILL_CURSOR_VERSION,
            source_type=first_stage,
            after_anchor=0,
            high_water=high_waters[first_stage],
            target_index_version=str(index_version or ""),
            adapter_manifest=str(adapter_manifest or ""),
            high_waters=high_waters,
        )
    if (
        set(state.high_waters) != set(stages)
        or state.high_water != state.high_waters.get(state.source_type)
    ):
        raise ValueError("cursor_source_type_mismatch")

    if state.source_type == "orphan_sweep":
        snapshot_page = _orphan_snapshot_page(
            db,
            high_water=state.high_water,
            after_anchor=state.after_anchor,
            limit=normalized_limit,
            allowed_source_types=set(source_types_for_backfill(source_type)),
            business_high_waters=state.high_waters,
        )
    else:
        snapshot_page = _business_snapshot_page(
            db,
            state.source_type,
            high_water=state.high_water,
            after_anchor=state.after_anchor,
            limit=normalized_limit,
        )
    classified = [
        _classify_snapshot(
            db,
            snapshot,
            target_index_version=(
                snapshot.index_version
                if state.source_type == "orphan_sweep"
                else str(index_version or "")
            ),
            orphan=state.source_type == "orphan_sweep",
        )
        for snapshot in snapshot_page.snapshots
    ]
    if snapshot_page.has_more:
        next_state = SemanticBackfillCursor(
            version=state.version,
            source_type=state.source_type,
            after_anchor=snapshot_page.last_anchor,
            high_water=state.high_water,
            target_index_version=state.target_index_version,
            adapter_manifest=state.adapter_manifest,
            high_waters=state.high_waters,
        )
        return classified, _encode_backfill_cursor(next_state), False

    stage_index = stages.index(state.source_type)
    if stage_index + 1 >= len(stages):
        return classified, "", True
    next_stage = stages[stage_index + 1]
    next_state = SemanticBackfillCursor(
        version=state.version,
        source_type=next_stage,
        after_anchor=0,
        high_water=state.high_waters[next_stage],
        target_index_version=state.target_index_version,
        adapter_manifest=state.adapter_manifest,
        high_waters=state.high_waters,
    )
    return classified, _encode_backfill_cursor(next_state), False


def _backfill_page_result(
    classified: list[_BackfillClassifiedSource],
    *,
    next_cursor: str,
    done: bool,
    enqueued: int,
) -> dict[str, Any]:
    counts = {
        category: sum(item.category == category for item in classified)
        for category in ("current", "missing", "stale", "orphan")
    }
    return {
        "scanned": len(classified),
        **counts,
        "enqueued": int(enqueued),
        "next_cursor": next_cursor,
        "done": bool(done),
        "reasons": [_classified_record(item) for item in classified],
    }


def preview_semantic_index_backfill_page(
    db: Session,
    *,
    source_type: str = "all",
    limit: int = 100,
    cursor: str = "",
    index_version: str = DEFAULT_BACKFILL_INDEX_VERSION,
    adapter_manifest: str = SEMANTIC_ADAPTER_MANIFEST,
) -> dict[str, Any]:
    """只读分页诊断；不得确保 schema、flush 或 commit。"""

    classified, next_cursor, done = _scan_backfill_page(
        db,
        source_type=source_type,
        limit=limit,
        cursor=cursor,
        index_version=index_version,
        adapter_manifest=adapter_manifest,
    )
    return _backfill_page_result(
        classified,
        next_cursor=next_cursor,
        done=done,
        enqueued=0,
    )


def enqueue_semantic_index_backfill(
    db: Session,
    *,
    source_type: str = "all",
    limit: int = 100,
    cursor: str = "",
    index_version: str = DEFAULT_BACKFILL_INDEX_VERSION,
    adapter_manifest: str = SEMANTIC_ADAPTER_MANIFEST,
) -> dict[str, Any]:
    from core.semantic.jobs import enqueue_index_job

    # 维护入口在开启入队 unit-of-work 前显式准备 schema；事务型 producer
    # 的 enqueue_index_job(commit=False) 不得自行运行 DDL。
    ensure_semantic_schema(db.bind)
    classified, next_cursor, done = _scan_backfill_page(
        db,
        source_type=source_type,
        limit=limit,
        cursor=cursor,
        index_version=index_version,
        adapter_manifest=adapter_manifest,
    )
    enqueued = 0
    for item in classified:
        if item.category == "current":
            continue
        snapshot = item.snapshot
        job_type = "delete" if item.category == "orphan" else "replace"
        target_version = snapshot.index_version if item.category == "orphan" else str(index_version or "")
        existing = (
            db.query(SemanticIndexJob.id)
            .filter(
                SemanticIndexJob.source_type == snapshot.source_type,
                SemanticIndexJob.source_id == snapshot.source_id,
                SemanticIndexJob.index_version == target_version,
                SemanticIndexJob.source_revision == snapshot.source_revision,
                SemanticIndexJob.job_type == job_type,
                SemanticIndexJob.status.in_(("pending", "running")),
            )
            .first()
        )
        if existing is not None:
            continue
        observed_business_head = _observed_business_head(
            db,
            source_type=snapshot.source_type,
            source_id=snapshot.source_id,
        )
        enqueue_index_job(
            db,
            source_type=snapshot.source_type,
            source_id=snapshot.source_id,
            job_type=job_type,
            index_version=target_version,
            source_revision=snapshot.source_revision,
            meta={
                "contract_version": 2,
                "job_origin": "backfill",
                "backfill_category": item.category,
                "backfill_reasons": list(item.reasons),
                "observed_business_head": observed_business_head,
                "document_ids": list(snapshot.document_ids),
                "document_id": (
                    snapshot.document_ids[0]
                    if snapshot.source_type == "session_summary" and snapshot.document_ids
                    else None
                ),
                "delete_source_ids": list(snapshot.delete_source_ids),
            },
            commit=False,
        )
        enqueued += 1
    return _backfill_page_result(
        classified,
        next_cursor=next_cursor,
        done=done,
        enqueued=enqueued,
    )
