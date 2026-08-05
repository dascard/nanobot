from uuid import uuid4

import pytest
from sqlalchemy import text

from core.database import (
    SandboxAccessGrant,
    SystemSetting,
    Workspace,
    WorkspaceQuotaBinding,
    WorkspaceRuntimeQuotaBinding,
)
from core.sandbox.access_contracts import (
    LEASE_PROCESS_TOOL_NAMES,
    TOOL_REQUIRED_CAPABILITY,
    SandboxCapability,
)
from core.sandbox.access_policy import (
    SANDBOX_ACCESS_POLICY_DESCRIPTOR,
    SandboxAccessPolicy,
)
from core.settings_service import settings
from core.tool_registry import SANDBOX_TOOL_NAMES


MIB = 1024 * 1024


@pytest.fixture(autouse=True)
def _developer_hard_ceiling(monkeypatch):
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_SESSION_EXECUTION_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED",
        "true",
    )
    settings.invalidate()
    try:
        yield
    finally:
        settings.invalidate()


def _grant(
    db_session,
    *,
    session_id: str,
    execution_profile: str = "restricted",
):
    workspace_id = str(uuid4())
    grant_id = str(uuid4())
    db_session.add(Workspace(
        id=workspace_id,
        platform="qq",
        owner_type="user",
        owner_id=grant_id,
        name="default",
        status="active",
        quota_bytes=32 * MIB,
        used_bytes=0,
    ))
    db_session.flush()
    db_session.add_all([
        WorkspaceQuotaBinding(
            workspace_id=workspace_id,
            project_id=10000,
            desired_quota_bytes=32 * MIB,
            applied_quota_bytes=32 * MIB,
            status="applied",
            generation=1,
        ),
        WorkspaceRuntimeQuotaBinding(
            workspace_id=workspace_id,
            project_id=10001,
            desired_quota_bytes=(
                10 * 1024 * MIB
                if execution_profile == "developer"
                else 512 * MIB
            ),
            applied_quota_bytes=(
                10 * 1024 * MIB
                if execution_profile == "developer"
                else 512 * MIB
            ),
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
            capability_level="exec",
            execution_profile=execution_profile,
            status="active",
            version=1,
        ),
    ])
    db_session.add_all([
        SystemSetting(key="sandbox.enabled", value="true"),
        SystemSetting(key="sandbox.exec_enabled", value="true"),
    ])
    db_session.commit()


def test_sandbox_tool_and_capability_registries_remain_exactly_aligned():
    assert set(SANDBOX_TOOL_NAMES) == set(TOOL_REQUIRED_CAPABILITY)


def test_restricted_profile_rejects_lease_process_tools(db_session):
    _grant(db_session, session_id="restricted-process-tools")
    policy = SandboxAccessPolicy(
        db_session,
        infrastructure_allowed=True,
    )

    for tool_name in LEASE_PROCESS_TOOL_NAMES:
        decision = policy.evaluate(
            tool_name,
            platform="qq",
            chat_type="private",
            session_id="private_restricted-process-tools",
        )
        assert decision.allowed is False
        assert decision.code == "authorization_failed"
        assert decision.execution_profile == "restricted"


def test_developer_profile_allows_registered_lease_process_tools(db_session):
    _grant(
        db_session,
        session_id="developer-process-tools",
        execution_profile="developer",
    )
    policy = SandboxAccessPolicy(
        db_session,
        infrastructure_allowed=True,
    )

    for tool_name in LEASE_PROCESS_TOOL_NAMES:
        decision = policy.evaluate(
            tool_name,
            platform="qq",
            chat_type="private",
            session_id="private_developer-process-tools",
        )
        assert decision.allowed is True
        assert decision.execution_profile == "developer"


def test_access_decision_contract_is_versioned_and_defaults_to_restricted(
    db_session,
    monkeypatch,
):
    _grant(db_session, session_id="profile-contract")
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED",
        "true",
    )
    settings.invalidate()

    decision = SandboxAccessPolicy(db_session).evaluate_context(
        "sandbox_exec",
        {
            "platform": "qq",
            "chat_type": "private",
            "session_id": "private_profile-contract",
            "execution_profile": "trusted_developer",
        },
    )

    assert SANDBOX_ACCESS_POLICY_DESCRIPTOR.output_contract == (
        "sandbox.access.decision.v2"
    )
    assert decision.allowed is True
    assert decision.execution_profile == "restricted"
    assert decision.execution_profile != "trusted_developer"


def test_access_decision_reads_developer_profile_only_from_grant(
    db_session,
):
    _grant(
        db_session,
        session_id="developer-profile",
        execution_profile="developer",
    )

    decision = SandboxAccessPolicy(
        db_session,
        infrastructure_allowed=True,
    ).evaluate_context(
        "sandbox_exec",
        {
            "platform": "qq",
            "chat_type": "private",
            "session_id": "private_developer-profile",
            "execution_profile": "trusted_developer",
        },
    )

    assert decision.allowed is True
    assert decision.execution_profile == "developer"


def test_non_grantable_profile_fails_closed_even_when_grant_is_active(
    db_session,
):
    _grant(
        db_session,
        session_id="trusted-profile",
        execution_profile="trusted_developer",
    )

    decision = SandboxAccessPolicy(
        db_session,
        infrastructure_allowed=True,
    ).evaluate(
        "sandbox_exec",
        platform="qq",
        chat_type="private",
        session_id="private_trusted-profile",
    )

    assert decision.allowed is False
    assert decision.code == "authorization_failed"
    assert decision.execution_profile == "trusted_developer"


def test_corrupted_unknown_grant_profile_fails_closed(
    db_session,
):
    _grant(db_session, session_id="unknown-profile")
    db_session.execute(text("PRAGMA ignore_check_constraints = ON"))
    db_session.execute(text(
        "UPDATE sandbox_access_grants "
        "SET execution_profile = 'unknown_profile' "
        "WHERE external_session_id = 'unknown-profile'"
    ))
    db_session.commit()
    db_session.execute(text("PRAGMA ignore_check_constraints = OFF"))
    db_session.expire_all()

    decision = SandboxAccessPolicy(
        db_session,
        infrastructure_allowed=True,
    ).evaluate(
        "sandbox_exec",
        platform="qq",
        chat_type="private",
        session_id="private_unknown-profile",
    )

    assert decision.allowed is False
    assert decision.code == "authorization_failed"
    assert decision.execution_profile == "unknown_profile"


def test_unknown_tool_is_rejected_without_falling_back_to_exec(
    db_session,
):
    decision = SandboxAccessPolicy(
        db_session,
        infrastructure_allowed=True,
    ).evaluate(
        "model_supplied_tool",
        platform="qq",
        chat_type="private",
        session_id="private_unknown",
    )

    assert decision.allowed is False
    assert decision.code == "authorization_failed"
    assert decision.required_capability is SandboxCapability.OFF
