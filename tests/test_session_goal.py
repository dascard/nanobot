"""Session Goal 与服务端 Plan Mode 合同测试。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import sessionmaker

from core.agent_runtime.request_scope import runtime_context_scope
from core.db.models.session_goal import (
    SessionGoalEventRow,
    SessionGoalRow,
    SessionPlanAssetRow,
)
from core.session_goal import (
    SessionGoalBudget,
    SessionGoalConflictError,
    SessionGoalMode,
    SessionGoalNotFoundError,
    SessionGoalPrincipal,
    SessionGoalService,
    SessionGoalStatus,
    SessionGoalValidationError,
)
from tests.async_helpers import run_async


def _principal() -> SessionGoalPrincipal:
    return SessionGoalPrincipal("qq", "user", "goal-user", "private-goal-user")


def _create_goal(db_session):
    snapshot = SessionGoalService(db_session).create_goal(
        principal=_principal(),
        objective="完成可验证的长任务",
        completion_criteria=["实现完成", "测试为零失败"],
        budget=SessionGoalBudget(max_model_steps=32, max_tool_calls=64),
        actor_id="goal-user",
    )
    db_session.commit()
    return snapshot


def test_session_goal_requires_frozen_approval_before_execution(db_session):
    service = SessionGoalService(db_session)
    goal = service.create_goal(
        principal=_principal(),
        objective="完成可验证的长任务",
        completion_criteria=["实现完成", "测试为零失败"],
        budget=SessionGoalBudget(max_model_steps=32, max_tool_calls=64),
        actor_id="goal-user",
    )
    assert goal.status is SessionGoalStatus.PLANNING
    assert goal.mode is SessionGoalMode.PLAN

    with pytest.raises(SessionGoalConflictError, match="已批准"):
        service.start_execution(
            goal_id=goal.goal_id,
            principal=_principal(),
            expected_version=goal.version,
            actor_id="goal-user",
        )

    goal = service.write_plan(
        goal_id=goal.goal_id,
        principal=_principal(),
        content="# 实施计划\n\n1. 实现\n2. 验证",
        expected_version=goal.version,
        actor_id="goal-user",
    )
    goal = service.request_approval(
        goal_id=goal.goal_id,
        principal=_principal(),
        expected_version=goal.version,
        actor_id="goal-user",
    )
    with pytest.raises(SessionGoalConflictError, match="摘要已变化"):
        service.approve(
            goal_id=goal.goal_id,
            principal=_principal(),
            expected_version=goal.version,
            expected_plan_revision=goal.latest_plan_revision,
            expected_plan_sha256="0" * 64,
            approver_id="reviewer",
        )

    goal = service.approve(
        goal_id=goal.goal_id,
        principal=_principal(),
        expected_version=goal.version,
        expected_plan_revision=goal.latest_plan_revision,
        expected_plan_sha256=goal.latest_plan_sha256,
        approver_id="reviewer",
    )
    assert goal.status is SessionGoalStatus.APPROVED
    assert goal.mode is SessionGoalMode.PLAN
    with pytest.raises(SessionGoalConflictError, match="不允许写入计划"):
        service.write_plan(
            goal_id=goal.goal_id,
            principal=_principal(),
            content="批准后偷偷修改",
            expected_version=goal.version,
            actor_id="goal-user",
        )

    goal = service.start_execution(
        goal_id=goal.goal_id,
        principal=_principal(),
        expected_version=goal.version,
        actor_id="reviewer",
    )
    assert goal.status is SessionGoalStatus.EXECUTING
    assert goal.mode is SessionGoalMode.EXECUTE
    policy = service.runtime_policy(goal_id=goal.goal_id, principal=_principal())
    assert policy.plan is not None
    assert policy.plan.content_sha256 == goal.approved_plan_sha256
    assert '"mode":"execute"' in policy.runtime_context()

    goal = service.finish(
        goal_id=goal.goal_id,
        principal=_principal(),
        expected_version=goal.version,
        actor_id="reviewer",
        status=SessionGoalStatus.COMPLETED,
        reason="完成条件均已核验",
    )
    assert goal.status is SessionGoalStatus.COMPLETED
    with pytest.raises(SessionGoalConflictError, match="终态"):
        service.runtime_policy(goal_id=goal.goal_id, principal=_principal())

    assert [
        row.event_kind
        for row in db_session.query(SessionGoalEventRow)
        .filter(SessionGoalEventRow.goal_id == goal.goal_id)
        .order_by(SessionGoalEventRow.goal_version)
        .all()
    ] == [
        "created",
        "plan_written",
        "approval_requested",
        "approved",
        "execution_started",
        "completed",
    ]
    assert db_session.query(SessionPlanAssetRow).filter_by(
        goal_id=goal.goal_id
    ).count() == 1


def test_session_goal_enforces_owner_and_optimistic_version(db_session):
    goal = _create_goal(db_session)
    service = SessionGoalService(db_session)

    with pytest.raises(SessionGoalNotFoundError):
        service.get_goal(
            goal.goal_id,
            SessionGoalPrincipal("qq", "user", "other-user", "private-goal-user"),
        )
    with pytest.raises(SessionGoalConflictError, match="版本冲突"):
        service.write_plan(
            goal_id=goal.goal_id,
            principal=_principal(),
            content="# 计划",
            expected_version=goal.version + 1,
            actor_id="goal-user",
        )

    with pytest.raises(SessionGoalValidationError, match="actor_id 无效"):
        service.write_plan(
            goal_id=goal.goal_id,
            principal=_principal(),
            content="# 不应写入",
            expected_version=goal.version,
            actor_id="",
        )
    db_session.commit()
    unchanged = service.get_goal(goal.goal_id, _principal())
    assert unchanged.version == goal.version
    assert unchanged.latest_plan_revision == 0


def test_session_goal_runtime_rejects_projection_proof_drift(db_session):
    from sqlalchemy import update

    goal = _create_goal(db_session)
    service = SessionGoalService(db_session)
    goal = service.write_plan(
        goal_id=goal.goal_id,
        principal=_principal(),
        content="# 受证明保护的计划",
        expected_version=goal.version,
        actor_id="goal-user",
    )
    db_session.commit()
    db_session.execute(
        update(SessionGoalRow)
        .where(SessionGoalRow.goal_id == goal.goal_id)
        .values(latest_plan_sha256="0" * 64)
    )
    db_session.commit()

    with pytest.raises(SessionGoalValidationError, match="投影证明不一致"):
        service.runtime_policy(goal_id=goal.goal_id, principal=_principal())


def test_session_goal_runtime_context_is_bounded_but_plan_asset_is_complete(
    db_session,
):
    from core.token_utils import estimate_tokens

    service = SessionGoalService(db_session)
    goal = service.create_goal(
        principal=_principal(),
        objective="目" * 1_500,
        completion_criteria=[f"条件{index}" + "验" * 496 for index in range(8)],
        budget=SessionGoalBudget(),
        actor_id="goal-user",
    )
    full_plan = "计" * 8_000
    goal = service.write_plan(
        goal_id=goal.goal_id,
        principal=_principal(),
        content=full_plan,
        expected_version=goal.version,
        actor_id="goal-user",
    )

    policy = service.runtime_policy(goal_id=goal.goal_id, principal=_principal())
    assert policy.plan is not None
    assert policy.plan.content == full_plan
    runtime_context = policy.runtime_context()
    assert '"truncated":true' in runtime_context
    assert estimate_tokens(runtime_context) < 8_500


def test_plan_tools_use_only_trusted_runtime_identity(db_session, monkeypatch):
    from app.tool_services.session_plan import (
        execute_session_plan_read,
        execute_session_plan_write,
    )
    from core import database

    goal = _create_goal(db_session)
    tool_sessions = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(database, "SessionLocal", tool_sessions)
    context = {
        "platform": "qq",
        "owner_type": "user",
        "owner_id": "goal-user",
        "session_id": "private-goal-user",
        "actor_id": "goal-user",
        "run_id": "run-session-goal-test",
        "session_goal_id": goal.goal_id,
        "session_goal_mode": "plan",
    }

    with runtime_context_scope(context):
        written = run_async(execute_session_plan_write({
            "content": "# 工具生成计划\n\n- 验证",
            "expected_version": goal.version,
            "goal_id": "模型伪造字段不会进入 schema",
        }))
        assert written.success is True
        payload = json.loads(str(written.output))
        assert payload["plan_revision"] == 1

        read = run_async(execute_session_plan_read({"revision": 0}))
        assert read.success is True
        read_payload = json.loads(str(read.output))
        assert read_payload["goal_id"] == goal.goal_id
        assert read_payload["plan"]["content"] == "# 工具生成计划\n\n- 验证"

    db_session.expire_all()
    service = SessionGoalService(db_session)
    goal = service.get_goal(goal.goal_id, _principal())
    goal = service.write_plan(
        goal_id=goal.goal_id,
        principal=_principal(),
        content="# 最终批准计划\n\n- 只执行此版本",
        expected_version=goal.version,
        actor_id="goal-user",
    )
    goal = service.request_approval(
        goal_id=goal.goal_id,
        principal=_principal(),
        expected_version=goal.version,
        actor_id="goal-user",
    )
    goal = service.approve(
        goal_id=goal.goal_id,
        principal=_principal(),
        expected_version=goal.version,
        expected_plan_revision=goal.latest_plan_revision,
        expected_plan_sha256=goal.latest_plan_sha256,
        approver_id="reviewer",
    )
    goal = service.start_execution(
        goal_id=goal.goal_id,
        principal=_principal(),
        expected_version=goal.version,
        actor_id="reviewer",
    )
    db_session.commit()

    with runtime_context_scope({**context, "session_goal_mode": "execute"}):
        stale = run_async(execute_session_plan_read({"revision": 1}))
        assert stale.success is False
        assert "只能读取已批准" in str(stale.error)
        approved = run_async(execute_session_plan_read({"revision": 0}))
        assert approved.success is True
        approved_payload = json.loads(str(approved.output))
        assert approved_payload["plan"]["revision"] == 2

    with runtime_context_scope({**context, "owner_id": "other-user"}):
        denied = run_async(execute_session_plan_read({"revision": 0}))
    assert denied.success is False
    assert "owner 不匹配" in str(denied.error)


def test_session_goal_control_api_keeps_approval_and_mode_switch_separate(client):
    principal = {
        "platform": "qq",
        "owner_type": "user",
        "owner_id": "api-goal-user",
        "session_id": "private-api-goal-user",
    }
    created = client.post("/api/v1/session-goals", json={
        "principal": principal,
        "objective": "通过 API 执行长任务",
        "completion_criteria": ["计划获批", "执行完成"],
        "budget": {"max_model_steps": 8, "max_tool_calls": 16},
        "actor_id": "api-goal-user",
    })
    assert created.status_code == 200
    goal = created.json()

    written = client.put(
        f"/api/v1/session-goals/{goal['goal_id']}/plan",
        json={
            "principal": principal,
            "expected_version": goal["version"],
            "actor_id": "api-goal-user",
            "content": "# API 计划\n\n1. 执行",
        },
    )
    assert written.status_code == 200
    goal = written.json()
    requested = client.post(
        f"/api/v1/session-goals/{goal['goal_id']}/request-approval",
        json={
            "principal": principal,
            "expected_version": goal["version"],
            "actor_id": "api-goal-user",
        },
    )
    assert requested.status_code == 200
    goal = requested.json()
    approved = client.post(
        f"/api/v1/session-goals/{goal['goal_id']}/approve",
        json={
            "principal": principal,
            "expected_version": goal["version"],
            "actor_id": "human-reviewer",
            "expected_plan_revision": goal["latest_plan_revision"],
            "expected_plan_sha256": goal["latest_plan_sha256"],
        },
    )
    assert approved.status_code == 200
    goal = approved.json()
    assert goal["status"] == "approved"
    assert goal["mode"] == "plan"

    started = client.post(
        f"/api/v1/session-goals/{goal['goal_id']}/start-execution",
        json={
            "principal": principal,
            "expected_version": goal["version"],
            "actor_id": "human-reviewer",
        },
    )
    assert started.status_code == 200
    goal = started.json()
    assert goal["status"] == "executing"
    assert goal["mode"] == "execute"


def test_build_tool_plan_only_exposes_plan_assets_when_server_enables_mode(
    monkeypatch,
):
    import core.tool_plan as tool_plan_module

    monkeypatch.setattr(
        tool_plan_module,
        "resolve_effective_tools",
        lambda **_kwargs: (
            {
                "reply": True,
                "no_reply": True,
                "web_search": True,
                "session_plan_read": False,
                "session_plan_write": False,
            },
            {},
        ),
    )
    plan = tool_plan_module.build_tool_plan(
        session_goal_mode="plan",
        session_plan_writable=True,
    )
    restricted = tool_plan_module.restrict_tool_plan(
        plan,
        {"reply", "no_reply", "session_plan_read", "session_plan_write"},
    )
    assert restricted.executable_tool_names == frozenset({
        "reply",
        "no_reply",
        "session_plan_read",
        "session_plan_write",
    })
    assert restricted.can_execute("web_search") is False

    execution = tool_plan_module.build_tool_plan(
        session_goal_mode="execute",
        session_plan_writable=False,
    )
    assert execution.can_execute("web_search") is True
    assert execution.can_execute("session_plan_read") is True
    assert execution.can_execute("session_plan_write") is False


def test_session_goal_bridge_binding_preserves_source_hard_disable(db_session):
    from nanobot_kt.session_goal_runtime import (
        build_session_goal_bridge_binding,
    )

    binding = build_session_goal_bridge_binding(
        db=db_session,
        metadata={"project_context": "已有项目事实"},
        platform="qq",
        runtime_chat_type="private",
        is_group=False,
        group_id="",
        user_id="goal-user",
        session_id="private-goal-user",
        runtime_preset="full",
        disabled_tool_names=["web_search", ""],
    )

    assert binding.policy is None
    assert binding.project_context == "已有项目事实"
    assert binding.plan_ref is None
    assert binding.runtime_attributes == ()
    assert binding.tool_plan.can_execute("web_search") is False
    assert binding.tool_plan.disabled_reason("web_search") == (
        "来源上下文禁用(防递归)"
    )
