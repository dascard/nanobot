"""MemoryDigest v2 检索服务。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.database import MemoryDigest


def safe_digest_meta(meta_json: str | None) -> dict[str, Any]:
    try:
        data = json.loads(meta_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def digest_status(meta: dict[str, Any]) -> str:
    if int(meta.get("schema_version") or 0) != 2:
        return "legacy"
    return str(meta.get("status") or "active").strip() or "active"


def _is_legacy(meta: dict[str, Any]) -> bool:
    return digest_status(meta) == "legacy"


def calc_recall_confidence(keyword: str, content: str, meta: dict[str, Any]) -> float:
    key = str(keyword or "").strip().lower()
    if not key:
        return 0.5
    haystacks = [str(content or "").lower(), json.dumps(meta.get("recall_cards") or [], ensure_ascii=False).lower()]
    hits = sum(text.count(key) for text in haystacks)
    quality = meta.get("quality") if isinstance(meta.get("quality"), dict) else {}
    quality_score = float(quality.get("score") or 0.5)
    return round(min(0.98, 0.25 + min(0.45, hits * 0.1) + quality_score * 0.25), 3)


class MemoryDigestRetrievalService:
    def __init__(self, db: Session):
        self.db = db

    def list_digests(
        self,
        *,
        user_id: str = "",
        session_id: str = "",
        digest_date: str = "",
        date_start: str = "",
        date_end: str = "",
        level: int | None = None,
        limit: int = 50,
        include_content: bool = False,
        include_legacy: bool = True,
    ) -> list[dict[str, Any]]:
        query = self.db.query(MemoryDigest)
        if user_id:
            query = query.filter(MemoryDigest.user_id == user_id)
        if session_id:
            query = query.filter(MemoryDigest.session_id == session_id)
        if digest_date:
            query = query.filter(MemoryDigest.digest_date == digest_date)
        if date_start:
            query = query.filter(MemoryDigest.digest_date >= date_start)
        if date_end:
            query = query.filter(MemoryDigest.digest_date <= date_end)
        if level is not None and level >= 0:
            query = query.filter(MemoryDigest.level == level)

        rows = query.order_by(MemoryDigest.id.desc()).limit(max(1, min(int(limit), 500))).all()
        return [
            self.serialize(row, include_content=include_content)
            for row in rows
            if include_legacy or not _is_legacy(safe_digest_meta(row.meta_json))
        ]

    def recall(
        self,
        *,
        keyword: str,
        user_id: str = "",
        session_id: str = "",
        digest_date: str = "",
        date_start: str = "",
        date_end: str = "",
        limit: int = 20,
        reveal_to_level: int = 2,
        include_content: bool = False,
        include_legacy: bool = False,
    ) -> list[dict[str, Any]]:
        key = str(keyword or "").strip()
        if not key:
            return []
        reveal_to_level = max(0, min(2, int(reveal_to_level)))
        base = self.db.query(MemoryDigest).filter(MemoryDigest.level == 2)
        if user_id:
            base = base.filter(MemoryDigest.user_id == user_id)
        if session_id:
            base = base.filter(MemoryDigest.session_id == session_id)
        if digest_date:
            base = base.filter(MemoryDigest.digest_date == digest_date)
        if date_start:
            base = base.filter(MemoryDigest.digest_date >= date_start)
        if date_end:
            base = base.filter(MemoryDigest.digest_date <= date_end)

        rows = (
            base.filter(or_(MemoryDigest.content.like(f"%{key}%"), MemoryDigest.meta_json.like(f"%{key}%")))
            .order_by(MemoryDigest.id.desc())
            .limit(max(1, min(int(limit) * 3, 300)))
            .all()
        )
        results: list[dict[str, Any]] = []
        for row in rows:
            meta = safe_digest_meta(row.meta_json)
            if not include_legacy and _is_legacy(meta):
                continue
            if digest_status(meta) not in {"active", "legacy"}:
                continue
            chain = self.expand_chain(row, reveal_to_level=reveal_to_level)
            expanded = [
                self.serialize(node, include_content=include_content)
                for node in sorted(chain, key=lambda item: item.level, reverse=True)
            ]
            results.append({
                "digest_id": row.id,
                "user_id": row.user_id,
                "session_id": row.session_id,
                "digest_date": row.digest_date,
                "confidence": calc_recall_confidence(key, row.content or "", meta),
                "source_range": {
                    "start_log_id": row.source_start_log_id,
                    "end_log_id": row.source_end_log_id,
                },
                "meta": meta,
                "revealed_chain": expanded,
            })
            if len(results) >= max(1, min(int(limit), 200)):
                break
        return results

    def expand_digest(
        self,
        *,
        digest_id: int,
        include_detail: bool = False,
        include_legacy: bool = False,
    ) -> dict[str, Any] | None:
        row = self.db.query(MemoryDigest).filter(MemoryDigest.id == int(digest_id)).first()
        if not row:
            return None
        meta = safe_digest_meta(row.meta_json)
        if not include_legacy and _is_legacy(meta):
            return None
        chain = self.expand_chain(row, reveal_to_level=0)
        return {
            "digest_id": row.id,
            "user_id": row.user_id,
            "session_id": row.session_id,
            "digest_date": row.digest_date,
            "status": digest_status(meta),
            "preview": meta.get("preview") or {},
            "long_summary": meta.get("long_summary") or {},
            "recall_cards": meta.get("recall_cards") or [],
            "quality": meta.get("quality") or {},
            "chain": [self.serialize(node, include_content=include_detail) for node in chain],
        }

    def expand_chain(self, base: MemoryDigest, *, reveal_to_level: int) -> list[MemoryDigest]:
        chain = [base]
        current = base
        while current.parent_id and current.level > reveal_to_level:
            parent = self.db.query(MemoryDigest).filter(MemoryDigest.id == current.parent_id).first()
            if not parent:
                break
            chain.append(parent)
            current = parent
        return chain

    @staticmethod
    def serialize(row: MemoryDigest, *, include_content: bool = False) -> dict[str, Any]:
        meta = safe_digest_meta(row.meta_json)
        item = {
            "id": row.id,
            "user_id": row.user_id,
            "session_id": row.session_id,
            "digest_date": row.digest_date,
            "level": row.level,
            "parent_id": row.parent_id,
            "source_start_log_id": row.source_start_log_id,
            "source_end_log_id": row.source_end_log_id,
            "meta": meta,
            "status": digest_status(meta),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        if include_content:
            item["content"] = row.content
        return item
