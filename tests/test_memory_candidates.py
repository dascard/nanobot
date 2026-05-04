"""memory_candidates 映射测试。"""
import pytest
from core.database import init_db
from creatures.nanobot.prompts.skills.group_analysis.memory_candidates import (
    extract_and_persist,
)


@pytest.fixture(autouse=True)
def _init_db():
    init_db()


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
        # quotes confidence_hint=0.50 < CONFIDENCE_FLOOR → status=review, 不active
        from core.group_memory import query_active
        mems = query_active("g_test2")
        assert len(mems) == 0  # review 不进入 active

    def test_source_meta_passed(self):
        analysis = {
            "topics": {"topics": [{"topic": "测试", "detail": ""}]},
            "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []},
            "titles": {"users": []},
        }
        stats = extract_and_persist("g_test", analysis, source_meta={
            "source": "group_analysis", "latest_log_id": 42,
            "raw_count": 100, "window_hours": 24,
        })
        assert stats["new"] >= 1

    def test_empty_analysis_returns_zero(self):
        analysis = {
            "topics": {"topics": []}, "quality": {"dimensions": [], "summary": ""},
            "quotes": {"quotes": []}, "titles": {"users": []},
        }
        stats = extract_and_persist("g_test", analysis)
        assert stats["new"] == 0
        assert stats["updated"] == 0
