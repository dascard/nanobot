from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError

from core.agent_collaboration import (
    AgentCollaborationAccessDenied,
    AgentCollaborationConflict,
    AgentCollaborationNotFound,
)
from core.agent_collaboration.service import SqlAlchemyAgentCollaborationService
from core.agent_orchestration import (
    AgentModelClass,
    AgentOrchestrationBudget,
    AgentOrchestrationPlan,
    AgentPlanGovernanceService,
    AgentRoleDefinition,
    AgentRoleKind,
    AgentTaskAuthority,
    AgentTaskCompletionCondition,
    AgentTaskDefinition,
    AgentTaskInputBinding,
    AgentTaskPurpose,
    AgentTaskRuntimeBudget,
    AgentTaskRuntimePolicy,
    JsonObjectContract,
    SqlAlchemyAgentPlanRepository,
)
from core.agent_runtime import (
    RuntimeActor,
    RuntimeActorType,
    RuntimeOwnerType,
    RuntimePrincipal,
    RuntimeRunIdentity,
)
from core.db.models.agent_collaboration import (
    AgentCollaborationBoardRow,
    AgentCollaborationEventRow,
)
from core.db.models.agent_orchestration import AgentOrchestrationCheckpointRow
from core.db.models.durable_task import RunTaskControl
from core.db.session import session_factory_from_session
from core.schema_migrations import (
    _agent_collaboration_v1,
    _agent_orchestration_governance_v1,
    _run_durable_task_v1,
)


NOW = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}-{self.value}"


def _owner(owner_id: str = "group-42") -> RuntimePrincipal:
    return RuntimePrincipal("qq", RuntimeOwnerType.GROUP, owner_id)


def _identity(owner_id: str = "group-42") -> RuntimeRunIdentity:
    return RuntimeRunIdentity(
        run_id=f"run-collaboration-{owner_id}",
        turn_id=f"turn-collaboration-{owner_id}",
        correlation_id=f"trace-collaboration-{owner_id}",
        actor=RuntimeActor(RuntimeActorType.USER, "coordinator-user"),
        owner=_owner(owner_id),
    )


def _policy(
    purpose: AgentTaskPurpose,
    model_class: AgentModelClass,
    route_id: str,
) -> AgentTaskRuntimePolicy:
    return AgentTaskRuntimePolicy(
        purpose=purpose,
        model_class=model_class,
        model_route_id=route_id,
        model_route_sha256=("a" if route_id == "economy" else "b") * 64,
        authority=AgentTaskAuthority(),
        budget=AgentTaskRuntimeBudget(
            model_call_limit=1,
            token_limit=100,
            cost_limit_microunits=100,
            tool_call_limit=0,
            time_limit_ms=30_000,
        ),
    )


def _plan() -> AgentOrchestrationPlan:
    research = AgentTaskDefinition(
        task_id="research",
        role_id="worker",
        description="调查冻结主题",
        dependencies=(),
        input_contract=JsonObjectContract(required_keys=("topic",)),
        input_bindings=(AgentTaskInputBinding("topic", "topic"),),
        output_contract=JsonObjectContract(required_keys=("finding",)),
        completion=AgentTaskCompletionCondition(
            required_data_keys=("finding",),
        ),
        timeout_ms=30_000,
        runtime_policy=_policy(
            AgentTaskPurpose.EXPLORE,
            AgentModelClass.ECONOMY,
            "economy",
        ),
    )
    aggregate = AgentTaskDefinition(
        task_id="aggregate",
        role_id="aggregator",
        description="汇总已审批结果",
        dependencies=("research",),
        input_contract=JsonObjectContract(required_keys=("finding",)),
        input_bindings=(
            AgentTaskInputBinding(
                "finding",
                "finding",
                source_task_id="research",
            ),
        ),
        output_contract=JsonObjectContract(required_keys=("answer",)),
        completion=AgentTaskCompletionCondition(required_data_keys=("answer",)),
        timeout_ms=30_000,
        runtime_policy=_policy(
            AgentTaskPurpose.AGGREGATE,
            AgentModelClass.QUALITY,
            "quality",
        ),
    )
    return AgentOrchestrationPlan(
        plan_id="collaboration-plan",
        revision=1,
        roles=(
            AgentRoleDefinition(
                "coordinator",
                AgentRoleKind.COORDINATOR,
                "批准并冻结协作计划",
            ),
            AgentRoleDefinition("worker", AgentRoleKind.WORKER, "执行调查"),
            AgentRoleDefinition(
                "aggregator",
                AgentRoleKind.AGGREGATOR,
                "汇总交付",
            ),
        ),
        tasks=(research, aggregate),
        root_input_contract=JsonObjectContract(required_keys=("topic",)),
        aggregation_task_id="aggregate",
        budget=AgentOrchestrationBudget(
            max_tasks=2,
            max_concurrency=1,
            max_model_calls=2,
            max_tokens=200,
            max_cost_microunits=200,
            max_elapsed_ms=60_000,
            max_output_bytes=512 * 1024,
            max_checkpoints=2,
            max_tool_calls=0,
        ),
    )


def _freeze_plan(db_session):
    ids = _Ids()
    governance = AgentPlanGovernanceService(
        SqlAlchemyAgentPlanRepository(db_session),
        now=lambda: NOW,
        id_factory=ids,
    )
    plan = _plan()
    identity = _identity()
    preview = governance.preview(
        plan,
        identity=identity,
        proposed_by="coordinator-user",
    )
    approved = governance.approve(
        plan_id=plan.plan_id,
        revision=plan.revision,
        plan_sha256=plan.content_sha256,
        owner=identity.owner,
        approved_by="human-reviewer",
        expected_event_sequence=preview.latest_event_sequence,
    )
    assert approved.approval is not None
    frozen = governance.freeze(
        plan_id=plan.plan_id,
        revision=plan.revision,
        plan_sha256=plan.content_sha256,
        approval_id=approved.approval.approval_id,
        owner=identity.owner,
        frozen_by="orchestration-host",
        expected_event_sequence=approved.latest_event_sequence,
    )
    db_session.commit()
    return plan, identity, frozen, ids


def _service(db_session, ids: _Ids) -> SqlAlchemyAgentCollaborationService:
    return SqlAlchemyAgentCollaborationService(
        db_session,
        session_factory=session_factory_from_session(db_session),
        now=lambda: NOW,
        id_factory=ids,
        enforce_feature=False,
    )


def _output(data: dict[str, str]) -> dict[str, object]:
    return {
        "status": "success",
        "summary": "任务已完成",
        "next_actions": [],
        "artifacts": [],
        "data": data,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 10,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
            "cost_microunits": 10,
        },
        "model_calls": 1,
        "tool_calls": 0,
    }


@pytest.mark.asyncio
async def test_task_board_runs_agent_handoff_human_review_and_checkpoints(db_session):
    plan, identity, _frozen, ids = _freeze_plan(db_session)
    service = _service(db_session, ids)
    board = service.create_board(
        identity=identity,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        root_input={"topic": "任务板真实链路"},
        source_type="group",
        source_id="group-42",
        created_by="coordinator-user",
        idempotency_key="create-board-1",
    )
    duplicate = service.create_board(
        identity=identity,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        root_input={"topic": "任务板真实链路"},
        source_type="group",
        source_id="group-42",
        created_by="coordinator-user",
        idempotency_key="create-board-1",
    )
    assert duplicate.board_id == board.board_id
    assert db_session.query(RunTaskControl).count() == 2
    db_session.commit()

    with pytest.raises(
        AgentCollaborationAccessDenied,
        match="collaboration_invitation_required",
    ):
        service.claim_task(
            board_id=board.board_id,
            task_id="research",
            actor_id="agent_link:worker",
            idempotency_key="claim-before-invite",
        )
    invitation = service.invite_agent(
        board_id=board.board_id,
        task_id="research",
        owner=identity.owner,
        invited_by="coordinator-user",
        target_actor_id="agent_link:worker",
        idempotency_key="invite-research",
    )
    assert invitation["task"]["input"] == {"topic": "任务板真实链路"}
    claim = service.claim_task(
        board_id=board.board_id,
        task_id="research",
        actor_id="agent_link:worker",
        idempotency_key="claim-research",
    )
    replayed_claim = service.claim_task(
        board_id=board.board_id,
        task_id="research",
        actor_id="agent_link:worker",
        idempotency_key="claim-research",
    )
    assert replayed_claim.lease.token == claim.lease.token
    with pytest.raises(
        AgentCollaborationConflict,
        match="collaboration_dependency_not_approved",
    ):
        service.claim_task(
            board_id=board.board_id,
            task_id="aggregate",
            actor_id="agent_link:aggregator",
            idempotency_key="claim-aggregate-too-early",
            require_invitation=False,
        )
    db_session.commit()
    with pytest.raises(
        AgentCollaborationConflict,
        match="collaboration_lease_lost",
    ):
        service.submit_delivery(
            board_id=board.board_id,
            task_id="research",
            actor_id="agent_link:worker",
            lease_token="wrong-token",
            lease_generation=claim.lease.generation,
            attempt_no=claim.lease.attempt_no,
            output_payload=_output({"finding": "可信调查"}),
            idempotency_key="bad-delivery",
        )
    db_session.rollback()
    delivery = service.submit_delivery(
        board_id=board.board_id,
        task_id="research",
        actor_id="agent_link:worker",
        lease_token=claim.lease.token,
        lease_generation=claim.lease.generation,
        attempt_no=claim.lease.attempt_no,
        output_payload=_output({"finding": "可信调查"}),
        idempotency_key="deliver-research",
    )
    replayed_delivery = service.submit_delivery(
        board_id=board.board_id,
        task_id="research",
        actor_id="agent_link:worker",
        lease_token=claim.lease.token,
        lease_generation=claim.lease.generation,
        attempt_no=claim.lease.attempt_no,
        output_payload=_output({"finding": "可信调查"}),
        idempotency_key="deliver-research",
    )
    assert replayed_delivery["duplicate"] is True
    db_session.commit()
    persisted_event_payloads = "\n".join(
        row.payload_json
        for row in db_session.query(AgentCollaborationEventRow).all()
    )
    assert claim.lease.token not in persisted_event_payloads

    review = service.review_delivery(
        board_id=board.board_id,
        delivery_id=delivery["delivery_id"],
        expected_delivery_sha256=delivery["delivery_sha256"],
        owner=identity.owner,
        reviewer_id="human-reviewer",
        approved=True,
        reason_code="",
        idempotency_key="approve-research",
    )
    assert review["approved"] is True
    db_session.commit()
    first_checkpoint = await service.advance_checkpoints(
        board_id=board.board_id,
        owner=identity.owner,
    )
    assert first_checkpoint is not None
    assert first_checkpoint.sequence == 1
    assert first_checkpoint.outputs["research"].usage.total_tokens == 100

    service.invite_agent(
        board_id=board.board_id,
        task_id="aggregate",
        owner=identity.owner,
        invited_by="coordinator-user",
        target_actor_id="agent_link:aggregator",
        idempotency_key="invite-aggregate",
    )
    aggregate_claim = service.claim_task(
        board_id=board.board_id,
        task_id="aggregate",
        actor_id="agent_link:aggregator",
        idempotency_key="claim-aggregate",
    )
    assert aggregate_claim.task_payload["input"] == {"finding": "可信调查"}
    aggregate_delivery = service.submit_delivery(
        board_id=board.board_id,
        task_id="aggregate",
        actor_id="agent_link:aggregator",
        lease_token=aggregate_claim.lease.token,
        lease_generation=aggregate_claim.lease.generation,
        attempt_no=aggregate_claim.lease.attempt_no,
        output_payload=_output({"answer": "最终答案"}),
        idempotency_key="deliver-aggregate",
    )
    db_session.commit()
    service.review_delivery(
        board_id=board.board_id,
        delivery_id=aggregate_delivery["delivery_id"],
        expected_delivery_sha256=aggregate_delivery["delivery_sha256"],
        owner=identity.owner,
        reviewer_id="human-reviewer",
        approved=True,
        reason_code="human_approved",
        idempotency_key="approve-aggregate",
    )
    db_session.commit()
    final_checkpoint = await service.advance_checkpoints(
        board_id=board.board_id,
        owner=identity.owner,
    )
    assert final_checkpoint is not None
    assert final_checkpoint.sequence == 2
    assert final_checkpoint.cumulative_usage.usage.total_tokens == 200
    view = service.board_view(board_id=board.board_id, owner=identity.owner)
    assert view["status"] == "succeeded"
    assert view["aggregate_output"]["data"] == {"answer": "最终答案"}
    assert view["latest_checkpoint_sequence"] == 2
    assert db_session.query(AgentOrchestrationCheckpointRow).count() == 2


@pytest.mark.asyncio
async def test_group_status_command_uses_canonical_message_principal(
    db_session,
    monkeypatch,
):
    from api.group_message_routes import GroupMessageRequest
    from app.group_ingress.service import GroupIngressService

    plan, identity, _frozen, ids = _freeze_plan(db_session)
    service = SqlAlchemyAgentCollaborationService(
        db_session,
        session_factory=session_factory_from_session(db_session),
        now=lambda: datetime.now(timezone.utc),
        id_factory=ids,
        enforce_feature=False,
    )
    board = service.create_board(
        identity=identity,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        root_input={"topic": "群聊 canonical owner"},
        source_type="group",
        source_id="group-42",
        created_by="coordinator-user",
        idempotency_key="create-board-group-status",
    )
    db_session.commit()

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            raise AssertionError("严格协作状态命令不应进入 Timing 或模型")

        def note_bot_replied(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(
        "core.agent_collaboration.is_agent_collaboration_requested",
        lambda: True,
    )
    monkeypatch.setattr(
        "core.agent_collaboration.feature.is_agent_collaboration_requested",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.group_ingress.service.is_super_user_id",
        lambda _sender_id: True,
    )
    monkeypatch.setattr("core.timing_runtime.get_group_runtime", Runtime)
    request = GroupMessageRequest(
        group_id="qq:group-42:group",
        sender_id="coordinator-user",
        sender_name="协调者",
        message=f"@agent 状态 {board.board_id}",
        message_id="group-status-canonical-owner",
        client_meta={"platform": "qq", "chat_type": "group"},
    )

    result = await GroupIngressService(db=db_session).handle(request)

    assert result["action"] == "continue"
    assert f"任务板 {board.board_id}：ready" in result["reply"]
    assert "research=ready" in result["reply"]


def test_task_board_rejects_owner_conflict_expired_invite_and_human_rejection(
    db_session,
):
    plan, identity, _frozen, ids = _freeze_plan(db_session)
    service = _service(db_session, ids)
    board = service.create_board(
        identity=identity,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        root_input={"topic": "拒绝链路"},
        source_type="group",
        source_id="group-42",
        created_by="coordinator-user",
        idempotency_key="create-board-reject",
    )
    db_session.commit()
    with pytest.raises(AgentCollaborationNotFound):
        service.board_view(board_id=board.board_id, owner=_owner("other-group"))

    expired_service = SqlAlchemyAgentCollaborationService(
        db_session,
        session_factory=session_factory_from_session(db_session),
        now=lambda: NOW + timedelta(seconds=2),
        id_factory=ids,
        enforce_feature=False,
    )
    service.invite_agent(
        board_id=board.board_id,
        task_id="research",
        owner=identity.owner,
        invited_by="coordinator-user",
        target_actor_id="agent_link:worker",
        idempotency_key="short-invite",
        ttl_seconds=1,
    )
    db_session.commit()
    with pytest.raises(AgentCollaborationAccessDenied):
        expired_service.claim_task(
            board_id=board.board_id,
            task_id="research",
            actor_id="agent_link:worker",
            idempotency_key="expired-claim",
        )
    with pytest.raises(AgentCollaborationAccessDenied):
        expired_service.agent_status(
            board_id=board.board_id,
            actor_id="agent_link:worker",
        )

    service.invite_agent(
        board_id=board.board_id,
        task_id="research",
        owner=identity.owner,
        invited_by="coordinator-user",
        target_actor_id="agent_link:worker",
        idempotency_key="fresh-invite",
    )
    claim = service.claim_task(
        board_id=board.board_id,
        task_id="research",
        actor_id="agent_link:worker",
        idempotency_key="fresh-claim",
    )
    delivery = service.submit_delivery(
        board_id=board.board_id,
        task_id="research",
        actor_id="agent_link:worker",
        lease_token=claim.lease.token,
        lease_generation=claim.lease.generation,
        attempt_no=claim.lease.attempt_no,
        output_payload=_output({"finding": "需要退回"}),
        idempotency_key="fresh-delivery",
    )
    db_session.commit()
    service.review_delivery(
        board_id=board.board_id,
        delivery_id=delivery["delivery_id"],
        expected_delivery_sha256=delivery["delivery_sha256"],
        owner=identity.owner,
        reviewer_id="human-reviewer",
        approved=False,
        reason_code="evidence_insufficient",
        idempotency_key="reject-delivery",
    )
    db_session.commit()
    assert service.board_view(
        board_id=board.board_id,
        owner=identity.owner,
    )["status"] == "blocked"
    with pytest.raises(AgentCollaborationConflict, match="局部修复计划"):
        service.claim_task(
            board_id=board.board_id,
            task_id="aggregate",
            actor_id="agent_link:aggregator",
            idempotency_key="claim-after-reject",
            require_invitation=False,
        )


def test_agent_collaboration_migration_installs_append_only_guards():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _agent_orchestration_governance_v1(connection, engine, None)
        _run_durable_task_v1(connection, engine, None)
        _agent_collaboration_v1(connection, engine, None)
        trigger_names = {
            row[0]
            for row in connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND name LIKE 'trg_agent_collaboration_%'"
            ))
        }
        assert trigger_names == {
            "trg_agent_collaboration_boards_no_delete",
            "trg_agent_collaboration_boards_no_update",
            "trg_agent_collaboration_events_no_delete",
            "trg_agent_collaboration_events_no_update",
        }
        connection.execute(AgentCollaborationBoardRow.__table__.insert().values(
            board_id="board-trigger",
            owner_platform="qq",
            owner_type="group",
            owner_id="group-trigger",
            plan_id="missing-plan",
            plan_revision=1,
            plan_sha256="a" * 64,
            approval_id="approval-trigger",
            freeze_id="freeze-trigger",
            run_id="run-trigger",
            turn_id="turn-trigger",
            correlation_id="trace-trigger",
            actor_type="user",
            actor_id="actor-trigger",
            parent_actor_id="",
            root_input_json="{}",
            root_input_sha256=(
                "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
            ),
            root_input_size_bytes=2,
            source_type="group",
            source_id="group-trigger",
            created_by="actor-trigger",
            idempotency_key_sha256="b" * 64,
            request_sha256="c" * 64,
            created_at=NOW.replace(tzinfo=None),
            expires_at=(NOW + timedelta(minutes=1)).replace(tzinfo=None),
        ))
        with pytest.raises(DatabaseError, match="append_only"):
            connection.execute(text(
                "UPDATE agent_collaboration_boards "
                "SET source_id = 'changed' WHERE board_id = 'board-trigger'"
            ))


def test_admin_collaboration_api_runs_real_claim_delivery_and_review(
    db_session,
    client,
    monkeypatch,
):
    plan, identity, _frozen, _ids = _freeze_plan(db_session)
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(
        "core.agent_collaboration.feature.is_agent_collaboration_requested",
        lambda: True,
    )
    auth = {"Authorization": "Bearer test-token"}
    owner = {
        "platform": identity.owner.platform,
        "owner_type": identity.owner.owner_type.value,
        "owner_id": identity.owner.owner_id,
    }
    identity_payload = {
        "run_id": identity.run_id,
        "turn_id": identity.turn_id,
        "correlation_id": identity.correlation_id,
        "actor": {
            "actor_type": identity.actor.actor_type.value,
            "actor_id": identity.actor.actor_id,
            "parent_actor_id": identity.actor.parent_actor_id,
        },
        "owner": owner,
    }

    created = client.post(
        "/api/v1/admin/collaboration/boards",
        headers={**auth, "Idempotency-Key": "api-create-board"},
        json={
            "identity": identity_payload,
            "plan_id": plan.plan_id,
            "plan_revision": plan.revision,
            "root_input": {"topic": "管理 API 真实链路"},
            "source_type": "admin",
            "source_id": "admin-collaboration-test",
        },
    )
    assert created.status_code == 200, created.text
    board_id = created.json()["board_id"]
    initial_research = next(
        item
        for item in created.json()["tasks"]
        if item["task_id"] == "research"
    )
    assert initial_research["state"] == "ready"

    claimed = client.post(
        f"/api/v1/admin/collaboration/boards/{board_id}/tasks/research/claim",
        headers={**auth, "Idempotency-Key": "api-claim-research"},
        json={"actor_id": "admin-agent:researcher"},
    )
    assert claimed.status_code == 200, claimed.text
    lease = claimed.json()["lease"]
    assert lease["token"]

    delivered = client.post(
        f"/api/v1/admin/collaboration/boards/{board_id}/tasks/research/deliver",
        headers={**auth, "Idempotency-Key": "api-deliver-research"},
        json={
            "actor_id": "admin-agent:researcher",
            "lease_token": lease["token"],
            "lease_generation": lease["generation"],
            "attempt_no": lease["attempt_no"],
            "output": _output({"finding": "管理 API 已完成真实交付"}),
        },
    )
    assert delivered.status_code == 200, delivered.text
    delivery = delivered.json()
    assert delivery["status"] == "waiting_approval"

    reviewed = client.post(
        (
            f"/api/v1/admin/collaboration/boards/{board_id}/deliveries/"
            f"{delivery['delivery_id']}/review"
        ),
        headers={**auth, "Idempotency-Key": "api-review-research"},
        json={
            "owner": owner,
            "expected_delivery_sha256": delivery["delivery_sha256"],
            "approved": True,
            "reason_code": "verified",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["approved"] is True
    assert reviewed.json()["checkpoint_sequence"] == 1
    assert reviewed.json()["checkpoint_pending"] is False

    view = client.get(
        f"/api/v1/admin/collaboration/boards/{board_id}",
        headers=auth,
        params={
            "owner_platform": owner["platform"],
            "owner_type": owner["owner_type"],
            "owner_id": owner["owner_id"],
        },
    )
    assert view.status_code == 200, view.text
    assert view.json()["latest_checkpoint_sequence"] == 1
    research = next(
        item for item in view.json()["tasks"] if item["task_id"] == "research"
    )
    assert research["state"] == "approved"
    assert research["responsible_actor_id"] == "admin-agent:researcher"


def test_admin_collaboration_api_is_authenticated_and_default_off(
    client,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(
        "core.agent_collaboration.feature.is_agent_collaboration_requested",
        lambda: False,
    )
    path = "/api/v1/admin/collaboration/plans/missing/revisions/1"
    params = {
        "owner_platform": "qq",
        "owner_type": "group",
        "owner_id": "group-42",
    }

    assert client.get(path, params=params).status_code == 401
    disabled = client.get(
        path,
        params=params,
        headers={"Authorization": "Bearer test-token"},
    )
    assert disabled.status_code == 409
    assert disabled.json()["detail"]["code"] == "agent_collaboration_disabled"


@pytest.mark.asyncio
async def test_admin_review_keeps_committed_approval_when_checkpoint_is_pending(
    db_session,
    monkeypatch,
):
    from api.admin import collaboration_routes as routes

    events: list[str] = []
    original_commit = db_session.commit

    def recording_commit():
        events.append("commit")
        original_commit()

    class Service:
        def __init__(self, *_args, **_kwargs):
            return None

        def review_delivery(self, **_kwargs):
            events.append("review")
            return {"approved": True, "duplicate": False}

        async def advance_checkpoints(self, **_kwargs):
            events.append("checkpoint")
            raise RuntimeError("checkpoint unavailable")

    monkeypatch.setattr(db_session, "commit", recording_commit)
    monkeypatch.setattr(
        routes,
        "SqlAlchemyAgentCollaborationService",
        Service,
    )
    body = routes.ReviewBody(
        owner=routes.OwnerBody(
            platform="qq",
            owner_type="group",
            owner_id="group-42",
        ),
        expected_delivery_sha256="a" * 64,
        approved=True,
        reason_code="verified",
    )

    result = await routes.review_delivery(
        board_id="board-one",
        delivery_id="delivery-one",
        body=body,
        idempotency_key="review-one",
        db=db_session,
        _auth="admin",
    )

    assert events == ["review", "commit", "checkpoint"]
    assert result["approved"] is True
    assert result["checkpoint_id"] == ""
    assert result["checkpoint_sequence"] == 0
    assert result["checkpoint_pending"] is True
