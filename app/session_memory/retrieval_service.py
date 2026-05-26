"""RollingSessionSummary 检索服务。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.memory_digest.retrieval_service import validate_digest_date
from core.database import RollingSessionSummary


def _date_bounds(date_start: str = "", date_end: str = "") -> tuple[datetime | None, datetime | None]:
    start = validate_digest_date(date_start, "date_start")
    end = validate_digest_date(date_end, "date_end")
    start_dt = datetime.strptime(start, "%Y-%m-%d") if start else None
    end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1) if end else None
    return start_dt, end_dt


def _safe_json(meta_json: str | None) -> dict[str, Any]:
    try:
        value = json.loads(meta_json or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


class SessionSummaryRetrievalService:
    def __init__(self, db: Session):
        self.db = db

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
    ) -> list[dict[str, Any]]:
        start_dt, end_dt = _date_bounds(date_start, date_end)
        query = self.db.query(RollingSessionSummary)
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
    ) -> list[dict[str, Any]]:
        key = str(keyword or "").strip()
        if not key:
            return []
        start_dt, end_dt = _date_bounds(date_start, date_end)
        query = self.db.query(RollingSessionSummary)
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
    ) -> dict[str, Any] | None:
        row = (
            self.db.query(RollingSessionSummary)
            .filter(RollingSessionSummary.id == int(summary_id))
            .first()
        )
        if row is None:
            return None
        if not include_archived and row.status != "active":
            return None
        return self.serialize(row, include_content=True)

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
