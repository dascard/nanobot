"""阶段 7B：candidate-only 写入、旁路审核与旧 Writer 退役测试。"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json

import pytest


CHAT_STREAM_ID = "qq:42:group"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _batch_write(
    *,
    run_id: str = "glr_test_1",
    idempotency_key: str = "group-learning:42:100:101",
    candidate_id: str = "glc_test_1",
    evidence_id: str = "gle_test_1",
    chat_log_id: int = 101,
):
    from core.db.group_learning_contracts import (
        GroupLearningBatchWrite,
        GroupLearningCandidateWrite,
        GroupLearningEvidenceWrite,
        GroupLearningRunWrite,
    )

    content = "摸鱼"
    meaning = "上班时偷懒"
    fingerprint = _sha256(
        f"{CHAT_STREAM_ID}\0slang\0{content}\0{meaning}"
    )
    return GroupLearningBatchWrite(
        run=GroupLearningRunWrite(
            run_id=run_id,
            idempotency_key=idempotency_key,
            chat_stream_id=CHAT_STREAM_ID,
            trigger="manual",
            selected_aspects_json='["slang"]',
            cursor_start_chat_log_id=100,
            cursor_end_chat_log_id=chat_log_id,
            context_start_chat_log_id=90,
            context_end_chat_log_id=99,
            rules_generation=1,
            raw_message_count=2,
            cleaned_message_count=2,
            eligible_message_count=1,
            trace_id="trace_stage7b",
            job_id="job_stage7b",
        ),
        candidates=(
            GroupLearningCandidateWrite(
                candidate_id=candidate_id,
                chat_stream_id=CHAT_STREAM_ID,
                candidate_type="slang",
                content=content,
                meaning=meaning,
                normalized_key=content,
                fingerprint=fingerprint,
                content_hash=_sha256(f"{content}\0{meaning}"),
                source="rule",
                rule_id="slang.explicit_definition.v1",
                rule_version=1,
                source_run_id=run_id,
            ),
        ),
        evidence=(
            GroupLearningEvidenceWrite(
                evidence_id=evidence_id,
                candidate_id=candidate_id,
                chat_log_id=chat_log_id,
                sender_id="u1",
                source_run_id=run_id,
                batch_id=run_id,
                evidence_hash=_sha256(
                    f"{candidate_id}\0{chat_log_id}\0u1"
                ),
                evidence_kind="explicit_definition",
            ),
        ),
    )


def _metrics(**overrides):
    from core.db.group_learning_contracts import (
        GroupLearningObservationMetrics,
    )

    values = {
        "task_run_id": "taskrun_stage7b",
        "contract_version": "group_memory_learning_v1",
        "provider": "test-provider",
        "model": "test-model",
        "input_chars": 120,
        "input_tokens": 80,
        "output_tokens": 30,
        "total_tokens": 110,
        "cost_microusd": None,
        "latency_ms": 35,
        "attempt_count": 1,
        "raw_output_bytes": 64,
        "raw_output_sha256": "d" * 64,
    }
    values.update(overrides)
    return GroupLearningObservationMetrics(**values)


def test_candidate_batch_is_idempotent_and_only_advances_scanned_cursor(
    db_session,
):
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningEvidence,
        GroupLearningRun,
        GroupLearningStreamState,
        GroupMemory,
    )

    repository = SqlAlchemyGroupLearningCommandRepository(db_session)
    first = repository.persist_candidate_batch(_batch_write())
    repository.commit()
    replay = repository.persist_candidate_batch(_batch_write())
    repository.commit()

    assert first.replayed is False
    assert first.candidate_count == 1
    assert first.evidence_added_count == 1
    assert replay.replayed is True
    assert replay.run_id == first.run_id
    assert replay.evidence_added_count == 0
    assert db_session.query(GroupLearningRun).count() == 1
    assert db_session.query(GroupLearningCandidate).count() == 1
    assert db_session.query(GroupLearningEvidence).count() == 1
    assert db_session.query(GroupMemory).count() == 0
    candidate = db_session.query(GroupLearningCandidate).one()
    assert candidate.status == "pending_model_review"
    assert candidate.hit_count == 1
    state = db_session.get(GroupLearningStreamState, CHAT_STREAM_ID)
    assert state is not None
    assert state.last_scanned_chat_log_id == 101
    assert state.last_success_chat_log_id == 0
    assert state.last_candidate_watermark == first.candidate_watermark


def test_repeated_log_is_not_counted_twice_for_same_candidate(db_session):
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.models import GroupLearningCandidate, GroupLearningEvidence

    repository = SqlAlchemyGroupLearningCommandRepository(db_session)
    repository.persist_candidate_batch(_batch_write())
    repository.commit()
    second = _batch_write(
        run_id="glr_test_2",
        idempotency_key="group-learning:42:101:101",
        candidate_id="glc_same_fingerprint",
        evidence_id="gle_duplicate_log",
    )

    result = repository.persist_candidate_batch(second)
    repository.commit()

    assert result.replayed is False
    assert result.candidate_ids == ("glc_test_1",)
    assert result.evidence_added_count == 0
    assert db_session.query(GroupLearningEvidence).count() == 1
    assert db_session.query(GroupLearningCandidate).one().hit_count == 1


def test_new_rule_evidence_does_not_replace_human_candidate_provenance(
    db_session,
):
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.group_learning_contracts import (
        GroupLearningHumanReviewWrite,
    )
    from core.db.models import GroupLearningCandidate, GroupLearningEvidence

    repository = SqlAlchemyGroupLearningCommandRepository(db_session)
    repository.persist_candidate_batch(_batch_write())
    repository.commit()
    repository.apply_human_review(
        GroupLearningHumanReviewWrite(
            candidate_id="glc_test_1",
            reviewer_id="admin-1",
            action="accept",
            reviewed_content="摸鱼",
            reviewed_meaning="上班时偷懒",
            reviewed_content_hash=_sha256("摸鱼\0上班时偷懒"),
            reviewed_at=datetime(2026, 7, 23, 12, 0, 0),
        )
    )
    repository.commit()

    second = _batch_write(
        run_id="glr_test_2",
        idempotency_key="group-learning:42:101:102",
        candidate_id="glc_same_human_fingerprint",
        evidence_id="gle_human_new_log",
        chat_log_id=102,
    )
    repository.persist_candidate_batch(second)
    repository.commit()

    candidate = db_session.query(GroupLearningCandidate).one()
    assert candidate.approval_source == "human"
    assert candidate.source_run_id == "glr_test_1"
    assert candidate.human_reviewer_id == "admin-1"
    assert candidate.model_review_run_id == ""
    assert db_session.query(GroupLearningEvidence).count() == 2


def test_file_sqlite_concurrent_replay_writes_one_candidate_and_evidence(
    tmp_path,
):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from core.db.base import Base
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningEvidence,
        GroupLearningRun,
        GroupLearningStreamState,
    )

    engine = create_engine(
        f"sqlite:///{tmp_path / 'group-learning-concurrency.db'}",
        connect_args={
            "check_same_thread": False,
            "timeout": 5,
        },
    )
    Base.metadata.create_all(
        engine,
        tables=[
            GroupLearningCandidate.__table__,
            GroupLearningEvidence.__table__,
            GroupLearningRun.__table__,
            GroupLearningStreamState.__table__,
        ],
    )
    session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    both_checked_idempotency = threading.Barrier(2)
    synchronized_threads: set[int] = set()
    synchronized_threads_lock = threading.Lock()

    def synchronize_first_idempotency_read(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        normalized = " ".join(statement.lower().split())
        if (
            "from group_learning_runs" not in normalized
            or "idempotency_key" not in normalized
        ):
            return
        thread_id = threading.get_ident()
        with synchronized_threads_lock:
            if (
                thread_id in synchronized_threads
                or len(synchronized_threads) >= 2
            ):
                return
            synchronized_threads.add(thread_id)
        both_checked_idempotency.wait(timeout=5)

    def persist() -> bool:
        session = session_factory()
        repository = SqlAlchemyGroupLearningCommandRepository(session)
        try:
            result = repository.persist_candidate_batch(_batch_write())
            repository.commit()
            return result.replayed
        except BaseException:
            repository.rollback()
            raise
        finally:
            session.close()

    event.listen(
        engine,
        "after_cursor_execute",
        synchronize_first_idempotency_read,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            replayed = [
                future.result(timeout=10)
                for future in (
                    pool.submit(persist),
                    pool.submit(persist),
                )
            ]
        verify = session_factory()
        try:
            assert sorted(replayed) == [False, True]
            assert verify.query(GroupLearningRun).count() == 1
            assert verify.query(GroupLearningCandidate).count() == 1
            assert verify.query(GroupLearningEvidence).count() == 1
        finally:
            verify.close()
    finally:
        event.remove(
            engine,
            "after_cursor_execute",
            synchronize_first_idempotency_read,
        )
        engine.dispose()


def test_run_id_collision_replays_when_initial_idempotency_read_is_stale(
    db_session,
):
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.models import GroupLearningRun

    repository = SqlAlchemyGroupLearningCommandRepository(db_session)
    repository.persist_candidate_batch(_batch_write())
    repository.commit()

    class _StaleQuery:
        def filter(self, *_criteria):
            return self

        def first(self):
            return None

    class _StaleIdempotencySession:
        def __init__(self, delegate):
            self._delegate = delegate
            self._missed = False

        def query(self, *entities):
            if entities == (GroupLearningRun,) and not self._missed:
                self._missed = True
                return _StaleQuery()
            return self._delegate.query(*entities)

        def __getattr__(self, name):
            return getattr(self._delegate, name)

    stale_repository = SqlAlchemyGroupLearningCommandRepository(
        _StaleIdempotencySession(db_session)
    )
    result = stale_repository.persist_candidate_batch(_batch_write())

    assert result.replayed is True
    assert result.run_id == "glr_test_1"


def test_model_observation_records_only_proposal_and_never_group_memory(
    db_session,
):
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.group_learning_contracts import (
        GroupLearningObservationWrite,
    )
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningRun,
        GroupLearningStreamState,
        GroupMemory,
    )

    repository = SqlAlchemyGroupLearningCommandRepository(db_session)
    repository.persist_candidate_batch(_batch_write())
    repository.commit()
    repository.record_model_observation(
        run_id="glr_test_1",
        observations=(
            GroupLearningObservationWrite(
                candidate_id="glc_test_1",
                action="new",
                reviewed_content="摸鱼",
                reviewed_meaning="工作时间暂时偷懒",
                reviewed_content_hash=_sha256(
                    "摸鱼\0工作时间暂时偷懒"
                ),
                target_memory_id=None,
                reason_hash=_sha256("模型认为释义更准确"),
            ),
        ),
        discoveries=(),
        discovery_evidence=(),
        metrics=_metrics(),
        observed_at=datetime(2026, 7, 23, 12, 0, 0),
    )
    repository.commit()

    candidate = db_session.query(GroupLearningCandidate).one()
    run = db_session.get(GroupLearningRun, "glr_test_1")
    state = db_session.get(GroupLearningStreamState, CHAT_STREAM_ID)
    assert candidate.status == "pending_model_review"
    assert candidate.approval_source is None
    assert candidate.model_decision == "new"
    assert candidate.reviewed_meaning == "工作时间暂时偷懒"
    assert candidate.observation_reason_hash == _sha256(
        "模型认为释义更准确"
    )
    assert db_session.query(GroupMemory).count() == 0
    assert run is not None
    assert run.status == "succeeded"
    assert run.task_run_id == "taskrun_stage7b"
    assert run.total_tokens == 110
    assert run.cost_microusd is None
    assert state is not None
    assert state.last_success_chat_log_id == 0
    assert state.last_success_run_id == ""


def test_model_failure_preserves_pending_candidate_and_success_cursor(
    db_session,
):
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningRun,
        GroupLearningStreamState,
    )

    repository = SqlAlchemyGroupLearningCommandRepository(db_session)
    repository.persist_candidate_batch(_batch_write())
    repository.commit()
    repository.record_model_failure(
        run_id="glr_test_1",
        error_code="schema_invalid",
        metrics=_metrics(attempt_count=2),
    )
    repository.commit()

    candidate = db_session.query(GroupLearningCandidate).one()
    run = db_session.get(GroupLearningRun, "glr_test_1")
    state = db_session.get(GroupLearningStreamState, CHAT_STREAM_ID)
    assert candidate.status == "pending_model_review"
    assert candidate.model_decision == ""
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "schema_invalid"
    assert run.attempt_count == 2
    assert state is not None
    assert state.last_success_chat_log_id == 0
    assert state.last_error_code == "schema_invalid"


def test_human_review_is_authoritative_and_model_cannot_overwrite_it(
    db_session,
):
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.group_learning_contracts import (
        GroupLearningHumanReviewWrite,
        GroupLearningObservationWrite,
    )
    from core.db.models import GroupLearningCandidate, GroupMemory

    repository = SqlAlchemyGroupLearningCommandRepository(db_session)
    repository.persist_candidate_batch(_batch_write())
    repository.commit()
    reviewed_at = datetime(2026, 7, 23, 12, 30, 0)
    repository.apply_human_review(
        GroupLearningHumanReviewWrite(
            candidate_id="glc_test_1",
            reviewer_id="admin-1",
            action="edit_accept",
            reviewed_content="摸鱼",
            reviewed_meaning="工作时暂时休息",
            reviewed_content_hash=_sha256("摸鱼\0工作时暂时休息"),
            reviewed_at=reviewed_at,
        )
    )
    repository.commit()
    repository.record_model_observation(
        run_id="glr_test_1",
        observations=(
            GroupLearningObservationWrite(
                candidate_id="glc_test_1",
                action="reject",
                reviewed_content="模型覆盖内容",
                reviewed_meaning="模型覆盖释义",
                reviewed_content_hash=_sha256(
                    "模型覆盖内容\0模型覆盖释义"
                ),
                target_memory_id=None,
                reason_hash=_sha256("模型拒绝原因"),
            ),
        ),
        discoveries=(),
        discovery_evidence=(),
        metrics=_metrics(),
        observed_at=datetime(2026, 7, 23, 13, 0, 0),
    )
    repository.commit()

    candidate = db_session.query(GroupLearningCandidate).one()
    assert candidate.status == "accepted"
    assert candidate.approval_source == "human"
    assert candidate.human_reviewer_id == "admin-1"
    assert candidate.human_action == "edit_accept"
    assert candidate.human_reviewed_at == reviewed_at
    assert candidate.reviewed_meaning == "工作时暂时休息"
    assert candidate.model_decision == ""
    assert db_session.query(GroupMemory).count() == 0


def test_rule_candidate_service_ignores_context_only_evidence(db_session):
    from app.group_learning.candidate_service import (
        GroupLearningCandidateBatchRequest,
        GroupLearningCandidateService,
        GroupLearningMessage,
    )
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.models import GroupLearningCandidate, GroupLearningEvidence

    service = GroupLearningCandidateService(
        SqlAlchemyGroupLearningCommandRepository(db_session)
    )
    result = service.persist_rule_candidates(
        GroupLearningCandidateBatchRequest(
            run_id="glr_service_1",
            idempotency_key="group-learning:service:1",
            chat_stream_id=CHAT_STREAM_ID,
            trigger="manual",
            aspects=("slang",),
            cursor_start_chat_log_id=100,
            cursor_end_chat_log_id=102,
            context_start_chat_log_id=90,
            context_end_chat_log_id=100,
            messages=(
                GroupLearningMessage(
                    chat_log_id=100,
                    sender_id="u0",
                    content="划水的意思是暂时休息",
                    context_only=True,
                ),
                GroupLearningMessage(
                    chat_log_id=102,
                    sender_id="u1",
                    content="摸鱼的意思是上班时偷懒",
                    context_only=False,
                ),
            ),
        )
    )

    assert result.candidate_count == 1
    assert result.evidence_added_count == 1
    candidate = db_session.query(GroupLearningCandidate).one()
    evidence = db_session.query(GroupLearningEvidence).one()
    assert candidate.content == "摸鱼"
    assert candidate.meaning == "上班时偷懒"
    assert evidence.chat_log_id == 102


def test_rule_candidate_service_honors_session_rule_activation_policy(
    db_session,
):
    from app.group_learning.candidate_service import (
        GroupLearningCandidateBatchRequest,
        GroupLearningCandidateService,
        GroupLearningMessage,
    )
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )

    service = GroupLearningCandidateService(
        SqlAlchemyGroupLearningCommandRepository(db_session),
        rule_ids_for_session=lambda chat_stream_id: (
            "expression.short_phrase.v1",
        ),
    )
    result = service.persist_rule_candidates(
        GroupLearningCandidateBatchRequest(
            run_id="glr_rule_control",
            idempotency_key="group-learning:rule-control",
            chat_stream_id=CHAT_STREAM_ID,
            trigger="manual",
            aspects=("slang",),
            cursor_start_chat_log_id=0,
            cursor_end_chat_log_id=1,
            context_start_chat_log_id=0,
            context_end_chat_log_id=0,
            messages=(
                GroupLearningMessage(
                    chat_log_id=1,
                    sender_id="u1",
                    content="摸鱼的意思是上班时偷懒",
                ),
            ),
        )
    )

    assert result.candidate_count == 0
    assert result.evidence_added_count == 0


def test_rule_activation_controls_validate_registry_and_canonical_scope():
    from core.group_learning.rule_activation import (
        GroupLearningRuleControls,
    )

    controls = GroupLearningRuleControls().with_rule_enabled(
        rule_id="slang.explicit_definition.v1",
        enabled=False,
        chat_stream_id=CHAT_STREAM_ID,
    )
    restored = GroupLearningRuleControls.from_json(controls.to_json())

    assert "slang.explicit_definition.v1" in (
        restored.disabled_rule_ids(CHAT_STREAM_ID)
    )
    assert "slang.explicit_definition.v1" not in (
        restored.enabled_rule_ids(CHAT_STREAM_ID)
    )

    with pytest.raises(ValueError, match="未登记规则"):
        GroupLearningRuleControls(global_disabled=("unknown.rule",))
    with pytest.raises(ValueError, match="canonical"):
        GroupLearningRuleControls(
            session_disabled={
                "group_42": ("slang.explicit_definition.v1",),
            }
        )


def test_rule_candidate_service_rejects_noncanonical_or_private_identity(
    db_session,
):
    from app.group_learning.candidate_service import (
        GroupLearningCandidateBatchRequest,
        GroupLearningCandidateService,
        GroupLearningMessage,
    )
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )

    service = GroupLearningCandidateService(
        SqlAlchemyGroupLearningCommandRepository(db_session)
    )
    values = {
        "run_id": "glr_invalid",
        "idempotency_key": "group-learning:invalid",
        "trigger": "manual",
        "aspects": ("slang",),
        "cursor_start_chat_log_id": 0,
        "cursor_end_chat_log_id": 1,
        "context_start_chat_log_id": 0,
        "context_end_chat_log_id": 0,
        "messages": (
            GroupLearningMessage(
                chat_log_id=1,
                sender_id="u1",
                content="摸鱼的意思是休息",
            ),
        ),
    }

    with pytest.raises(ValueError, match="canonical"):
        service.persist_rule_candidates(
            GroupLearningCandidateBatchRequest(
                chat_stream_id="group_42",
                **values,
            )
        )
    with pytest.raises(ValueError, match="canonical group"):
        service.persist_rule_candidates(
            GroupLearningCandidateBatchRequest(
                chat_stream_id="qq:42:private",
                **values,
            )
        )


def test_group_memory_learning_business_validator_rejects_scope_escape():
    from core.task_runtime.validators import (
        TaskBusinessValidationError,
        validate_task_business_output,
    )

    value = {
        "reviews": [{
            "candidate_id": "glc_test_1",
            "action": "merge_into",
            "candidate_type": "slang",
            "content": "摸鱼",
            "meaning": "偷懒",
            "evidence_log_ids": [999],
            "target_memory_id": 88,
            "reason": "尝试越权引用",
        }],
        "discoveries": [],
    }
    context = {
        "allowed_candidate_ids": ("glc_test_1",),
        "candidate_types": {"glc_test_1": "slang"},
        "allowed_evidence_log_ids": (101,),
        "allowed_target_memory_ids": {"glc_test_1": (7,)},
        "selected_candidate_types": ("slang",),
    }

    with pytest.raises(
        TaskBusinessValidationError,
        match="群记忆审核",
    ):
        validate_task_business_output(
            "group_memory_learning_v1",
            value,
            request_context=context,
        )


def test_legacy_learning_cycle_is_noop_without_opening_database(monkeypatch):
    from core import database
    from core.expression_learner import run_learning_cycle

    monkeypatch.setattr(
        database,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(
            AssertionError("旧学习周期不得再打开数据库")
        ),
    )

    assert run_learning_cycle() == {
        "retired": True,
        "writer": "disabled",
        "replacement": "group_learning_candidates",
    }


def test_legacy_expression_confidence_no_longer_auto_activates(db_session):
    from core.db.models import ExpressionMemory
    from core.expression_memory import (
        LegacyGroupLearningWriteRetired,
        upsert_expression,
    )

    row = ExpressionMemory(
        chat_stream_id=CHAT_STREAM_ID,
        expression="芜湖",
        confidence=0.74,
        source_count=3,
        checked=0,
        status="candidate",
    )
    db_session.add(row)
    db_session.commit()

    with pytest.raises(LegacyGroupLearningWriteRetired):
        upsert_expression(
            CHAT_STREAM_ID,
            "芜湖",
            source_count=10,
        )
    db_session.expire_all()
    row = db_session.query(ExpressionMemory).one()
    assert row.confidence == 0.74
    assert row.source_count == 3
    assert row.status == "candidate"


def test_group_analysis_legacy_candidate_writer_is_retired(
    db_session,
):
    from app.group_analysis.memory_candidates import (
        LegacyMemoryCandidateWriterRetired,
        extract_and_persist,
    )
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningEvidence,
        GroupMemory,
    )

    with pytest.raises(LegacyMemoryCandidateWriterRetired):
        extract_and_persist(
            "group_42",
            {
                "topics": {
                    "_generator": "llm",
                    "topics": [{
                        "topic": "本地模型",
                        "detail": "群成员持续讨论部署",
                        "evidence_log_ids": [101, 102],
                    }],
                },
            },
        )

    assert db_session.query(GroupLearningCandidate).count() == 0
    assert db_session.query(GroupLearningEvidence).count() == 0
    assert db_session.query(GroupMemory).count() == 0


def test_model_reason_body_is_never_stored_in_run_or_candidate(db_session):
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.group_learning_contracts import (
        GroupLearningObservationWrite,
    )
    from core.db.models import GroupLearningCandidate, GroupLearningRun

    secret_reason = "不得进入数据库的完整模型理由 secret-123"
    repository = SqlAlchemyGroupLearningCommandRepository(db_session)
    repository.persist_candidate_batch(_batch_write())
    repository.commit()
    repository.record_model_observation(
        run_id="glr_test_1",
        observations=(
            GroupLearningObservationWrite(
                candidate_id="glc_test_1",
                action="reject",
                reviewed_content="摸鱼",
                reviewed_meaning="上班时偷懒",
                reviewed_content_hash=_sha256("摸鱼\0上班时偷懒"),
                target_memory_id=None,
                reason_hash=_sha256(secret_reason),
            ),
        ),
        discoveries=(),
        discovery_evidence=(),
        metrics=_metrics(
            raw_output_sha256=_sha256(
                json.dumps({"reason": secret_reason})
            )
        ),
        observed_at=datetime(2026, 7, 23, 14, 0, 0),
    )
    repository.commit()

    candidate = db_session.query(GroupLearningCandidate).one()
    run = db_session.get(GroupLearningRun, "glr_test_1")
    assert secret_reason not in repr(vars(candidate))
    assert run is not None
    assert secret_reason not in repr(vars(run))
    assert candidate.observation_reason_hash == _sha256(secret_reason)


def _successful_review_result():
    from core.task_runtime import TaskResult

    return TaskResult(
        parsed_value={
            "reviews": [{
                "candidate_id": "glc_test_1",
                "action": "new",
                "candidate_type": "slang",
                "content": "摸鱼",
                "meaning": "工作时间暂时休息",
                "evidence_log_ids": [101],
                "target_memory_id": None,
                "reason": "语境与候选释义一致",
            }],
            "discoveries": [{
                "candidate_type": "slang",
                "content": "赛博监工",
                "meaning": "在线提醒大家交作业的人",
                "evidence_log_ids": [102],
                "reason": "消息中给出了明确用法",
            }],
        },
        contract_version="group_memory_learning_v1",
        route_key="group_memory_learning",
        provider="test-provider",
        model="test-model",
        attempt_count=1,
        latency_ms=42.4,
        failure=None,
        raw_output_sha256="e" * 64,
        raw_output_bytes=320,
        validation_diagnostics=(),
        run_id="taskrun_review_success",
        usage={
            "prompt_tokens": 180,
            "completion_tokens": 80,
            "total_tokens": 260,
        },
    )


def _review_request():
    from app.group_learning.candidate_service import GroupLearningMessage
    from app.group_learning.review_service import (
        GroupLearningModelReviewRequest,
    )

    return GroupLearningModelReviewRequest(
        run_id="glr_test_1",
        chat_stream_id=CHAT_STREAM_ID,
        aspects=("slang",),
        candidate_ids=("glc_test_1",),
        messages=(
            GroupLearningMessage(
                chat_log_id=99,
                sender_id="u0",
                content="上一批上下文",
                context_only=True,
            ),
            GroupLearningMessage(
                chat_log_id=101,
                sender_id="u1",
                content="摸鱼的意思是工作时间暂时休息",
            ),
            GroupLearningMessage(
                chat_log_id=102,
                sender_id="u2",
                content="赛博监工就是在线提醒大家交作业的人",
            ),
        ),
    )


def test_model_review_service_records_review_and_discovery_without_promotion(
    db_session,
):
    from app.group_learning.review_service import (
        GroupLearningModelReviewService,
    )
    from core.db.group_learning_adapter import (
        SqlAlchemyGroupLearningQueryRepository,
    )
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.group_memory_adapter import (
        SqlAlchemyGroupMemoryRepository,
    )
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningEvidence,
        GroupLearningRun,
        GroupMemory,
    )

    command_repository = SqlAlchemyGroupLearningCommandRepository(
        db_session
    )
    command_repository.persist_candidate_batch(_batch_write())
    command_repository.commit()
    captured = {}

    def execute(invocation):
        captured["invocation"] = invocation
        return _successful_review_result()

    outcome = GroupLearningModelReviewService(
        query_repository=SqlAlchemyGroupLearningQueryRepository(
            db_session
        ),
        command_repository=command_repository,
        group_memory_repository=SqlAlchemyGroupMemoryRepository(
            db_session
        ),
        task_executor=execute,
    ).review(_review_request())

    assert outcome.status == "observed"
    assert outcome.reviewed_count == 1
    assert outcome.discovery_count == 1
    assert captured["invocation"].route_key == "group_memory_learning"
    assert captured["invocation"].request_context[
        "allowed_evidence_log_ids"
    ] == (101, 102)
    assert 99 not in captured["invocation"].request_context[
        "allowed_evidence_log_ids"
    ]
    rows = (
        db_session.query(GroupLearningCandidate)
        .order_by(GroupLearningCandidate.id)
        .all()
    )
    assert len(rows) == 2
    assert rows[0].model_decision == "new"
    assert rows[0].status == "pending_model_review"
    assert rows[1].source == "model"
    assert rows[1].content == "赛博监工"
    assert rows[1].status == "pending_model_review"
    discovery_evidence = (
        db_session.query(GroupLearningEvidence)
        .filter(
            GroupLearningEvidence.candidate_id
            == rows[1].candidate_id
        )
        .one()
    )
    assert discovery_evidence.chat_log_id == 102
    assert discovery_evidence.evidence_kind == "message"
    assert db_session.query(GroupMemory).count() == 0
    run = db_session.get(GroupLearningRun, "glr_test_1")
    assert run is not None
    assert run.status == "succeeded"
    assert run.input_tokens == 180
    assert run.output_tokens == 80
    assert run.total_tokens == 260
    assert run.cost_microusd is None


def test_model_review_service_failure_preserves_candidate(db_session):
    from app.group_learning.review_service import (
        GroupLearningModelReviewService,
    )
    from core.db.group_learning_adapter import (
        SqlAlchemyGroupLearningQueryRepository,
    )
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.group_memory_adapter import (
        SqlAlchemyGroupMemoryRepository,
    )
    from core.db.models import GroupLearningCandidate, GroupLearningRun
    from core.task_runtime import (
        TaskFailureCode,
        TaskFailureStage,
        TaskResult,
        TaskTerminalAction,
        TaskTypedFailure,
    )

    command_repository = SqlAlchemyGroupLearningCommandRepository(
        db_session
    )
    command_repository.persist_candidate_batch(_batch_write())
    command_repository.commit()
    failed = TaskResult(
        parsed_value=None,
        contract_version="group_memory_learning_v1",
        route_key="group_memory_learning",
        provider="test-provider",
        model="test-model",
        attempt_count=1,
        latency_ms=20,
        failure=TaskTypedFailure(
            code=TaskFailureCode.SCHEMA_INVALID,
            stage=TaskFailureStage.OUTPUT_PARSE,
            retryable=False,
            summary="结构化输出无效",
            terminal_action=TaskTerminalAction.PRESERVE_PENDING,
        ),
        raw_output_sha256="f" * 64,
        raw_output_bytes=100,
        validation_diagnostics=(),
        run_id="taskrun_review_failed",
    )

    outcome = GroupLearningModelReviewService(
        query_repository=SqlAlchemyGroupLearningQueryRepository(
            db_session
        ),
        command_repository=command_repository,
        group_memory_repository=SqlAlchemyGroupMemoryRepository(
            db_session
        ),
        task_executor=lambda _invocation: failed,
    ).review(_review_request())

    assert outcome.status == "failed"
    assert outcome.failure_code == "schema_invalid"
    candidate = db_session.query(GroupLearningCandidate).one()
    assert candidate.status == "pending_model_review"
    assert candidate.model_decision == ""
    run = db_session.get(GroupLearningRun, "glr_test_1")
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "schema_invalid"


def test_model_review_service_rejects_discovery_duplicate_of_rule_candidate(
    db_session,
):
    from app.group_learning.review_service import (
        GroupLearningModelReviewService,
    )
    from core.db.group_learning_adapter import (
        SqlAlchemyGroupLearningQueryRepository,
    )
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.group_memory_adapter import (
        SqlAlchemyGroupMemoryRepository,
    )
    from core.db.models import GroupLearningCandidate, GroupLearningRun
    from core.task_runtime import TaskResult

    command_repository = SqlAlchemyGroupLearningCommandRepository(
        db_session
    )
    command_repository.persist_candidate_batch(_batch_write())
    command_repository.commit()
    duplicate = TaskResult(
        parsed_value={
            "reviews": [{
                "candidate_id": "glc_test_1",
                "action": "new",
                "candidate_type": "slang",
                "content": "摸鱼",
                "meaning": "上班时偷懒",
                "evidence_log_ids": [101],
                "target_memory_id": None,
                "reason": "保留原候选",
            }],
            "discoveries": [{
                "candidate_type": "slang",
                "content": "摸鱼",
                "meaning": "上班时偷懒",
                "evidence_log_ids": [101],
                "reason": "错误地重复补充同一候选",
            }],
        },
        contract_version="group_memory_learning_v1",
        route_key="group_memory_learning",
        provider="test-provider",
        model="test-model",
        attempt_count=1,
        latency_ms=10,
        failure=None,
        raw_output_sha256="a" * 64,
        raw_output_bytes=100,
        validation_diagnostics=(),
        run_id="taskrun_duplicate_discovery",
    )

    outcome = GroupLearningModelReviewService(
        query_repository=SqlAlchemyGroupLearningQueryRepository(
            db_session
        ),
        command_repository=command_repository,
        group_memory_repository=SqlAlchemyGroupMemoryRepository(
            db_session
        ),
        task_executor=lambda _invocation: duplicate,
    ).review(_review_request())

    assert outcome.status == "failed"
    assert outcome.failure_code == "business_validation_failed"
    assert db_session.query(GroupLearningCandidate).count() == 1
    run = db_session.get(GroupLearningRun, "glr_test_1")
    assert run is not None
    assert run.status == "failed"


def test_model_discovery_reuses_older_human_candidate_without_overwriting_it(
    db_session,
):
    from app.group_learning.candidate_service import (
        GroupLearningCandidateBatchRequest,
        GroupLearningCandidateService,
        GroupLearningMessage,
    )
    from app.group_learning.review_service import (
        GroupLearningHumanReviewService,
        GroupLearningModelReviewRequest,
        GroupLearningModelReviewService,
    )
    from core.db.group_learning_adapter import (
        SqlAlchemyGroupLearningQueryRepository,
    )
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.group_memory_adapter import (
        SqlAlchemyGroupMemoryRepository,
    )
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningEvidence,
        GroupLearningRun,
    )
    from core.task_runtime import TaskResult

    command_repository = SqlAlchemyGroupLearningCommandRepository(
        db_session
    )
    command_repository.persist_candidate_batch(_batch_write())
    command_repository.commit()
    query_repository = SqlAlchemyGroupLearningQueryRepository(db_session)
    GroupLearningHumanReviewService(
        query_repository=query_repository,
        command_repository=command_repository,
    ).review(
        candidate_id="glc_test_1",
        reviewer_id="admin-1",
        action="edit_accept",
        reviewed_content="摸鱼",
        reviewed_meaning="人工确认的释义",
        reviewed_at=datetime(2026, 7, 23, 15, 0, 0),
    )

    second_batch = GroupLearningCandidateService(
        command_repository
    ).persist_rule_candidates(
        GroupLearningCandidateBatchRequest(
            run_id="glr_test_2",
            idempotency_key="group-learning:42:102:103",
            chat_stream_id=CHAT_STREAM_ID,
            trigger="manual",
            aspects=("slang",),
            cursor_start_chat_log_id=102,
            cursor_end_chat_log_id=103,
            context_start_chat_log_id=101,
            context_end_chat_log_id=102,
            messages=(
                GroupLearningMessage(
                    chat_log_id=103,
                    sender_id="u2",
                    content="划水的意思是暂时休息",
                ),
            ),
        )
    )
    assert second_batch.candidate_count == 1
    second_candidate_id = second_batch.candidate_ids[0]
    result = TaskResult(
        parsed_value={
            "reviews": [{
                "candidate_id": second_candidate_id,
                "action": "new",
                "candidate_type": "slang",
                "content": "划水",
                "meaning": "暂时休息",
                "evidence_log_ids": [103],
                "target_memory_id": None,
                "reason": "候选释义与消息一致",
            }],
            "discoveries": [{
                "candidate_type": "slang",
                "content": "摸鱼",
                "meaning": "上班时偷懒",
                "evidence_log_ids": [103],
                "reason": "模型重复发现了已有人工候选",
            }],
        },
        contract_version="group_memory_learning_v1",
        route_key="group_memory_learning",
        provider="test-provider",
        model="test-model",
        attempt_count=1,
        latency_ms=12,
        failure=None,
        raw_output_sha256="b" * 64,
        raw_output_bytes=120,
        validation_diagnostics=(),
        run_id="taskrun_reuse_human_candidate",
    )
    outcome = GroupLearningModelReviewService(
        query_repository=query_repository,
        command_repository=command_repository,
        group_memory_repository=SqlAlchemyGroupMemoryRepository(
            db_session
        ),
        task_executor=lambda _invocation: result,
    ).review(GroupLearningModelReviewRequest(
        run_id="glr_test_2",
        chat_stream_id=CHAT_STREAM_ID,
        aspects=("slang",),
        candidate_ids=(second_candidate_id,),
        messages=(
            GroupLearningMessage(
                chat_log_id=103,
                sender_id="u2",
                content="划水的意思是暂时休息，摸鱼也差不多",
            ),
        ),
    ))

    assert outcome.status == "observed"
    assert db_session.query(GroupLearningCandidate).count() == 2
    human = (
        db_session.query(GroupLearningCandidate)
        .filter(
            GroupLearningCandidate.candidate_id == "glc_test_1"
        )
        .one()
    )
    assert human.approval_source == "human"
    assert human.human_reviewer_id == "admin-1"
    assert human.reviewed_meaning == "人工确认的释义"
    assert human.source_run_id == "glr_test_1"
    assert human.model_review_run_id == ""
    assert (
        db_session.query(GroupLearningEvidence)
        .filter(
            GroupLearningEvidence.candidate_id == "glc_test_1"
        )
        .count()
        == 2
    )
    second_run = db_session.get(GroupLearningRun, "glr_test_2")
    assert second_run is not None
    assert second_run.candidate_count == 1


def test_model_review_service_never_sends_human_reviewed_candidate(
    db_session,
):
    from app.group_learning.review_service import (
        GroupLearningHumanReviewService,
        GroupLearningModelReviewService,
    )
    from core.db.group_learning_adapter import (
        SqlAlchemyGroupLearningQueryRepository,
    )
    from core.db.group_learning_command_adapter import (
        SqlAlchemyGroupLearningCommandRepository,
    )
    from core.db.group_memory_adapter import (
        SqlAlchemyGroupMemoryRepository,
    )

    command_repository = SqlAlchemyGroupLearningCommandRepository(
        db_session
    )
    command_repository.persist_candidate_batch(_batch_write())
    command_repository.commit()
    query_repository = SqlAlchemyGroupLearningQueryRepository(db_session)
    GroupLearningHumanReviewService(
        query_repository=query_repository,
        command_repository=command_repository,
    ).review(
        candidate_id="glc_test_1",
        reviewer_id="admin-1",
        action="edit_accept",
        reviewed_content="摸鱼",
        reviewed_meaning="人工确认的释义",
        reviewed_at=datetime(2026, 7, 23, 15, 0, 0),
    )

    called = False

    def forbidden_executor(_invocation):
        nonlocal called
        called = True
        raise AssertionError("人工审核后的候选不得送模型")

    service = GroupLearningModelReviewService(
        query_repository=query_repository,
        command_repository=command_repository,
        group_memory_repository=SqlAlchemyGroupMemoryRepository(
            db_session
        ),
        task_executor=forbidden_executor,
    )
    with pytest.raises(ValueError, match="人工审核"):
        service.review(_review_request())
    assert called is False


def test_stage7b_file_migration_is_idempotent_and_snapshots_before_alter(
    tmp_path,
):
    from sqlalchemy import create_engine, inspect

    from core.schema_migrations import MIGRATIONS, run_schema_migrations

    database_path = tmp_path / "legacy-stage7b.db"
    engine = create_engine(f"sqlite:///{database_path}")
    target_version = "20260723_group_learning_stage7b_review_fields"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE group_learning_candidates ("
            "id INTEGER PRIMARY KEY, "
            "candidate_id TEXT NOT NULL, "
            "status TEXT NOT NULL"
            ")"
        )
        connection.exec_driver_sql(
            "CREATE TABLE group_learning_runs ("
            "run_id TEXT PRIMARY KEY, "
            "status TEXT NOT NULL"
            ")"
        )
        connection.exec_driver_sql(
            "INSERT INTO group_learning_candidates"
            "(id, candidate_id, status) "
            "VALUES (1, 'glc_legacy', 'pending_model_review')"
        )
        connection.exec_driver_sql(
            "INSERT INTO group_learning_runs"
            "(run_id, status) VALUES ('glr_legacy', 'candidate_persisted')"
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
                "(version, name, applied_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (version, name),
            )

    run_schema_migrations(engine, db_path=str(database_path))
    run_schema_migrations(engine, db_path=str(database_path))

    backups = tuple(tmp_path.glob("legacy-stage7b.db.bak.*"))
    assert len(backups) == 1
    assert backups[0].stat().st_size > 0
    db_inspector = inspect(engine)
    assert {
        "model_observed_at",
        "observation_reason_hash",
        "reviewed_content",
        "reviewed_meaning",
        "reviewed_content_hash",
        "human_reviewer_id",
        "human_reviewed_at",
        "human_action",
    } <= {
        column["name"]
        for column in db_inspector.get_columns(
            "group_learning_candidates"
        )
    }
    assert {
        "mode",
        "task_run_id",
        "input_chars",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_microusd",
        "latency_ms",
        "attempt_count",
        "raw_output_bytes",
        "raw_output_sha256",
    } <= {
        column["name"]
        for column in db_inspector.get_columns("group_learning_runs")
    }
    with engine.connect() as connection:
        candidate = connection.exec_driver_sql(
            "SELECT candidate_id, status "
            "FROM group_learning_candidates WHERE id = 1"
        ).one()
        migration_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (target_version,),
        ).scalar_one()
    assert tuple(candidate) == ("glc_legacy", "pending_model_review")
    assert migration_count == 1


def test_stage7b_migration_is_registered_without_enabling_feature():
    from core.config_registry import SETTING_DEFS
    from core.lifecycle import FEATURE_LIFECYCLE_REGISTRY

    feature = FEATURE_LIFECYCLE_REGISTRY.require("group_learning")

    assert feature.data_migrations == (
        "20260723_group_learning_stage7a_schema",
        "20260723_group_learning_stage7b_review_fields",
        "20260724_group_learning_stage7c_schedule_fencing",
        "20260724_group_learning_stage7d_legacy_read_only",
    )
    assert feature.default_enabled is False
    assert SETTING_DEFS["group_learning.enabled"].default is False


def test_group_memory_learning_prompt_is_synced_and_candidate_only():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    canonical = (
        root
        / "prompts.v2.default"
        / "tasks"
        / "group_memory_learning.md"
    ).read_text(encoding="utf-8")
    runtime = (
        root
        / "data"
        / "prompts_v2"
        / "tasks"
        / "group_memory_learning.md"
    ).read_text(encoding="utf-8")

    assert runtime == canonical
    assert "不直接激活、删除或注入任何长期记忆" in canonical
    assert "后端还会独立校验会话范围、证据和治理策略" in canonical
