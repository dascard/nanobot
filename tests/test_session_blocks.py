"""块式会话记忆(P1):块边界判定与 open 块状态维护单测。

见 docs/superpowers/specs/2026-07-26-block-session-memory-design.md 机制 1。
覆盖 kill-switch、群聊短路、开块/并入/封口(gap 与 size)、时钟回拨、
唯一 open 块不变式,以及两条写路径的归块集成。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.session_memory import blocks, config
from core.database import ConversationBlock, ConversationTurn


@pytest.fixture
def enable_blocks(monkeypatch):
    # 开关判定逻辑由下方 settings 门控用例单独覆盖;行为用例直接放行。
    monkeypatch.setattr(blocks, "is_block_memory_enabled", lambda session_id="": True)


def _add_turn(db, *, session_id, role, content, created_at, user_id="u1"):
    turn = ConversationTurn(
        user_id=user_id,
        session_id=session_id,
        role=role,
        content=content,
        created_at=created_at,
    )
    db.add(turn)
    db.flush()
    return turn


def _exchange(db, session_id, *, at, user_id="u1", user_content="你好呀今天怎么样", assistant_content="挺好的谢谢关心"):
    user_turn = _add_turn(
        db, session_id=session_id, role="user", content=user_content, created_at=at, user_id=user_id
    )
    assistant_turn = _add_turn(
        db, session_id=session_id, role="assistant", content=assistant_content, created_at=at, user_id=user_id
    )
    return [user_turn, assistant_turn]


def _assign(db, turns, *, session_id="private_u1", user_id="u1", chat_type="private", now=None):
    return blocks.assign_turns_to_block(
        db,
        session_id=session_id,
        user_id=user_id,
        chat_type=chat_type,
        turns=turns,
        now=now,
    )


# ── kill-switch 与短路 ──

def test_disabled_returns_none_and_creates_no_block(db_session):
    at = datetime(2026, 7, 26, 10, 0, 0)
    turns = _exchange(db_session, "private_u1", at=at)
    result = _assign(db_session, turns)
    assert result is None
    assert db_session.query(ConversationBlock).count() == 0


def test_group_chat_short_circuits(db_session, enable_blocks):
    at = datetime(2026, 7, 26, 10, 0, 0)
    turns = _exchange(db_session, "group_123", at=at, user_id="group_123")
    result = _assign(db_session, turns, session_id="group_123", user_id="group_123", chat_type="group")
    assert result is None
    assert db_session.query(ConversationBlock).count() == 0


def test_empty_turns_returns_none(db_session, enable_blocks):
    assert _assign(db_session, []) is None
    assert db_session.query(ConversationBlock).count() == 0


# ── settings 门控与灰度白名单(P5) ──

def test_allowlist_gates_sessions(monkeypatch):
    from core.settings_service import settings

    monkeypatch.setattr(settings, "get_bool", lambda key, default=False: True)
    monkeypatch.setattr(
        settings, "get_str", lambda key, default="": "private_u1, private_u9",
    )
    assert blocks.is_block_memory_enabled("private_u1") is True
    assert blocks.is_block_memory_enabled("private_u9") is True
    assert blocks.is_block_memory_enabled("private_u2") is False


def test_settings_disabled_overrides_config(monkeypatch):
    from core.settings_service import settings

    monkeypatch.setattr(config, "BLOCK_SESSION_MEMORY_ENABLED", True)
    monkeypatch.setattr(settings, "get_bool", lambda key, default=False: False)
    assert blocks.is_block_memory_enabled("private_u1") is False


def test_empty_allowlist_means_all_sessions(monkeypatch):
    from core.settings_service import settings

    monkeypatch.setattr(settings, "get_bool", lambda key, default=False: True)
    monkeypatch.setattr(settings, "get_str", lambda key, default="": "")
    assert blocks.is_block_memory_enabled("任意会话") is True


# ── 开块 ──

def test_first_exchange_opens_block(db_session, enable_blocks):
    at = datetime(2026, 7, 26, 10, 0, 0)
    turns = _exchange(db_session, "private_u1", at=at)
    block = _assign(db_session, turns, now=at)

    assert block is not None
    assert block.status == "open"
    assert block.open_key == "private_u1"
    assert block.block_seq == 1
    assert block.chat_type == "private"
    assert block.first_turn_id == turns[0].id
    assert block.last_turn_id == turns[1].id
    assert block.turn_count == 2
    assert block.token_estimate > 0
    assert block.last_turn_at == at
    assert db_session.query(ConversationBlock).count() == 1


# ── 并入当前块 ──

def test_within_gap_appends_same_block(db_session, enable_blocks):
    at1 = datetime(2026, 7, 26, 10, 0, 0)
    block1 = _assign(db_session, _exchange(db_session, "private_u1", at=at1), now=at1)

    at2 = at1 + timedelta(seconds=config.BLOCK_GAP_SECONDS - 1)
    block2 = _assign(db_session, _exchange(db_session, "private_u1", at=at2), now=at2)

    assert block2.id == block1.id
    assert block2.turn_count == 4
    assert block2.last_turn_at == at2
    assert db_session.query(ConversationBlock).count() == 1
    open_blocks = db_session.query(ConversationBlock).filter(
        ConversationBlock.open_key.isnot(None)
    ).count()
    assert open_blocks == 1


# ── gap 封口 ──

def test_gap_exceeds_seals_and_opens_new(db_session, enable_blocks):
    at1 = datetime(2026, 7, 26, 10, 0, 0)
    block1 = _assign(db_session, _exchange(db_session, "private_u1", at=at1), now=at1)

    at2 = at1 + timedelta(seconds=config.BLOCK_GAP_SECONDS + 5)
    block2 = _assign(db_session, _exchange(db_session, "private_u1", at=at2), now=at2)

    assert block2.id != block1.id
    assert block2.block_seq == 2
    assert block2.status == "open"

    sealed = db_session.get(ConversationBlock, block1.id)
    assert sealed.status == "closed"
    assert sealed.open_key is None
    assert sealed.closed_reason == "gap"
    assert sealed.closed_at is not None

    open_blocks = db_session.query(ConversationBlock).filter(
        ConversationBlock.open_key.isnot(None)
    ).all()
    assert len(open_blocks) == 1
    assert open_blocks[0].id == block2.id


def test_gap_exactly_at_threshold_seals(db_session, enable_blocks):
    at1 = datetime(2026, 7, 26, 10, 0, 0)
    block1 = _assign(db_session, _exchange(db_session, "private_u1", at=at1), now=at1)

    at2 = at1 + timedelta(seconds=config.BLOCK_GAP_SECONDS)
    block2 = _assign(db_session, _exchange(db_session, "private_u1", at=at2), now=at2)

    assert block2.id != block1.id
    assert db_session.get(ConversationBlock, block1.id).closed_reason == "gap"


# ── size 封口 ──

def test_turn_count_exceeds_seals_with_size_reason(db_session, enable_blocks, monkeypatch):
    monkeypatch.setattr(config, "BLOCK_MAX_TURNS", 2)
    at1 = datetime(2026, 7, 26, 10, 0, 0)
    block1 = _assign(db_session, _exchange(db_session, "private_u1", at=at1), now=at1)

    at2 = at1 + timedelta(seconds=10)
    block2 = _assign(db_session, _exchange(db_session, "private_u1", at=at2), now=at2)

    assert block2.id != block1.id
    assert db_session.get(ConversationBlock, block1.id).closed_reason == "size"


def test_token_estimate_exceeds_seals_with_size_reason(db_session, enable_blocks, monkeypatch):
    monkeypatch.setattr(config, "BLOCK_MAX_TOKENS", 5)
    at1 = datetime(2026, 7, 26, 10, 0, 0)
    block1 = _assign(db_session, _exchange(db_session, "private_u1", at=at1), now=at1)

    at2 = at1 + timedelta(seconds=10)
    block2 = _assign(db_session, _exchange(db_session, "private_u1", at=at2), now=at2)

    assert block2.id != block1.id
    assert db_session.get(ConversationBlock, block1.id).closed_reason == "size"


# ── 时钟回拨 ──

def test_clock_skew_stays_in_same_block(db_session, enable_blocks):
    at1 = datetime(2026, 7, 26, 10, 0, 0)
    block1 = _assign(db_session, _exchange(db_session, "private_u1", at=at1), now=at1)

    at2 = at1 - timedelta(seconds=30)  # 新消息时间早于上一条(时钟回拨)
    block2 = _assign(db_session, _exchange(db_session, "private_u1", at=at2), now=at1)

    assert block2.id == block1.id  # gap 记 0,不封口
    assert db_session.query(ConversationBlock).count() == 1


# ── 唯一 open 块不变式 ──

def test_only_one_open_block_after_many_seals(db_session, enable_blocks):
    base = datetime(2026, 7, 26, 8, 0, 0)
    for i in range(4):
        at = base + timedelta(seconds=i * (config.BLOCK_GAP_SECONDS + 60))
        _assign(db_session, _exchange(db_session, "private_u1", at=at), now=at)

    open_blocks = db_session.query(ConversationBlock).filter(
        ConversationBlock.open_key.isnot(None)
    ).all()
    assert len(open_blocks) == 1
    assert db_session.query(ConversationBlock).count() == 4
    assert open_blocks[0].block_seq == 4


def test_get_open_block(db_session, enable_blocks):
    assert blocks.get_open_block(db_session, "private_u1") is None
    at = datetime(2026, 7, 26, 10, 0, 0)
    block = _assign(db_session, _exchange(db_session, "private_u1", at=at), now=at)
    found = blocks.get_open_block(db_session, "private_u1")
    assert found is not None
    assert found.id == block.id


# ── 写路径集成 ──

def _make_req(**updates):
    from api.routes import ChatProxyRequest

    data = {
        "user_id": "u-blk",
        "session_id": "private_u-blk",
        "query": "第一个问题今天天气怎么样",
        "client_meta": {"platform": "qq", "chat_type": "private"},
    }
    data.update(updates)
    return ChatProxyRequest(**data)


def test_persist_chat_turn_creates_block_for_private(db_session, enable_blocks):
    from api import routes

    routes._persist_chat_turn(db_session, _make_req(), "第一个回答")
    rows = db_session.query(ConversationBlock).all()
    assert len(rows) == 1
    assert rows[0].status == "open"
    assert rows[0].chat_type == "private"
    assert rows[0].turn_count == 2


def test_persist_chat_turn_no_block_for_group(db_session, enable_blocks):
    from api import routes

    req = _make_req(
        user_id="group_999",
        session_id="group_999",
        client_meta={"platform": "qq", "chat_type": "group"},
    )
    routes._persist_chat_turn(db_session, req, "群回答")
    assert db_session.query(ConversationBlock).count() == 0


def test_persist_chat_turn_disabled_creates_no_block(db_session):
    from api import routes

    routes._persist_chat_turn(db_session, _make_req(), "回答")
    assert db_session.query(ConversationBlock).count() == 0
