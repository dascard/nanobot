"""GroupMemory 表 + 逻辑测试。"""
import logging

import pytest
from core.group_memory import upsert, query_active, query_injectable, build_profile, apply_decay
from core.context_builder import build_group_profile_context, build_group_recent_context


def test_legacy_context_module_exports_group_context_builders():
    from core import context_legacy

    assert context_legacy.build_group_recent_context is not build_group_recent_context
    assert context_legacy.build_group_profile_context is not build_group_profile_context
    assert callable(context_legacy.build_group_recent_context)
    assert callable(context_legacy.build_group_profile_context)


def test_deprecated_group_profile_context_logs_build_failure(monkeypatch, caplog):
    from core.context_legacy import build_group_profile_context

    def broken_build_profile_with_evidence(*_args, **_kwargs):
        raise RuntimeError("profile boom")

    monkeypatch.setattr(
        "core.group_memory.build_profile_with_evidence",
        broken_build_profile_with_evidence,
    )

    with caplog.at_level(logging.DEBUG, logger="nanobot.context_legacy"):
        context = build_group_profile_context("g_fail")

    assert context == ""
    assert "g_fail" in caplog.text
    assert "profile boom" in caplog.text


@pytest.fixture(autouse=True)
def _init_db():
    from core.database import Base, engine
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


class TestUpsert:
    def test_high_confidence_writes_active(self):
        r = upsert("g_test", "topic", "群里常聊LLM部署", confidence_hint=0.80, evidence_log_ids=[1])
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
        db = SessionLocal()
        row = db.query(GroupMemory).filter(
            GroupMemory.group_id == "group_g_review").first()
        assert row is not None
        assert row.status == "review"
        db.close()

    def test_preference_requires_stronger_evidence_for_auto_injection(self):
        from core.database import SessionLocal, GroupMemory

        upsert("g_pref", "preference", "群里希望回答直接一点", confidence_hint=0.74, evidence_log_ids=[1, 2])
        upsert("g_pref", "preference", "群里偏好先给结论", confidence_hint=0.80, evidence_log_ids=[3, 4])

        db = SessionLocal()
        rows = {
            row.content: row
            for row in db.query(GroupMemory).filter(GroupMemory.group_id == "group_g_pref").all()
        }
        assert rows["群里希望回答直接一点"].status == "review"
        assert rows["群里希望回答直接一点"].inject_policy == "auto"
        assert rows["群里偏好先给结论"].status == "active"
        assert rows["群里偏好先给结论"].inject_policy == "auto"
        db.close()

    def test_duplicate_updates_evidence(self):
        upsert("g_dup", "topic", "测试话题", confidence_hint=0.70, evidence_log_ids=[1])
        r = upsert("g_dup", "topic", "测试话题", confidence_hint=0.70, evidence_log_ids=[2])
        assert r == "updated"
        mems = query_active("g_dup", min_confidence=0.5)
        assert mems[0]["evidence_count"] == 2
        assert mems[0]["confidence"] > 0.70

    def test_invalid_type_skipped(self):
        r = upsert("g_test", "invalid_type", "xxx", confidence_hint=0.80)
        assert r == "skipped"


class TestBuildProfile:
    def test_single_group_analysis_topic_with_evidence_is_injectable(self):
        upsert(
            "g_single_pass",
            "topic",
            "稳定话题: 群里经常讨论本地模型部署",
            confidence_hint=0.65,
            evidence_log_ids=[1, 2, 3],
        )

        memories = query_injectable("g_single_pass")

        assert any(m["content"].startswith("稳定话题") for m in memories)

    def test_only_active_high_confidence(self):
        # topic needs ≥2 evidence, call twice
        upsert("g_profile", "topic", "高置信话题", confidence_hint=0.85, evidence_log_ids=[1])
        upsert("g_profile", "topic", "高置信话题", confidence_hint=0.85, evidence_log_ids=[2])
        upsert("g_profile", "topic", "低置信话题", confidence_hint=0.40, evidence_log_ids=[3])
        upsert("g_profile", "style", "群风格", confidence_hint=0.80, evidence_log_ids=[4])
        upsert("g_profile", "style", "群风格", confidence_hint=0.80, evidence_log_ids=[5])
        profile = build_profile("g_profile")
        assert "高置信话题" in profile["common_topics"]
        assert "低置信话题" not in profile["common_topics"]

    def test_empty_group_returns_empty_profile(self):
        profile = build_profile("g_nonexistent")
        assert profile["common_topics"] == []

    def test_profile_includes_relationships_in_context(self):
        from core.context_builder import GROUP_PROFILE_CONTEXT_DEPRECATED
        from core.database import GroupMemory, SessionLocal

        assert GROUP_PROFILE_CONTEXT_DEPRECATED is True

        db = SessionLocal()
        db.add(GroupMemory(
            group_id="group_g_relationship",
            memory_type="relationship",
            content="A 经常和 B 一起讨论模型部署",
            content_hash="relationship-context",
            confidence=0.85,
            evidence_count=2,
            evidence_log_ids_json="[1, 2]",
            decay_score=1.0,
            status="active",
            inject_policy="auto",
        ))
        db.commit()
        db.close()
        profile = build_profile("g_relationship")
        assert "A 经常和 B 一起讨论模型部署" in profile["relationships"]

        context = build_group_profile_context("g_relationship")
        assert context.startswith('<group_memory_context group_id="g_relationship">')
        assert "群内关系" in context
        assert "A 经常和 B 一起讨论模型部署" in context


class TestDecay:
    def test_decay_archives_old(self):
        upsert("g_decay", "topic", "旧话题", confidence_hint=0.70, evidence_log_ids=[1])
        for _ in range(50):
            apply_decay("g_decay")
        mems = query_active("g_decay")
        assert len(mems) == 0


class TestGroupRecentContext:
    def test_recent_context_uses_maibot_message_prefix(self):
        from core.database import SessionLocal, ChatLog

        db = SessionLocal()
        db.add(ChatLog(
            user_id="group_recent",
            session_id="group_recent",
            role="ambient",
            sender_name="A",
            content="[A]: 这个方案有点绕",
            message_id="m1",
            processed=1,
        ))
        db.commit()

        context = build_group_recent_context(db, "group_recent")
        db.close()

        assert context.startswith("<group_recent_context>")
        assert "[msg_id]m1" in context
        assert "[用户名]A" in context
        assert "[发言内容]这个方案有点绕" in context
        assert context.endswith("</group_recent_context>")
