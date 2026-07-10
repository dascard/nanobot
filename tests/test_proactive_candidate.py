from datetime import datetime, timedelta

import pytest

from core.proactive_research import ResearchResult, ResearchSource


def _judge(now, *, kind="message"):
    return {
        "should_reach_out": True,
        "reason": "有具体话题",
        "next_check_at": (now + timedelta(hours=2)).isoformat(),
        "next_intent": "继续跟进",
        "outreach_kind": kind,
        "research_query": "调查 Agent 记忆" if kind == "research" else "",
        "error_type": None,
    }


@pytest.mark.asyncio
async def test_candidate_evaluator_handles_message_and_fail_closed_paths():
    from core.proactive_candidate import evaluate_outreach_candidate

    now = datetime(2026, 7, 10, 12, 0, 0)
    grounding = {"user_id": "u1", "recent_messages": []}
    candidate = await evaluate_outreach_candidate(
        user_id="u1",
        request_id="candidate-1",
        grounding=grounding,
        now=now,
        judge_fn=lambda *_args, **_kwargs: _judge(now),
        generator_fn=lambda *_args, **_kwargs: "候选正文",
    )
    empty = await evaluate_outreach_candidate(
        user_id="u1",
        request_id="candidate-2",
        grounding=grounding,
        now=now,
        judge_fn=lambda *_args, **_kwargs: _judge(now),
        generator_fn=lambda *_args, **_kwargs: "<think>只有推理</think>",
    )
    judge_error = await evaluate_outreach_candidate(
        user_id="u1",
        request_id="candidate-3",
        grounding=grounding,
        now=now,
        judge_fn=lambda *_args, **_kwargs: {
            "should_reach_out": None,
            "reason": "截断",
            "next_check_at": now.isoformat(),
            "error_type": "model_truncated",
        },
        generator_fn=lambda *_args, **_kwargs: pytest.fail("Judge error 不得生成"),
    )

    assert candidate["status"] == "candidate"
    assert candidate["message"] == "候选正文"
    assert empty["status"] == "generation_error"
    assert judge_error["status"] == "judge_error"


@pytest.mark.asyncio
async def test_candidate_evaluator_requires_verified_research_result():
    from core.proactive_candidate import evaluate_outreach_candidate

    now = datetime(2026, 7, 10, 12, 0, 0)
    grounding = {"user_id": "u1", "recent_messages": []}

    async def ready(request):
        return ResearchResult(
            request_id=request.request_id,
            trace_id="trace-ready",
            status="draft_ready",
            draft="研究候选",
            sources=(
                ResearchSource("tool-1", "一", "https://example.test/one"),
                ResearchSource("tool-2", "二", "https://example.test/two"),
            ),
        )

    async def blocked(request):
        return ResearchResult(
            request_id=request.request_id,
            trace_id="trace-blocked",
            status="blocked",
            reason_code="insufficient_sources",
        )

    accepted = await evaluate_outreach_candidate(
        user_id="u1",
        request_id="research-1",
        grounding=grounding,
        now=now,
        judge_fn=lambda *_args, **_kwargs: _judge(now, kind="research"),
        generator_fn=lambda *_args, **_kwargs: pytest.fail("研究不走普通生成"),
        research_fn=ready,
    )
    rejected = await evaluate_outreach_candidate(
        user_id="u1",
        request_id="research-2",
        grounding=grounding,
        now=now,
        judge_fn=lambda *_args, **_kwargs: _judge(now, kind="research"),
        generator_fn=lambda *_args, **_kwargs: pytest.fail("研究不走普通生成"),
        research_fn=blocked,
    )

    assert accepted["status"] == "candidate"
    assert len(accepted["research"]["sources"]) == 2
    assert rejected["status"] == "research_blocked"
    assert rejected["reason_code"] == "insufficient_sources"


@pytest.mark.asyncio
async def test_production_outreach_uses_shared_candidate_evaluator(
    monkeypatch,
    db_session,
):
    from core import proactive_candidate, proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    grounding = {"user_id": "shared-user", "recent_messages": []}
    evaluator_calls = []
    published = []

    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: grounding,
    )

    async def evaluator(**kwargs):
        evaluator_calls.append(kwargs)
        return {
            "status": "candidate",
            "would_publish": True,
            "message": "共享内核生成的候选",
            "judge": _judge(now),
        }

    async def publisher(target_type, target_id, message):
        published.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(proactive_candidate, "evaluate_outreach_candidate", evaluator)
    result = await proactive_outreach.run_outreach_once(
        "shared-user",
        db=db_session,
        now=now,
        judge_fn=lambda *_args, **_kwargs: _judge(now),
        generator_fn=lambda *_args, **_kwargs: "旧分支生成的候选",
        publisher=publisher,
    )

    assert result["status"] == "sent"
    assert len(evaluator_calls) == 1
    assert evaluator_calls[0]["request_id"].startswith("outreach:shared-user:")
    assert published == [("private", "shared-user", "共享内核生成的候选")]


@pytest.mark.asyncio
async def test_research_candidate_rejects_url_reassembled_by_think_removal():
    from core.proactive_candidate import evaluate_outreach_candidate

    now = datetime(2026, 7, 10, 12, 0, 0)

    async def research_fn(request):
        return ResearchResult(
            request_id=request.request_id,
            trace_id="trace-think-url",
            status="draft_ready",
            draft=(
                "研究正文 https://<think>隐藏</think>evil.example/payload\n\n"
                "来源（本次真实检索）：\n"
                "1. 来源一\n   https://example.test/one\n"
                "2. 来源二\n   https://example.test/two"
            ),
            sources=(
                ResearchSource("tool-1", "来源一", "https://example.test/one"),
                ResearchSource("tool-2", "来源二", "https://example.test/two"),
            ),
        )

    result = await evaluate_outreach_candidate(
        user_id="candidate-think-url",
        request_id="candidate-think-url",
        grounding={"recent_messages": []},
        now=now,
        judge_fn=lambda *_args, **_kwargs: _judge(now, kind="research"),
        generator_fn=lambda *_args, **_kwargs: "不应调用",
        research_fn=research_fn,
    )

    assert result["status"] == "research_blocked"
    assert result["reason_code"] == "unverified_url"
    assert result["would_publish"] is False
