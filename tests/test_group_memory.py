"""GroupMemory 表 + 逻辑测试。"""
import pytest
from core.database import init_db
from core.group_memory import upsert, query_active, build_profile, apply_decay


@pytest.fixture(autouse=True)
def _init_db():
    from core.database import Base, engine
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


class TestUpsert:
    def test_high_confidence_writes_active(self):
        r = upsert("g_test", "topic", "群里常聊LLM部署", confidence_hint=0.80)
        assert r == "new"
        mems = query_active("g_test")
        assert len(mems) == 1
        assert mems[0]["status"] == "active"

    def test_low_confidence_writes_review(self):
        from core.database import SessionLocal, GroupMemory
        r = upsert("g_review", "event", "某人说了一句梗", confidence_hint=0.40)
        assert r == "new"
        mems = query_active("g_review")
        assert len(mems) == 0  # review 不被 query_active 取出
        # 直接查 DB 确认 status=review
        db = SessionLocal()
        row = db.query(GroupMemory).filter(
            GroupMemory.group_id == "g_review").first()
        assert row is not None
        assert row.status == "review"
        db.close()

    def test_duplicate_updates_evidence(self):
        upsert("g_dup", "topic", "测试话题", confidence_hint=0.60)
        r = upsert("g_dup", "topic", "测试话题", confidence_hint=0.60)
        assert r == "updated"
        mems = query_active("g_dup", min_confidence=0.5)
        assert mems[0]["evidence_count"] == 2
        assert mems[0]["confidence"] > 0.60

    def test_invalid_type_skipped(self):
        r = upsert("g_test", "invalid_type", "xxx", confidence_hint=0.80)
        assert r == "skipped"


class TestBuildProfile:
    def test_only_active_high_confidence(self):
        upsert("g_profile", "topic", "高置信话题", confidence_hint=0.85)
        upsert("g_profile", "topic", "低置信话题", confidence_hint=0.40)  # → review
        upsert("g_profile", "style", "群风格", confidence_hint=0.80)
        profile = build_profile("g_profile")
        assert "高置信话题" in profile["common_topics"]
        assert "低置信话题" not in profile["common_topics"]

    def test_empty_group_returns_empty_profile(self):
        profile = build_profile("g_nonexistent")
        assert profile["common_topics"] == []


class TestDecay:
    def test_decay_archives_old(self):
        upsert("g_decay", "topic", "旧话题", confidence_hint=0.70)
        for _ in range(50):
            apply_decay("g_decay")
        mems = query_active("g_decay")
        assert len(mems) == 0
