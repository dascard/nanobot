"""主动外呼重复主题的纯策略。"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol


class PreviousOutreach(Protocol):
    id: int
    created_at: datetime | None
    next_intent: str


def normalized_topic_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def generation_input_grounding(
    value: Mapping[str, Any],
    *,
    metadata_key: str,
) -> Mapping[str, Any]:
    metadata = value.get(metadata_key)
    if isinstance(metadata, Mapping):
        input_grounding = metadata.get("input_grounding")
        if isinstance(input_grounding, Mapping):
            return input_grounding
    return value


def last_user_anchor(
    grounding: Mapping[str, Any],
    *,
    metadata_key: str,
) -> tuple[str, str]:
    resolved = generation_input_grounding(grounding, metadata_key=metadata_key)
    last_user = resolved.get("last_user_message")
    if not isinstance(last_user, Mapping):
        return "", ""
    return (
        normalized_topic_text(last_user.get("content")),
        str(last_user.get("created_at") or "").strip(),
    )


def previous_research_query(
    grounding: Mapping[str, Any],
    *,
    metadata_key: str,
) -> str:
    metadata = grounding.get(metadata_key)
    if not isinstance(metadata, Mapping):
        return ""
    judge = metadata.get("judge")
    if not isinstance(judge, Mapping):
        return ""
    return normalized_topic_text(judge.get("research_query"))


def repeated_topic_guard(
    previous: PreviousOutreach | None,
    *,
    previous_grounding: Mapping[str, Any],
    grounding: Mapping[str, Any],
    judge: Mapping[str, Any],
    now: datetime,
    cooldown_min: int,
    metadata_key: str,
) -> dict[str, Any] | None:
    if cooldown_min <= 0 or previous is None or previous.created_at is None:
        return None
    cooldown_until = previous.created_at + timedelta(minutes=cooldown_min)
    if now >= cooldown_until:
        return None
    if last_user_anchor(
        grounding,
        metadata_key=metadata_key,
    ) != last_user_anchor(previous_grounding, metadata_key=metadata_key):
        return None

    current_query = normalized_topic_text(judge.get("research_query"))
    previous_query = previous_research_query(
        previous_grounding,
        metadata_key=metadata_key,
    )
    current_intent = normalized_topic_text(judge.get("next_intent"))
    previous_intent = normalized_topic_text(previous.next_intent)
    if current_query and current_query == previous_query:
        reason_code = "same_research_query"
    elif current_intent and current_intent == previous_intent:
        reason_code = "same_next_intent"
    else:
        return None
    return {
        "reason_code": reason_code,
        "matched_log_id": int(previous.id),
        "cooldown_until": cooldown_until,
    }


__all__ = [
    "generation_input_grounding",
    "last_user_anchor",
    "normalized_topic_text",
    "previous_research_query",
    "repeated_topic_guard",
]
