"""Admin-only session memory browser.

This module is for audit/browsing UI. It deliberately avoids RAG recall
semantics such as query rewrite, similarity search, and top-k ranking.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.memory_digest.retrieval_service import validate_digest_date_range
from app.memory_digest.renderer import render_digest_levels
from core.database import MemoryDigest, RollingSessionSummary


def _safe_json(raw: str | None, fallback: Any) -> Any:
    try:
        value = json.loads(raw or "")
        return value if value is not None else fallback
    except Exception:
        return fallback


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _iso_any(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


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


def _canonical_session_id(session_id: str | None, user_id: str | None = "") -> str:
    sid = str(session_id or "").strip()
    uid = str(user_id or "").strip()
    if not sid:
        return ""
    if sid.startswith("group_"):
        return sid
    if re.fullmatch(r"\d+", sid) and uid.startswith("group_"):
        return uid
    return sid


def _session_aliases(session_id: str | None) -> list[str]:
    sid = str(session_id or "").strip()
    if not sid:
        return []
    aliases = {sid}
    if sid.startswith("group_"):
        raw = sid.removeprefix("group_")
        if raw:
            aliases.add(raw)
    elif re.fullmatch(r"\d+", sid):
        aliases.add(f"group_{sid}")
    return sorted(aliases)


def _infer_chat_type(session_id: str, explicit: str = "") -> str:
    value = str(explicit or "").strip()
    if value:
        return value
    if str(session_id or "").startswith("group_"):
        return "group"
    if str(session_id or "").startswith("private_"):
        return "private"
    return ""


def _is_system_session(item: dict[str, Any]) -> bool:
    sid = str(item.get("session_id") or "").strip().lower()
    user_id = str(item.get("user_id") or "").strip().lower()
    aliases = [str(x or "").strip().lower() for x in item.get("session_aliases", [])]
    tokens = [sid, user_id, *aliases]
    exact = {"g_test", "news_search", "test", "smoke", "local_test", "news_tool", "test_user_001"}
    if any(token in exact for token in tokens):
        return True
    return any(("test" in token or "smoke" in token or token.endswith("_repl")) for token in tokens)


def _preview_parts(meta: dict[str, Any]) -> tuple[str, list[str]]:
    preview = meta.get("preview") if isinstance(meta.get("preview"), dict) else {}
    text = str(preview.get("text") or preview.get("brief") or "").strip()
    keywords = [str(x).strip() for x in preview.get("keywords", []) if str(x).strip()] if isinstance(preview.get("keywords"), list) else []
    return text, keywords


def _digest_preview_text(row: MemoryDigest, meta: dict[str, Any]) -> str:
    text, keywords = _preview_parts(meta)
    parts = []
    if text:
        parts.append(text)
    if keywords:
        parts.append("关键词：" + "、".join(keywords[:12]))
    if parts:
        return "\n".join(parts)

    content = str(row.content or "").strip()
    if content:
        return content

    long_summary = meta.get("long_summary") if isinstance(meta.get("long_summary"), dict) else {}
    topic_flow = str(long_summary.get("topic_flow") or "").strip()
    if topic_flow:
        return topic_flow
    cards = meta.get("recall_cards") if isinstance(meta.get("recall_cards"), list) else []
    for card in cards:
        if isinstance(card, dict) and str(card.get("text") or "").strip():
            return str(card.get("text") or "").strip()
    return ""


def _digest_content_text(row: MemoryDigest, meta: dict[str, Any]) -> str:
    content = str(row.content or "").strip()
    if content:
        return content
    if int(meta.get("schema_version") or 0) == 2:
        rendered = render_digest_levels(meta)
        return str(rendered.get(int(row.level or 0)) or "").strip()
    return ""


class AdminSessionMemoryBrowser:
    def __init__(self, db: Session):
        self.db = db

    def _session_rows_sql(
        self,
        *,
        limit: int,
        offset: int,
        kind: str,
        include_system_sessions: bool,
    ) -> tuple[int, list[dict[str, Any]]]:
        sql = text("""
WITH all_rows AS (
    SELECT
        CASE
            WHEN session_id NOT LIKE 'group_%'
             AND session_id GLOB '[0-9]*'
             AND user_id LIKE 'group_%'
            THEN user_id
            ELSE session_id
        END AS canonical_session_id,
        session_id AS alias_session_id,
        user_id AS user_id,
        chat_type AS chat_type,
        1 AS summary_count,
        0 AS digest_count,
        0 AS turn_count,
        CASE WHEN status = 'archived' THEN 1 ELSE 0 END AS has_archived,
        covered_until_turn_id AS latest_turn_index,
        CASE WHEN covered_from_turn_id > 0 THEN covered_from_turn_id ELSE NULL END AS oldest_turn_index,
        COALESCE(updated_at, created_at) AS latest_at
    FROM rolling_session_summaries
    WHERE session_id IS NOT NULL AND session_id != ''
    UNION ALL
    SELECT
        CASE
            WHEN session_id NOT LIKE 'group_%'
             AND session_id GLOB '[0-9]*'
             AND user_id LIKE 'group_%'
            THEN user_id
            ELSE session_id
        END AS canonical_session_id,
        session_id AS alias_session_id,
        user_id AS user_id,
        '' AS chat_type,
        0 AS summary_count,
        1 AS digest_count,
        0 AS turn_count,
        0 AS has_archived,
        0 AS latest_turn_index,
        NULL AS oldest_turn_index,
        created_at AS latest_at
    FROM memory_digests
    WHERE session_id IS NOT NULL AND session_id != ''
    UNION ALL
    SELECT
        CASE
            WHEN session_id NOT LIKE 'group_%'
             AND session_id GLOB '[0-9]*'
             AND user_id LIKE 'group_%'
            THEN user_id
            ELSE session_id
        END AS canonical_session_id,
        session_id AS alias_session_id,
        max(user_id) AS user_id,
        CASE
            WHEN session_id LIKE 'group_%' THEN 'group'
            WHEN session_id LIKE 'private_%' THEN 'private'
            WHEN max(user_id) LIKE 'group_%' THEN 'group'
            ELSE ''
        END AS chat_type,
        0 AS summary_count,
        0 AS digest_count,
        count(*) AS turn_count,
        0 AS has_archived,
        max(id) AS latest_turn_index,
        min(id) AS oldest_turn_index,
        max(created_at) AS latest_at
    FROM conversation_turns
    WHERE session_id IS NOT NULL AND session_id != ''
    GROUP BY canonical_session_id, alias_session_id
),
grouped AS (
    SELECT
        canonical_session_id AS session_id,
        group_concat(DISTINCT alias_session_id) AS aliases,
        max(user_id) AS user_id,
        max(chat_type) AS chat_type,
        sum(summary_count) AS summary_count,
        sum(digest_count) AS digest_count,
        sum(turn_count) AS turn_count,
        max(has_archived) AS has_archived,
        max(latest_turn_index) AS latest_turn_index,
        min(oldest_turn_index) AS oldest_turn_index,
        max(latest_at) AS latest_at
    FROM all_rows
    WHERE canonical_session_id IS NOT NULL AND canonical_session_id != ''
    GROUP BY canonical_session_id
),
filtered AS (
    SELECT *
    FROM grouped
    WHERE
        (:kind = 'all'
         OR (:kind = 'recent' AND (summary_count > 0 OR turn_count > 0))
         OR (:kind = 'long' AND digest_count > 0))
        AND (
            :include_system = 1
            OR (
                lower(session_id) NOT IN ('g_test', 'news_search', 'test', 'smoke', 'local_test', 'news_tool', 'test_user_001')
                AND lower(coalesce(user_id, '')) NOT IN ('g_test', 'news_search', 'test', 'smoke', 'local_test', 'news_tool', 'test_user_001')
                AND lower(session_id) NOT LIKE '%test%'
                AND lower(session_id) NOT LIKE '%smoke%'
                AND lower(coalesce(user_id, '')) NOT LIKE '%test%'
                AND lower(coalesce(user_id, '')) NOT LIKE '%smoke%'
                AND lower(session_id) NOT LIKE '%_repl'
                AND lower(coalesce(user_id, '')) NOT LIKE '%_repl'
            )
        )
)
SELECT *, count(*) OVER () AS total_count
FROM filtered
ORDER BY latest_at DESC, session_id DESC
LIMIT :limit OFFSET :offset
""")
        rows = [
            dict(row)
            for row in self.db.execute(
                sql,
                {
                    "kind": kind,
                    "include_system": 1 if include_system_sessions else 0,
                    "limit": limit,
                    "offset": offset,
                },
            ).mappings().all()
        ]
        total = int(rows[0].get("total_count") or 0) if rows else 0
        if not rows and offset > 0:
            count_sql = text("""
WITH all_rows AS (
    SELECT
        CASE
            WHEN session_id NOT LIKE 'group_%'
             AND session_id GLOB '[0-9]*'
             AND user_id LIKE 'group_%'
            THEN user_id
            ELSE session_id
        END AS canonical_session_id,
        session_id AS alias_session_id,
        user_id AS user_id,
        chat_type AS chat_type,
        1 AS summary_count,
        0 AS digest_count,
        0 AS turn_count,
        CASE WHEN status = 'archived' THEN 1 ELSE 0 END AS has_archived,
        covered_until_turn_id AS latest_turn_index,
        CASE WHEN covered_from_turn_id > 0 THEN covered_from_turn_id ELSE NULL END AS oldest_turn_index,
        COALESCE(updated_at, created_at) AS latest_at
    FROM rolling_session_summaries
    WHERE session_id IS NOT NULL AND session_id != ''
    UNION ALL
    SELECT
        CASE
            WHEN session_id NOT LIKE 'group_%'
             AND session_id GLOB '[0-9]*'
             AND user_id LIKE 'group_%'
            THEN user_id
            ELSE session_id
        END AS canonical_session_id,
        session_id AS alias_session_id,
        user_id AS user_id,
        '' AS chat_type,
        0 AS summary_count,
        1 AS digest_count,
        0 AS turn_count,
        0 AS has_archived,
        0 AS latest_turn_index,
        NULL AS oldest_turn_index,
        created_at AS latest_at
    FROM memory_digests
    WHERE session_id IS NOT NULL AND session_id != ''
    UNION ALL
    SELECT
        CASE
            WHEN session_id NOT LIKE 'group_%'
             AND session_id GLOB '[0-9]*'
             AND user_id LIKE 'group_%'
            THEN user_id
            ELSE session_id
        END AS canonical_session_id,
        session_id AS alias_session_id,
        max(user_id) AS user_id,
        CASE
            WHEN session_id LIKE 'group_%' THEN 'group'
            WHEN session_id LIKE 'private_%' THEN 'private'
            WHEN max(user_id) LIKE 'group_%' THEN 'group'
            ELSE ''
        END AS chat_type,
        0 AS summary_count,
        0 AS digest_count,
        count(*) AS turn_count,
        0 AS has_archived,
        max(id) AS latest_turn_index,
        min(id) AS oldest_turn_index,
        max(created_at) AS latest_at
    FROM conversation_turns
    WHERE session_id IS NOT NULL AND session_id != ''
    GROUP BY canonical_session_id, alias_session_id
),
grouped AS (
    SELECT
        canonical_session_id AS session_id,
        max(user_id) AS user_id,
        sum(summary_count) AS summary_count,
        sum(digest_count) AS digest_count,
        sum(turn_count) AS turn_count
    FROM all_rows
    WHERE canonical_session_id IS NOT NULL AND canonical_session_id != ''
    GROUP BY canonical_session_id
)
SELECT count(*) FROM grouped
WHERE
    (:kind = 'all'
     OR (:kind = 'recent' AND (summary_count > 0 OR turn_count > 0))
     OR (:kind = 'long' AND digest_count > 0))
    AND (
        :include_system = 1
        OR (
            lower(session_id) NOT IN ('g_test', 'news_search', 'test', 'smoke', 'local_test', 'news_tool', 'test_user_001')
            AND lower(coalesce(user_id, '')) NOT IN ('g_test', 'news_search', 'test', 'smoke', 'local_test', 'news_tool', 'test_user_001')
            AND lower(session_id) NOT LIKE '%test%'
            AND lower(session_id) NOT LIKE '%smoke%'
            AND lower(coalesce(user_id, '')) NOT LIKE '%test%'
            AND lower(coalesce(user_id, '')) NOT LIKE '%smoke%'
            AND lower(session_id) NOT LIKE '%_repl'
            AND lower(coalesce(user_id, '')) NOT LIKE '%_repl'
        )
    )
""")
            total = int(self.db.execute(
                count_sql,
                {"kind": kind, "include_system": 1 if include_system_sessions else 0},
            ).scalar() or 0)
        return total, rows

    def list_sessions(
        self,
        *,
        session_limit: int = 50,
        cursor: str = "",
        kind: str = "all",
        include_system_sessions: bool = False,
    ) -> dict[str, Any]:
        limit = max(1, min(int(session_limit or 50), 200))
        normalized_kind = str(kind or "all").strip().lower()
        if normalized_kind not in {"all", "recent", "long"}:
            normalized_kind = "all"
        try:
            offset = max(0, int(cursor or 0))
        except ValueError:
            offset = 0

        total, session_rows = self._session_rows_sql(
            limit=limit,
            offset=offset,
            kind=normalized_kind,
            include_system_sessions=include_system_sessions,
        )
        sessions: dict[str, dict[str, Any]] = {}
        alias_to_session: dict[str, str] = {}
        for row in session_rows:
            session_id = str(row.get("session_id") or "")
            aliases = set(_session_aliases(session_id))
            aliases.update(str(item or "").strip() for item in str(row.get("aliases") or "").split(",") if str(item or "").strip())
            item = {
                "session_id": session_id,
                "session_aliases": aliases,
                "chat_type": _infer_chat_type(session_id, str(row.get("chat_type") or "")),
                "user_id": str(row.get("user_id") or ""),
                "summary_count": int(row.get("summary_count") or 0),
                "digest_count": int(row.get("digest_count") or 0),
                "turn_count": int(row.get("turn_count") or 0),
                "active_summary_id": None,
                "active_summary_preview": "",
                "active_summary_created_at": "",
                "active_summary_updated_at": "",
                "latest_turn_index": int(row.get("latest_turn_index") or 0),
                "oldest_turn_index": int(row.get("oldest_turn_index") or 0),
                "has_archived": bool(row.get("has_archived") or 0),
                "llm_status": "",
                "quality_score": 0.0,
                "latest_digest_id": None,
                "latest_digest_preview": "",
                "latest_digest_date": "",
                "latest_digest_created_at": "",
                "latest_at": _iso_any(row.get("latest_at")),
            }
            sessions[session_id] = item
            for alias in aliases:
                alias_to_session[alias] = session_id

        aliases = sorted(alias_to_session)
        if aliases:
            summary_rows = (
                self.db.query(RollingSessionSummary)
                .filter(RollingSessionSummary.session_id.in_(aliases))
                .order_by(RollingSessionSummary.updated_at.desc(), RollingSessionSummary.id.desc())
                .all()
            )
            for row in summary_rows:
                session_id = _canonical_session_id(row.session_id, row.user_id)
                session_id = session_id if session_id in sessions else alias_to_session.get(str(row.session_id or ""), "")
                item = sessions.get(session_id)
                if item is None or row.status != "active":
                    continue
                item["_active_summary_count"] = int(item.get("_active_summary_count") or 0) + 1
                if item["active_summary_id"]:
                    continue
                item["active_summary_id"] = row.id
                item["active_summary_preview"] = _preview(row.summary_text)
                item["active_summary_created_at"] = _iso(row.created_at)
                item["active_summary_updated_at"] = _iso(row.updated_at)
                item["llm_status"] = str(row.llm_status or "")
                item["quality_score"] = float(row.quality_score or 0.0)

            digest_rows = (
                self.db.query(MemoryDigest)
                .filter(MemoryDigest.session_id.in_(aliases))
                .order_by(MemoryDigest.created_at.desc(), MemoryDigest.id.desc())
                .all()
            )
            for row in digest_rows:
                session_id = _canonical_session_id(row.session_id, row.user_id)
                session_id = session_id if session_id in sessions else alias_to_session.get(str(row.session_id or ""), "")
                item = sessions.get(session_id)
                if item is None:
                    continue
                meta = _safe_json(row.meta_json, {})
                if _digest_status(meta) != "active":
                    continue
                logical_id = str(meta.get("source_id") or "").strip() or f"row:{int(row.id or 0)}"
                item.setdefault("_active_digest_source_ids", set()).add(logical_id)
                is_preview_digest = int(row.level or 0) == 1 or str(meta.get("summary_type") or "") == "preview_digest"
                if item["latest_digest_id"] and not (is_preview_digest and not item.get("_latest_digest_is_preview")):
                    continue
                item["latest_digest_id"] = row.id
                item["latest_digest_preview"] = _preview(_digest_preview_text(row, meta))
                item["latest_digest_date"] = str(row.digest_date or "")
                item["latest_digest_created_at"] = _iso(row.created_at)
                item["_latest_digest_is_preview"] = is_preview_digest

        page_items = []
        for item in sessions.values():
            item["session_aliases"] = sorted(item.get("session_aliases") or [])
            item["summary_count"] = int(item.pop("_active_summary_count", 0) or 0)
            item["digest_count"] = len(item.pop("_active_digest_source_ids", set()))
            item.pop("_latest_digest_is_preview", None)
            page_items.append(item)
        return {
            "items": page_items,
            "total": total,
            "session_limit": limit,
            "cursor": str(offset),
            "next_cursor": str(offset + limit) if offset + limit < total else "",
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
        aliases = _session_aliases(session_id)
        query = self.db.query(RollingSessionSummary).filter(RollingSessionSummary.session_id.in_(aliases))
        if not include_archived:
            query = query.filter(RollingSessionSummary.status == "active")
        rows = query.order_by(RollingSessionSummary.id.desc()).limit(limit).all()
        return {
            "session_id": _canonical_session_id(session_id),
            "session_aliases": aliases,
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
        include_archived: bool = False,
    ) -> dict[str, Any]:
        limit = max(1, min(int(digest_limit_per_session or 50), 200))
        aliases = _session_aliases(session_id)
        date_start, date_end = validate_digest_date_range(date_start, date_end)
        query = self.db.query(MemoryDigest).filter(MemoryDigest.session_id.in_(aliases))
        if date_start:
            query = query.filter(MemoryDigest.digest_date >= date_start)
        if date_end:
            query = query.filter(MemoryDigest.digest_date <= date_end)
        if level >= 0:
            query = query.filter(MemoryDigest.level == level)
        if parent_id is not None:
            query = query.filter(MemoryDigest.parent_id == parent_id)
        rows = query.order_by(MemoryDigest.id.desc()).all()
        grouped: dict[str, list[MemoryDigest]] = {}
        for row in rows:
            meta = _safe_json(row.meta_json, {})
            if not include_archived and _digest_status(meta) != "active":
                continue
            source_id = str(meta.get("source_id") or "").strip()
            logical_id = source_id or f"row:{int(row.id or 0)}"
            grouped.setdefault(logical_id, []).append(row)
        logical_rows = sorted(
            grouped.items(),
            key=lambda entry: max(int(row.id or 0) for row in entry[1]),
            reverse=True,
        )[:limit]
        return {
            "session_id": _canonical_session_id(session_id),
            "session_aliases": aliases,
            "items": [
                self._digest_group_to_dict(
                    logical_id,
                    group_rows,
                    include_content=include_content,
                )
                for logical_id, group_rows in logical_rows
            ],
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
        content = _digest_content_text(row, meta)
        item = {
            "digest_id": row.id,
            "digest_date": row.digest_date or "",
            "level": int(row.level or 0),
            "parent_id": row.parent_id,
            "preview": _preview(_digest_preview_text(row, meta) or content),
            "source_start_log_id": row.source_start_log_id,
            "source_end_log_id": row.source_end_log_id,
            "source_id": str(meta.get("source_id") or ""),
            "source_type": str(meta.get("source_type") or ""),
            "source_range": str(meta.get("source_range") or ""),
            "summary_type": str(meta.get("summary_type") or ""),
            "generator": str(meta.get("generator") or ""),
            "quality_score": float((meta.get("quality") if isinstance(meta.get("quality"), dict) else {}).get("score") or 0.0),
            "prompt_template": str(meta.get("prompt_template") or ""),
            "prompt_version": meta.get("prompt_version") if isinstance(meta.get("prompt_version"), dict) else {},
            "fallback_reason": meta.get("fallback_reason"),
            "recall_card_count": int(meta.get("recall_card_count") or 0),
            "message_count": int(meta.get("message_count") or 0),
            "status": _digest_status(meta),
            "created_at": _iso(row.created_at),
            "raw_json": {"meta_json": meta},
        }
        if include_content:
            item["content"] = content
        return item

    @staticmethod
    def _digest_layer_to_dict(
        row: MemoryDigest,
        *,
        include_content: bool = False,
    ) -> dict[str, Any]:
        meta = _safe_json(row.meta_json, {})
        content = _digest_content_text(row, meta)
        item = {
            "digest_id": row.id,
            "level": int(row.level or 0),
            "parent_id": row.parent_id,
            "summary_type": str(meta.get("summary_type") or ""),
            "preview": _preview(content),
            "created_at": _iso(row.created_at),
        }
        card = meta.get("recall_card")
        if isinstance(card, dict):
            item["recall_card"] = card
        if include_content:
            item["content"] = content
        return item

    @classmethod
    def _digest_group_to_dict(
        cls,
        logical_id: str,
        rows: list[MemoryDigest],
        *,
        include_content: bool = False,
    ) -> dict[str, Any]:
        ordered = sorted(rows, key=lambda row: (int(row.level or 0), int(row.id or 0)))
        level0 = next((row for row in ordered if int(row.level or 0) == 0), None)
        level1 = next((row for row in ordered if int(row.level or 0) == 1), None)
        representative = level1 or level0 or ordered[-1]
        item = cls._digest_to_dict(representative, include_content=False)
        meta_row = level0 or level1 or representative
        meta = _safe_json(meta_row.meta_json, {})
        item.update({
            "source_id": str(meta.get("source_id") or "") or logical_id,
            "preview": _preview(_digest_preview_text(representative, meta)),
            "status": _digest_status(meta),
            "layer_count": len(ordered),
            "levels": sorted({int(row.level or 0) for row in ordered}),
            "layers": [
                cls._digest_layer_to_dict(row, include_content=include_content)
                for row in ordered
            ],
            "raw_json": {"meta_json": meta},
        })
        if len(ordered) > 1:
            item["summary_type"] = "logical_digest"
        if include_content:
            content_row = level0 or representative
            item["content"] = _digest_content_text(
                content_row,
                _safe_json(content_row.meta_json, {}),
            )
        return item
