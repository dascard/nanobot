from __future__ import annotations

import json
from datetime import datetime, timedelta

from core.db.models.chat import ChatLog
from core.db.models.session_memory import RollingSessionSummary, SessionSummaryJob


def _add_group_logs(
    db_session,
    *,
    session_id: str,
    count: int,
    started_at: datetime,
) -> list[ChatLog]:
    rows: list[ChatLog] = []
    for index in range(count):
        row = ChatLog(
            user_id=session_id,
            session_id=session_id,
            sender_name="甲" if index % 2 == 0 else "乙",
            role="ambient",
            content=f"[发送者]: 群消息 {index + 1}",
            processed=1,
            message_id=f"m{index + 1}",
            created_at=started_at + timedelta(seconds=index),
            meta_json=json.dumps({"kind": "chat"}, ensure_ascii=False),
        )
        db_session.add(row)
        rows.append(row)
    db_session.flush()
    return rows


def _small_thresholds(monkeypatch) -> None:
    from app.session_memory import group_rollup

    monkeypatch.setattr(group_rollup.config, "GROUP_RAW_WINDOW_MAX_TOKENS", 6)
    monkeypatch.setattr(
        group_rollup.config,
        "GROUP_CACHE_EPOCH_LOW_WATER_TOKENS",
        6,
        raising=False,
    )
    monkeypatch.setattr(
        group_rollup.config,
        "GROUP_CACHE_EPOCH_HIGH_WATER_TOKENS",
        22,
        raising=False,
    )
    monkeypatch.setattr(group_rollup.config, "GROUP_ROLLING_MIN_TOKENS", 8)
    # 新逻辑必须以总 epoch 高水位触发，不能继续依赖旧 pending-only 阈值。
    monkeypatch.setattr(group_rollup.config, "GROUP_ROLLING_FORCE_TOKENS", 999)
    monkeypatch.setattr(group_rollup.config, "GROUP_ROLLING_JOB_MAX_TOKENS", 16)
    monkeypatch.setattr(group_rollup, "group_chatlog_token_cost", lambda _row: 1)


def test_group_rollup_protects_latest_tail_and_waits_for_idle(
    db_session,
    monkeypatch,
):
    from app.session_memory.group_rollup import build_group_rollup_decision

    _small_thresholds(monkeypatch)
    started_at = datetime(2026, 7, 31, 10, 0, 0)
    rows = _add_group_logs(
        db_session,
        session_id="group_soft",
        count=14,
        started_at=started_at,
    )

    active = build_group_rollup_decision(
        db_session,
        session_id="group_soft",
        now=rows[-1].created_at + timedelta(seconds=30),
    )
    idle = build_group_rollup_decision(
        db_session,
        session_id="group_soft",
        now=rows[-1].created_at + timedelta(seconds=61),
    )

    assert [row.id for row in idle.protected_rows] == [
        row.id for row in rows[-6:]
    ]
    assert [row.id for row in idle.pending_rows] == [
        row.id for row in rows[:8]
    ]
    assert idle.protected_tokens == 6
    assert idle.pending_tokens == 8
    assert active.should_enqueue is False
    assert active.reason == "group_active"
    assert idle.should_enqueue is True
    assert idle.reason == "idle_threshold"


def test_group_rollup_cooldown_and_force_threshold(
    db_session,
    monkeypatch,
):
    from app.session_memory.group_rollup import build_group_rollup_decision
    from app.session_memory.rolling_summary import SUMMARY_SOURCE_CHAT_LOG

    _small_thresholds(monkeypatch)
    started_at = datetime(2026, 7, 31, 11, 0, 0)
    rows = _add_group_logs(
        db_session,
        session_id="group_force",
        count=22,
        started_at=started_at,
    )
    summary = RollingSessionSummary(
        session_id="group_force",
        user_id="group_force",
        chat_type="group",
        status="active",
        summary_kind="llm_episode",
        summary_text="旧摘要",
        source_type=SUMMARY_SOURCE_CHAT_LOG,
        covered_from_source_id=0,
        covered_until_source_id=0,
        source_ids_json="[]",
        updated_at=rows[-1].created_at,
    )
    db_session.add(summary)
    db_session.flush()

    forced = build_group_rollup_decision(
        db_session,
        session_id="group_force",
        now=rows[-1].created_at + timedelta(seconds=1),
    )

    assert len(forced.protected_rows) == 6
    assert len(forced.pending_rows) == 16
    assert forced.pending_tokens == 16
    assert forced.epoch_tokens == 22
    assert forced.epoch_high_water_tokens == 22
    assert forced.force is True
    assert forced.should_enqueue is True
    assert forced.reason == "force_threshold"

    db_session.query(ChatLog).filter(
        ChatLog.session_id == "group_force",
        ChatLog.id.in_([row.id for row in rows[:8]]),
    ).delete(synchronize_session=False)
    db_session.flush()
    cooled = build_group_rollup_decision(
        db_session,
        session_id="group_force",
        now=rows[-1].created_at + timedelta(seconds=61),
    )
    assert cooled.pending_tokens == 8
    assert cooled.should_enqueue is False
    assert cooled.reason == "cooldown"


def test_group_rollup_discovery_deduplicates_inflight_and_failed_coverage(
    db_session,
    monkeypatch,
):
    from app.session_memory.group_rollup import discover_group_summary_jobs
    from app.session_memory.rolling_summary import SUMMARY_SOURCE_CHAT_LOG

    _small_thresholds(monkeypatch)
    started_at = datetime(2026, 7, 31, 12, 0, 0)
    rows = _add_group_logs(
        db_session,
        session_id="group_discovery",
        count=14,
        started_at=started_at,
    )
    checked_at = rows[-1].created_at + timedelta(seconds=61)

    first = discover_group_summary_jobs(db_session, now=checked_at)
    job = db_session.query(SessionSummaryJob).one()
    second = discover_group_summary_jobs(db_session, now=checked_at)
    job.status = "failed"
    db_session.flush()
    third = discover_group_summary_jobs(db_session, now=checked_at)

    assert first["created"] == 1
    assert second["inflight"] == 1
    assert third["failed_same_coverage"] == 1
    assert db_session.query(SessionSummaryJob).count() == 1
    assert job.source_type == SUMMARY_SOURCE_CHAT_LOG
    assert job.covered_from_turn_id == 0
    assert job.covered_until_turn_id == 0
    assert json.loads(job.source_turn_ids_json) == []
    assert json.loads(job.source_ids_json) == [
        row.id for row in rows[:8]
    ]
    assert job.fallback_summary_id is None


def test_group_rollup_contract_change_resets_automatic_recovery_budget(
    db_session,
    monkeypatch,
):
    from app.session_memory.group_rollup import discover_group_summary_jobs
    from app.session_memory.summary_contract import (
        SESSION_SUMMARY_CONTRACT_VERSION,
    )

    _small_thresholds(monkeypatch)
    started_at = datetime(2026, 7, 31, 12, 15, 0)
    rows = _add_group_logs(
        db_session,
        session_id="group_contract_recovery",
        count=14,
        started_at=started_at,
    )
    checked_at = rows[-1].created_at + timedelta(seconds=61)
    first = discover_group_summary_jobs(db_session, now=checked_at)
    failed = db_session.query(SessionSummaryJob).one()
    failed.status = "failed"
    failed.meta_json = json.dumps({
        "summary_contract_version": SESSION_SUMMARY_CONTRACT_VERSION - 1,
        "summary_prompt_fingerprint": "旧合同",
        "auto_recovery_count": 1,
    }, ensure_ascii=False)
    db_session.flush()

    recovered = discover_group_summary_jobs(db_session, now=checked_at)
    jobs = db_session.query(SessionSummaryJob).order_by(
        SessionSummaryJob.id.asc()
    ).all()

    assert first["created"] == 1
    assert recovered["created"] == 1
    assert len(jobs) == 2
    assert jobs[-1].status == "pending"
    recovered_meta = json.loads(jobs[-1].meta_json)
    assert recovered_meta["summary_contract_version"] == (
        SESSION_SUMMARY_CONTRACT_VERSION
    )
    assert recovered_meta["auto_recovery_count"] == 1

    jobs[-1].status = "failed"
    db_session.flush()
    blocked = discover_group_summary_jobs(db_session, now=checked_at)

    assert blocked["failed_same_coverage"] == 1
    assert db_session.query(SessionSummaryJob).count() == 2


def test_group_rollup_discovery_accepts_canonical_group_identity(
    db_session,
    monkeypatch,
):
    from app.session_memory.group_rollup import discover_group_summary_jobs

    _small_thresholds(monkeypatch)
    started_at = datetime(2026, 7, 31, 12, 30, 0)
    rows = _add_group_logs(
        db_session,
        session_id="qq:canonical-group:group",
        count=14,
        started_at=started_at,
    )
    db_session.add(
        ChatLog(
            user_id="private_user",
            session_id="private_user",
            role="user",
            content="更新更晚的私聊消息",
            processed=1,
            created_at=rows[-1].created_at + timedelta(seconds=1),
        )
    )
    db_session.flush()

    result = discover_group_summary_jobs(
        db_session,
        now=rows[-1].created_at + timedelta(seconds=61),
        limit=1,
    )

    job = db_session.query(SessionSummaryJob).one()
    assert result["scanned"] == 1
    assert result["created"] == 1
    assert job.session_id == "qq:canonical-group:group"


def test_group_rollup_below_threshold_creates_no_job(
    db_session,
    monkeypatch,
):
    from app.session_memory.group_rollup import discover_group_summary_jobs

    _small_thresholds(monkeypatch)
    started_at = datetime(2026, 7, 31, 13, 0, 0)
    rows = _add_group_logs(
        db_session,
        session_id="group_small",
        count=10,
        started_at=started_at,
    )

    result = discover_group_summary_jobs(
        db_session,
        now=rows[-1].created_at + timedelta(minutes=5),
    )

    assert result["created"] == 0
    assert result["below_threshold"] == 1
    assert db_session.query(SessionSummaryJob).count() == 0


def test_group_llm_summary_uses_chatlog_cursor_without_legacy_turn_ids(
    db_session,
):
    from app.session_memory.jobs import enqueue_session_summary_job
    from app.session_memory.llm_summarizer import save_llm_session_summary
    from app.session_memory.rolling_summary import SUMMARY_SOURCE_CHAT_LOG

    started_at = datetime(2026, 7, 31, 14, 0, 0)
    rows = _add_group_logs(
        db_session,
        session_id="group_finalize",
        count=4,
        started_at=started_at,
    )
    previous = RollingSessionSummary(
        session_id="group_finalize",
        user_id="group_finalize",
        chat_type="group",
        status="active",
        summary_kind="llm_episode",
        summary_text="前两条摘要",
        source_type=SUMMARY_SOURCE_CHAT_LOG,
        covered_from_source_id=rows[0].id,
        covered_until_source_id=rows[1].id,
        source_ids_json=json.dumps([rows[0].id, rows[1].id]),
    )
    db_session.add(previous)
    db_session.flush()
    job, created = enqueue_session_summary_job(
        db_session,
        session_id="group_finalize",
        user_id="group_finalize",
        chat_type="group",
        pending_turns=rows[2:],
        previous_summary=previous,
        fallback_summary=None,
        source_type=SUMMARY_SOURCE_CHAT_LOG,
        recent_raw_turn_ids=[],
    )

    summary = save_llm_session_summary(
        db_session,
        job=job,
        payload={
            "summary": "四条群消息的累计摘要",
            "open_threads": [],
            "decisions": [],
            "important_user_requests": [],
            "resolved_items": [],
            "artifacts": [],
            "participants": ["甲", "乙"],
            "keywords": ["群消息"],
            "quality": {"score": 0.9, "issues": []},
        },
        source_turns=rows[2:],
        model="test-model",
    )

    assert created is True
    assert summary.source_type == SUMMARY_SOURCE_CHAT_LOG
    assert summary.covered_from_source_id == rows[0].id
    assert summary.covered_until_source_id == rows[-1].id
    assert json.loads(summary.source_ids_json) == [row.id for row in rows]
    assert summary.covered_from_turn_id == 0
    assert summary.covered_until_turn_id == 0
    assert json.loads(summary.source_turn_ids_json) == []
    assert previous.status == "archived"
