from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from core.database import (
    SandboxAccessGrant,
    SystemSetting,
    Workspace,
    WorkspaceQuotaBinding,
    WorkspaceRuntimeQuotaBinding,
)
from core.sandbox.access_contracts import SandboxCapability
from core.sandbox.access_policy import SandboxAccessPolicy
from core.runtime_tool_service import resolve_effective_tools
from core.settings_service import settings


MIB = 1024 * 1024


@pytest.fixture
def infrastructure_allowed(monkeypatch):
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED",
        "true",
    )
    settings.invalidate()
    try:
        yield
    finally:
        settings.invalidate()


def _set_bool(db, key: str, value: bool) -> None:
    db.add(SystemSetting(key=key, value="true" if value else "false"))


def _grant_session(
    db,
    *,
    session_id: str,
    capability: str = "workspace",
    quota_status: str = "applied",
    desired_quota: int = 32 * MIB,
    applied_quota: int | None = None,
):
    workspace_id = str(uuid4())
    grant_id = str(uuid4())
    workspace = Workspace(
        id=workspace_id,
        platform="qq",
        owner_type="user",
        owner_id=grant_id,
        name="default",
        status="active",
        quota_bytes=desired_quota,
        used_bytes=0,
    )
    db.add(workspace)
    db.flush()
    db.add_all([
        WorkspaceQuotaBinding(
            workspace_id=workspace_id,
            project_id=10000,
            desired_quota_bytes=desired_quota,
            applied_quota_bytes=(
                desired_quota if applied_quota is None else applied_quota
            ),
            status=quota_status,
            generation=1,
        ),
        WorkspaceRuntimeQuotaBinding(
            workspace_id=workspace_id,
            project_id=10001,
            desired_quota_bytes=512 * MIB,
            applied_quota_bytes=512 * MIB,
            status="applied",
            generation=1,
        ),
        SandboxAccessGrant(
            id=grant_id,
            chat_stream_id=f"qq:{session_id}:private",
            platform="qq",
            chat_type="private",
            external_session_id=session_id,
            workspace_id=workspace_id,
            capability_level=capability,
            status="active" if capability != "off" else "disabled",
            version=1,
        ),
    ])
    db.commit()
    return workspace_id


def _grant_group_session(
    db,
    *,
    group_id: str,
    capability: str = "workspace",
    project_id: int = 10000,
):
    workspace_id = str(uuid4())
    grant_id = str(uuid4())
    db.add(Workspace(
        id=workspace_id,
        platform="qq",
        owner_type="group",
        owner_id=group_id,
        name="default",
        status="active",
        quota_bytes=32 * MIB,
        used_bytes=0,
    ))
    db.flush()
    db.add_all([
        WorkspaceQuotaBinding(
            workspace_id=workspace_id,
            project_id=project_id,
            desired_quota_bytes=32 * MIB,
            applied_quota_bytes=32 * MIB,
            status="applied",
            generation=1,
        ),
        WorkspaceRuntimeQuotaBinding(
            workspace_id=workspace_id,
            project_id=project_id + 1,
            desired_quota_bytes=512 * MIB,
            applied_quota_bytes=512 * MIB,
            status="applied",
            generation=1,
        ),
        SandboxAccessGrant(
            id=grant_id,
            chat_stream_id=f"qq:{group_id}:group",
            platform="qq",
            chat_type="group",
            external_session_id=group_id,
            workspace_id=workspace_id,
            capability_level=capability,
            status="active",
            version=1,
        ),
    ])
    db.commit()
    return workspace_id


def test_private_superuser_without_explicit_session_grant_is_denied(
    db_session,
    infrastructure_allowed,
):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", True)
    db_session.commit()

    decision = SandboxAccessPolicy(db_session).evaluate(
        "sandbox_exec",
        platform="qq",
        chat_type="private_superuser",
        session_id="private_10001",
    )

    assert decision.allowed is False
    assert decision.code == "authorization_failed"
    assert decision.granted_capability is SandboxCapability.OFF


def test_canonical_alias_resolves_to_the_same_explicit_session_grant(
    db_session,
    infrastructure_allowed,
):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", False)
    workspace_id = _grant_session(db_session, session_id="alice")

    alias = SandboxAccessPolicy(db_session).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="private_superuser",
        session_id="private_alice",
    )
    canonical = SandboxAccessPolicy(db_session).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="private",
        session_id="qq:alice:private",
    )

    assert alias.allowed is True
    assert canonical.allowed is True
    assert alias.workspace_id == canonical.workspace_id == workspace_id
    assert alias.identity is not None
    assert alias.identity.chat_stream_id == "qq:alice:private"


def test_grant_cannot_authorize_workspace_owned_by_another_scope(
    db_session,
    infrastructure_allowed,
):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", False)
    workspace_id = _grant_session(db_session, session_id="alice")
    workspace = db_session.get(Workspace, workspace_id)
    workspace.owner_id = "foreign-grant"
    db_session.commit()

    decision = SandboxAccessPolicy(db_session).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="private",
        session_id="private_alice",
    )

    assert decision.allowed is False
    assert decision.code == "authorization_failed"
    assert decision.workspace_id == workspace_id


def test_same_user_metadata_cannot_bridge_two_sessions(
    db_session,
    infrastructure_allowed,
):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", False)
    _grant_session(db_session, session_id="session-a")

    allowed = SandboxAccessPolicy(db_session).evaluate_context(
        "workspace_list",
        {
            "platform": "qq",
            "chat_type": "private",
            "session_id": "private_session-a",
            "user_id": "same-user",
        },
    )
    denied = SandboxAccessPolicy(db_session).evaluate_context(
        "workspace_list",
        {
            "platform": "qq",
            "chat_type": "private",
            "session_id": "private_session-b",
            "user_id": "same-user",
        },
    )

    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.code == "authorization_failed"


@pytest.mark.parametrize(
    ("quota_status", "applied_quota"),
    [
        ("pending", 0),
        ("applying", 0),
        ("error", 0),
        ("applied", 16 * MIB),
    ],
)
def test_unconfirmed_or_mismatched_project_quota_denies_all_tools(
    db_session,
    infrastructure_allowed,
    quota_status,
    applied_quota,
):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", True)
    _grant_session(
        db_session,
        session_id="quota-not-ready",
        capability="exec",
        quota_status=quota_status,
        applied_quota=applied_quota,
    )

    decision = SandboxAccessPolicy(db_session).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="private",
        session_id="private_quota-not-ready",
    )

    assert decision.allowed is False
    assert decision.code == "authorization_failed"


def test_group_session_is_denied_when_group_feature_is_disabled(
    db_session,
    infrastructure_allowed,
):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", True)
    _set_bool(db_session, "sandbox.group_enabled", False)
    _grant_group_session(db_session, group_id="10086", capability="exec")

    decision = SandboxAccessPolicy(db_session).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="group",
        session_id="group_10086",
    )

    assert decision.allowed is False
    assert decision.code == "sandbox_not_enabled"


def test_group_session_requires_feature_and_explicit_group_grant(
    db_session,
    infrastructure_allowed,
):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", False)
    _set_bool(db_session, "sandbox.group_enabled", True)
    workspace_id = _grant_group_session(db_session, group_id="10086")

    allowed = SandboxAccessPolicy(db_session).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="group",
        session_id="group_10086",
    )
    ungranted = SandboxAccessPolicy(db_session).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="group",
        session_id="group_10010",
    )

    assert allowed.allowed is True
    assert allowed.workspace_id == workspace_id
    assert ungranted.allowed is False
    assert ungranted.code == "authorization_failed"


def test_group_grant_cannot_bridge_another_group_or_private_session(
    db_session,
    infrastructure_allowed,
):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", False)
    _set_bool(db_session, "sandbox.group_enabled", True)
    _grant_group_session(db_session, group_id="shared-id")

    group_a = SandboxAccessPolicy(db_session).evaluate(
        "workspace_list",
        platform="qq",
        chat_type="group",
        session_id="group_shared-id",
    )
    group_b = SandboxAccessPolicy(db_session).evaluate(
        "workspace_list",
        platform="qq",
        chat_type="group",
        session_id="group_other",
    )
    private = SandboxAccessPolicy(db_session).evaluate(
        "workspace_list",
        platform="qq",
        chat_type="private",
        session_id="private_shared-id",
    )

    assert group_a.allowed is True
    assert group_b.allowed is False
    assert private.allowed is False
    assert group_b.code == private.code == "authorization_failed"


def test_private_grant_cannot_bridge_group_with_same_external_id(
    db_session,
    infrastructure_allowed,
):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", False)
    _set_bool(db_session, "sandbox.group_enabled", True)
    _grant_session(db_session, session_id="shared-id")

    private = SandboxAccessPolicy(db_session).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="private",
        session_id="private_shared-id",
    )
    group = SandboxAccessPolicy(db_session).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="group",
        session_id="group_shared-id",
    )

    assert private.allowed is True
    assert group.allowed is False
    assert group.code == "authorization_failed"


def test_group_grant_is_projected_into_runtime_tool_plan(
    db_session,
    infrastructure_allowed,
):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", True)
    _set_bool(db_session, "sandbox.group_enabled", True)
    _grant_group_session(
        db_session,
        group_id="tool-plan-group",
        capability="exec",
    )

    enabled, disabled = resolve_effective_tools(
        chat_type="group",
        group_id="tool-plan-group",
        user_id="group-member",
        platform="qq",
        session_id="group_tool-plan-group",
        runtime_preset="full",
        db=db_session,
    )

    assert enabled["workspace_read"] is True
    assert enabled["sandbox_exec"] is True
    assert "workspace_read" not in disabled
    assert "sandbox_exec" not in disabled


def test_host_hard_ceiling_precedes_database_feature_flags(db_session):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", True)
    _grant_session(db_session, session_id="hard-ceiling", capability="exec")

    decision = SandboxAccessPolicy(
        db_session,
        infrastructure_allowed=False,
    ).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="private",
        session_id="private_hard-ceiling",
    )

    assert decision.allowed is False
    assert decision.code == "sandbox_not_enabled"
    assert "硬上限" in decision.reason


def test_runtime_permission_decision_is_committed_before_tool_access(
    db_session,
    infrastructure_allowed,
):
    from core.run_ledger.adapters import run_accepted_event
    from core.run_ledger.persistence import SqlAlchemyRunEventLedger
    from core.tracing_context import (
        reset_runtime_correlation,
        set_runtime_correlation,
    )

    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", False)
    _grant_session(db_session, session_id="ledger-user")
    ledger = SqlAlchemyRunEventLedger(db_session)
    ledger.append(run_accepted_event(
        run_id="run-permission-ledger",
        trace_id="trace-permission-ledger",
        session_id="qq:ledger-user:private",
        user_id="ledger-user",
        chat_type="private",
        group_id="",
        run_type="chat",
        prompt_mode="prompt",
        prompt_key="chat_private",
        prompt_sha256="",
        model="",
        input_value="workspace read",
    ))
    db_session.commit()

    tokens = set_runtime_correlation(
        run_id="run-permission-ledger",
        trace_id="trace-permission-ledger",
        tool_call_id="tool-workspace-read",
    )
    try:
        decision = SandboxAccessPolicy(db_session).evaluate(
            "workspace_read",
            platform="qq",
            chat_type="private",
            session_id="private_ledger-user",
        )
    finally:
        reset_runtime_correlation(tokens)

    assert decision.allowed is True
    records = ledger.read("run-permission-ledger")
    permission = records[-1]
    assert permission.event_type == "permission.decided"
    assert permission.status == "allow"
    assert permission.event.payload == {
        "action": "workspace_read",
        "outcome": "allow",
        "reason_code": "allowed",
        "capability": "workspace",
    }


def test_repeated_runtime_permission_decision_is_idempotent(
    db_session,
    infrastructure_allowed,
    monkeypatch,
):
    from core.run_ledger.adapters import run_accepted_event
    from core.run_ledger.persistence import SqlAlchemyRunEventLedger
    from core.tracing_context import (
        reset_runtime_correlation,
        set_runtime_correlation,
    )

    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", False)
    _grant_session(db_session, session_id="repeated-ledger-user")
    ledger = SqlAlchemyRunEventLedger(db_session)
    ledger.append(run_accepted_event(
        run_id="run-repeated-permission-ledger",
        trace_id="trace-repeated-permission-ledger",
        session_id="qq:repeated-ledger-user:private",
        user_id="repeated-ledger-user",
        chat_type="private",
        group_id="",
        run_type="chat",
        prompt_mode="prompt",
        prompt_key="chat_private",
        prompt_sha256="",
        model="",
        input_value="workspace read",
    ))
    db_session.commit()

    first = datetime(2026, 8, 15, 11, 0, tzinfo=timezone.utc)
    instants = iter((first, first + timedelta(seconds=1)))
    monkeypatch.setattr(
        "core.run_ledger.adapters._now",
        lambda: next(instants),
    )
    tokens = set_runtime_correlation(
        run_id="run-repeated-permission-ledger",
        trace_id="trace-repeated-permission-ledger",
        tool_call_id="tool-workspace-read",
    )
    try:
        policy = SandboxAccessPolicy(db_session)
        first_decision = policy.evaluate(
            "workspace_read",
            platform="qq",
            chat_type="private",
            session_id="private_repeated-ledger-user",
        )
        repeated_decision = policy.evaluate(
            "workspace_read",
            platform="qq",
            chat_type="private",
            session_id="private_repeated-ledger-user",
        )
    finally:
        reset_runtime_correlation(tokens)

    assert first_decision.allowed is True
    assert repeated_decision == first_decision
    permission_records = [
        record
        for record in ledger.read("run-repeated-permission-ledger")
        if record.event_type == "permission.decided"
    ]
    assert len(permission_records) == 1
    assert permission_records[0].event.occurred_at == first
