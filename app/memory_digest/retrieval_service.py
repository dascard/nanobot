"""MemoryDigest v2 检索服务。"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core.database import MemoryDigest

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FILTER_FETCH_FACTOR = 10
_MAX_FILTER_SCAN = 2000


def safe_digest_meta(meta_json: str | None) -> dict[str, Any]:
    try:
        data = json.loads(meta_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def digest_status(meta: dict[str, Any]) -> str:
    explicit = str(meta.get("status") or "").strip()
    if explicit in {"archived", "failed", "skipped"}:
        return explicit
    if int(meta.get("schema_version") or 0) != 2:
        return "legacy"
    return explicit or "active"


def validate_digest_date(value: str, field_name: str) -> str:
    date_value = str(value or "").strip()
    if date_value and not _DATE_RE.fullmatch(date_value):
        raise ValueError(f"{field_name} must be YYYY-MM-DD")
    return date_value


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
        digest_date = validate_digest_date(digest_date, "digest_date")
        date_start = validate_digest_date(date_start, "date_start")
        date_end = validate_digest_date(date_end, "date_end")
        target_limit = max(1, min(int(limit), 500))
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

        ordered = query.order_by(MemoryDigest.id.desc())
        if include_legacy:
            rows = ordered.limit(target_limit).all()
            return [self.serialize(row, include_content=include_content) for row in rows]

        results: list[dict[str, Any]] = []
        offset = 0
        batch_size = min(max(target_limit * _FILTER_FETCH_FACTOR, 50), 500)
        while len(results) < target_limit and offset < _MAX_FILTER_SCAN:
            rows = ordered.offset(offset).limit(batch_size).all()
            if not rows:
                break
            for row in rows:
                if not _is_legacy(safe_digest_meta(row.meta_json)):
                    results.append(self.serialize(row, include_content=include_content))
                    if len(results) >= target_limit:
                        break
            offset += len(rows)
        return results

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
        digest_date = validate_digest_date(digest_date, "digest_date")
        date_start = validate_digest_date(date_start, "date_start")
        date_end = validate_digest_date(date_end, "date_end")
        target_limit = max(1, min(int(limit), 200))
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

        ordered = (
            base.filter(or_(MemoryDigest.content.like(f"%{key}%"), MemoryDigest.meta_json.like(f"%{key}%")))
            .order_by(MemoryDigest.id.desc())
        )
        results: list[dict[str, Any]] = []
        offset = 0
        batch_size = min(max(target_limit * _FILTER_FETCH_FACTOR, 50), 500)
        while len(results) < target_limit and offset < _MAX_FILTER_SCAN:
            rows = ordered.offset(offset).limit(batch_size).all()
            if not rows:
                break
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
                if len(results) >= target_limit:
                    break
            offset += len(rows)
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

    def expand_by_source(
        self,
        *,
        source_id: str,
        include_detail: bool = False,
        include_legacy: bool = False,
    ) -> dict[str, Any] | None:
        """按 digest_source_id 展开：聚合所有同 source 的 level0/1/2 行。

        与 expand_digest（按 row id 查找单条）互补，用于 RAG 命中后回溯同 source 的
        摘要层级和全部卡片。
        """
        if not source_id or not str(source_id).strip():
            return None
        sid = str(source_id).strip()
        # json_extract 语义正确；LIKE 兜底兼容无 JSON1 的 SQLite 或紧凑 JSON
        rows = (
            self.db.query(MemoryDigest)
            .filter(
                or_(
                    func.json_extract(MemoryDigest.meta_json, "$.source_id") == sid,
                    MemoryDigest.meta_json.like(f'%"source_id":"{sid}"%'),
                    MemoryDigest.meta_json.like(f'%"source_id": "{sid}"%'),
                )
            )
            .order_by(MemoryDigest.level.asc(), MemoryDigest.id.asc())
            .all()
        )
        if not rows:
            return None
        # 过滤 legacy / 非 active 状态
        filtered: list[Any] = []
        for row in rows:
            meta = safe_digest_meta(row.meta_json)
            if not include_legacy and _is_legacy(meta):
                continue
            if digest_status(meta) not in {"active", "legacy"}:
                continue
            filtered.append(row)
        if not filtered:
            return None

        level0 = next((r for r in filtered if r.level == 0), None)
        level1 = next((r for r in filtered if r.level == 1), None)
        level2_rows = [r for r in filtered if r.level == 2]

        best_meta = safe_digest_meta(level0.meta_json) if level0 else (
            safe_digest_meta(level1.meta_json) if level1 else safe_digest_meta(filtered[0].meta_json)
        )
        all_cards: list[dict[str, Any]] = []
        for r in level2_rows:
            m = safe_digest_meta(r.meta_json)
            cards = m.get("recall_cards") if isinstance(m.get("recall_cards"), list) else []
            all_cards.extend(cards)

        chain: list[dict[str, Any]] = []
        if level0:
            chain.append(self.serialize(level0, include_content=include_detail))
        if level1:
            chain.append(self.serialize(level1, include_content=include_detail))

        return {
            "digest_source_id": sid,
            "digest_id": level2_rows[0].id if level2_rows else (filtered[0].id if filtered else None),
            "matched_digest_row_ids": [r.id for r in level2_rows],
            "user_id": filtered[0].user_id if filtered else "",
            "session_id": filtered[0].session_id if filtered else "",
            "digest_date": filtered[0].digest_date if filtered else "",
            "status": digest_status(best_meta),
            "preview": best_meta.get("preview") or {},
            "long_summary": best_meta.get("long_summary") or {},
            "recall_cards": all_cards,
            "quality": best_meta.get("quality") or {},
            "chain": chain,
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
