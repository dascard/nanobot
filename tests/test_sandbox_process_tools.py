from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from core.database import (
    SandboxAccessGrant,
    SandboxLease,
    SandboxRun,
    SystemSetting,
    Workspace,
    WorkspaceMaintenanceState,
    WorkspaceQuotaBinding,
    WorkspaceRuntimeQuotaBinding,
)
from core.sandbox.access_policy import SandboxAccessPolicy
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.execution_profiles import load_execution_profile_registry
from core.sandbox.process_service import SandboxProcessService
from core.settings_service import settings
from tests.test_sandbox_admin_service import MIB, _factory
from tests.test_sandbox_lease_reconciler import (
    IMAGE_ID,
    _LeaseServiceBackend,
)
from tests.test_sandbox_workspace_quiesce import (
    _prepare_developer_workspace,
)
from tests.test_sandboxd_lease_backend import _manifest


class _ProcessBackend(_LeaseServiceBackend):
    def __init__(self, registry):
        super().__init__(registry)
        self.leases: dict[str, dict] = {}
        self.processes: dict[str, dict] = {}

    def ensure_lease(self, payload):
        response = super().ensure_lease(payload)
        self.leases[str(payload["lease_id"])] = dict(
            response["data"]
        )
        return response

    @staticmethod
    def _response(data):
        return {
            "status": "success",
            "summary": "ok",
            "next_actions": [],
            "artifacts": [],
            "data": data,
        }

    def _active(self, lease_id: str):
        return sorted(
            [
                {
                    "process_id": process_id,
                    "state": "running",
                }
                for process_id, process in self.processes.items()
                if (
                    process["lease_id"] == lease_id
                    and process["execution_status"] == "running"
                )
            ],
            key=lambda item: item["process_id"],
        )

    def start_process(self, lease_id, payload):
        self.calls.append((
            "start_process",
            {"lease_id": lease_id, **dict(payload)},
        ))
        lease = self.leases[lease_id]
        process_id = str(payload["request_id"])
        process = {
            "process_id": process_id,
            "lease_id": lease_id,
            "workspace_id": lease["workspace_id"],
            "profile_id": lease["profile_id"],
            "controller_epoch": lease["controller_epoch"],
            "execution_status": "running",
            "process_state": "running",
            "exit_code": None,
            "termination_reason": "",
            "next_cursor": "v1:0:0",
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "cpu_time_ms": 0,
            "peak_memory_bytes": 0,
            "lease_recycled": False,
            "affected_process_ids": [],
            "stdout": "",
            "stderr": "",
        }
        self.processes[process_id] = process
        process["active_processes"] = self._active(lease_id)
        return self._response(dict(process))

    def get_process(self, process_id, *, cursor=""):
        self.calls.append((
            "get_process",
            {"process_id": process_id, "cursor": cursor},
        ))
        process = dict(self.processes[process_id])
        process.pop("stdout", None)
        process.pop("stderr", None)
        process["stdout_delta"] = "done\n"
        process["stderr_delta"] = ""
        process["next_cursor"] = "v1:5:0"
        process["stdout_bytes"] = 5
        process["active_processes"] = self._active(
            process["lease_id"]
        )
        return self._response(process)

    def write_process_stdin(self, process_id, payload):
        self.calls.append((
            "write_process_stdin",
            {"process_id": process_id, **dict(payload)},
        ))
        process = self.processes[process_id]
        return self._response({
            "process_id": process_id,
            "lease_id": process["lease_id"],
            "workspace_id": process["workspace_id"],
            "profile_id": process["profile_id"],
            "controller_epoch": process["controller_epoch"],
            "execution_status": process["execution_status"],
            "written_bytes": len(str(payload["chars"]).encode()),
            "active_processes": self._active(
                process["lease_id"]
            ),
        })

    def terminate_process(self, process_id, *, request_id):
        self.calls.append((
            "terminate_process",
            {
                "process_id": process_id,
                "request_id": request_id,
            },
        ))
        target = self.processes[process_id]
        lease_id = target["lease_id"]
        affected = sorted(
            item_id
            for item_id, item in self.processes.items()
            if (
                item["lease_id"] == lease_id
                and item["execution_status"] == "running"
            )
        )
        for item_id in affected:
            item = self.processes[item_id]
            item.update({
                "execution_status": "cancelled",
                "process_state": "lost",
                "exit_code": None,
                "termination_reason": "cancelled",
                "lease_recycled": True,
                "affected_process_ids": affected,
                "active_processes": [],
                "termination_scope": "lease",
                "workspace_preserved": True,
                "runtime_preserved": True,
            })
        response = dict(self.processes[process_id])
        response["active_processes"] = []
        return self._response(response)

    def complete(self, process_id: str, *, exit_code: int = 0):
        process = self.processes[process_id]
        process.update({
            "execution_status": (
                "completed" if exit_code == 0 else "failed"
            ),
            "process_state": "exited",
            "exit_code": exit_code,
            "termination_reason": (
                "completed" if exit_code == 0 else "nonzero_exit"
            ),
            "affected_process_ids": [process_id],
        })


def _setup(
    db_session,
    monkeypatch,
    request,
    tmp_path,
    *,
    suffix: str,
):
    workspace_id, grant, old_lease = _prepare_developer_workspace(
        db_session,
        suffix=suffix,
    )
    old_lease.status = "stopped"
    runtime_binding = db_session.get(
        WorkspaceRuntimeQuotaBinding,
        workspace_id,
    )
    workspace_binding = db_session.get(
        WorkspaceQuotaBinding,
        workspace_id,
    )
    maintenance = db_session.get(
        WorkspaceMaintenanceState,
        workspace_id,
    )
    runtime_binding.desired_quota_bytes = 10 * 1024 * MIB
    runtime_binding.applied_quota_bytes = 10 * 1024 * MIB
    runtime_binding.status = "applied"
    runtime_binding.generation = workspace_binding.generation
    maintenance.status = "ready"
    maintenance.generation = workspace_binding.generation
    maintenance.applied_quota_generation = workspace_binding.generation
    db_session.add_all([
        SystemSetting(key="sandbox.enabled", value="true"),
        SystemSetting(key="sandbox.exec_enabled", value="true"),
    ])
    db_session.commit()
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_SESSION_EXECUTION_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE",
        str(_manifest(tmp_path)),
    )
    settings.invalidate()
    request.addfinalizer(settings.invalidate)
    registry = load_execution_profile_registry()
    access = SandboxAccessPolicy(db_session).evaluate(
        "sandbox_exec",
        platform=grant.platform,
        chat_type=grant.chat_type,
        session_id=grant.external_session_id,
    )
    assert access.allowed is True
    backend = _ProcessBackend(registry)
    service = SandboxProcessService(
        db_session,
        backend,
        profile_registry=registry,
        run_session_factory=_factory(db_session),
    )
    return workspace_id, grant, access, backend, service


def test_process_start_and_poll_finish_run_without_model_poll_zombie(
    db_session,
    monkeypatch,
    request,
    tmp_path,
):
    workspace_id, _grant, access, backend, service = _setup(
        db_session,
        monkeypatch,
        request,
        tmp_path,
        suffix="process-start",
    )

    started = service.start(
        access,
        {
            "command": "python -m pytest tests/ -v",
            "cwd": "repos/project",
            "yield_time_ms": 10,
            "timeout_seconds": 300,
        },
    )
    process_id = started["data"]["process_id"]
    row = db_session.get(SandboxRun, process_id)

    assert started["data"]["execution_status"] == "running"
    assert row.workspace_id == workspace_id
    assert row.execution_mode == "lease"
    assert row.process_state == "running"
    assert row.status == "running"
    start_call = [
        payload
        for operation, payload in backend.calls
        if operation == "start_process"
    ][0]
    assert set(start_call) == {
        "lease_id",
        "request_id",
        "command",
        "cwd",
        "yield_time_ms",
        "timeout_seconds",
    }
    assert "workspace_id" not in start_call
    assert "profile_id" not in start_call

    backend.complete(process_id)
    finished = service.poll(
        access,
        process_id,
        cursor="v1:0:0",
    )

    db_session.expire_all()
    settled = db_session.get(SandboxRun, process_id)
    assert finished["data"]["execution_status"] == "completed"
    assert finished["data"]["stdout_delta"] == "done\n"
    assert settled.status == "completed"
    assert settled.process_state == "exited"
    assert settled.termination_reason == "completed"
    assert settled.stdout_bytes == 5
    assert settled.finished_at is not None


def test_process_service_rejects_malformed_controller_response(
    db_session,
    monkeypatch,
    request,
    tmp_path,
):
    _workspace_id, _grant, access, backend, service = _setup(
        db_session,
        monkeypatch,
        request,
        tmp_path,
        suffix="process-malformed-response",
    )
    process_id = service.start(
        access,
        {
            "command": "true",
            "yield_time_ms": 0,
            "timeout_seconds": 30,
        },
    )["data"]["process_id"]
    backend.complete(process_id)
    backend.processes[process_id]["stdout_truncated"] = "false"

    with pytest.raises(SandboxServiceError) as rejected:
        service.poll(access, process_id)

    assert rejected.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE
    db_session.expire_all()
    run = db_session.get(SandboxRun, process_id)
    assert run.status == "running"
    assert run.process_state == "running"


def test_process_object_access_denies_other_session_workspace_and_old_lease(
    db_session,
    monkeypatch,
    request,
    tmp_path,
):
    workspace_id, grant, access, backend, service = _setup(
        db_session,
        monkeypatch,
        request,
        tmp_path,
        suffix="process-owner",
    )
    process_id = service.start(
        access,
        {
            "command": "sleep 30",
            "yield_time_ms": 0,
            "timeout_seconds": 30,
        },
    )["data"]["process_id"]
    initial_calls = len(backend.calls)

    wrong_session = replace(
        access,
        identity=replace(
            access.identity,
            external_session_id="other-session",
            chat_stream_id="qq:other-session:private",
        ),
    )
    wrong_workspace = replace(access, workspace_id=str(uuid4()))
    for invalid_access in (wrong_session, wrong_workspace):
        with pytest.raises(SandboxServiceError) as denied:
            service.poll(invalid_access, process_id)
        assert denied.value.code is SandboxErrorCode.AUTHORIZATION_FAILED

    other_workspace = Workspace(
        id=str(uuid4()),
        platform="qq",
        owner_type="user",
        owner_id="other-process-owner",
        name="default",
        status="active",
        quota_bytes=64 * MIB,
        used_bytes=0,
    )
    other_grant = SandboxAccessGrant(
        id=str(uuid4()),
        chat_stream_id="qq:other-process-owner:private",
        platform="qq",
        chat_type="private",
        external_session_id="other-process-owner",
        workspace_id=other_workspace.id,
        capability_level="exec",
        execution_profile="developer",
        status="active",
        version=1,
    )
    other_lease = SandboxLease(
        lease_id="sbxlease_other_process_owner",
        lease_key="f" * 64,
        grant_id=other_grant.id,
        chat_stream_id=other_grant.chat_stream_id,
        workspace_id=other_workspace.id,
        profile_id="developer",
        catalog_generation=service.profile_registry.catalog_generation,
        policy_sha256=service.profile_registry.policy_sha256,
        status="active",
        image_digest=IMAGE_ID,
        controller_epoch=backend.controller_epoch,
    )
    other_run = SandboxRun(
        run_id="sbxrun_other_process_owner",
        request_id="sbxrun_other_process_owner",
        workspace_id=other_workspace.id,
        lease_id=other_lease.lease_id,
        profile_id="developer",
        execution_mode="lease",
        process_state="running",
        image_digest=IMAGE_ID,
        status="running",
    )
    db_session.add(other_workspace)
    db_session.flush()
    db_session.add(other_grant)
    db_session.flush()
    db_session.add(other_lease)
    db_session.flush()
    db_session.add(other_run)
    db_session.commit()

    with pytest.raises(SandboxServiceError) as cross_owner:
        service.poll(access, other_run.run_id)
    assert cross_owner.value.code is SandboxErrorCode.AUTHORIZATION_FAILED

    current_run = db_session.get(SandboxRun, process_id)
    current_lease = db_session.get(SandboxLease, current_run.lease_id)
    current_lease.status = "stopped"
    db_session.commit()
    with pytest.raises(SandboxServiceError) as old_lease:
        service.poll(access, process_id)
    assert old_lease.value.code is SandboxErrorCode.AUTHORIZATION_FAILED
    assert len(backend.calls) == initial_calls

    db_session.expire_all()
    current_grant = db_session.get(SandboxAccessGrant, grant.id)
    current_grant.execution_profile = "restricted"
    current_grant.version += 1
    db_session.commit()
    with pytest.raises(SandboxServiceError) as changed_profile:
        service.write_stdin(access, process_id, chars="q\n")
    assert changed_profile.value.code is SandboxErrorCode.AUTHORIZATION_FAILED


def test_terminate_settles_all_runs_and_next_start_lazily_rebuilds_lease(
    db_session,
    monkeypatch,
    request,
    tmp_path,
):
    _workspace_id, _grant, access, backend, service = _setup(
        db_session,
        monkeypatch,
        request,
        tmp_path,
        suffix="process-terminate",
    )
    first_id = service.start(
        access,
        {
            "command": "sleep 30",
            "yield_time_ms": 0,
            "timeout_seconds": 30,
        },
    )["data"]["process_id"]
    second_id = service.start(
        access,
        {
            "command": "python -m http.server 8000",
            "yield_time_ms": 0,
            "timeout_seconds": 30,
        },
    )["data"]["process_id"]
    first_lease_id = db_session.get(SandboxRun, first_id).lease_id

    terminated = service.terminate(access, first_id)
    terminate_calls = len([
        call
        for call in backend.calls
        if call[0] == "terminate_process"
    ])
    repeated = service.terminate(access, first_id)

    assert terminated["data"]["termination_scope"] == "lease"
    assert terminated["data"]["affected_process_ids"] == sorted(
        [first_id, second_id]
    )
    assert repeated["data"]["lease_recycled"] is True
    assert len([
        call
        for call in backend.calls
        if call[0] == "terminate_process"
    ]) == terminate_calls
    db_session.expire_all()
    first = db_session.get(SandboxRun, first_id)
    second = db_session.get(SandboxRun, second_id)
    old_lease = db_session.get(SandboxLease, first_lease_id)
    assert first.status == "cancelled"
    assert second.status == "cancelled"
    assert first.process_state == "lost"
    assert second.process_state == "lost"
    assert old_lease.status == "stopped"

    next_started = service.start(
        access,
        {
            "command": "true",
            "yield_time_ms": 0,
            "timeout_seconds": 30,
        },
    )
    next_run = db_session.get(
        SandboxRun,
        next_started["data"]["process_id"],
    )
    assert next_run.lease_id != first_lease_id


def test_terminate_after_natural_exit_settles_ledger_idempotently(
    db_session,
    monkeypatch,
    request,
    tmp_path,
):
    _workspace_id, _grant, access, backend, service = _setup(
        db_session,
        monkeypatch,
        request,
        tmp_path,
        suffix="terminate-exited",
    )
    process_id = service.start(
        access,
        {
            "command": "sleep 1",
            "yield_time_ms": 0,
            "timeout_seconds": 30,
        },
    )["data"]["process_id"]
    backend.complete(process_id, exit_code=0)

    terminated = service.terminate(access, process_id)

    assert terminated["status"] == "success"
    assert terminated["data"]["execution_status"] == "completed"
    assert terminated["data"]["lease_recycled"] is False
    db_session.expire_all()
    row = db_session.get(SandboxRun, process_id)
    assert row.status == "completed"
    assert row.termination_reason == "completed"
    assert row.process_state == "exited"

    repeated = service.terminate(access, process_id)
    assert repeated["data"]["execution_status"] == "completed"
    assert len([
        call
        for call in backend.calls
        if call[0] == "terminate_process"
    ]) == 1
