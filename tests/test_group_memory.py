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
    def test_new_write_persists_canonical_identity_and_legacy_projection(self):
        from core.database import GroupMemory, SessionLocal

        result = upsert(
            "g_identity",
            "event",
            "群里约定周五复盘",
            confidence_hint=0.40,
        )

        assert result == "new"
        db = SessionLocal()
        try:
            row = db.query(GroupMemory).one()
            assert row.chat_stream_id == "qq:g_identity:group"
            assert row.group_id == "group_g_identity"
        finally:
            db.close()

    def test_canonical_and_legacy_inputs_update_the_same_memory(self):
        from core.database import GroupMemory, SessionLocal

        assert upsert(
            "qq:g_alias:group",
            "topic",
            "群里讨论身份迁移",
            evidence_log_ids=[1],
            confidence_hint=0.70,
        ) == "new"
        assert upsert(
            "group_g_alias",
            "topic",
            "群里讨论身份迁移",
            evidence_log_ids=[2],
            confidence_hint=0.70,
        ) == "updated"

        db = SessionLocal()
        try:
            rows = db.query(GroupMemory).all()
            assert len(rows) == 1
            assert rows[0].chat_stream_id == "qq:g_alias:group"
            assert rows[0].group_id == "group_g_alias"
        finally:
            db.close()

    def test_same_external_id_on_different_platforms_does_not_collide(self):
        assert upsert(
            "shared",
            "topic",
            "跨平台共同话题",
            platform="qq",
            evidence_log_ids=[1, 2],
            confidence_hint=0.80,
        ) == "new"
        assert upsert(
            "shared",
            "topic",
            "跨平台共同话题",
            platform="web",
            evidence_log_ids=[3, 4],
            confidence_hint=0.80,
        ) == "new"

        qq_rows = query_active("shared", platform="qq")
        web_rows = query_active("shared", platform="web")

        assert [row["content"] for row in qq_rows] == ["跨平台共同话题"]
        assert [row["content"] for row in web_rows] == ["跨平台共同话题"]

        from core.database import GroupMemory, SessionLocal

        db = SessionLocal()
        try:
            projections = {
                row.chat_stream_id: row.group_id
                for row in db.query(GroupMemory).all()
            }
        finally:
            db.close()
        assert projections == {
            "qq:shared:group": "group_shared",
            "web:shared:group": "web:shared:group",
        }

    def test_conflicting_canonical_and_legacy_identity_fails_closed(self):
        from core.database import GroupMemory, SessionLocal
        from core.group_memory import GroupMemoryIdentityConflictError

        db = SessionLocal()
        db.add(GroupMemory(
            chat_stream_id="qq:g_conflict:group",
            group_id="group_other",
            memory_type="topic",
            content="冲突记录",
            content_hash="identity-conflict",
            confidence=0.8,
            evidence_count=2,
            evidence_log_ids_json="[1, 2]",
            decay_score=1.0,
            status="active",
        ))
        db.commit()
        db.close()

        with pytest.raises(
            GroupMemoryIdentityConflictError,
            match="群体记忆身份投影不一致",
        ):
            query_active("g_conflict")

    def test_high_confidence_requires_two_distinct_evidence_logs_to_activate(self):
        r = upsert(
            "g_test",
            "topic",
            "群里常聊LLM部署",
            confidence_hint=0.80,
            evidence_log_ids=[1, 1],
        )
        assert r == "new"
        assert query_active("g_test") == []

        r = upsert(
            "g_test",
            "topic",
            "群里常聊LLM部署",
            confidence_hint=0.80,
            evidence_log_ids=[2],
        )
        assert r == "updated"
        mems = query_active("g_test")
        assert len(mems) == 1
        assert mems[0]["status"] == "active"
        assert mems[0]["evidence_count"] == 2

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
    def test_legacy_upsert_topic_cannot_bypass_governance(self):
        upsert(
            "g_single_pass",
            "topic",
            "稳定话题: 群里经常讨论本地模型部署",
            confidence_hint=0.65,
            evidence_log_ids=[1, 2, 3],
        )

        memories = query_injectable("g_single_pass")

        assert memories == []

    def test_legacy_confidence_and_evidence_cannot_activate_prompt_memory(self):
        upsert("g_profile", "topic", "高置信话题", confidence_hint=0.85, evidence_log_ids=[1])
        upsert("g_profile", "topic", "高置信话题", confidence_hint=0.85, evidence_log_ids=[2])
        upsert("g_profile", "topic", "低置信话题", confidence_hint=0.40, evidence_log_ids=[3])
        upsert("g_profile", "style", "群风格", confidence_hint=0.80, evidence_log_ids=[4])
        upsert("g_profile", "style", "群风格", confidence_hint=0.80, evidence_log_ids=[5])
        profile = build_profile("g_profile")
        assert profile["common_topics"] == []
        assert profile["style"] == []

    def test_empty_group_returns_empty_profile(self):
        profile = build_profile("g_nonexistent")
        assert profile["common_topics"] == []

    def test_deprecated_profile_excludes_non_governed_relationship(self):
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
        assert profile["relationships"] == []

        context = build_group_profile_context("g_relationship")
        assert context == ""


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
