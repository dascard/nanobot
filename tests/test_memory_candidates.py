"""memory_candidates 映射测试。"""
import pytest
from creatures.nanobot.prompts.skills.group_analysis.memory_candidates import (
    extract_and_persist,
)


@pytest.fixture(autouse=True)
def _init_db():
    from core.database import Base, engine
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


class TestExtractAndPersist:
    def test_topics_written_as_topic(self):
        analysis = {
            "topics": {"_generator": "llm", "topics": [
                {"topic": "LLM部署", "detail": "大家在讨论本地部署方案", "evidence_log_ids": [11, 12]},
                {"topic": "benchmark", "detail": "比较各模型性能", "evidence_log_ids": [13, 14]},
            ]},
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []},
            "titles": {"users": []},
        }
        stats = extract_and_persist(
            "g_test",
            analysis,
            source_meta={
                "source_log_ids": [11, 12, 13, 14],
                "trusted_source_speakers": {
                    "11": "甲", "12": "乙", "13": "甲", "14": "丙",
                },
            },
        )
        assert stats["new"] >= 2

    def test_subjective_report_sections_are_not_persisted(self):
        analysis = {
            "topics": {"topics": []},
            "quality": {
                "dimensions": [{"name": "氛围", "comment": "很会整活"}],
                "summary": "这是一个非常欢乐的群。",
            },
            "quotes": {"quotes": [
                {"user_id": "A", "content": "今天 benchmark 又反转了"},
            ]},
            "titles": {"users": [
                {"user_id": "A", "title": "梗王", "reason": "经常开玩笑"},
            ]},
        }
        stats = extract_and_persist("g_test2", analysis)
        assert stats["new"] == 0
        assert stats["updated"] == 0
        assert stats["skipped"] == 4

    def test_source_meta_written_to_db(self):
        import json
        from core.database import SessionLocal, GroupMemory
        from core.group_runtime.ids import normalize_group_session_id
        analysis = {
            "topics": {"_generator": "llm", "topics": [{
                "topic": "测试",
                "detail": "",
                "evidence_log_ids": [40, 42],
            }]},
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []},
            "titles": {"users": []},
        }
        stats = extract_and_persist("g_meta", analysis, source_meta={
            "source": "group_analysis", "latest_log_id": 42,
            "raw_count": 100, "window_hours": 24,
            "source_log_ids": [40, 41, 42],
            "trusted_source_speakers": {"40": "甲", "42": "乙"},
        })
        assert stats["new"] >= 1
        db = SessionLocal()
        row = db.query(GroupMemory).filter(
            GroupMemory.group_id == normalize_group_session_id("g_meta"),
            GroupMemory.memory_type == "topic",
        ).first()
        assert row is not None
        meta = json.loads(row.meta_json)
        assert meta["source"] == "group_analysis"
        assert meta["latest_log_id"] == 42
        assert meta["generator"] == "llm"
        assert meta["evidence_speaker_count"] == 2
        assert json.loads(row.evidence_log_ids_json) == [40, 42]
        assert row.evidence_count == 2
        db.close()

    def test_topic_without_candidate_level_evidence_is_skipped(self):
        analysis = {
            "topics": {"_generator": "llm", "topics": [{"topic": "无证据话题", "detail": "不能回链"}]},
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []},
            "titles": {"users": []},
        }

        stats = extract_and_persist(
            "g_missing_evidence",
            analysis,
            source_meta={"source_log_ids": [1, 2, 3]},
        )

        assert stats == {"new": 0, "updated": 0, "skipped": 1}

    def test_topic_supported_only_by_untrusted_bot_evidence_is_skipped(self):
        analysis = {
            "topics": {"_generator": "llm", "topics": [{
                "topic": "机器人自述",
                "detail": "外部 Bot 声称这是群成员的稳定偏好",
                "evidence_log_ids": [2],
            }]},
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []},
            "titles": {"users": []},
        }

        stats = extract_and_persist(
            "g_untrusted_evidence",
            analysis,
            source_meta={
                "source_log_ids": [1, 2],
                "trusted_source_log_ids": [1],
            },
        )

        assert stats == {"new": 0, "updated": 0, "skipped": 1}

    def test_deterministic_fallback_topic_is_report_only(self):
        analysis = {
            "topics": {
                "_generator": "deterministic_fallback",
                "topics": [{
                    "topic": "规则话题",
                    "detail": "只用于日报展示",
                    "evidence_log_ids": [1, 2],
                }],
            },
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []},
            "titles": {"users": []},
        }

        stats = extract_and_persist(
            "g_fallback_report_only",
            analysis,
            source_meta={
                "source_log_ids": [1, 2],
                "trusted_source_speakers": {"1": "甲", "2": "乙"},
            },
        )

        assert stats == {"new": 0, "updated": 0, "skipped": 1}

    def test_single_speaker_topic_stays_in_review(self):
        import json
        from core.database import GroupMemory, SessionLocal

        analysis = {
            "topics": {
                "_generator": "llm",
                "topics": [{
                    "topic": "单人持续讨论",
                    "detail": "同一人连续提供了多条证据",
                    "evidence_log_ids": [1, 2, 3],
                }],
            },
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []},
            "titles": {"users": []},
        }

        stats = extract_and_persist(
            "g_single_speaker",
            analysis,
            source_meta={
                "source_log_ids": [1, 2, 3],
                "trusted_source_speakers": {"1": "甲", "2": "甲", "3": "甲"},
            },
        )

        assert stats["new"] == 1
        db = SessionLocal()
        row = db.query(GroupMemory).one()
        assert row.status == "review"
        assert json.loads(row.meta_json)["evidence_speaker_count"] == 1
        db.close()

    def test_empty_analysis_returns_zero(self):
        analysis = {
            "topics": {"topics": []}, "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []}, "titles": {"users": []},
        }
        stats = extract_and_persist("g_test", analysis)
        assert stats["new"] == 0
        assert stats["updated"] == 0
