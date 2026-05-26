import json
from datetime import datetime, timedelta

from core.database import ChatLog, ConversationTurn, RollingSessionSummary, SessionSummaryJob, User


def _turn(db, *, session_id="s1", user_id="u1", role="user", content="hello", meta=None, created_at=None):
    row = ConversationTurn(
        user_id=user_id,
        session_id=session_id,
        role=role,
        content=content,
        created_at=created_at or datetime.now(),
        meta_json=json.dumps(meta or {"kind": "chat"}, ensure_ascii=False),
    )
    db.add(row)
    db.flush()
    return row


def test_eligible_excludes_internal_and_no_context_turns(db_session):
    from app.session_memory.windowing import load_context_eligible_turns

    ok = _turn(db_session, content="可用消息")
    internal = _turn(db_session, content="内部重试", meta={"kind": "reply_contract_retry"})
    no_context = _turn(db_session, content="不可注入", meta={"no_context": True})
    moderated = _turn(db_session, content="审核不可注入", meta={"moderation": {"no_context": True}})
    db_session.commit()

    eligible, debug = load_context_eligible_turns(db_session, session_id="s1")

    assert [row.id for row in eligible] == [ok.id]
    skipped = {item["turn_id"]: item["reason"] for item in debug["skipped"]}
    assert skipped[internal.id] == "internal_kind:reply_contract_retry"
    assert skipped[no_context.id] == "meta_no_context"
    assert skipped[moderated.id] == "moderation_no_context"


def test_recent_raw_window_selects_latest_by_id_not_created_at(db_session):
    from app.session_memory.windowing import select_latest_raw_window

    old_created_late_id = _turn(
        db_session,
        content="id 小但时间新",
        created_at=datetime.now() + timedelta(days=1),
    )
    late_id_old_created = _turn(
        db_session,
        content="id 大但时间旧",
        created_at=datetime.now() - timedelta(days=1),
    )
    newest_id = _turn(db_session, content="最新 id")
    db_session.commit()

    selected, debug = select_latest_raw_window(
        [old_created_late_id, late_id_old_created, newest_id],
        chat_type="private",
        max_turns=2,
        max_tokens=10000,
    )

    assert debug["raw_window_turn_ids"] == [late_id_old_created.id, newest_id.id]
    assert selected[0]["turn_id"] == late_id_old_created.id


def test_pending_boundary_between_summary_cursor_and_raw_start(db_session):
    from app.session_memory.windowing import select_pending_for_summary

    turns = [_turn(db_session, content=f"turn {i}") for i in range(6)]
    db_session.commit()

    pending = select_pending_for_summary(
        turns,
        last_covered_id=turns[1].id,
        raw_window_start_turn_id=turns[4].id,
    )

    assert [row.id for row in pending] == [turns[2].id, turns[3].id]


def test_build_session_memory_large_session_uses_latest_raw_window(db_session):
    from core.context_builder import build_session_memory

    for i in range(1000):
        _turn(db_session, content=f"历史消息 {i + 1}")
    db_session.commit()

    _header, messages, debug = build_session_memory(
        db_session,
        "s1",
        user_id="u1",
        max_total=10000,
    )

    turn_ids = [int(m["turn_id"]) for m in messages if m.get("turn_id")]
    assert turn_ids
    assert turn_ids[-1] == 1000
    assert min(turn_ids) > 900
    assert debug["rolling_summary_raw_start_turn_id"] == min(turn_ids)


def test_admin_rollup_inputs_large_session_uses_latest_raw_window(db_session):
    from api.admin.session_memory_routes import _build_rollup_inputs

    for i in range(1000):
        _turn(db_session, content=f"管理端历史消息 {i + 1}")
    db_session.commit()

    _active, pending, raw_window, raw_debug, _eligible_debug = _build_rollup_inputs(
        db_session,
        session_id="s1",
        user_id="u1",
        chat_type="private",
    )

    turn_ids = [int(item["turn_id"]) for item in raw_window]
    assert turn_ids
    assert turn_ids[-1] == 1000
    assert min(turn_ids) > 900
    assert raw_debug["raw_window_start_turn_id"] == min(turn_ids)
    assert all(turn.id < min(turn_ids) for turn in pending)


def test_history_clear_at_archives_active_summary(db_session):
    from app.session_memory.rolling_summary import get_active_summary

    db_session.add(User(id="u1", history_clear_at=datetime.now()))
    row = RollingSessionSummary(
        session_id="s1",
        user_id="u1",
        status="active",
        summary_text="旧摘要",
        covered_until_turn_id=10,
        updated_at=datetime.now() - timedelta(minutes=1),
    )
    db_session.add(row)
    db_session.commit()

    summary = get_active_summary(
        db_session,
        "s1",
        after_clear_at=datetime.now() - timedelta(seconds=1),
    )

    assert summary is None
    assert row.status == "archived"


def test_save_new_summary_archives_old_active(db_session):
    from app.session_memory.rolling_summary import save_new_active_summary

    old = RollingSessionSummary(
        session_id="s1",
        user_id="u1",
        status="active",
        summary_text="旧摘要",
        covered_until_turn_id=2,
    )
    db_session.add(old)
    turns = [_turn(db_session, content=f"新内容 {i}") for i in range(3)]
    db_session.commit()

    row = save_new_active_summary(
        db_session,
        old_summary=old,
        session_id="s1",
        user_id="u1",
        chat_type="private",
        summary_json={"summary": "新摘要", "quality": {"score": 0.8, "issues": []}},
        pending_turns=turns,
        raw_window_start_turn_id=turns[-1].id + 1,
        model="deterministic",
        prompt_sha256="abc",
    )

    assert old.status == "archived"
    assert row.status == "active"
    assert row.covered_until_turn_id == turns[-1].id
    assert row.summary_kind == "deterministic_fallback"
    meta = json.loads(row.meta_json or "{}")
    assert meta["summary_kind"] == "deterministic_fallback"
    assert row.stable_hash


def test_save_new_summary_archives_all_existing_active_rows(db_session):
    from app.session_memory.rolling_summary import save_new_active_summary

    old_a = RollingSessionSummary(
        session_id="s1",
        user_id="u1",
        status="active",
        summary_text="旧摘要 A",
        covered_until_turn_id=2,
    )
    old_b = RollingSessionSummary(
        session_id="s1",
        user_id="u1",
        status="active",
        summary_text="旧摘要 B",
        covered_until_turn_id=4,
    )
    db_session.add_all([old_a, old_b])
    turns = [_turn(db_session, content=f"新内容 {i}") for i in range(3)]
    db_session.commit()

    row = save_new_active_summary(
        db_session,
        old_summary=old_b,
        session_id="s1",
        user_id="u1",
        chat_type="private",
        summary_json={"summary": "新摘要", "quality": {"score": 0.8, "issues": []}},
        pending_turns=turns,
        raw_window_start_turn_id=turns[-1].id + 1,
        model="deterministic",
        prompt_sha256="abc",
    )

    active_rows = (
        db_session.query(RollingSessionSummary)
        .filter(
            RollingSessionSummary.session_id == "s1",
            RollingSessionSummary.status == "active",
        )
        .all()
    )
    assert [item.id for item in active_rows] == [row.id]
    assert old_a.status == "archived"
    assert old_b.status == "archived"


def test_deterministic_summary_compacts_instead_of_appending_raw_text(db_session):
    from app.session_memory import config
    from app.session_memory.rolling_summary import audit_rolling_summary
    from app.session_memory.summarizer import build_rolling_summary_payload

    previous = RollingSessionSummary(
        session_id="s1",
        user_id="u1",
        status="active",
        summary_text="此前摘要" + "甲" * 1700,
        covered_until_turn_id=10,
    )
    db_session.add(previous)
    pending = [
        _turn(
            db_session,
            role="user" if i % 2 == 0 else "assistant",
            content=f"需要滚动压缩的新增消息 {i} " + "乙" * 180,
        )
        for i in range(20)
    ]
    db_session.commit()

    payload = build_rolling_summary_payload(
        previous_summary=previous,
        pending_turns=pending,
    )
    ok, issues = audit_rolling_summary(
        summary_json=payload,
        pending_turn_ids=[turn.id for turn in pending],
        recent_raw_turn_ids=[],
    )

    assert len(payload["summary"]) <= config.ROLLING_SUMMARY_MAX_CHARS
    assert ok is True
    assert issues == []
    assert "需要滚动压缩的新增消息 18" in payload["summary"]
    assert "需要滚动压缩的新增消息 0" not in payload["summary"]


def test_build_chat_context_group_rolls_up_pending_conversation_turns(db_session):
    from core.context_builder import build_chat_context

    now = datetime.now()
    for i in range(24):
        sender = "A" if i % 2 == 0 else "B"
        _turn(
            db_session,
            session_id="group_1",
            user_id="group_1",
            role="user",
            content=f"群聊历史 {i + 1}：讨论滚动摘要边界",
            meta={"kind": "chat", "sender_name": sender},
            created_at=now + timedelta(seconds=i),
        )
    db_session.add(ChatLog(
        user_id="group_1",
        session_id="group_1",
        role="ambient",
        sender_name="A",
        content="[A]: 继续刚才的话题",
        message_id="m1",
        processed=1,
        created_at=now + timedelta(minutes=1),
        meta_json=json.dumps({"kind": "chat"}, ensure_ascii=False),
    ))
    db_session.commit()

    header, messages, debug = build_chat_context(
        db_session,
        "group_1",
        user_id="group_1",
        is_group=True,
        group_id="1",
        max_total=10000,
    )

    active_summary = (
        db_session.query(RollingSessionSummary)
        .filter(
            RollingSessionSummary.session_id == "group_1",
            RollingSessionSummary.status == "active",
        )
        .first()
    )
    assert active_summary is not None
    assert "<rolling_session_summary" in header
    assert messages
    assert debug["rolling_summary_injected"] is True
    assert debug["rolling_summary_source"] == "conversation_turn"
    assert debug["rolling_summary_scope"] == "bot_participation"
    assert debug["rolling_summary_pending_turn_ids"]
    assert debug["rolling_summary_recent_raw_turn_ids"][-1] == 24


def test_rollup_audit_rejects_current_user_input_leak(db_session):
    from app.session_memory.rolling_summary import audit_rolling_summary

    ok, issues = audit_rolling_summary(
        summary_json={"summary": "用户刚刚要求：不要把这句话提前总结进历史"},
        pending_turn_ids=[1, 2],
        recent_raw_turn_ids=[3, 4],
        current_user_input="用户刚刚要求：不要把这句话提前总结进历史",
    )

    assert ok is False
    assert "summary_contains_current_user_input" in issues


def test_enqueue_session_summary_job_is_idempotent_for_same_range(db_session):
    from app.session_memory.jobs import enqueue_session_summary_job

    fallback = RollingSessionSummary(
        session_id="s1",
        user_id="u1",
        chat_type="private",
        status="active",
        summary_kind="deterministic_fallback",
        covered_from_turn_id=1,
        covered_until_turn_id=3,
    )
    db_session.add(fallback)
    turns = [_turn(db_session, content=f"待总结 {i}") for i in range(3)]
    db_session.commit()

    job1, created1 = enqueue_session_summary_job(
        db_session,
        session_id="s1",
        user_id="u1",
        chat_type="private",
        pending_turns=turns,
        previous_summary=None,
        fallback_summary=fallback,
    )
    job2, created2 = enqueue_session_summary_job(
        db_session,
        session_id="s1",
        user_id="u1",
        chat_type="private",
        pending_turns=turns,
        previous_summary=None,
        fallback_summary=fallback,
    )

    assert created1 is True
    assert created2 is False
    assert job1.id == job2.id
    assert job1.status == "pending"
    assert job1.fallback_summary_id == fallback.id
    assert job1.covered_from_turn_id == turns[0].id
    assert job1.covered_until_turn_id == turns[-1].id


def test_failed_session_summary_job_can_be_retried(db_session):
    from app.session_memory.jobs import retry_session_summary_job

    job = SessionSummaryJob(
        session_id="s1",
        user_id="u1",
        chat_type="private",
        covered_from_turn_id=1,
        covered_until_turn_id=3,
        source_turn_ids_json="[1,2,3]",
        status="failed",
        retry_count=1,
        max_retry=3,
        error="json_parse_failed",
    )
    db_session.add(job)
    db_session.commit()

    retried = retry_session_summary_job(db_session, job.id)

    assert retried.status == "pending"
    assert retried.error == ""
    assert retried.next_retry_at is None


def test_rollup_success_enqueues_llm_summary_job(db_session):
    from app.session_memory.rolling_summary import maybe_rollup_session_summary

    turns = [_turn(db_session, content=("需要摘要 " + "甲" * 300)) for _ in range(6)]
    db_session.commit()

    result = maybe_rollup_session_summary(
        db_session,
        session_id="s1",
        user_id="u1",
        chat_type="private",
        active_summary=None,
        pending_turns=turns,
        recent_raw_turn_ids=[],
        raw_window_start_turn_id=turns[-1].id + 1,
    )

    assert result.summary is not None
    assert result.summary_job_id > 0
    job = db_session.get(SessionSummaryJob, result.summary_job_id)
    assert job is not None
    assert job.status == "pending"
    assert job.fallback_summary_id == result.summary.id


def test_rollup_dry_run_does_not_enqueue_llm_summary_job(db_session):
    from app.session_memory.rolling_summary import maybe_rollup_session_summary

    turns = [_turn(db_session, content=("dry run 摘要 " + "乙" * 300)) for _ in range(6)]
    db_session.commit()

    result = maybe_rollup_session_summary(
        db_session,
        session_id="s1",
        user_id="u1",
        chat_type="private",
        active_summary=None,
        pending_turns=turns,
        recent_raw_turn_ids=[],
        raw_window_start_turn_id=turns[-1].id + 1,
        dry_run=True,
    )

    assert result.summary is None
    assert result.summary_job_id == 0
    assert db_session.query(SessionSummaryJob).count() == 0


def test_session_summary_worker_promotes_llm_summary_and_archives_fallback(db_session):
    from app.session_memory.jobs import enqueue_session_summary_job
    from app.session_memory.llm_summarizer import run_session_summary_worker_once

    turns = [_turn(db_session, content=f"用户继续讨论会话摘要质量 {i}") for i in range(6)]
    fallback = RollingSessionSummary(
        session_id="s1",
        user_id="u1",
        chat_type="private",
        status="active",
        summary_kind="deterministic_fallback",
        summary_text="fallback 摘录",
        covered_from_turn_id=turns[0].id,
        covered_until_turn_id=turns[-1].id,
        source_turn_ids_json=json.dumps([turn.id for turn in turns]),
        source_turn_count=len(turns),
        quality_score=0.72,
    )
    db_session.add(fallback)
    db_session.commit()
    job, created = enqueue_session_summary_job(
        db_session,
        session_id="s1",
        user_id="u1",
        chat_type="private",
        pending_turns=turns,
        previous_summary=None,
        fallback_summary=fallback,
    )
    assert created is True

    payload = {
        "summary": "用户正在持续完善滚动会话摘要，重点是异步 LLM 摘要、审计和 fallback 边界。",
        "open_threads": ["继续补齐 worker 与管理入口"],
        "decisions": ["LLM 摘要异步生成，审计通过后优先注入"],
        "important_user_requests": ["一次性做完并分批次提交"],
        "resolved_items": [],
        "artifacts": [],
        "participants": [],
        "keywords": ["rolling summary", "worker"],
        "quality": {"score": 0.86, "issues": []},
    }

    result = run_session_summary_worker_once(
        db_session,
        summarizer=lambda _messages: json.dumps(payload, ensure_ascii=False),
        owner="test-worker",
    )

    db_session.refresh(job)
    db_session.refresh(fallback)
    assert result["processed"] == 1
    assert result["done"] == 1
    assert job.status == "done"
    assert job.result_summary_id
    assert fallback.status == "archived"

    llm_summary = db_session.get(RollingSessionSummary, job.result_summary_id)
    assert llm_summary is not None
    assert llm_summary.status == "active"
    assert llm_summary.summary_kind == "llm_episode"
    assert llm_summary.supersedes_summary_id == fallback.id
    assert llm_summary.llm_status == "success"
    assert llm_summary.quality_score == 0.86
    assert "异步 LLM 摘要" in llm_summary.summary_text


def test_session_summary_worker_non_json_retries_without_replacing_fallback(db_session):
    from app.session_memory.jobs import enqueue_session_summary_job
    from app.session_memory.llm_summarizer import run_session_summary_worker_once

    turns = [_turn(db_session, content=f"待总结失败用例 {i}") for i in range(6)]
    fallback = RollingSessionSummary(
        session_id="s1",
        user_id="u1",
        chat_type="private",
        status="active",
        summary_kind="deterministic_fallback",
        summary_text="fallback 保留",
        covered_from_turn_id=turns[0].id,
        covered_until_turn_id=turns[-1].id,
        source_turn_ids_json=json.dumps([turn.id for turn in turns]),
    )
    db_session.add(fallback)
    db_session.commit()
    job, _created = enqueue_session_summary_job(
        db_session,
        session_id="s1",
        user_id="u1",
        chat_type="private",
        pending_turns=turns,
        previous_summary=None,
        fallback_summary=fallback,
        max_retry=2,
    )

    result = run_session_summary_worker_once(
        db_session,
        summarizer=lambda _messages: "不是 JSON",
        owner="test-worker",
    )

    db_session.refresh(job)
    db_session.refresh(fallback)
    assert result["failed"] == 1
    assert job.status == "pending"
    assert job.retry_count == 1
    assert job.next_retry_at is not None
    assert "json_parse_failed" in job.error
    assert job.result_summary_id is None
    assert fallback.status == "active"
    assert (
        db_session.query(RollingSessionSummary)
        .filter(RollingSessionSummary.summary_kind == "llm_episode")
        .count()
        == 0
    )


def test_session_summary_worker_quality_gate_keeps_fallback(db_session):
    from app.session_memory.jobs import enqueue_session_summary_job
    from app.session_memory.llm_summarizer import run_session_summary_worker_once

    turns = [_turn(db_session, content=f"低质量摘要用例 {i}") for i in range(6)]
    fallback = RollingSessionSummary(
        session_id="s1",
        user_id="u1",
        chat_type="private",
        status="active",
        summary_kind="deterministic_fallback",
        summary_text="fallback 保留",
        covered_from_turn_id=turns[0].id,
        covered_until_turn_id=turns[-1].id,
        source_turn_ids_json=json.dumps([turn.id for turn in turns]),
    )
    db_session.add(fallback)
    db_session.commit()
    job, _created = enqueue_session_summary_job(
        db_session,
        session_id="s1",
        user_id="u1",
        chat_type="private",
        pending_turns=turns,
        previous_summary=None,
        fallback_summary=fallback,
        max_retry=1,
    )
    payload = {
        "summary": "过短",
        "quality": {"score": 0.4, "issues": []},
    }

    result = run_session_summary_worker_once(
        db_session,
        summarizer=lambda _messages: json.dumps(payload, ensure_ascii=False),
        owner="test-worker",
    )

    db_session.refresh(job)
    db_session.refresh(fallback)
    assert result["failed"] == 1
    assert job.status == "failed"
    assert "quality_score_below_threshold" in job.error
    assert fallback.status == "active"
