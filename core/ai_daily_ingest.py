"""ai_daily 摘要入库到 Knowledge Library。"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from core.database import KnowledgeChunk, KnowledgeDocument, SessionLocal
from core.semantic.adapters import chunk_from_knowledge_chunk
from core.semantic.indexer import upsert_semantic_chunks
from core.time_utils import db_now_naive


logger = logging.getLogger("nanobot.ai_daily_ingest")
INDEX_VERSION = "ai_daily:v1:knowledge"


def _hash(value: str) -> str:
    return hashlib.sha256(str(value or "").strip().encode("utf-8")).hexdigest()


def _date_key(value: str) -> str:
    text = str(value or "").strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return match.group(0) if match else text[:10]


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _plain_text(value: Any, *, limit: int = 1200) -> str:
    text = str(value or "")
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _safe_meta(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalized_item(item: dict[str, Any], query: str) -> dict[str, Any]:
    title = _plain_text(item.get("title"), limit=300)
    url = str(item.get("url") or item.get("href") or "").strip()
    source_name = _plain_text(item.get("source_name") or item.get("source") or _domain(url), limit=120)
    published_at = str(item.get("published_at") or item.get("date") or "").strip()
    summary = _plain_text(
        item.get("summary")
        or item.get("description")
        or item.get("snippet")
        or item.get("body")
        or item.get("content_excerpt")
        or title,
        limit=1200,
    )
    published_date = _date_key(published_at)
    url_hash = _hash(url) if url else ""
    title_key = _hash("|".join([title, source_name, published_date])) if title and source_name and published_date else ""
    summary_key = _hash("|".join([summary, source_name, published_date])) if summary and source_name and published_date else ""
    return {
        "title": title or summary[:80],
        "url": url,
        "summary": summary,
        "source_name": source_name,
        "published_at": published_at,
        "published_date": published_date,
        "domain": _domain(url),
        "author": _plain_text(item.get("author"), limit=120),
        "trust_level": str(item.get("trust_level") or "medium"),
        "query": str(query or ""),
        "url_hash": url_hash,
        "title_source_date_hash": title_key,
        "summary_source_date_hash": summary_key,
    }


def _item_to_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    return {
        "title": getattr(item, "title", ""),
        "url": getattr(item, "url", "") or getattr(item, "href", ""),
        "summary": (
            getattr(item, "summary", "")
            or getattr(item, "description", "")
            or getattr(item, "snippet", "")
            or getattr(item, "body", "")
            or getattr(item, "content_excerpt", "")
            or getattr(item, "detail_text", "")
        ),
        "source_name": getattr(item, "source_name", "") or getattr(item, "source", ""),
        "published_at": getattr(item, "published_at", "") or getattr(item, "date", ""),
        "author": getattr(item, "author", ""),
        "trust_level": getattr(item, "trust_level", ""),
    }


def _find_existing_document(db: Session, normalized: dict[str, Any]) -> KnowledgeDocument | None:
    url = normalized.get("url") or ""
    if url:
        row = (
            db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.document_kind == "ai_daily")
            .filter(KnowledgeDocument.status == "active")
            .filter(KnowledgeDocument.url == url)
            .first()
        )
        if row is not None:
            return row

    title_key = normalized.get("title_source_date_hash") or ""
    summary_key = normalized.get("summary_source_date_hash") or ""
    if not title_key and not summary_key:
        return None
    rows = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.document_kind == "ai_daily")
        .filter(KnowledgeDocument.status == "active")
        .all()
    )
    for row in rows:
        meta = _safe_meta(row.meta_json)
        if title_key and meta.get("title_source_date_hash") == title_key:
            return row
        if summary_key and meta.get("summary_source_date_hash") == summary_key:
            return row
    return None


def filter_new_ai_daily_items(
    db: Session,
    items: list[Any],
    *,
    query: str = "",
) -> tuple[list[Any], dict[str, Any]]:
    kept: list[Any] = []
    skipped_seen = 0
    warnings: list[str] = []
    for item in items or []:
        normalized = _normalized_item(_item_to_dict(item), query)
        if not normalized.get("title") and not normalized.get("summary"):
            warnings.append("skip_empty_item")
            continue
        if _find_existing_document(db, normalized) is not None:
            skipped_seen += 1
            continue
        kept.append(item)
    return kept, {
        "input": len(items or []),
        "kept": len(kept),
        "skipped_seen": skipped_seen,
        "warnings": warnings,
    }


def best_effort_filter_new_ai_daily_items(
    items: list[Any],
    *,
    query: str = "",
) -> tuple[list[Any], dict[str, Any]]:
    db = SessionLocal()
    try:
        return filter_new_ai_daily_items(db, items, query=query)
    except Exception as exc:
        logger.warning("[ai_daily_ingest] history dedup failed: %s", exc)
        return list(items or []), {
            "input": len(items or []),
            "kept": len(items or []),
            "skipped_seen": 0,
            "warnings": [str(exc)],
        }
    finally:
        db.close()


def _citation(document: KnowledgeDocument, chunk_id: str, source_name: str) -> dict[str, Any]:
    return {
        "document_id": str(document.id),
        "chunk_id": chunk_id,
        "title": document.title,
        "url": document.url or "",
        "source_name": source_name,
        "published_at": document.published_at or "",
        "trust_level": document.trust_level or "medium",
    }


def _upsert_chunk(db: Session, document: KnowledgeDocument, normalized: dict[str, Any]) -> KnowledgeChunk:
    chunk_id = "ai_daily:summary"
    citation = _citation(document, chunk_id, normalized.get("source_name") or "")
    row = (
        db.query(KnowledgeChunk)
        .filter(KnowledgeChunk.document_id == int(document.id))
        .filter(KnowledgeChunk.chunk_id == chunk_id)
        .first()
    )
    if row is None:
        row = KnowledgeChunk(document_id=int(document.id), chunk_id=chunk_id, order_index=0)
        db.add(row)
    row.title = document.title
    row.text = normalized.get("summary") or document.summary or document.title
    row.citation_json = json.dumps(citation, ensure_ascii=False, sort_keys=True)
    row.status = "active"
    row.trust_level = document.trust_level or "medium"
    row.meta_json = json.dumps({"document_kind": "ai_daily"}, ensure_ascii=False, sort_keys=True)
    row.updated_at = db_now_naive()
    db.flush()
    return row


def _apply_document_payload(
    document: KnowledgeDocument,
    normalized: dict[str, Any],
    *,
    created: bool,
) -> None:
    now = db_now_naive()
    document.document_kind = "ai_daily"
    document.title = normalized.get("title") or document.title
    document.url = normalized.get("url") or document.url
    document.domain = normalized.get("domain") or document.domain
    document.author = normalized.get("author") or document.author
    document.published_at = normalized.get("published_at") or document.published_at
    document.summary = normalized.get("summary") or document.summary
    document.status = "active"
    document.trust_level = normalized.get("trust_level") or document.trust_level or "medium"
    document.latest_seen = now
    document.updated_at = now
    if created:
        document.created_at = now
    meta = _safe_meta(document.meta_json)
    meta.update({
        "source_name": normalized.get("source_name") or "",
        "query": normalized.get("query") or "",
        "url_hash": normalized.get("url_hash") or "",
        "title_source_date_hash": normalized.get("title_source_date_hash") or "",
        "summary_source_date_hash": normalized.get("summary_source_date_hash") or "",
        "published_date": normalized.get("published_date") or "",
    })
    document.meta_json = json.dumps(meta, ensure_ascii=False, sort_keys=True)


def ingest_ai_daily_items(
    db: Session,
    items: list[dict[str, Any]],
    *,
    query: str = "",
    index_version: str = INDEX_VERSION,
) -> dict[str, Any]:
    created = 0
    updated = 0
    warnings: list[str] = []
    indexed_chunks: list[KnowledgeChunk] = []
    for raw_item in items or []:
        normalized = _normalized_item(raw_item, query)
        if not normalized.get("title") and not normalized.get("summary"):
            warnings.append("skip_empty_item")
            continue
        document = _find_existing_document(db, normalized)
        is_new = document is None
        if document is None:
            document = KnowledgeDocument(
                document_kind="ai_daily",
                created_by="ai_daily",
                updated_by="ai_daily",
            )
            db.add(document)
            db.flush()
        _apply_document_payload(document, normalized, created=is_new)
        chunk = _upsert_chunk(db, document, normalized)
        indexed_chunks.append(chunk)
        if is_new:
            created += 1
        else:
            updated += 1
    db.commit()

    semantic_chunks = []
    for chunk in indexed_chunks:
        document = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == chunk.document_id).first()
        if document is not None:
            semantic_chunks.append(chunk_from_knowledge_chunk(chunk, document=document))
    if semantic_chunks:
        upsert_semantic_chunks(db, semantic_chunks, index_version=index_version)
    return {"created": created, "updated": updated, "warnings": warnings}


def _items_from_html(html_text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pattern = re.compile(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)</a>", re.IGNORECASE)
    for match in pattern.finditer(str(html_text or "")):
        url = html.unescape(match.group(1).strip())
        title = _plain_text(match.group(2), limit=300)
        if not url or not title:
            continue
        items.append({
            "title": title,
            "url": url,
            "summary": title,
            "source_name": _domain(url),
        })
    return items[:50]


def ingest_ai_daily_html(db: Session, html_text: str, *, query: str = "") -> dict[str, Any]:
    items = _items_from_html(html_text)
    if not items:
        return {"created": 0, "updated": 0, "warnings": ["no_items_extracted"]}
    return ingest_ai_daily_items(db, items, query=query)


def best_effort_ingest_ai_daily_result(html_text: str, *, query: str = "") -> dict[str, Any]:
    db = SessionLocal()
    try:
        return ingest_ai_daily_html(db, html_text, query=query)
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("[ai_daily_ingest] best-effort ingest failed: %s", exc)
        return {"created": 0, "updated": 0, "warnings": [str(exc)]}
    finally:
        db.close()
