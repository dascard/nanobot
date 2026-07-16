import json
from datetime import datetime, timedelta

import pytest

from core.database import (
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
    ProactiveOutreachLog,
)
from core.proactive_research import ResearchResult, ResearchSource


_UNTRUSTED_ERRORS = (
    pytest.param(
        "Authorization: Bearer AUTH-REVIEW-SECRET",
        "AUTH-REVIEW-SECRET",
        id="authorization",
    ),
    pytest.param(
        "https://provider.test/fail?token=URL-REVIEW-SECRET",
        "URL-REVIEW-SECRET",
        id="credential-url",
    ),
    pytest.param(
        "上游异常 RANDOM-REVIEW-SECRET",
        "RANDOM-REVIEW-SECRET",
        id="unkeyed-random",
    ),
    pytest.param(
        "BODY-REVIEW-SECRET" + "x" * 5000,
        "BODY-REVIEW-SECRET",
        id="oversized-body",
    ),
)


def _research_judge(now):
    return {
        "should_reach_out": True,
        "reason": "近期话题适合补充一份资料",
        "next_check_at": (now + timedelta(hours=4)).isoformat(),
        "next_intent": "后续问问资料是否有用",
        "outreach_kind": "research",
        "research_query": "调查可靠的 Agent 长期记忆设计",
        "error_type": None,
    }


def _seed_delivery_control(db_session):
    db_session.add(OutboundDeliveryControl(
        source_type="proactive_outreach",
        mode="legacy_direct",
        cutover_epoch=0,
        effective_from=datetime(1970, 1, 1),
        protocol_version=2,
        writer_version=0,
    ))
    db_session.commit()


@pytest.mark.asyncio
async def test_research_output_and_outbox_are_committed_before_delivery(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_delivery_control(db_session)
    now = datetime(2026, 7, 10, 12, 0, 0)
    source = ResearchSource(
        tool_call_id="tool-1",
        title="来源一",
        url="https://example.test/one",
    )
    source_two = ResearchSource(
        tool_call_id="tool-2",
        title="来源二",
        url="https://example.test/two",
    )
    research_calls = []

    async def research_fn(request):
        research_calls.append(request)
        return ResearchResult(
            request_id=request.request_id,
            trace_id="trace-research",
            status="draft_ready",
            draft="研究正文\n\n来源（本次真实检索）：...",
            sources=(source, source_two),
        )

    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {
            "user_id": "research-user",
            "recent_threads": ["Agent 长期记忆"],
            "recent_messages": [],
        },
    )
    original_delivery = proactive_outreach._deliver_legacy_outreach_leaf
    observed_before_delivery = []

    async def delivery_spy(**kwargs):
        db_session.expire_all()
        row = db_session.query(ProactiveOutreachLog).one()
        run = db_session.query(OutboundRun).one()
        attempt = db_session.query(OutboundGenerationAttempt).one()
        outbox = db_session.query(OutboundDeliveryOutbox).one()
        observed_before_delivery.append({
            "row_status": row.status,
            "message": row.message,
            "grounding": json.loads(row.grounding_json),
            "run_status": run.status,
            "attempt_status": attempt.status,
            "outbox_status": outbox.status,
        })
        return await original_delivery(**kwargs)

    monkeypatch.setattr(
        proactive_outreach,
        "_deliver_legacy_outreach_leaf",
        delivery_spy,
    )
    published = []

    async def publisher(target_type, target_id, message):
        published.append((target_type, target_id, message))
        return True

    result = await proactive_outreach.run_outreach_once(
        "research-user",
        db=db_session,
        now=now,
        max_silence_min=999999,
        judge_fn=lambda *_args, **_kwargs: _research_judge(now),
        generator_fn=lambda *_args, **_kwargs: pytest.fail("研究任务不应调用普通 Generator"),
        research_fn=research_fn,
        publisher=publisher,
    )

    assert result["status"] == "sent"
    assert research_calls[0].query == "调查可靠的 Agent 长期记忆设计"
    assert observed_before_delivery[0]["row_status"] == "queued"
    assert observed_before_delivery[0]["message"].startswith("研究正文")
    assert observed_before_delivery[0]["grounding"]["research"][
        "trace_id"
    ] == "trace-research"
    assert len(
        observed_before_delivery[0]["grounding"]["research"]["sources"]
    ) == 2
    assert observed_before_delivery[0]["run_status"] == "queued"
    assert observed_before_delivery[0]["attempt_status"] == "succeeded"
    assert observed_before_delivery[0]["outbox_status"] == "pending"
    assert published == [("private", "research-user", "研究正文\n\n来源（本次真实检索）：...")]


@pytest.mark.asyncio
async def test_blocked_research_stays_pending_and_never_publishes(monkeypatch, db_session):
    from core import proactive_outreach

    _seed_delivery_control(db_session)
    now = datetime(2026, 7, 10, 12, 0, 0)
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {"user_id": "research-user", "recent_messages": []},
    )

    async def research_fn(request):
        return ResearchResult(
            request_id=request.request_id,
            trace_id="trace-blocked",
            status="blocked",
            reason_code="insufficient_sources",
            sources=(),
        )

    async def publisher(*_args):
        pytest.fail("来源不足的研究任务不可发布")

    result = await proactive_outreach.run_outreach_once(
        "research-user",
        db=db_session,
        now=now,
        max_silence_min=999999,
        judge_fn=lambda *_args, **_kwargs: _research_judge(now),
        research_fn=research_fn,
        publisher=publisher,
    )

    assert result["status"] == "research_blocked"
    assert result["reason_code"] == "insufficient_sources"
    failed_row = db_session.get(
        ProactiveOutreachLog,
        result["failed_log_id"],
    )
    pending_row = db_session.get(ProactiveOutreachLog, result["log_id"])
    assert failed_row is not None
    assert pending_row is not None
    assert db_session.query(ProactiveOutreachLog).count() == 2
    assert failed_row.status == "failed"
    assert failed_row.outbound_run_id == result["run_id"]
    assert pending_row.status == "pending"
    assert pending_row.outbound_run_id is None
    failed_grounding = json.loads(failed_row.grounding_json)
    pending_grounding = json.loads(pending_row.grounding_json)
    assert failed_grounding["research"]["status"] == "blocked"
    assert failed_grounding["research"]["reason_code"] == "insufficient_sources"
    assert pending_grounding["research"]["status"] == "blocked"
    run = db_session.get(OutboundRun, result["run_id"])
    attempt = db_session.query(OutboundGenerationAttempt).one()
    assert run is not None
    assert run.status == "failed"
    assert run.failure_type == "insufficient_sources"
    assert attempt.status == "failed"
    assert attempt.error_type == "insufficient_sources"
    assert db_session.query(OutboundDeliveryOutbox).count() == 0


@pytest.mark.asyncio
async def test_unknown_research_reason_code_is_normalized_before_persistence(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_delivery_control(db_session)
    now = datetime(2026, 7, 10, 12, 0, 0)
    untrusted_reason_code = "attacker_controlled_research_type"
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {
            "user_id": "research-unknown-reason",
            "recent_messages": [],
        },
    )

    async def research_fn(request):
        return ResearchResult(
            request_id=request.request_id,
            trace_id="trace-unknown-reason",
            status="blocked",
            reason_code=untrusted_reason_code,
        )

    result = await proactive_outreach.run_outreach_once(
        "research-unknown-reason",
        db=db_session,
        now=now,
        max_silence_min=999999,
        judge_fn=lambda *_args, **_kwargs: _research_judge(now),
        research_fn=research_fn,
    )

    failed_row = db_session.get(ProactiveOutreachLog, result["failed_log_id"])
    pending_row = db_session.get(ProactiveOutreachLog, result["log_id"])
    run = db_session.get(OutboundRun, result["run_id"])
    attempt = db_session.query(OutboundGenerationAttempt).one()
    combined = json.dumps(
        {
            "result": result,
            "run": {
                "failure_type": run.failure_type,
                "failure_summary": run.failure_summary,
            },
            "attempt": {
                "error_type": attempt.error_type,
                "error_summary": attempt.error_summary,
            },
            "failed_grounding": json.loads(failed_row.grounding_json),
            "pending_grounding": json.loads(pending_row.grounding_json),
        },
        ensure_ascii=False,
    )

    assert result["status"] == "research_blocked"
    assert result["reason_code"] == "contract_error"
    assert run.failure_type == "contract_error"
    assert run.failure_summary == "主动外呼正文不符合生成契约"
    assert attempt.error_type == "contract_error"
    assert attempt.error_summary == "主动外呼正文不符合生成契约"
    assert untrusted_reason_code not in combined


@pytest.mark.asyncio
@pytest.mark.parametrize("error_text,secret", _UNTRUSTED_ERRORS)
async def test_judge_runtime_error_uses_fixed_diagnostic_everywhere(
    error_text,
    secret,
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_delivery_control(db_session)
    now = datetime(2026, 7, 10, 12, 0, 0)
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {
            "user_id": "judge-runtime-error",
            "recent_messages": [],
        },
    )

    def failed_judge(*_args, **_kwargs):
        raise RuntimeError(error_text)

    result = await proactive_outreach.run_outreach_once(
        "judge-runtime-error",
        db=db_session,
        now=now,
        max_silence_min=999999,
        judge_fn=failed_judge,
    )

    row = db_session.query(ProactiveOutreachLog).one()
    combined = json.dumps(
        {
            "result": result,
            "judge_reason": row.judge_reason,
            "grounding": json.loads(row.grounding_json),
        },
        ensure_ascii=False,
    )

    assert result["status"] == "judge_error"
    assert result["error_type"] == "model_error"
    assert result["reason"] == "主动外呼 Judge 调用失败"
    assert secret not in combined


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_text,secret",
    [
        pytest.param(
            "Authorization: Bearer AUTH-RESEARCH-SECRET",
            "AUTH-RESEARCH-SECRET",
            id="authorization",
        ),
        pytest.param(
            "https://provider.test/fail?token=URL-RESEARCH-SECRET",
            "URL-RESEARCH-SECRET",
            id="credential-url",
        ),
        pytest.param(
            "研究异常 RANDOM-RESEARCH-SECRET",
            "RANDOM-RESEARCH-SECRET",
            id="unkeyed-random",
        ),
        pytest.param(
            "BODY-RESEARCH-SECRET" + "x" * 5000,
            "BODY-RESEARCH-SECRET",
            id="oversized-body",
        ),
    ],
)
async def test_research_runtime_error_uses_fixed_diagnostic_everywhere(
    monkeypatch,
    db_session,
    error_text,
    secret,
):
    from core import proactive_outreach

    _seed_delivery_control(db_session)
    now = datetime(2026, 7, 10, 12, 0, 0)
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {
            "user_id": "research-runtime-error",
            "recent_messages": [],
        },
    )

    async def research_fn(_request):
        raise RuntimeError(error_text)

    result = await proactive_outreach.run_outreach_once(
        "research-runtime-error",
        db=db_session,
        now=now,
        max_silence_min=999999,
        judge_fn=lambda *_args, **_kwargs: _research_judge(now),
        research_fn=research_fn,
    )

    failed_row = db_session.get(ProactiveOutreachLog, result["failed_log_id"])
    pending_row = db_session.get(ProactiveOutreachLog, result["log_id"])
    run = db_session.get(OutboundRun, result["run_id"])
    attempt = db_session.query(OutboundGenerationAttempt).one()
    combined = json.dumps(
        {
            "result": result,
            "run": {
                "failure_type": run.failure_type,
                "failure_summary": run.failure_summary,
            },
            "attempt": {
                "error_type": attempt.error_type,
                "error_summary": attempt.error_summary,
            },
            "failed_grounding": json.loads(failed_row.grounding_json),
            "pending_grounding": json.loads(pending_row.grounding_json),
        },
        ensure_ascii=False,
    )

    assert result["status"] == "research_blocked"
    assert result["reason_code"] == "runtime_error"
    assert run.failure_type == "generation_error"
    assert run.failure_summary == "主动外呼正文生成失败"
    assert attempt.error_type == "generation_error"
    assert attempt.error_summary == "主动外呼正文生成失败"
    assert secret not in combined


@pytest.mark.asyncio
async def test_blocked_research_payload_replaces_untrusted_error_before_persistence(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    _seed_delivery_control(db_session)
    now = datetime(2026, 7, 10, 12, 0, 0)
    secret = "UNKEYED-RESEARCH-PAYLOAD-SECRET"
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {
            "user_id": "research-payload-error",
            "recent_messages": [],
        },
    )

    async def research_fn(request):
        return ResearchResult(
            request_id=request.request_id,
            trace_id="trace-runtime-error",
            status="blocked",
            reason_code="runtime_error",
            error=f"研究内部失败 {secret}",
        )

    result = await proactive_outreach.run_outreach_once(
        "research-payload-error",
        db=db_session,
        now=now,
        max_silence_min=999999,
        judge_fn=lambda *_args, **_kwargs: _research_judge(now),
        research_fn=research_fn,
    )

    failed_row = db_session.get(ProactiveOutreachLog, result["failed_log_id"])
    pending_row = db_session.get(ProactiveOutreachLog, result["log_id"])
    failed_grounding = json.loads(failed_row.grounding_json)
    pending_grounding = json.loads(pending_row.grounding_json)
    assert result["status"] == "research_blocked"
    assert result["reason_code"] == "runtime_error"
    assert failed_grounding["research"]["error"] == "主动外呼正文生成失败"
    assert pending_grounding["research"]["error"] == "主动外呼正文生成失败"
    assert secret not in json.dumps(
        {
            "result": result,
            "failed": failed_grounding,
            "pending": pending_grounding,
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_empty_ready_research_draft_never_creates_sticky_candidate(monkeypatch, db_session):
    from core import proactive_outreach

    _seed_delivery_control(db_session)
    now = datetime(2026, 7, 10, 12, 0, 0)
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {"user_id": "research-user", "recent_messages": []},
    )

    async def research_fn(request):
        return ResearchResult(
            request_id=request.request_id,
            trace_id="trace-empty",
            status="draft_ready",
            draft="<think>只有推理</think>",
            sources=(
                ResearchSource("tool-1", "来源一", "https://example.test/one"),
                ResearchSource("tool-2", "来源二", "https://example.test/two"),
            ),
        )

    result = await proactive_outreach.run_outreach_once(
        "research-user",
        db=db_session,
        now=now,
        max_silence_min=999999,
        judge_fn=lambda *_args, **_kwargs: _research_judge(now),
        research_fn=research_fn,
    )

    assert result["status"] == "generation_error"
    assert result["error_type"] == "contract_error"
    rows = db_session.query(ProactiveOutreachLog).all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].message == ""
    assert rows[0].outbound_run_id == result["run_id"]
    grounding = json.loads(rows[0].grounding_json)
    assert grounding["generation_error"]["error_type"] == "contract_error"
    assert grounding["generation_error"]["reason"] == "主动研究返回空草稿"
    run = db_session.get(OutboundRun, result["run_id"])
    attempt = db_session.query(OutboundGenerationAttempt).one()
    assert run is not None
    assert run.status == "failed"
    assert run.failure_type == "contract_error"
    assert "主动研究返回空草稿" in run.failure_summary
    assert attempt.status == "failed"
    assert attempt.error_type == "contract_error"
    assert db_session.query(OutboundDeliveryOutbox).count() == 0
    assert db_session.query(ProactiveOutreachLog).filter(
        ProactiveOutreachLog.status.in_(("candidate", "pending", "sending", "sent"))
    ).count() == 0
