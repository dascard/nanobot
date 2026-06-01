import asyncio
import json
from datetime import datetime

from core import daily_digest
from core.database import ChatLog, MemoryDigest, ScheduledTask


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

    created = daily_digest.generate_daily_digest_for_date("2026-04-30", use_llm=False)

    assert created == 1
    rows = db_session.query(MemoryDigest).filter_by(session_id="group_123456").all()
    assert len(rows) >= 3
    assert sum(1 for row in rows if row.level == 0) == 1
    assert sum(1 for row in rows if row.level == 1) == 1
    assert sum(1 for row in rows if row.level == 2) >= 1
    summary_types = {
        json.loads(row.meta_json or "{}").get("summary_type")
        for row in rows
    }
    assert {"detailed_digest", "preview_digest", "recall_card"}.issubset(summary_types)
    assert db_session.query(MemoryDigest).filter_by(session_id="123456").count() == 0


def test_generate_daily_digest_can_filter_specific_session(db_session, monkeypatch):
    ts = datetime(2026, 5, 31, 12, 0, 0)
    db_session.add_all([
        ChatLog(
            user_id="shared_user",
            session_id="private_a",
            role="user",
            content="A session should be regenerated",
            created_at=ts,
        ),
        ChatLog(
            user_id="shared_user",
            session_id="private_b",
            role="user",
            content="B session should stay untouched",
            created_at=ts,
        ),
    ])
    db_session.commit()

    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    created = daily_digest.generate_daily_digest_for_date(
        "2026-05-31",
        user_id="shared_user",
        session_id="private_a",
        use_llm=False,
    )

    assert created == 1
    assert db_session.query(MemoryDigest).filter_by(session_id="private_a").count() >= 3
    assert db_session.query(MemoryDigest).filter_by(session_id="private_b").count() == 0


def test_qq_push_timeout_covers_html_rendering_window():
    assert daily_digest.QQBOT_PUSH_TIMEOUT >= 120


def test_build_scheduled_task_query_requires_tools_for_fresh_info():
    task = ScheduledTask(
        id=7,
        name="AI日报",
        target_type="private",
        target_id="0000000000",
        prompt_template="给我今天的AI日报",
    )

    query = daily_digest._build_scheduled_task_query(task, datetime(2026, 5, 2, 8, 0, 0))

    assert "当前时间（北京时间）：2026-05-02 08:00:00" in query
    assert "必须先调用 ai_daily" in query
    assert "news_search" not in query
    assert "调用 group_analysis" in query
    assert "保留 HTML" in query
    assert "给我今天的AI日报" in query


def test_generate_task_message_uses_kt_agent(monkeypatch):
    calls = {}

    class FakeBridge:
        async def start(self):
            calls["started"] = True

        async def stop(self):
            calls["stopped"] = True

        async def handle_message(self, query, *, user_id="", session_id="", sender_name="", metadata=None):
            calls["query"] = query
            calls["user_id"] = user_id
            calls["session_id"] = session_id
            calls["sender_name"] = sender_name
            calls["metadata"] = metadata or {}
            return "<article class=\"news-brief\"><h1>今日 AI 日报</h1></article>"

    monkeypatch.setattr("nanobot_kt.bridge.NanobotBridge", FakeBridge)
    task = ScheduledTask(
        id=7,
        name="AI日报",
        target_type="private",
        target_id="0000000000",
        prompt_template="给我今天的AI日报",
    )

    result = asyncio.run(daily_digest._generate_task_message(task))

    assert result.startswith("<article")
    assert calls["started"] is True
    assert calls["stopped"] is True
    assert calls["user_id"] == "scheduled_task:7"
    assert calls["session_id"] == "scheduled_task_7"
    assert calls["sender_name"] == "定时任务"
    assert calls["metadata"]["raw_query"] == "给我今天的AI日报"
    assert "必须先调用 ai_daily" in calls["query"]
    assert "news_search" not in calls["query"]


def test_generate_task_message_uses_group_session_for_group_target(monkeypatch):
    calls = {}

    class FakeBridge:
        async def start(self):
            pass

        async def stop(self):
            pass

        async def handle_message(self, query, *, user_id="", session_id="", sender_name="", metadata=None):
            calls["session_id"] = session_id
            calls["metadata"] = metadata or {}
            return "ok"

    monkeypatch.setattr("nanobot_kt.bridge.NanobotBridge", FakeBridge)
    task = ScheduledTask(
        id=8,
        name="群日报",
        target_type="group",
        target_id="984760873",
        prompt_template="总结这个群今天的消息",
    )

    result = asyncio.run(daily_digest._generate_task_message(task))

    assert result == "ok"
    assert calls["session_id"] == "group_984760873"
    assert calls["metadata"]["is_group"] is True
