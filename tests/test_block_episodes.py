"""块式会话记忆(P3):封口块 → 历史 episode。

见 docs/superpowers/specs/2026-07-26-block-session-memory-design.md 机制 3。
覆盖:gap 封口即产出 active episode、LLM 摘要固化为 llm_episode、封口归档块
滚动摘要、幂等封口、get_previous_closed_block / get_active_episode_for_block。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.session_memory import blocks, config
from app.session_memory.block_episodes import (
    get_active_episode_for_block,
    get_previous_closed_block,
    seal_block_to_episode,
)
from core.database import (
    ConversationBlock,
    ConversationBlockEpisode,
    ConversationTurn,
    RollingSessionSummary,
)


@pytest.fixture
def enable_blocks(monkeypatch):
    monkeypatch.setattr(blocks, "is_block_memory_enabled", lambda session_id="": True)


def _exchange(db, *, at, session_id="private_u1", user_id="u1"):
    user_turn = ConversationTurn(
        user_id=user_id, session_id=session_id, role="user",
        content="用户消息内容用于测试块封口", created_at=at,
    )
    assistant_turn = ConversationTurn(
        user_id=user_id, session_id=session_id, role="assistant",
        content="助手回复内容用于测试块封口", created_at=at,
    )
    db.add(user_turn)
    db.add(assistant_turn)
    db.flush()
    return [user_turn, assistant_turn]


def _assign(db, turns, now):
    return blocks.assign_turns_to_block(
        db, session_id="private_u1", user_id="u1", chat_type="private",
        turns=turns, now=now,
    )


def test_gap_seal_produces_active_episode(db_session, enable_blocks):
    at1 = datetime(2026, 7, 26, 10, 0, 0)
    block1 = _assign(db_session, _exchange(db_session, at=at1), at1)

    at2 = at1 + timedelta(seconds=config.BLOCK_GAP_SECONDS + 5)
    _assign(db_session, _exchange(db_session, at=at2), at2)

    sealed = db_session.get(ConversationBlock, block1.id)
    assert sealed.status == "closed"
    assert sealed.episode_id is not None

    episode = get_active_episode_for_block(db_session, block1.id)
    assert episode is not None
    assert episode.status == "active"
    assert episode.block_id == block1.id
    assert episode.summary_kind == "deterministic_fallback"
    assert episode.covered_first_turn_id == sealed.first_turn_id
    assert episode.covered_last_turn_id == sealed.last_turn_id
    assert db_session.get(ConversationBlockEpisode, sealed.episode_id).id == episode.id


def test_episode_from_llm_summary_is_llm_episode(db_session, enable_blocks):
    at1 = datetime(2026, 7, 26, 10, 0, 0)
    block1 = _assign(db_session, _exchange(db_session, at=at1), at1)

    # 该块已有一条 LLM 质量的滚动摘要(系统 A 升级产物)。
    llm_summary = RollingSessionSummary(
        session_id="private_u1", user_id="u1", chat_type="private",
        status="active", summary_kind="llm_episode",
        summary_text="块1的LLM摘要", summary_json='{"summary":"块1的LLM摘要"}',
        covered_until_turn_id=block1.last_turn_id, block_id=block1.id,
        updated_at=at1,
    )
    db_session.add(llm_summary)
    db_session.flush()

    episode = seal_block_to_episode(db_session, block1, reason="manual", now=at1)
    assert episode.summary_kind == "llm_episode"
    assert episode.llm_status == "done"
    assert episode.summary_text == "块1的LLM摘要"
    assert episode.seed_summary_id == llm_summary.id

    # 块的滚动摘要固化进 episode 后应归档。
    db_session.refresh(llm_summary)
    assert llm_summary.status == "archived"


def test_seal_is_idempotent(db_session, enable_blocks):
    at1 = datetime(2026, 7, 26, 10, 0, 0)
    block1 = _assign(db_session, _exchange(db_session, at=at1), at1)

    ep1 = seal_block_to_episode(db_session, block1, reason="manual", now=at1)
    ep2 = seal_block_to_episode(db_session, block1, reason="manual", now=at1)

    assert ep1.id == ep2.id
    count = (
        db_session.query(ConversationBlockEpisode)
        .filter(ConversationBlockEpisode.block_id == block1.id)
        .count()
    )
    assert count == 1


def test_get_previous_closed_block(db_session, enable_blocks):
    at1 = datetime(2026, 7, 26, 10, 0, 0)
    block1 = _assign(db_session, _exchange(db_session, at=at1), at1)
    assert get_previous_closed_block(db_session, "private_u1") is None  # 只有 open 块

    at2 = at1 + timedelta(seconds=config.BLOCK_GAP_SECONDS + 5)
    _assign(db_session, _exchange(db_session, at=at2), at2)

    prev = get_previous_closed_block(db_session, "private_u1")
    assert prev is not None
    assert prev.id == block1.id
    assert prev.status == "closed"


def test_previous_closed_block_returns_most_recent(db_session, enable_blocks):
    base = datetime(2026, 7, 26, 8, 0, 0)
    for i in range(3):
        at = base + timedelta(seconds=i * (config.BLOCK_GAP_SECONDS + 60))
        _assign(db_session, _exchange(db_session, at=at), at)

    # 3 个块:seq1、seq2 已封口,seq3 open。上一块应是 seq2。
    prev = get_previous_closed_block(db_session, "private_u1")
    assert prev.block_seq == 2
