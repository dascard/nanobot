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
                {"topic": "LLM部署", "detail": "大家在讨论本地部署方案"},
                {"topic": "benchmark", "detail": "比较各模型性能"},
            ]},
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []},
            "titles": {"users": []},
        }
        stats = extract_and_persist("g_test", analysis)
        assert stats["new"] >= 2

    def test_quotes_low_confidence_goes_review(self):
        analysis = {
            "topics": {"topics": []},
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": [
                {"user_id": "A", "content": "今天 benchmark 又反转了"},
            ]},
            "titles": {"users": []},
        }
        stats = extract_and_persist("g_test2", analysis)
        assert stats["new"] == 1
        assert stats["updated"] == 0
        # quotes confidence_hint=0.50 < CONFIDENCE_FLOOR → status=review, 不active
        from core.group_memory import query_active
        mems = query_active("g_test2")
        assert len(mems) == 0  # review 不进入 active

    def test_source_meta_written_to_db(self):
        import json
        from core.database import SessionLocal, GroupMemory
        from core.group_runtime.ids import normalize_group_session_id
        analysis = {
            "topics": {"topics": [{"topic": "测试", "detail": ""}]},
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []},
            "titles": {"users": []},
        }
        stats = extract_and_persist("g_meta", analysis, source_meta={
            "source": "group_analysis", "latest_log_id": 42,
            "raw_count": 100, "window_hours": 24,
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
        db.close()

    def test_empty_analysis_returns_zero(self):
        analysis = {
            "topics": {"topics": []}, "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []}, "titles": {"users": []},
        }
        stats = extract_and_persist("g_test", analysis)
        assert stats["new"] == 0
        assert stats["updated"] == 0
