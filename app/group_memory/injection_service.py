"""群体记忆注入服务。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.group_runtime.ids import (
    normalize_group_session_id,
    normalize_group_stream_id,
    raw_group_id,
)

from app.group_memory.renderer import render_group_memory_context
from app.group_memory.retrieval_service import GroupMemoryRetrievalService


@dataclass
class GroupMemoryInjectionResult:
    context: str = ""
    selected_ids: list[int] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    score_components: dict[str, dict[str, Any]] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)


def group_memory_config_ids(group_id: str) -> list[str]:
    raw = raw_group_id(group_id)
    candidates = [
        normalize_group_stream_id(raw),
        normalize_group_session_id(raw),
    ]
    return list(dict.fromkeys(x for x in candidates if x))


class GroupMemoryInjectionService:
    def __init__(self, db: Session):
        self.db = db

    def build_context(
        self,
        *,
        group_id: str,
        current_user_input: str = "",
        recent_messages: list[dict[str, Any]] | None = None,
        max_items: int = 10,
        max_chars: int = 1200,
    ) -> GroupMemoryInjectionResult:
        from core.database import ChatStreamConfig

        session_id = normalize_group_session_id(group_id)
        cfg_ids = group_memory_config_ids(group_id)
        cfg_rows = (
            self.db.query(ChatStreamConfig)
            .filter(ChatStreamConfig.chat_stream_id.in_(cfg_ids))
            .all()
            if cfg_ids else None
        )
        rows_by_id = {row.chat_stream_id: row for row in (cfg_rows or [])}
        cfg = next((rows_by_id[candidate] for candidate in cfg_ids if candidate in rows_by_id), None)
        mode = (cfg.group_profile_mode or "off") if cfg else "off"
        debug: dict[str, Any] = {
            "group_profile_mode": mode,
            "group_memory_injected": False,
            "group_memory_ids": [],
            "group_memory_skipped": [],
            "group_memory_context_chars": 0,
            "group_memory_context": "",
            "score_components": {},
            "group_memory_config_ids": cfg_ids,
        }
        if mode not in {"preview", "on"}:
            return GroupMemoryInjectionResult(debug=debug)

        selection = GroupMemoryRetrievalService(self.db).select(
            group_id=session_id,
            current_user_input=current_user_input,
            recent_messages=recent_messages or [],
            max_items=max_items,
            max_chars=max_chars,
        )
        context = render_group_memory_context(session_id, selection.selected)
        debug.update({
            "group_memory_ids": selection.selected_ids,
            "group_memory_skipped": selection.skipped,
            "group_memory_context": context,
            "group_memory_context_chars": len(context),
            "score_components": selection.score_components,
        })
        if mode == "on" and context:
            debug["group_memory_injected"] = True
            self._record_injected(selection.selected)
            return GroupMemoryInjectionResult(
                context=context,
                selected_ids=selection.selected_ids,
                skipped=selection.skipped,
                score_components=selection.score_components,
                debug=debug,
            )
        return GroupMemoryInjectionResult(
            context="",
            selected_ids=selection.selected_ids,
            skipped=selection.skipped,
            score_components=selection.score_components,
            debug=debug,
        )

    def _record_injected(self, memories: list[Any]) -> None:
        if not memories:
            return
        now = datetime.now()
        for row in memories:
            row.last_injected_at = now
            row.injected_count = int(row.injected_count or 0) + 1
        self.db.flush()
