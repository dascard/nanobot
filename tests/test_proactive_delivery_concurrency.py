import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from core.database import (
    Base,
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
    ProactiveOutreachLease,
    ProactiveOutreachLog,
    User,
)
from tests.async_helpers import run_async


def _session_factory(tmp_path, name):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=5000")

    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    with factory() as setup:
        setup.add(OutboundDeliveryControl(
            source_type="proactive_outreach",
            mode="legacy_direct",
            cutover_epoch=0,
            effective_from=datetime(1970, 1, 1),
            protocol_version=2,
            writer_version=0,
        ))
        setup.commit()
    return engine, factory


@pytest.mark.parametrize(
    ("initial_status", "publish_ok", "expected_final_status"),
    [
        ("candidate", True, "sent"),
        ("pending", False, "failed"),
        (None, True, "sent"),
    ],
)
def test_concurrent_delivery_claims_schedule_row_once(
    tmp_path,
    monkeypatch,
    initial_status,
    publish_ok,
    expected_final_status,
):
    from core import proactive_outreach

    engine, session_factory = _session_factory(
        tmp_path,
        f"outreach-{initial_status}.db",
    )

    setup_session = session_factory()
    try:
        if initial_status is not None:
            row = ProactiveOutreachLog(
                user_id="concurrent-user",
                idempotency_key=f"outreach:concurrent:{initial_status}",
                grounding_json="{}",
                judge_should=initial_status == "candidate",
                judge_reason="并发投递测试",
                next_intent="",
                message="预生成消息",
                status=initial_status,
                forced=False,
            )
            setup_session.add(row)
            setup_session.commit()
    finally:
        setup_session.close()

    both_rows_loaded = threading.Barrier(2)
    original_strip = proactive_outreach.strip_think_blocks

    def synchronize_after_initial_read(text):
        cleaned = original_strip(text)
        both_rows_loaded.wait(timeout=5)
        return cleaned

    monkeypatch.setattr(
        proactive_outreach,
        "strip_think_blocks",
        synchronize_after_initial_read,
    )

    publish_calls = []
    publish_lock = threading.Lock()

    async def publisher(target_type, target_id, message):
        with publish_lock:
            publish_calls.append((target_type, target_id, message))
        return publish_ok

    def deliver_from_independent_session():
        session = session_factory()
        try:
            return run_async(
                proactive_outreach.deliver_outreach_once(
                    user_id="concurrent-user",
                    idempotency_key=f"outreach:concurrent:{initial_status}",
                    grounding={"recent_messages": []},
                    judge_should=True,
                    judge_reason="并发投递测试",
                    next_check_at=None,
                    next_intent="",
                    message="并发只发一次",
                    forced=False,
                    db=session,
                    publisher=publisher,
                )
            )
        finally:
            session.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(deliver_from_independent_session) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]

        assert {result["status"] for result in results} == {
            expected_final_status,
            "skipped_duplicate",
        }
        assert publish_calls == [
            ("private", "concurrent-user", "并发只发一次"),
        ]

        verify_session = session_factory()
        try:
            stored = verify_session.query(ProactiveOutreachLog).one()
            assert stored.status == expected_final_status
            assert stored.message == "并发只发一次"
        finally:
            verify_session.close()
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_concurrent_outreach_runs_use_one_per_user_evaluation_lease(tmp_path):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "outreach-run-lease.db")
    judge_entered = threading.Event()
    release_first_judge = threading.Event()
    judge_calls = []
    publish_calls = []
    call_lock = threading.Lock()

    def judge(_grounding, *, now, **_kwargs):
        with call_lock:
            judge_calls.append(now)
            call_index = len(judge_calls)
        if call_index == 1:
            judge_entered.set()
            assert release_first_judge.wait(timeout=5)
        return {
            "should_reach_out": True,
            "reason": "并发入口只允许一个评估者",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    async def publisher(target_type, target_id, message):
        with call_lock:
            publish_calls.append((target_type, target_id, message))
        return True

    def run_at(now):
        session = session_factory()
        try:
            return run_async(
                proactive_outreach.run_outreach_once(
                    "lease-user",
                    db=session,
                    now=now,
                    judge_fn=judge,
                    generator_fn=lambda *_args, **_kwargs: "并发租约候选",
                    thread_extractor=lambda _messages: [],
                    publisher=publisher,
                )
            )
        finally:
            session.close()

    first_now = datetime(2026, 7, 10, 12, 0, 0)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(run_at, first_now)
            assert judge_entered.wait(timeout=5)
            second = executor.submit(run_at, first_now + timedelta(seconds=1))
            second_result = second.result(timeout=5)
            release_first_judge.set()
            first_result = first.result(timeout=10)

        assert {first_result["status"], second_result["status"]} == {
            "sent",
            "skipped_in_progress",
        }
        assert len(judge_calls) == 1
        assert publish_calls == [("private", "lease-user", "并发租约候选")]
        verify = session_factory()
        try:
            assert verify.query(ProactiveOutreachLog).count() == 1
        finally:
            verify.close()
    finally:
        release_first_judge.set()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_delivery_claim_rejects_stale_schedule_version(tmp_path, monkeypatch):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "outreach-stale-claim.db")
    setup = session_factory()
    try:
        row = ProactiveOutreachLog(
            user_id="stale-user",
            idempotency_key="outreach:old-plan",
            status="pending",
            message="",
            created_at=datetime(2026, 7, 10, 10, 0, 0),
        )
        setup.add(row)
        setup.commit()
        row_id = int(row.id)
    finally:
        setup.close()

    initial_read_done = threading.Event()
    allow_claim = threading.Event()
    original_strip = proactive_outreach.strip_think_blocks

    def pause_after_initial_read(text):
        cleaned = original_strip(text)
        initial_read_done.set()
        assert allow_claim.wait(timeout=5)
        return cleaned

    monkeypatch.setattr(proactive_outreach, "strip_think_blocks", pause_after_initial_read)
    published = []

    async def publisher(*args):
        published.append(args)
        return True

    def deliver_old_plan():
        session = session_factory()
        try:
            return run_async(
                proactive_outreach.deliver_outreach_once(
                    user_id="stale-user",
                    idempotency_key="outreach:old-plan",
                    grounding={"version": "old"},
                    judge_should=True,
                    judge_reason="旧决策",
                    next_check_at=None,
                    next_intent="",
                    message="旧候选",
                    forced=False,
                    db=session,
                    schedule_row_id=row_id,
                    publisher=publisher,
                )
            )
        finally:
            session.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(deliver_old_plan)
            assert initial_read_done.wait(timeout=5)
            newer = session_factory()
            try:
                current = newer.get(ProactiveOutreachLog, row_id)
                current.idempotency_key = "outreach:new-plan"
                current.judge_reason = "新决策"
                current.message = "新候选"
                newer.commit()
            finally:
                newer.close()
            allow_claim.set()
            result = future.result(timeout=10)

        assert result["status"] == "stale_schedule"
        assert published == []
        verify = session_factory()
        try:
            stored = verify.get(ProactiveOutreachLog, row_id)
            assert stored.idempotency_key == "outreach:new-plan"
            assert stored.judge_reason == "新决策"
            assert stored.message == "新候选"
            assert stored.status == "pending"
        finally:
            verify.close()
    finally:
        allow_claim.set()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_history_clear_committed_before_claim_cancels_loaded_candidate(
    tmp_path,
    monkeypatch,
):
    from api.history_log_routes import mark_clear
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "outreach-clear-race.db")
    created_at = datetime.now() - timedelta(days=1)
    setup = session_factory()
    try:
        row = ProactiveOutreachLog(
            user_id="clear-race-user",
            idempotency_key="outreach:clear-race",
            grounding_json='{"recent_threads":["旧话题"]}',
            judge_should=True,
            judge_reason="清除前决策",
            message="清除前候选",
            status="candidate",
            created_at=created_at,
        )
        setup.add(row)
        setup.commit()
        row_id = int(row.id)
    finally:
        setup.close()

    initial_read_done = threading.Event()
    allow_claim = threading.Event()
    original_strip = proactive_outreach.strip_think_blocks

    def pause_after_initial_read(text):
        cleaned = original_strip(text)
        initial_read_done.set()
        assert allow_claim.wait(timeout=5)
        return cleaned

    monkeypatch.setattr(proactive_outreach, "strip_think_blocks", pause_after_initial_read)
    published = []

    async def publisher(*args):
        published.append(args)
        return True

    def deliver_loaded_candidate():
        session = session_factory()
        try:
            return run_async(
                proactive_outreach.deliver_outreach_once(
                    user_id="clear-race-user",
                    idempotency_key="outreach:clear-race",
                    grounding={"recent_threads": ["旧话题"]},
                    judge_should=True,
                    judge_reason="清除前决策",
                    next_check_at=None,
                    next_intent="",
                    message="清除前候选",
                    forced=False,
                    db=session,
                    schedule_row_id=row_id,
                    created_at=created_at,
                    publisher=publisher,
                )
            )
        finally:
            session.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(deliver_loaded_candidate)
            assert initial_read_done.wait(timeout=5)
            clear_session = session_factory()
            try:
                response = mark_clear(
                    "clear-race-user",
                    db=clear_session,
                    _auth="test",
                )
                assert response["status"] == "success"
            finally:
                clear_session.close()
            allow_claim.set()
            result = future.result(timeout=10)

        assert result["status"] == "cancelled_history_clear"
        assert published == []
        verify = session_factory()
        try:
            assert verify.get(ProactiveOutreachLog, row_id).status == "cancelled"
        finally:
            verify.close()
    finally:
        allow_claim.set()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_history_clear_committed_before_first_row_flush_cancels_old_evaluation(
    tmp_path,
    monkeypatch,
):
    from api.history_log_routes import mark_clear
    from core import proactive_outreach

    engine, session_factory = _session_factory(
        tmp_path,
        "outreach-clear-before-first-row.db",
    )
    evaluation_started_at = datetime.now() - timedelta(seconds=1)
    initial_read_done = threading.Event()
    allow_flush = threading.Event()
    original_strip = proactive_outreach.strip_think_blocks

    def pause_before_first_row_flush(text):
        cleaned = original_strip(text)
        initial_read_done.set()
        assert allow_flush.wait(timeout=5)
        return cleaned

    monkeypatch.setattr(
        proactive_outreach,
        "strip_think_blocks",
        pause_before_first_row_flush,
    )
    published = []

    async def publisher(*args):
        published.append(args)
        return True

    def deliver_old_evaluation():
        session = session_factory()
        try:
            return run_async(
                proactive_outreach.deliver_outreach_once(
                    user_id="clear-before-first-row-user",
                    idempotency_key="outreach:clear-before-first-row",
                    grounding={"recent_threads": ["清除前旧话题"]},
                    judge_should=True,
                    judge_reason="清除前旧决策",
                    next_check_at=None,
                    next_intent="",
                    message="清除前旧候选",
                    forced=False,
                    db=session,
                    created_at=evaluation_started_at,
                    publisher=publisher,
                )
            )
        finally:
            session.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(deliver_old_evaluation)
            assert initial_read_done.wait(timeout=5)
            clear_session = session_factory()
            try:
                response = mark_clear(
                    "clear-before-first-row-user",
                    db=clear_session,
                    _auth="test",
                )
                assert response["status"] == "success"
                clear_marker = clear_session.get(
                    User,
                    "clear-before-first-row-user",
                ).history_clear_at
            finally:
                clear_session.close()
            allow_flush.set()
            result = future.result(timeout=10)

        assert result["status"] == "cancelled_history_clear"
        assert published == []
        verify = session_factory()
        try:
            rows = verify.query(ProactiveOutreachLog).filter_by(
                user_id="clear-before-first-row-user"
            ).all()
            assert all(row.status == "cancelled" for row in rows)
            assert all(row.created_at <= clear_marker for row in rows)
        finally:
            verify.close()
    finally:
        allow_flush.set()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_run_outreach_lease_uses_wall_clock_instead_of_virtual_now(
    tmp_path,
    monkeypatch,
):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "outreach-wall-clock.db")
    captured = []

    def acquire(session, *, user_id, now, **_kwargs):
        captured.append((user_id, now))
        session.add(ProactiveOutreachLease(
            user_id=user_id,
            owner_token="owner",
            lease_expires_at=now + timedelta(minutes=15),
            created_at=now,
            updated_at=now,
        ))
        session.commit()
        return "owner"

    monkeypatch.setattr(
        proactive_outreach,
        "_acquire_evaluation_lease",
        acquire,
    )
    monkeypatch.setattr(
        proactive_outreach,
        "_release_evaluation_lease",
        lambda *_args, **_kwargs: None,
    )
    virtual_now = datetime(2042, 1, 1, 12, 0, 0)
    session = session_factory()
    try:
        result = run_async(
            proactive_outreach.run_outreach_once(
                "wall-clock-user",
                db=session,
                now=virtual_now,
                thread_extractor=lambda _messages: [],
                judge_fn=lambda *_args, **_kwargs: {
                    "should_reach_out": False,
                    "reason": "稍后再看",
                    "next_check_at": (virtual_now + timedelta(hours=2)).isoformat(),
                    "next_intent": "",
                    "outreach_kind": "message",
                    "research_query": "",
                    "error_type": None,
                },
            )
        )

        assert result["status"] == "pending"
        assert captured[0][0] == "wall-clock-user"
        assert captured[0][1] != virtual_now
        assert abs((datetime.now() - captured[0][1]).total_seconds()) < 5
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_expired_evaluation_lease_owner_is_fenced_before_publish(tmp_path):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "outreach-owner-fence.db")
    takeover_done = threading.Event()
    published = []

    def generator(_grounding, _reason):
        takeover = session_factory()
        try:
            row = takeover.get(ProactiveOutreachLease, "fenced-user")
            assert row is not None
            row.owner_token = "new-owner"
            row.lease_expires_at = datetime.now() + timedelta(minutes=15)
            takeover.commit()
            takeover_done.set()
        finally:
            takeover.close()
        return "旧 owner 不应发送"

    async def publisher(*args):
        published.append(args)
        return True

    session = session_factory()
    try:
        result = run_async(
            proactive_outreach.run_outreach_once(
                "fenced-user",
                db=session,
                now=datetime(2026, 7, 10, 12, 0, 0),
                thread_extractor=lambda _messages: [],
                judge_fn=lambda _grounding, *, now, **_kwargs: {
                    "should_reach_out": True,
                    "reason": "验证 owner fencing",
                    "next_check_at": (now + timedelta(hours=2)).isoformat(),
                    "next_intent": "",
                    "outreach_kind": "message",
                    "research_query": "",
                    "error_type": None,
                },
                generator_fn=generator,
                publisher=publisher,
            )
        )

        assert takeover_done.is_set()
        assert result["status"] == "lease_lost"
        assert published == []
        row = session.query(ProactiveOutreachLog).one()
        run = session.query(OutboundRun).one()
        attempt = session.query(OutboundGenerationAttempt).one()
        assert row.status == "candidate"
        assert row.message == ""
        assert row.outbound_run_id == run.id
        assert run.status == "generating"
        assert attempt.run_id == run.id
        assert attempt.status == "started"
        assert session.query(OutboundDeliveryOutbox).count() == 0
    finally:
        session.close()
        cleanup = session_factory()
        try:
            cleanup.query(ProactiveOutreachLease).delete()
            cleanup.commit()
        finally:
            cleanup.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_research_candidate_from_replaced_lease_owner_cannot_be_published(
    tmp_path,
):
    from core import proactive_outreach
    from core.proactive_research import ResearchResult, ResearchSource

    engine, session_factory = _session_factory(tmp_path, "research-owner-fence.db")
    published = []
    research_calls = []
    current = datetime(2026, 7, 10, 12, 0, 0)

    async def research(request):
        research_calls.append(request)
        if len(research_calls) == 1:
            takeover = session_factory()
            try:
                lease = takeover.get(
                    ProactiveOutreachLease,
                    "research-fence-user",
                )
                run = takeover.query(OutboundRun).one()
                assert lease is not None
                lease.owner_token = "new-owner"
                lease.lease_expires_at = datetime.now() + timedelta(minutes=15)
                run.claim_expires_at = datetime.now() - timedelta(seconds=1)
                takeover.commit()
            finally:
                takeover.close()
        draft = (
            "旧 owner 研究候选"
            if len(research_calls) == 1
            else "新 owner 研究候选"
        )
        return ResearchResult(
            request_id=request.request_id,
            trace_id=f"trace-research-owner-fence-{len(research_calls)}",
            status="draft_ready",
            draft=draft,
            sources=(
                ResearchSource("tool-1", "来源一", "https://example.test/one"),
                ResearchSource("tool-2", "来源二", "https://example.test/two"),
            ),
        )

    async def publisher(*args):
        published.append(args)
        return True

    first_session = session_factory()
    try:
        first = run_async(
            proactive_outreach.run_outreach_once(
                "research-fence-user",
                db=first_session,
                now=current,
                thread_extractor=lambda _messages: [],
                judge_fn=lambda _grounding, *, now, **_kwargs: {
                    "should_reach_out": True,
                    "reason": "旧 owner 研究决策",
                    "next_check_at": (now + timedelta(hours=2)).isoformat(),
                    "next_intent": "旧 owner 计划",
                    "outreach_kind": "research",
                    "research_query": "调查 Agent 记忆",
                    "error_type": None,
                },
                research_fn=research,
                publisher=publisher,
            )
        )
    finally:
        first_session.close()

    second_session = session_factory()
    try:
        second = run_async(
            proactive_outreach._run_outreach_once_acquired(
                "research-fence-user",
                db=second_session,
                now=current + timedelta(seconds=1),
                thread_extractor=lambda _messages: pytest.fail(
                    "恢复冻结 occurrence 不得重建 grounding"
                ),
                judge_fn=lambda *_args, **_kwargs: pytest.fail(
                    "恢复冻结 occurrence 不得重新调用 Judge"
                ),
                research_fn=research,
                publisher=publisher,
                evaluation_owner_token="new-owner",
            )
        )
        second_session.expire_all()
        row = second_session.query(ProactiveOutreachLog).one()
        attempts = (
            second_session.query(OutboundGenerationAttempt)
            .order_by(OutboundGenerationAttempt.attempt_no.asc())
            .all()
        )
        outbox = second_session.query(OutboundDeliveryOutbox).one()
        candidate_rows = second_session.query(ProactiveOutreachLog).filter(
            ProactiveOutreachLog.status == "candidate",
            ProactiveOutreachLog.message == "旧 owner 研究候选",
        ).count()

        assert first["status"] == "lease_lost"
        assert second["status"] == "sent"
        assert len(research_calls) == 2
        assert candidate_rows == 0
        assert row.status == "sent"
        assert "新 owner 研究候选" in row.message
        assert "旧 owner 研究候选" not in row.message
        assert [(item.attempt_no, item.status) for item in attempts] == [
            (1, "abandoned"),
            (2, "succeeded"),
        ]
        assert attempts[0].error_type == "claim_expired"
        assert outbox.status == "delivered"
        assert "旧 owner 研究候选" not in outbox.payload_json
        assert len(published) == 1
        assert "新 owner 研究候选" in published[0][2]
        assert "旧 owner 研究候选" not in published[0][2]
    finally:
        second_session.close()
        cleanup = session_factory()
        try:
            cleanup.query(ProactiveOutreachLease).delete()
            cleanup.commit()
        finally:
            cleanup.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_no_candidate_from_replaced_lease_owner_cannot_persist_pending(
    tmp_path,
):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "pending-owner-fence.db")
    current = datetime(2026, 7, 10, 12, 0, 0)

    def judge(_grounding, *, now, **_kwargs):
        takeover = session_factory()
        try:
            lease = takeover.get(ProactiveOutreachLease, "pending-fence-user")
            assert lease is not None
            lease.owner_token = "new-owner"
            lease.lease_expires_at = datetime.now() + timedelta(minutes=15)
            takeover.commit()
        finally:
            takeover.close()
        return {
            "should_reach_out": False,
            "reason": "旧 owner 否决",
            "next_check_at": (now + timedelta(hours=12)).isoformat(),
            "next_intent": "旧 owner 计划",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    session = session_factory()
    try:
        result = run_async(
            proactive_outreach.run_outreach_once(
                "pending-fence-user",
                db=session,
                now=current,
                max_silence_min=999999,
                thread_extractor=lambda _messages: [],
                judge_fn=judge,
            )
        )

        assert result["status"] == "lease_lost"
        assert session.query(ProactiveOutreachLog).count() == 0
        session.expire_all()
        assert session.get(
            ProactiveOutreachLease,
            "pending-fence-user",
        ).owner_token == "new-owner"
    finally:
        session.close()
        cleanup = session_factory()
        try:
            cleanup.query(ProactiveOutreachLease).delete()
            cleanup.commit()
        finally:
            cleanup.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_lease_loss_before_atomic_enqueue_discards_unpublished_candidate(
    tmp_path,
    monkeypatch,
):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "claim-lease-loss-requeue.db")
    session = session_factory()
    published = []

    async def publisher(*args):
        published.append(args)
        return True

    try:
        session.add(ProactiveOutreachLease(
            user_id="claim-lease-loss-user",
            owner_token="old-owner",
            lease_expires_at=datetime.now() + timedelta(minutes=15),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ))
        session.commit()
        monkeypatch.setattr(
            proactive_outreach,
            "_evaluation_lease_is_owned",
            lambda *_args, **_kwargs: False,
        )

        result = run_async(proactive_outreach.deliver_outreach_once(
            user_id="claim-lease-loss-user",
            idempotency_key="claim-lease-loss-key",
            grounding={"user_id": "claim-lease-loss-user"},
            judge_should=True,
            judge_reason="已有完整候选",
            next_check_at=datetime(2026, 7, 10, 14, 0, 0),
            next_intent="",
            message="尚未调用 publisher 的完整候选",
            forced=False,
            db=session,
            created_at=datetime(2026, 7, 10, 12, 0, 0),
            publisher=publisher,
            evaluation_owner_token="old-owner",
        ))

        session.expire_all()
        assert result["status"] == "lease_lost"
        assert published == []
        assert session.query(ProactiveOutreachLog).count() == 0
        assert session.query(OutboundRun).count() == 0
        assert session.query(OutboundDeliveryOutbox).count() == 0
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_stale_ambiguous_sending_does_not_retry_old_key_or_block_new_outreach(
    tmp_path,
):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "stale-sending-ambiguous.db")
    session = session_factory()
    published = []
    current = datetime(2026, 7, 10, 12, 0, 0)

    async def publisher(*args):
        published.append(args)
        return True

    try:
        session.add(ProactiveOutreachLog(
            user_id="stale-sending-user",
            idempotency_key="old-ambiguous-key",
            status="sending",
            message="可能已经送达的旧消息",
            created_at=current - timedelta(days=3),
            forced=False,
        ))
        session.commit()

        result = run_async(proactive_outreach.run_outreach_once(
            "stale-sending-user",
            db=session,
            now=current,
            max_silence_min=60,
            thread_extractor=lambda _messages: [],
            generator_fn=lambda *_args, **_kwargs: "新的最长沉默候选",
            publisher=publisher,
        ))

        session.expire_all()
        old_row = session.query(ProactiveOutreachLog).filter_by(
            idempotency_key="old-ambiguous-key"
        ).one()
        assert old_row.status == "ambiguous"
        assert result["status"] == "sent"
        assert published == [
            ("private", "stale-sending-user", "新的最长沉默候选")
        ]
        assert session.query(ProactiveOutreachLog).filter_by(
            idempotency_key="old-ambiguous-key",
            status="sent",
        ).count() == 0
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_history_clear_during_judge_invalidates_evaluation_lease(tmp_path):
    from api.history_log_routes import mark_clear
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "clear-invalidates-lease.db")
    current = datetime(2026, 7, 10, 12, 0, 0)

    def judge(_grounding, *, now, **_kwargs):
        clear_session = session_factory()
        try:
            response = mark_clear(
                "clear-invalidates-lease-user",
                db=clear_session,
                _auth="test",
            )
            assert response["status"] == "success"
        finally:
            clear_session.close()
        return {
            "should_reach_out": False,
            "reason": "清除前评估不应落库",
            "next_check_at": (now + timedelta(hours=12)).isoformat(),
            "next_intent": "清除前计划",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    session = session_factory()
    try:
        result = run_async(
            proactive_outreach.run_outreach_once(
                "clear-invalidates-lease-user",
                db=session,
                now=current,
                max_silence_min=999999,
                thread_extractor=lambda _messages: [],
                judge_fn=judge,
            )
        )

        assert result["status"] == "lease_lost"
        assert session.query(ProactiveOutreachLease).count() == 0
        assert session.query(ProactiveOutreachLog).filter(
            ProactiveOutreachLog.status.in_(("pending", "candidate"))
        ).count() == 0
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_history_clear_during_generator_terminalizes_generation_ledger(
    tmp_path,
    monkeypatch,
):
    from api.history_log_routes import mark_clear
    from core import proactive_outreach

    engine, session_factory = _session_factory(
        tmp_path,
        "clear-terminalizes-generation.db",
    )
    user_id = "clear-terminalizes-generation-user"
    current = datetime.now() + timedelta(days=365)
    clear_responses = []
    published = []

    def generator(_grounding, _reason):
        clear_session = session_factory()
        try:
            clear_responses.append(mark_clear(
                user_id,
                db=clear_session,
                _auth="test",
            ))
        finally:
            clear_session.close()
        return "清除前生成的正文不得进入投递队列"

    async def publisher(*args):
        published.append(args)
        return True

    session = session_factory()
    try:
        first = run_async(
            proactive_outreach.run_outreach_once(
                user_id,
                db=session,
                now=current,
                max_silence_min=999999,
                thread_extractor=lambda _messages: [],
                judge_fn=lambda _grounding, *, now, **_kwargs: {
                    "should_reach_out": True,
                    "reason": "验证生成期间历史清除",
                    "next_check_at": (now + timedelta(hours=2)).isoformat(),
                    "next_intent": "",
                    "outreach_kind": "message",
                    "research_query": "",
                    "error_type": None,
                },
                generator_fn=generator,
                publisher=publisher,
            )
        )

        session.expire_all()
        row = session.query(ProactiveOutreachLog).one()
        run = session.query(OutboundRun).one()
        attempt = session.query(OutboundGenerationAttempt).one()

        assert first["status"] == "lease_lost"
        assert clear_responses[0]["cancelled_outreach_deliveries"] == 1
        assert clear_responses[0]["unsafe_outreach_deliveries"] == 0
        assert published == []
        assert row.status == "cancelled"
        assert row.message == ""
        assert row.outbound_run_id == run.id
        assert run.status == "failed"
        assert run.failure_type == "history_cleared"
        assert run.claim_owner is None
        assert run.claim_token is None
        assert run.claim_expires_at is None
        assert run.active_outbox_id is None
        assert attempt.run_id == run.id
        assert attempt.status == "abandoned"
        assert attempt.error_type == "history_cleared"
        assert attempt.completed_at is not None
        assert session.query(OutboundDeliveryOutbox).count() == 0

        second = run_async(
            proactive_outreach.run_outreach_once(
                user_id,
                db=session,
                now=current + timedelta(minutes=1),
                max_silence_min=999999,
                thread_extractor=lambda _messages: [],
                judge_fn=lambda _grounding, *, now, **_kwargs: {
                    "should_reach_out": False,
                    "reason": "清除后重新建立调度",
                    "next_check_at": (now + timedelta(hours=2)).isoformat(),
                    "next_intent": "",
                    "outreach_kind": "message",
                    "research_query": "",
                    "error_type": None,
                },
            )
        )

        session.expire_all()
        rows = (
            session.query(ProactiveOutreachLog)
            .order_by(ProactiveOutreachLog.id.asc())
            .all()
        )
        assert second["status"] == "pending"
        assert len(rows) == 2
        assert rows[0].status == "cancelled"
        assert rows[0].outbound_run_id == run.id
        assert rows[1].status == "pending"
        assert rows[1].outbound_run_id is None
        assert session.get(OutboundRun, run.id).status == "failed"
        assert session.get(
            OutboundGenerationAttempt,
            attempt.id,
        ).status == "abandoned"

        pre_clear_key = rows[0].idempotency_key
        monkeypatch.setattr(
            proactive_outreach,
            "_outreach_key",
            lambda *_args, **_kwargs: pre_clear_key,
        )

        def no_candidate(_grounding, *, now, **_kwargs):
            return {
                "should_reach_out": False,
                "reason": "同 key 清除后调度",
                "next_check_at": (now + timedelta(hours=2)).isoformat(),
                "next_intent": "",
                "outreach_kind": "message",
                "research_query": "",
                "error_type": None,
            }

        same_key = run_async(
            proactive_outreach.run_outreach_once(
                user_id,
                db=session,
                now=current + timedelta(minutes=2),
                max_silence_min=999999,
                thread_extractor=lambda _messages: [],
                judge_fn=no_candidate,
            )
        )
        repeated = run_async(
            proactive_outreach.run_outreach_once(
                user_id,
                db=session,
                now=current + timedelta(minutes=3),
                max_silence_min=999999,
                thread_extractor=lambda _messages: [],
                judge_fn=no_candidate,
            )
        )

        session.expire_all()
        rows = (
            session.query(ProactiveOutreachLog)
            .order_by(ProactiveOutreachLog.id.asc())
            .all()
        )
        assert same_key["status"] == "pending"
        assert repeated["status"] == "pending"
        assert repeated["log_id"] == same_key["log_id"]
        assert len(rows) == 2
        assert rows[0].status == "cancelled"
        assert rows[0].idempotency_key == pre_clear_key
        assert rows[0].outbound_run_id == run.id
        assert rows[1].status == "pending"
        assert rows[1].idempotency_key != pre_clear_key
        assert rows[1].outbound_run_id is None
        assert same_key["log_id"] == rows[1].id
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_lease_release_failure_does_not_replace_successful_business_result(
    tmp_path,
    monkeypatch,
):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "release-after-success.db")
    monkeypatch.setattr(
        proactive_outreach,
        "_release_evaluation_lease",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("release failed")),
    )
    session = session_factory()
    try:
        result = run_async(
            proactive_outreach.run_outreach_once(
                "release-success-user",
                db=session,
                now=datetime(2026, 7, 10, 12, 0, 0),
                thread_extractor=lambda _messages: [],
                judge_fn=lambda _grounding, *, now, **_kwargs: {
                    "should_reach_out": False,
                    "reason": "正常 pending",
                    "next_check_at": (now + timedelta(hours=2)).isoformat(),
                    "next_intent": "",
                    "outreach_kind": "message",
                    "research_query": "",
                    "error_type": None,
                },
            )
        )

        assert result["status"] == "pending"
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_lease_release_failure_does_not_replace_business_exception(
    tmp_path,
    monkeypatch,
):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "release-after-error.db")

    async def fail_business(*_args, **_kwargs):
        raise ValueError("business failed")

    monkeypatch.setattr(
        proactive_outreach,
        "_run_outreach_once_acquired",
        fail_business,
    )
    monkeypatch.setattr(
        proactive_outreach,
        "_release_evaluation_lease",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("release failed")),
    )
    session = session_factory()
    try:
        with pytest.raises(ValueError, match="business failed"):
            run_async(
                proactive_outreach.run_outreach_once(
                    "release-error-user",
                    db=session,
                    now=datetime(2026, 7, 10, 12, 0, 0),
                )
            )
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_post_clear_evaluation_does_not_persist_pre_clear_virtual_generation(tmp_path):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "post-clear-generation.db")
    user_id = "post-clear-generation-user"
    clear_at = datetime(2026, 7, 10, 12, 0, 0)
    virtual_now = clear_at - timedelta(seconds=1)
    session = session_factory()
    try:
        session.add(User(id=user_id, history_clear_at=clear_at))
        session.commit()

        result = run_async(
            proactive_outreach.run_outreach_once(
                user_id,
                db=session,
                now=virtual_now,
                max_silence_min=999999,
                thread_extractor=lambda _messages: [],
                judge_fn=lambda _grounding, *, now, **_kwargs: {
                    "should_reach_out": False,
                    "reason": "清除后的新一代评估",
                    "next_check_at": (now + timedelta(hours=2)).isoformat(),
                    "next_intent": "",
                    "outreach_kind": "message",
                    "research_query": "",
                    "error_type": None,
                },
            )
        )

        session.expire_all()
        row = session.query(ProactiveOutreachLog).filter_by(user_id=user_id).one()
        assert result["status"] == "pending"
        assert row.status == "pending"
        assert row.created_at > clear_at
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_acquired_lease_is_still_valid_after_sqlite_write_lock_wait(tmp_path):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "lease-lock-wait.db")
    blocker = engine.raw_connection()
    started = threading.Event()

    def acquire_after_lock():
        session = session_factory()
        try:
            started.set()
            return proactive_outreach._acquire_evaluation_lease(
                session,
                user_id="lease-lock-wait-user",
                now=datetime.now(),
                lease_seconds=1,
            )
        finally:
            session.close()

    try:
        blocker.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(acquire_after_lock)
            assert started.wait(timeout=2)
            time.sleep(1.1)
            blocker.commit()
            owner_token = future.result(timeout=5)

        verify = session_factory()
        try:
            lease = verify.get(ProactiveOutreachLease, "lease-lock-wait-user")
            assert owner_token
            assert lease is not None
            assert lease.owner_token == owner_token
            assert lease.lease_expires_at > datetime.now()
        finally:
            verify.close()
    finally:
        try:
            blocker.rollback()
        except Exception:
            pass
        blocker.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_evaluation_lease_acquire_sql_is_compatible_without_returning(tmp_path):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "lease-no-returning.db")
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture_sql(_conn, _cursor, statement, _params, _context, _many):
        statements.append(str(statement))

    session = session_factory()
    try:
        token = proactive_outreach._acquire_evaluation_lease(
            session,
            user_id="lease-no-returning-user",
            now=datetime.now(),
        )

        assert token
        assert statements
        assert all("RETURNING" not in statement.upper() for statement in statements)
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_non_locked_acquire_failure_rolls_back_sqlite_write_lock(tmp_path):
    from core import proactive_outreach
    from sqlalchemy.exc import OperationalError

    engine, session_factory = _session_factory(tmp_path, "lease-failure-rollback.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE INDEX ix_lease_malformed_json "
            "ON proactive_outreach_leases(json_extract(owner_token, '$'))"
        )

    session = session_factory()
    try:
        with pytest.raises(OperationalError, match="malformed JSON"):
            proactive_outreach._acquire_evaluation_lease(
                session,
                user_id="lease-malformed-json-user",
                now=datetime.now(),
            )

        assert session.in_transaction() is False
        second = engine.raw_connection()
        try:
            second.execute("BEGIN IMMEDIATE")
            second.rollback()
        finally:
            second.close()
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_release_commit_failure_rolls_back_sqlite_write_lock(tmp_path, monkeypatch):
    from core import proactive_outreach

    engine, session_factory = _session_factory(tmp_path, "lease-release-rollback.db")
    session = session_factory()
    try:
        now = datetime.now()
        session.add(ProactiveOutreachLease(
            user_id="release-rollback-user",
            owner_token="release-owner",
            lease_expires_at=now + timedelta(minutes=15),
            created_at=now,
            updated_at=now,
        ))
        session.commit()

        monkeypatch.setattr(
            session,
            "commit",
            lambda: (_ for _ in ()).throw(RuntimeError("commit failed")),
        )
        with pytest.raises(RuntimeError, match="commit failed"):
            proactive_outreach._release_evaluation_lease(
                session,
                user_id="release-rollback-user",
                owner_token="release-owner",
            )

        assert session.in_transaction() is False
        second = engine.raw_connection()
        try:
            second.execute("BEGIN IMMEDIATE")
            second.rollback()
        finally:
            second.close()
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
