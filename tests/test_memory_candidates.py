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
            "topics": {"topics": [
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
            source_meta={"source_log_ids": [11, 12, 13, 14]},
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
            "topics": {"topics": [{
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
        assert json.loads(row.evidence_log_ids_json) == [40, 42]
        assert row.evidence_count == 2
        db.close()

    def test_topic_without_candidate_level_evidence_is_skipped(self):
        analysis = {
            "topics": {"topics": [{"topic": "无证据话题", "detail": "不能回链"}]},
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

    def test_empty_analysis_returns_zero(self):
        analysis = {
            "topics": {"topics": []}, "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []}, "titles": {"users": []},
        }
        stats = extract_and_persist("g_test", analysis)
        assert stats["new"] == 0
        assert stats["updated"] == 0
