import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

from core import daily_digest
from core.database import ChatLog, MemoryDigest


def test_generate_daily_digest_merges_legacy_group_session_ids(db_session, monkeypatch):
    ts = datetime(2026, 4, 30, 12, 0, 0)
    db_session.add_all(
        [
            ChatLog(
                user_id="group_123456",
                session_id="123456",
                role="ambient",
                sender_name="甲",
                content="[甲]: 环境消息",
                created_at=ts,
            ),
            ChatLog(
                user_id="group_123456",
                session_id="group_123456",
                role="user",
                sender_name="甲",
                content="正式提问",
                created_at=ts,
            ),
        ]
    )
    db_session.commit()

    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    created = daily_digest.generate_daily_digest_for_date("2026-04-30")

    assert created == 1
    assert db_session.query(MemoryDigest).filter_by(session_id="group_123456").count() == 3
    assert db_session.query(MemoryDigest).filter_by(session_id="123456").count() == 0


def test_run_group_analysis_scheduled_normalizes_group_ids_and_pushes_raw_target(db_session, monkeypatch):
    ts = datetime(2026, 4, 30, 12, 0, 0)
    db_session.add_all(
        [
            ChatLog(
                user_id="group_654321",
                session_id="654321",
                role="ambient",
                sender_name="乙",
                content="[乙]: 环境消息",
                created_at=ts,
            ),
            ChatLog(
                user_id="group_654321",
                session_id="group_654321",
                role="user",
                sender_name="乙",
                content="正式提问",
                created_at=ts,
            ),
        ]
    )
    db_session.commit()

    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(daily_digest, "_run_single_group_analysis", AsyncMock(return_value="群日报"))
    monkeypatch.setattr(daily_digest, "push_to_qq", AsyncMock(return_value=True))
    monkeypatch.setattr(daily_digest.asyncio, "sleep", AsyncMock(return_value=None))

    executed = asyncio.run(daily_digest.run_group_analysis_scheduled())

    assert executed == 1
    daily_digest._run_single_group_analysis.assert_awaited_once_with("group_654321")
    daily_digest.push_to_qq.assert_awaited_once_with("group", "654321", "群日报")
