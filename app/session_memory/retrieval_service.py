"""RollingSessionSummary 检索服务。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.memory_digest.retrieval_service import validate_digest_date_range
from core.db.models.session_memory import RollingSessionSummary
from core.memory_governance import MemoryDataScopeFilter


def _date_bounds(date_start: str = "", date_end: str = "") -> tuple[datetime | None, datetime | None]:
    start, end = validate_digest_date_range(date_start, date_end)
    start_dt = datetime.fromisoformat(start) if start else None
    try:
        end_dt = datetime.fromisoformat(end) + timedelta(days=1) if end else None
    except OverflowError as exc:
        raise ValueError("date_end is outside the supported date range") from exc
    return start_dt, end_dt


def _safe_json(meta_json: str | None) -> dict[str, Any]:
    try:
        value = json.loads(meta_json or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


class SessionSummaryRetrievalService:
    def __init__(
        self,
        db: Session,
        *,
        scope_filter: MemoryDataScopeFilter | None = None,
    ):
        self.db = db
        self.scope_filter = scope_filter

    def list_summaries(
        self,
        *,
        user_id: str = "",
        session_id: str = "",
        date_start: str = "",
        date_end: str = "",
        limit: int = 10,
        include_content: bool = False,
        include_archived: bool = False,
        scope_filter: MemoryDataScopeFilter | None = None,
    ) -> list[dict[str, Any]]:
        start_dt, end_dt = _date_bounds(date_start, date_end)
        query = self._apply_scope(
            self.db.query(RollingSessionSummary),
            scope_filter,
        )
        if user_id:
            query = query.filter(RollingSessionSummary.user_id == user_id)
        if session_id:
            query = query.filter(RollingSessionSummary.session_id == session_id)
        if not include_archived:
            query = query.filter(RollingSessionSummary.status == "active")
        if start_dt is not None:
            query = query.filter(RollingSessionSummary.created_at >= start_dt)
        if end_dt is not None:
            query = query.filter(RollingSessionSummary.created_at < end_dt)
        rows = (
            query.order_by(RollingSessionSummary.id.desc())
            .limit(max(1, min(int(limit), 50)))
            .all()
        )
        return [self.serialize(row, include_content=include_content) for row in rows]

    def search(
        self,
        *,
        keyword: str,
        user_id: str = "",
        session_id: str = "",
        date_start: str = "",
        date_end: str = "",
        limit: int = 10,
        include_content: bool = False,
        include_archived: bool = False,
        scope_filter: MemoryDataScopeFilter | None = None,
    ) -> list[dict[str, Any]]:
        key = str(keyword or "").strip()
        if not key:
            return []
        start_dt, end_dt = _date_bounds(date_start, date_end)
        query = self._apply_scope(
            self.db.query(RollingSessionSummary),
            scope_filter,
        )
        if user_id:
            query = query.filter(RollingSessionSummary.user_id == user_id)
        if session_id:
            query = query.filter(RollingSessionSummary.session_id == session_id)
        if not include_archived:
            query = query.filter(RollingSessionSummary.status == "active")
        if start_dt is not None:
            query = query.filter(RollingSessionSummary.created_at >= start_dt)
        if end_dt is not None:
            query = query.filter(RollingSessionSummary.created_at < end_dt)
        rows = (
            query.filter(or_(
                RollingSessionSummary.summary_text.like(f"%{key}%"),
                RollingSessionSummary.summary_json.like(f"%{key}%"),
            ))
            .order_by(RollingSessionSummary.id.desc())
            .limit(max(1, min(int(limit), 50)))
            .all()
        )
        return [self.serialize(row, include_content=include_content) for row in rows]

    def expand_summary(
        self,
        *,
        summary_id: int,
        include_archived: bool = False,
        scope_filter: MemoryDataScopeFilter | None = None,
    ) -> dict[str, Any] | None:
        query = self.db.query(RollingSessionSummary).filter(
            RollingSessionSummary.id == int(summary_id)
        )
        row = self._apply_scope(query, scope_filter).first()
        if row is None:
            return None
        if not include_archived and row.status != "active":
            return None
        return self.serialize(row, include_content=True)

    def _apply_scope(self, query, scope_filter: MemoryDataScopeFilter | None):
        scope_filter = scope_filter or self.scope_filter
        if scope_filter is None:
            return query
        if scope_filter.user_ids:
            query = query.filter(
                RollingSessionSummary.user_id.in_(scope_filter.user_ids)
            )
        if scope_filter.session_ids:
            query = query.filter(
                RollingSessionSummary.session_id.in_(scope_filter.session_ids)
            )
        return query

    @staticmethod
    def serialize(row: RollingSessionSummary, *, include_content: bool = False) -> dict[str, Any]:
        item = {
            "id": row.id,
            "summary_id": row.id,
            "user_id": row.user_id,
            "session_id": row.session_id,
            "chat_type": row.chat_type,
            "status": row.status,
            "summary_kind": row.summary_kind or "deterministic_fallback",
            "covered_from_turn_id": row.covered_from_turn_id,
            "covered_until_turn_id": row.covered_until_turn_id,
            "source_turn_count": row.source_turn_count,
            "raw_window_start_turn_id": row.raw_window_start_turn_id,
            "quality_score": row.quality_score,
            "meta": _safe_json(row.meta_json),
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        }
        if include_content:
            item["summary_text"] = row.summary_text
            item["summary_json"] = _safe_json(row.summary_json)
        else:
            item["preview"] = (row.summary_text or "")[:360]
        return item
