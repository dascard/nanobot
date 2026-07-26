from __future__ import annotations

from core.database import (
    SandboxControllerState,
    SandboxLease,
    SandboxRun,
)
from core.sandbox.lease_reconciler import SandboxLeaseReconciler
from tests.test_sandbox_lease_reconciler import (
    IMAGE_ID,
    _backend,
    _factory,
    _lease,
    _parents,
)


def _lease_fact(lease, *, epoch: str, active=()):
    return {
        "lease_id": lease.lease_id,
        "workspace_id": lease.workspace_id,
        "profile_id": lease.profile_id,
        "catalog_generation": lease.catalog_generation,
        "policy_sha256": lease.policy_sha256,
        "image_digest": IMAGE_ID,
        "controller_epoch": epoch,
        "quota_generation": 1,
        "status": "active" if active else "idle",
        "present": True,
        "running": True,
        "created_at_unix": 1_785_000_000,
        "last_active_at_unix": 1_785_000_000,
        "idle_expires_at_unix": 1_785_001_800,
        "max_expires_at_unix": 1_785_028_800,
        "active_process_ids": list(active),
    }


def _process_fact(
    run,
    lease,
    *,
    epoch: str,
    status: str,
    reason: str,
    recycled: bool,
    affected=(),
):
    running = status == "running"
    return {
        "process_id": run.run_id,
        "lease_id": lease.lease_id,
        "workspace_id": lease.workspace_id,
        "profile_id": lease.profile_id,
        "controller_epoch": epoch,
        "execution_status": status,
        "process_state": (
            "running" if running else "lost" if recycled else "exited"
        ),
        "exit_code": None if running or recycled else 0,
        "termination_reason": reason,
        "created_at_unix": 1_785_000_000,
        "started_at_unix": 1_785_000_000,
        "finished_at_unix": (
            None if running else 1_785_000_010
        ),
        "stdout_bytes": 12,
        "stderr_bytes": 3,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "cpu_time_ms": 25,
        "peak_memory_bytes": 4096,
        "lease_recycled": recycled,
        "affected_process_ids": list(affected),
    }


def test_reconciler_finishes_unpolled_process_from_controller_fact(
    db_session,
):
    workspace, grant = _parents(db_session, suffix="unpolled")
    epoch = "sbxctl_" + "8" * 32
    lease = _lease(
        workspace,
        grant,
        lease_id="sbxlease_unpolled_process",
        controller_epoch=epoch,
    )
    run = SandboxRun(
        run_id="sbxrun_unpolled_process",
        request_id="sbxrun_unpolled_process",
        workspace_id=workspace.id,
        lease_id=lease.lease_id,
        profile_id="developer",
        execution_mode="lease",
        process_state="running",
        image_digest=IMAGE_ID,
        status="running",
    )
    db_session.add_all([lease, run])
    db_session.commit()
    process = _process_fact(
        run,
        lease,
        epoch=epoch,
        status="completed",
        reason="completed",
        recycled=False,
        affected=(run.run_id,),
    )
    backend = _backend(
        epoch=epoch,
        leases=(_lease_fact(lease, epoch=epoch),),
        processes=(process,),
    )

    assert SandboxLeaseReconciler(
        _factory(db_session),
        backend_factory=lambda: backend,
    ).run_once() is True

    db_session.expire_all()
    settled = db_session.get(SandboxRun, run.run_id)
    assert settled.status == "completed"
    assert settled.process_state == "exited"
    assert settled.termination_reason == "completed"
    assert settled.stdout_bytes == 12
    assert settled.stderr_bytes == 3
    assert settled.cpu_time_ms == 25
    assert settled.peak_memory_bytes == 4096
    assert (
        db_session.query(SandboxRun)
        .filter(SandboxRun.status.in_({"pending", "running"}))
        .count()
        == 0
    )


def test_reconciler_uses_recycled_process_reason_to_settle_whole_lease(
    db_session,
):
    workspace, grant = _parents(db_session, suffix="timeout-fact")
    epoch = "sbxctl_" + "9" * 32
    lease = _lease(
        workspace,
        grant,
        lease_id="sbxlease_timeout_fact",
        controller_epoch=epoch,
    )
    run = SandboxRun(
        run_id="sbxrun_timeout_fact",
        request_id="sbxrun_timeout_fact",
        workspace_id=workspace.id,
        lease_id=lease.lease_id,
        profile_id="developer",
        execution_mode="lease",
        process_state="running",
        image_digest=IMAGE_ID,
        status="running",
    )
    db_session.add_all([lease, run])
    db_session.commit()
    process = _process_fact(
        run,
        lease,
        epoch=epoch,
        status="failed",
        reason="execution_timeout",
        recycled=True,
        affected=(run.run_id,),
    )
    backend = _backend(
        epoch=epoch,
        leases=(),
        processes=(process,),
    )

    reconciler = SandboxLeaseReconciler(
        _factory(db_session),
        backend_factory=lambda: backend,
    )
    assert reconciler.run_once() is True
    assert reconciler.run_once() is True

    db_session.expire_all()
    settled_lease = db_session.get(SandboxLease, lease.lease_id)
    settled_run = db_session.get(SandboxRun, run.run_id)
    assert settled_lease.status == "failed"
    assert settled_lease.last_error_code == "execution_timeout"
    assert settled_run.status == "failed"
    assert settled_run.process_state == "lost"
    assert settled_run.termination_reason == "execution_timeout"


def test_reconciler_rejects_active_process_without_matching_process_fact(
    db_session,
):
    workspace, grant = _parents(db_session, suffix="missing-process-fact")
    epoch = "sbxctl_" + "a" * 32
    lease = _lease(
        workspace,
        grant,
        lease_id="sbxlease_missing_process_fact",
        controller_epoch=epoch,
    )
    run = SandboxRun(
        run_id="sbxrun_missing_process_fact",
        request_id="sbxrun_missing_process_fact",
        workspace_id=workspace.id,
        lease_id=lease.lease_id,
        profile_id="developer",
        execution_mode="lease",
        process_state="running",
        image_digest=IMAGE_ID,
        status="running",
    )
    db_session.add_all([lease, run])
    db_session.commit()
    backend = _backend(
        epoch=epoch,
        leases=(
            _lease_fact(
                lease,
                epoch=epoch,
                active=(run.run_id,),
            ),
        ),
        processes=(),
    )

    assert SandboxLeaseReconciler(
        _factory(db_session),
        backend_factory=lambda: backend,
    ).run_once() is False

    db_session.expire_all()
    assert db_session.get(SandboxLease, lease.lease_id).status == "active"
    assert db_session.get(SandboxRun, run.run_id).status == "running"


def test_reconciler_rejects_malformed_process_fact_without_partial_settlement(
    db_session,
):
    workspace, grant = _parents(db_session, suffix="malformed-process-fact")
    epoch = "sbxctl_" + "c" * 32
    lease = _lease(
        workspace,
        grant,
        lease_id="sbxlease_malformed_process_fact",
        controller_epoch=epoch,
    )
    run = SandboxRun(
        run_id="sbxrun_malformed_process_fact",
        request_id="sbxrun_malformed_process_fact",
        workspace_id=workspace.id,
        lease_id=lease.lease_id,
        profile_id="developer",
        execution_mode="lease",
        process_state="running",
        image_digest=IMAGE_ID,
        status="running",
    )
    db_session.add_all([lease, run])
    db_session.commit()
    malformed = _process_fact(
        run,
        lease,
        epoch=epoch,
        status="completed",
        reason="completed",
        recycled=False,
        affected=(run.run_id,),
    )
    malformed["workspace_id"] = "../../srv/other-owner"
    backend = _backend(
        epoch=epoch,
        leases=(_lease_fact(lease, epoch=epoch),),
        processes=(malformed,),
    )

    assert SandboxLeaseReconciler(
        _factory(db_session),
        backend_factory=lambda: backend,
    ).run_once() is False

    db_session.expire_all()
    preserved_lease = db_session.get(SandboxLease, lease.lease_id)
    preserved_run = db_session.get(SandboxRun, run.run_id)
    controller = db_session.get(SandboxControllerState, "sandboxd")
    assert preserved_lease.status == "active"
    assert preserved_run.status == "running"
    assert controller.last_error_code == "runtime_unavailable"
