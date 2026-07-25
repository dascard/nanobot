"""群体记忆提取服务测试。"""

from datetime import datetime

import pytest


def _local_now() -> datetime:
    # 群体记忆窗口查询和 DB fixture 保持 naive 本地墙钟时间语义。
    return datetime.now()  # noqa: DTZ005


@pytest.mark.asyncio
async def test_extract_group_memories_reuses_group_analysis_pipeline(db_session, monkeypatch):
    from app.group_memory.extraction_service import extract_group_memories
    from core.database import ChatLog
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningEvidence,
        GroupLearningRun,
        GroupMemory,
    )

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

    async def fake_analyze_group(payload, instructions, *, aspects):
        assert not db_session.in_transaction()
        assert instructions == "提取稳定群体记忆"
        assert aspects == ("topics",)
        assert len(payload["messages"]) == 3
        assert payload["source_log_ids"]
        source_ids = payload["source_log_ids"]
        return {
            "topics": {
                "_generator": "llm",
                "_task_provenance": {
                    "run_id": "task_topics_manual",
                    "contract_version": "group_analysis_topics_v1",
                    "route_key": "group_analysis_topics",
                    "provider": "test-provider",
                    "model": "test-model",
                    "attempt_count": 1,
                    "latency_ms": 20,
                    "raw_output_sha256": "b" * 64,
                    "raw_output_bytes": 96,
                    "usage": {
                        "prompt_tokens": 80,
                        "completion_tokens": 20,
                        "total_tokens": 100,
                    },
                },
                "topics": [
                    {
                        "topic": "本地模型部署",
                        "detail": "群里在讨论压测、上下文窗口和部署文档",
                        "contributors": [
                            "group_4242",
                        ],
                        "evidence_log_ids": source_ids[:3],
                    },
                ],
            },
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []},
            "titles": {"users": []},
        }

    monkeypatch.setattr(
        "app.group_analysis.analyzer.analyze_group",
        fake_analyze_group,
    )
    monkeypatch.setattr(
        "app.group_memory.extraction_service.settings.get_bool",
        lambda key, default=False: key == "group_learning.enabled",
    )
    monkeypatch.setattr(
        "app.group_analysis.memory_candidates.extract_and_persist",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Web 提取不得调用旧候选写入入口")
        ),
    )

    result = await extract_group_memories(
        db_session,
        "group_4242",
        window_hours=24,
        instructions="提取稳定群体记忆",
        aspects=("topics",),
    )

    assert result.group_id == "group_4242"
    assert result.raw_count == 3
    assert result.eligible_count == 3
    assert result.deduped_count == 3
    assert result.stats["new"] >= 1
    assert result.memory_count == 1
    assert result.active_count == 1
    assert result.injectable_count == 1
    candidate = db_session.query(GroupLearningCandidate).one()
    assert candidate.candidate_type == "topic"
    assert candidate.status == "accepted"
    assert candidate.source == "model"
    assert db_session.query(GroupLearningEvidence).count() == 3
    assert db_session.query(GroupMemory).count() == 1
    run = db_session.query(GroupLearningRun).one()
    assert run.trigger == "manual"
    assert run.mode == "active"
    assert run.status == "succeeded"


@pytest.mark.asyncio
async def test_extract_group_memories_kill_switch_stops_before_model(
    db_session,
    monkeypatch,
):
    from app.group_memory.extraction_service import (
        GroupMemoryLearningDisabled,
        extract_group_memories,
    )
    from core.database import ChatLog

    for idx in range(1, 4):
        db_session.add(ChatLog(
            user_id="group_5252",
            session_id="group_5252",
            session_name="关闭测试群",
            role="ambient",
            sender_name=f"user-{idx}",
            content=f"第 {idx} 条可分析消息",
            message_id=f"disabled-{idx}",
            created_at=_local_now(),
            processed=1,
        ))
    db_session.commit()
    monkeypatch.setattr(
        "app.group_memory.extraction_service.settings.get_bool",
        lambda _key, default=False: False,
    )

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("kill switch 关闭时不得调用模型")

    monkeypatch.setattr(
        "app.group_analysis.analyzer.analyze_group",
        fail_if_called,
    )

    with pytest.raises(GroupMemoryLearningDisabled):
        await extract_group_memories(
            db_session,
            "group_5252",
            aspects=("topics",),
        )
