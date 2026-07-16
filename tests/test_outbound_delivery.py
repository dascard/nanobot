from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import core.outbound_delivery as outbound_delivery_state
from core.database import (
    Base,
    OutboundDeliveryAttempt,
    OutboundDeliveryCircuit,
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
    ProactiveOutreachLog,
)
from core.outbound_delivery import (
    InvalidOutboundTransitionError,
    OutboundConflictError,
    OutboundFencingError,
    OutboundSafetyError,
    acquire_or_renew_delivery_writer,
    cancel_delivery_before_send,
    claim_due_outbox,
    claim_outbound_run,
    commit_generated_outbox,
    create_delivery_replay,
    destination_circuit_fingerprint,
    endpoint_circuit_fingerprint,
    expire_stale_delivery_leases,
    fail_outbound_generation,
    mark_delivery_request_started,
    payload_contract_circuit_fingerprint,
    renew_outbound_run_claim,
    reset_delivery_circuit,
    settle_delivery_attempt,
    start_generation_attempt,
    transition_delivery_control,
)


NOW = datetime(2026, 7, 14, 12, 0, 0)
SOURCE_TYPE = "scheduled_task"
ENDPOINT_KEY = "qq_push"
DESTINATION_FINGERPRINT = "destination-fingerprint"
PAYLOAD_CONTRACT_FINGERPRINT = "qq-envelope-v1"


def _canonical_json(value: dict) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_control(
    db,
    *,
    mode: str = "outbox_active",
    epoch: int = 7,
    effective_from: datetime | None = None,
    protocol_version: int = 2,
    writer_owner: str | None = None,
    writer_token: str | None = None,
    writer_lease_expires_at: datetime | None = None,
) -> OutboundDeliveryControl:
    row = OutboundDeliveryControl(
        source_type=SOURCE_TYPE,
        mode=mode,
        cutover_epoch=epoch,
        effective_from=effective_from or NOW - timedelta(hours=1),
        protocol_version=protocol_version,
        writer_version=0,
        writer_owner=writer_owner,
        writer_token=writer_token,
        writer_lease_expires_at=writer_lease_expires_at,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
    )
    db.add(row)
    db.flush()
    return row


def _claim_run(
    db,
    *,
    source_id: str = "task-1",
    occurrence_key: str = "slot-2026-07-14T12:00",
    source_revision: str = "revision-1",
    source_snapshot: dict | None = None,
    owner: str = "producer-a",
    writer_owner: str = "producer-a",
    writer_token: str = "writer-a",
    endpoint_config_revision: str = "qq-config-r1",
    claim_lease_seconds: int = 60,
    writer_lease_seconds: int = 60,
    scheduled_for: datetime | None = None,
    now: datetime = NOW,
):
    return claim_outbound_run(
        db,
        source_type=SOURCE_TYPE,
        source_id=source_id,
        occurrence_key=occurrence_key,
        source_revision=source_revision,
        source_snapshot=source_snapshot or {
            "target_type": "private",
            "target_id": "opaque-user",
            "prompt": "生成日报",
        },
        destination_snapshot={
            "target_type": "private",
            "target_id": "opaque-user",
        },
        target_type="private",
        task_kind="ai_digest",
        scheduled_for=scheduled_for or NOW,
        trigger_type="cron",
        owner=owner,
        claim_lease_seconds=claim_lease_seconds,
        writer_owner=writer_owner,
        writer_token=writer_token,
        writer_protocol_version=2,
        writer_lease_seconds=writer_lease_seconds,
        endpoint_key=ENDPOINT_KEY,
        destination_fingerprint=DESTINATION_FINGERPRINT,
        endpoint_config_revision=endpoint_config_revision,
        payload_contract_fingerprint=PAYLOAD_CONTRACT_FINGERPRINT,
        now=now,
    )


def _start_generation(
    db,
    claim,
    *,
    endpoint_config_revision: str = "qq-config-r1",
    now: datetime = NOW,
):
    return start_generation_attempt(
        db,
        run_id=claim.run_id,
        owner=claim.owner,
        claim_token=claim.claim_token,
        writer_owner="producer-a",
        writer_token="writer-a",
        writer_protocol_version=2,
        endpoint_key=ENDPOINT_KEY,
        destination_fingerprint=DESTINATION_FINGERPRINT,
        endpoint_config_revision=endpoint_config_revision,
        payload_contract_fingerprint=PAYLOAD_CONTRACT_FINGERPRINT,
        now=now,
    )


def _commit_outbox(
    db,
    claim,
    generation,
    *,
    idempotency_key: str = "delivery-task-1-slot-1",
    payload: dict | None = None,
    destination_snapshot: dict | None = None,
    endpoint_config_revision: str = "qq-config-r1",
    max_attempts: int = 3,
    generation_error_type: str = "",
    generation_error_summary: str = "",
    now: datetime = NOW,
):
    return commit_generated_outbox(
        db,
        run_id=claim.run_id,
        generation_attempt_id=generation.attempt_id,
        owner=claim.owner,
        claim_token=claim.claim_token,
        idempotency_key=idempotency_key,
        destination_snapshot=destination_snapshot or {
            "target_type": "private",
            "target_id": "opaque-user",
        },
        destination_fingerprint=DESTINATION_FINGERPRINT,
        target_type="private",
        endpoint_key=ENDPOINT_KEY,
        payload=payload or {"content": "已生成但尚未投递"},
        max_attempts=max_attempts,
        retry_deadline_at=now + timedelta(hours=1),
        endpoint_config_revision=endpoint_config_revision,
        payload_contract_fingerprint=PAYLOAD_CONTRACT_FINGERPRINT,
        model_trace_id="trace-1",
        generation_error_type=generation_error_type,
        generation_error_summary=generation_error_summary,
        now=now,
    )


def _queue_outbox(
    db,
    *,
    source_id: str = "task-1",
    occurrence_key: str = "slot-2026-07-14T12:00",
    idempotency_key: str = "delivery-task-1-slot-1",
    max_attempts: int = 3,
    endpoint_config_revision: str = "qq-config-r1",
):
    claim = _claim_run(
        db,
        source_id=source_id,
        occurrence_key=occurrence_key,
        endpoint_config_revision=endpoint_config_revision,
    )
    generation = _start_generation(
        db,
        claim,
        endpoint_config_revision=endpoint_config_revision,
    )
    outbox = _commit_outbox(
        db,
        claim,
        generation,
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        endpoint_config_revision=endpoint_config_revision,
    )
    return claim, generation, outbox


def _claim_legacy_outbox(db, outbox_id: int, *, now: datetime):
    return outbound_delivery_state.claim_legacy_direct_outbox(
        db,
        outbox_id=outbox_id,
        worker_owner="legacy-worker-a",
        lease_seconds=30,
        writer_owner="producer-a",
        writer_token="writer-a",
        writer_protocol_version=2,
        writer_lease_seconds=3600,
        endpoint_key=ENDPOINT_KEY,
        endpoint_config_revision="qq-config-r1",
        now=now,
    )


def _prepare_outbox_state(db, state: str) -> OutboundDeliveryOutbox:
    _seed_control(db)
    _claim, _generation, queued = _queue_outbox(db)
    if state == "pending":
        row = db.get(OutboundDeliveryOutbox, queued.outbox_id)
        assert row is not None
        return row
    if state == "blocked":
        db.add(OutboundDeliveryCircuit(
            scope_type="endpoint",
            scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
            config_revision="qq-config-r1",
            status="open",
            reason_type="unauthorized",
            opened_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        ))
        db.flush()
        assert claim_due_outbox(
            db,
            worker_owner="worker-a",
            lease_seconds=30,
            endpoint_config_revision="qq-config-r1",
            now=NOW + timedelta(seconds=1),
        ) is None
        db.expire_all()
        row = db.get(OutboundDeliveryOutbox, queued.outbox_id)
        assert row is not None and row.status == "blocked"
        return row

    delivery = claim_due_outbox(
        db,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert delivery is not None
    if state == "leased":
        row = db.get(OutboundDeliveryOutbox, queued.outbox_id)
        assert row is not None
        return row

    mark_delivery_request_started(
        db,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=2),
    )
    outcome = {
        "retry_wait": {
            "outcome": "transient_failure",
            "transport_phase": "response_received",
            "http_status": 503,
            "result_category": "transient",
            "error_type": "service_unavailable",
            "retry_at": NOW + timedelta(minutes=10),
        },
        "ambiguous": {
            "outcome": "ambiguous",
            "transport_phase": "read",
            "http_status": None,
            "result_category": "ambiguous",
            "error_type": "read_timeout",
            "retry_at": None,
        },
        "delivered": {
            "outcome": "succeeded",
            "transport_phase": "response_received",
            "http_status": 204,
            "result_category": "success",
            "error_type": "",
            "retry_at": None,
        },
    }[state]
    settle_delivery_attempt(
        db,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        safe_summary="",
        duration_ms=10,
        now=NOW + timedelta(seconds=3),
        **outcome,
    )
    db.expire_all()
    row = db.get(OutboundDeliveryOutbox, queued.outbox_id)
    assert row is not None and row.status == state
    return row


def _file_session_factory(tmp_path, name: str):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=5000")

    Base.metadata.create_all(engine)
    return engine, sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


def _is_source_control_lock(statement: str) -> bool:
    compact = "".join(statement.lower().split())
    return (
        compact.startswith("updateoutbound_delivery_controlsset")
        and "writer_version=outbound_delivery_controls.writer_version" in compact
    )


def _run_forced_source_lock_order(
    engine,
    *,
    first_name: str,
    first_operation,
    second_name: str,
    second_operation,
):
    first_locked = threading.Event()
    release_first = threading.Event()
    second_attempted = threading.Event()
    first_paused = False
    pause_guard = threading.Lock()
    results = {}
    errors = {}

    def after_cursor_execute(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        nonlocal first_paused
        if (
            threading.current_thread().name != first_name
            or not _is_source_control_lock(statement)
        ):
            return
        with pause_guard:
            if first_paused:
                return
            first_paused = True
        first_locked.set()
        if not release_first.wait(timeout=10):
            raise TimeoutError("第一方持有 source-control 写锁后未获释放")

    def before_cursor_execute(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        if (
            threading.current_thread().name == second_name
            and _is_source_control_lock(statement)
        ):
            second_attempted.set()

    def run(name, operation):
        try:
            results[name] = operation()
        except BaseException as exc:  # 测试线程必须把异常传回主线程
            errors[name] = exc

    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    first_thread = threading.Thread(
        target=run,
        args=(first_name, first_operation),
        name=first_name,
    )
    second_thread = threading.Thread(
        target=run,
        args=(second_name, second_operation),
        name=second_name,
    )
    try:
        first_thread.start()
        assert first_locked.wait(timeout=10)
        second_thread.start()
        assert second_attempted.wait(timeout=10)
        release_first.set()
        first_thread.join(timeout=10)
        second_thread.join(timeout=10)
        assert not first_thread.is_alive()
        assert not second_thread.is_alive()
        if errors:
            raise next(iter(errors.values()))
        return results
    finally:
        release_first.set()
        if first_thread.ident is not None:
            first_thread.join(timeout=1)
        if second_thread.ident is not None:
            second_thread.join(timeout=1)
        event.remove(engine, "after_cursor_execute", after_cursor_execute)
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


def test_occurrence_claim_freezes_first_snapshot_and_is_idempotent(db_session):
    _seed_control(db_session)
    first_snapshot = {
        "target_type": "private",
        "target_id": "first-target",
        "prompt": "first-prompt",
    }
    first = _claim_run(db_session, source_snapshot=first_snapshot)
    second = _claim_run(
        db_session,
        source_revision="revision-edited",
        source_snapshot={
            "target_type": "group",
            "target_id": "edited-target",
            "prompt": "edited-prompt",
        },
        owner="producer-b",
        writer_owner="producer-b",
        writer_token="writer-b",
    )

    assert first.acquired is True
    assert second.acquired is False
    assert second.run_id == first.run_id
    stored = db_session.get(OutboundRun, first.run_id)
    assert stored is not None
    assert stored.source_revision == "revision-1"
    assert stored.source_snapshot_json == _canonical_json(first_snapshot)
    assert stored.source_snapshot_sha256 == _sha256(stored.source_snapshot_json)
    assert db_session.query(OutboundRun).count() == 1


def test_generation_takeover_is_monotonic_and_stale_owner_is_fenced(db_session):
    _seed_control(db_session)
    first = _claim_run(db_session)
    attempt_one = _start_generation(db_session, first)
    db_session.commit()

    takeover_time = NOW + timedelta(seconds=61)
    second = _claim_run(
        db_session,
        owner="producer-b",
        writer_owner="producer-b",
        writer_token="writer-b",
        now=takeover_time,
    )
    assert second.acquired is True
    assert second.claim_token != first.claim_token
    attempt_two = start_generation_attempt(
        db_session,
        run_id=second.run_id,
        owner=second.owner,
        claim_token=second.claim_token,
        writer_owner="producer-b",
        writer_token="writer-b",
        writer_protocol_version=2,
        endpoint_key=ENDPOINT_KEY,
        destination_fingerprint=DESTINATION_FINGERPRINT,
        endpoint_config_revision="qq-config-r1",
        payload_contract_fingerprint=PAYLOAD_CONTRACT_FINGERPRINT,
        now=takeover_time,
    )

    with pytest.raises(OutboundFencingError):
        fail_outbound_generation(
            db_session,
            run_id=first.run_id,
            generation_attempt_id=attempt_one.attempt_id,
            owner=first.owner,
            claim_token=first.claim_token,
            error_type="late_failure",
            error_summary="旧 owner 不得覆盖",
            now=takeover_time,
        )

    rows = (
        db_session.query(OutboundGenerationAttempt)
        .order_by(OutboundGenerationAttempt.attempt_no)
        .all()
    )
    assert [row.attempt_no for row in rows] == [1, 2]
    assert rows[0].status == "abandoned"
    assert rows[1].id == attempt_two.attempt_id


def test_start_generation_rechecks_control_and_circuit(db_session):
    _seed_control(db_session)
    claim = _claim_run(db_session)
    db_session.add(OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
        config_revision="qq-config-r1",
        status="open",
        reason_type="unauthorized",
        opened_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    ))
    db_session.flush()

    result = _start_generation(db_session, claim)
    assert result.status == "blocked"
    assert result.attempt_id is None

    db_session.expire_all()
    run = db_session.get(OutboundRun, claim.run_id)
    assert run is not None
    assert run.status == "blocked"
    assert run.claim_owner is None
    assert db_session.query(OutboundGenerationAttempt).count() == 0


def test_start_generation_blocks_run_when_control_changes_after_claim(db_session):
    _seed_control(db_session)
    claim = _claim_run(db_session)
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None
    control.mode = "outbox_draining"
    control.updated_at = NOW + timedelta(seconds=1)
    db_session.flush()

    result = _start_generation(db_session, claim, now=NOW + timedelta(seconds=2))
    assert result.status == "blocked"
    assert result.attempt_id is None

    db_session.expire_all()
    run = db_session.get(OutboundRun, claim.run_id)
    assert run is not None
    assert run.status == "blocked"
    assert run.claim_owner is None
    assert run.claim_token is None
    assert db_session.query(OutboundGenerationAttempt).count() == 0


def test_run_claim_can_be_renewed_only_by_live_fence(db_session):
    _seed_control(db_session)
    claim = _claim_run(db_session)

    renewed = renew_outbound_run_claim(
        db_session,
        run_id=claim.run_id,
        owner=claim.owner,
        claim_token=claim.claim_token,
        lease_seconds=120,
        now=NOW + timedelta(seconds=30),
    )
    assert renewed.applied is True
    assert renewed.claim_expires_at == NOW + timedelta(seconds=150)

    stale = renew_outbound_run_claim(
        db_session,
        run_id=claim.run_id,
        owner=claim.owner,
        claim_token="stale-token",
        lease_seconds=120,
        now=NOW + timedelta(seconds=31),
    )
    assert stale.applied is False


def test_generated_outbox_commit_is_atomic_idempotent_and_conflict_safe(db_session):
    _seed_control(db_session)
    claim = _claim_run(db_session)
    generation = _start_generation(db_session, claim)
    first = _commit_outbox(db_session, claim, generation)
    same = _commit_outbox(db_session, claim, generation)

    assert first.created is True
    assert same.created is False
    assert same.outbox_id == first.outbox_id
    with pytest.raises(OutboundConflictError):
        _commit_outbox(
            db_session,
            claim,
            generation,
            payload={"content": "相同 key 的冲突正文"},
        )

    db_session.expire_all()
    run = db_session.get(OutboundRun, claim.run_id)
    attempt = db_session.get(OutboundGenerationAttempt, generation.attempt_id)
    assert run is not None and run.active_outbox_id == first.outbox_id
    assert run.status == "queued"
    assert attempt is not None and attempt.status == "succeeded"
    assert db_session.query(OutboundDeliveryOutbox).count() == 1


def test_generated_outbox_idempotency_includes_generation_settlement(db_session):
    _seed_control(db_session)
    claim = _claim_run(db_session)
    generation = _start_generation(db_session, claim)
    first = _commit_outbox(
        db_session,
        claim,
        generation,
        generation_error_type="model_truncated",
        generation_error_summary="模型输出被截断",
    )
    same = _commit_outbox(
        db_session,
        claim,
        generation,
        generation_error_type="model_truncated",
        generation_error_summary="模型输出被截断",
    )

    assert first.created is True
    assert same.created is False
    with pytest.raises(OutboundConflictError):
        _commit_outbox(
            db_session,
            claim,
            generation,
            generation_error_type="contract_error",
            generation_error_summary="模型输出被截断",
        )
    with pytest.raises(OutboundConflictError):
        _commit_outbox(
            db_session,
            claim,
            generation,
            generation_error_type="model_truncated",
            generation_error_summary="不同的安全摘要",
        )
    with pytest.raises(OutboundConflictError):
        _commit_outbox(db_session, claim, generation)


def test_generated_source_revision_freezes_marker_but_allows_projection_outputs(
    db_session,
):
    grounding = outbound_delivery_state.prepare_proactive_generation_grounding(
        grounding={"user_id": "generated-source-user", "recent_messages": []},
        kind="forced",
        judge={
            "should_reach_out": True,
            "reason": "超过最长沉默窗口",
            "next_check_at": (NOW + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
    )
    row = ProactiveOutreachLog(
        user_id="generated-source-user",
        idempotency_key="generated-source-key",
        grounding_json=json.dumps(grounding, ensure_ascii=False),
        judge_should=True,
        judge_reason="超过最长沉默窗口",
        next_check_at=NOW + timedelta(hours=2),
        next_intent="",
        message="",
        status="candidate",
        forced=True,
        created_at=NOW,
    )
    db_session.add(row)
    db_session.flush()
    original = outbound_delivery_state.proactive_outreach_generated_source_revision(
        row
    )

    dynamic_grounding = json.loads(row.grounding_json)
    dynamic_grounding["forced_fallback"] = {
        "error_type": "model_truncated",
        "reason": "模型输出被截断",
    }
    row.grounding_json = json.dumps(dynamic_grounding, ensure_ascii=False)
    row.message = "服务端安全兜底正文"
    row.judge_reason = "超过最长沉默窗口；使用服务端安全兜底"
    row.next_intent = "动态展示字段"
    row.forced = False
    assert (
        outbound_delivery_state.proactive_outreach_generated_source_revision(row)
        == original
    )

    row.created_at = NOW + timedelta(seconds=1)
    assert (
        outbound_delivery_state.proactive_outreach_generated_source_revision(row)
        != original
    )


@pytest.mark.parametrize("safety_change", ["control", "circuit"])
def test_generated_outbox_commit_rechecks_safety_after_model_call(
    db_session,
    safety_change,
):
    _seed_control(db_session)
    claim = _claim_run(db_session)
    generation = _start_generation(db_session, claim)
    if safety_change == "control":
        control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
        assert control is not None
        control.mode = "outbox_draining"
        control.updated_at = NOW + timedelta(seconds=1)
    else:
        db_session.add(OutboundDeliveryCircuit(
            scope_type="endpoint",
            scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
            config_revision="qq-config-r1",
            status="open",
            reason_type="unauthorized",
            opened_at=NOW + timedelta(seconds=1),
            created_at=NOW + timedelta(seconds=1),
            updated_at=NOW + timedelta(seconds=1),
        ))
    db_session.flush()

    result = _commit_outbox(
        db_session,
        claim,
        generation,
        now=NOW + timedelta(seconds=2),
    )
    assert result.status == "blocked"
    assert result.outbox_id is None

    db_session.expire_all()
    run = db_session.get(OutboundRun, claim.run_id)
    attempt = db_session.get(OutboundGenerationAttempt, generation.attempt_id)
    assert run is not None and run.status == "blocked"
    assert run.claim_owner is None and run.claim_token is None
    assert attempt is not None and attempt.status == "abandoned"
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_state_machine_never_commits_caller_transaction(tmp_path):
    engine, factory = _file_session_factory(tmp_path, "no-implicit-commit.db")
    try:
        with factory() as setup:
            _seed_control(setup)
            setup.commit()

        with factory() as writer:
            claim = _claim_run(writer)
            assert claim.acquired is True
            with factory() as observer:
                assert observer.query(OutboundRun).count() == 0
            writer.rollback()

        with factory() as observer:
            assert observer.query(OutboundRun).count() == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_concurrent_occurrence_claim_has_one_owner_and_one_snapshot(tmp_path):
    engine, factory = _file_session_factory(tmp_path, "occurrence-race.db")
    try:
        with factory() as setup:
            _seed_control(setup)
            setup.commit()
        barrier = threading.Barrier(2)

        def claim_from(owner: str):
            with factory() as db:
                barrier.wait(timeout=5)
                result = _claim_run(
                    db,
                    source_revision=f"revision-{owner}",
                    source_snapshot={"winner": owner},
                    owner=owner,
                    writer_owner=owner,
                    writer_token=f"writer-{owner}",
                )
                db.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim_from, ("a", "b")))

        assert sum(result.acquired for result in results) == 1
        assert len({result.run_id for result in results}) == 1
        with factory() as verify:
            rows = verify.query(OutboundRun).all()
            assert len(rows) == 1
            assert json.loads(rows[0].source_snapshot_json)["winner"] in {"a", "b"}
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_two_workers_concurrently_claim_one_due_outbox(tmp_path):
    engine, factory = _file_session_factory(tmp_path, "delivery-claim-race.db")
    try:
        with factory() as setup:
            _seed_control(setup)
            _queue_outbox(setup)
            setup.commit()
        barrier = threading.Barrier(2)

        def claim_from(owner: str):
            with factory() as db:
                barrier.wait(timeout=5)
                result = claim_due_outbox(
                    db,
                    worker_owner=owner,
                    lease_seconds=30,
                    endpoint_config_revision="qq-config-r1",
                    now=NOW + timedelta(seconds=1),
                )
                db.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim_from, ("worker-a", "worker-b")))

        winners = [result for result in results if result is not None]
        assert len(winners) == 1
        with factory() as verify:
            outbox = verify.query(OutboundDeliveryOutbox).one()
            attempts = verify.query(OutboundDeliveryAttempt).all()
            assert outbox.allocated_attempt_count == 1
            assert len(attempts) == 1
            assert attempts[0].worker_owner == winners[0].worker_owner
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_delivery_attempt_allocates_before_request_and_boundary_is_idempotent(db_session):
    _seed_control(db_session)
    claim, _generation, outbox_result = _queue_outbox(db_session)

    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(seconds=1),
    )
    assert delivery is not None
    assert delivery.outbox_id == outbox_result.outbox_id
    outbox = db_session.get(OutboundDeliveryOutbox, delivery.outbox_id)
    attempt = db_session.get(OutboundDeliveryAttempt, delivery.attempt_id)
    assert outbox is not None and outbox.allocated_attempt_count == 1
    assert outbox.request_started_count == 0
    assert attempt is not None and attempt.attempt_no == 1
    assert attempt.endpoint_config_revision == "qq-config-r2"
    assert attempt.request_started is False

    first = mark_delivery_request_started(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=2),
    )
    second = mark_delivery_request_started(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=3),
    )
    assert first.applied is True
    assert second.applied is False
    db_session.expire_all()
    assert db_session.get(OutboundDeliveryOutbox, delivery.outbox_id).request_started_count == 1


def test_before_send_expiry_abandons_without_consuming_network_budget(db_session):
    _seed_control(db_session)
    _claim, _generation, outbox_result = _queue_outbox(
        db_session,
        max_attempts=1,
    )
    first = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=10,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert first is not None

    summary = expire_stale_delivery_leases(
        db_session,
        now=NOW + timedelta(seconds=11),
    )
    assert summary.abandoned_before_send == 1
    assert summary.ambiguous == 0
    second = claim_due_outbox(
        db_session,
        worker_owner="worker-b",
        lease_seconds=10,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=12),
    )
    assert second is not None
    assert second.attempt_no == 2
    outbox = db_session.get(OutboundDeliveryOutbox, outbox_result.outbox_id)
    assert outbox is not None
    assert outbox.allocated_attempt_count == 2
    assert outbox.request_started_count == 0
    old_attempt = db_session.get(OutboundDeliveryAttempt, first.attempt_id)
    assert old_attempt is not None and old_attempt.status == "abandoned_before_send"


def test_after_request_boundary_expiry_is_ambiguous_and_never_auto_reclaimed(db_session):
    _seed_control(db_session)
    claim, _generation, outbox_result = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=10,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=2),
    )

    summary = expire_stale_delivery_leases(
        db_session,
        now=NOW + timedelta(seconds=11),
    )
    assert summary.ambiguous == 1
    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, outbox_result.outbox_id)
    run = db_session.get(OutboundRun, claim.run_id)
    assert outbox is not None and outbox.status == "ambiguous"
    assert outbox.next_attempt_at is None
    assert run is not None and run.status == "ambiguous"
    assert run.has_ambiguous_ancestor is True
    assert claim_due_outbox(
        db_session,
        worker_owner="worker-b",
        lease_seconds=10,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(minutes=5),
    ) is None


def test_transient_failure_uses_request_budget_and_preserves_payload(db_session):
    _seed_control(db_session)
    claim, _generation, outbox_result = _queue_outbox(
        db_session,
        max_attempts=1,
    )
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=2),
    )
    result = settle_delivery_attempt(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        outcome="transient_failure",
        transport_phase="response_received",
        http_status=503,
        result_category="transient",
        error_type="upstream_unavailable",
        safe_summary="上游暂时不可用",
        duration_ms=20,
        retry_at=NOW + timedelta(minutes=1),
        now=NOW + timedelta(seconds=3),
    )
    assert result.applied is True
    assert result.outbox_status == "failed"
    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, outbox_result.outbox_id)
    run = db_session.get(OutboundRun, claim.run_id)
    assert outbox is not None and outbox.request_started_count == 1
    assert outbox.payload_json == _canonical_json({"content": "已生成但尚未投递"})
    assert run is not None and run.status == "failed"


def test_stale_delivery_token_cannot_mutate_attempt_run_or_circuit(db_session):
    _seed_control(db_session)
    claim, _generation, outbox_result = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=5,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert delivery is not None

    with pytest.raises(OutboundFencingError):
        settle_delivery_attempt(
            db_session,
            outbox_id=delivery.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            outcome="permanent_failure",
            transport_phase="response_received",
            http_status=401,
            result_category="endpoint",
            error_type="unauthorized",
            safe_summary="迟到的旧结算",
            duration_ms=10,
            circuit_scope_type="endpoint",
            now=NOW + timedelta(seconds=6),
        )

    db_session.expire_all()
    assert db_session.get(OutboundDeliveryOutbox, outbox_result.outbox_id).status == "leased"
    assert db_session.get(OutboundRun, claim.run_id).status == "delivering"
    assert db_session.query(OutboundDeliveryCircuit).count() == 0


def test_terminal_delivery_settlement_retry_is_read_only(db_session):
    _seed_control(db_session)
    claim, _generation, outbox_result = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=2),
    )
    first = settle_delivery_attempt(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        outcome="succeeded",
        transport_phase="response_received",
        http_status=204,
        result_category="success",
        error_type="",
        safe_summary="",
        duration_ms=10,
        now=NOW + timedelta(seconds=3),
    )
    second = settle_delivery_attempt(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        outcome="succeeded",
        transport_phase="response_received",
        http_status=204,
        result_category="success",
        error_type="",
        safe_summary="",
        duration_ms=10,
        now=NOW + timedelta(seconds=4),
    )
    assert first.applied is True
    assert second.applied is False
    assert second.outbox_status == "delivered"
    assert db_session.get(OutboundDeliveryOutbox, outbox_result.outbox_id).status == "delivered"
    assert db_session.get(OutboundRun, claim.run_id).status == "succeeded"
    assert db_session.query(OutboundDeliveryAttempt).count() == 1


def test_terminal_settlement_retry_survives_circuit_reset(db_session):
    _seed_control(db_session)
    claim, _generation, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=2),
    )
    settlement = {
        "outbox_id": delivery.outbox_id,
        "attempt_id": delivery.attempt_id,
        "worker_owner": delivery.worker_owner,
        "lease_token": delivery.lease_token,
        "outcome": "permanent_failure",
        "transport_phase": "response_received",
        "http_status": 401,
        "result_category": "endpoint",
        "error_type": "unauthorized",
        "safe_summary": "旧配置鉴权失败",
        "duration_ms": 15,
        "circuit_scope_type": "endpoint",
    }
    first = settle_delivery_attempt(
        db_session,
        **settlement,
        now=NOW + timedelta(seconds=3),
    )
    assert first.applied is True
    circuit = db_session.query(OutboundDeliveryCircuit).one()
    reset = reset_delivery_circuit(
        db_session,
        scope_type="endpoint",
        scope_fingerprint=str(circuit.scope_fingerprint),
        config_revision=str(circuit.config_revision),
        expected_updated_at=circuit.updated_at,
        now=NOW + timedelta(seconds=4),
    )
    assert reset.applied is True

    repeated = settle_delivery_attempt(
        db_session,
        **settlement,
        now=NOW + timedelta(seconds=5),
    )

    assert repeated.applied is False
    assert repeated.outbox_status == "failed"
    assert repeated.run_status == "blocked"
    assert db_session.get(OutboundDeliveryOutbox, queued.outbox_id).status == "failed"
    assert db_session.get(OutboundRun, claim.run_id).status == "blocked"
    assert db_session.query(OutboundDeliveryAttempt).count() == 1
    assert db_session.query(OutboundDeliveryCircuit).one().status == "closed"


def test_circuit_is_scoped_by_actual_attempt_revision(db_session):
    _seed_control(db_session)
    _claim, _generation, first_outbox = _queue_outbox(db_session)
    first = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert first is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=first.outbox_id,
        attempt_id=first.attempt_id,
        worker_owner=first.worker_owner,
        lease_token=first.lease_token,
        now=NOW + timedelta(seconds=2),
    )
    settle_delivery_attempt(
        db_session,
        outbox_id=first.outbox_id,
        attempt_id=first.attempt_id,
        worker_owner=first.worker_owner,
        lease_token=first.lease_token,
        outcome="permanent_failure",
        transport_phase="response_received",
        http_status=401,
        result_category="endpoint",
        error_type="unauthorized",
        safe_summary="旧配置鉴权失败",
        duration_ms=15,
        circuit_scope_type="endpoint",
        now=NOW + timedelta(seconds=3),
    )
    circuit = db_session.query(OutboundDeliveryCircuit).one()
    assert circuit.scope_fingerprint == endpoint_circuit_fingerprint(ENDPOINT_KEY)
    assert circuit.config_revision == "qq-config-r1"

    _queue_outbox(
        db_session,
        source_id="task-2",
        occurrence_key="slot-task-2",
        idempotency_key="delivery-task-2",
        endpoint_config_revision="qq-config-r2",
    )
    second = claim_due_outbox(
        db_session,
        worker_owner="worker-b",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(seconds=4),
    )
    assert second is not None
    assert second.outbox_id != first_outbox.outbox_id


def test_blocked_occurrence_can_resume_with_new_endpoint_config_revision(
    db_session,
):
    _seed_control(db_session)
    db_session.add(OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
        config_revision="qq-config-r1",
        status="open",
        reason_type="unauthorized",
        opened_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    ))
    db_session.flush()

    blocked = _claim_run(
        db_session,
        source_id="task-revision-recovery",
        occurrence_key="slot-revision-recovery",
        endpoint_config_revision="qq-config-r1",
    )
    assert blocked.acquired is False
    assert blocked.status == "blocked"

    resumed = _claim_run(
        db_session,
        source_id="task-revision-recovery",
        occurrence_key="slot-revision-recovery",
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(seconds=1),
    )
    assert resumed.acquired is True
    generation = _start_generation(
        db_session,
        resumed,
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(seconds=2),
    )
    committed = _commit_outbox(
        db_session,
        resumed,
        generation,
        idempotency_key="delivery-revision-recovery",
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(seconds=3),
    )

    outbox = db_session.get(OutboundDeliveryOutbox, committed.outbox_id)
    assert outbox is not None
    assert outbox.endpoint_config_revision == "qq-config-r2"
    assert "endpoint_config_revision" not in json.loads(
        resumed.delivery_contract_json
    )
    old_circuit = (
        db_session.query(OutboundDeliveryCircuit)
        .filter(
            OutboundDeliveryCircuit.config_revision == "qq-config-r1",
            OutboundDeliveryCircuit.status == "open",
        )
        .one()
    )
    assert old_circuit.reason_type == "unauthorized"


def test_ambiguous_replay_preserves_lineage_and_success_risk(db_session):
    _seed_control(db_session)
    claim, _generation, original_result = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=5,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=2),
    )
    expire_stale_delivery_leases(db_session, now=NOW + timedelta(seconds=6))

    with pytest.raises(OutboundSafetyError):
        create_delivery_replay(
            db_session,
            parent_outbox_id=original_result.outbox_id,
            manual_request_key="manual-1",
            confirm_duplicate_risk=False,
            reason="确认重放",
            max_attempts=2,
            retry_deadline_at=NOW + timedelta(hours=2),
            endpoint_config_revision="qq-config-r2",
            now=NOW + timedelta(seconds=7),
        )
    replay = create_delivery_replay(
        db_session,
        parent_outbox_id=original_result.outbox_id,
        manual_request_key="manual-1",
        confirm_duplicate_risk=True,
        reason="管理员确认可能重复投递",
        max_attempts=2,
        retry_deadline_at=NOW + timedelta(hours=2),
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(seconds=7),
    )
    same = create_delivery_replay(
        db_session,
        parent_outbox_id=original_result.outbox_id,
        manual_request_key="manual-1",
        confirm_duplicate_risk=True,
        reason="管理员确认可能重复投递",
        max_attempts=2,
        retry_deadline_at=NOW + timedelta(hours=2),
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(seconds=8),
    )
    assert replay.created is True
    assert same.created is False
    assert same.outbox_id == replay.outbox_id

    original = db_session.get(OutboundDeliveryOutbox, original_result.outbox_id)
    child = db_session.get(OutboundDeliveryOutbox, replay.outbox_id)
    assert original is not None and original.status == "ambiguous"
    assert child is not None and child.replay_of_outbox_id == original.id
    assert child.replay_sequence == 1
    assert child.payload_json == original.payload_json
    assert child.payload_sha256 == original.payload_sha256
    assert child.destination_snapshot_json == original.destination_snapshot_json
    assert child.last_error_type == "manual_replay"
    assert child.last_error_summary == "管理员确认可能重复投递"

    replay_claim = claim_due_outbox(
        db_session,
        worker_owner="worker-b",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(seconds=9),
    )
    assert replay_claim is not None and replay_claim.outbox_id == child.id
    mark_delivery_request_started(
        db_session,
        outbox_id=child.id,
        attempt_id=replay_claim.attempt_id,
        worker_owner=replay_claim.worker_owner,
        lease_token=replay_claim.lease_token,
        now=NOW + timedelta(seconds=10),
    )
    settled = settle_delivery_attempt(
        db_session,
        outbox_id=child.id,
        attempt_id=replay_claim.attempt_id,
        worker_owner=replay_claim.worker_owner,
        lease_token=replay_claim.lease_token,
        outcome="succeeded",
        transport_phase="response_received",
        http_status=204,
        result_category="success",
        error_type="",
        safe_summary="",
        duration_ms=10,
        now=NOW + timedelta(seconds=11),
    )
    assert settled.run_status == "succeeded_after_ambiguous_replay"
    db_session.expire_all()
    run = db_session.get(OutboundRun, claim.run_id)
    assert run is not None
    assert run.status == "succeeded_after_ambiguous_replay"
    assert run.has_ambiguous_ancestor is True
    assert db_session.get(OutboundDeliveryOutbox, original.id).status == "ambiguous"


def test_successful_replay_history_does_not_block_draining_rollback(db_session):
    _seed_control(db_session)
    claim, _generation, original_result = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=5,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=2),
    )
    expire_stale_delivery_leases(db_session, now=NOW + timedelta(seconds=6))
    replay = create_delivery_replay(
        db_session,
        parent_outbox_id=original_result.outbox_id,
        manual_request_key="manual-draining-rollback",
        confirm_duplicate_risk=True,
        reason="管理员确认回放并验证回切",
        max_attempts=2,
        retry_deadline_at=NOW + timedelta(hours=2),
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(seconds=7),
    )
    replay_claim = claim_due_outbox(
        db_session,
        worker_owner="worker-b",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(seconds=8),
    )
    assert replay_claim is not None
    assert replay_claim.outbox_id == replay.outbox_id
    mark_delivery_request_started(
        db_session,
        outbox_id=replay.outbox_id,
        attempt_id=replay_claim.attempt_id,
        worker_owner=replay_claim.worker_owner,
        lease_token=replay_claim.lease_token,
        now=NOW + timedelta(seconds=9),
    )
    settled = settle_delivery_attempt(
        db_session,
        outbox_id=replay.outbox_id,
        attempt_id=replay_claim.attempt_id,
        worker_owner=replay_claim.worker_owner,
        lease_token=replay_claim.lease_token,
        outcome="succeeded",
        transport_phase="response_received",
        http_status=204,
        result_category="success",
        error_type="",
        safe_summary="",
        duration_ms=10,
        now=NOW + timedelta(seconds=10),
    )
    assert settled.run_status == "succeeded_after_ambiguous_replay"

    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None
    drain_boundary = NOW + timedelta(minutes=20)
    draining = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="outbox_active",
        new_mode="outbox_draining",
        expected_writer_version=int(control.writer_version),
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=drain_boundary,
        writer_lease_seconds=1800,
        now=NOW + timedelta(seconds=11),
    )
    legacy = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="outbox_draining",
        new_mode="legacy_direct",
        expected_writer_version=draining.writer_version,
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=drain_boundary,
        writer_lease_seconds=600,
        now=drain_boundary,
    )

    assert legacy.mode == "legacy_direct"
    assert legacy.cutover_epoch == 8
    original = db_session.get(
        OutboundDeliveryOutbox,
        original_result.outbox_id,
    )
    child = db_session.get(OutboundDeliveryOutbox, replay.outbox_id)
    run = db_session.get(OutboundRun, claim.run_id)
    assert original is not None and original.status == "ambiguous"
    assert child is not None and child.status == "delivered"
    assert run is not None and run.active_outbox_id == child.id
    assert run.status == "succeeded_after_ambiguous_replay"


def test_ambiguous_settlement_retry_survives_replay_lineage_advance(db_session):
    _seed_control(db_session)
    _claim, _generation, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=2),
    )
    settlement = {
        "outbox_id": delivery.outbox_id,
        "attempt_id": delivery.attempt_id,
        "worker_owner": delivery.worker_owner,
        "lease_token": delivery.lease_token,
        "outcome": "ambiguous",
        "transport_phase": "read",
        "http_status": None,
        "result_category": "ambiguous",
        "error_type": "read_timeout",
        "safe_summary": "请求可能已经到达接收端",
        "duration_ms": 5000,
    }
    first = settle_delivery_attempt(
        db_session,
        **settlement,
        now=NOW + timedelta(seconds=3),
    )
    assert first.applied is True
    replay = create_delivery_replay(
        db_session,
        parent_outbox_id=queued.outbox_id,
        manual_request_key="retry-after-ambiguous",
        confirm_duplicate_risk=True,
        reason="人工确认重放",
        max_attempts=2,
        retry_deadline_at=NOW + timedelta(hours=1),
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=4),
    )
    assert replay.created is True

    repeated = settle_delivery_attempt(
        db_session,
        **settlement,
        now=NOW + timedelta(seconds=5),
    )

    assert repeated.applied is False
    assert repeated.outbox_status == "ambiguous"
    assert repeated.run_status == "queued"
    run = db_session.get(OutboundRun, replay.run_id)
    assert run is not None and run.active_outbox_id == replay.outbox_id


def test_manual_replay_retry_survives_child_delivery(db_session):
    _seed_control(db_session)
    _claim, _generation, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=2),
    )
    settle_delivery_attempt(
        db_session,
        outbox_id=delivery.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        outcome="ambiguous",
        transport_phase="read",
        http_status=None,
        result_category="ambiguous",
        error_type="read_timeout",
        safe_summary="请求可能已经到达接收端",
        duration_ms=5000,
        now=NOW + timedelta(seconds=3),
    )
    replay_request = {
        "parent_outbox_id": queued.outbox_id,
        "manual_request_key": "stable-manual-request",
        "confirm_duplicate_risk": True,
        "reason": "人工确认重放",
        "max_attempts": 2,
        "retry_deadline_at": NOW + timedelta(hours=1),
        "endpoint_config_revision": "qq-config-r1",
    }
    replay = create_delivery_replay(
        db_session,
        **replay_request,
        now=NOW + timedelta(seconds=4),
    )
    child_delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-b",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=5),
    )
    assert child_delivery is not None
    assert child_delivery.outbox_id == replay.outbox_id
    mark_delivery_request_started(
        db_session,
        outbox_id=child_delivery.outbox_id,
        attempt_id=child_delivery.attempt_id,
        worker_owner=child_delivery.worker_owner,
        lease_token=child_delivery.lease_token,
        now=NOW + timedelta(seconds=6),
    )
    settle_delivery_attempt(
        db_session,
        outbox_id=child_delivery.outbox_id,
        attempt_id=child_delivery.attempt_id,
        worker_owner=child_delivery.worker_owner,
        lease_token=child_delivery.lease_token,
        outcome="succeeded",
        transport_phase="response_received",
        http_status=204,
        result_category="success",
        error_type="",
        safe_summary="",
        duration_ms=10,
        now=NOW + timedelta(seconds=7),
    )

    repeated = create_delivery_replay(
        db_session,
        **replay_request,
        now=NOW + timedelta(hours=2),
    )

    assert repeated.created is False
    assert repeated.outbox_id == replay.outbox_id
    assert db_session.query(OutboundDeliveryOutbox).count() == 2


def test_concurrent_manual_replay_creates_one_successor(tmp_path):
    engine, factory = _file_session_factory(tmp_path, "replay-race.db")
    try:
        with factory() as setup:
            _seed_control(setup)
            _claim, _generation, original = _queue_outbox(setup)
            delivery = claim_due_outbox(
                setup,
                worker_owner="worker-a",
                lease_seconds=5,
                endpoint_config_revision="qq-config-r1",
                now=NOW + timedelta(seconds=1),
            )
            assert delivery is not None
            mark_delivery_request_started(
                setup,
                outbox_id=delivery.outbox_id,
                attempt_id=delivery.attempt_id,
                worker_owner=delivery.worker_owner,
                lease_token=delivery.lease_token,
                now=NOW + timedelta(seconds=2),
            )
            expire_stale_delivery_leases(
                setup,
                now=NOW + timedelta(seconds=6),
            )
            setup.commit()
            parent_id = original.outbox_id

        barrier = threading.Barrier(2)

        def replay_from(request_key: str):
            with factory() as db:
                barrier.wait(timeout=5)
                try:
                    result = create_delivery_replay(
                        db,
                        parent_outbox_id=parent_id,
                        manual_request_key=request_key,
                        confirm_duplicate_risk=True,
                        reason="并发人工确认",
                        max_attempts=2,
                        retry_deadline_at=NOW + timedelta(hours=2),
                        endpoint_config_revision="qq-config-r2",
                        now=NOW + timedelta(seconds=7),
                    )
                    db.commit()
                    return result
                except OutboundConflictError:
                    db.rollback()
                    return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(replay_from, ("manual-a", "manual-b")))

        assert sum(result is not None for result in results) == 1
        with factory() as verify:
            rows = (
                verify.query(OutboundDeliveryOutbox)
                .order_by(OutboundDeliveryOutbox.replay_sequence)
                .all()
            )
            assert [row.replay_sequence for row in rows] == [0, 1]
            run = verify.query(OutboundRun).one()
            assert run.active_outbox_id == rows[1].id
            assert rows[0].status == "ambiguous"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_delivery_control_graph_is_cas_and_epoch_monotonic(db_session):
    _seed_control(
        db_session,
        mode="legacy_direct",
        epoch=0,
        protocol_version=2,
        writer_owner="producer-a",
        writer_token="writer-a",
        writer_lease_expires_at=NOW + timedelta(minutes=10),
    )
    hold_boundary = NOW + timedelta(minutes=5)
    hold = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="legacy_direct",
        new_mode="outbox_hold",
        expected_writer_version=0,
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=hold_boundary,
        writer_lease_seconds=600,
        now=NOW,
    )
    assert hold.applied is True
    assert hold.cutover_epoch == 1

    with pytest.raises(InvalidOutboundTransitionError):
        transition_delivery_control(
            db_session,
            source_type=SOURCE_TYPE,
            expected_mode="outbox_hold",
            new_mode="legacy_direct",
            expected_writer_version=hold.writer_version,
            actor_owner="producer-a",
            actor_token="writer-a",
            protocol_version=2,
            effective_from=NOW + timedelta(minutes=6),
            writer_lease_seconds=600,
            now=NOW,
        )
    with pytest.raises(OutboundSafetyError):
        transition_delivery_control(
            db_session,
            source_type=SOURCE_TYPE,
            expected_mode="outbox_hold",
            new_mode="outbox_active",
            expected_writer_version=hold.writer_version,
            actor_owner="producer-a",
            actor_token="writer-a",
            protocol_version=2,
            effective_from=hold_boundary,
            writer_lease_seconds=600,
            now=NOW + timedelta(minutes=4),
        )

    active = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="outbox_hold",
        new_mode="outbox_active",
        expected_writer_version=hold.writer_version,
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=hold_boundary,
        writer_lease_seconds=600,
        now=hold_boundary,
    )
    assert active.cutover_epoch == 1
    drain_boundary = NOW + timedelta(minutes=20)
    draining = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="outbox_active",
        new_mode="outbox_draining",
        expected_writer_version=active.writer_version,
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=drain_boundary,
        writer_lease_seconds=1800,
        now=NOW + timedelta(minutes=10),
    )
    assert draining.cutover_epoch == 1
    legacy = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="outbox_draining",
        new_mode="legacy_direct",
        expected_writer_version=draining.writer_version,
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=drain_boundary,
        writer_lease_seconds=600,
        now=drain_boundary,
    )
    assert legacy.cutover_epoch == 2


@pytest.mark.parametrize(
    "unsafe_state",
    [
        "claimed",
        "generating",
        "pending",
        "retry_wait",
        "leased",
        "ambiguous",
        "started_attempt",
    ],
)
def test_hold_to_active_rejects_every_live_legacy_state(
    db_session,
    unsafe_state,
):
    boundary = NOW + timedelta(minutes=20)
    _seed_control(
        db_session,
        mode="outbox_hold",
        epoch=1,
        effective_from=boundary,
        writer_owner="producer-a",
        writer_token="writer-a",
        writer_lease_expires_at=NOW + timedelta(hours=2),
    )
    claim = _claim_run(
        db_session,
        occurrence_key=f"legacy-{unsafe_state}",
        scheduled_for=NOW,
        writer_lease_seconds=7200,
    )
    assert claim.delivery_mode == "legacy_direct"
    run = db_session.get(OutboundRun, claim.run_id)
    assert run is not None and run.cutover_epoch == 0

    if unsafe_state != "claimed":
        generation = _start_generation(db_session, claim)
        if unsafe_state != "generating":
            queued = _commit_outbox(
                db_session,
                claim,
                generation,
                idempotency_key=f"legacy-{unsafe_state}-leaf",
            )
            if unsafe_state in {
                "retry_wait",
                "leased",
                "ambiguous",
                "started_attempt",
            }:
                delivery = _claim_legacy_outbox(
                    db_session,
                    queued.outbox_id,
                    now=NOW + timedelta(seconds=1),
                )
                assert delivery is not None
                if unsafe_state in {"retry_wait", "ambiguous"}:
                    mark_delivery_request_started(
                        db_session,
                        outbox_id=delivery.outbox_id,
                        attempt_id=delivery.attempt_id,
                        worker_owner=delivery.worker_owner,
                        lease_token=delivery.lease_token,
                        now=NOW + timedelta(seconds=2),
                    )
                    settle_delivery_attempt(
                        db_session,
                        outbox_id=delivery.outbox_id,
                        attempt_id=delivery.attempt_id,
                        worker_owner=delivery.worker_owner,
                        lease_token=delivery.lease_token,
                        outcome=(
                            "transient_failure"
                            if unsafe_state == "retry_wait"
                            else "ambiguous"
                        ),
                        transport_phase=(
                            "response_received"
                            if unsafe_state == "retry_wait"
                            else "read"
                        ),
                        http_status=(
                            503 if unsafe_state == "retry_wait" else None
                        ),
                        result_category=(
                            "transient"
                            if unsafe_state == "retry_wait"
                            else "ambiguous"
                        ),
                        error_type=(
                            "service_unavailable"
                            if unsafe_state == "retry_wait"
                            else "read_timeout"
                        ),
                        safe_summary="legacy leaf 尚未安全结算",
                        duration_ms=10,
                        retry_at=(
                            NOW + timedelta(minutes=10)
                            if unsafe_state == "retry_wait"
                            else None
                        ),
                        now=NOW + timedelta(seconds=3),
                    )
                elif unsafe_state == "started_attempt":
                    outbox = db_session.get(
                        OutboundDeliveryOutbox,
                        queued.outbox_id,
                    )
                    run = db_session.get(OutboundRun, claim.run_id)
                    assert outbox is not None and run is not None
                    outbox.status = "failed"
                    outbox.lease_owner = None
                    outbox.lease_token = None
                    outbox.lease_expires_at = None
                    outbox.last_error_type = "inconsistent_started_attempt"
                    run.status = "failed"
                    run.failure_type = "inconsistent_started_attempt"
                    db_session.flush()
                    attempt = db_session.get(
                        OutboundDeliveryAttempt,
                        delivery.attempt_id,
                    )
                    assert attempt is not None and attempt.status == "started"

    db_session.expire_all()
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None
    with pytest.raises(OutboundSafetyError):
        transition_delivery_control(
            db_session,
            source_type=SOURCE_TYPE,
            expected_mode="outbox_hold",
            new_mode="outbox_active",
            expected_writer_version=int(control.writer_version),
            actor_owner="producer-a",
            actor_token="writer-a",
            protocol_version=2,
            effective_from=boundary,
            writer_lease_seconds=3600,
            now=boundary,
        )

    db_session.expire_all()
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None and control.mode == "outbox_hold"
    assert control.cutover_epoch == 1


def test_active_to_draining_rejects_legacy_queued_leaf(db_session):
    _seed_control(
        db_session,
        mode="outbox_active",
        epoch=7,
        effective_from=NOW + timedelta(minutes=20),
        writer_owner="producer-a",
        writer_token="writer-a",
        writer_lease_expires_at=NOW + timedelta(hours=2),
    )
    claim = _claim_run(
        db_session,
        occurrence_key="legacy-queued-before-draining",
        scheduled_for=NOW,
    )
    assert claim.delivery_mode == "legacy_direct"
    assert claim.cutover_epoch == 6
    generation = _start_generation(db_session, claim)
    queued = _commit_outbox(
        db_session,
        claim,
        generation,
        idempotency_key="legacy-queued-before-draining-leaf",
    )
    run = db_session.get(OutboundRun, claim.run_id)
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert run is not None and run.status == "queued"
    assert outbox is not None and outbox.status == "pending"
    assert control is not None

    with pytest.raises(OutboundSafetyError):
        transition_delivery_control(
            db_session,
            source_type=SOURCE_TYPE,
            expected_mode="outbox_active",
            new_mode="outbox_draining",
            expected_writer_version=int(control.writer_version),
            actor_owner="producer-a",
            actor_token="writer-a",
            protocol_version=2,
            effective_from=NOW + timedelta(minutes=30),
            writer_lease_seconds=3600,
            now=NOW + timedelta(seconds=1),
        )

    db_session.expire_all()
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None and control.mode == "outbox_active"
    assert control.cutover_epoch == 7


def test_legacy_protocol_writer_cannot_start_cutover(db_session):
    _seed_control(
        db_session,
        mode="legacy_direct",
        epoch=0,
        protocol_version=1,
        writer_owner="legacy-writer",
        writer_token="legacy-token",
        writer_lease_expires_at=NOW + timedelta(minutes=10),
    )
    with pytest.raises(OutboundSafetyError):
        transition_delivery_control(
            db_session,
            source_type=SOURCE_TYPE,
            expected_mode="legacy_direct",
            new_mode="outbox_hold",
            expected_writer_version=0,
            actor_owner="legacy-writer",
            actor_token="legacy-token",
            protocol_version=1,
            effective_from=NOW + timedelta(minutes=5),
            writer_lease_seconds=600,
            now=NOW,
        )
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None and control.mode == "legacy_direct"
    assert control.cutover_epoch == 0


def test_legacy_cutover_rejects_recoverable_blocked_run_without_outbox(
    db_session,
):
    _seed_control(
        db_session,
        mode="legacy_direct",
        epoch=0,
        protocol_version=2,
        writer_owner="producer-a",
        writer_token="writer-a",
        writer_lease_expires_at=NOW + timedelta(minutes=10),
    )
    db_session.add(OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
        config_revision="qq-config-r1",
        status="open",
        reason_type="route_missing",
        opened_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    ))
    db_session.flush()

    blocked = _claim_run(db_session)
    run = db_session.get(OutboundRun, blocked.run_id)
    assert blocked.acquired is False
    assert blocked.status == "blocked"
    assert run is not None and run.active_outbox_id is None
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None

    with pytest.raises(OutboundSafetyError, match="未安全结算"):
        transition_delivery_control(
            db_session,
            source_type=SOURCE_TYPE,
            expected_mode="legacy_direct",
            new_mode="outbox_hold",
            expected_writer_version=int(control.writer_version),
            actor_owner="producer-a",
            actor_token="writer-a",
            protocol_version=2,
            effective_from=NOW + timedelta(minutes=5),
            writer_lease_seconds=600,
            now=NOW + timedelta(seconds=1),
        )


def test_active_cutover_rejects_recoverable_blocked_run_without_outbox(
    db_session,
):
    _seed_control(db_session, mode="outbox_active", epoch=7)
    db_session.add(OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
        config_revision="qq-config-r1",
        status="open",
        reason_type="route_missing",
        opened_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    ))
    db_session.flush()

    blocked = _claim_run(db_session)
    run = db_session.get(OutboundRun, blocked.run_id)
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert blocked.status == "blocked"
    assert run is not None and run.delivery_mode == "outbox"
    assert run.active_outbox_id is None
    assert control is not None

    with pytest.raises(OutboundSafetyError, match="生成中|未安全结算"):
        transition_delivery_control(
            db_session,
            source_type=SOURCE_TYPE,
            expected_mode="outbox_active",
            new_mode="outbox_draining",
            expected_writer_version=int(control.writer_version),
            actor_owner="producer-a",
            actor_token="writer-a",
            protocol_version=2,
            effective_from=NOW + timedelta(minutes=5),
            writer_lease_seconds=600,
            now=NOW + timedelta(seconds=1),
        )


def test_hold_blocks_worker_and_draining_blocks_producer(db_session):
    _seed_control(
        db_session,
        mode="outbox_hold",
        epoch=1,
        effective_from=NOW - timedelta(minutes=1),
    )
    _queue_outbox(db_session)
    assert claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=1),
    ) is None

    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    control.mode = "outbox_draining"
    control.updated_at = NOW + timedelta(seconds=2)
    db_session.flush()
    with pytest.raises(OutboundSafetyError):
        _claim_run(
            db_session,
            source_id="task-2",
            occurrence_key="draining-slot",
            now=NOW + timedelta(seconds=3),
        )


def test_release_delivery_writer_rejects_stale_version_without_mutation(
    db_session,
):
    _seed_control(db_session, mode="legacy_direct", epoch=0)
    lease = acquire_or_renew_delivery_writer(
        db_session,
        source_type=SOURCE_TYPE,
        owner="producer-a",
        token="writer-a",
        protocol_version=2,
        lease_seconds=60,
        now=NOW,
    )
    assert lease.acquired is True

    with pytest.raises(OutboundFencingError):
        outbound_delivery_state.release_delivery_writer(
            db_session,
            source_type=SOURCE_TYPE,
            owner="producer-a",
            token="writer-a",
            protocol_version=2,
            expected_writer_version=lease.writer_version - 1,
            now=NOW,
        )

    db_session.expire_all()
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None
    assert control.writer_version == lease.writer_version
    assert control.writer_owner == "producer-a"
    assert control.writer_token == "writer-a"
    assert control.writer_lease_expires_at == lease.lease_expires_at


def test_release_delivery_writer_clears_lease_and_allows_takeover(db_session):
    _seed_control(db_session, mode="legacy_direct", epoch=0)
    lease = acquire_or_renew_delivery_writer(
        db_session,
        source_type=SOURCE_TYPE,
        owner="producer-a",
        token="writer-a",
        protocol_version=2,
        lease_seconds=60,
        now=NOW,
    )
    assert lease.acquired is True

    released = outbound_delivery_state.release_delivery_writer(
        db_session,
        source_type=SOURCE_TYPE,
        owner="producer-a",
        token="writer-a",
        protocol_version=2,
        expected_writer_version=lease.writer_version,
        now=NOW,
    )

    assert released.applied is True
    assert released.source_type == SOURCE_TYPE
    assert released.writer_version == lease.writer_version + 1
    db_session.expire_all()
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None
    assert control.writer_version == released.writer_version
    assert control.writer_owner is None
    assert control.writer_token is None
    assert control.writer_lease_expires_at is None

    takeover = acquire_or_renew_delivery_writer(
        db_session,
        source_type=SOURCE_TYPE,
        owner="producer-b",
        token="writer-b",
        protocol_version=2,
        lease_seconds=60,
        now=NOW,
    )
    assert takeover.acquired is True
    assert takeover.writer_version == released.writer_version + 1


def test_writer_lease_and_protocol_are_fenced(db_session):
    _seed_control(
        db_session,
        mode="legacy_direct",
        epoch=0,
        protocol_version=1,
        writer_owner="old-writer",
        writer_token="old-token",
        writer_lease_expires_at=NOW + timedelta(minutes=1),
    )
    blocked = acquire_or_renew_delivery_writer(
        db_session,
        source_type=SOURCE_TYPE,
        owner="new-writer",
        token="new-token",
        protocol_version=2,
        lease_seconds=60,
        now=NOW,
    )
    assert blocked.acquired is False
    acquired = acquire_or_renew_delivery_writer(
        db_session,
        source_type=SOURCE_TYPE,
        owner="new-writer",
        token="new-token",
        protocol_version=2,
        lease_seconds=60,
        now=NOW + timedelta(minutes=1),
    )
    assert acquired.acquired is True
    assert acquired.protocol_version == 2

    downgraded = acquire_or_renew_delivery_writer(
        db_session,
        source_type=SOURCE_TYPE,
        owner="old-protocol-writer",
        token="old-protocol-token",
        protocol_version=1,
        lease_seconds=60,
        now=NOW + timedelta(minutes=3),
    )
    assert downgraded.acquired is False
    assert downgraded.protocol_version == 2


def test_open_circuit_blocked_run_resumes_same_occurrence_after_reset(db_session):
    _seed_control(db_session)
    fingerprint = endpoint_circuit_fingerprint(ENDPOINT_KEY)
    db_session.add(OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=fingerprint,
        config_revision="qq-config-r1",
        status="open",
        reason_type="unauthorized",
        opened_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    ))
    db_session.flush()
    blocked = _claim_run(db_session)
    assert blocked.acquired is False
    assert blocked.status == "blocked"
    reset_delivery_circuit(
        db_session,
        scope_type="endpoint",
        scope_fingerprint=fingerprint,
        config_revision="qq-config-r1",
        expected_updated_at=NOW,
        now=NOW + timedelta(seconds=1),
    )
    resumed = _claim_run(db_session, now=NOW + timedelta(seconds=2))
    assert resumed.acquired is True
    assert resumed.run_id == blocked.run_id
    assert db_session.query(OutboundRun).count() == 1


def test_worker_holds_queued_outbox_until_circuit_reset_without_regeneration(
    db_session,
):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    fingerprint = endpoint_circuit_fingerprint(ENDPOINT_KEY)
    circuit = OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=fingerprint,
        config_revision="qq-config-r1",
        status="open",
        reason_type="unauthorized",
        opened_at=NOW + timedelta(seconds=1),
        created_at=NOW + timedelta(seconds=1),
        updated_at=NOW + timedelta(seconds=1),
    )
    db_session.add(circuit)
    db_session.flush()

    assert claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=2),
    ) is None
    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    run = db_session.get(OutboundRun, queued.run_id)
    assert outbox is not None and outbox.status == "blocked"
    assert run is not None and run.status == "blocked"
    assert db_session.query(OutboundDeliveryAttempt).count() == 0
    assert db_session.query(OutboundGenerationAttempt).count() == 1

    reset = reset_delivery_circuit(
        db_session,
        scope_type="endpoint",
        scope_fingerprint=fingerprint,
        config_revision="qq-config-r1",
        expected_updated_at=NOW + timedelta(seconds=1),
        now=NOW + timedelta(seconds=3),
    )
    assert reset.applied is True
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-b",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=4),
    )

    assert delivery is not None
    assert delivery.outbox_id == queued.outbox_id
    assert db_session.query(OutboundGenerationAttempt).count() == 1


def test_draining_worker_requires_current_epoch(db_session):
    _seed_control(db_session, mode="outbox_active", epoch=7)
    _queue_outbox(db_session)
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None
    control.mode = "outbox_draining"
    control.cutover_epoch = 8
    control.updated_at = NOW + timedelta(seconds=1)
    db_session.flush()
    assert claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=2),
    ) is None


def test_reset_circuit_is_revision_specific(db_session):
    _seed_control(db_session)
    fingerprint = endpoint_circuit_fingerprint(ENDPOINT_KEY)
    for revision in ("qq-config-r1", "qq-config-r2"):
        db_session.add(OutboundDeliveryCircuit(
            scope_type="endpoint",
            scope_fingerprint=fingerprint,
            config_revision=revision,
            status="open",
            reason_type="unauthorized",
            opened_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        ))
    db_session.flush()

    reset = reset_delivery_circuit(
        db_session,
        scope_type="endpoint",
        scope_fingerprint=fingerprint,
        config_revision="qq-config-r1",
        expected_updated_at=NOW,
        now=NOW + timedelta(seconds=1),
    )
    assert reset.applied is True
    states = {
        row.config_revision: row.status
        for row in db_session.query(OutboundDeliveryCircuit).all()
    }
    assert states == {"qq-config-r1": "closed", "qq-config-r2": "open"}


def test_generated_outbox_cannot_replace_frozen_occurrence_destination(db_session):
    _seed_control(db_session)
    claim = _claim_run(db_session)
    generation = _start_generation(db_session, claim)

    with pytest.raises(OutboundConflictError, match="冻结|目标|投递"):
        _commit_outbox(
            db_session,
            claim,
            generation,
            destination_snapshot={
                "target_type": "private",
                "target_id": "different-user",
            },
        )

    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_writer_takeover_fences_run_renewal_and_generated_commit(db_session):
    _seed_control(db_session)
    claim = _claim_run(
        db_session,
        claim_lease_seconds=120,
        writer_lease_seconds=30,
    )
    generation = _start_generation(db_session, claim)
    takeover = acquire_or_renew_delivery_writer(
        db_session,
        source_type=SOURCE_TYPE,
        owner="producer-b",
        token="writer-b",
        protocol_version=2,
        lease_seconds=60,
        now=NOW + timedelta(seconds=31),
    )
    assert takeover.acquired is True

    renewal = renew_outbound_run_claim(
        db_session,
        run_id=claim.run_id,
        owner=claim.owner,
        claim_token=claim.claim_token,
        lease_seconds=60,
        now=NOW + timedelta(seconds=32),
    )
    assert renewal.applied is False
    with pytest.raises(OutboundFencingError, match="writer|生成"):
        _commit_outbox(
            db_session,
            claim,
            generation,
            now=NOW + timedelta(seconds=33),
        )


def test_request_boundary_rechecks_actual_revision_circuits(db_session):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    db_session.add(OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
        config_revision="qq-config-r1",
        status="open",
        reason_type="unauthorized",
        opened_at=NOW + timedelta(seconds=1),
        created_at=NOW + timedelta(seconds=1),
        updated_at=NOW + timedelta(seconds=1),
    ))
    db_session.flush()

    with pytest.raises(OutboundSafetyError, match="circuit"):
        mark_delivery_request_started(
            db_session,
            outbox_id=queued.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            now=NOW + timedelta(seconds=2),
        )

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    attempt = db_session.get(OutboundDeliveryAttempt, delivery.attempt_id)
    assert outbox is not None and outbox.request_started_count == 0
    assert attempt is not None and attempt.request_started is False


def test_terminal_settlement_rejects_conflicting_audit_facts(db_session):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=1),
    )
    settle_delivery_attempt(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        outcome="succeeded",
        transport_phase="response_received",
        http_status=204,
        result_category="success",
        error_type="",
        safe_summary="first",
        duration_ms=12,
        now=NOW + timedelta(seconds=2),
    )

    with pytest.raises(OutboundConflictError, match="结算|事实"):
        settle_delivery_attempt(
            db_session,
            outbox_id=queued.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            outcome="succeeded",
            transport_phase="settled",
            http_status=200,
            result_category="different_category",
            error_type="different_error",
            safe_summary="changed",
            duration_ms=999,
            now=NOW + timedelta(seconds=3),
        )


def test_manual_replay_same_key_rejects_conflicting_immutable_facts(db_session):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=5,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=1),
    )
    expire_stale_delivery_leases(
        db_session,
        now=NOW + timedelta(seconds=6),
    )
    create_delivery_replay(
        db_session,
        parent_outbox_id=queued.outbox_id,
        manual_request_key="manual-replay-immutable",
        confirm_duplicate_risk=True,
        reason="第一次人工确认",
        max_attempts=2,
        retry_deadline_at=NOW + timedelta(hours=1),
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(seconds=7),
    )

    with pytest.raises(OutboundConflictError, match="replay|不可变"):
        create_delivery_replay(
            db_session,
            parent_outbox_id=queued.outbox_id,
            manual_request_key="manual-replay-immutable",
            confirm_duplicate_risk=True,
            reason="第二次改写",
            max_attempts=9,
            retry_deadline_at=NOW + timedelta(hours=2),
            endpoint_config_revision="qq-config-r3",
            now=NOW + timedelta(seconds=8),
        )


def test_transient_retry_must_be_strictly_in_the_future(db_session):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session, max_attempts=3)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="retry_at"):
        settle_delivery_attempt(
            db_session,
            outbox_id=queued.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            outcome="transient_failure",
            transport_phase="response_received",
            http_status=503,
            result_category="transient",
            error_type="service_unavailable",
            safe_summary="temporary",
            duration_ms=10,
            retry_at=NOW,
            now=NOW + timedelta(seconds=2),
        )


def test_payload_specific_413_cannot_open_shared_endpoint_circuit(db_session):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="payload|共享|circuit"):
        settle_delivery_attempt(
            db_session,
            outbox_id=queued.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            outcome="permanent_failure",
            transport_phase="response_received",
            http_status=413,
            result_category="payload",
            error_type="payload_too_large",
            safe_summary="too large",
            duration_ms=10,
            circuit_scope_type="endpoint",
            now=NOW + timedelta(seconds=2),
        )
    assert db_session.query(OutboundDeliveryCircuit).count() == 0


def test_expired_lease_without_attempt_converges_to_ambiguous(db_session):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=5,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    attempt = db_session.get(OutboundDeliveryAttempt, delivery.attempt_id)
    assert attempt is not None
    db_session.delete(attempt)
    db_session.flush()

    summary = expire_stale_delivery_leases(
        db_session,
        now=NOW + timedelta(seconds=6),
    )

    assert summary.ambiguous == 1
    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    run = db_session.get(OutboundRun, delivery.run_id)
    assert outbox is not None and outbox.status == "ambiguous"
    assert run is not None and run.status == "ambiguous"


def test_active_to_draining_rejects_live_legacy_generation(db_session):
    _seed_control(
        db_session,
        mode="outbox_active",
        epoch=7,
        effective_from=NOW + timedelta(minutes=10),
    )
    claim = _claim_run(db_session, scheduled_for=NOW)
    assert claim.delivery_mode == "legacy_direct"
    _start_generation(db_session, claim)
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None

    with pytest.raises(OutboundSafetyError, match="legacy|生成|安全"):
        transition_delivery_control(
            db_session,
            source_type=SOURCE_TYPE,
            expected_mode="outbox_active",
            new_mode="outbox_draining",
            expected_writer_version=int(control.writer_version),
            actor_owner="producer-a",
            actor_token="writer-a",
            protocol_version=2,
            effective_from=NOW + timedelta(minutes=20),
            writer_lease_seconds=600,
            now=NOW + timedelta(seconds=1),
        )


def test_draining_to_legacy_rejects_generation_without_outbox(db_session):
    _seed_control(db_session, mode="outbox_active", epoch=7)
    claim = _claim_run(db_session)
    _start_generation(db_session, claim)
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None
    control.mode = "outbox_draining"
    control.updated_at = NOW + timedelta(seconds=1)
    db_session.flush()

    with pytest.raises(OutboundSafetyError, match="run|生成|安全"):
        transition_delivery_control(
            db_session,
            source_type=SOURCE_TYPE,
            expected_mode="outbox_draining",
            new_mode="legacy_direct",
            expected_writer_version=int(control.writer_version),
            actor_owner="producer-a",
            actor_token="writer-a",
            protocol_version=2,
            effective_from=control.effective_from,
            writer_lease_seconds=600,
            now=NOW + timedelta(seconds=2),
        )


@pytest.mark.parametrize(
    "legacy_status",
    [
        "claimed",
        "generating",
        "queued",
        "delivering",
        "blocked",
        "ambiguous",
    ],
)
def test_draining_to_legacy_rejects_cross_epoch_live_legacy_run(
    db_session,
    legacy_status,
):
    boundary = NOW + timedelta(minutes=20)
    _seed_control(
        db_session,
        mode="outbox_active",
        epoch=7,
        effective_from=boundary,
        writer_owner="producer-a",
        writer_token="writer-a",
        writer_lease_expires_at=NOW + timedelta(hours=2),
    )
    claim = _claim_run(
        db_session,
        occurrence_key=f"legacy-before-draining-{legacy_status}",
        scheduled_for=NOW,
        writer_lease_seconds=7200,
    )
    assert claim.delivery_mode == "legacy_direct"
    assert claim.cutover_epoch == 6

    queued = None
    if legacy_status == "blocked":
        control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
        assert control is not None
        control.mode = "outbox_draining"
        control.updated_at = NOW + timedelta(seconds=1)
        db_session.flush()
        blocked = _start_generation(
            db_session,
            claim,
            now=NOW + timedelta(seconds=2),
        )
        assert blocked.status == "blocked"
    elif legacy_status != "claimed":
        generation = _start_generation(db_session, claim)
        if legacy_status != "generating":
            queued = _commit_outbox(
                db_session,
                claim,
                generation,
                idempotency_key=f"legacy-before-draining-{legacy_status}",
            )
            if legacy_status in {"delivering", "ambiguous"}:
                delivery = _claim_legacy_outbox(
                    db_session,
                    queued.outbox_id,
                    now=NOW + timedelta(seconds=1),
                )
                assert delivery is not None
                if legacy_status == "ambiguous":
                    mark_delivery_request_started(
                        db_session,
                        outbox_id=delivery.outbox_id,
                        attempt_id=delivery.attempt_id,
                        worker_owner=delivery.worker_owner,
                        lease_token=delivery.lease_token,
                        now=NOW + timedelta(seconds=2),
                    )
                    settled = settle_delivery_attempt(
                        db_session,
                        outbox_id=delivery.outbox_id,
                        attempt_id=delivery.attempt_id,
                        worker_owner=delivery.worker_owner,
                        lease_token=delivery.lease_token,
                        outcome="ambiguous",
                        transport_phase="read",
                        http_status=None,
                        result_category="ambiguous",
                        error_type="read_timeout",
                        safe_summary="legacy leaf 投递结果不明",
                        duration_ms=10,
                        now=NOW + timedelta(seconds=3),
                    )
                    assert settled.run_status == "ambiguous"

    if legacy_status != "blocked":
        control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
        assert control is not None
        control.mode = "outbox_draining"
        control.updated_at = NOW + timedelta(seconds=4)
        db_session.flush()

    db_session.expire_all()
    run = db_session.get(OutboundRun, claim.run_id)
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert run is not None and run.status == legacy_status
    assert run.delivery_mode == "legacy_direct"
    assert run.cutover_epoch == 6
    assert control is not None and control.mode == "outbox_draining"
    assert control.cutover_epoch == 7
    if legacy_status == "queued":
        assert queued is not None
        outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
        assert outbox is not None and outbox.status == "pending"
        assert run.active_outbox_id == outbox.id

    with pytest.raises(OutboundSafetyError, match="legacy|安全"):
        transition_delivery_control(
            db_session,
            source_type=SOURCE_TYPE,
            expected_mode="outbox_draining",
            new_mode="legacy_direct",
            expected_writer_version=int(control.writer_version),
            actor_owner="producer-a",
            actor_token="writer-a",
            protocol_version=2,
            effective_from=boundary,
            writer_lease_seconds=600,
            now=boundary,
        )

    db_session.expire_all()
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None and control.mode == "outbox_draining"
    assert control.cutover_epoch == 7


@pytest.mark.parametrize("stage", ["start", "commit"])
def test_generation_safety_block_is_durable_across_caller_commit(tmp_path, stage):
    engine, factory = _file_session_factory(tmp_path, f"safety-{stage}.db")
    try:
        with factory() as setup:
            _seed_control(setup)
            setup.commit()

        with factory() as producer:
            claim = _claim_run(producer)
            generation = None
            if stage == "commit":
                generation = _start_generation(producer, claim)
            producer.commit()

        with factory() as changer:
            changer.add(OutboundDeliveryCircuit(
                scope_type="endpoint",
                scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
                config_revision="qq-config-r1",
                status="open",
                reason_type="unauthorized",
                opened_at=NOW + timedelta(seconds=1),
                created_at=NOW + timedelta(seconds=1),
                updated_at=NOW + timedelta(seconds=1),
            ))
            changer.commit()

        with factory() as producer:
            if stage == "start":
                result = _start_generation(
                    producer,
                    claim,
                    now=NOW + timedelta(seconds=2),
                )
            else:
                assert generation is not None
                result = _commit_outbox(
                    producer,
                    claim,
                    generation,
                    now=NOW + timedelta(seconds=2),
                )
            assert result.status == "blocked"
            producer.commit()

        with factory() as observer:
            run = observer.get(OutboundRun, claim.run_id)
            assert run is not None and run.status == "blocked"
            assert run.claim_owner is None and run.claim_token is None
            attempts = observer.query(OutboundGenerationAttempt).all()
            if stage == "start":
                assert attempts == []
            else:
                assert len(attempts) == 1
                assert attempts[0].status == "abandoned"
            assert observer.query(OutboundDeliveryOutbox).count() == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_delivery_lease_starts_after_sqlite_write_lock(tmp_path):
    engine, factory = _file_session_factory(tmp_path, "lease-lock-wait.db")
    blocker = None
    try:
        with factory() as setup:
            _seed_control(setup)
            _, _, queued = _queue_outbox(setup)
            outbox = setup.get(OutboundDeliveryOutbox, queued.outbox_id)
            assert outbox is not None
            outbox.retry_deadline_at = (
                datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(days=1)
            )
            setup.commit()

        blocker = engine.raw_connection()
        blocker.execute("BEGIN IMMEDIATE")
        started = threading.Event()
        result_box: dict[str, object] = {}

        def claim_after_lock():
            with factory() as worker:
                started.set()
                result = claim_due_outbox(
                    worker,
                    worker_owner="worker-lock-wait",
                    lease_seconds=1,
                    endpoint_config_revision="qq-config-r1",
                    now=None,
                )
                worker.commit()
                result_box["result"] = result
                result_box["finished_at"] = datetime.now(timezone.utc).replace(
                    tzinfo=None
                )

        thread = threading.Thread(target=claim_after_lock)
        thread.start()
        assert started.wait(timeout=2)
        time.sleep(1.2)
        assert thread.is_alive()
        blocker.rollback()
        blocker.close()
        blocker = None
        thread.join(timeout=5)
        assert not thread.is_alive()

        result = result_box["result"]
        assert result is not None
        assert result.lease_expires_at > result_box["finished_at"]
    finally:
        if blocker is not None:
            blocker.rollback()
            blocker.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_pending_outbox_past_deadline_converges_to_retry_exhausted(db_session):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    assert outbox is not None
    outbox.retry_deadline_at = NOW + timedelta(seconds=1)
    db_session.flush()

    assert claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=2),
    ) is None

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    run = db_session.get(OutboundRun, queued.run_id)
    assert outbox is not None and outbox.status == "failed"
    assert outbox.last_error_type == "retry_exhausted"
    assert run is not None and run.status == "failed"


def test_before_send_expiry_past_deadline_is_terminal(db_session):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    assert outbox is not None
    outbox.retry_deadline_at = NOW + timedelta(seconds=3)
    db_session.flush()
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=5,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None

    summary = expire_stale_delivery_leases(
        db_session,
        now=NOW + timedelta(seconds=6),
    )

    assert summary.abandoned_before_send == 1
    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    run = db_session.get(OutboundRun, queued.run_id)
    assert outbox is not None and outbox.status == "failed"
    assert outbox.last_error_type == "retry_exhausted"
    assert run is not None and run.status == "failed"


@pytest.mark.parametrize("initial_status", ["retry_wait", "blocked"])
def test_deferred_outbox_past_deadline_converges_to_retry_exhausted(
    db_session,
    initial_status,
):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    if initial_status == "retry_wait":
        delivery = claim_due_outbox(
            db_session,
            worker_owner="worker-a",
            lease_seconds=30,
            endpoint_config_revision="qq-config-r1",
            now=NOW,
        )
        assert delivery is not None
        mark_delivery_request_started(
            db_session,
            outbox_id=queued.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            now=NOW + timedelta(seconds=1),
        )
        settle_delivery_attempt(
            db_session,
            outbox_id=queued.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            outcome="transient_failure",
            transport_phase="response_received",
            http_status=503,
            result_category="transient",
            error_type="service_unavailable",
            safe_summary="稍后重试",
            duration_ms=10,
            retry_at=NOW + timedelta(minutes=10),
            now=NOW + timedelta(seconds=2),
        )
    else:
        db_session.add(OutboundDeliveryCircuit(
            scope_type="endpoint",
            scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
            config_revision="qq-config-r1",
            status="open",
            reason_type="unauthorized",
            opened_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        ))
        db_session.flush()
        assert claim_due_outbox(
            db_session,
            worker_owner="worker-a",
            lease_seconds=30,
            endpoint_config_revision="qq-config-r1",
            now=NOW + timedelta(seconds=1),
        ) is None

    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    assert outbox is not None and outbox.status == initial_status
    attempt_count = db_session.query(OutboundDeliveryAttempt).count()
    assert claim_due_outbox(
        db_session,
        worker_owner="worker-b",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(hours=2),
    ) is None

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    run = db_session.get(OutboundRun, queued.run_id)
    assert outbox is not None and outbox.status == "failed"
    assert outbox.last_error_type == "retry_exhausted"
    assert run is not None and run.status == "failed"
    assert db_session.query(OutboundDeliveryAttempt).count() == attempt_count


@pytest.mark.parametrize("safe_status", ["pending", "retry_wait", "blocked"])
def test_cancel_safe_outbox_cancels_only_unleased_active_leaf(
    db_session,
    safe_status,
):
    outbox = _prepare_outbox_state(db_session, safe_status)
    run_id = int(outbox.run_id)
    expected_updated_at = outbox.updated_at

    outbound_delivery_state.cancel_safe_outbox(
        db_session,
        outbox_id=int(outbox.id),
        expected_status=safe_status,
        expected_updated_at=expected_updated_at,
        reason_type="manual_cancel",
        safe_summary="管理员确认取消未发送 leaf",
        now=NOW + timedelta(minutes=20),
    )

    db_session.expire_all()
    cancelled = db_session.get(OutboundDeliveryOutbox, int(outbox.id))
    run = db_session.get(OutboundRun, run_id)
    assert cancelled is not None and cancelled.status == "cancelled"
    assert cancelled.cancel_reason_type == "manual_cancel"
    assert cancelled.cancelled_at == NOW + timedelta(minutes=20)
    assert cancelled.lease_owner is None
    assert cancelled.lease_token is None
    assert cancelled.lease_expires_at is None
    assert run is not None and run.status == "failed"
    assert run.active_outbox_id == cancelled.id


@pytest.mark.parametrize("unsafe_status", ["leased", "ambiguous", "delivered"])
def test_cancel_safe_outbox_rejects_unsafe_or_delivered_leaf(
    db_session,
    unsafe_status,
):
    outbox = _prepare_outbox_state(db_session, unsafe_status)
    expected_updated_at = outbox.updated_at

    with pytest.raises(OutboundSafetyError):
        outbound_delivery_state.cancel_safe_outbox(
            db_session,
            outbox_id=int(outbox.id),
            expected_status=unsafe_status,
            expected_updated_at=expected_updated_at,
            reason_type="manual_cancel",
            safe_summary="不得取消不安全 leaf",
            now=NOW + timedelta(minutes=20),
        )

    db_session.expire_all()
    unchanged = db_session.get(OutboundDeliveryOutbox, int(outbox.id))
    assert unchanged is not None and unchanged.status == unsafe_status


def test_cancel_safe_outbox_rejects_inactive_pending_leaf(db_session):
    outbox = _prepare_outbox_state(db_session, "pending")
    run = db_session.get(OutboundRun, int(outbox.run_id))
    assert run is not None and run.active_outbox_id == outbox.id
    run.active_outbox_id = None
    db_session.flush()

    with pytest.raises(OutboundFencingError):
        outbound_delivery_state.cancel_safe_outbox(
            db_session,
            outbox_id=int(outbox.id),
            expected_status="pending",
            expected_updated_at=outbox.updated_at,
            reason_type="manual_cancel",
            safe_summary="不得取消非活动 leaf",
            now=NOW + timedelta(minutes=20),
        )

    db_session.expire_all()
    unchanged = db_session.get(OutboundDeliveryOutbox, int(outbox.id))
    assert unchanged is not None and unchanged.status == "pending"


@pytest.mark.parametrize("stale_field", ["status", "updated_at"])
def test_cancel_safe_outbox_uses_status_and_updated_at_cas(
    db_session,
    stale_field,
):
    outbox = _prepare_outbox_state(db_session, "pending")
    expected_status = "retry_wait" if stale_field == "status" else "pending"
    expected_updated_at = (
        outbox.updated_at - timedelta(microseconds=1)
        if stale_field == "updated_at"
        else outbox.updated_at
    )

    with pytest.raises(OutboundFencingError):
        outbound_delivery_state.cancel_safe_outbox(
            db_session,
            outbox_id=int(outbox.id),
            expected_status=expected_status,
            expected_updated_at=expected_updated_at,
            reason_type="manual_cancel",
            safe_summary="过期管理请求",
            now=NOW + timedelta(minutes=20),
        )

    db_session.expire_all()
    unchanged = db_session.get(OutboundDeliveryOutbox, int(outbox.id))
    assert unchanged is not None and unchanged.status == "pending"


def test_cancel_before_send_is_fenced_and_does_not_consume_network_budget(
    db_session,
):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None

    cancelled = cancel_delivery_before_send(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        reason_type="shutdown",
        safe_summary="worker 正常停止",
        now=NOW + timedelta(seconds=1),
    )

    assert cancelled.outbox_status == "cancelled"
    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    attempt = db_session.get(OutboundDeliveryAttempt, delivery.attempt_id)
    run = db_session.get(OutboundRun, queued.run_id)
    assert outbox is not None and outbox.request_started_count == 0
    assert outbox.status == "cancelled"
    assert attempt is not None and attempt.status == "cancelled_before_send"
    assert run is not None and run.status == "failed"

    with pytest.raises(OutboundFencingError):
        mark_delivery_request_started(
            db_session,
            outbox_id=queued.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            now=NOW + timedelta(seconds=2),
        )


@pytest.mark.parametrize("invalid_identity", ["owner", "token", "expired"])
def test_cancel_before_send_rejects_invalid_or_expired_lease(
    db_session,
    invalid_identity,
):
    _seed_control(db_session)
    claim, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=5,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    owner = "worker-b" if invalid_identity == "owner" else delivery.worker_owner
    token = "wrong-token" if invalid_identity == "token" else delivery.lease_token
    cancel_at = (
        NOW + timedelta(seconds=6)
        if invalid_identity == "expired"
        else NOW + timedelta(seconds=1)
    )

    with pytest.raises(OutboundFencingError):
        cancel_delivery_before_send(
            db_session,
            outbox_id=queued.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=owner,
            lease_token=token,
            reason_type="shutdown",
            safe_summary="worker 正常停止",
            now=cancel_at,
        )

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    attempt = db_session.get(OutboundDeliveryAttempt, delivery.attempt_id)
    run = db_session.get(OutboundRun, claim.run_id)
    assert outbox is not None and outbox.status == "leased"
    assert outbox.request_started_count == 0
    assert attempt is not None and attempt.status == "started"
    assert run is not None and run.status == "delivering"


def test_cancel_after_request_boundary_is_rejected_without_state_change(db_session):
    _seed_control(db_session)
    claim, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(OutboundSafetyError, match="request boundary|取消"):
        cancel_delivery_before_send(
            db_session,
            outbox_id=queued.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            reason_type="shutdown",
            safe_summary="worker 正常停止",
            now=NOW + timedelta(seconds=2),
        )

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    attempt = db_session.get(OutboundDeliveryAttempt, delivery.attempt_id)
    run = db_session.get(OutboundRun, claim.run_id)
    assert outbox is not None and outbox.status == "leased"
    assert outbox.request_started_count == 1
    assert attempt is not None and attempt.status == "started"
    assert attempt.request_started is True
    assert run is not None and run.status == "delivering"


def test_resolve_legacy_ambiguous_outreach_cancels_without_replay(db_session):
    row = ProactiveOutreachLog(
        user_id="opaque-user",
        idempotency_key="legacy-ambiguous-hold-1",
        grounding_json='{"topic":"旧外呼"}',
        judge_should=True,
        judge_reason="旧版本可能已发出",
        next_check_at=NOW + timedelta(days=1),
        next_intent="等待人工处理",
        message="保留原始外呼消息",
        status="legacy_ambiguous_hold",
        forced=False,
        outbound_run_id=None,
        created_at=NOW - timedelta(hours=2),
    )
    db_session.add(row)
    db_session.flush()
    original = {
        "grounding_json": row.grounding_json,
        "message": row.message,
        "created_at": row.created_at,
    }
    source_revision = (
        outbound_delivery_state.proactive_outreach_source_revision(row)
    )

    resolved = outbound_delivery_state.resolve_legacy_ambiguous_outreach(
        db_session,
        outreach_log_id=int(row.id),
        expected_created_at=row.created_at,
        expected_source_revision=source_revision,
        resolution="cancel_without_replay",
        reason="管理员确认不重放旧外呼",
        now=NOW,
    )

    assert resolved.applied is True
    assert resolved.status == "cancelled"
    db_session.expire_all()
    current = db_session.get(ProactiveOutreachLog, int(row.id))
    assert current is not None and current.status == "cancelled"
    assert current.grounding_json == original["grounding_json"]
    assert current.message == original["message"]
    assert current.created_at == original["created_at"]
    assert current.outbound_run_id is None
    assert db_session.query(OutboundRun).count() == 0
    assert db_session.query(OutboundDeliveryOutbox).count() == 0

    repeated = outbound_delivery_state.resolve_legacy_ambiguous_outreach(
        db_session,
        outreach_log_id=int(row.id),
        expected_created_at=original["created_at"],
        expected_source_revision=source_revision,
        resolution="cancel_without_replay",
        reason="管理员确认不重放旧外呼",
        now=NOW + timedelta(seconds=1),
    )
    assert repeated.applied is False
    assert repeated.status == "cancelled"
    assert db_session.query(OutboundRun).count() == 0
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


@pytest.mark.parametrize(
    ("initial_status", "resolution"),
    [
        ("sent", "cancel_without_replay"),
        ("legacy_ambiguous_hold", "mark_sent"),
    ],
)
def test_resolve_legacy_ambiguous_outreach_rejects_other_state_or_success(
    db_session,
    initial_status,
    resolution,
):
    row = ProactiveOutreachLog(
        user_id="opaque-user",
        idempotency_key=f"legacy-resolve-rejected-{initial_status}-{resolution}",
        grounding_json="{}",
        judge_should=True,
        judge_reason="旧状态",
        next_check_at=None,
        next_intent="",
        message="不能制造历史成功",
        status=initial_status,
        forced=False,
        outbound_run_id=None,
        created_at=NOW - timedelta(hours=1),
    )
    db_session.add(row)
    db_session.flush()
    source_revision = (
        outbound_delivery_state.proactive_outreach_source_revision(row)
    )

    with pytest.raises((OutboundSafetyError, OutboundFencingError, ValueError)):
        outbound_delivery_state.resolve_legacy_ambiguous_outreach(
            db_session,
            outreach_log_id=int(row.id),
            expected_created_at=row.created_at,
            expected_source_revision=source_revision,
            resolution=resolution,
            reason="拒绝不安全解析",
            now=NOW,
        )

    db_session.expire_all()
    unchanged = db_session.get(ProactiveOutreachLog, int(row.id))
    assert unchanged is not None and unchanged.status == initial_status
    assert unchanged.outbound_run_id is None
    assert db_session.query(OutboundRun).count() == 0
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_concurrent_generation_start_has_one_winner(tmp_path):
    engine, factory = _file_session_factory(tmp_path, "generation-start-race.db")
    try:
        with factory() as setup:
            _seed_control(setup)
            claim = _claim_run(setup)
            setup.commit()
        barrier = threading.Barrier(2)

        def start_from(_index: int):
            with factory() as db:
                barrier.wait(timeout=5)
                try:
                    result = _start_generation(
                        db,
                        claim,
                        now=NOW + timedelta(seconds=1),
                    )
                    db.commit()
                    return result.status
                except OutboundFencingError:
                    db.rollback()
                    return "fenced"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(start_from, range(2)))

        assert sorted(results) == ["fenced", "started"]
        with factory() as observer:
            attempts = observer.query(OutboundGenerationAttempt).all()
            assert len(attempts) == 1
            assert attempts[0].status == "started"
            run = observer.get(OutboundRun, claim.run_id)
            assert run is not None and run.status == "generating"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_concurrent_request_boundary_increments_budget_once(tmp_path):
    engine, factory = _file_session_factory(tmp_path, "request-boundary-race.db")
    try:
        with factory() as setup:
            _seed_control(setup)
            _, _, queued = _queue_outbox(setup)
            delivery = claim_due_outbox(
                setup,
                worker_owner="worker-a",
                lease_seconds=30,
                endpoint_config_revision="qq-config-r1",
                now=NOW,
            )
            assert delivery is not None
            setup.commit()
        barrier = threading.Barrier(2)

        def mark_from(_index: int):
            with factory() as db:
                barrier.wait(timeout=5)
                result = mark_delivery_request_started(
                    db,
                    outbox_id=queued.outbox_id,
                    attempt_id=delivery.attempt_id,
                    worker_owner=delivery.worker_owner,
                    lease_token=delivery.lease_token,
                    now=NOW + timedelta(seconds=1),
                )
                db.commit()
                return result.applied

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(mark_from, range(2)))

        assert sorted(results) == [False, True]
        with factory() as observer:
            outbox = observer.get(OutboundDeliveryOutbox, queued.outbox_id)
            attempt = observer.get(OutboundDeliveryAttempt, delivery.attempt_id)
            assert outbox is not None and outbox.request_started_count == 1
            assert attempt is not None and attempt.request_started is True
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_concurrent_control_transition_has_one_winner(tmp_path):
    engine, factory = _file_session_factory(tmp_path, "control-transition-race.db")
    try:
        with factory() as setup:
            _seed_control(
                setup,
                mode="legacy_direct",
                epoch=0,
                protocol_version=2,
                writer_owner="producer-a",
                writer_token="writer-a",
                writer_lease_expires_at=NOW + timedelta(minutes=10),
            )
            setup.commit()
        barrier = threading.Barrier(2)

        def transition_from(_index: int):
            with factory() as db:
                barrier.wait(timeout=5)
                try:
                    result = transition_delivery_control(
                        db,
                        source_type=SOURCE_TYPE,
                        expected_mode="legacy_direct",
                        new_mode="outbox_hold",
                        expected_writer_version=0,
                        actor_owner="producer-a",
                        actor_token="writer-a",
                        protocol_version=2,
                        effective_from=NOW + timedelta(minutes=5),
                        writer_lease_seconds=600,
                        now=NOW,
                    )
                    db.commit()
                    return result.applied
                except OutboundFencingError:
                    db.rollback()
                    return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(transition_from, range(2)))

        assert sorted(results) == [False, True]
        with factory() as observer:
            control = observer.get(OutboundDeliveryControl, SOURCE_TYPE)
            assert control is not None and control.mode == "outbox_hold"
            assert control.cutover_epoch == 1
            assert control.writer_version == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_settlement_and_lease_expiry_cannot_overwrite_each_other(tmp_path):
    engine, factory = _file_session_factory(tmp_path, "settle-expiry-race.db")
    try:
        with factory() as setup:
            _seed_control(setup)
            _, _, queued = _queue_outbox(setup)
            delivery = claim_due_outbox(
                setup,
                worker_owner="worker-a",
                lease_seconds=5,
                endpoint_config_revision="qq-config-r1",
                now=NOW,
            )
            assert delivery is not None
            mark_delivery_request_started(
                setup,
                outbox_id=queued.outbox_id,
                attempt_id=delivery.attempt_id,
                worker_owner=delivery.worker_owner,
                lease_token=delivery.lease_token,
                now=NOW + timedelta(seconds=1),
            )
            setup.commit()
        barrier = threading.Barrier(2)

        def settle():
            with factory() as db:
                barrier.wait(timeout=5)
                try:
                    result = settle_delivery_attempt(
                        db,
                        outbox_id=queued.outbox_id,
                        attempt_id=delivery.attempt_id,
                        worker_owner=delivery.worker_owner,
                        lease_token=delivery.lease_token,
                        outcome="succeeded",
                        transport_phase="response_received",
                        http_status=200,
                        result_category="success",
                        error_type="",
                        safe_summary="ok",
                        duration_ms=10,
                        now=NOW + timedelta(seconds=4),
                    )
                    db.commit()
                    return result.applied
                except (OutboundConflictError, OutboundFencingError):
                    db.rollback()
                    return False

        def expire():
            with factory() as db:
                barrier.wait(timeout=5)
                result = expire_stale_delivery_leases(
                    db,
                    now=NOW + timedelta(seconds=6),
                )
                db.commit()
                return result.ambiguous

        with ThreadPoolExecutor(max_workers=2) as pool:
            settled_future = pool.submit(settle)
            expired_future = pool.submit(expire)
            settled = settled_future.result(timeout=10)
            expired = expired_future.result(timeout=10)

        assert (settled, expired) in {(True, 0), (False, 1)}
        with factory() as observer:
            outbox = observer.get(OutboundDeliveryOutbox, queued.outbox_id)
            attempt = observer.get(OutboundDeliveryAttempt, delivery.attempt_id)
            run = observer.get(OutboundRun, queued.run_id)
            assert outbox is not None and attempt is not None and run is not None
            if settled:
                assert (outbox.status, attempt.status, run.status) == (
                    "delivered",
                    "succeeded",
                    "succeeded",
                )
            else:
                assert (outbox.status, attempt.status, run.status) == (
                    "ambiguous",
                    "ambiguous",
                    "ambiguous",
                )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_generation_failure_and_takeover_remain_cross_table_consistent(tmp_path):
    engine, factory = _file_session_factory(tmp_path, "generation-fail-race.db")
    try:
        with factory() as setup:
            _seed_control(setup)
            first = _claim_run(
                setup,
                claim_lease_seconds=5,
                writer_lease_seconds=5,
            )
            first_attempt = _start_generation(setup, first)
            setup.commit()
        barrier = threading.Barrier(2)

        def fail_old():
            with factory() as db:
                barrier.wait(timeout=5)
                try:
                    fail_outbound_generation(
                        db,
                        run_id=first.run_id,
                        generation_attempt_id=first_attempt.attempt_id,
                        owner=first.owner,
                        claim_token=first.claim_token,
                        error_type="model_error",
                        error_summary="old owner",
                        now=NOW + timedelta(seconds=4),
                    )
                    db.commit()
                    return "failed"
                except OutboundFencingError:
                    db.rollback()
                    return "fenced"

        def takeover():
            with factory() as db:
                barrier.wait(timeout=5)
                claim = _claim_run(
                    db,
                    owner="producer-b",
                    writer_owner="producer-b",
                    writer_token="writer-b",
                    claim_lease_seconds=60,
                    writer_lease_seconds=60,
                    now=NOW + timedelta(seconds=6),
                )
                if not claim.acquired:
                    db.rollback()
                    return "not_acquired"
                start_generation_attempt(
                    db,
                    run_id=claim.run_id,
                    owner=claim.owner,
                    claim_token=claim.claim_token,
                    writer_owner="producer-b",
                    writer_token="writer-b",
                    writer_protocol_version=2,
                    endpoint_key=ENDPOINT_KEY,
                    destination_fingerprint=DESTINATION_FINGERPRINT,
                    endpoint_config_revision="qq-config-r1",
                    payload_contract_fingerprint=PAYLOAD_CONTRACT_FINGERPRINT,
                    now=NOW + timedelta(seconds=6),
                )
                db.commit()
                return "acquired"

        with ThreadPoolExecutor(max_workers=2) as pool:
            failed_future = pool.submit(fail_old)
            takeover_future = pool.submit(takeover)
            failure_result = failed_future.result(timeout=10)
            takeover_result = takeover_future.result(timeout=10)

        assert (failure_result, takeover_result) in {
            ("failed", "not_acquired"),
            ("fenced", "acquired"),
        }
        with factory() as observer:
            run = observer.get(OutboundRun, first.run_id)
            attempts = (
                observer.query(OutboundGenerationAttempt)
                .order_by(OutboundGenerationAttempt.attempt_no)
                .all()
            )
            assert run is not None
            if takeover_result == "acquired":
                assert run.status == "generating"
                assert [row.status for row in attempts] == ["abandoned", "started"]
            else:
                assert run.status == "failed"
                assert [row.status for row in attempts] == ["failed"]
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.parametrize("first_name", ["settle", "expire"])
def test_settlement_and_expiry_cover_both_commit_orders(
    tmp_path,
    first_name,
):
    engine, factory = _file_session_factory(
        tmp_path,
        f"settle-expiry-{first_name}-first.db",
    )
    try:
        with factory() as setup:
            _seed_control(setup)
            _, _, queued = _queue_outbox(setup)
            delivery = claim_due_outbox(
                setup,
                worker_owner="worker-a",
                lease_seconds=5,
                endpoint_config_revision="qq-config-r1",
                now=NOW,
            )
            assert delivery is not None
            mark_delivery_request_started(
                setup,
                outbox_id=queued.outbox_id,
                attempt_id=delivery.attempt_id,
                worker_owner=delivery.worker_owner,
                lease_token=delivery.lease_token,
                now=NOW + timedelta(seconds=1),
            )
            setup.commit()

        def settle():
            with factory() as db:
                try:
                    result = settle_delivery_attempt(
                        db,
                        outbox_id=queued.outbox_id,
                        attempt_id=delivery.attempt_id,
                        worker_owner=delivery.worker_owner,
                        lease_token=delivery.lease_token,
                        outcome="succeeded",
                        transport_phase="response_received",
                        http_status=200,
                        result_category="success",
                        error_type="",
                        safe_summary="ok",
                        duration_ms=10,
                        now=NOW + timedelta(seconds=4),
                    )
                    db.commit()
                    return result.applied
                except (OutboundConflictError, OutboundFencingError):
                    db.rollback()
                    return False

        def expire():
            with factory() as db:
                result = expire_stale_delivery_leases(
                    db,
                    now=NOW + timedelta(seconds=6),
                )
                db.commit()
                return result.ambiguous

        operations = {"settle": settle, "expire": expire}
        second_name = "expire" if first_name == "settle" else "settle"
        results = _run_forced_source_lock_order(
            engine,
            first_name=first_name,
            first_operation=operations[first_name],
            second_name=second_name,
            second_operation=operations[second_name],
        )

        with factory() as observer:
            outbox = observer.get(OutboundDeliveryOutbox, queued.outbox_id)
            attempt = observer.get(OutboundDeliveryAttempt, delivery.attempt_id)
            run = observer.get(OutboundRun, queued.run_id)
            assert outbox is not None and attempt is not None and run is not None
            assert outbox.lease_owner is None
            assert outbox.lease_token is None
            assert outbox.lease_expires_at is None
            if first_name == "settle":
                assert results == {"settle": True, "expire": 0}
                assert (outbox.status, attempt.status, run.status) == (
                    "delivered",
                    "succeeded",
                    "succeeded",
                )
                assert outbox.delivered_at is not None
                assert run.succeeded_at is not None
            else:
                assert results == {"expire": 1, "settle": False}
                assert (outbox.status, attempt.status, run.status) == (
                    "ambiguous",
                    "ambiguous",
                    "ambiguous",
                )
                assert outbox.last_error_type == "lease_expired"
                assert attempt.error_type == "lease_expired"
                assert run.failure_type == "lease_expired"
                assert run.has_ambiguous_ancestor is True
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.parametrize("first_name", ["fail", "takeover"])
def test_generation_failure_and_takeover_cover_both_commit_orders(
    tmp_path,
    first_name,
):
    engine, factory = _file_session_factory(
        tmp_path,
        f"generation-failure-{first_name}-first.db",
    )
    try:
        with factory() as setup:
            _seed_control(setup)
            first = _claim_run(
                setup,
                claim_lease_seconds=5,
                writer_lease_seconds=5,
            )
            first_attempt = _start_generation(setup, first)
            setup.commit()

        def fail():
            with factory() as db:
                try:
                    fail_outbound_generation(
                        db,
                        run_id=first.run_id,
                        generation_attempt_id=first_attempt.attempt_id,
                        owner=first.owner,
                        claim_token=first.claim_token,
                        error_type="model_error",
                        error_summary="old owner",
                        now=NOW + timedelta(seconds=4),
                    )
                    db.commit()
                    return "failed"
                except OutboundFencingError:
                    db.rollback()
                    return "fenced"

        def takeover():
            with factory() as db:
                claim = _claim_run(
                    db,
                    owner="producer-b",
                    writer_owner="producer-b",
                    writer_token="writer-b",
                    claim_lease_seconds=60,
                    writer_lease_seconds=60,
                    now=NOW + timedelta(seconds=6),
                )
                if not claim.acquired:
                    db.rollback()
                    return "not_acquired"
                start_generation_attempt(
                    db,
                    run_id=claim.run_id,
                    owner=claim.owner,
                    claim_token=claim.claim_token,
                    writer_owner="producer-b",
                    writer_token="writer-b",
                    writer_protocol_version=2,
                    endpoint_key=ENDPOINT_KEY,
                    destination_fingerprint=DESTINATION_FINGERPRINT,
                    endpoint_config_revision="qq-config-r1",
                    payload_contract_fingerprint=PAYLOAD_CONTRACT_FINGERPRINT,
                    now=NOW + timedelta(seconds=6),
                )
                db.commit()
                return "acquired"

        operations = {"fail": fail, "takeover": takeover}
        second_name = "takeover" if first_name == "fail" else "fail"
        results = _run_forced_source_lock_order(
            engine,
            first_name=first_name,
            first_operation=operations[first_name],
            second_name=second_name,
            second_operation=operations[second_name],
        )

        with factory() as observer:
            control = observer.get(OutboundDeliveryControl, SOURCE_TYPE)
            run = observer.get(OutboundRun, first.run_id)
            attempts = (
                observer.query(OutboundGenerationAttempt)
                .order_by(OutboundGenerationAttempt.attempt_no)
                .all()
            )
            assert control is not None and run is not None
            assert observer.query(OutboundDeliveryOutbox).count() == 0
            if first_name == "fail":
                assert results == {"fail": "failed", "takeover": "not_acquired"}
                assert control.writer_owner == "producer-a"
                assert run.status == "failed"
                assert [row.status for row in attempts] == ["failed"]
                assert attempts[0].error_type == "model_error"
            else:
                assert results == {"takeover": "acquired", "fail": "fenced"}
                assert control.writer_owner == "producer-b"
                assert run.status == "generating"
                assert run.claim_owner == "producer-b"
                assert [row.status for row in attempts] == [
                    "abandoned",
                    "started",
                ]
                assert attempts[0].error_type == "claim_expired"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_multirecord_transitions_follow_caller_rollback(tmp_path):
    engine, factory = _file_session_factory(tmp_path, "caller-rollback.db")
    try:
        with factory() as setup:
            _seed_control(setup)
            claim = _claim_run(setup)
            generation = _start_generation(setup, claim)
            setup.commit()

        with factory() as writer:
            _commit_outbox(writer, claim, generation)
            writer.rollback()
        with factory() as observer:
            run = observer.get(OutboundRun, claim.run_id)
            attempt = observer.get(
                OutboundGenerationAttempt,
                generation.attempt_id,
            )
            assert observer.query(OutboundDeliveryOutbox).count() == 0
            assert run is not None and run.status == "generating"
            assert attempt is not None and attempt.status == "started"

        with factory() as writer:
            queued = _commit_outbox(writer, claim, generation)
            writer.commit()

        with factory() as worker:
            claimed = claim_due_outbox(
                worker,
                worker_owner="worker-a",
                lease_seconds=30,
                endpoint_config_revision="qq-config-r1",
                now=NOW,
            )
            assert claimed is not None
            worker.rollback()
        with factory() as observer:
            outbox = observer.get(OutboundDeliveryOutbox, queued.outbox_id)
            run = observer.get(OutboundRun, claim.run_id)
            assert outbox is not None and outbox.status == "pending"
            assert outbox.allocated_attempt_count == 0
            assert observer.query(OutboundDeliveryAttempt).count() == 0
            assert run is not None and run.status == "queued"

        with factory() as worker:
            delivery = claim_due_outbox(
                worker,
                worker_owner="worker-a",
                lease_seconds=30,
                endpoint_config_revision="qq-config-r1",
                now=NOW,
            )
            assert delivery is not None
            worker.commit()

        with factory() as worker:
            mark_delivery_request_started(
                worker,
                outbox_id=queued.outbox_id,
                attempt_id=delivery.attempt_id,
                worker_owner=delivery.worker_owner,
                lease_token=delivery.lease_token,
                now=NOW + timedelta(seconds=1),
            )
            worker.rollback()
        with factory() as observer:
            outbox = observer.get(OutboundDeliveryOutbox, queued.outbox_id)
            attempt = observer.get(OutboundDeliveryAttempt, delivery.attempt_id)
            assert outbox is not None and outbox.request_started_count == 0
            assert attempt is not None and attempt.request_started is False

        with factory() as worker:
            mark_delivery_request_started(
                worker,
                outbox_id=queued.outbox_id,
                attempt_id=delivery.attempt_id,
                worker_owner=delivery.worker_owner,
                lease_token=delivery.lease_token,
                now=NOW + timedelta(seconds=1),
            )
            worker.commit()

        with factory() as worker:
            settle_delivery_attempt(
                worker,
                outbox_id=queued.outbox_id,
                attempt_id=delivery.attempt_id,
                worker_owner=delivery.worker_owner,
                lease_token=delivery.lease_token,
                outcome="permanent_failure",
                transport_phase="response_received",
                http_status=401,
                result_category="endpoint",
                error_type="unauthorized",
                safe_summary="auth failed",
                duration_ms=10,
                circuit_scope_type="endpoint",
                now=NOW + timedelta(seconds=2),
            )
            worker.rollback()
        with factory() as observer:
            outbox = observer.get(OutboundDeliveryOutbox, queued.outbox_id)
            attempt = observer.get(OutboundDeliveryAttempt, delivery.attempt_id)
            run = observer.get(OutboundRun, claim.run_id)
            assert outbox is not None and outbox.status == "leased"
            assert attempt is not None and attempt.status == "started"
            assert run is not None and run.status == "delivering"
            assert observer.query(OutboundDeliveryCircuit).count() == 0

        with factory() as worker:
            expire_stale_delivery_leases(
                worker,
                now=NOW + timedelta(seconds=31),
            )
            worker.commit()
        with factory() as admin:
            create_delivery_replay(
                admin,
                parent_outbox_id=queued.outbox_id,
                manual_request_key="rollback-replay",
                confirm_duplicate_risk=True,
                reason="验证 rollback",
                max_attempts=1,
                retry_deadline_at=NOW + timedelta(hours=2),
                endpoint_config_revision="qq-config-r2",
                now=NOW + timedelta(seconds=32),
            )
            admin.rollback()
        with factory() as observer:
            run = observer.get(OutboundRun, claim.run_id)
            rows = observer.query(OutboundDeliveryOutbox).all()
            assert len(rows) == 1 and rows[0].status == "ambiguous"
            assert run is not None and run.active_outbox_id == queued.outbox_id
            assert run.status == "ambiguous"

        secondary_source = "proactive_outreach"
        with factory() as setup:
            setup.add(OutboundDeliveryControl(
                source_type=secondary_source,
                mode="legacy_direct",
                cutover_epoch=0,
                effective_from=NOW - timedelta(hours=1),
                protocol_version=2,
                writer_version=0,
                writer_owner="producer-b",
                writer_token="writer-b",
                writer_lease_expires_at=NOW + timedelta(minutes=10),
                created_at=NOW - timedelta(days=1),
                updated_at=NOW - timedelta(days=1),
            ))
            setup.commit()
        with factory() as admin:
            transition_delivery_control(
                admin,
                source_type=secondary_source,
                expected_mode="legacy_direct",
                new_mode="outbox_hold",
                expected_writer_version=0,
                actor_owner="producer-b",
                actor_token="writer-b",
                protocol_version=2,
                effective_from=NOW + timedelta(minutes=5),
                writer_lease_seconds=600,
                now=NOW,
            )
            admin.rollback()
        with factory() as observer:
            control = observer.get(OutboundDeliveryControl, secondary_source)
            assert control is not None and control.mode == "legacy_direct"
            assert control.cutover_epoch == 0
            assert control.writer_version == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_legal_draining_transition_consumes_existing_current_epoch(db_session):
    _seed_control(db_session, mode="outbox_active", epoch=7)
    _, _, queued = _queue_outbox(db_session)
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None

    draining = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="outbox_active",
        new_mode="outbox_draining",
        expected_writer_version=int(control.writer_version),
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=NOW + timedelta(minutes=5),
        writer_lease_seconds=600,
        now=NOW + timedelta(seconds=1),
    )
    assert draining.mode == "outbox_draining"
    assert draining.cutover_epoch == 7

    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=2),
    )
    assert delivery is not None and delivery.outbox_id == queued.outbox_id


@pytest.mark.parametrize(
    "unsafe_state",
    ["pending", "retry_wait", "leased", "blocked", "ambiguous", "started"],
)
def test_draining_to_legacy_rejects_every_unsafe_queue_or_attempt_state(
    db_session,
    unsafe_state,
):
    _seed_control(db_session, mode="outbox_active", epoch=7)
    _, _, queued = _queue_outbox(db_session)
    if unsafe_state in {"retry_wait", "leased", "ambiguous", "started"}:
        delivery = claim_due_outbox(
            db_session,
            worker_owner="worker-a",
            lease_seconds=30,
            endpoint_config_revision="qq-config-r1",
            now=NOW,
        )
        assert delivery is not None
        if unsafe_state in {"retry_wait", "ambiguous"}:
            mark_delivery_request_started(
                db_session,
                outbox_id=queued.outbox_id,
                attempt_id=delivery.attempt_id,
                worker_owner=delivery.worker_owner,
                lease_token=delivery.lease_token,
                now=NOW + timedelta(seconds=1),
            )
            settle_delivery_attempt(
                db_session,
                outbox_id=queued.outbox_id,
                attempt_id=delivery.attempt_id,
                worker_owner=delivery.worker_owner,
                lease_token=delivery.lease_token,
                outcome=(
                    "transient_failure"
                    if unsafe_state == "retry_wait"
                    else "ambiguous"
                ),
                transport_phase=(
                    "response_received"
                    if unsafe_state == "retry_wait"
                    else "read"
                ),
                http_status=503 if unsafe_state == "retry_wait" else None,
                result_category=(
                    "transient"
                    if unsafe_state == "retry_wait"
                    else "ambiguous"
                ),
                error_type=(
                    "service_unavailable"
                    if unsafe_state == "retry_wait"
                    else "read_timeout"
                ),
                safe_summary="unsafe state",
                duration_ms=10,
                retry_at=(
                    NOW + timedelta(minutes=1)
                    if unsafe_state == "retry_wait"
                    else None
                ),
                now=NOW + timedelta(seconds=2),
            )
        elif unsafe_state == "started":
            outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
            run = db_session.get(OutboundRun, queued.run_id)
            assert outbox is not None and run is not None
            outbox.status = "failed"
            outbox.lease_owner = None
            outbox.lease_token = None
            outbox.lease_expires_at = None
            outbox.last_error_type = "inconsistent_started_attempt"
            run.status = "failed"
            run.failure_type = "inconsistent_started_attempt"
            db_session.flush()
    elif unsafe_state == "blocked":
        db_session.add(OutboundDeliveryCircuit(
            scope_type="endpoint",
            scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
            config_revision="qq-config-r1",
            status="open",
            reason_type="unauthorized",
            opened_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        ))
        db_session.flush()
        assert claim_due_outbox(
            db_session,
            worker_owner="worker-a",
            lease_seconds=30,
            endpoint_config_revision="qq-config-r1",
            now=NOW + timedelta(seconds=1),
        ) is None

    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None
    draining = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="outbox_active",
        new_mode="outbox_draining",
        expected_writer_version=int(control.writer_version),
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=NOW + timedelta(minutes=5),
        writer_lease_seconds=600,
        now=NOW + timedelta(seconds=3),
    )

    with pytest.raises(OutboundSafetyError, match="draining|队列|attempt"):
        transition_delivery_control(
            db_session,
            source_type=SOURCE_TYPE,
            expected_mode="outbox_draining",
            new_mode="legacy_direct",
            expected_writer_version=draining.writer_version,
            actor_owner="producer-a",
            actor_token="writer-a",
            protocol_version=2,
            effective_from=draining.effective_from,
            writer_lease_seconds=600,
            now=NOW + timedelta(minutes=5),
        )

    db_session.expire_all()
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None and control.mode == "outbox_draining"
    assert control.cutover_epoch == 7


def test_same_occurrence_is_not_reclaimed_across_forward_and_rollback_cutover(
    db_session,
):
    _seed_control(db_session, mode="legacy_direct", epoch=0)
    first = _claim_run(db_session, occurrence_key="stable-cutover-occurrence")
    assert first.acquired is True
    assert first.delivery_mode == "legacy_direct"
    generation = _start_generation(db_session, first)
    fail_outbound_generation(
        db_session,
        run_id=first.run_id,
        generation_attempt_id=generation.attempt_id,
        owner=first.owner,
        claim_token=first.claim_token,
        error_type="model_error",
        error_summary="生成失败",
        now=NOW + timedelta(seconds=1),
    )
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None
    hold = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="legacy_direct",
        new_mode="outbox_hold",
        expected_writer_version=int(control.writer_version),
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=NOW + timedelta(minutes=5),
        writer_lease_seconds=1200,
        now=NOW + timedelta(seconds=2),
    )
    active = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="outbox_hold",
        new_mode="outbox_active",
        expected_writer_version=hold.writer_version,
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=hold.effective_from,
        writer_lease_seconds=1200,
        now=NOW + timedelta(minutes=5),
    )
    draining = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="outbox_active",
        new_mode="outbox_draining",
        expected_writer_version=active.writer_version,
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=NOW + timedelta(minutes=10),
        writer_lease_seconds=1200,
        now=NOW + timedelta(minutes=5, seconds=1),
    )
    legacy = transition_delivery_control(
        db_session,
        source_type=SOURCE_TYPE,
        expected_mode="outbox_draining",
        new_mode="legacy_direct",
        expected_writer_version=draining.writer_version,
        actor_owner="producer-a",
        actor_token="writer-a",
        protocol_version=2,
        effective_from=draining.effective_from,
        writer_lease_seconds=1200,
        now=NOW + timedelta(minutes=10),
    )
    assert legacy.mode == "legacy_direct"

    repeated = _claim_run(
        db_session,
        occurrence_key="stable-cutover-occurrence",
        endpoint_config_revision="qq-config-r2",
        now=NOW + timedelta(minutes=11),
    )

    assert repeated.acquired is False
    assert repeated.run_id == first.run_id
    assert db_session.query(OutboundRun).count() == 1
    assert db_session.query(OutboundGenerationAttempt).count() == 1
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_transient_retry_wait_reuses_payload_and_allocates_next_attempt(db_session):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session, max_attempts=3)
    first = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert first is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=first.attempt_id,
        worker_owner=first.worker_owner,
        lease_token=first.lease_token,
        now=NOW + timedelta(seconds=1),
    )
    settled = settle_delivery_attempt(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=first.attempt_id,
        worker_owner=first.worker_owner,
        lease_token=first.lease_token,
        outcome="transient_failure",
        transport_phase="response_received",
        http_status=503,
        result_category="transient",
        error_type="service_unavailable",
        safe_summary="retry later",
        duration_ms=10,
        retry_at=NOW + timedelta(seconds=10),
        now=NOW + timedelta(seconds=2),
    )
    assert settled.outbox_status == "retry_wait"
    assert claim_due_outbox(
        db_session,
        worker_owner="worker-b",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=9),
    ) is None

    second = claim_due_outbox(
        db_session,
        worker_owner="worker-b",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW + timedelta(seconds=10),
    )
    assert second is not None and second.attempt_no == 2
    assert second.payload_sha256 == first.payload_sha256
    assert second.payload_json == first.payload_json


@pytest.mark.parametrize(
    ("http_status", "result_category", "error_type"),
    [
        (401, "endpoint", "unauthorized"),
        (403, "endpoint", "forbidden"),
        (404, "endpoint", "route_missing"),
        (405, "endpoint", "method_not_allowed"),
        (410, "endpoint", "route_gone"),
        (415, "endpoint", "unsupported_media_type"),
        (501, "endpoint", "not_implemented"),
        (505, "endpoint", "http_version_not_supported"),
        (401, "payload", "payload_rejected"),
        (405, "destination", "destination_missing"),
        (415, "payload", "payload_rejected"),
    ],
)
def test_stable_endpoint_failure_derives_required_circuit_scope(
    db_session,
    http_status,
    result_category,
    error_type,
):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=1),
    )

    settled = settle_delivery_attempt(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        outcome="permanent_failure",
        transport_phase="response_received",
        http_status=http_status,
        result_category=result_category,
        error_type=error_type,
        safe_summary="稳定 endpoint 错误",
        duration_ms=10,
        now=NOW + timedelta(seconds=2),
    )

    assert settled.run_status == "blocked"
    circuit = db_session.query(OutboundDeliveryCircuit).one()
    assert circuit.scope_type == "endpoint"
    assert circuit.scope_fingerprint == endpoint_circuit_fingerprint(ENDPOINT_KEY)
    next_occurrence = _claim_run(
        db_session,
        source_id=f"task-after-{http_status}",
        occurrence_key=f"slot-after-{http_status}",
        now=NOW + timedelta(seconds=3),
    )
    assert next_occurrence.acquired is False
    assert next_occurrence.status == "blocked"


@pytest.mark.parametrize(
    (
        "http_status",
        "result_category",
        "error_type",
        "wrong_scope",
    ),
    [
        (404, "destination", "destination_missing", "endpoint"),
        (415, "payload_contract", "unsupported_envelope", "endpoint"),
    ],
)
def test_stable_failure_rejects_mismatched_circuit_scope(
    db_session,
    http_status,
    result_category,
    error_type,
    wrong_scope,
):
    _seed_control(db_session)
    claim, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="circuit|scope|分类"):
        settle_delivery_attempt(
            db_session,
            outbox_id=queued.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            outcome="permanent_failure",
            transport_phase="response_received",
            http_status=http_status,
            result_category=result_category,
            error_type=error_type,
            safe_summary="稳定错误",
            duration_ms=10,
            circuit_scope_type=wrong_scope,
            now=NOW + timedelta(seconds=2),
        )

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    attempt = db_session.get(OutboundDeliveryAttempt, delivery.attempt_id)
    run = db_session.get(OutboundRun, claim.run_id)
    assert outbox is not None and outbox.status == "leased"
    assert attempt is not None and attempt.status == "started"
    assert run is not None and run.status == "delivering"
    assert db_session.query(OutboundDeliveryCircuit).count() == 0


@pytest.mark.parametrize(
    ("outcome", "http_status", "result_category"),
    [
        ("permanent_failure", 204, "endpoint"),
        ("permanent_failure", 408, "endpoint"),
        ("permanent_failure", 425, "endpoint"),
        ("permanent_failure", 429, "endpoint"),
        ("permanent_failure", 500, "endpoint"),
        ("permanent_failure", 502, "endpoint"),
        ("permanent_failure", 503, "endpoint"),
        ("permanent_failure", 504, "endpoint"),
        ("transient_failure", 400, "transient"),
        ("transient_failure", 401, "transient"),
        ("transient_failure", 403, "transient"),
        ("transient_failure", 404, "transient"),
        ("transient_failure", 405, "transient"),
        ("transient_failure", 410, "transient"),
        ("transient_failure", 413, "transient"),
        ("transient_failure", 415, "transient"),
        ("transient_failure", 422, "transient"),
        ("transient_failure", 409, "transient"),
        ("transient_failure", 418, "transient"),
        ("transient_failure", 431, "transient"),
        ("transient_failure", 501, "transient"),
        ("transient_failure", 505, "transient"),
        ("permanent_failure", 507, "endpoint"),
        ("permanent_failure", 599, "destination"),
        ("permanent_failure", None, "transient"),
        ("transient_failure", None, "endpoint"),
        ("succeeded", 204, "ambiguous"),
        ("ambiguous", 200, "success"),
    ],
)
def test_http_status_rejects_contradictory_settlement_classification(
    db_session,
    outcome,
    http_status,
    result_category,
):
    _seed_control(db_session)
    claim, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="HTTP|分类|outcome"):
        settle_delivery_attempt(
            db_session,
            outbox_id=queued.outbox_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            outcome=outcome,
            transport_phase="response_received",
            http_status=http_status,
            result_category=result_category,
            error_type="contradictory_classification",
            safe_summary="矛盾分类",
            duration_ms=10,
            now=NOW + timedelta(seconds=2),
        )

    db_session.expire_all()
    outbox = db_session.get(OutboundDeliveryOutbox, queued.outbox_id)
    attempt = db_session.get(OutboundDeliveryAttempt, delivery.attempt_id)
    run = db_session.get(OutboundRun, claim.run_id)
    assert outbox is not None and outbox.status == "leased"
    assert attempt is not None and attempt.status == "started"
    assert run is not None and run.status == "delivering"
    assert db_session.query(OutboundDeliveryCircuit).count() == 0


@pytest.mark.parametrize(
    ("http_status", "result_category"),
    [
        (400, "payload"),
        (413, "endpoint"),
        (422, "payload"),
    ],
)
def test_payload_http_failure_never_opens_shared_circuit(
    db_session,
    http_status,
    result_category,
):
    _seed_control(db_session)
    claim, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=1),
    )

    settled = settle_delivery_attempt(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        outcome="permanent_failure",
        transport_phase="response_received",
        http_status=http_status,
        result_category=result_category,
        error_type="payload_rejected",
        safe_summary="单 payload 失败",
        duration_ms=10,
        now=NOW + timedelta(seconds=2),
    )

    assert settled.outbox_status == "failed"
    assert settled.run_status == "failed"
    assert db_session.get(OutboundRun, claim.run_id).status == "failed"
    assert db_session.query(OutboundDeliveryCircuit).count() == 0


@pytest.mark.parametrize(
    ("scope_type", "http_status", "result_category", "error_type", "expected"),
    [
        (
            "endpoint",
            401,
            "endpoint",
            "unauthorized",
            endpoint_circuit_fingerprint(ENDPOINT_KEY),
        ),
        (
            "destination",
            404,
            "destination",
            "destination_missing",
            destination_circuit_fingerprint(
                ENDPOINT_KEY,
                DESTINATION_FINGERPRINT,
            ),
        ),
        (
            "payload_contract",
            415,
            "payload_contract",
            "unsupported_envelope",
            payload_contract_circuit_fingerprint(
                ENDPOINT_KEY,
                PAYLOAD_CONTRACT_FINGERPRINT,
            ),
        ),
        (
            "payload_contract",
            422,
            "payload_contract",
            "schema_contract_mismatch",
            payload_contract_circuit_fingerprint(
                ENDPOINT_KEY,
                PAYLOAD_CONTRACT_FINGERPRINT,
            ),
        ),
    ],
)
def test_permanent_failure_opens_only_requested_circuit_scope(
    db_session,
    scope_type,
    http_status,
    result_category,
    error_type,
    expected,
):
    _seed_control(db_session)
    _, _, queued = _queue_outbox(db_session)
    delivery = claim_due_outbox(
        db_session,
        worker_owner="worker-a",
        lease_seconds=30,
        endpoint_config_revision="qq-config-r1",
        now=NOW,
    )
    assert delivery is not None
    mark_delivery_request_started(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        now=NOW + timedelta(seconds=1),
    )
    settle_delivery_attempt(
        db_session,
        outbox_id=queued.outbox_id,
        attempt_id=delivery.attempt_id,
        worker_owner=delivery.worker_owner,
        lease_token=delivery.lease_token,
        outcome="permanent_failure",
        transport_phase="response_received",
        http_status=http_status,
        result_category=result_category,
        error_type=error_type,
        safe_summary="stable failure",
        duration_ms=10,
        circuit_scope_type=scope_type,
        now=NOW + timedelta(seconds=2),
    )

    rows = db_session.query(OutboundDeliveryCircuit).all()
    assert len(rows) == 1
    assert rows[0].scope_type == scope_type
    assert rows[0].scope_fingerprint == expected
    assert rows[0].config_revision == "qq-config-r1"
