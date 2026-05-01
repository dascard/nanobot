from datetime import datetime

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


def test_qq_push_timeout_covers_html_rendering_window():
    assert daily_digest.QQBOT_PUSH_TIMEOUT >= 120
