"""Admin-only session memory browser.

This module is for audit/browsing UI. It deliberately avoids RAG recall
semantics such as query rewrite, similarity search, and top-k ranking.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.memory_digest.retrieval_service import validate_digest_date_range
from app.memory_digest.renderer import render_digest_levels
from core.chat_stream_identity import (
    parse_compatibility_chat_stream_identity,
)
from core.db.models.chat import ConversationTurn
from core.db.models.session_memory import (
    MemoryDigest,
    RollingSessionSummary,
)


def _summary_source_ids(row: RollingSessionSummary) -> list[int]:
    """返回摘要实际覆盖的来源 ID；群聊来源是 ChatLog 而非 turn。"""

    source_type = str(getattr(row, "source_type", "conversation_turn") or "conversation_turn")
    raw = (
        getattr(row, "source_ids_json", "[]")
        if source_type == "chat_log"
        else getattr(row, "source_turn_ids_json", "[]")
    )
    value = _safe_json(raw, [])
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            result.append(parsed)
    return result
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
    session_identity = parse_compatibility_chat_stream_identity(sid)
    if (
        session_identity is not None
        and session_identity.chat_type == "group"
    ):
        return session_identity.legacy_runtime_session_id
    user_identity = parse_compatibility_chat_stream_identity(uid)
    if (
        sid.isdigit()
        and user_identity is not None
        and user_identity.chat_type == "group"
    ):
        return user_identity.legacy_runtime_session_id
    return sid


def _session_aliases(session_id: str | None) -> list[str]:
    sid = str(session_id or "").strip()
    if not sid:
        return []
    identity = parse_compatibility_chat_stream_identity(sid)
    if (
        identity is not None
        and identity.platform == "qq"
        and identity.chat_type == "group"
    ):
        return sorted({
            identity.external_session_id,
            identity.legacy_runtime_session_id,
        })
    return [sid]


def _infer_chat_type(session_id: str, explicit: str = "") -> str:
    value = str(explicit or "").strip()
    if value:
        return value
    identity = parse_compatibility_chat_stream_identity(
        str(session_id or "")
    )
    return identity.chat_type if identity is not None else ""


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

    def _session_rows(
        self,
        *,
        limit: int,
        offset: int,
        kind: str,
        include_system_sessions: bool,
    ) -> tuple[int, list[dict[str, Any]]]:
        grouped: dict[str, dict[str, Any]] = {}

        def merge_row(
            *,
            session_id: Any,
            user_id: Any,
            chat_type: Any = "",
            summary_count: int = 0,
            digest_count: int = 0,
            turn_count: int = 0,
            has_archived: bool = False,
            latest_turn_index: int = 0,
            oldest_turn_index: int = 0,
            latest_at: Any = None,
        ) -> None:
            raw_session_id = str(session_id or "").strip()
            if not raw_session_id:
                return
            canonical = _canonical_session_id(
                raw_session_id,
                str(user_id or ""),
            )
            item = grouped.setdefault(canonical, {
                "session_id": canonical,
                "aliases": set(_session_aliases(canonical)),
                "user_id": "",
                "chat_type": "",
                "summary_count": 0,
                "digest_count": 0,
                "turn_count": 0,
                "has_archived": False,
                "latest_turn_index": 0,
                "oldest_turn_index": 0,
                "latest_at": None,
            })
            item["aliases"].add(raw_session_id)
            item["user_id"] = max(
                str(item["user_id"] or ""),
                str(user_id or ""),
            )
            inferred_type = _infer_chat_type(
                canonical,
                str(chat_type or ""),
            )
            if inferred_type:
                item["chat_type"] = max(
                    str(item["chat_type"] or ""),
                    inferred_type,
                )
            item["summary_count"] += int(summary_count or 0)
            item["digest_count"] += int(digest_count or 0)
            item["turn_count"] += int(turn_count or 0)
            item["has_archived"] = (
                bool(item["has_archived"]) or bool(has_archived)
            )
            item["latest_turn_index"] = max(
                int(item["latest_turn_index"] or 0),
                int(latest_turn_index or 0),
            )
            oldest = int(oldest_turn_index or 0)
            if oldest and (
                not item["oldest_turn_index"]
                or oldest < int(item["oldest_turn_index"])
            ):
                item["oldest_turn_index"] = oldest
            if (
                latest_at is not None
                and (
                    item["latest_at"] is None
                    or _iso_any(latest_at)
                    > _iso_any(item["latest_at"])
                )
            ):
                item["latest_at"] = latest_at

        summary_rows = (
            self.db.query(
                RollingSessionSummary.session_id,
                RollingSessionSummary.user_id,
                RollingSessionSummary.chat_type,
                func.count(RollingSessionSummary.id),
                func.max(case(
                    (RollingSessionSummary.status == "archived", 1),
                    else_=0,
                )),
                func.max(
                    RollingSessionSummary.covered_until_turn_id
                ),
                func.min(case(
                    (
                        RollingSessionSummary.covered_from_turn_id > 0,
                        RollingSessionSummary.covered_from_turn_id,
                    ),
                    else_=None,
                )),
                func.max(func.coalesce(
                    RollingSessionSummary.updated_at,
                    RollingSessionSummary.created_at,
                )),
            )
            .filter(
                RollingSessionSummary.session_id.is_not(None),
                RollingSessionSummary.session_id != "",
            )
            .group_by(
                RollingSessionSummary.session_id,
                RollingSessionSummary.user_id,
                RollingSessionSummary.chat_type,
            )
            .all()
        )
        for row in summary_rows:
            merge_row(
                session_id=row[0],
                user_id=row[1],
                chat_type=row[2],
                summary_count=row[3],
                has_archived=bool(row[4]),
                latest_turn_index=row[5],
                oldest_turn_index=row[6],
                latest_at=row[7],
            )

        digest_rows = (
            self.db.query(
                MemoryDigest.session_id,
                MemoryDigest.user_id,
                func.count(MemoryDigest.id),
                func.max(MemoryDigest.created_at),
            )
            .filter(
                MemoryDigest.session_id.is_not(None),
                MemoryDigest.session_id != "",
            )
            .group_by(
                MemoryDigest.session_id,
                MemoryDigest.user_id,
            )
            .all()
        )
        for row in digest_rows:
            merge_row(
                session_id=row[0],
                user_id=row[1],
                digest_count=row[2],
                latest_at=row[3],
            )

        turn_rows = (
            self.db.query(
                ConversationTurn.session_id,
                func.max(ConversationTurn.user_id),
                func.count(ConversationTurn.id),
                func.max(ConversationTurn.id),
                func.min(ConversationTurn.id),
                func.max(ConversationTurn.created_at),
            )
            .filter(
                ConversationTurn.session_id.is_not(None),
                ConversationTurn.session_id != "",
            )
            .group_by(ConversationTurn.session_id)
            .all()
        )
        for row in turn_rows:
            merge_row(
                session_id=row[0],
                user_id=row[1],
                turn_count=row[2],
                latest_turn_index=row[3],
                oldest_turn_index=row[4],
                latest_at=row[5],
            )

        rows = []
        for item in grouped.values():
            if (
                kind == "recent"
                and not (
                    item["summary_count"] > 0
                    or item["turn_count"] > 0
                )
            ):
                continue
            if kind == "long" and item["digest_count"] <= 0:
                continue
            system_probe = {
                **item,
                "session_aliases": item["aliases"],
            }
            if (
                not include_system_sessions
                and _is_system_session(system_probe)
            ):
                continue
            rows.append({
                **item,
                "aliases": ",".join(sorted(item["aliases"])),
                "latest_at": item["latest_at"],
            })
        rows.sort(
            key=lambda item: (
                _iso_any(item["latest_at"]),
                str(item["session_id"]),
            ),
            reverse=True,
        )
        total = len(rows)
        return total, rows[offset:offset + limit]

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

        total, session_rows = self._session_rows(
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
                "active_summary_source_type": "",
                "active_summary_covered_from_source_id": 0,
                "active_summary_covered_until_source_id": 0,
                "active_summary_source_message_count": 0,
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
                source_type = str(
                    getattr(row, "source_type", "conversation_turn")
                    or "conversation_turn"
                )
                source_ids = _summary_source_ids(row)
                item["active_summary_source_type"] = source_type
                if source_type == "chat_log":
                    item["active_summary_covered_from_source_id"] = int(
                        getattr(row, "covered_from_source_id", 0) or 0
                    )
                    item["active_summary_covered_until_source_id"] = int(
                        getattr(row, "covered_until_source_id", 0) or 0
                    )
                else:
                    item["active_summary_covered_from_source_id"] = int(
                        getattr(row, "covered_from_turn_id", 0) or 0
                    )
                    item["active_summary_covered_until_source_id"] = int(
                        getattr(row, "covered_until_turn_id", 0) or 0
                    )
                item["active_summary_source_message_count"] = int(
                    getattr(row, "source_turn_count", 0) or 0
                ) or len(source_ids)

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
        source_type = str(
            getattr(row, "source_type", "conversation_turn")
            or "conversation_turn"
        )
        source_ids = _summary_source_ids(row)
        if source_type == "chat_log":
            covered_from = int(getattr(row, "covered_from_source_id", 0) or 0)
            covered_until = int(getattr(row, "covered_until_source_id", 0) or 0)
        else:
            covered_from = int(getattr(row, "covered_from_turn_id", 0) or 0)
            covered_until = int(getattr(row, "covered_until_turn_id", 0) or 0)
        item = {
            "summary_id": row.id,
            "summary_kind": row.summary_kind or "deterministic_fallback",
            "preview": _preview(row.summary_text),
            # legacy 字段继续保留；新字段按来源类型表达真实覆盖范围。
            "turn_start": int(row.covered_from_turn_id or 0),
            "turn_end": int(row.covered_until_turn_id or 0),
            "source_type": source_type,
            "covered_from_source_id": covered_from,
            "covered_until_source_id": covered_until,
            "source_ids": source_ids,
            "source_ids_json": (
                str(getattr(row, "source_ids_json", "[]") or "[]")
                if source_type == "chat_log"
                else str(getattr(row, "source_turn_ids_json", "[]") or "[]")
            ),
            "source_message_count": int(
                getattr(row, "source_turn_count", 0) or 0
            ) or len(source_ids),
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
                "source_ids_json": _safe_json(row.source_ids_json, []),
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
