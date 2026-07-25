"""阶段 7D：旧 Expression/Jargon 数据与兼容入口退役测试。"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DatabaseError
from sqlalchemy.pool import StaticPool


CHAT_STREAM_ID = "qq:42:group"


def _now() -> datetime:
    return datetime(2026, 7, 24, 12, 0, 0)


def test_stage7d_migrates_legacy_rows_without_trusting_checked(
    db_session,
):
    from app.group_learning.legacy_migration_service import (
        build_group_learning_legacy_migration_service,
    )
    from core.db.models import (
        AdminAuditLog,
        ExpressionMemory,
        GroupLearningCandidate,
        GroupLearningRun,
        GroupMemory,
        JargonMemory,
    )
    from core.group_learning.legacy_migration import legacy_content_hash

    unproven = ExpressionMemory(
        chat_stream_id=CHAT_STREAM_ID,
        expression="未经证明的旧表达",
        checked=1,
        status="active",
        created_at=_now(),
        last_seen=_now(),
    )
    proven = ExpressionMemory(
        chat_stream_id=CHAT_STREAM_ID,
        expression="人工确认的旧表达",
        checked=1,
        status="active",
        created_at=_now(),
        last_seen=_now(),
    )
    jargon = JargonMemory(
        chat_stream_id=CHAT_STREAM_ID,
        term="摸鱼",
        meaning="上班时偷懒",
        checked=1,
        status="active",
        created_at=_now(),
        last_seen=_now(),
    )
    legacy_memory = GroupMemory(
        chat_stream_id=CHAT_STREAM_ID,
        group_id="group_42",
        memory_type="topic",
        content="缺少新版审核凭据的旧话题",
        content_hash="legacy-topic",
        status="active",
        inject_policy="auto",
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add_all([unproven, proven, jargon, legacy_memory])
    db_session.flush()
    db_session.add(AdminAuditLog(
        admin_user="reviewer-1",
        action="legacy_expression.accept",
        target_type="expression_memory",
        target_id=str(proven.id),
        detail_json=json.dumps({
            "schema_version": 1,
            "chat_stream_id": CHAT_STREAM_ID,
            "content_hash": legacy_content_hash(
                proven.expression,
                "",
            ),
        }),
        created_at=_now(),
    ))
    db_session.commit()

    service = build_group_learning_legacy_migration_service(db_session)
    audit = service.audit()

    assert audit.source_count == 4
    assert audit.planned_count == 4
    assert audit.checked_without_human_proof_count == 2

    result = service.apply(
        expected_source_sha256=audit.source_sha256,
        expected_planned_sha256=audit.planned_sha256,
        actor="pytest",
    )

    assert result.source_count == 4
    assert result.migrated_count == 4
    assert result.human_promoted_count == 1
    assert result.legacy_group_memory_downgraded_count == 1
    assert result.replayed_count == 0

    candidates = {
        (row.source, row.content): row
        for row in db_session.query(GroupLearningCandidate).all()
    }
    unchecked_candidate = candidates[
        ("legacy_expression", "未经证明的旧表达")
    ]
    proven_candidate = candidates[
        ("legacy_expression", "人工确认的旧表达")
    ]
    jargon_candidate = candidates[("legacy_jargon", "摸鱼")]
    topic_candidate = candidates[
        ("legacy_group_memory", "缺少新版审核凭据的旧话题")
    ]

    assert unchecked_candidate.status == "pending_model_review"
    assert unchecked_candidate.approval_source is None
    assert jargon_candidate.status == "pending_model_review"
    assert jargon_candidate.approval_source is None
    assert topic_candidate.status == "pending_model_review"
    assert topic_candidate.approval_source is None

    assert proven_candidate.status == "accepted"
    assert proven_candidate.approval_source == "human"
    assert proven_candidate.human_reviewer_id == "reviewer-1"
    assert proven_candidate.human_reviewed_at == _now()
    assert proven_candidate.human_action == "legacy_accept"
    assert proven_candidate.promoted_group_memory_id is not None

    promoted = db_session.get(
        GroupMemory,
        proven_candidate.promoted_group_memory_id,
    )
    assert promoted is not None
    assert promoted.memory_type == "expression"
    assert promoted.status == "active"
    assert promoted.governance_mode == "human_managed"
    assert promoted.approval_source == "human"
    assert promoted.human_reviewer_id == "reviewer-1"
    assert promoted.source == "legacy_group_learning_migration"

    db_session.refresh(legacy_memory)
    assert legacy_memory.status == "review"
    assert legacy_memory.inject_policy == "manual_only"
    assert db_session.query(GroupLearningRun).count() == 1

    replay = service.apply(
        expected_source_sha256=audit.source_sha256,
        expected_planned_sha256=audit.planned_sha256,
        actor="pytest",
    )
    assert replay.migrated_count == 0
    assert replay.replayed_count == 4
    assert db_session.query(GroupLearningCandidate).count() == 4
    assert db_session.query(GroupLearningRun).count() == 1


def test_stage7d_migration_rejects_audit_hash_drift(db_session):
    from app.group_learning.legacy_migration_service import (
        GroupLearningLegacyMigrationMismatch,
        build_group_learning_legacy_migration_service,
    )
    from core.db.models import ExpressionMemory, GroupLearningCandidate

    db_session.add(ExpressionMemory(
        chat_stream_id=CHAT_STREAM_ID,
        expression="待迁移表达",
        checked=0,
        status="candidate",
    ))
    db_session.commit()
    service = build_group_learning_legacy_migration_service(db_session)
    audit = service.audit()

    with pytest.raises(
        GroupLearningLegacyMigrationMismatch,
        match="审计摘要",
    ):
        service.apply(
            expected_source_sha256="0" * 64,
            expected_planned_sha256=audit.planned_sha256,
            actor="pytest",
        )

    assert db_session.query(GroupLearningCandidate).count() == 0


def test_stage7d_schema_migration_makes_legacy_tables_read_only():
    from core.database import Base
    from core.schema_migrations import run_schema_migrations

    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO expression_memories"
            "(chat_stream_id, expression, checked, status) "
            "VALUES ('qq:42:group', '旧表达', 1, 'active')"
        )
        connection.exec_driver_sql(
            "INSERT INTO jargon_memories"
            "(chat_stream_id, term, meaning, checked, status) "
            "VALUES ('qq:42:group', '摸鱼', '上班时偷懒', 1, 'active')"
        )

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    with engine.connect() as connection:
        expression_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM expression_memories"
        ).scalar_one()
        jargon_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM jargon_memories"
        ).scalar_one()
        migration_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = "
            "'20260724_group_learning_stage7d_legacy_read_only'"
        ).scalar_one()
    assert expression_count == 1
    assert jargon_count == 1
    assert migration_count == 1

    statements = (
        "INSERT INTO expression_memories"
        "(chat_stream_id, expression) VALUES ('qq:42:group', '新表达')",
        "UPDATE expression_memories SET status = 'candidate' WHERE id = 1",
        "DELETE FROM expression_memories WHERE id = 1",
        "INSERT INTO jargon_memories"
        "(chat_stream_id, term) VALUES ('qq:42:group', '新黑话')",
        "UPDATE jargon_memories SET status = 'candidate' WHERE id = 1",
        "DELETE FROM jargon_memories WHERE id = 1",
    )
    for statement in statements:
        with pytest.raises(DatabaseError, match="read_only"):
            with engine.begin() as connection:
                connection.exec_driver_sql(statement)


def test_stage7d_file_migration_snapshots_before_installing_read_only_triggers(
    tmp_path,
):
    from core.database import Base
    from core.schema_migrations import MIGRATIONS, run_schema_migrations

    database_path = tmp_path / "legacy-stage7d.db"
    target_version = (
        "20260724_group_learning_stage7d_legacy_read_only"
    )
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "applied_at DATETIME NOT NULL"
            ")"
        )
        for version, name, _migration in MIGRATIONS:
            if version == target_version:
                continue
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations"
                "(version, name, applied_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (version, name),
            )

    run_schema_migrations(engine, db_path=str(database_path))

    backups = tuple(tmp_path.glob("legacy-stage7d.db.bak.*"))
    assert len(backups) == 1
    assert backups[0].stat().st_size > 0
    with engine.connect() as connection:
        trigger_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'trigger' "
            "AND name LIKE 'trg_group_learning_legacy_%_read_only'"
        ).scalar_one()
    assert trigger_count == 6


def test_stage7d_legacy_writers_are_rejecting_tombstones(monkeypatch):
    from core import database
    from core.expression_memory import (
        LegacyGroupLearningWriteRetired,
        mark_expression_checked,
        mark_jargon_checked,
        upsert_expression,
        upsert_jargon,
    )
    from core.lifecycle import get_compatibility_usage_snapshot

    monkeypatch.setattr(
        database,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(
            AssertionError("退役 Writer 不得打开数据库")
        ),
    )
    calls = (
        lambda: upsert_expression(CHAT_STREAM_ID, "旧表达"),
        lambda: upsert_jargon(CHAT_STREAM_ID, "旧词"),
        lambda: mark_expression_checked(
            CHAT_STREAM_ID,
            "旧表达",
        ),
        lambda: mark_jargon_checked(CHAT_STREAM_ID, "旧词"),
    )
    for call in calls:
        with pytest.raises(LegacyGroupLearningWriteRetired):
            call()

    usage = get_compatibility_usage_snapshot()
    assert usage["schema.legacy_expression_memory_write"].count >= 2
    assert usage["schema.legacy_jargon_memory_write"].count >= 2


def test_stage7d_legacy_memory_candidate_writer_is_rejecting_tombstone():
    from app.group_analysis.memory_candidates import (
        LegacyMemoryCandidateWriterRetired,
        extract_and_persist,
    )
    from core.lifecycle import get_compatibility_usage_snapshot

    before = get_compatibility_usage_snapshot()
    previous_count = (
        before.get(
            "schema.legacy_group_analysis_memory_candidate_write"
        ).count
        if before.get(
            "schema.legacy_group_analysis_memory_candidate_write"
        )
        else 0
    )

    with pytest.raises(LegacyMemoryCandidateWriterRetired):
        extract_and_persist(
            "group_42",
            {"topics": {"topics": []}},
        )

    after = get_compatibility_usage_snapshot()
    assert (
        after[
            "schema.legacy_group_analysis_memory_candidate_write"
        ].count
        == previous_count + 1
    )


def test_stage7d_legacy_readers_record_compatibility_before_database_access(
    monkeypatch,
):
    from core import expression_memory
    from core.lifecycle import get_compatibility_usage_snapshot

    before = get_compatibility_usage_snapshot()
    expression_before = (
        before.get("schema.legacy_expression_memory_read").count
        if before.get("schema.legacy_expression_memory_read")
        else 0
    )
    jargon_before = (
        before.get("schema.legacy_jargon_memory_read").count
        if before.get("schema.legacy_jargon_memory_read")
        else 0
    )
    monkeypatch.setattr(
        expression_memory,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(
            AssertionError("compatibility usage recorded")
        ),
    )

    with pytest.raises(AssertionError, match="usage recorded"):
        expression_memory.query_active_expressions(CHAT_STREAM_ID)
    with pytest.raises(AssertionError, match="usage recorded"):
        expression_memory.query_active_jargon(CHAT_STREAM_ID)

    after = get_compatibility_usage_snapshot()
    assert (
        after["schema.legacy_expression_memory_read"].count
        == expression_before + 1
    )
    assert (
        after["schema.legacy_jargon_memory_read"].count
        == jargon_before + 1
    )


def test_stage7d_removes_legacy_prompt_fields_and_context_builders():
    from core import expression_memory
    from core.prompt_v2.schema import PromptCompileRequest
    from nanobot_kt.prompt_runtime import PromptRuntimeInput

    prompt_fields = {item.name for item in fields(PromptCompileRequest)}
    runtime_fields = {item.name for item in fields(PromptRuntimeInput)}

    assert "expression_context" not in prompt_fields
    assert "jargon_context" not in prompt_fields
    assert "expression_context" not in runtime_fields
    assert "jargon_context" not in runtime_fields
    assert not hasattr(expression_memory, "build_expression_context")
    assert not hasattr(expression_memory, "build_jargon_context")


def test_stage7d_expression_learner_keeps_only_retired_scheduler_surface():
    from core import expression_learner

    for retired_helper in (
        "_extract_expression_candidates",
        "_extract_jargon_candidates",
        "_short_cjk_phrases",
        "should_learn_from_chatlog",
        "sanitize_learnable_group_text",
    ):
        assert not hasattr(expression_learner, retired_helper)
    assert callable(expression_learner.run_learning_cycle)
    assert callable(expression_learner.expression_learner_scheduler)


def test_stage7d_registers_legacy_read_and_write_compatibility():
    from core.lifecycle import (
        COMPATIBILITY_REGISTRY,
        CompatibilityKind,
        CompatibilityTombstoneBehavior,
    )

    expected = {
        "schema.legacy_expression_memory_read": (
            CompatibilityTombstoneBehavior.PRESERVE
        ),
        "schema.legacy_jargon_memory_read": (
            CompatibilityTombstoneBehavior.PRESERVE
        ),
        "schema.legacy_expression_memory_write": (
            CompatibilityTombstoneBehavior.REJECT
        ),
        "schema.legacy_jargon_memory_write": (
            CompatibilityTombstoneBehavior.REJECT
        ),
        "schema.legacy_group_analysis_memory_candidate_write": (
            CompatibilityTombstoneBehavior.REJECT
        ),
    }
    for compatibility_id, behavior in expected.items():
        descriptor = COMPATIBILITY_REGISTRY.require(compatibility_id)
        assert descriptor.kind is CompatibilityKind.SCHEMA
        assert descriptor.tombstone_behavior is behavior
        assert descriptor.removal_gate.consecutive_zero_usage_days == 30
        assert descriptor.removal_gate.minimum_full_releases == 1
