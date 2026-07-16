"""群体记忆提取服务测试。"""

from datetime import datetime

import pytest


def _local_now() -> datetime:
    # 群体记忆窗口查询和 DB fixture 保持 naive 本地墙钟时间语义。
    return datetime.now()  # noqa: DTZ005


@pytest.mark.asyncio
async def test_extract_group_memories_reuses_group_analysis_pipeline(db_session, monkeypatch):
    from app.group_memory.extraction_service import extract_group_memories
    from core.database import ChatLog, GroupMemory
    from core.group_memory import query_active

    for idx, text in enumerate([
        "[Alice]: 今天继续压测本地模型部署方案",
        "[Bob]: 我觉得 Qwen 的上下文窗口更适合这个群",
        "[Carol]: 部署文档最好写成可复用脚本",
    ], start=1):
        db_session.add(ChatLog(
            user_id="group_4242",
            session_id="group_4242",
            session_name="测试群",
            role="ambient",
            sender_name=f"user-{idx}",
            content=text,
            message_id=f"m-{idx}",
            created_at=_local_now(),
            processed=1,
        ))
    db_session.commit()

    async def fake_analyze_group(payload, instructions):
        assert instructions == "提取稳定群体记忆"
        assert len(payload["messages"]) == 3
        assert payload["source_log_ids"]
        source_ids = payload["source_log_ids"]
        return {
            "topics": {"topics": [
                {
                    "topic": "本地模型部署",
                    "detail": "群里在讨论压测、上下文窗口和部署文档",
                    "evidence_log_ids": source_ids[:3],
                },
            ]},
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []},
            "titles": {"users": []},
        }

    monkeypatch.setattr(
        "creatures.nanobot.prompts.skills.group_analysis.analyzer.analyze_group",
        fake_analyze_group,
    )

    result = await extract_group_memories(
        db_session,
        "group_4242",
        window_hours=24,
        instructions="提取稳定群体记忆",
    )

    assert result.group_id == "group_4242"
    assert result.raw_count == 3
    assert result.eligible_count == 3
    assert result.deduped_count == 3
    assert result.stats["new"] >= 1
    assert query_active("group_4242", min_confidence=0.5)[0]["memory_type"] == "topic"
    row = db_session.query(GroupMemory).filter(GroupMemory.group_id == "group_4242").first()
    assert row.source == "manual_group_memory_extract"
