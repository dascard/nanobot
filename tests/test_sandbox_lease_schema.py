from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import BigInteger
from sqlalchemy.exc import IntegrityError

from core.database import (
    SANDBOX_LEASE_NONTERMINAL_STATUSES,
    SANDBOX_LEASE_STATUSES,
    SANDBOX_LEASE_TERMINAL_STATUSES,
    Asset,
    SandboxAccessGrant,
    SandboxAdminOperation,
    SandboxLease,
    SandboxRun,
    Workspace,
    WorkspaceQuotaBinding,
)
from core.sandbox.repositories import SandboxLeaseRepository


def _parents(db_session, *, suffix: str = "") -> tuple[Workspace, SandboxAccessGrant]:
    workspace = Workspace(
        id=str(uuid4()),
        platform="qq",
        owner_type="user",
        owner_id=f"lease-owner-{suffix}",
        name="default",
        status="active",
        quota_bytes=4 * 1024 * 1024 * 1024,
        used_bytes=0,
    )
    grant = SandboxAccessGrant(
        id=str(uuid4()),
        chat_stream_id=f"qq:lease-owner-{suffix}:private",
        platform="qq",
        chat_type="private",
        external_session_id=f"lease-owner-{suffix}",
        workspace_id=workspace.id,
        capability_level="exec",
        execution_profile="developer",
        status="active",
        version=1,
    )
    db_session.add(workspace)
    db_session.flush()
    db_session.add(grant)
    db_session.commit()
    return workspace, grant


def _lease(
    workspace: Workspace,
    grant: SandboxAccessGrant,
    *,
    lease_id: str,
    lease_key: str,
    status: str = "provisioning",
    profile_id: str = "developer",
) -> SandboxLease:
    return SandboxLease(
        lease_id=lease_id,
        lease_key=lease_key,
        grant_id=grant.id,
        chat_stream_id=grant.chat_stream_id,
        workspace_id=workspace.id,
        profile_id=profile_id,
        catalog_generation="20260725.1",
        policy_sha256="a" * 64,
        status=status,
        image_digest="",
        controller_epoch="",
    )


def test_lease_status_sets_are_explicit_and_disjoint():
    assert SANDBOX_LEASE_NONTERMINAL_STATUSES == {
        "provisioning",
        "active",
        "idle",
        "stopping",
    }
    assert SANDBOX_LEASE_TERMINAL_STATUSES == {
        "stopped",
        "expired",
        "destroyed",
        "failed",
    }
    assert (
        SANDBOX_LEASE_NONTERMINAL_STATUSES
        & SANDBOX_LEASE_TERMINAL_STATUSES
    ) == set()
    assert SANDBOX_LEASE_STATUSES == {
        "provisioning",
        "active",
        "idle",
        "stopping",
        "stopped",
        "expired",
        "destroyed",
        "failed",
    }


def test_lease_and_run_ledgers_do_not_persist_sensitive_bodies_or_host_paths():
    lease_columns = set(SandboxLease.__table__.columns.keys())
    run_columns = set(SandboxRun.__table__.columns.keys())

    assert {
        "lease_id",
        "lease_key",
        "grant_id",
        "chat_stream_id",
        "workspace_id",
        "profile_id",
        "catalog_generation",
        "policy_sha256",
        "status",
        "image_digest",
        "controller_epoch",
        "created_at",
        "last_active_at",
        "idle_expires_at",
        "max_expires_at",
        "stopped_at",
        "reconciled_at",
        "last_error_code",
        "last_error_summary",
    } == lease_columns
    assert {
        "lease_id",
        "profile_id",
        "execution_mode",
        "process_state",
        "last_seen_at",
    } <= run_columns
    forbidden_columns = {
        "host_path",
        "workspace_host_path",
        "runtime_host_path",
        "command",
        "stdout",
        "stderr",
        "token",
        "raw_token",
        "secret",
        "environment",
    }
    assert lease_columns.isdisjoint(forbidden_columns)
    assert run_columns.isdisjoint(forbidden_columns)


def test_byte_accounting_columns_use_big_integer_orm_semantics():
    assert isinstance(Workspace.quota_bytes.type, BigInteger)
    assert isinstance(Workspace.used_bytes.type, BigInteger)
    assert isinstance(Asset.size_bytes.type, BigInteger)
    assert isinstance(SandboxRun.peak_memory_bytes.type, BigInteger)
    assert isinstance(SandboxRun.stdout_bytes.type, BigInteger)
    assert isinstance(SandboxRun.stderr_bytes.type, BigInteger)
    assert isinstance(WorkspaceQuotaBinding.desired_quota_bytes.type, BigInteger)
    assert isinstance(WorkspaceQuotaBinding.applied_quota_bytes.type, BigInteger)
    assert isinstance(SandboxAdminOperation.desired_quota_bytes.type, BigInteger)


def test_partial_unique_index_allows_terminal_history_but_one_current_lease(
    db_session,
):
    workspace, grant = _parents(db_session, suffix="history")
    repository = SandboxLeaseRepository(db_session)
    lease_key = "b" * 64
    first = repository.add_or_get_current(_lease(
        workspace,
        grant,
        lease_id="lease-first",
        lease_key=lease_key,
    ))
    db_session.commit()
    assert first.lease_id == "lease-first"

    db_session.add(_lease(
        workspace,
        grant,
        lease_id="lease-conflict",
        lease_key=lease_key,
        status="active",
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    stopped_at = datetime(2026, 7, 25, 12, 0, 0)
    stopped = repository.transition(
        "lease-first",
        expected_statuses={"provisioning"},
        target_status="stopped",
        changes={"stopped_at": stopped_at},
    )
    db_session.commit()
    assert stopped is not None
    assert stopped.status == "stopped"

    second = repository.add_or_get_current(_lease(
        workspace,
        grant,
        lease_id="lease-second",
        lease_key=lease_key,
        status="active",
    ))
    repository.add_history(_lease(
        workspace,
        grant,
        lease_id="lease-failed-history",
        lease_key=lease_key,
        status="failed",
    ))
    db_session.commit()

    assert second.lease_id == "lease-second"
    assert repository.get_current_by_lease_key(lease_key).lease_id == "lease-second"
    assert (
        db_session.query(SandboxLease)
        .filter(SandboxLease.lease_key == lease_key)
        .count()
    ) == 3


def test_repository_returns_winner_after_partial_unique_key_race(
    db_session,
    monkeypatch,
):
    workspace, grant = _parents(db_session, suffix="race")
    repository = SandboxLeaseRepository(db_session)
    lease_key = "c" * 64
    winner = _lease(
        workspace,
        grant,
        lease_id="lease-winner",
        lease_key=lease_key,
        status="active",
    )
    db_session.add(winner)
    db_session.commit()

    original_get = repository.get_current_by_lease_key
    calls = {"count": 0}

    def miss_before_insert(key: str):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original_get(key)

    monkeypatch.setattr(
        repository,
        "get_current_by_lease_key",
        miss_before_insert,
    )
    resolved = repository.add_or_get_current(_lease(
        workspace,
        grant,
        lease_id="lease-loser",
        lease_key=lease_key,
        status="idle",
    ))

    assert calls["count"] == 2
    assert resolved.lease_id == "lease-winner"


def test_repository_transition_is_idempotent_and_terminal_is_not_reopened(
    db_session,
):
    workspace, grant = _parents(db_session, suffix="transition")
    repository = SandboxLeaseRepository(db_session)
    repository.add_or_get_current(_lease(
        workspace,
        grant,
        lease_id="lease-transition",
        lease_key="d" * 64,
        status="active",
    ))
    db_session.commit()

    stopped = repository.transition(
        "lease-transition",
        expected_statuses={"active", "idle"},
        target_status="stopped",
    )
    repeated = repository.transition(
        "lease-transition",
        expected_statuses={"active"},
        target_status="stopped",
    )
    reopened = repository.transition(
        "lease-transition",
        expected_statuses={"stopped"},
        target_status="active",
    )

    assert stopped is not None
    assert repeated is not None
    assert repeated.status == "stopped"
    assert reopened is None


def test_repository_rejects_unknown_status_profile_and_transition_fields(
    db_session,
):
    workspace, grant = _parents(db_session, suffix="invalid")
    repository = SandboxLeaseRepository(db_session)

    with pytest.raises(ValueError, match="status 无效"):
        repository.add_or_get_current(_lease(
            workspace,
            grant,
            lease_id="lease-bad-status",
            lease_key="e" * 64,
            status="surprise",
        ))
    with pytest.raises(ValueError, match="profile_id 无效"):
        repository.add_or_get_current(_lease(
            workspace,
            grant,
            lease_id="lease-bad-profile",
            lease_key="f" * 64,
            profile_id="model_supplied",
        ))
    with pytest.raises(ValueError, match="字段无效"):
        repository.transition(
            "missing",
            expected_statuses={"active"},
            target_status="stopped",
            changes={"command": "禁止持久化"},
        )
