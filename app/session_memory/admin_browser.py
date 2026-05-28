"""Admin-only session memory browser.

This module is for audit/browsing UI. It deliberately avoids RAG recall
semantics such as query rewrite, similarity search, and top-k ranking.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.database import MemoryDigest, RollingSessionSummary


def _safe_json(raw: str | None, fallback: Any) -> Any:
    try:
        value = json.loads(raw or "")
        return value if value is not None else fallback
    except Exception:
        return fallback


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _preview(text: str | None, limit: int = 360) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit] + ("..." if len(value) > limit else "")


def _digest_status(meta: dict[str, Any]) -> str:
    explicit = str(meta.get("status") or "").strip()
    if explicit:
        return explicit
    if int(meta.get("schema_version") or 0) != 2:
        return "legacy"
    return "active"


class AdminSessionMemoryBrowser:
    def __init__(self, db: Session):
        self.db = db

    def list_sessions(
        self,
        *,
        session_limit: int = 50,
        cursor: str = "",
    ) -> dict[str, Any]:
        limit = max(1, min(int(session_limit or 50), 200))
        try:
            offset = max(0, int(cursor or 0))
        except ValueError:
            offset = 0

        sessions: dict[str, dict[str, Any]] = {}
        summary_rows = (
            self.db.query(
                RollingSessionSummary.id,
                RollingSessionSummary.session_id,
                RollingSessionSummary.user_id,
                RollingSessionSummary.chat_type,
                RollingSessionSummary.status,
                RollingSessionSummary.summary_text,
                RollingSessionSummary.covered_from_turn_id,
                RollingSessionSummary.covered_until_turn_id,
                RollingSessionSummary.quality_score,
                RollingSessionSummary.llm_status,
                RollingSessionSummary.created_at,
                RollingSessionSummary.updated_at,
            )
            .order_by(RollingSessionSummary.updated_at.desc(), RollingSessionSummary.id.desc())
            .limit(5000)
            .all()
        )
        digest_rows = (
            self.db.query(
                MemoryDigest.id,
                MemoryDigest.session_id,
                MemoryDigest.user_id,
                MemoryDigest.created_at,
            )
            .order_by(MemoryDigest.created_at.desc(), MemoryDigest.id.desc())
            .limit(5000)
            .all()
        )

        def ensure(session_id: str) -> dict[str, Any]:
            item = sessions.get(session_id)
            if item is None:
                item = {
                    "session_id": session_id,
                    "chat_type": "",
                    "user_id": "",
                    "summary_count": 0,
                    "digest_count": 0,
                    "active_summary_id": None,
                    "active_summary_preview": "",
                    "active_summary_created_at": "",
                    "active_summary_updated_at": "",
                    "latest_turn_index": 0,
                    "oldest_turn_index": 0,
                    "has_archived": False,
                    "llm_status": "",
                    "quality_score": 0.0,
                    "_latest_at": None,
                    "_oldest_turn_candidates": [],
                }
                sessions[session_id] = item
            return item

        for row in summary_rows:
            sid = str(row.session_id or "")
            if not sid:
                continue
            item = ensure(sid)
            item["summary_count"] += 1
            item["chat_type"] = item["chat_type"] or str(row.chat_type or "")
            item["user_id"] = item["user_id"] or str(row.user_id or "")
            item["latest_turn_index"] = max(int(item["latest_turn_index"] or 0), int(row.covered_until_turn_id or 0))
            if int(row.covered_from_turn_id or 0) > 0:
                item["_oldest_turn_candidates"].append(int(row.covered_from_turn_id or 0))
            item["has_archived"] = bool(item["has_archived"] or row.status == "archived")
            latest_at = row.updated_at or row.created_at
            if latest_at and (item["_latest_at"] is None or latest_at > item["_latest_at"]):
                item["_latest_at"] = latest_at
            if row.status == "active" and not item["active_summary_id"]:
                item["active_summary_id"] = row.id
                item["active_summary_preview"] = _preview(row.summary_text)
                item["active_summary_created_at"] = _iso(row.created_at)
                item["active_summary_updated_at"] = _iso(row.updated_at)
                item["llm_status"] = str(row.llm_status or "")
                item["quality_score"] = float(row.quality_score or 0.0)

        for row in digest_rows:
            sid = str(row.session_id or "")
            if not sid:
                continue
            item = ensure(sid)
            item["digest_count"] += 1
            item["user_id"] = item["user_id"] or str(row.user_id or "")
            if row.created_at and (item["_latest_at"] is None or row.created_at > item["_latest_at"]):
                item["_latest_at"] = row.created_at

        items = []
        for item in sessions.values():
            candidates = item.pop("_oldest_turn_candidates", [])
            item["oldest_turn_index"] = min(candidates) if candidates else 0
            latest_at = item.pop("_latest_at", None)
            item["latest_at"] = _iso(latest_at)
            items.append(item)

        items.sort(key=lambda item: (item.get("latest_at") or "", item.get("session_id") or ""), reverse=True)
        page_items = items[offset:offset + limit]
        next_offset = offset + limit
        return {
            "items": page_items,
            "total": len(items),
            "session_limit": limit,
            "cursor": str(offset),
            "next_cursor": str(next_offset) if next_offset < len(items) else "",
        }

    def list_summaries(
        self,
        session_id: str,
        *,
        summary_limit_per_session: int = 20,
        include_content: bool = False,
        include_archived: bool = True,
    ) -> dict[str, Any]:
        limit = max(1, min(int(summary_limit_per_session or 20), 100))
        query = self.db.query(RollingSessionSummary).filter(RollingSessionSummary.session_id == session_id)
        if not include_archived:
            query = query.filter(RollingSessionSummary.status == "active")
        rows = query.order_by(RollingSessionSummary.id.desc()).limit(limit).all()
        return {
            "session_id": session_id,
            "items": [self._summary_to_dict(row, include_content=include_content) for row in rows],
            "summary_limit_per_session": limit,
        }

    def list_digests(
        self,
        session_id: str,
        *,
        digest_limit_per_session: int = 50,
        include_content: bool = False,
        date_start: str = "",
        date_end: str = "",
        level: int = -1,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(int(digest_limit_per_session or 50), 200))
        query = self.db.query(MemoryDigest).filter(MemoryDigest.session_id == session_id)
        if date_start:
            query = query.filter(MemoryDigest.digest_date >= date_start)
        if date_end:
            query = query.filter(MemoryDigest.digest_date <= date_end)
        if level >= 0:
            query = query.filter(MemoryDigest.level == level)
        if parent_id is not None:
            query = query.filter(MemoryDigest.parent_id == parent_id)
        rows = query.order_by(MemoryDigest.id.desc()).limit(limit).all()
        return {
            "session_id": session_id,
            "items": [self._digest_to_dict(row, include_content=include_content) for row in rows],
            "digest_limit_per_session": limit,
        }

    @staticmethod
    def _summary_to_dict(row: RollingSessionSummary, *, include_content: bool = False) -> dict[str, Any]:
        item = {
            "summary_id": row.id,
            "summary_kind": row.summary_kind or "deterministic_fallback",
            "preview": _preview(row.summary_text),
            "turn_start": int(row.covered_from_turn_id or 0),
            "turn_end": int(row.covered_until_turn_id or 0),
            "is_active": row.status == "active",
            "is_archived": row.status == "archived",
            "quality_score": float(row.quality_score or 0.0),
            "llm_status": row.llm_status or "",
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "raw_json": {
                "summary_json": _safe_json(row.summary_json, {}),
                "issues_json": _safe_json(row.issues_json, []),
                "meta_json": _safe_json(row.meta_json, {}),
                "source_turn_ids_json": _safe_json(row.source_turn_ids_json, []),
            },
        }
        if include_content:
            item["content"] = row.summary_text or ""
        return item

    @staticmethod
    def _digest_to_dict(row: MemoryDigest, *, include_content: bool = False) -> dict[str, Any]:
        meta = _safe_json(row.meta_json, {})
        item = {
            "digest_id": row.id,
            "digest_date": row.digest_date or "",
            "level": int(row.level or 0),
            "parent_id": row.parent_id,
            "preview": _preview((meta.get("preview") or {}).get("text") if isinstance(meta.get("preview"), dict) else row.content),
            "source_start_log_id": row.source_start_log_id,
            "source_end_log_id": row.source_end_log_id,
            "status": _digest_status(meta),
            "created_at": _iso(row.created_at),
            "raw_json": {"meta_json": meta},
        }
        if include_content:
            item["content"] = row.content or ""
        return item
