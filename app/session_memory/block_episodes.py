"""块式会话记忆(P3):封口块 → 历史 episode(系统 B)。

见 docs/superpowers/specs/2026-07-26-block-session-memory-design.md 机制 3。

封口时把块的最佳滚动摘要「固化」成一条 episode:
- seed 已是 LLM 摘要 → 直接固化为 ``llm_episode``(免二次 LLM 调用);
- 否则 → 由块内 turn 或 fallback 摘要生成 ``deterministic_fallback`` episode。

episode 是历史块的长期记忆单元,供 P4 召回(上一块恒召回 + 未来 RAG 跨块)。
本模块只做「封口即产出可召回 episode」;LLM 二次精炼(BlockEpisodeJob)与语义
索引作为后续增强,不在此实现。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from app.session_memory.rolling_summary import (
    archive_active_summaries_for_session,
    get_best_session_summary,
)
from app.session_memory.summarizer import (
    build_rolling_summary_payload,
    render_summary_text,
)
from core.db.models.chat import ConversationTurn
from core.db.models.session_memory import (
    ConversationBlock,
    ConversationBlockEpisode,
)

logger = logging.getLogger("nanobot.session_memory.block_episodes")

_LLM_SUMMARY_KINDS = {"llm_episode", "llm_summary"}


def get_active_episode_for_block(
    db: Any,
    block_id: int,
) -> ConversationBlockEpisode | None:
    """返回某块当前 active 的 episode;无则 None。"""

    return (
        db.query(ConversationBlockEpisode)
        .filter(
            ConversationBlockEpisode.block_id == int(block_id),
            ConversationBlockEpisode.status == "active",
        )
        .order_by(ConversationBlockEpisode.id.desc())
        .first()
    )


def get_previous_closed_block(
    db: Any,
    session_id: str,
    *,
    after_clear_at: datetime | None = None,
) -> ConversationBlock | None:
    """返回该 session 最近一个已封口块(block_seq 最大);供 P4 上一块恒召回。"""

    query = db.query(ConversationBlock).filter(
        ConversationBlock.session_id == session_id,
        ConversationBlock.status == "closed",
    )
    if after_clear_at is not None:
        query = query.filter(ConversationBlock.last_turn_at > after_clear_at)
    return query.order_by(ConversationBlock.block_seq.desc()).first()


def _load_block_turns(db: Any, block: ConversationBlock) -> list[ConversationTurn]:
    first_id = int(block.first_turn_id or 0)
    last_id = int(block.last_turn_id or 0)
    if first_id <= 0 or last_id < first_id:
        return []
    return list(
        db.query(ConversationTurn)
        .filter(
            ConversationTurn.session_id == block.session_id,
            ConversationTurn.id >= first_id,
            ConversationTurn.id <= last_id,
        )
        .order_by(ConversationTurn.id.asc())
        .all()
    )


def _episode_stable_hash(
    *,
    block_id: int,
    covered_first: int,
    covered_last: int,
    summary_kind: str,
    summary_text: str,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "block_id": int(block_id),
                "covered": [int(covered_first), int(covered_last)],
                "kind": summary_kind,
                "summary": summary_text,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def seal_block_to_episode(
    db: Any,
    block: ConversationBlock,
    *,
    reason: str,
    now: datetime | None = None,
) -> ConversationBlockEpisode:
    """把已确定要封口的块固化成一条 active episode(幂等)。

    调用方负责把 block.status 置为 closed;本函数只产出 episode、设置
    block.episode_id 并归档该块的滚动摘要(已固化进 episode)。
    """

    now = now or datetime.now()

    existing = get_active_episode_for_block(db, int(block.id))
    if existing is not None:
        # 一块至多一条 active episode(UNIQUE(block_id));重复封口 no-op。
        if block.episode_id is None:
            block.episode_id = int(existing.id)
        return existing

    seed = get_best_session_summary(db, block.session_id, block_id=int(block.id))
    if seed is not None:
        summary_text = str(seed.summary_text or "")
        summary_json_str = str(seed.summary_json or "{}")
        seed_summary_id: int | None = int(seed.id)
        is_llm = (seed.summary_kind or "") in _LLM_SUMMARY_KINDS
        quality_score = float(seed.quality_score or 0.0)
    else:
        turns = _load_block_turns(db, block)
        if turns:
            payload = build_rolling_summary_payload(
                previous_summary=None,
                pending_turns=turns,
            )
        else:
            payload = {"summary": ""}
        summary_text = render_summary_text(payload)
        summary_json_str = json.dumps(payload, ensure_ascii=False)
        seed_summary_id = None
        is_llm = False
        quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
        quality_score = float(quality.get("score") or 0.0)

    summary_kind = "llm_episode" if is_llm else "deterministic_fallback"
    llm_status = "done" if is_llm else ""
    covered_first = int(block.first_turn_id or 0)
    covered_last = int(block.last_turn_id or 0)
    stable_hash = _episode_stable_hash(
        block_id=int(block.id),
        covered_first=covered_first,
        covered_last=covered_last,
        summary_kind=summary_kind,
        summary_text=summary_text,
    )

    episode = ConversationBlockEpisode(
        block_id=int(block.id),
        block_seq=int(block.block_seq or 0),
        session_id=block.session_id,
        user_id=block.user_id or "",
        chat_type=block.chat_type or "private",
        status="active",
        summary_kind=summary_kind,
        llm_status=llm_status,
        summary_text=summary_text,
        summary_json=summary_json_str,
        covered_first_turn_id=covered_first,
        covered_last_turn_id=covered_last,
        source_turn_ids_json=json.dumps([], ensure_ascii=False),
        source_turn_count=int(block.turn_count or 0),
        seed_summary_id=seed_summary_id,
        quality_score=quality_score,
        model="deterministic" if not is_llm else str(getattr(seed, "model", "") or ""),
        stable_hash=stable_hash,
        source_revision=stable_hash,
        created_at=now,
        sealed_at=now,
        updated_at=now,
        meta_json=json.dumps(
            {"reason": str(reason or ""), "block_seq": int(block.block_seq or 0)},
            ensure_ascii=False,
        ),
    )
    db.add(episode)
    db.flush()

    block.episode_id = int(episode.id)
    # 该块滚动摘要已固化进 episode,归档避免系统 A 继续把它当当前块摘要。
    archive_active_summaries_for_session(db, block.session_id, block_id=int(block.id))
    logger.info(
        "[BlockEpisode] created session=%s block_id=%s seq=%s kind=%s seed=%s chars=%s",
        block.session_id, block.id, block.block_seq, summary_kind,
        seed_summary_id or 0, len(summary_text),
    )
    return episode


__all__ = [
    "get_active_episode_for_block",
    "get_previous_closed_block",
    "seal_block_to_episode",
]
