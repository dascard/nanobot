"""用户画像注入服务。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.persona.renderer import render_persona_context
from app.persona.retrieval_service import PersonaRetrievalService
from core.db import PersonaFactRepositoryPort, persona_fact_repository
from core.time_utils import db_now_naive


@dataclass
class PersonaInjectionResult:
    context: str = ""
    selected_ids: list[int] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    score_components: dict[str, dict[str, Any]] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)


class PersonaInjectionService:
    def __init__(self, repository: Any):
        self.repository: PersonaFactRepositoryPort = persona_fact_repository(
            repository
        )

    def build_context(
        self,
        *,
        user_id: str,
        current_user_input: str = "",
        recent_messages: list[dict[str, Any]] | None = None,
        max_items: int = 6,
        max_chars: int = 900,
    ) -> PersonaInjectionResult:
        debug: dict[str, Any] = {
            "persona_injected": False,
            "persona_fact_ids": [],
            "persona_skipped": [],
            "persona_context_chars": 0,
            "persona_context": "",
            "score_components": {},
        }
        if not str(user_id or "").strip():
            return PersonaInjectionResult(debug=debug)

        selection = PersonaRetrievalService(self.repository).select(
            user_id=str(user_id),
            current_user_input=current_user_input,
            recent_messages=recent_messages or [],
            max_items=max_items,
            max_chars=max_chars,
        )
        context = render_persona_context(str(user_id), selection.selected)
        debug.update({
            "persona_injected": bool(context),
            "persona_fact_ids": selection.selected_ids,
            "persona_skipped": selection.skipped,
            "persona_context": context,
            "persona_context_chars": len(context),
            "score_components": selection.score_components,
        })
        return PersonaInjectionResult(
            context=context,
            selected_ids=selection.selected_ids,
            skipped=selection.skipped,
            score_components=selection.score_components,
            debug=debug,
        )

    def record_injected(self, selected_ids: list[int]) -> int:
        return record_persona_injected(self.repository, selected_ids)


def record_persona_injected(repository: Any, selected_ids: list[int]) -> int:
    ids: list[int] = []
    for item in selected_ids or []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.append(value)
    if not ids:
        return 0

    facts = persona_fact_repository(repository)
    now = db_now_naive()
    rows = facts.list_by_ids(ids)
    for row in rows:
        row.last_injected_at = now
        row.injected_count = int(row.injected_count or 0) + 1
    facts.flush()
    return len(rows)
