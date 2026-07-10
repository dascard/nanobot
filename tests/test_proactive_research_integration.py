import json
from datetime import datetime, timedelta

import pytest

from core.database import ProactiveOutreachLog
from core.proactive_research import ResearchResult, ResearchSource


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


@pytest.mark.asyncio
async def test_research_candidate_is_committed_before_independent_delivery(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

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
    original_deliver = proactive_outreach.deliver_outreach_once
    observed_candidate = []

    async def deliver_spy(**kwargs):
        row = db_session.get(ProactiveOutreachLog, kwargs["schedule_row_id"])
        observed_candidate.append((row.status, row.message, json.loads(row.grounding_json)))
        return await original_deliver(**kwargs)

    monkeypatch.setattr(proactive_outreach, "deliver_outreach_once", deliver_spy)
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
    assert observed_candidate[0][0] == "candidate"
    assert observed_candidate[0][1].startswith("研究正文")
    assert observed_candidate[0][2]["research"]["trace_id"] == "trace-research"
    assert len(observed_candidate[0][2]["research"]["sources"]) == 2
    assert published == [("private", "research-user", "研究正文\n\n来源（本次真实检索）：...")]


@pytest.mark.asyncio
async def test_blocked_research_stays_pending_and_never_publishes(monkeypatch, db_session):
    from core import proactive_outreach

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

    row = db_session.query(ProactiveOutreachLog).one()
    grounding = json.loads(row.grounding_json)
    assert result["status"] == "research_blocked"
    assert result["reason_code"] == "insufficient_sources"
    assert row.status == "pending"
    assert grounding["research"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_empty_ready_research_draft_never_creates_sticky_candidate(monkeypatch, db_session):
    from core import proactive_outreach

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
    rows = db_session.query(ProactiveOutreachLog).all()
    assert len(rows) == 1
    assert rows[0].status == "evaluation_error"
    assert rows[0].message == ""
    assert "generator:contract_error" in rows[0].judge_reason
    assert "主动研究返回空草稿" in rows[0].judge_reason
    assert db_session.query(ProactiveOutreachLog).filter(
        ProactiveOutreachLog.status.in_(("candidate", "pending", "sending", "sent"))
    ).count() == 0
