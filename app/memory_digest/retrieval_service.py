"""MemoryDigest v2 检索服务。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core.db.models.session_memory import MemoryDigest
from core.memory_governance import MemoryDataScopeFilter
from core.semantic.adapters import is_recallable_memory_digest_meta

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
    if not date_value:
        return ""
    if not _DATE_RE.fullmatch(date_value):
        raise ValueError(f"{field_name} must be a valid YYYY-MM-DD date")
    try:
        parsed = datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be a valid YYYY-MM-DD date"
        ) from exc
    if parsed.strftime("%Y-%m-%d") != date_value:
        raise ValueError(f"{field_name} must be a valid YYYY-MM-DD date")
    return date_value


def validate_digest_date_range(
    date_start: str,
    date_end: str,
) -> tuple[str, str]:
    start = validate_digest_date(date_start, "date_start")
    end = validate_digest_date(date_end, "date_end")
    if start and end and start > end:
        raise ValueError("date_start must be earlier than or equal to date_end")
    return start, end


def _is_legacy(meta: dict[str, Any]) -> bool:
    return digest_status(meta) == "legacy"


def _is_retrievable(meta: dict[str, Any], *, include_legacy: bool) -> bool:
    if _is_legacy(meta):
        return bool(include_legacy)
    return is_recallable_memory_digest_meta(meta)


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
    def __init__(
        self,
        db: Session,
        *,
        scope_filter: MemoryDataScopeFilter | None = None,
    ):
        self.db = db
        self.scope_filter = scope_filter

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
        scope_filter: MemoryDataScopeFilter | None = None,
    ) -> list[dict[str, Any]]:
        digest_date = validate_digest_date(digest_date, "digest_date")
        date_start, date_end = validate_digest_date_range(date_start, date_end)
        target_limit = max(1, min(int(limit), 500))
        query = self._apply_scope(
            self.db.query(MemoryDigest),
            scope_filter,
        )
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
                if _is_retrievable(
                    safe_digest_meta(row.meta_json),
                    include_legacy=False,
                ):
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
        scope_filter: MemoryDataScopeFilter | None = None,
    ) -> list[dict[str, Any]]:
        key = str(keyword or "").strip()
        if not key:
            return []
        digest_date = validate_digest_date(digest_date, "digest_date")
        date_start, date_end = validate_digest_date_range(date_start, date_end)
        target_limit = max(1, min(int(limit), 200))
        reveal_to_level = max(0, min(2, int(reveal_to_level)))
        base = self._apply_scope(
            self.db.query(MemoryDigest).filter(MemoryDigest.level == 2),
            scope_filter,
        )
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
        # limit 的单位是逻辑摘要，而不是 level-2 数据库行。先完成 source_id 折叠，
        # 才能避免一份含多张卡片的摘要挤占全部召回名额。
        grouped: dict[str, list[tuple[MemoryDigest, dict[str, Any]]]] = {}
        for row in ordered.limit(_MAX_FILTER_SCAN).all():
            meta = safe_digest_meta(row.meta_json)
            if not _is_retrievable(meta, include_legacy=include_legacy):
                continue
            source_id = str(meta.get("source_id") or "").strip()
            logical_id = source_id or f"row:{int(row.id or 0)}"
            grouped.setdefault(logical_id, []).append((row, meta))

        results: list[dict[str, Any]] = []
        for logical_id, matches in list(grouped.items())[:target_limit]:
            row, meta = matches[0]
            logical_meta = dict(meta)
            logical_cards: list[dict[str, Any]] = []
            seen_cards: set[str] = set()
            for _, match_meta in matches:
                cards = match_meta.get("recall_cards")
                if not isinstance(cards, list):
                    continue
                for card in cards:
                    if not isinstance(card, dict):
                        continue
                    identity = str(card.get("card_id") or "").strip() or json.dumps(
                        card,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if identity in seen_cards:
                        continue
                    seen_cards.add(identity)
                    logical_cards.append(card)
            logical_meta["recall_cards"] = logical_cards
            chain = self.expand_chain(
                row,
                reveal_to_level=reveal_to_level,
                scope_filter=scope_filter,
            )
            expanded = [
                self.serialize(
                    node,
                    include_content=include_content,
                    include_meta=False,
                )
                for node in sorted(chain, key=lambda item: item.level, reverse=True)
            ]
            start_ids = [
                int(match.source_start_log_id)
                for match, _ in matches
                if match.source_start_log_id is not None
            ]
            end_ids = [
                int(match.source_end_log_id)
                for match, _ in matches
                if match.source_end_log_id is not None
            ]
            results.append({
                "digest_id": row.id,
                "digest_source_id": str(meta.get("source_id") or "") or logical_id,
                "matched_digest_row_ids": [int(match.id) for match, _ in matches],
                "user_id": row.user_id,
                "session_id": row.session_id,
                "digest_date": row.digest_date,
                "confidence": max(
                    calc_recall_confidence(key, match.content or "", match_meta)
                    for match, match_meta in matches
                ),
                "source_range": {
                    "start_log_id": min(start_ids) if start_ids else None,
                    "end_log_id": max(end_ids) if end_ids else None,
                },
                "meta": logical_meta,
                "revealed_chain": expanded,
            })
        return results

    def expand_digest(
        self,
        *,
        digest_id: int,
        include_detail: bool = False,
        include_legacy: bool = False,
        scope_filter: MemoryDataScopeFilter | None = None,
    ) -> dict[str, Any] | None:
        query = self.db.query(MemoryDigest).filter(
            MemoryDigest.id == int(digest_id)
        )
        row = self._apply_scope(query, scope_filter).first()
        if not row:
            return None
        meta = safe_digest_meta(row.meta_json)
        if not _is_retrievable(meta, include_legacy=include_legacy):
            return None
        chain = self.expand_chain(
            row,
            reveal_to_level=0,
            scope_filter=scope_filter,
        )
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
            "chain": [
                self.serialize(node, include_content=include_detail, include_meta=False)
                for node in chain
            ],
        }

    def expand_by_source(
        self,
        *,
        source_id: str,
        include_detail: bool = False,
        include_legacy: bool = False,
        scope_filter: MemoryDataScopeFilter | None = None,
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
            self._apply_scope(
                self.db.query(MemoryDigest),
                scope_filter,
            )
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
            if not _is_retrievable(meta, include_legacy=include_legacy):
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
        seen_cards: set[str] = set()
        for r in level2_rows:
            m = safe_digest_meta(r.meta_json)
            cards = m.get("recall_cards") if isinstance(m.get("recall_cards"), list) else []
            for card in cards:
                if not isinstance(card, dict):
                    continue
                identity = str(card.get("card_id") or "").strip() or json.dumps(
                    card,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if identity in seen_cards:
                    continue
                seen_cards.add(identity)
                all_cards.append(card)
        if not all_cards and isinstance(best_meta.get("recall_cards"), list):
            all_cards = [card for card in best_meta["recall_cards"] if isinstance(card, dict)]

        chain: list[dict[str, Any]] = []
        if level0:
            chain.append(self.serialize(level0, include_content=include_detail, include_meta=False))
        if level1:
            chain.append(self.serialize(level1, include_content=include_detail, include_meta=False))

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

    def expand_chain(
        self,
        base: MemoryDigest,
        *,
        reveal_to_level: int,
        scope_filter: MemoryDataScopeFilter | None = None,
    ) -> list[MemoryDigest]:
        chain = [base]
        current = base
        while current.parent_id and current.level > reveal_to_level:
            query = self.db.query(MemoryDigest).filter(
                MemoryDigest.id == current.parent_id
            )
            parent = self._apply_scope(query, scope_filter).first()
            if not parent:
                break
            chain.append(parent)
            current = parent
        return chain

    def _apply_scope(self, query, scope_filter: MemoryDataScopeFilter | None):
        scope_filter = scope_filter or self.scope_filter
        if scope_filter is None:
            return query
        if scope_filter.user_ids:
            query = query.filter(
                MemoryDigest.user_id.in_(scope_filter.user_ids)
            )
        if scope_filter.session_ids:
            query = query.filter(
                MemoryDigest.session_id.in_(scope_filter.session_ids)
            )
        return query

    @staticmethod
    def serialize(
        row: MemoryDigest,
        *,
        include_content: bool = False,
        include_meta: bool = True,
    ) -> dict[str, Any]:
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
            "status": digest_status(meta),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        if include_meta:
            item["meta"] = meta
        if include_content:
            item["content"] = row.content
        return item
