"""Session Goal 与服务端 Plan Mode 合同测试。"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json

import pytest
from sqlalchemy.orm import sessionmaker

from core.agent_runtime.request_scope import runtime_context_scope
from core.db.models.admin import AdminAuditLog
from core.db.models.session_goal import (
    SessionGoalEventRow,
    SessionGoalRow,
    SessionPlanAssetRow,
)
from core.db.models.gateway_control import (
    GatewayRunBindingRow,
    GatewaySessionBindingRow,
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
from core.session_goal_control import (
    SessionGoalControlIdentityIntegrityError,
    resolve_session_goal_control_identity,
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


def _gateway_run(
    db_session,
    *,
    run_id: str,
    owner_id: str = "api-goal-user",
    actor_id: str = "api-goal-user",
    session_id: str = "private-api-goal-user",
) -> GatewayRunBindingRow:
    binding_id = hashlib.sha256(
        f"qq\0private\0{session_id}".encode("utf-8")
    ).hexdigest()
    session = db_session.get(GatewaySessionBindingRow, binding_id)
    if session is None:
        db_session.add(GatewaySessionBindingRow(
            binding_id=binding_id,
            transport="qq",
            owner_platform="qq",
            owner_type="user",
            owner_id=owner_id,
            actor_id=actor_id,
            chat_type="private",
            chat_stream_id=session_id,
            runtime_session_id=session_id,
            current_run_id=run_id,
            generation=1,
            created_at=datetime(2026, 8, 9, 12, 0, 0),
            updated_at=datetime(2026, 8, 9, 12, 0, 0),
        ))
    row = GatewayRunBindingRow(
        run_id=run_id,
        binding_id=binding_id,
        transport="qq",
        owner_platform="qq",
        owner_type="user",
        owner_id=owner_id,
        actor_id=actor_id,
        chat_type="private",
        chat_stream_id=session_id,
        runtime_session_id=session_id,
        admitted_at=datetime(2026, 8, 9, 12, 0, 0),
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_session_goal_api_uses_gateway_binding_and_rejects_self_asserted_identity(
    client,
    db_session,
):
    run = _gateway_run(db_session, run_id="run-session-goal-owner")

    created = client.post("/api/v1/session-goals", json={
        "gateway_run_id": run.run_id,
        "objective": "只接受受信 Gateway 身份",
        "completion_criteria": ["拒绝自报 owner", "记录真实 actor"],
        "budget": {"max_model_steps": 8, "max_tool_calls": 16},
    })

    assert created.status_code == 200, created.text
    goal = created.json()
    assert goal["principal"] == {
        "platform": "qq",
        "owner_type": "user",
        "owner_id": "api-goal-user",
        "session_id": "private-api-goal-user",
    }
    event = db_session.query(SessionGoalEventRow).filter_by(
        goal_id=goal["goal_id"],
        event_kind="created",
    ).one()
    assert event.actor_id == "api-goal-user"
    assert event.source_run_id == run.run_id

    forged = client.post("/api/v1/session-goals", json={
        "gateway_run_id": run.run_id,
        "principal": {
            "platform": "qq",
            "owner_type": "user",
            "owner_id": "forged-owner",
            "session_id": "forged-session",
        },
        "actor_id": "forged-actor",
        "objective": "不应创建",
        "completion_criteria": ["不应执行"],
    })
    assert forged.status_code == 422

    foreign = _gateway_run(
        db_session,
        run_id="run-session-goal-foreign",
        owner_id="foreign-owner",
        actor_id="foreign-owner",
        session_id="private-foreign-owner",
    )
    hidden = client.get(
        f"/api/v1/session-goals/{goal['goal_id']}",
        params={"gateway_run_id": foreign.run_id},
    )
    assert hidden.status_code == 404


def test_session_goal_api_requires_authenticated_control_scope(
    client,
    db_session,
):
    from api import routes
    from api.common_auth import AuthenticatedApiPrincipal

    run = _gateway_run(
        db_session,
        run_id="run-session-goal-without-control-scope",
    )
    client.app.dependency_overrides[routes.verify_token] = lambda: (
        AuthenticatedApiPrincipal(
            subject="read-only-service",
            kind="service",
            scopes=frozenset({"api:access"}),
        )
    )

    response = client.post("/api/v1/session-goals", json={
        "gateway_run_id": run.run_id,
        "objective": "无控制权限时拒绝创建",
        "completion_criteria": ["返回 403"],
    })

    assert response.status_code == 403
    assert response.json()["detail"] == "当前认证主体无 Session Goal 控制权限"


def test_session_goal_gateway_identity_fails_closed_for_private_actor_forgery(
    db_session,
):
    forged = _gateway_run(
        db_session,
        run_id="run-session-goal-forged-actor",
        owner_id="real-owner",
        actor_id="forged-actor",
        session_id="private-real-owner",
    )

    with pytest.raises(
        SessionGoalControlIdentityIntegrityError,
        match="actor",
    ):
        resolve_session_goal_control_identity(db_session, forged.run_id)


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


def test_session_goal_control_api_keeps_approval_and_mode_switch_separate(
    client,
    db_session,
):
    owner_run = _gateway_run(
        db_session,
        run_id="run-session-goal-api-owner",
    )
    created = client.post("/api/v1/session-goals", json={
        "gateway_run_id": owner_run.run_id,
        "objective": "通过 API 执行长任务",
        "completion_criteria": ["计划获批", "执行完成"],
        "budget": {"max_model_steps": 8, "max_tool_calls": 16},
    })
    assert created.status_code == 200
    goal = created.json()

    written = client.put(
        f"/api/v1/session-goals/{goal['goal_id']}/plan",
        json={
            "gateway_run_id": owner_run.run_id,
            "expected_version": goal["version"],
            "content": "# API 计划\n\n1. 执行",
        },
    )
    assert written.status_code == 200
    goal = written.json()
    requested = client.post(
        f"/api/v1/session-goals/{goal['goal_id']}/request-approval",
        json={
            "gateway_run_id": owner_run.run_id,
            "expected_version": goal["version"],
        },
    )
    assert requested.status_code == 200
    goal = requested.json()
    approved = client.post(
        f"/api/v1/session-goals/{goal['goal_id']}/approve",
        json={
            "gateway_run_id": owner_run.run_id,
            "expected_version": goal["version"],
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
            "gateway_run_id": owner_run.run_id,
            "expected_version": goal["version"],
        },
    )
    assert started.status_code == 200
    goal = started.json()
    assert goal["status"] == "executing"
    assert goal["mode"] == "execute"


def test_admin_session_goal_approval_is_explicit_scoped_and_audited(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "session-goal-admin-token",
    )
    owner_run = _gateway_run(
        db_session,
        run_id="run-session-goal-admin-owner",
    )
    created = client.post("/api/v1/session-goals", json={
        "gateway_run_id": owner_run.run_id,
        "objective": "由独立管理权限批准",
        "completion_criteria": ["批准有真实管理审计"],
    }).json()
    written = client.put(
        f"/api/v1/session-goals/{created['goal_id']}/plan",
        json={
            "gateway_run_id": owner_run.run_id,
            "expected_version": created["version"],
            "content": "# 待管理端批准的计划",
        },
    ).json()
    awaiting = client.post(
        f"/api/v1/session-goals/{created['goal_id']}/request-approval",
        json={
            "gateway_run_id": owner_run.run_id,
            "expected_version": written["version"],
        },
    ).json()
    headers = {"Authorization": "Bearer session-goal-admin-token"}

    forged = client.post(
        f"/api/v1/admin/session-goals/{created['goal_id']}/approve",
        headers=headers,
        json={
            "gateway_run_id": owner_run.run_id,
            "expected_version": awaiting["version"],
            "expected_plan_revision": awaiting["latest_plan_revision"],
            "expected_plan_sha256": awaiting["latest_plan_sha256"],
            "reason": "人工复核通过",
            "approver_id": "伪造批准者",
        },
    )
    assert forged.status_code == 422

    approved = client.post(
        f"/api/v1/admin/session-goals/{created['goal_id']}/approve",
        headers=headers,
        json={
            "gateway_run_id": owner_run.run_id,
            "expected_version": awaiting["version"],
            "expected_plan_revision": awaiting["latest_plan_revision"],
            "expected_plan_sha256": awaiting["latest_plan_sha256"],
            "reason": "人工复核通过",
        },
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_by"] == "admin:admin"
    audit = db_session.query(AdminAuditLog).filter_by(
        action="session_goal.admin_approve",
        target_id=created["goal_id"],
    ).one()
    detail = json.loads(audit.detail_json)
    assert detail["scope"] == "session_goal:approve"
    assert detail["gateway_run_id"] == owner_run.run_id
    assert detail["approver_id"] == "admin:admin"


def test_admin_session_goal_approval_rolls_back_when_audit_fails(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "session-goal-admin-token",
    )
    owner_run = _gateway_run(
        db_session,
        run_id="run-session-goal-admin-audit-failure",
    )
    created = client.post("/api/v1/session-goals", json={
        "gateway_run_id": owner_run.run_id,
        "objective": "审计失败不得批准",
        "completion_criteria": ["状态保持待批准"],
    }).json()
    written = client.put(
        f"/api/v1/session-goals/{created['goal_id']}/plan",
        json={
            "gateway_run_id": owner_run.run_id,
            "expected_version": created["version"],
            "content": "# 审计失败回滚计划",
        },
    ).json()
    awaiting = client.post(
        f"/api/v1/session-goals/{created['goal_id']}/request-approval",
        json={
            "gateway_run_id": owner_run.run_id,
            "expected_version": written["version"],
        },
    ).json()

    def reject_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        "api.admin.session_goal_routes.stage_audit_request",
        reject_audit,
    )
    response = client.post(
        f"/api/v1/admin/session-goals/{created['goal_id']}/approve",
        headers={
            "Authorization": "Bearer session-goal-admin-token"
        },
        json={
            "gateway_run_id": owner_run.run_id,
            "expected_version": awaiting["version"],
            "expected_plan_revision": awaiting["latest_plan_revision"],
            "expected_plan_sha256": awaiting["latest_plan_sha256"],
            "reason": "应被审计故障阻断",
        },
    )

    assert response.status_code == 500
    db_session.expire_all()
    row = db_session.get(SessionGoalRow, created["goal_id"])
    assert row is not None
    assert row.status == SessionGoalStatus.AWAITING_APPROVAL.value
    assert row.approved_by == ""
    assert db_session.query(AdminAuditLog).filter_by(
        action="session_goal.admin_approve",
        target_id=created["goal_id"],
    ).count() == 0


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
