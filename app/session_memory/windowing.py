"""确定 Session raw window 与 rolling summary 的 turn 边界。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.session_memory import config
from core.db.models.chat import ChatLog, ConversationTurn
from core.token_utils import estimate_tokens

RAW_WINDOW_CANDIDATE_MIN_LIMIT = 200
RAW_WINDOW_CANDIDATE_MULTIPLIER = 8
RAW_WINDOW_CANDIDATE_HARD_LIMIT = 2000
PENDING_FOR_SUMMARY_HARD_LIMIT = 5000

INTERNAL_KINDS = frozenset({
    "context_gap_marker",
    "tool_internal",
    "no_send",
    "reply_contract_retry",
    "system_control",
    "empty_reply",
})
LEADING_ASSISTANT_CONTEXT_KINDS = frozenset({"outbound_delivery_summary"})

USERNAME_MARKER_RE = re.compile(r"\[用户名\]\s*([^\r\n\[]+)")


def safe_meta(meta_json: str | None) -> dict[str, Any]:
    try:
        value = json.loads(meta_json or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _sender_key_for_turn(turn: ConversationTurn) -> str:
    meta = safe_meta(getattr(turn, "meta_json", "{}"))
    for key in ("sender_id", "sender_name", "nickname", "display_name"):
        value = str(meta.get(key) or "").strip()
        if value:
            return value

    content = str(getattr(turn, "content", "") or "")
    match = USERNAME_MARKER_RE.search(content)
    if match:
        return match.group(1).strip()
    return ""


def is_context_eligible_turn(turn: ConversationTurn) -> tuple[bool, str]:
    meta = safe_meta(getattr(turn, "meta_json", "{}"))
    moderation = meta.get("moderation")
    if isinstance(moderation, dict) and moderation.get("no_context"):
        return False, "moderation_no_context"
    if meta.get("no_context") or meta.get("internal"):
        return False, "meta_no_context"

    kind = str(meta.get("kind", "chat") or "chat")
    if kind in INTERNAL_KINDS:
        return False, f"internal_kind:{kind}"

    role = str(getattr(turn, "role", "") or "")
    if role not in {"user", "assistant"}:
        return False, f"unsupported_role:{role}"

    if not str(getattr(turn, "content", "") or "").strip():
        return False, "empty_content"

    return True, ""


def is_context_eligible_chat_log(row: ChatLog) -> tuple[bool, str]:
    """判断 ChatLog 是否可进入群聊原文窗口或摘要来源。"""

    meta = safe_meta(getattr(row, "meta_json", "{}"))
    moderation = meta.get("moderation")
    if isinstance(moderation, dict) and moderation.get("no_context"):
        return False, "moderation_no_context"
    if meta.get("no_context") or meta.get("internal"):
        return False, "meta_no_context"

    kind = str(meta.get("kind", "chat") or "chat")
    if kind in INTERNAL_KINDS:
        return False, f"internal_kind:{kind}"

    role = str(getattr(row, "role", "") or "")
    if role not in {"ambient", "user", "assistant"}:
        return False, f"unsupported_role:{role}"
    if not str(getattr(row, "content", "") or "").strip():
        return False, "empty_content"
    return True, ""


def is_allowed_leading_assistant(item: Mapping[str, Any]) -> bool:
    """仅允许服务端确认投递事件作为上下文的起始 assistant。"""

    if str(item.get("role") or "") != "assistant":
        return False
    meta = safe_meta(str(item.get("meta_json") or "{}"))
    return str(meta.get("kind") or "") in LEADING_ASSISTANT_CONTEXT_KINDS


def load_context_eligible_turns(
    db: Session,
    *,
    session_id: str,
    user_id: str = "",
    after_clear_at=None,
    after_turn_id: int = 0,
    hard_limit: int = 500,
) -> tuple[list[ConversationTurn], dict[str, Any]]:
    query = db.query(ConversationTurn).filter(
        ConversationTurn.session_id == session_id,
        ConversationTurn.id > int(after_turn_id or 0),
    )
    if after_clear_at is not None:
        query = query.filter(ConversationTurn.created_at > after_clear_at)

    rows = query.order_by(ConversationTurn.id.asc()).limit(max(1, int(hard_limit))).all()
    eligible: list[ConversationTurn] = []
    skipped: list[dict[str, Any]] = []
    for turn in rows:
        ok, reason = is_context_eligible_turn(turn)
        if ok:
            eligible.append(turn)
        else:
            skipped.append({"turn_id": int(turn.id), "reason": reason})

    return eligible, {
        "eligible_count": len(eligible),
        "skipped": skipped,
        "after_turn_id": int(after_turn_id or 0),
    }


def _base_context_turn_query(
    db: Session,
    *,
    session_id: str,
    after_clear_at=None,
    after_turn_id: int = 0,
):
    query = db.query(ConversationTurn).filter(
        ConversationTurn.session_id == session_id,
        ConversationTurn.id > int(after_turn_id or 0),
    )
    if after_clear_at is not None:
        query = query.filter(ConversationTurn.created_at > after_clear_at)
    return query


def load_latest_raw_window(
    db: Session,
    *,
    session_id: str,
    chat_type: str,
    max_turns: int,
    max_tokens: int,
    max_per_msg: int = 300,
    after_clear_at=None,
    after_turn_id: int = 0,
    hard_limit: int = RAW_WINDOW_CANDIDATE_HARD_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """从最新 ConversationTurn 向前加载 raw window 候选。

    不先按 id.asc() 截断，避免大 session 只拿到最早一批 turn。
    """
    normalized_max_turns = max(1, int(max_turns))
    normalized_hard_limit = max(normalized_max_turns, int(hard_limit or RAW_WINDOW_CANDIDATE_HARD_LIMIT))
    batch_size = max(
        RAW_WINDOW_CANDIDATE_MIN_LIMIT,
        normalized_max_turns * RAW_WINDOW_CANDIDATE_MULTIPLIER,
    )
    query = _base_context_turn_query(
        db,
        session_id=session_id,
        after_clear_at=after_clear_at,
        after_turn_id=after_turn_id,
    )
    eligible_desc: list[ConversationTurn] = []
    skipped: list[dict[str, Any]] = []
    loaded = 0
    offset = 0

    while loaded < normalized_hard_limit:
        rows = (
            query.order_by(ConversationTurn.id.desc())
            .offset(offset)
            .limit(min(batch_size, normalized_hard_limit - loaded))
            .all()
        )
        if not rows:
            break
        loaded += len(rows)
        offset += len(rows)
        for turn in rows:
            ok, reason = is_context_eligible_turn(turn)
            if ok:
                eligible_desc.append(turn)
            else:
                skipped.append({"turn_id": int(turn.id), "reason": reason})
        if len(eligible_desc) >= normalized_max_turns:
            break

    eligible_asc = sorted(eligible_desc, key=lambda row: int(row.id or 0))
    recent_window, raw_debug = select_latest_raw_window(
        eligible_asc,
        chat_type=chat_type,
        max_turns=max_turns,
        max_tokens=max_tokens,
        max_per_msg=max_per_msg,
    )
    raw_debug.update({
        "raw_candidates_loaded": loaded,
        "raw_candidates_eligible": len(eligible_desc),
        "raw_candidate_hard_limit": normalized_hard_limit,
        "raw_window_skipped": skipped,
        "after_turn_id": int(after_turn_id or 0),
    })
    return recent_window, raw_debug


def load_pending_for_summary_turns(
    db: Session,
    *,
    session_id: str,
    last_covered_id: int,
    raw_window_start_turn_id: int,
    after_clear_at=None,
    hard_limit: int = PENDING_FOR_SUMMARY_HARD_LIMIT,
) -> tuple[list[ConversationTurn], dict[str, Any]]:
    if raw_window_start_turn_id <= 0:
        return [], {"pending_loaded": 0, "pending_skipped": [], "pending_truncated": False}

    normalized_hard_limit = max(1, int(hard_limit or PENDING_FOR_SUMMARY_HARD_LIMIT))
    rows = (
        _base_context_turn_query(
            db,
            session_id=session_id,
            after_clear_at=after_clear_at,
            after_turn_id=last_covered_id,
        )
        .filter(ConversationTurn.id < int(raw_window_start_turn_id))
        .order_by(ConversationTurn.id.asc())
        .limit(normalized_hard_limit)
        .all()
    )
    pending: list[ConversationTurn] = []
    skipped: list[dict[str, Any]] = []
    for turn in rows:
        ok, reason = is_context_eligible_turn(turn)
        if ok:
            pending.append(turn)
        else:
            skipped.append({"turn_id": int(turn.id), "reason": reason})
    return pending, {
        "pending_loaded": len(rows),
        "pending_count": len(pending),
        "pending_skipped": skipped,
        "pending_truncated": len(rows) >= normalized_hard_limit,
        "last_covered_id": int(last_covered_id or 0),
        "raw_window_start_turn_id": int(raw_window_start_turn_id or 0),
    }


def normalize_turn_for_prompt(
    turn: ConversationTurn,
    *,
    max_per_msg: int = 300,
) -> dict[str, Any] | None:
    from core.context_builder import LONG_USER_MESSAGE_CHARS, sanitize_prompt_text

    raw_content = str(getattr(turn, "content", "") or "")
    # 长消息判定必须基于原文长度：先按 max_per_msg 截断再判会让该分支
    # 永远不可达（真实链路 max_per_msg=300 < 2000）。
    if turn.role == "user" and len(raw_content) > LONG_USER_MESSAGE_CHARS:
        preview = sanitize_prompt_text(raw_content[:200]).rstrip()
        content = (
            f"[长消息摘要] 用户发送了约 {len(raw_content)} 字符的长消息，"
            f"开头为: {preview}...[截断]"
        )
    else:
        content = sanitize_prompt_text(raw_content, max_per_msg)
    if not content.strip():
        return None

    token_cost = max(len(content), estimate_tokens(content))
    return {
        "turn_id": int(turn.id),
        "role": turn.role,
        "content": content,
        "created_at": turn.created_at,
        "token_cost": token_cost,
        "meta_json": turn.meta_json,
    }


def select_latest_raw_window(
    eligible_turns: Sequence[ConversationTurn],
    *,
    chat_type: str,
    max_turns: int,
    max_tokens: int,
    max_per_msg: int = 300,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected_desc: list[dict[str, Any]] = []
    total_tokens = 0
    normalized_max_turns = max(1, int(max_turns))
    normalized_max_tokens = max(1, int(max_tokens))

    for turn in reversed(list(eligible_turns)):
        item = normalize_turn_for_prompt(turn, max_per_msg=max_per_msg)
        if item is None:
            continue
        if selected_desc and len(selected_desc) >= normalized_max_turns:
            break
        if selected_desc and total_tokens + int(item["token_cost"]) > normalized_max_tokens:
            break
        selected_desc.append(item)
        total_tokens += int(item["token_cost"])

    selected = list(reversed(selected_desc))
    while (
        selected
        and selected[0]["role"] == "assistant"
        and not is_allowed_leading_assistant(selected[0])
    ):
        selected.pop(0)
    total_tokens = sum(int(item.get("token_cost") or 0) for item in selected)

    turn_ids = [int(item["turn_id"]) for item in selected]
    return selected, {
        "raw_window_turn_ids": turn_ids,
        "raw_window_start_turn_id": turn_ids[0] if turn_ids else 0,
        "raw_window_end_turn_id": turn_ids[-1] if turn_ids else 0,
        "raw_window_tokens": total_tokens,
        "raw_window_count": len(selected),
        "chat_type": chat_type,
    }


def select_pending_for_summary(
    eligible_after_summary: Sequence[ConversationTurn],
    *,
    last_covered_id: int,
    raw_window_start_turn_id: int,
) -> list[ConversationTurn]:
    if raw_window_start_turn_id <= 0:
        return []
    return [
        turn for turn in eligible_after_summary
        if int(turn.id) > int(last_covered_id or 0)
        and int(turn.id) < int(raw_window_start_turn_id)
    ]


def should_rollup(
    pending: Sequence[ConversationTurn],
    *,
    chat_type: str,
    force: bool = False,
) -> tuple[bool, dict[str, Any]]:
    if not pending:
        return False, {"reason": "empty_pending"}
    if force:
        return True, {"reason": "force"}

    user_turns = [turn for turn in pending if turn.role == "user"]
    char_count = sum(len(str(turn.content or "")) for turn in pending)
    token_count = sum(estimate_tokens(str(turn.content or "")) for turn in pending)
    distinct_senders = {_sender_key_for_turn(turn) for turn in pending}
    distinct_senders.discard("")

    if chat_type == "group":
        ok = (
            len(pending) >= config.GROUP_ROLLING_MIN_TURNS
            or len(user_turns) >= config.GROUP_ROLLING_MIN_USER_TURNS
            or char_count >= config.GROUP_ROLLING_MIN_CHARS
            or token_count >= config.GROUP_ROLLING_LEGACY_MIN_TOKENS
        )
        if (
            ok
            and len(user_turns) >= 2
            and len(distinct_senders)
            < config.GROUP_ROLLING_MIN_DISTINCT_SENDERS
        ):
            return False, {
                "reason": "not_enough_distinct_senders",
                "turns": len(pending),
                "user_turns": len(user_turns),
                "chars": char_count,
                "tokens": token_count,
                "distinct_senders": len(distinct_senders),
            }
        return ok, {
            "reason": "threshold" if ok else "below_threshold",
            "turns": len(pending),
            "user_turns": len(user_turns),
            "chars": char_count,
            "tokens": token_count,
            "distinct_senders": len(distinct_senders),
        }

    ok = (
        len(pending) >= config.PRIVATE_ROLLING_MIN_TURNS
        or (
            len(user_turns) >= config.PRIVATE_ROLLING_MIN_USER_TURNS
            and token_count >= config.PRIVATE_ROLLING_MIN_TOKENS
        )
        or char_count >= config.PRIVATE_ROLLING_MIN_CHARS
        or token_count >= config.PRIVATE_ROLLING_MIN_TOKENS
    )
    return ok, {
        "reason": "threshold" if ok else "below_threshold",
        "turns": len(pending),
        "user_turns": len(user_turns),
        "chars": char_count,
        "tokens": token_count,
    }


def raw_window_limits(chat_type: str, *, max_total: int | None = None) -> tuple[int, int]:
    if chat_type == "group":
        return (
            config.GROUP_RAW_WINDOW_MAX_TURNS,
            int(max_total or config.GROUP_RAW_WINDOW_MAX_TOKENS),
        )
    return (
        config.PRIVATE_CACHE_EPOCH_LOW_WATER_TURNS,
        int(max_total or config.PRIVATE_CACHE_EPOCH_LOW_WATER_TOKENS),
    )


def cache_epoch_window_limits(
    chat_type: str,
    *,
    max_total: int | None = None,
) -> tuple[int, int, int, int]:
    """返回 (低水位 turns/tokens, 高水位 turns/tokens)。"""

    low_turns, low_tokens = raw_window_limits(chat_type, max_total=max_total)
    if chat_type == "group":
        configured_low = max(1, config.GROUP_CACHE_EPOCH_LOW_WATER_TOKENS)
        configured_high = max(
            configured_low + 1,
            config.GROUP_CACHE_EPOCH_HIGH_WATER_TOKENS,
        )
        high_tokens = max(
            low_tokens + 1,
            round(low_tokens * configured_high / configured_low),
        )
        return low_turns, low_tokens, low_turns, high_tokens

    configured_low = max(1, config.PRIVATE_CACHE_EPOCH_LOW_WATER_TOKENS)
    configured_high = max(
        configured_low + 1,
        config.PRIVATE_CACHE_EPOCH_HIGH_WATER_TOKENS,
    )
    high_tokens = max(
        low_tokens + 1,
        round(low_tokens * configured_high / configured_low),
    )
    return (
        low_turns,
        low_tokens,
        max(low_turns + 1, config.PRIVATE_CACHE_EPOCH_HIGH_WATER_TURNS),
        high_tokens,
    )
