from datetime import datetime, timedelta

import pytest

from core.database import ProactiveOutreachLog
from core.proactive_research import ResearchResult, ResearchSource


def _message_judge(now):
    return {
        "should_reach_out": True,
        "reason": "有具体话题",
        "next_check_at": (now + timedelta(hours=2)).isoformat(),
        "next_intent": "继续跟进",
        "outreach_kind": "message",
        "research_query": "",
        "error_type": None,
    }


@pytest.mark.asyncio
async def test_live_dry_run_returns_candidate_without_business_writes_or_publisher(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {"user_id": "dry-user", "recent_messages": []},
    )

    async def forbidden_publisher(*_args, **_kwargs):
        pytest.fail("dry-run 调用图不可到达 publisher")

    monkeypatch.setattr(proactive_outreach, "push_to_qq", forbidden_publisher)
    result = await proactive_outreach.run_outreach_dry_run_once(
        "dry-user",
        db=db_session,
        now=now,
        judge_fn=lambda *_args, **_kwargs: _message_judge(now),
        generator_fn=lambda *_args, **_kwargs: "这是一条只返回、不发送的候选消息。",
    )

    assert result["status"] == "candidate"
    assert result["would_publish"] is True
    assert result["message"] == "这是一条只返回、不发送的候选消息。"
    assert db_session.query(ProactiveOutreachLog).count() == 0


@pytest.mark.asyncio
async def test_live_research_dry_run_returns_verified_candidate_without_outreach_log(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    judge = _message_judge(now)
    judge.update({
        "outreach_kind": "research",
        "research_query": "调查 Agent 记忆",
    })
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {"user_id": "dry-user", "recent_messages": []},
    )

    async def research_fn(request):
        return ResearchResult(
            request_id=request.request_id,
            trace_id="trace-dry",
            status="draft_ready",
            draft="研究 dry-run 候选",
            sources=(
                ResearchSource("tool-1", "来源一", "https://example.test/one"),
                ResearchSource("tool-2", "来源二", "https://example.test/two"),
            ),
        )

    result = await proactive_outreach.run_outreach_dry_run_once(
        "dry-user",
        db=db_session,
        now=now,
        judge_fn=lambda *_args, **_kwargs: judge,
        research_fn=research_fn,
    )

    assert result["status"] == "candidate"
    assert result["research"]["trace_id"] == "trace-dry"
    assert len(result["research"]["sources"]) == 2
    assert db_session.query(ProactiveOutreachLog).count() == 0


@pytest.mark.asyncio
async def test_admin_scripted_simulation_uses_dedicated_safe_runner(monkeypatch):
    from api.admin import proactive_outreach_routes

    async def forbidden(*_args, **_kwargs):
        pytest.fail("scripted simulate 不可复用生产 run-once")

    monkeypatch.setattr(proactive_outreach_routes, "run_outreach_once", forbidden)
    monkeypatch.setattr(proactive_outreach_routes, "run_outreach_due_once", forbidden)

    result = await proactive_outreach_routes.proactive_outreach_simulate(
        proactive_outreach_routes.ProactiveSimulationRequest(mode="scripted"),
        _auth="admin",
    )

    assert result["ok"] is True
    assert result["mode"] == "scripted"
    assert result["report"]["passed"] is True
    assert result["report"]["metrics"]["external_push_count"] == 0


@pytest.mark.asyncio
async def test_admin_live_dry_run_reports_research_timeout_as_execution_failure(monkeypatch):
    from api.admin import proactive_outreach_routes

    async def fake_dry_run(user_id, **kwargs):
        assert user_id == "dry-user"
        return {
            "status": "research_blocked",
            "would_publish": False,
            "reason_code": "timeout",
        }

    monkeypatch.setattr(
        proactive_outreach_routes,
        "run_outreach_dry_run_once",
        fake_dry_run,
    )
    result = await proactive_outreach_routes.proactive_outreach_simulate(
        proactive_outreach_routes.ProactiveSimulationRequest(
            mode="live_dry_run",
            user_id="dry-user",
        ),
        db=object(),
        _auth="admin",
    )

    assert result["request_ok"] is True
    assert result["execution_ok"] is False
    assert result["candidate_available"] is False
    assert result["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dry_run_result", "candidate_available"),
    [
        (
            {
                "status": "candidate",
                "would_publish": True,
                "message": "  可发布候选  ",
            },
            True,
        ),
        ({"status": "no_candidate", "would_publish": False}, False),
        *[
            (
                {
                    "status": "research_blocked",
                    "would_publish": False,
                    "reason_code": reason_code,
                },
                False,
            )
            for reason_code in (
                "insufficient_sources",
                "empty_draft",
                "unverified_url",
                "draft_budget_too_small",
            )
        ],
    ],
)
async def test_admin_live_dry_run_accepts_only_normal_and_quality_block_results(
    monkeypatch,
    dry_run_result,
    candidate_available,
):
    from api.admin import proactive_outreach_routes

    async def fake_dry_run(*_args, **_kwargs):
        return dry_run_result

    monkeypatch.setattr(
        proactive_outreach_routes,
        "run_outreach_dry_run_once",
        fake_dry_run,
    )
    result = await proactive_outreach_routes.proactive_outreach_simulate(
        proactive_outreach_routes.ProactiveSimulationRequest(
            mode="live_dry_run",
            user_id="dry-user",
        ),
        db=object(),
        _auth="admin",
    )

    assert result["request_ok"] is True
    assert result["execution_ok"] is True
    assert result["candidate_available"] is candidate_available
    assert result["ok"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dry_run_result",
    [
        {"status": "judge_error", "would_publish": False},
        {"status": "generation_error", "would_publish": False},
        {"status": "lease_lost", "would_publish": False},
        {"status": "state_error", "would_publish": False},
        {"status": "", "would_publish": False},
        {"status": "unknown_status", "would_publish": False},
        *[
            {
                "status": "research_blocked",
                "would_publish": False,
                "reason_code": reason_code,
            }
            for reason_code in (
                "timeout",
                "runtime_error",
                "budget_exhausted",
                "budget_guard_unavailable",
                "tool_guard_unavailable",
                "runner_unavailable",
                "empty_query",
                "",
                "unknown_reason",
            )
        ],
    ],
)
async def test_admin_live_dry_run_rejects_failures_and_unknown_results(
    monkeypatch,
    dry_run_result,
):
    from api.admin import proactive_outreach_routes

    async def fake_dry_run(*_args, **_kwargs):
        return dry_run_result

    monkeypatch.setattr(
        proactive_outreach_routes,
        "run_outreach_dry_run_once",
        fake_dry_run,
    )
    result = await proactive_outreach_routes.proactive_outreach_simulate(
        proactive_outreach_routes.ProactiveSimulationRequest(
            mode="live_dry_run",
            user_id="dry-user",
        ),
        db=object(),
        _auth="admin",
    )

    assert result["request_ok"] is True
    assert result["execution_ok"] is False
    assert result["candidate_available"] is False
    assert result["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dry_run_result",
    [
        {"status": "candidate", "would_publish": False, "message": "候选"},
        {"status": "candidate", "would_publish": True, "message": ""},
        {"status": "candidate", "would_publish": True, "message": "  \n\t  "},
        {
            "status": "candidate",
            "would_publish": True,
            "message": "<think>只有推理，没有正文</think>",
        },
        {"status": "candidate", "message": "候选"},
    ],
)
async def test_admin_live_dry_run_rejects_malformed_candidate_contract(
    monkeypatch,
    dry_run_result,
):
    from api.admin import proactive_outreach_routes

    async def fake_dry_run(*_args, **_kwargs):
        return dry_run_result

    monkeypatch.setattr(
        proactive_outreach_routes,
        "run_outreach_dry_run_once",
        fake_dry_run,
    )
    result = await proactive_outreach_routes.proactive_outreach_simulate(
        proactive_outreach_routes.ProactiveSimulationRequest(
            mode="live_dry_run",
            user_id="dry-user",
        ),
        db=object(),
        _auth="admin",
    )

    assert result["execution_ok"] is False
    assert result["candidate_available"] is False
    assert result["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run_result", [None, [], "candidate", 123])
async def test_admin_live_dry_run_rejects_non_mapping_result(
    monkeypatch,
    dry_run_result,
):
    from api.admin import proactive_outreach_routes

    async def fake_dry_run(*_args, **_kwargs):
        return dry_run_result

    monkeypatch.setattr(
        proactive_outreach_routes,
        "run_outreach_dry_run_once",
        fake_dry_run,
    )
    result = await proactive_outreach_routes.proactive_outreach_simulate(
        proactive_outreach_routes.ProactiveSimulationRequest(
            mode="live_dry_run",
            user_id="dry-user",
        ),
        db=object(),
        _auth="admin",
    )

    assert result["request_ok"] is True
    assert result["execution_ok"] is False
    assert result["candidate_available"] is False
    assert result["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [123, {"text": "候选"}, ["候选"], True])
async def test_admin_live_dry_run_rejects_non_string_candidate_message(
    monkeypatch,
    message,
):
    from api.admin import proactive_outreach_routes

    async def fake_dry_run(*_args, **_kwargs):
        return {
            "status": "candidate",
            "would_publish": True,
            "message": message,
        }

    monkeypatch.setattr(
        proactive_outreach_routes,
        "run_outreach_dry_run_once",
        fake_dry_run,
    )
    result = await proactive_outreach_routes.proactive_outreach_simulate(
        proactive_outreach_routes.ProactiveSimulationRequest(
            mode="live_dry_run",
            user_id="dry-user",
        ),
        db=object(),
        _auth="admin",
    )

    assert result["request_ok"] is True
    assert result["execution_ok"] is False
    assert result["candidate_available"] is False
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_admin_live_dry_run_call_graph_cannot_reach_production_delivery(
    monkeypatch,
    db_session,
):
    from api.admin import proactive_outreach_routes
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)

    async def forbidden(*_args, **_kwargs):
        pytest.fail("live dry-run 调用图不可到达生产调度或发布")

    monkeypatch.setattr(proactive_outreach_routes, "run_outreach_once", forbidden)
    monkeypatch.setattr(proactive_outreach_routes, "run_outreach_due_once", forbidden)
    monkeypatch.setattr(proactive_outreach, "deliver_outreach_once", forbidden)
    monkeypatch.setattr(proactive_outreach, "push_to_qq", forbidden)
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {"user_id": "dry-user", "recent_messages": []},
    )
    monkeypatch.setattr(
        proactive_outreach,
        "judge_outreach",
        lambda *_args, **_kwargs: _message_judge(now),
    )
    monkeypatch.setattr(
        proactive_outreach,
        "generate_outreach_message",
        lambda *_args, **_kwargs: "管理端 live dry-run 候选",
    )

    result = await proactive_outreach_routes.proactive_outreach_simulate(
        proactive_outreach_routes.ProactiveSimulationRequest(
            mode="live_dry_run",
            user_id="dry-user",
        ),
        db=db_session,
        _auth="admin",
    )

    assert result["request_ok"] is True
    assert result["execution_ok"] is True
    assert result["candidate_available"] is True
    assert result["result"]["message"] == "管理端 live dry-run 候选"
    assert db_session.query(ProactiveOutreachLog).count() == 0
