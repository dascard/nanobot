"""统一 Gateway 会话绑定、远程 Run 控制和模型切换测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError

from core.agent_runtime import RuntimeOwnerType, RuntimePrincipal
from core.database import AgentRun
from core.durable_tasks import RunTaskKind, SqlAlchemyRunTaskService
from core.gateway_control import (
    GatewayControlAccessDenied,
    GatewayControlConflict,
    GatewayControlPrincipal,
    GatewayModelProfileDescriptor,
    GatewayRunAdmission,
    SqlAlchemyGatewayControlService,
    active_gateway_model_profile,
    admit_gateway_run,
    build_gateway_session_binding_id,
)
from core.run_ledger.adapters import (
    run_accepted_event,
    run_status_changed_event,
    run_terminated_event,
)
from core.run_ledger.persistence import SqlAlchemyRunEventLedger


NOW = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
OWNER_ID = "agent_link:owner-one"
RUNTIME_SESSION_ID = "agent_link:runtime-session-one"
CHAT_STREAM_ID = "meapet:runtime-session-one:private"


class _StaticGatewayModelProfilePort:
    def __init__(
        self,
        *profiles: GatewayModelProfileDescriptor,
    ) -> None:
        self._profiles = tuple(profiles)

    def list_profiles(self) -> tuple[GatewayModelProfileDescriptor, ...]:
        return self._profiles


def _principal(
    *,
    owner_id: str = OWNER_ID,
    runtime_session_id: str = RUNTIME_SESSION_ID,
) -> GatewayControlPrincipal:
    return GatewayControlPrincipal(
        principal=RuntimePrincipal(
            "meapet",
            RuntimeOwnerType.USER,
            owner_id,
        ),
        actor_id=f"actor:{owner_id}",
        transport="agent_link",
        runtime_session_id=runtime_session_id,
    )


def _admit(
    db_session,
    run_id: str,
    *,
    status: str = "running",
) -> GatewayRunAdmission:
    accepted = run_accepted_event(
        run_id=run_id,
        trace_id=f"trace-{run_id}",
        session_id=RUNTIME_SESSION_ID,
        user_id=OWNER_ID,
        chat_type="private",
        group_id="",
        run_type="chat",
        prompt_mode="prompt",
        prompt_key="chat_private",
        prompt_sha256="",
        model="model-before-switch",
        input_value="测试 Gateway 控制",
        platform="meapet",
        request_id=f"request-{run_id}",
        occurred_at=NOW,
    )
    ledger = SqlAlchemyRunEventLedger(db_session)
    ledger.append(accepted, expected_sequence=1)
    ledger.append(
        run_status_changed_event(
            accepted_event=accepted,
            status="running",
            previous_status="accepted",
        ),
        expected_sequence=2,
    )
    if status in {"waiting_approval", "waiting_input"}:
        ledger.append(
            run_status_changed_event(
                accepted_event=accepted,
                status=status,
                previous_status="running",
            ),
            expected_sequence=3,
        )
    elif status in {
        "cancelled",
        "failed",
        "succeeded",
        "timed_out",
    }:
        ledger.append(
            run_terminated_event(
                run_id=run_id,
                trace_id=f"trace-{run_id}",
                session_id=RUNTIME_SESSION_ID,
                status=status,
                output_value="",
                error_value="",
                latency_ms=1,
                model="model-before-switch",
                occurred_at=NOW,
            ),
            expected_sequence=3,
        )
    db_session.add(AgentRun(
        run_id=run_id,
        trace_id=f"trace-{run_id}",
        session_id=RUNTIME_SESSION_ID,
        user_id=OWNER_ID,
        chat_type="private",
        run_type="chat",
        model="model-before-switch",
        status=status,
        started_at=NOW.replace(tzinfo=None),
    ))
    SqlAlchemyRunTaskService(db_session).admit_running(
        run_id=run_id,
        task_kind=RunTaskKind.CHAT,
        source_type="inbound_message",
        source_id=f"message-{run_id}",
        request_id=f"request-{run_id}",
        idempotency_key=f"request-{run_id}",
        owner="gateway-control-test",
        now=NOW,
    )
    admission = GatewayRunAdmission(
        binding_id=build_gateway_session_binding_id(
            "agent_link",
            CHAT_STREAM_ID,
        ),
        transport="agent_link",
        principal=_principal().principal,
        actor_id="agent_link:meapet:device-one",
        chat_type="private",
        chat_stream_id=CHAT_STREAM_ID,
        runtime_session_id=RUNTIME_SESSION_ID,
    )
    admit_gateway_run(
        db_session,
        run_id=run_id,
        admission=admission,
        admitted_at=NOW,
    )
    db_session.commit()
    return admission


def test_gateway_status_uses_authoritative_ledger_and_exact_acl(db_session):
    _admit(db_session, "run-gateway-status", status="waiting_approval")
    service = SqlAlchemyGatewayControlService(db_session)

    result = service.status("run-gateway-status", _principal())

    assert result["status"] == "waiting_approval"
    assert result["pending"] == "approval"
    assert result["pending_approval"] is True
    assert result["pending_question"] is False
    assert result["binding"]["transport"] == "agent_link"
    assert result["resume"]["mode"] == "channel_continuation"
    assert result["model"]["active_profile_id"] == ""
    with pytest.raises(GatewayControlAccessDenied):
        service.status(
            "run-gateway-status",
            _principal(owner_id="agent_link:other-owner"),
        )
    with pytest.raises(GatewayControlAccessDenied):
        service.status(
            "run-gateway-status",
            _principal(runtime_session_id="agent_link:other-session"),
        )
    assert (
        service.status(
            "run-gateway-status",
            GatewayControlPrincipal.admin("admin"),
        )["status"]
        == "waiting_approval"
    )


def test_gateway_admission_rejects_forged_string_metadata():
    from core.gateway_control import gateway_run_admission_from_metadata

    forged = {
        "message_contract_version": 1,
        "gateway_transport": "agent_link",
        "platform": "meapet",
        "principal_owner_type": "user",
        "principal_owner_id": OWNER_ID,
        "sender_id": "forged-actor",
        "chat_type": "private",
        "chat_stream_id": CHAT_STREAM_ID,
        "gateway_binding_id": build_gateway_session_binding_id(
            "agent_link",
            CHAT_STREAM_ID,
        ),
    }

    assert gateway_run_admission_from_metadata(
        metadata=forged,
        runtime_session_id=RUNTIME_SESSION_ID,
    ) is None


@pytest.mark.parametrize(
    ("status", "pending", "approval", "question"),
    [
        ("running", "none", False, False),
        ("waiting_approval", "approval", True, False),
        ("waiting_input", "question", False, True),
    ],
)
def test_gateway_pending_projection(
    db_session,
    status: str,
    pending: str,
    approval: bool,
    question: bool,
):
    run_id = f"run-gateway-pending-{status}"
    _admit(db_session, run_id, status=status)

    result = SqlAlchemyGatewayControlService(db_session).status(
        run_id,
        _principal(),
    )

    assert result["pending"] == pending
    assert result["pending_approval"] is approval
    assert result["pending_question"] is question


def test_gateway_stop_is_durable_idempotent_and_conflict_safe(db_session):
    _admit(db_session, "run-gateway-stop")
    service = SqlAlchemyGatewayControlService(db_session)

    first = service.stop(
        run_id="run-gateway-stop",
        request_id="stop-request-one",
        reason_code="user_requested",
        principal=_principal(),
    )
    replay = service.stop(
        run_id="run-gateway-stop",
        request_id="stop-request-one",
        reason_code="user_requested",
        principal=_principal(),
    )

    task = SqlAlchemyRunTaskService(db_session).get("run-gateway-stop")
    assert first["status"] == "accepted"
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert task is not None and task.cancel_requested_at is not None
    with pytest.raises(GatewayControlConflict):
        service.stop(
            run_id="run-gateway-stop",
            request_id="stop-request-one",
            reason_code="different_reason",
            principal=_principal(),
        )


def test_gateway_resume_only_authorizes_channel_continuation(db_session):
    _admit(db_session, "run-gateway-active")
    _admit(db_session, "run-gateway-question", status="waiting_input")
    _admit(db_session, "run-gateway-terminal", status="succeeded")
    service = SqlAlchemyGatewayControlService(db_session)

    with pytest.raises(GatewayControlConflict):
        service.authorize_resume(
            run_id="run-gateway-active",
            request_id="resume-active",
            principal=_principal(),
        )
    waiting = service.authorize_resume(
        run_id="run-gateway-question",
        request_id="resume-waiting",
        principal=_principal(),
    )
    terminal = service.authorize_resume(
        run_id="run-gateway-terminal",
        request_id="resume-terminal",
        principal=_principal(),
    )

    assert waiting["resume_mode"] == "channel_continuation"
    assert terminal["status"] == "authorized"


def test_model_switch_activates_only_when_next_run_is_admitted(db_session):
    admission = _admit(db_session, "run-before-model-switch")
    service = SqlAlchemyGatewayControlService(db_session)

    switched = service.switch_model(
        run_id="run-before-model-switch",
        request_id="model-switch-one",
        profile_id="quality-profile",
        expected_generation=1,
        available_profile_ids=["economy-profile", "quality-profile"],
        principal=_principal(),
    )

    assert switched["binding_generation"] == 2
    assert switched["effective_from_generation"] == 3
    assert active_gateway_model_profile(admission.binding_id) == ""
    current = service.status("run-before-model-switch", _principal())
    assert current["model"]["preferred_profile_id"] == "quality-profile"
    assert current["model"]["active_profile_id"] == ""

    _admit(db_session, "run-after-model-switch")

    next_run = service.status("run-after-model-switch", _principal())
    assert next_run["binding"]["generation"] == 3
    assert next_run["model"]["active_profile_id"] == "quality-profile"
    assert next_run["model"]["preferred_profile_id"] == ""
    assert active_gateway_model_profile(admission.binding_id) == (
        "quality-profile"
    )


def test_gateway_schema_migration_is_idempotent_and_append_only():
    from core.schema_migrations import _gateway_session_control_v1

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _gateway_session_control_v1(conn, engine, None)
        _gateway_session_control_v1(conn, engine, None)
    inspector = inspect(engine)

    assert {
        "gateway_session_bindings",
        "gateway_run_bindings",
        "gateway_control_events",
    } <= set(inspector.get_table_names())
    assert {
        "active_model_profile_id",
        "preferred_model_profile_id",
        "preferred_model_effective_generation",
    } <= {
        column["name"]
        for column in inspector.get_columns("gateway_session_bindings")
    }
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO gateway_run_bindings ("
            "run_id,binding_id,transport,owner_platform,owner_type,owner_id,"
            "actor_id,chat_type,chat_stream_id,runtime_session_id,admitted_at"
            ") VALUES ("
            "'run-one','binding-one','agent_link','meapet','user','owner-one',"
            "'actor-one','private','stream-one','session-one',CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO gateway_control_events ("
            "event_id,request_id_sha256,request_fingerprint_sha256,binding_id,"
            "run_id,action,actor_platform,actor_type,actor_id,outcome,"
            "result_json,occurred_at) VALUES ("
            "'event-one','a','b','binding-one','run-one','stop','meapet',"
            "'user','actor-one','accepted','{}',CURRENT_TIMESTAMP)"
        ))
    with pytest.raises(DatabaseError, match="append_only"):
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE gateway_run_bindings SET owner_id='other' "
                "WHERE run_id='run-one'"
            ))
    with pytest.raises(DatabaseError, match="append_only"):
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM gateway_control_events "
                "WHERE event_id='event-one'"
            ))


def test_admin_gateway_control_is_authenticated_and_uses_admin_acl(
    client,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
):
    _admit(db_session, "run-gateway-admin", status="waiting_input")
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "gateway-admin-token",
    )
    path = "/api/v1/admin/gateway-control/runs/run-gateway-admin"

    assert client.get(path).status_code == 401
    response = client.get(
        path,
        headers={"Authorization": "Bearer gateway-admin-token"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["pending_question"] is True
    monkeypatch.setattr(
        "api.admin.gateway_control_routes.get_gateway_model_profile_port",
        lambda: _StaticGatewayModelProfilePort(
            GatewayModelProfileDescriptor(
                profile_id="quality",
                model="model-quality",
                provider_id="provider-one",
                provider_name="Provider One",
                supports_tools=True,
                supports_image=False,
            )
        ),
    )
    profiles_response = client.get(
        "/api/v1/admin/gateway-control/model-profiles",
        headers={"Authorization": "Bearer gateway-admin-token"},
    )
    assert profiles_response.status_code == 200
    assert profiles_response.json()["items"] == [{
        "profile_id": "quality",
        "model": "model-quality",
        "provider_id": "provider-one",
        "provider_name": "Provider One",
        "supports_tools": True,
        "supports_image": False,
    }]


@pytest.mark.asyncio
async def test_agent_link_control_adapter_enforces_peer_session_acl(
    db_session,
):
    from core.agent_link.protocol import AgentLinkProtocolError
    from core.db.session import session_factory_from_session
    from core.gateway_control.agent_link import (
        SqlAlchemyAgentLinkSessionControlAdapter,
    )

    _admit(db_session, "run-agent-link-control")
    adapter = SqlAlchemyAgentLinkSessionControlAdapter(
        session_factory_from_session(db_session),
        _StaticGatewayModelProfilePort(),
    )
    status = await adapter.handle_session_control(
        platform_id="meapet",
        owner_id=OWNER_ID,
        actor_id="agent_link:meapet:device-one",
        runtime_session_id=RUNTIME_SESSION_ID,
        message_type="session.status",
        request_id="status-one",
        payload={"run_id": "run-agent-link-control", "owner_id": "伪造"},
    )

    assert status["run_id"] == "run-agent-link-control"
    with pytest.raises(AgentLinkProtocolError) as caught:
        await adapter.handle_session_control(
            platform_id="meapet",
            owner_id=OWNER_ID,
            actor_id="agent_link:meapet:device-one",
            runtime_session_id="agent_link:wrong-session",
            message_type="session.status",
            request_id="status-wrong-session",
            payload={"run_id": "run-agent-link-control"},
        )
    assert caught.value.code == "gateway_control_access_denied"


def test_model_profile_descriptor_and_priority_do_not_expose_credentials():
    from core.model_provider.route_plan import ReplyRoutePlan
    from nanobot_kt.model_runtime import (
        _prioritize_reply_profile,
        reply_model_profile_descriptors,
    )

    plans = [
        ReplyRoutePlan(
            provider_id="provider-one",
            registry_provider="openai",
            base_url="https://secret.example/v1",
            api_key="secret-key",
            timeout=30,
            profile_id="economy",
            model="model-economy",
            codex_account_id="secret-account",
        ),
        ReplyRoutePlan(
            provider_id="provider-two",
            registry_provider="openai",
            base_url="https://secret-two.example/v1",
            api_key="secret-key-two",
            timeout=30,
            profile_id="quality",
            model="model-quality",
        ),
    ]

    prioritized = _prioritize_reply_profile(plans, "quality")
    descriptors = reply_model_profile_descriptors(plans)

    assert prioritized[0].profile_id == "quality"
    serialized = str(descriptors)
    assert "secret-key" not in serialized
    assert "secret.example" not in serialized
    assert "secret-account" not in serialized


def test_run_tracer_atomically_admits_typed_web_session_binding(db_session):
    from core.db.models.gateway_control import (
        GatewayRunBindingRow,
        GatewaySessionBindingRow,
    )
    from core.tracing import RunTracer
    from foundation.identity import (
        ActorIdentity,
        Principal,
        RecipientIdentity,
        resolve_chat_stream_identity,
    )
    from foundation.message_contract import (
        GatewayMetadata,
        InboundMessageContract,
        TextContent,
    )
    from nanobot_kt.message_adapter import build_kt_message_invocation
    from nanobot_kt.bridge_state import build_bridge_run_meta

    message = InboundMessageContract(
        message_id="web-message-one",
        chat_stream=resolve_chat_stream_identity(
            platform="web",
            chat_type="private",
            session_id="web-user-one",
        ),
        actor=ActorIdentity(platform="web", actor_id="web-user-one"),
        recipient=RecipientIdentity(
            platform="web",
            recipient_type="user",
            recipient_id="web-user-one",
        ),
        principal=Principal(
            platform="web",
            owner_type="user",
            owner_id="web-user-one",
        ),
        text="测试类型化 Gateway 接纳",
        parts=(TextContent("测试类型化 Gateway 接纳"),),
        gateway=GatewayMetadata(source="web-gateway"),
    )
    invocation = build_kt_message_invocation(
        message,
        content="测试类型化 Gateway 接纳",
        runtime_user_id="web-user-one",
        runtime_session_id="private_web-user-one",
        sender_name="Web 用户",
    )

    run_meta = build_bridge_run_meta(
        invocation.metadata,
        sender_name="Web 用户",
        is_group=False,
        prompt_engine="v2",
        platform="web",
        chat_type="private",
    )
    assert "_gateway_run_admission" not in invocation.metadata
    handle = RunTracer.start_run(
        session_id=invocation.session_id,
        user_id=invocation.user_id,
        chat_type="private",
        input_preview=invocation.content,
        meta=run_meta,
    )
    try:
        db_session.expire_all()
        run_binding = db_session.get(GatewayRunBindingRow, handle.run_id)
        session_binding = db_session.get(
            GatewaySessionBindingRow,
            invocation.metadata["gateway_binding_id"],
        )
        assert run_binding is not None
        assert session_binding is not None
        assert run_binding.transport == "web"
        assert run_binding.owner_id == "web-user-one"
        assert run_binding.runtime_session_id == "private_web-user-one"
        assert session_binding.current_run_id == handle.run_id
    finally:
        RunTracer.finish_run(
            handle.run_id,
            task_lease=handle.task_lease,
            status="success",
        )
