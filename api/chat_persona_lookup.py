"""聊天 persona snapshot lookup helper。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatPersonaSnapshot:
    persona_obj: Any | None
    persona_json: str
    persona_data: dict[str, Any]
    persona_text: str
    lookup_user_id: str
    matched_user_id: str | None
    candidate_count: int
    parse_failed: bool


def iter_persona_user_id_candidates(uid: str) -> list[str]:
    candidates: list[str] = [uid]
    for prefix in ("private_", "group_"):
        if not uid.startswith(prefix):
            candidates.append(f"{prefix}{uid}")
    for prefix in ("private_", "group_"):
        if uid.startswith(prefix):
            candidates.append(uid[len(prefix):])
    return list(dict.fromkeys(candidates))


def _parse_persona_json(value: str) -> tuple[dict[str, Any], bool]:
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}, True
    if not isinstance(data, dict):
        return {}, True
    return data, False


def resolve_chat_persona_snapshot(
    db: Any,
    user_id: str,
    *,
    persona_model: Any,
    format_persona: Callable[[dict[str, Any]], str],
) -> ChatPersonaSnapshot:
    lookup_user_id = str(user_id or "")
    candidates = iter_persona_user_id_candidates(lookup_user_id)
    persona_obj = None
    matched_user_id: str | None = None
    for candidate in candidates:
        persona_obj = db.query(persona_model).filter(persona_model.user_id == candidate).first()
        if (
            persona_obj is not None
            and str(getattr(persona_obj, "status", "active") or "") == "active"
        ):
            matched_user_id = candidate
            break
        persona_obj = None

    persona_json = str(getattr(persona_obj, "persona_json", "{}") if persona_obj else "{}")
    persona_data, parse_failed = _parse_persona_json(persona_json)
    persona_text = format_persona(persona_data)
    return ChatPersonaSnapshot(
        persona_obj=persona_obj,
        persona_json=persona_json,
        persona_data=persona_data,
        persona_text=persona_text,
        lookup_user_id=lookup_user_id,
        matched_user_id=matched_user_id,
        candidate_count=len(candidates),
        parse_failed=parse_failed,
    )
