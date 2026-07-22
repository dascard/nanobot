from uuid import uuid4

import pytest

from core.database import (
    SandboxAccessGrant,
    SystemSetting,
    Workspace,
    WorkspaceQuotaBinding,
)
from core.sandbox.access_contracts import SandboxCapability
from core.sandbox.access_policy import SandboxAccessPolicy
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


def test_group_session_remains_hard_disabled_even_with_database_grant(
    db_session,
    infrastructure_allowed,
):
    _set_bool(db_session, "sandbox.enabled", True)
    _set_bool(db_session, "sandbox.exec_enabled", True)
    _set_bool(db_session, "sandbox.group_enabled", True)
    workspace_id = str(uuid4())
    grant_id = str(uuid4())
    db_session.add(Workspace(
        id=workspace_id,
        platform="qq",
        owner_type="group",
        owner_id="10086",
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
        SandboxAccessGrant(
            id=grant_id,
            chat_stream_id="qq:10086:group",
            platform="qq",
            chat_type="group",
            external_session_id="10086",
            workspace_id=workspace_id,
            capability_level="exec",
            status="active",
            version=1,
        ),
    ])
    db_session.commit()

    decision = SandboxAccessPolicy(db_session).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="group",
        session_id="group_10086",
    )

    assert decision.allowed is False
    assert decision.code == "sandbox_not_enabled"


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
