"""阶段 7A：群学习 Schema、Registry 与只读基础设施测试。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
import inspect as python_inspect

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool


def _candidate(**overrides):
    from core.db.models import GroupLearningCandidate

    values = {
        "candidate_id": "glc_test_1",
        "chat_stream_id": "qq:42:group",
        "candidate_type": "expression",
        "content": "芜湖",
        "meaning": "",
        "normalized_key": "芜湖",
        "fingerprint": "a" * 64,
        "content_hash": "b" * 64,
        "source": "rule",
        "status": "pending_model_review",
        "rule_id": "expression.short_phrase.v1",
        "rule_version": 1,
    }
    values.update(overrides)
    return GroupLearningCandidate(**values)


def _legacy_record(**overrides):
    from core.db.group_learning_contracts import LegacyGroupLearningRecord

    values = {
        "source": "legacy_expression",
        "legacy_id": 1,
        "chat_stream_id": "qq:42:group",
        "legacy_group_id": "",
        "candidate_type": "expression",
        "content": "芜湖",
        "meaning": "",
        "checked": False,
        "status": "active",
        "approval_source": "",
        "governance_mode": "",
        "approved_content_hash": "",
        "human_reviewer_id": "",
        "human_reviewed_at": None,
        "human_action": "",
        "human_proof_audit_log_id": 0,
        "model_review_run_id": "",
        "model_contract_version": "",
        "created_at": datetime(2026, 7, 1),
        "updated_at": datetime(2026, 7, 2),
    }
    values.update(overrides)
    return LegacyGroupLearningRecord(**values)


def test_aspect_registry_has_exact_seven_items_and_four_defaults():
    from core.group_learning import (
        GROUP_ANALYSIS_ASPECT_IDS,
        GROUP_ANALYSIS_ASPECT_REGISTRY,
        default_scheduled_aspects,
        list_group_analysis_aspects,
    )

    assert GROUP_ANALYSIS_ASPECT_IDS == (
        "topics",
        "expressions",
        "slang",
        "style",
        "titles",
        "quotes",
        "quality",
    )
    assert set(GROUP_ANALYSIS_ASPECT_REGISTRY.ordered_ids) == set(
        GROUP_ANALYSIS_ASPECT_IDS
    )
    assert default_scheduled_aspects() == (
        "topics",
        "expressions",
        "slang",
        "style",
    )
    descriptors = {
        item.aspect_id: item
        for item in list_group_analysis_aspects()
    }
    for aspect_id in ("topics", "expressions", "slang", "style"):
        assert descriptors[aspect_id].writes_long_term_memory is True
        assert descriptors[aspect_id].prompt_injectable is True
    for aspect_id in ("titles", "quotes", "quality"):
        assert descriptors[aspect_id].writes_long_term_memory is False
        assert descriptors[aspect_id].memory_type == ""
        assert descriptors[aspect_id].prompt_injectable is False


def test_rule_registry_is_frozen_fixture_checked_and_dry_run_only():
    from core.group_learning import (
        LEARNING_SIGNAL_RULE_REGISTRY,
        dry_run_learning_rules,
    )
    from core.registry import RegistryBuilder, RegistryFrozenError

    assert len(LEARNING_SIGNAL_RULE_REGISTRY.ordered_ids) == 3
    for descriptor in LEARNING_SIGNAL_RULE_REGISTRY:
        assert descriptor.positive_fixtures
        assert descriptor.negative_fixtures
        assert descriptor.max_input_chars <= 10_000
        assert descriptor.max_matches_per_message > 0
        assert descriptor.performance_budget_ms <= 100

    result = dry_run_learning_rules("摸鱼的意思是上班时偷懒")
    assert [(item.candidate_type, item.canonical_content, item.meaning)
            for item in result.matches] == [
        ("slang", "摸鱼", "上班时偷懒"),
    ]

    builder = RegistryBuilder("group_learning_signal_rule")
    descriptor = next(iter(LEARNING_SIGNAL_RULE_REGISTRY))
    builder.register(descriptor)
    builder.freeze()
    with pytest.raises(RegistryFrozenError):
        builder.register(descriptor)


def test_reserved_terms_are_derived_from_frozen_registries():
    from core.group_learning.reserved_terms import (
        build_reserved_term_snapshot,
    )

    first = build_reserved_term_snapshot()
    second = build_reserved_term_snapshot()

    assert first.sha256 == second.sha256
    assert first.contains("sandbox_exec")
    assert first.contains("group_learning")
    assert set(first.provenance) == {
        "compatibility",
        "features",
        "model_routes",
        "prompts",
        "tasks",
        "tools",
    }


def test_evidence_policy_enforces_each_memory_type_threshold():
    from core.group_learning import (
        EvidenceFact,
        evaluate_evidence_policy,
    )

    two_people = [
        EvidenceFact("u1", "b1", "message"),
        EvidenceFact("u2", "b1", "message"),
    ]
    assert evaluate_evidence_policy("topic", two_people).eligible
    assert evaluate_evidence_policy("expression", two_people).eligible
    assert not evaluate_evidence_policy(
        "style",
        two_people,
    ).eligible
    assert evaluate_evidence_policy(
        "style",
        [*two_people, EvidenceFact("u1", "b2", "message")],
    ).eligible
    assert evaluate_evidence_policy(
        "slang",
        [EvidenceFact("u1", "b1", "explicit_definition")],
    ).eligible
    assert evaluate_evidence_policy(
        "expression",
        [
            EvidenceFact("u1", "b1", "repeated_usage"),
            EvidenceFact("u1", "b2", "repeated_usage"),
            EvidenceFact("u1", "b2", "repeated_usage"),
        ],
    ).eligible


def test_stage7a_migration_is_idempotent_and_creates_only_empty_read_schema():
    from core.database import Base
    from core.schema_migrations import run_schema_migrations

    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    db_inspector = inspect(engine)
    assert {
        "group_learning_schedules",
        "group_learning_stream_states",
        "group_learning_candidates",
        "group_learning_evidence",
        "group_learning_runs",
    } <= set(db_inspector.get_table_names())
    governance_columns = {
        "approval_source",
        "governance_mode",
        "approved_content_hash",
        "model_review_run_id",
        "model_contract_version",
        "human_reviewer_id",
        "human_reviewed_at",
        "human_action",
        "conflict_group_id",
        "version",
    }
    assert governance_columns <= {
        column["name"]
        for column in db_inspector.get_columns("group_memories")
    }
    with engine.connect() as connection:
        schedule_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM group_learning_schedules"
        ).scalar_one()
        migration_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = "
            "'20260723_group_learning_stage7a_schema'"
        ).scalar_one()
    assert schedule_count == 0
    assert migration_count == 1


def test_stage7a_file_migration_snapshots_before_altering_group_memory(
    tmp_path,
):
    from core.schema_migrations import MIGRATIONS, run_schema_migrations

    database_path = tmp_path / "legacy-stage7a.db"
    engine = create_engine(f"sqlite:///{database_path}")
    target_version = "20260723_group_learning_stage7a_schema"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE group_memories ("
            "id INTEGER PRIMARY KEY, "
            "group_id TEXT NOT NULL, "
            "memory_type TEXT NOT NULL, "
            "content TEXT NOT NULL"
            ")"
        )
        connection.exec_driver_sql(
            "INSERT INTO group_memories"
            "(id, group_id, memory_type, content) "
            "VALUES (1, 'group_42', 'topic', '旧内容')"
        )
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
                "(version, name, applied_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (version, name),
            )

    run_schema_migrations(engine, db_path=str(database_path))

    backups = tuple(tmp_path.glob("legacy-stage7a.db.bak.*"))
    assert len(backups) == 1
    assert backups[0].stat().st_size > 0
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT content, approval_source, governance_mode, version "
            "FROM group_memories WHERE id = 1"
        ).one()
    assert tuple(row) == ("旧内容", None, None, 1)


def test_candidate_and_evidence_uniqueness_constraints(db_session):
    from core.db.models import GroupLearningEvidence

    db_session.add(_candidate())
    db_session.commit()

    db_session.add(_candidate(candidate_id="glc_test_2"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    first = GroupLearningEvidence(
        evidence_id="gle_test_1",
        candidate_id="glc_test_1",
        chat_log_id=101,
        sender_id="u1",
        source_run_id="glr_1",
        batch_id="batch_1",
        evidence_hash="c" * 64,
        evidence_kind="message",
    )
    duplicate = GroupLearningEvidence(
        evidence_id="gle_test_2",
        candidate_id="glc_test_1",
        chat_log_id=101,
        sender_id="u1",
        source_run_id="glr_1",
        batch_id="batch_1",
        evidence_hash="c" * 64,
        evidence_kind="message",
    )
    db_session.add(first)
    db_session.commit()
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_query_repository_has_no_write_interface_and_returns_immutable_dto(
    db_session,
):
    from app.group_learning.query_service import GroupLearningQueryService
    from core.db.group_learning_adapter import (
        SqlAlchemyGroupLearningQueryRepository,
    )

    db_session.add(_candidate())
    db_session.commit()
    repository = SqlAlchemyGroupLearningQueryRepository(db_session)

    for method_name in ("add", "update", "delete", "commit", "rollback"):
        assert not hasattr(repository, method_name)
    method_names = {
        name
        for name, _member in python_inspect.getmembers(
            type(repository),
            predicate=python_inspect.isfunction,
        )
    }
    assert not {
        "add",
        "update",
        "delete",
        "commit",
        "rollback",
    } & method_names

    page = GroupLearningQueryService(repository).list_candidates(
        "qq:42:group"
    )
    assert page.items[0].candidate_id == "glc_test_1"
    with pytest.raises(FrozenInstanceError):
        page.items[0].status = "accepted"
    with pytest.raises(ValueError, match="canonical"):
        GroupLearningQueryService(repository).overview("group_42")
    with pytest.raises(ValueError, match="canonical group"):
        GroupLearningQueryService(repository).overview(
            "qq:42:private"
        )


def test_migration_audit_is_stable_and_checked_does_not_mean_human():
    from app.group_learning.migration_audit import (
        GroupLearningMigrationAuditService,
    )

    records = (
        _legacy_record(legacy_id=1, checked=True),
        _legacy_record(legacy_id=2, source="legacy_group_memory"),
        _legacy_record(
            source="legacy_jargon",
            legacy_id=3,
            candidate_type="slang",
            content="摸鱼",
            meaning="上班时偷懒",
        ),
        _legacy_record(
            source="legacy_jargon",
            legacy_id=4,
            candidate_type="slang",
            content="摸鱼",
            meaning="随便划水",
        ),
        _legacy_record(
            legacy_id=5,
            chat_stream_id="not-canonical",
        ),
        _legacy_record(
            source="legacy_group_memory",
            legacy_id=6,
            candidate_type="topic",
            content="长期话题",
            approval_source="human",
            governance_mode="human_managed",
            approved_content_hash="c" * 64,
            human_reviewer_id="admin-1",
            human_reviewed_at=datetime(2026, 7, 2),
            human_action="accept",
        ),
    )

    class FakeRepository:
        def list_legacy_records(self):
            return tuple(reversed(records))

    service = GroupLearningMigrationAuditService(FakeRepository())
    first = service.audit()
    second = service.audit()

    assert first.source_count == 6
    assert first.valid_count == 5
    assert first.planned_count == 4
    assert first.duplicate_count == 1
    assert first.conflict_count == 1
    assert first.invalid_identity_count == 1
    assert first.checked_without_human_proof_count == 1
    assert first.source_sha256 == second.source_sha256
    assert first.planned_sha256 == second.planned_sha256
    checked = next(
        item
        for item in first.planned
        if item.source_ref == "legacy_expression:1"
    )
    human = next(
        item
        for item in first.planned
        if item.source_ref == "legacy_group_memory:6"
    )
    assert checked.planned_status == "pending_model_review"
    assert checked.planned_approval_source == ""
    assert checked.checked_without_human_proof is True
    assert human.planned_status == "accepted"
    assert human.planned_approval_source == "human"
    assert not hasattr(first.planned[0], "content")


def test_group_learning_setting_and_feature_are_default_off():
    from core.config_registry import SETTING_DEFS
    from core.lifecycle import (
        FEATURE_LIFECYCLE_REGISTRY,
        FeatureLifecycleState,
        FeatureScope,
    )

    setting = SETTING_DEFS["group_learning.enabled"]
    feature = FEATURE_LIFECYCLE_REGISTRY.require("group_learning")

    assert setting.default is False
    assert feature.state is FeatureLifecycleState.EXPERIMENTAL
    assert feature.default_enabled is False
    assert feature.supported_scopes == (
        FeatureScope.GROUP_SESSION,
        FeatureScope.ADMIN,
    )
    assert feature.data_migrations == (
        "20260723_group_learning_stage7a_schema",
        "20260723_group_learning_stage7b_review_fields",
        "20260724_group_learning_stage7c_schedule_fencing",
        "20260724_group_learning_stage7d_legacy_read_only",
    )
    assert set(feature.enablement_gates) == {
        "schema_ready",
        "explicit_session_schedule",
        "candidate_writer_exclusive",
        "model_review_observation_passed",
        "evidence_policy_ready",
        "operator_approval",
    }


def test_group_memory_legacy_projection_does_not_expose_new_governance_fields(
    db_session,
):
    from core.db.group_memory_adapter import (
        SqlAlchemyGroupMemoryRepository,
    )
    from core.db.group_memory_contracts import (
        group_memory_record_to_dict,
    )
    from core.db.models import GroupMemory

    memory = GroupMemory(
        chat_stream_id="qq:42:group",
        group_id="group_42",
        memory_type="topic",
        content="稳定话题",
        content_hash="d" * 64,
        approval_source="human",
        governance_mode="human_managed",
        human_reviewer_id="admin-1",
        human_action="accept",
    )
    db_session.add(memory)
    db_session.commit()

    record = SqlAlchemyGroupMemoryRepository(db_session).get_memory(
        int(memory.id)
    )
    assert record is not None
    assert record.approval_source == "human"
    legacy_payload = group_memory_record_to_dict(record)
    assert "approval_source" not in legacy_payload
    assert "governance_mode" not in legacy_payload
    assert "human_reviewer_id" not in legacy_payload
