import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from core.database import (
    Base,
    ConversationTurn,
    OutboundDeliveryAttempt,
    OutboundDeliveryCircuit,
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
    ProactiveOutreachLease,
    ProactiveOutreachLog,
    User,
)
from core.outbound_delivery import (
    OutboundFencingError,
    claim_due_outbox,
    endpoint_circuit_fingerprint,
    expire_stale_delivery_leases,
    mark_delivery_request_started,
    settle_delivery_attempt,
)
from core.outbound_delivery_service import (
    OutboundTransportRequest,
    OutboundWorkerConfig,
    deliver_outbound_once,
)
from core.outbound_transport import DeliveryOutcome
from tests.async_helpers import run_async
from tests.sqlite_test_utils import install_base_schema


NOW = datetime(2026, 7, 15, 12, 0, 0)
SOURCE_TYPE = "proactive_outreach"
CONFIG_REVISION = "proactive-test-revision"


def _seed_control(db, *, mode: str = "outbox_active") -> None:
    db.add(OutboundDeliveryControl(
        source_type=SOURCE_TYPE,
        mode=mode,
        cutover_epoch=1 if mode != "legacy_direct" else 0,
        effective_from=NOW - timedelta(days=1),
        protocol_version=2,
        writer_version=0,
    ))
    db.commit()


def _activate_legacy_writer(
    db,
    *,
    now: datetime = NOW,
    owner: str = "server-proactive-writer",
) -> None:
    control = db.get(OutboundDeliveryControl, SOURCE_TYPE)
    assert control is not None
    control.writer_owner = owner
    control.writer_token = f"{owner}-token"
    control.writer_version += 1
    control.writer_lease_expires_at = now + timedelta(hours=1)
    db.commit()


def _file_session_factory(tmp_path, name: str, *, mode: str = "outbox_active"):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=5000")

    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    install_base_schema(engine)
    with factory() as setup:
        _seed_control(setup, mode=mode)
    return engine, factory


def _worker_config() -> OutboundWorkerConfig:
    return OutboundWorkerConfig(
        push_url="http://qq-push.test/nanobot/push",
        push_token="push-token-proactive-helper-sentinel",
        push_timeout_seconds=1.0,
        endpoint_config_revision=CONFIG_REVISION,
        batch_size=10,
        lease_seconds=60.0,
        poll_interval_seconds=0.1,
    )


def _success() -> DeliveryOutcome:
    return DeliveryOutcome(
        category="success",
        error_type="",
        status_code=200,
        retry_after_seconds=None,
        duration_ms=5,
        safe_summary="投递成功",
        transport_phase="response_received",
    )


def _transient_503() -> DeliveryOutcome:
    return DeliveryOutcome(
        category="transient",
        error_type="service_unavailable",
        status_code=503,
        retry_after_seconds=None,
        duration_ms=5,
        safe_summary="上游暂时不可用",
        transport_phase="response_received",
    )


def _transport_outcome(
    category: str,
    status_code: int,
    error_type: str,
    *,
    retry_after_seconds: int | None = None,
) -> DeliveryOutcome:
    return DeliveryOutcome(
        category=category,
        error_type=error_type,
        status_code=status_code,
        retry_after_seconds=retry_after_seconds,
        duration_ms=7,
        safe_summary="结构化传输测试",
        transport_phase="response_received",
    )


_DEFAULT_PUBLISHER = object()


async def _enqueue(
    db,
    *,
    user_id: str = "outbox-user",
    key: str = "outreach:outbox-user:one",
    created_at: datetime = NOW,
    publisher=_DEFAULT_PUBLISHER,
):
    from core import proactive_outreach

    if publisher is _DEFAULT_PUBLISHER:
        async def publisher(*_args):
            return True

    return await proactive_outreach.deliver_outreach_once(
        user_id=user_id,
        idempotency_key=key,
        grounding={"recent_messages": [], "topic": "持久投递"},
        judge_should=True,
        judge_reason="存在可跟进话题",
        next_check_at=NOW + timedelta(hours=2),
        next_intent="稍后继续",
        message="这是一条只生成一次的主动消息。",
        forced=False,
        db=db,
        created_at=created_at,
        publisher=publisher,
    )


def _message_judge(now: datetime, *, reason: str = "存在可跟进话题"):
    return {
        "should_reach_out": True,
        "reason": reason,
        "next_check_at": (now + timedelta(hours=2)).isoformat(),
        "next_intent": "稍后继续",
        "outreach_kind": "message",
        "research_query": "",
        "error_type": None,
    }


@pytest.mark.asyncio
async def test_local_naive_occurrence_is_converted_before_cutover_routing(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    local_created_at = datetime(2026, 7, 15, 12, 0, 0)
    actual_utc = (
        local_created_at.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    cutover_at = datetime(2026, 7, 15, 8, 0, 0)
    assert actual_utc < cutover_at < local_created_at
    db_session.add(OutboundDeliveryControl(
        source_type=SOURCE_TYPE,
        mode="outbox_hold",
        cutover_epoch=1,
        effective_from=cutover_at,
        protocol_version=2,
        writer_version=0,
    ))
    db_session.commit()
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    monkeypatch.setenv(
        "NANOBOT_PUSH_TOKEN",
        "push-token-proactive-cutover-sentinel",
    )
    published = []

    async def publisher(*args):
        published.append(args)
        return True

    result = await proactive_outreach.deliver_outreach_once(
        user_id="cutover-timezone-user",
        idempotency_key="outreach:cutover-timezone-user:one",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="验证切换时区",
        next_check_at=None,
        next_intent="",
        message="边界前的 occurrence 应由 legacy writer 投递。",
        forced=False,
        db=db_session,
        created_at=local_created_at,
        publisher=publisher,
    )

    db_session.expire_all()
    run = db_session.query(OutboundRun).one()
    assert result["status"] == "sent"
    assert run.delivery_mode == "legacy_direct"
    assert run.scheduled_for == actual_utc
    assert len(published) == 1


@pytest.mark.asyncio
async def test_outbox_mode_only_persists_payload_and_never_calls_publisher(
    monkeypatch,
    db_session,
):
    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    published = []

    async def publisher(*args):
        published.append(args)
        raise AssertionError("outbox producer 不得执行 HTTP")

    result = await _enqueue(db_session, publisher=publisher)

    db_session.expire_all()
    row = db_session.query(ProactiveOutreachLog).one()
    run = db_session.query(OutboundRun).one()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    assert result == {
        "status": "queued",
        "log_id": row.id,
        "run_id": run.id,
        "outbox_id": outbox.id,
        "forced": False,
        "deduplicated": False,
    }
    assert published == []
    assert row.status == "queued"
    assert row.outbound_run_id == run.id
    assert run.source_type == SOURCE_TYPE
    assert run.source_id == str(row.id)
    assert run.status == "queued"
    assert run.active_outbox_id == outbox.id
    assert outbox.status == "pending"
    assert db_session.query(OutboundGenerationAttempt).count() == 0


@pytest.mark.asyncio
async def test_runtime_generator_starts_persisted_generation_attempt_before_call(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    observed: dict[str, object] = {}

    def generator(_grounding, _reason):
        db_session.expire_all()
        run = db_session.query(OutboundRun).one_or_none()
        attempt = db_session.query(OutboundGenerationAttempt).one_or_none()
        observed.update({
            "run_status": run.status if run is not None else None,
            "attempt_status": attempt.status if attempt is not None else None,
            "outbox_count": db_session.query(OutboundDeliveryOutbox).count(),
        })
        return "普通 Generator 生成的主动消息。"

    result = await proactive_outreach.run_outreach_once(
        "generated-message-user",
        db=db_session,
        now=NOW,
        max_silence_min=999999,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: _message_judge(NOW),
        generator_fn=generator,
    )

    db_session.expire_all()
    run = db_session.query(OutboundRun).one()
    attempt = db_session.query(OutboundGenerationAttempt).one()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    assert observed == {
        "run_status": "generating",
        "attempt_status": "started",
        "outbox_count": 0,
    }
    assert result["status"] == "queued"
    assert run.task_kind == "proactive_outreach_generated"
    assert run.status == "queued"
    assert attempt.status == "succeeded"
    assert attempt.content_sha256 == outbox.payload_sha256


@pytest.mark.asyncio
async def test_research_draft_starts_persisted_generation_attempt_before_workflow(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach
    from core.proactive_research import ResearchResult, ResearchSource

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    observed: dict[str, object] = {}

    async def research(request):
        db_session.expire_all()
        run = db_session.query(OutboundRun).one_or_none()
        attempt = db_session.query(OutboundGenerationAttempt).one_or_none()
        observed.update({
            "run_status": run.status if run is not None else None,
            "attempt_status": attempt.status if attempt is not None else None,
            "outbox_count": db_session.query(OutboundDeliveryOutbox).count(),
        })
        return ResearchResult(
            request_id=request.request_id,
            trace_id="trace-generation-attempt",
            status="draft_ready",
            draft="研究正文\n\n来源（本次真实检索）：...",
            sources=(
                ResearchSource("tool-1", "来源一", "https://example.test/one"),
                ResearchSource("tool-2", "来源二", "https://example.test/two"),
            ),
        )

    result = await proactive_outreach.run_outreach_once(
        "generated-research-user",
        db=db_session,
        now=NOW,
        max_silence_min=999999,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: {
            **_message_judge(NOW, reason="需要先查资料"),
            "outreach_kind": "research",
            "research_query": "调查 Agent 长期记忆",
        },
        generator_fn=lambda *_args, **_kwargs: pytest.fail(
            "研究正文不得调用普通 Generator"
        ),
        research_fn=research,
    )

    db_session.expire_all()
    attempt = db_session.query(OutboundGenerationAttempt).one()
    assert observed == {
        "run_status": "generating",
        "attempt_status": "started",
        "outbox_count": 0,
    }
    assert result["status"] == "queued"
    assert attempt.status == "succeeded"


@pytest.mark.asyncio
async def test_max_silence_judged_generator_starts_attempt_before_call(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    db_session.add(ProactiveOutreachLog(
        user_id="generated-forced-user",
        idempotency_key="outreach:generated-forced-user:old",
        grounding_json="{}",
        judge_should=True,
        judge_reason="旧外呼",
        message="旧消息",
        status="sent",
        forced=False,
        created_at=NOW - timedelta(days=3),
    ))
    db_session.commit()
    observed: dict[str, object] = {}
    judge_calls = []

    def judge(grounding, **_kwargs):
        judge_calls.append(grounding)
        return {
            "should_reach_out": True,
            "reason": "仍有值得联系的新信息",
            "next_check_at": (NOW + timedelta(hours=2)).isoformat(),
            "next_intent": "等待新的自然话题",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    def generator(_grounding, decision):
        db_session.expire_all()
        run = db_session.query(OutboundRun).one_or_none()
        attempt = db_session.query(OutboundGenerationAttempt).one_or_none()
        observed.update({
            "run_status": run.status if run is not None else None,
            "attempt_status": attempt.status if attempt is not None else None,
        })
        assert decision["reason"] == "仍有值得联系的新信息"
        return "最长沉默窗口触发的主动消息。"

    result = await proactive_outreach.run_outreach_once(
        "generated-forced-user",
        db=db_session,
        now=NOW,
        max_silence_min=60,
        thread_extractor=lambda _messages: [],
        judge_fn=judge,
        generator_fn=generator,
    )

    db_session.expire_all()
    attempt = db_session.query(OutboundGenerationAttempt).one()
    assert observed == {
        "run_status": "generating",
        "attempt_status": "started",
    }
    assert len(judge_calls) == 1
    assert judge_calls[0]["trigger"]["kind"] == "max_silence_evaluation"
    assert result["status"] == "queued"
    assert result["forced"] is False
    assert attempt.status == "succeeded"


@pytest.mark.asyncio
async def test_crash_after_attempt_commit_keeps_started_generation_audit(
    monkeypatch,
    tmp_path,
):
    from core import proactive_outreach

    class SimulatedProcessCrash(BaseException):
        pass

    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    engine, factory = _file_session_factory(
        tmp_path,
        "proactive-generation-start-crash.db",
    )
    observed_attempt_count = 0

    def crash_before_model_work(_grounding, _reason):
        nonlocal observed_attempt_count
        with factory() as observer:
            observed_attempt_count = observer.query(OutboundGenerationAttempt).count()
        raise SimulatedProcessCrash("attempt 持久化后崩溃")

    session = factory()
    try:
        with pytest.raises(SimulatedProcessCrash, match="attempt 持久化后崩溃"):
            await proactive_outreach.run_outreach_once(
                "generation-start-crash-user",
                db=session,
                now=NOW,
                max_silence_min=999999,
                thread_extractor=lambda _messages: [],
                judge_fn=lambda *_args, **_kwargs: _message_judge(NOW),
                generator_fn=crash_before_model_work,
            )
    finally:
        session.close()
    try:
        with factory() as observer:
            run = observer.query(OutboundRun).one()
            attempt = observer.query(OutboundGenerationAttempt).one()
            row = observer.query(ProactiveOutreachLog).one()
            assert observed_attempt_count == 1
            assert run.status == "generating"
            assert attempt.status == "started"
            assert row.status == "candidate"
            assert row.message == ""
            assert observer.query(OutboundDeliveryOutbox).count() == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_crash_after_model_return_rolls_back_candidate_and_outbox_only(
    monkeypatch,
    tmp_path,
):
    from core import outbound_delivery, proactive_outreach

    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    engine, factory = _file_session_factory(
        tmp_path,
        "proactive-generation-commit-crash.db",
    )

    def crash_before_generated_commit(*_args, **_kwargs):
        raise RuntimeError("模型返回后提交前崩溃")

    monkeypatch.setattr(
        outbound_delivery,
        "commit_generated_outbox",
        crash_before_generated_commit,
    )

    session = factory()
    try:
        with pytest.raises(RuntimeError, match="模型返回后提交前崩溃"):
            await proactive_outreach.run_outreach_once(
                "generation-commit-crash-user",
                db=session,
                now=NOW,
                max_silence_min=999999,
                thread_extractor=lambda _messages: [],
                judge_fn=lambda *_args, **_kwargs: _message_judge(NOW),
                generator_fn=lambda *_args, **_kwargs: "已经生成但尚未提交的正文。",
            )
    finally:
        session.close()
    try:
        with factory() as observer:
            run = observer.query(OutboundRun).one()
            attempt = observer.query(OutboundGenerationAttempt).one()
            row = observer.query(ProactiveOutreachLog).one()
            assert run.status == "generating"
            assert attempt.status == "started"
            assert row.status == "candidate"
            assert row.message == ""
            assert observer.query(OutboundDeliveryOutbox).count() == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_expired_generation_owner_is_fenced_and_takeover_uses_next_attempt(
    monkeypatch,
    tmp_path,
):
    from core import proactive_outreach

    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    monkeypatch.setenv(
        "NANOBOT_PUSH_TOKEN",
        "push-token-proactive-takeover-sentinel",
    )
    engine, factory = _file_session_factory(
        tmp_path,
        "proactive-generation-takeover.db",
    )

    def old_generator(_grounding, _reason):
        with factory() as takeover:
            run = takeover.query(OutboundRun).one()
            attempt = takeover.query(OutboundGenerationAttempt).one()
            lease = takeover.get(
                ProactiveOutreachLease,
                "generation-takeover-user",
            )
            assert run.status == "generating"
            assert attempt.status == "started"
            assert lease is not None
            assert run.updated_at is not None
            run.claim_expires_at = run.updated_at - timedelta(seconds=1)
            lease.owner_token = "new-evaluation-owner"
            lease.lease_expires_at = datetime.now() + timedelta(minutes=15)
            takeover.commit()
        return "旧 owner 的结果不得提交。"

    session = factory()
    try:
        first = await proactive_outreach.run_outreach_once(
            "generation-takeover-user",
            db=session,
            now=NOW,
            max_silence_min=999999,
            thread_extractor=lambda _messages: [],
            judge_fn=lambda *_args, **_kwargs: _message_judge(NOW),
            generator_fn=old_generator,
        )

        second = await proactive_outreach._run_outreach_once_acquired(
            "generation-takeover-user",
            db=session,
            now=NOW + timedelta(seconds=1),
            max_silence_min=999999,
            judge_fn=lambda *_args, **_kwargs: pytest.fail(
                "恢复冻结 occurrence 不得重新调用 Judge"
            ),
            generator_fn=lambda *_args, **_kwargs: "新 owner 生成的正文。",
            research_fn=lambda *_args, **_kwargs: pytest.fail(
                "消息生成恢复不得调用 Research"
            ),
            thread_extractor=lambda _messages: pytest.fail(
                "恢复冻结 occurrence 不得重建 grounding"
            ),
            evaluation_owner_token="new-evaluation-owner",
            evaluation_generation_at=NOW + timedelta(seconds=1),
        )
    finally:
        session.close()
    try:
        with factory() as observer:
            attempts = (
                observer.query(OutboundGenerationAttempt)
                .order_by(OutboundGenerationAttempt.attempt_no.asc())
                .all()
            )
            assert observer.query(OutboundDeliveryOutbox).count() == 1
            outbox = observer.query(OutboundDeliveryOutbox).one()
            run = observer.query(OutboundRun).one()
            row = observer.query(ProactiveOutreachLog).one()
            assert first["status"] == "lease_lost"
            assert second["status"] == "queued"
            assert [(item.attempt_no, item.status) for item in attempts] == [
                (1, "abandoned"),
                (2, "succeeded"),
            ]
            assert attempts[0].error_type == "claim_expired"
            assert run.status == "queued"
            assert run.active_outbox_id == outbox.id
            assert row.message == "新 owner 生成的正文。"
            assert outbox.status == "pending"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_existing_outbox_recovery_does_not_regenerate_or_add_attempt(
    monkeypatch,
    tmp_path,
):
    from core import proactive_outreach

    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    engine, factory = _file_session_factory(
        tmp_path,
        "proactive-generation-post-commit-crash.db",
    )
    calls = {"generator": 0}

    def generator(*_args, **_kwargs):
        calls["generator"] += 1
        return "只应生成一次的正文。"

    original_result = proactive_outreach._outreach_result
    crash_once = {"pending": True}

    def crash_after_commit(*args, **kwargs):
        if crash_once["pending"]:
            crash_once["pending"] = False
            raise RuntimeError("outbox 提交后进程退出")
        return original_result(*args, **kwargs)

    monkeypatch.setattr(proactive_outreach, "_outreach_result", crash_after_commit)
    session = factory()
    try:
        with pytest.raises(RuntimeError, match="outbox 提交后进程退出"):
            await proactive_outreach.run_outreach_once(
                "existing-outbox-user",
                db=session,
                now=NOW,
                max_silence_min=999999,
                thread_extractor=lambda _messages: [],
                judge_fn=lambda *_args, **_kwargs: _message_judge(NOW),
                generator_fn=generator,
            )
        second = await proactive_outreach.run_outreach_once(
            "existing-outbox-user",
            db=session,
            now=NOW + timedelta(seconds=1),
            max_silence_min=999999,
            thread_extractor=lambda _messages: pytest.fail("已有 outbox 不得重建 grounding"),
            judge_fn=lambda *_args, **_kwargs: pytest.fail("已有 outbox 不得重新 Judge"),
            generator_fn=lambda *_args, **_kwargs: pytest.fail(
                "已有 outbox 不得重新 Generator"
            ),
            research_fn=lambda *_args, **_kwargs: pytest.fail(
                "已有 outbox 不得重新 Research"
            ),
        )
    finally:
        session.close()
    try:
        with factory() as observer:
            assert second["status"] == "skipped_duplicate"
            assert calls == {"generator": 1}
            assert observer.query(OutboundGenerationAttempt).count() == 1
            assert observer.query(OutboundDeliveryOutbox).count() == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("raise_after_insert", [False, True])
async def test_candidate_and_run_roll_back_together_on_enqueue_crash(
    monkeypatch,
    db_session,
    raise_after_insert,
):
    from core import outbound_delivery

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    original = outbound_delivery.claim_outbound_run

    def crash(*args, **kwargs):
        if raise_after_insert:
            original(*args, **kwargs)
        raise RuntimeError("注入原子入队崩溃")

    monkeypatch.setattr(outbound_delivery, "claim_outbound_run", crash)

    with pytest.raises(RuntimeError, match="注入原子入队崩溃"):
        await _enqueue(db_session, key=f"outreach:rollback:{raise_after_insert}")

    db_session.rollback()
    assert db_session.query(ProactiveOutreachLog).count() == 0
    assert db_session.query(OutboundRun).count() == 0
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


def test_concurrent_same_candidate_creates_one_run_and_one_outbox(
    tmp_path,
    monkeypatch,
):
    from core import proactive_outreach

    engine = create_engine(
        f"sqlite:///{tmp_path / 'proactive-outbox-race.db'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=5000")

    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    install_base_schema(engine)
    with factory() as setup:
        _seed_control(setup)

    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    ready = threading.Barrier(2)
    original_strip = proactive_outreach.strip_think_blocks

    def synchronize_before_claim(value):
        cleaned = original_strip(value)
        ready.wait(timeout=5)
        return cleaned

    monkeypatch.setattr(
        proactive_outreach,
        "strip_think_blocks",
        synchronize_before_claim,
    )
    published = []

    async def publisher(*args):
        published.append(args)
        raise AssertionError("outbox producer 不得执行 HTTP")

    def invoke():
        with factory() as db:
            return run_async(_enqueue(
                db,
                user_id="race-user",
                key="outreach:race-user:one",
                publisher=publisher,
            ))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: invoke(), range(2)))

    with factory() as observer:
        assert observer.query(ProactiveOutreachLog).count() == 1
        assert observer.query(OutboundRun).count() == 1
        assert observer.query(OutboundDeliveryOutbox).count() == 1
    assert published == []
    assert sorted(result["status"] for result in results) == [
        "queued",
        "skipped_duplicate",
    ]


@pytest.mark.asyncio
async def test_open_circuit_skips_all_outreach_model_work(monkeypatch, db_session):
    from core import proactive_outreach

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    db_session.add(OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=endpoint_circuit_fingerprint("qq_push"),
        config_revision=CONFIG_REVISION,
        status="open",
        reason_type="route_missing",
        opened_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    ))
    db_session.commit()
    calls = {"thread": 0, "judge": 0, "research": 0, "generator": 0}

    def thread_extractor(_messages):
        calls["thread"] += 1
        return []

    def judge(*_args, **_kwargs):
        calls["judge"] += 1
        return {
            "should_reach_out": True,
            "reason": "应当研究",
            "next_check_at": (NOW + timedelta(hours=1)).isoformat(),
            "next_intent": "",
            "outreach_kind": "research",
            "research_query": "测试",
            "error_type": None,
        }

    async def research(_request):
        calls["research"] += 1
        raise AssertionError("circuit 打开时不得研究")

    def generator(*_args, **_kwargs):
        calls["generator"] += 1
        raise AssertionError("circuit 打开时不得生成")

    result = await proactive_outreach.run_outreach_once(
        "circuit-user",
        db=db_session,
        now=NOW,
        max_silence_min=999999,
        thread_extractor=thread_extractor,
        judge_fn=judge,
        research_fn=research,
        generator_fn=generator,
    )

    assert result["status"] == "skipped_circuit"
    assert calls == {"thread": 0, "judge": 0, "research": 0, "generator": 0}
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


@pytest.mark.asyncio
async def test_503_retry_reuses_persisted_payload_and_projects_success(
    monkeypatch,
    db_session,
):
    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    queued = await _enqueue(db_session)
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    outcomes = iter((_transient_503(), _success()))
    requests: list[tuple[str, str]] = []

    async def transport(request: OutboundTransportRequest) -> DeliveryOutcome:
        requests.append((request.payload_sha256, request.message))
        return next(outcomes)

    first = await deliver_outbound_once(
        session_factory=factory,
        transport=transport,
        config=_worker_config(),
        worker_owner="proactive-worker-a",
        now=NOW + timedelta(seconds=1),
        jitter=lambda _maximum: 1.0,
    )
    second = await deliver_outbound_once(
        session_factory=factory,
        transport=transport,
        config=_worker_config(),
        worker_owner="proactive-worker-b",
        now=NOW + timedelta(seconds=3),
        jitter=lambda _maximum: 1.0,
    )

    db_session.expire_all()
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    assert first is not None and first.outbox_status == "retry_wait"
    assert second is not None and second.outbox_status == "delivered"
    assert requests[0] == requests[1]
    assert outbox.status == "delivered"
    assert outbox.request_started_count == 2
    assert row.status == "sent"
    assert db_session.query(OutboundRun).count() == 1
    assert db_session.query(OutboundDeliveryOutbox).count() == 1
    assert db_session.query(OutboundGenerationAttempt).count() == 0


@pytest.mark.asyncio
async def test_unknown_result_becomes_ambiguous_and_is_not_auto_reclaimed(
    monkeypatch,
    db_session,
):
    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    queued = await _enqueue(db_session)
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    calls = 0

    async def unknown(_request: OutboundTransportRequest) -> DeliveryOutcome:
        nonlocal calls
        calls += 1
        raise TimeoutError("模拟 request boundary 后结果未知")

    first = await deliver_outbound_once(
        session_factory=factory,
        transport=unknown,
        config=_worker_config(),
        worker_owner="proactive-worker-a",
        now=NOW + timedelta(seconds=1),
    )
    second = await deliver_outbound_once(
        session_factory=factory,
        transport=unknown,
        config=_worker_config(),
        worker_owner="proactive-worker-b",
        now=NOW + timedelta(minutes=10),
    )

    db_session.expire_all()
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    attempt = db_session.query(OutboundDeliveryAttempt).one()
    assert first is not None and first.outbox_status == "ambiguous"
    assert second is None
    assert calls == 1
    assert attempt.request_started is True
    assert attempt.status == "ambiguous"
    assert outbox.status == "ambiguous"
    assert row.status == "ambiguous"


@pytest.mark.asyncio
async def test_history_clear_cancels_safe_queued_outreach(monkeypatch, db_session):
    from api.history_log_routes import mark_clear

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    queued = await _enqueue(
        db_session,
        user_id="history-clear-user",
        created_at=datetime(2099, 1, 1),
    )

    response = mark_clear("history-clear-user", db=db_session, _auth=None)

    db_session.expire_all()
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    assert response["cancelled_outreach_deliveries"] == 1
    assert response["unsafe_outreach_deliveries"] == 0
    assert row.status == "cancelled"
    assert outbox.status == "cancelled"


def test_history_clear_archives_summary_and_enqueues_semantic_delete(
    db_session,
):
    from api.history_log_routes import mark_clear
    from core.database import RollingSessionSummary, SemanticIndexJob

    _seed_control(db_session)
    summary = RollingSessionSummary(
        session_id="history-clear-summary-session",
        user_id="history-clear-summary-user",
        status="active",
        summary_kind="llm_episode",
        summary_text="清除前摘要",
        stable_hash="history-clear-summary-revision",
    )
    db_session.add(summary)
    db_session.commit()

    response = mark_clear(
        "history-clear-summary-user",
        db=db_session,
        _auth=None,
    )

    db_session.refresh(summary)
    job = db_session.query(SemanticIndexJob).one()
    assert response["archived_rolling_summaries"] == 1
    assert summary.status == "archived"
    assert job.source_type == "session_summary"
    assert job.source_id == "history-clear-summary-session"
    assert job.job_type == "delete"
    assert job.status == "pending"
    job_meta = json.loads(job.meta_json)
    assert job_meta["job_origin"] == "business"
    assert str(summary.id) in job_meta["delete_source_ids"]


def test_history_clear_rolls_back_when_semantic_delete_enqueue_fails(
    db_session,
    monkeypatch,
):
    from fastapi import HTTPException

    from api.history_log_routes import mark_clear
    from core.database import RollingSessionSummary, User

    _seed_control(db_session)
    user = User(id="history-clear-rollback-user")
    summary = RollingSessionSummary(
        session_id="history-clear-rollback-session",
        user_id=user.id,
        status="active",
        summary_kind="llm_episode",
        summary_text="必须保留的摘要",
    )
    db_session.add_all([user, summary])
    db_session.commit()

    monkeypatch.setattr(
        "core.semantic.jobs.enqueue_index_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("semantic enqueue failed")
        ),
    )

    with pytest.raises(HTTPException) as raised:
        mark_clear(user.id, db=db_session, _auth=None)

    db_session.expire_all()
    assert raised.value.status_code == 500
    assert db_session.get(User, user.id).history_clear_at is None
    assert db_session.get(RollingSessionSummary, summary.id).status == "active"


@pytest.mark.asyncio
async def test_history_clear_terminalizes_claimed_run_without_generation_attempt(
    monkeypatch,
    db_session,
):
    from api.history_log_routes import mark_clear

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    queued = await _enqueue(
        db_session,
        user_id="history-clear-claimed-user",
        key="outreach:history-clear-claimed-user:one",
        created_at=datetime(2099, 1, 1),
    )
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    run = db_session.get(OutboundRun, queued["run_id"])
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    run.active_outbox_id = None
    run.status = "claimed"
    run.claim_owner = "claimed-before-clear"
    run.claim_token = "claimed-before-clear-token"
    run.claim_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        minutes=5
    )
    row.status = "candidate"
    db_session.flush()
    db_session.delete(outbox)
    db_session.commit()

    response = mark_clear(
        "history-clear-claimed-user",
        db=db_session,
        _auth=None,
    )

    db_session.expire_all()
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    run = db_session.get(OutboundRun, queued["run_id"])
    assert response["cancelled_outreach_deliveries"] == 1
    assert response["unsafe_outreach_deliveries"] == 0
    assert row.status == "cancelled"
    assert run.status == "failed"
    assert run.failure_type == "history_cleared"
    assert run.claim_owner is None
    assert run.claim_token is None
    assert run.claim_expires_at is None
    assert db_session.query(OutboundGenerationAttempt).count() == 0
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


@pytest.mark.asyncio
async def test_post_clear_new_run_is_not_cancelled_by_worker_preflight(
    monkeypatch,
    db_session,
):
    from api.history_log_routes import mark_clear

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    response = mark_clear("post-clear-worker-user", db=db_session, _auth=None)
    assert response["status"] == "success"
    queued = await _enqueue(
        db_session,
        user_id="post-clear-worker-user",
        key="outreach:post-clear-worker-user:one",
        created_at=datetime(2099, 1, 1),
    )
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    transport_calls = 0

    async def transport(_request):
        nonlocal transport_calls
        transport_calls += 1
        return _success()

    result = await deliver_outbound_once(
        session_factory=factory,
        transport=transport,
        config=_worker_config(),
        worker_owner="post-clear-worker",
        now=NOW + timedelta(seconds=1),
    )

    db_session.expire_all()
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    run = db_session.get(OutboundRun, queued["run_id"])
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    assert result is not None and result.outbox_status == "delivered"
    assert transport_calls == 1
    assert row.status == "sent"
    assert run.status == "succeeded"
    assert outbox.status == "delivered"


@pytest.mark.asyncio
async def test_history_clear_keeps_clear_marker_when_linkage_is_fenced(
    monkeypatch,
    db_session,
):
    from api.history_log_routes import mark_clear

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    queued = await _enqueue(
        db_session,
        user_id="history-clear-fenced-user",
        key="outreach:history-clear-fenced-user:one",
    )
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    row.message = "来源 revision 已在入队后变化。"
    db_session.commit()

    response = mark_clear("history-clear-fenced-user", db=db_session, _auth=None)

    db_session.expire_all()
    user = db_session.get(User, "history-clear-fenced-user")
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    run = db_session.get(OutboundRun, queued["run_id"])
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    assert response["status"] == "success"
    assert response["cancelled_outreach_deliveries"] == 0
    assert response["unsafe_outreach_deliveries"] == 1
    assert user is not None and user.history_clear_at is not None
    assert row.status == "queued"
    assert run.status == "queued"
    assert outbox.status == "pending"


@pytest.mark.asyncio
async def test_history_clear_keeps_clear_marker_when_linkage_metadata_conflicts(
    monkeypatch,
    db_session,
):
    from api.history_log_routes import mark_clear

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    queued = await _enqueue(
        db_session,
        user_id="history-clear-conflict-user",
        key="outreach:history-clear-conflict-user:one",
    )
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    row.grounding_json = "{损坏的来源元数据"
    db_session.commit()

    response = mark_clear(
        "history-clear-conflict-user",
        db=db_session,
        _auth=None,
    )

    db_session.expire_all()
    user = db_session.get(User, "history-clear-conflict-user")
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    run = db_session.get(OutboundRun, queued["run_id"])
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    assert response["status"] == "success"
    assert response["cancelled_outreach_deliveries"] == 0
    assert response["unsafe_outreach_deliveries"] == 1
    assert user is not None and user.history_clear_at is not None
    assert row.status == "queued"
    assert run.status == "queued"
    assert outbox.status == "pending"


@pytest.mark.asyncio
async def test_history_clear_between_preflight_and_request_boundary_cancels(
    monkeypatch,
    db_session,
):
    from api.history_log_routes import mark_clear
    from core import outbound_delivery_service

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    queued = await _enqueue(
        db_session,
        user_id="history-clear-boundary-user",
        key="outreach:history-clear-boundary-user:one",
        created_at=datetime(2099, 1, 1),
    )
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    original_preflight = (
        outbound_delivery_service.cancel_invalid_delivery_before_send
    )
    preflight_calls = 0
    clear_responses = []

    def clear_before_second_preflight(*args, **kwargs):
        nonlocal preflight_calls
        preflight_calls += 1
        if preflight_calls == 2:
            with factory() as clear_db:
                clear_responses.append(mark_clear(
                    "history-clear-boundary-user",
                    db=clear_db,
                    _auth=None,
                ))
        return original_preflight(*args, **kwargs)

    monkeypatch.setattr(
        outbound_delivery_service,
        "cancel_invalid_delivery_before_send",
        clear_before_second_preflight,
    )
    transport_calls = 0

    async def transport(_request):
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("历史清除后不得越过 request boundary")

    result = await deliver_outbound_once(
        session_factory=factory,
        transport=transport,
        config=_worker_config(),
        worker_owner="proactive-worker-clear-race",
        now=NOW + timedelta(seconds=1),
    )

    db_session.expire_all()
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    attempt = db_session.query(OutboundDeliveryAttempt).one()
    assert result is not None and result.outbox_status == "cancelled"
    assert preflight_calls == 2
    assert transport_calls == 0
    assert clear_responses[0]["cancelled_outreach_deliveries"] == 0
    assert clear_responses[0]["unsafe_outreach_deliveries"] == 1
    assert row.status == "cancelled"
    assert outbox.status == "cancelled"
    assert attempt.request_started is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome_kind", "expected_outbox_status", "expected_source_status"),
    [
        ("unknown", "ambiguous", "ambiguous"),
        ("success", "delivered", "sent"),
    ],
)
async def test_history_clear_after_request_boundary_preserves_delivery_fact(
    monkeypatch,
    db_session,
    outcome_kind,
    expected_outbox_status,
    expected_source_status,
):
    from api.history_log_routes import mark_clear

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    user_id = f"history-clear-after-request-{outcome_kind}"
    queued = await _enqueue(
        db_session,
        user_id=user_id,
        key=f"outreach:{user_id}:one",
    )
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    clear_responses = []

    async def transport(_request):
        with factory() as clear_db:
            clear_responses.append(mark_clear(user_id, db=clear_db, _auth=None))
        if outcome_kind == "unknown":
            raise TimeoutError("模拟 request boundary 后结果未知")
        return _success()

    result = await deliver_outbound_once(
        session_factory=factory,
        transport=transport,
        config=_worker_config(),
        worker_owner=f"proactive-worker-{outcome_kind}",
        now=NOW + timedelta(seconds=1),
    )

    db_session.expire_all()
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    attempt = db_session.query(OutboundDeliveryAttempt).one()
    assert result is not None
    assert result.outbox_status == expected_outbox_status
    assert clear_responses[0]["cancelled_outreach_deliveries"] == 0
    assert clear_responses[0]["unsafe_outreach_deliveries"] == 1
    assert attempt.request_started is True
    assert outbox.status == expected_outbox_status
    assert row.status == expected_source_status
    context_rows = db_session.query(ConversationTurn).filter_by(
        user_id=user_id,
        session_id=f"private_{user_id}",
        role="assistant",
    ).all()
    if outcome_kind == "success":
        assert len(context_rows) == 1
        assert context_rows[0].content.startswith("[主动外呼已发送] ")
        assert json.loads(context_rows[0].source_message_ids_json) == [
            f"outbound-delivery:proactive_outreach:{queued['outbox_id']}"
        ]
    else:
        assert context_rows == []


@pytest.mark.asyncio
async def test_stale_worker_settlement_cannot_overwrite_new_outreach_source(
    monkeypatch,
    db_session,
):
    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    queued = await _enqueue(
        db_session,
        user_id="source-fence-user",
        key="outreach:source-fence-user:one",
    )
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )

    with factory() as worker_db:
        claim = claim_due_outbox(
            worker_db,
            worker_owner="proactive-worker-stale",
            lease_seconds=60,
            endpoint_config_revision=CONFIG_REVISION,
            now=NOW + timedelta(seconds=1),
        )
        assert claim is not None
        worker_db.commit()
    with factory() as boundary_db:
        mark_delivery_request_started(
            boundary_db,
            outbox_id=claim.outbox_id,
            attempt_id=claim.attempt_id,
            worker_owner=claim.worker_owner,
            lease_token=claim.lease_token,
            now=NOW + timedelta(seconds=2),
        )
        boundary_db.commit()
    with factory() as replacement_db:
        row = replacement_db.get(ProactiveOutreachLog, queued["log_id"])
        row.status = "candidate"
        row.message = "这是更新后的新候选。"
        row.outbound_run_id = None
        replacement_db.commit()

    with factory() as settlement_db:
        with pytest.raises(OutboundFencingError, match="来源"):
            settle_delivery_attempt(
                settlement_db,
                outbox_id=claim.outbox_id,
                attempt_id=claim.attempt_id,
                worker_owner=claim.worker_owner,
                lease_token=claim.lease_token,
                outcome="succeeded",
                transport_phase="response_received",
                http_status=200,
                result_category="success",
                error_type="",
                safe_summary="",
                duration_ms=5,
                now=NOW + timedelta(seconds=3),
            )
        settlement_db.rollback()

    db_session.expire_all()
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    attempt = db_session.query(OutboundDeliveryAttempt).one()
    assert row.status == "candidate"
    assert row.message == "这是更新后的新候选。"
    assert row.outbound_run_id is None
    assert outbox.status == "leased"
    assert attempt.request_started is True


@pytest.mark.asyncio
async def test_legacy_mode_calls_publisher_only_after_durable_request_boundary(
    monkeypatch,
    db_session,
):
    _seed_control(db_session, mode="legacy_direct")
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    monkeypatch.delenv("NANOBOT_PUSH_TOKEN", raising=False)
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    observed = []

    async def publisher(target_type, target_id, message):
        with factory() as observer:
            row = observer.query(ProactiveOutreachLog).one()
            run = observer.query(OutboundRun).one()
            outbox = observer.query(OutboundDeliveryOutbox).one()
            attempt = observer.query(OutboundDeliveryAttempt).one()
            observed.append({
                "target_type": target_type,
                "target_id": target_id,
                "message": message,
                "row_status": row.status,
                "run_status": run.status,
                "outbox_status": outbox.status,
                "request_started_count": outbox.request_started_count,
                "request_started": bool(attempt.request_started),
            })
        return True

    result = await _enqueue(
        db_session,
        user_id="legacy-user",
        key="outreach:legacy-user:one",
        publisher=publisher,
    )

    db_session.expire_all()
    assert result["status"] == "sent"
    assert observed == [{
        "target_type": "private",
        "target_id": "legacy-user",
        "message": "这是一条只生成一次的主动消息。",
        "row_status": "delivering",
        "run_status": "delivering",
        "outbox_status": "leased",
        "request_started_count": 1,
        "request_started": True,
    }]
    assert db_session.query(OutboundRun).count() == 1
    assert db_session.query(OutboundDeliveryOutbox).count() == 1
    assert db_session.query(OutboundDeliveryAttempt).count() == 1


@pytest.mark.asyncio
async def test_legacy_producer_without_explicit_publisher_only_queues(
    monkeypatch,
    db_session,
):
    _seed_control(db_session, mode="legacy_direct")
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    monkeypatch.delenv("NANOBOT_PUSH_TOKEN", raising=False)

    result = await _enqueue(
        db_session,
        user_id="legacy-queued-user",
        key="outreach:legacy-queued-user:one",
        publisher=None,
    )

    db_session.expire_all()
    row = db_session.query(ProactiveOutreachLog).one()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    assert result["status"] == "queued"
    assert row.status == "queued"
    assert outbox.status == "pending"
    assert db_session.query(OutboundDeliveryAttempt).count() == 0


@pytest.mark.asyncio
async def test_worker_proactive_drain_reuses_live_writer_and_is_idempotent(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_control(db_session, mode="legacy_direct")
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    monkeypatch.delenv("NANOBOT_PUSH_TOKEN", raising=False)
    queued = await _enqueue(
        db_session,
        user_id="legacy-worker-user",
        key="outreach:legacy-worker-user:one",
        publisher=None,
    )

    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    control.writer_owner = "server-proactive-writer"
    control.writer_token = "server-proactive-token"
    control.writer_version += 1
    control.writer_lease_expires_at = NOW + timedelta(minutes=10)
    db_session.commit()
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    transport_calls = []

    async def success_transport(request):
        transport_calls.append(request.outbox_id)
        return _success()

    first = await proactive_outreach.drain_due_legacy_proactive_outboxes(
        session_factory=factory,
        worker_config=_worker_config(),
        transport=success_transport,
        now=NOW + timedelta(seconds=1),
    )
    second = await proactive_outreach.drain_due_legacy_proactive_outboxes(
        session_factory=factory,
        worker_config=_worker_config(),
        transport=success_transport,
        now=NOW + timedelta(seconds=2),
    )

    db_session.expire_all()
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    assert len(first) == 1
    assert second == []
    assert transport_calls == [queued["outbox_id"]]
    assert row.status == "sent"
    assert outbox.status == "delivered"
    assert db_session.query(OutboundDeliveryAttempt).count() == 1


@pytest.mark.asyncio
async def test_worker_proactive_drain_takes_over_expired_writer(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach
    from core.outbound_delivery_service import LegacyWriterTakeover

    _seed_control(db_session, mode="legacy_direct")
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    queued = await _enqueue(
        db_session,
        user_id="legacy-expired-writer-user",
        key="outreach:legacy-expired-writer-user:one",
        publisher=None,
    )
    control = db_session.get(OutboundDeliveryControl, SOURCE_TYPE)
    control.writer_lease_expires_at = NOW + timedelta(seconds=1)
    db_session.commit()
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    transport_calls = []

    async def success_transport(request):
        transport_calls.append(request.outbox_id)
        return _success()

    results = await proactive_outreach.drain_due_legacy_proactive_outboxes(
        session_factory=factory,
        worker_config=_worker_config(),
        transport=success_transport,
        worker_owner="outbound-worker",
        takeover_writer=LegacyWriterTakeover(
            writer_owner="outbound-worker:proactive-legacy",
            writer_token="proactive-takeover-token",
        ),
        now=NOW + timedelta(seconds=3),
    )

    db_session.expire_all()
    row = db_session.get(ProactiveOutreachLog, queued["log_id"])
    run = db_session.get(OutboundRun, row.outbound_run_id)
    outbox = db_session.get(OutboundDeliveryOutbox, queued["outbox_id"])
    assert len(results) == 1
    assert transport_calls == [queued["outbox_id"]]
    assert row.status == "sent"
    assert outbox.status == "delivered"
    assert run.writer_owner == "outbound-worker:proactive-legacy"
    assert db_session.query(OutboundDeliveryAttempt).count() == 1


@pytest.mark.asyncio
async def test_legacy_drain_recovers_committed_leaf_after_producer_crash(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_control(db_session, mode="legacy_direct")
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)

    async def crash_before_claim(**_kwargs):
        raise RuntimeError("模拟提交后进程退出")

    monkeypatch.setattr(
        proactive_outreach,
        "_deliver_legacy_outreach_leaf",
        crash_before_claim,
    )
    with pytest.raises(RuntimeError, match="提交后进程退出"):
        await _enqueue(
            db_session,
            user_id="legacy-drain-crash-user",
            key="outreach:legacy-drain-crash-user:one",
        )

    db_session.rollback()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    payload_sha256 = outbox.payload_sha256
    assert outbox.status == "pending"
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )

    async def success_transport(_request):
        return _success()

    results = await proactive_outreach.drain_due_legacy_proactive_outboxes(
        session_factory=factory,
        worker_config=_worker_config(),
        transport=success_transport,
        now=NOW + timedelta(seconds=1),
        jitter=lambda _maximum: 0.0,
    )

    db_session.expire_all()
    row = db_session.query(ProactiveOutreachLog).one()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    assert len(results) == 1
    assert row.status == "sent"
    assert outbox.status == "delivered"
    assert outbox.payload_sha256 == payload_sha256
    assert db_session.query(OutboundRun).count() == 1
    assert db_session.query(OutboundDeliveryAttempt).count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "outcome",
        "expected_status",
        "expected_run_status",
        "expected_outbox_status",
        "circuit_count",
    ),
    [
        (
            _transport_outcome("transient", 503, "service_unavailable"),
            "retry_wait",
            "queued",
            "retry_wait",
            0,
        ),
        (
            _transport_outcome(
                "transient",
                429,
                "rate_limited",
                retry_after_seconds=30,
            ),
            "retry_wait",
            "queued",
            "retry_wait",
            0,
        ),
        (
            _transport_outcome("endpoint", 401, "unauthorized"),
            "blocked",
            "blocked",
            "failed",
            1,
        ),
    ],
)
async def test_legacy_worker_default_transport_preserves_structured_failure(
    monkeypatch,
    db_session,
    outcome,
    expected_status,
    expected_run_status,
    expected_outbox_status,
    circuit_count,
):
    from core import outbound_transport, proactive_outreach

    _seed_control(db_session, mode="legacy_direct")
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    worker_config = _worker_config()
    observed_tokens = []

    async def structured_transport(*_args, **kwargs):
        observed_tokens.append(kwargs["push_token"])
        return outcome

    monkeypatch.setattr(
        outbound_transport,
        "deliver_qq_push_with_session",
        structured_transport,
    )

    queued = await proactive_outreach.deliver_outreach_once(
        user_id=f"legacy-structured-{outcome.status_code}",
        idempotency_key=f"outreach:legacy-structured:{outcome.status_code}",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="结构化错误分类",
        next_check_at=None,
        next_intent="",
        message="验证默认 transport 保留结构化错误。",
        forced=False,
        db=db_session,
        created_at=NOW,
        publisher=None,
    )
    assert queued["status"] == "queued"
    assert db_session.query(OutboundDeliveryAttempt).count() == 0
    assert observed_tokens == []

    _activate_legacy_writer(db_session)
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    results = await proactive_outreach.drain_due_legacy_proactive_outboxes(
        session_factory=factory,
        worker_config=worker_config,
        now=NOW + timedelta(seconds=1),
        jitter=lambda _maximum: 0.0,
    )

    db_session.expire_all()
    row = db_session.query(ProactiveOutreachLog).one()
    run = db_session.query(OutboundRun).one()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    attempt = db_session.query(OutboundDeliveryAttempt).one()
    assert len(results) == 1
    assert row.status == expected_status
    assert run.status == expected_run_status
    assert outbox.status == expected_outbox_status
    assert attempt.http_status == outcome.status_code
    assert attempt.result_category == outcome.category
    assert attempt.error_type == outcome.error_type
    assert db_session.query(OutboundDeliveryCircuit).count() == circuit_count
    assert observed_tokens == [worker_config.push_token]


@pytest.mark.asyncio
async def test_legacy_drain_retries_same_payload_without_regeneration(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_control(db_session, mode="legacy_direct")
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    first_outcome = _transient_503()
    queued = await proactive_outreach.deliver_outreach_once(
        user_id="legacy-drain-retry-user",
        idempotency_key="outreach:legacy-drain-retry-user:one",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="验证持久重试",
        next_check_at=None,
        next_intent="",
        message="重试时必须复用这条冻结正文。",
        forced=False,
        db=db_session,
        created_at=NOW,
        publisher=None,
    )
    assert queued["status"] == "queued"
    assert db_session.query(OutboundDeliveryAttempt).count() == 0

    _activate_legacy_writer(db_session)
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    transport_requests = []

    async def transient_transport(request):
        transport_requests.append((request.outbox_id, request.message))
        return first_outcome

    first_results = await proactive_outreach.drain_due_legacy_proactive_outboxes(
        session_factory=factory,
        worker_config=_worker_config(),
        transport=transient_transport,
        now=NOW + timedelta(seconds=1),
        jitter=lambda _maximum: 0.0,
    )

    db_session.expire_all()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    payload_sha256 = outbox.payload_sha256
    retry_at = outbox.next_attempt_at
    assert len(first_results) == 1
    assert outbox.status == "retry_wait"
    assert retry_at is not None

    async def success_transport(request):
        transport_requests.append((request.outbox_id, request.message))
        return _success()

    results = await proactive_outreach.drain_due_legacy_proactive_outboxes(
        session_factory=factory,
        worker_config=_worker_config(),
        transport=success_transport,
        now=retry_at + timedelta(microseconds=1),
        jitter=lambda _maximum: 0.0,
    )

    db_session.expire_all()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    assert len(results) == 1
    assert outbox.status == "delivered"
    assert outbox.payload_sha256 == payload_sha256
    assert db_session.query(OutboundRun).count() == 1
    assert db_session.query(OutboundDeliveryOutbox).count() == 1
    assert db_session.query(OutboundDeliveryAttempt).count() == 2
    assert transport_requests == [
        (outbox.id, "重试时必须复用这条冻结正文。"),
        (outbox.id, "重试时必须复用这条冻结正文。"),
    ]


@pytest.mark.asyncio
async def test_legacy_drain_terminalizes_leaf_past_retry_deadline(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_control(db_session, mode="legacy_direct")
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    queued = await proactive_outreach.deliver_outreach_once(
        user_id="legacy-deadline-user",
        idempotency_key="outreach:legacy-deadline-user:one",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="验证重试截止时间",
        next_check_at=None,
        next_intent="",
        message="超过截止时间后不得继续投递。",
        forced=False,
        db=db_session,
        created_at=NOW,
        publisher=None,
    )
    assert queued["status"] == "queued"
    assert db_session.query(OutboundDeliveryAttempt).count() == 0

    _activate_legacy_writer(db_session)
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )

    async def transient_transport(_request):
        return _transient_503()

    first_results = await proactive_outreach.drain_due_legacy_proactive_outboxes(
        session_factory=factory,
        worker_config=_worker_config(),
        transport=transient_transport,
        now=NOW + timedelta(seconds=1),
        jitter=lambda _maximum: 0.0,
    )

    db_session.expire_all()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    assert len(first_results) == 1
    assert outbox.status == "retry_wait"
    assert outbox.next_attempt_at is not None
    scan_at = outbox.next_attempt_at
    outbox.retry_deadline_at = scan_at - timedelta(microseconds=1)
    db_session.commit()
    transport_calls = []

    async def forbidden_transport(request):
        transport_calls.append(request)
        raise AssertionError("过期 legacy leaf 不得再次发送 HTTP")

    results = await proactive_outreach.drain_due_legacy_proactive_outboxes(
        session_factory=factory,
        worker_config=_worker_config(),
        transport=forbidden_transport,
        now=scan_at,
    )

    db_session.expire_all()
    row = db_session.query(ProactiveOutreachLog).one()
    run = db_session.query(OutboundRun).one()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    assert results == []
    assert transport_calls == []
    assert row.status == "failed"
    assert run.status == "failed"
    assert run.failure_type == "retry_exhausted"
    assert outbox.status == "failed"
    assert outbox.last_error_type == "retry_exhausted"
    assert db_session.query(OutboundDeliveryAttempt).count() == 1


@pytest.mark.asyncio
async def test_fenced_expired_legacy_leaf_does_not_poison_terminalization_batch(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_control(db_session, mode="legacy_direct")
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    for index in (1, 2):
        result = await proactive_outreach.deliver_outreach_once(
            user_id=f"legacy-poison-user-{index}",
            idempotency_key=f"outreach:legacy-poison-user:{index}",
            grounding={"recent_messages": []},
            judge_should=True,
            judge_reason="验证过期批次隔离",
            next_check_at=None,
            next_intent="",
            message=f"冻结正文 {index}",
            forced=False,
            db=db_session,
            created_at=NOW + timedelta(seconds=index),
            publisher=None,
        )
        assert result["status"] == "queued"

    assert db_session.query(OutboundDeliveryAttempt).count() == 0
    _activate_legacy_writer(db_session)
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )

    async def transient_transport(_request):
        return _transient_503()

    first_results = await proactive_outreach.drain_due_legacy_proactive_outboxes(
        session_factory=factory,
        worker_config=_worker_config(),
        transport=transient_transport,
        now=NOW + timedelta(seconds=3),
        jitter=lambda _maximum: 0.0,
    )
    assert len(first_results) == 2

    db_session.expire_all()
    rows = db_session.query(ProactiveOutreachLog).order_by(
        ProactiveOutreachLog.id.asc()
    ).all()
    outboxes = db_session.query(OutboundDeliveryOutbox).order_by(
        OutboundDeliveryOutbox.id.asc()
    ).all()
    scan_at = max(outbox.next_attempt_at for outbox in outboxes)
    rows[0].message = "篡改后触发来源 revision fencing"
    for outbox in outboxes:
        outbox.retry_deadline_at = scan_at - timedelta(microseconds=1)
    db_session.commit()
    transport_calls = []

    async def forbidden_transport(request):
        transport_calls.append(request)
        raise AssertionError("过期 leaf 不得再次发送 HTTP")

    results = await proactive_outreach.drain_due_legacy_proactive_outboxes(
        session_factory=factory,
        worker_config=_worker_config(),
        transport=forbidden_transport,
        now=scan_at,
    )

    db_session.expire_all()
    rows = db_session.query(ProactiveOutreachLog).order_by(
        ProactiveOutreachLog.id.asc()
    ).all()
    runs = db_session.query(OutboundRun).order_by(OutboundRun.id.asc()).all()
    outboxes = db_session.query(OutboundDeliveryOutbox).order_by(
        OutboundDeliveryOutbox.id.asc()
    ).all()
    assert results == []
    assert transport_calls == []
    assert [row.status for row in rows] == ["retry_wait", "failed"]
    assert [run.status for run in runs] == ["failed", "failed"]
    assert all(run.failure_type == "retry_exhausted" for run in runs)
    assert [outbox.status for outbox in outboxes] == [
        "failed",
        "failed",
    ]
    assert all(
        outbox.last_error_type == "retry_exhausted"
        for outbox in outboxes
    )
    assert db_session.query(OutboundDeliveryAttempt).count() == 2


@pytest.mark.asyncio
async def test_outreach_runtime_lease_does_not_reuse_old_source_time(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_control(db_session, mode="legacy_direct")
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    monkeypatch.setenv(
        "NANOBOT_PUSH_TOKEN",
        "push-token-proactive-runtime-sentinel",
    )
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    runtime_now = datetime.now(timezone.utc).replace(tzinfo=None)
    source_created_at = runtime_now - timedelta(minutes=20)
    expiry_summaries = []

    async def publisher(*_args):
        with factory() as scanner:
            summary = expire_stale_delivery_leases(
                scanner,
                endpoint_key="qq_push",
                now=runtime_now,
            )
            expiry_summaries.append(summary)
            scanner.commit()
        return True

    result = await proactive_outreach.deliver_outreach_once(
        user_id="legacy-fresh-clock-user",
        idempotency_key="outreach:legacy-fresh-clock-user:one",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="验证运行时钟",
        next_check_at=None,
        next_intent="",
        message="业务时间不能作为租约时钟。",
        forced=False,
        db=db_session,
        created_at=source_created_at,
        publisher=publisher,
    )

    db_session.expire_all()
    row = db_session.query(ProactiveOutreachLog).one()
    run = db_session.query(OutboundRun).one()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    attempt = db_session.query(OutboundDeliveryAttempt).one()
    assert result["status"] == "sent"
    assert [summary.total for summary in expiry_summaries] == [0]
    assert row.status == "sent"
    assert run.status == "succeeded"
    assert run.active_outbox_id == outbox.id
    assert outbox.status == "delivered"
    assert outbox.lease_owner is None
    assert outbox.lease_token is None
    assert outbox.lease_expires_at is None
    assert attempt.status == "succeeded"
    assert attempt.request_started is True


@pytest.mark.asyncio
async def test_blocked_candidate_without_outbox_recovers_without_model_work(
    monkeypatch,
    db_session,
):
    from core import outbound_delivery, proactive_outreach

    _seed_control(db_session)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    original_commit = outbound_delivery.commit_prepared_outbox

    def open_circuit_before_commit(db, **kwargs):
        opened_at = kwargs.get("now") or NOW
        db.add(OutboundDeliveryCircuit(
            scope_type="endpoint",
            scope_fingerprint=endpoint_circuit_fingerprint("qq_push"),
            config_revision=CONFIG_REVISION,
            status="open",
            reason_type="route_missing",
            opened_at=opened_at,
            created_at=opened_at,
            updated_at=opened_at,
        ))
        db.flush()
        return original_commit(db, **kwargs)

    monkeypatch.setattr(
        outbound_delivery,
        "commit_prepared_outbox",
        open_circuit_before_commit,
    )
    first = await _enqueue(
        db_session,
        user_id="blocked-recovery-user",
        key="outreach:blocked-recovery-user:one",
    )
    assert first["status"] == "blocked"
    assert db_session.query(OutboundRun).one().active_outbox_id is None
    assert db_session.query(OutboundDeliveryOutbox).count() == 0
    db_session.query(OutboundDeliveryCircuit).delete()
    db_session.commit()
    monkeypatch.setattr(
        outbound_delivery,
        "commit_prepared_outbox",
        original_commit,
    )
    calls = {"thread": 0, "judge": 0, "research": 0, "generator": 0}

    def forbidden(name):
        def invoke(*_args, **_kwargs):
            calls[name] += 1
            raise AssertionError("blocked 候选恢复不得重新调用模型")

        return invoke

    async def forbidden_research(*_args, **_kwargs):
        calls["research"] += 1
        raise AssertionError("blocked 候选恢复不得重新研究")

    second = await proactive_outreach.run_outreach_once(
        "blocked-recovery-user",
        db=db_session,
        now=NOW + timedelta(seconds=1),
        max_silence_min=999999,
        thread_extractor=forbidden("thread"),
        judge_fn=forbidden("judge"),
        generator_fn=forbidden("generator"),
        research_fn=forbidden_research,
    )

    db_session.expire_all()
    row = db_session.query(ProactiveOutreachLog).one()
    run = db_session.query(OutboundRun).one()
    outbox = db_session.query(OutboundDeliveryOutbox).one()
    assert second["status"] == "queued"
    assert calls == {"thread": 0, "judge": 0, "research": 0, "generator": 0}
    assert row.status == "queued"
    assert run.status == "queued"
    assert run.active_outbox_id == outbox.id
    assert db_session.query(OutboundRun).count() == 1
    assert db_session.query(OutboundDeliveryOutbox).count() == 1
