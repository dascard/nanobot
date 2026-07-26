"""块式会话记忆(P2):系统 A 滚动摘要按 open 块收窄。

见 docs/superpowers/specs/2026-07-26-block-session-memory-design.md 机制 3(系统 A)。
验证 get_best_session_summary / save_new_active_summary / archive_active_summaries_for_session
的 block_id 收窄语义,以及 block_id=None 保持旧的 session 级行为。
"""

from __future__ import annotations

from datetime import datetime

from app.session_memory.rolling_summary import (
    archive_active_summaries_for_session,
    get_best_session_summary,
    save_new_active_summary,
)
from core.database import ConversationTurn, RollingSessionSummary


def _summary(db, *, session_id="private_u1", block_id=None, covered_until=10,
             kind="deterministic_fallback", status="active"):
    row = RollingSessionSummary(
        session_id=session_id,
        user_id="u1",
        chat_type="private",
        status=status,
        summary_kind=kind,
        summary_text="旧摘要",
        covered_until_turn_id=covered_until,
        block_id=block_id,
        updated_at=datetime(2026, 7, 26, 10, 0, 0),
    )
    db.add(row)
    db.flush()
    return row


def _turns(db, ids, *, session_id="private_u1"):
    rows = []
    for tid in ids:
        row = ConversationTurn(
            id=tid,
            user_id="u1",
            session_id=session_id,
            role="user" if tid % 2 else "assistant",
            content=f"这是第{tid}条消息内容用于测试摘要",
            created_at=datetime(2026, 7, 26, 10, 0, tid % 60),
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


# ── get_best_session_summary block_id 过滤 ──

def test_get_best_filters_by_block_id(db_session):
    s1 = _summary(db_session, block_id=1, covered_until=5)
    s2 = _summary(db_session, block_id=2, covered_until=10)

    assert get_best_session_summary(db_session, "private_u1", block_id=1).id == s1.id
    assert get_best_session_summary(db_session, "private_u1", block_id=2).id == s2.id


def test_get_best_block_id_none_is_session_wide(db_session):
    _summary(db_session, block_id=1, covered_until=5)
    s2 = _summary(db_session, block_id=2, covered_until=10)

    # None => 旧行为:全 session 内 covered_until 最大者。
    assert get_best_session_summary(db_session, "private_u1").id == s2.id


def test_get_best_block_id_with_no_match_returns_none(db_session):
    _summary(db_session, block_id=1, covered_until=5)
    assert get_best_session_summary(db_session, "private_u1", block_id=99) is None


# ── archive_active_summaries_for_session block_id 收窄 ──

def test_archive_scoped_to_block(db_session):
    s1 = _summary(db_session, block_id=1, covered_until=5)
    s2 = _summary(db_session, block_id=2, covered_until=10)

    archived = archive_active_summaries_for_session(db_session, "private_u1", block_id=1)
    assert archived == 1

    db_session.refresh(s1)
    db_session.refresh(s2)
    assert s1.status == "archived"
    assert s2.status == "active"  # 其他块不受影响


def test_archive_all_when_block_id_none(db_session):
    s1 = _summary(db_session, block_id=1, covered_until=5)
    s2 = _summary(db_session, block_id=2, covered_until=10)

    archived = archive_active_summaries_for_session(db_session, "private_u1")
    assert archived == 2
    db_session.refresh(s1)
    db_session.refresh(s2)
    assert s1.status == "archived"
    assert s2.status == "archived"


# ── save_new_active_summary block_id 标记与同块归档 ──

def test_save_new_active_summary_tags_block_and_isolates_other_block(db_session):
    other_block = _summary(db_session, block_id=1, covered_until=3)
    turns = _turns(db_session, [10, 11])

    new_summary = save_new_active_summary(
        db_session,
        old_summary=None,
        session_id="private_u1",
        user_id="u1",
        chat_type="private",
        summary_json={"summary": "第二块的滚动摘要内容"},
        pending_turns=turns,
        raw_window_start_turn_id=12,
        model="deterministic",
        prompt_sha256="hash2",
        block_id=2,
    )

    assert new_summary.block_id == 2
    assert new_summary.status == "active"
    # 另一个块的 active 摘要不应被归档。
    db_session.refresh(other_block)
    assert other_block.status == "active"


def test_save_new_active_summary_archives_same_block_prior(db_session):
    prior = _summary(db_session, block_id=2, covered_until=8)
    turns = _turns(db_session, [10, 11])

    new_summary = save_new_active_summary(
        db_session,
        old_summary=prior,
        session_id="private_u1",
        user_id="u1",
        chat_type="private",
        summary_json={"summary": "同块新的滚动摘要"},
        pending_turns=turns,
        raw_window_start_turn_id=12,
        model="deterministic",
        prompt_sha256="hash3",
        block_id=2,
    )

    assert new_summary.block_id == 2
    db_session.refresh(prior)
    assert prior.status == "archived"  # 同块旧摘要被归档
    # 该块最佳仍是新摘要。
    best = get_best_session_summary(db_session, "private_u1", block_id=2)
    assert best.id == new_summary.id
