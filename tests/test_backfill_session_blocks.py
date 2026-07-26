"""块式会话记忆(P5):存量回填脚本。

见 docs/superpowers/specs/2026-07-26-block-session-memory-design.md P5。
覆盖:多段切块+episode、近期尾段保留 open、群聊跳过、幂等、已有块只补前缀、
dry-run 不落盘。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from scripts.backfill_session_blocks import backfill_session_blocks
from app.session_memory import config
from core.database import (
    ConversationBlock,
    ConversationBlockEpisode,
    ConversationTurn,
)


def _add_exchange(db, *, at, session_id="private_u1", user_id="u1", text="消息内容"):
    for role in ("user", "assistant"):
        db.add(ConversationTurn(
            user_id=user_id, session_id=session_id, role=role,
            content=f"{text}-{role}", created_at=at,
        ))
    db.flush()


GAP = timedelta(seconds=config.BLOCK_GAP_SECONDS + 60)


def test_backfill_splits_runs_and_creates_episodes(db_session):
    base = datetime(2026, 7, 20, 10, 0, 0)
    for i in range(3):
        _add_exchange(db_session, at=base + i * GAP, text=f"话题{i}")
    now = base + 10 * GAP  # 尾段早已冷却 → 全部封口

    stats = backfill_session_blocks(db_session, now=now)

    assert stats.sessions_backfilled == 1
    assert stats.blocks_created == 3
    assert stats.blocks_left_open == 0
    assert stats.episodes_created == 3

    rows = (
        db_session.query(ConversationBlock)
        .order_by(ConversationBlock.block_seq.asc())
        .all()
    )
    assert [b.block_seq for b in rows] == [1, 2, 3]
    assert all(b.status == "closed" and b.closed_reason == "backfill" for b in rows)
    assert all(b.episode_id is not None for b in rows)
    assert db_session.query(ConversationBlockEpisode).count() == 3


def test_backfill_keeps_recent_tail_open(db_session):
    base = datetime(2026, 7, 26, 10, 0, 0)
    _add_exchange(db_session, at=base, text="旧话题")
    _add_exchange(db_session, at=base + GAP, text="正在聊")
    now = base + GAP + timedelta(minutes=5)  # 尾段仍在 gap 窗口内

    stats = backfill_session_blocks(db_session, now=now)

    assert stats.blocks_created == 2
    assert stats.blocks_left_open == 1
    assert stats.episodes_created == 1
    open_blocks = db_session.query(ConversationBlock).filter(
        ConversationBlock.open_key.isnot(None)
    ).all()
    assert len(open_blocks) == 1
    assert open_blocks[0].block_seq == 2
    assert open_blocks[0].episode_id is None


def test_backfill_skips_group_sessions(db_session):
    base = datetime(2026, 7, 20, 10, 0, 0)
    _add_exchange(db_session, at=base, session_id="group_999", user_id="group_999")

    stats = backfill_session_blocks(db_session, now=base + GAP)

    assert stats.sessions_skipped_non_private == 1
    assert stats.blocks_created == 0
    assert db_session.query(ConversationBlock).count() == 0


def test_backfill_is_idempotent(db_session):
    base = datetime(2026, 7, 20, 10, 0, 0)
    for i in range(2):
        _add_exchange(db_session, at=base + i * GAP, text=f"话题{i}")
    now = base + 10 * GAP

    first = backfill_session_blocks(db_session, now=now)
    second = backfill_session_blocks(db_session, now=now)

    assert first.blocks_created == 2
    assert second.blocks_created == 0
    assert second.sessions_skipped_covered == 1
    assert db_session.query(ConversationBlock).count() == 2


def test_backfill_fills_prefix_before_existing_blocks(db_session):
    base = datetime(2026, 7, 20, 10, 0, 0)
    _add_exchange(db_session, at=base, text="更早历史")
    _add_exchange(db_session, at=base + GAP, text="也算早")
    # 模拟在线路径已建的块(seq=1 覆盖后来的 turn)。
    _add_exchange(db_session, at=base + 2 * GAP, text="在线块内容")
    live_first = (
        db_session.query(ConversationTurn)
        .filter(ConversationTurn.content == "在线块内容-user")
        .one()
    )
    db_session.add(ConversationBlock(
        session_id="private_u1", user_id="u1", chat_type="private",
        block_seq=1, status="open", open_key="private_u1",
        first_turn_id=live_first.id, last_turn_id=live_first.id + 1,
        started_at=base + 2 * GAP, last_turn_at=base + 2 * GAP,
        turn_count=2, token_estimate=10,
    ))
    db_session.flush()

    stats = backfill_session_blocks(db_session, now=base + 10 * GAP)

    # 只补前缀两段;编号排在现有 seq=1 之前(-1, 0),全部封口。
    assert stats.blocks_created == 2
    assert stats.blocks_left_open == 0
    prefix = (
        db_session.query(ConversationBlock)
        .filter(ConversationBlock.closed_reason == "backfill")
        .order_by(ConversationBlock.block_seq.asc())
        .all()
    )
    assert [b.block_seq for b in prefix] == [-1, 0]
    assert all(b.last_turn_id < live_first.id for b in prefix)
    # 在线 open 块不受影响。
    open_blocks = db_session.query(ConversationBlock).filter(
        ConversationBlock.open_key.isnot(None)
    ).all()
    assert len(open_blocks) == 1
    assert open_blocks[0].block_seq == 1


def test_backfill_dry_run_writes_nothing(db_session):
    base = datetime(2026, 7, 20, 10, 0, 0)
    _add_exchange(db_session, at=base, text="话题")

    stats = backfill_session_blocks(db_session, now=base + 10 * GAP, dry_run=True)

    assert stats.blocks_created == 1  # 统计有值
    assert db_session.query(ConversationBlock).count() == 0  # 但未落盘
    assert db_session.query(ConversationBlockEpisode).count() == 0
