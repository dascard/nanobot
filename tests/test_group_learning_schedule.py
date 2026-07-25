"""阶段 7C：群学习白名单、增量游标与租约调度测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
import json

import pytest


CHAT_STREAM_ID = "qq:42:group"
OTHER_CHAT_STREAM_ID = "qq:43:group"
NOW = datetime(2026, 7, 24, 12, 0, 0)


def _service(db_session, *, enabled: bool = True):
    from app.group_learning.schedule_service import (
        GroupLearningScheduleService,
    )
    from core.db.group_learning_schedule_adapter import (
        SqlAlchemyGroupLearningScheduleRepository,
    )

    return GroupLearningScheduleService(
        repository=SqlAlchemyGroupLearningScheduleRepository(
            db_session
        ),
        enabled=lambda: enabled,
    )


def _message_meta(
    sender_id: str,
    *,
    is_bot: bool = False,
    no_learn: bool = False,
) -> str:
    return json.dumps(
        {
            "sender": {
                "id": sender_id,
                "name": sender_id,
                "is_bot": is_bot,
            },
            "no_learn": no_learn,
        },
        ensure_ascii=False,
    )


def _add_log(
    db_session,
    *,
    row_id: int,
    chat_stream_id: str = CHAT_STREAM_ID,
    sender_id: str = "u1",
    role: str = "ambient",
    content: str = "这是一条可分析的群聊消息",
    created_at: datetime = NOW,
    is_bot: bool = False,
    no_learn: bool = False,
):
    from core.db.models import ChatLog
    from foundation.identity import parse_canonical_chat_stream_id

    identity = parse_canonical_chat_stream_id(chat_stream_id)
    row = ChatLog(
        id=row_id,
        user_id=identity.legacy_runtime_session_id,
        session_id=identity.legacy_runtime_session_id,
        sender_name=sender_id,
        role=role,
        content=content,
        created_at=created_at,
        meta_json=_message_meta(
            sender_id,
            is_bot=is_bot,
            no_learn=no_learn,
        ),
    )
    db_session.add(row)
    return row


def test_schedule_create_uses_registry_defaults_and_update_increments_generation(
    db_session,
):
    service = _service(db_session)

    created = service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        now=NOW,
    )
    updated = service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        aspects=("style", "topics"),
        interval_minutes=60,
        window_hours=48,
        now=NOW + timedelta(minutes=1),
    )

    assert created.aspects == (
        "topics",
        "expressions",
        "slang",
        "style",
    )
    assert created.config_generation == 1
    assert created.next_run_at == NOW
    assert updated.aspects == ("topics", "style")
    assert updated.interval_minutes == 60
    assert updated.window_hours == 48
    assert updated.config_generation == 2


@pytest.mark.parametrize(
    ("chat_stream_id", "aspects", "interval_minutes", "window_hours"),
    [
        ("group_42", None, 1440, 24),
        ("qq:42:private", None, 1440, 24),
        (CHAT_STREAM_ID, ("unknown",), 1440, 24),
        (CHAT_STREAM_ID, None, 14, 24),
        (CHAT_STREAM_ID, None, 1440, 0),
    ],
)
def test_schedule_rejects_noncanonical_scope_and_out_of_policy_values(
    db_session,
    chat_stream_id,
    aspects,
    interval_minutes,
    window_hours,
):
    with pytest.raises(ValueError):
        _service(db_session).put_schedule(
            chat_stream_id=chat_stream_id,
            aspects=aspects,
            interval_minutes=interval_minutes,
            window_hours=window_hours,
            now=NOW,
        )


def test_schedule_row_is_the_only_whitelist_and_disable_is_versioned(
    db_session,
):
    service = _service(db_session)

    assert service.claim_due(worker_id="worker-a", now=NOW) is None

    created = service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        now=NOW,
    )
    disabled = service.disable_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        now=NOW,
    )

    assert created.enabled is True
    assert disabled.enabled is False
    assert disabled.config_generation == 2
    assert service.claim_due(worker_id="worker-a", now=NOW) is None


def test_global_kill_switch_prevents_claim_without_deleting_schedule(
    db_session,
):
    enabled_service = _service(db_session)
    enabled_service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        now=NOW,
    )

    disabled_service = _service(db_session, enabled=False)

    assert disabled_service.claim_due(
        worker_id="worker-a",
        now=NOW,
    ) is None
    assert disabled_service.get_schedule(CHAT_STREAM_ID) is not None


def test_due_claim_uses_durable_job_lease_and_fences_stale_worker(
    db_session,
):
    from app.group_learning.schedule_service import (
        GroupLearningScheduleLeaseLost,
    )

    service = _service(db_session)
    service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        now=NOW,
    )

    first = service.claim_due(worker_id="worker-a", now=NOW)
    assert first is not None
    assert first.lease.generation == 1
    assert first.lease.attempt_no == 1
    assert service.claim_due(
        worker_id="worker-b",
        now=NOW,
    ) is None

    second = service.claim_due(
        worker_id="worker-b",
        now=first.lease.expires_at,
    )
    assert second is not None
    assert second.lease.generation == 2
    assert second.lease.attempt_no == 2

    with pytest.raises(GroupLearningScheduleLeaseLost):
        service.settle_success(first, now=first.lease.expires_at)

    settled = service.settle_success(
        second,
        now=first.lease.expires_at,
    )
    assert settled.last_completed_at == first.lease.expires_at
    assert settled.next_run_at == (
        first.lease.expires_at
        + timedelta(minutes=settled.interval_minutes)
    )
    assert settled.consecutive_failures == 0


def test_group_learning_job_descriptor_and_adapter_use_port_adapter_mode(
    db_session,
):
    from bootstrap.job_adapters import build_job_lease_adapter_registry
    from core.jobs import (
        JOB_DESCRIPTOR_REGISTRY,
        JobLifecycle,
        JobRepositoryMode,
    )

    service = _service(db_session)
    service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        now=NOW,
    )
    claim = service.claim_due(worker_id="worker-a", now=NOW)
    assert claim is not None

    descriptor = JOB_DESCRIPTOR_REGISTRY.require(
        "group_memory_learning"
    )
    binding = build_job_lease_adapter_registry().require(
        "group_memory_learning"
    )

    assert descriptor.owner_module == "app.group_learning"
    assert descriptor.lifecycle is JobLifecycle.ACTIVE
    assert descriptor.repository_mode is JobRepositoryMode.PORT_ADAPTER
    assert binding.project_lease(claim) == claim.lease


def test_incremental_batch_uses_success_cursor_and_context_is_never_evidence(
    db_session,
):
    from core.db.models import GroupLearningStreamState

    for row_id in range(1, 21):
        _add_log(
            db_session,
            row_id=row_id,
            sender_id=f"context-{row_id}",
            created_at=NOW - timedelta(hours=2),
        )
    for row_id in range(21, 24):
        _add_log(
            db_session,
            row_id=row_id,
            sender_id=f"new-{row_id}",
            content=f"新消息 {row_id} 的意思是测试释义",
        )
    _add_log(
        db_session,
        row_id=24,
        chat_stream_id=OTHER_CHAT_STREAM_ID,
        sender_id="other",
    )
    _add_log(
        db_session,
        row_id=25,
        sender_id="bot",
        is_bot=True,
    )
    _add_log(
        db_session,
        row_id=26,
        sender_id="internal",
        no_learn=True,
    )
    db_session.add(
        GroupLearningStreamState(
            chat_stream_id=CHAT_STREAM_ID,
            last_scanned_chat_log_id=20,
            last_success_chat_log_id=20,
        )
    )
    db_session.commit()

    service = _service(db_session)
    service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        aspects=("expressions", "slang"),
        now=NOW,
    )
    claim = service.claim_due(worker_id="worker-a", now=NOW)
    assert claim is not None

    prepared = service.prepare_batch(claim, now=NOW)

    assert prepared.status == "ready"
    assert prepared.request is not None
    request = prepared.request
    assert request.cursor_start_chat_log_id == 20
    assert request.cursor_end_chat_log_id == 23
    assert tuple(
        message.chat_log_id
        for message in request.messages
        if message.context_only
    ) == tuple(range(1, 21))
    assert tuple(
        message.chat_log_id
        for message in request.messages
        if not message.context_only
    ) == (21, 22, 23)
    assert tuple(
        message.sender_id
        for message in request.messages
        if not message.context_only
    ) == ("new-21", "new-22", "new-23")


def test_less_than_three_new_messages_defers_without_advancing_cursor(
    db_session,
):
    from core.db.models import GroupLearningStreamState

    _add_log(db_session, row_id=1, sender_id="u1")
    _add_log(db_session, row_id=2, sender_id="u2")
    db_session.commit()
    service = _service(db_session)
    service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        now=NOW,
    )
    claim = service.claim_due(worker_id="worker-a", now=NOW)
    assert claim is not None

    prepared = service.prepare_batch(claim, now=NOW)

    assert prepared.status == "insufficient_messages"
    assert prepared.request is None
    assert db_session.get(
        GroupLearningStreamState,
        CHAT_STREAM_ID,
    ) is None


def test_initial_window_excludes_old_messages_without_using_overlap_cursor(
    db_session,
):
    _add_log(
        db_session,
        row_id=1,
        sender_id="old",
        created_at=NOW - timedelta(hours=25),
    )
    for row_id in range(2, 5):
        _add_log(
            db_session,
            row_id=row_id,
            sender_id=f"new-{row_id}",
            created_at=NOW - timedelta(hours=1),
        )
    db_session.commit()
    service = _service(db_session)
    service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        window_hours=24,
        now=NOW,
    )
    claim = service.claim_due(worker_id="worker-a", now=NOW)
    assert claim is not None

    prepared = service.prepare_batch(claim, now=NOW)

    assert prepared.status == "ready"
    assert prepared.request is not None
    assert [
        message.chat_log_id
        for message in prepared.request.messages
    ] == [2, 3, 4]
    assert prepared.request.cursor_start_chat_log_id == 0
    assert prepared.request.cursor_end_chat_log_id == 4


def test_stage7c_schedule_migration_is_idempotent(db_session):
    from sqlalchemy import inspect

    from core.database import engine
    from core.schema_migrations import run_schema_migrations

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    columns = {
        column["name"]
        for column in inspect(engine).get_columns(
            "group_learning_schedules"
        )
    }
    assert {"lease_generation", "attempt_count"} <= columns


def test_file_sqlite_concurrent_due_claim_has_one_fenced_owner(
    tmp_path,
):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from app.group_learning.schedule_service import (
        GroupLearningScheduleService,
    )
    from core.db.group_learning_schedule_adapter import (
        SqlAlchemyGroupLearningScheduleRepository,
    )
    from core.db.models import GroupLearningSchedule

    engine = create_engine(
        f"sqlite:///{tmp_path / 'group-learning-claim.db'}",
        connect_args={
            "check_same_thread": False,
            "timeout": 5,
        },
    )
    GroupLearningSchedule.__table__.create(engine)
    session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    seed_session = session_factory()
    try:
        GroupLearningScheduleService(
            repository=SqlAlchemyGroupLearningScheduleRepository(
                seed_session
            ),
            enabled=lambda: True,
        ).put_schedule(
            chat_stream_id=CHAT_STREAM_ID,
            now=NOW,
        )
    finally:
        seed_session.close()

    both_read_due_row = threading.Barrier(2)
    synchronized_threads: set[int] = set()
    synchronization_lock = threading.Lock()

    def synchronize_due_select(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        normalized = " ".join(statement.lower().split())
        if (
            "from group_learning_schedules" not in normalized
            or "enabled" not in normalized
            or "limit" not in normalized
        ):
            return
        thread_id = threading.get_ident()
        with synchronization_lock:
            if thread_id in synchronized_threads:
                return
            synchronized_threads.add(thread_id)
        both_read_due_row.wait(timeout=5)

    def claim(worker_id: str):
        session = session_factory()
        try:
            return GroupLearningScheduleService(
                repository=SqlAlchemyGroupLearningScheduleRepository(
                    session
                ),
                enabled=lambda: True,
            ).claim_due(worker_id=worker_id, now=NOW)
        finally:
            session.close()

    event.listen(
        engine,
        "after_cursor_execute",
        synchronize_due_select,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = [
                future.result(timeout=10)
                for future in (
                    pool.submit(claim, "worker-a"),
                    pool.submit(claim, "worker-b"),
                )
            ]
        winners = [item for item in claims if item is not None]
        assert len(winners) == 1
        verify = session_factory()
        try:
            row = verify.get(GroupLearningSchedule, CHAT_STREAM_ID)
            assert row is not None
            assert row.lease_owner == winners[0].lease.worker_id
            assert row.lease_token == winners[0].lease.owner_token
            assert row.lease_generation == 1
            assert row.attempt_count == 1
        finally:
            verify.close()
    finally:
        event.remove(
            engine,
            "after_cursor_execute",
            synchronize_due_select,
        )
        engine.dispose()


def test_scheduler_empty_whitelist_never_invokes_processor(db_session):
    from app.group_learning.scheduler import (
        GroupLearningScheduleRunner,
    )

    class Processor:
        calls = 0

        def process(self, request):
            del request
            self.calls += 1
            raise AssertionError("空白名单不得调用群学习处理器")

    processor = Processor()
    result = GroupLearningScheduleRunner(
        schedule_service=_service(db_session),
        processor=processor,
    ).run_once(worker_id="worker-a", now=NOW)

    assert result.status == "idle"
    assert processor.calls == 0


def test_scheduler_insufficient_batch_settles_without_model_call(
    db_session,
):
    from app.group_learning.scheduler import (
        GroupLearningScheduleRunner,
    )

    class Processor:
        calls = 0

        def process(self, request):
            del request
            self.calls += 1
            raise AssertionError("不足三条消息不得调用群学习处理器")

    _add_log(db_session, row_id=1, sender_id="u1")
    _add_log(db_session, row_id=2, sender_id="u2")
    db_session.commit()
    service = _service(db_session)
    service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        now=NOW,
    )
    processor = Processor()

    result = GroupLearningScheduleRunner(
        schedule_service=service,
        processor=processor,
    ).run_once(worker_id="worker-a", now=NOW)

    assert result.status == "insufficient_messages"
    assert processor.calls == 0
    schedule = service.get_schedule(CHAT_STREAM_ID)
    assert schedule is not None
    assert schedule.last_completed_at == NOW
    assert schedule.next_run_at == NOW + timedelta(days=1)


def test_scheduler_ready_batch_calls_shared_processor_and_settles(
    db_session,
):
    from app.group_learning.scheduler import (
        GroupLearningProcessingOutcome,
        GroupLearningScheduleRunner,
    )

    class Processor:
        requests = []

        def process(self, request):
            self.requests.append(request)
            return GroupLearningProcessingOutcome.succeeded(
                run_id=request.run_id
            )

    for row_id in range(1, 4):
        _add_log(
            db_session,
            row_id=row_id,
            sender_id=f"u{row_id}",
            content=f"术语{row_id}的意思是解释{row_id}",
        )
    db_session.commit()
    service = _service(db_session)
    service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        aspects=("slang",),
        now=NOW,
    )
    processor = Processor()

    result = GroupLearningScheduleRunner(
        schedule_service=service,
        processor=processor,
    ).run_once(worker_id="worker-a", now=NOW)

    assert result.status == "succeeded"
    assert result.run_id
    assert len(processor.requests) == 1
    assert processor.requests[0].aspects == ("slang",)
    schedule = service.get_schedule(CHAT_STREAM_ID)
    assert schedule is not None
    assert schedule.last_completed_at == NOW


def test_scheduler_typed_failure_records_retry_without_success_settle(
    db_session,
):
    from app.group_learning.scheduler import (
        GroupLearningProcessingOutcome,
        GroupLearningScheduleRunner,
    )

    class Processor:
        def process(self, request):
            return GroupLearningProcessingOutcome.failed(
                run_id=request.run_id,
                error_code="provider_unavailable",
                retryable=True,
            )

    for row_id in range(1, 4):
        _add_log(
            db_session,
            row_id=row_id,
            sender_id=f"u{row_id}",
        )
    db_session.commit()
    service = _service(db_session)
    service.put_schedule(
        chat_stream_id=CHAT_STREAM_ID,
        now=NOW,
    )

    result = GroupLearningScheduleRunner(
        schedule_service=service,
        processor=Processor(),
    ).run_once(worker_id="worker-a", now=NOW)

    assert result.status == "failed"
    assert result.error_code == "provider_unavailable"
    schedule = service.get_schedule(CHAT_STREAM_ID)
    assert schedule is not None
    assert schedule.last_completed_at is None
    assert schedule.last_error_code == "provider_unavailable"
    assert schedule.consecutive_failures == 1
    assert schedule.next_run_at is not None
    assert schedule.next_run_at > NOW
