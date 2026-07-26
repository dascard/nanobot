import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.sandbox.backend import FakeSandboxBackend
from core.sandbox.access_contracts import LEASE_PROCESS_TOOL_NAMES
from core.sandbox.paths import SandboxStorageLayout
from core.sandbox.tool_service import SandboxToolService
from core.tool_registry import SANDBOX_TOOL_NAMES
from tests.async_helpers import run_async


def test_sandbox_tool_implementation_is_owned_by_kt_adapter_layer():
    from creatures.nanobot.prompts.skills.sandbox.tool import (
        SandboxExecTool as LegacySandboxExecTool,
    )
    from nanobot_kt.tools.sandbox import SandboxExecTool

    assert LegacySandboxExecTool is SandboxExecTool
    assert SandboxExecTool.__module__ == "nanobot_kt.tools.sandbox"


IMAGE_ID = "sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def _sandbox_infrastructure_ceiling(monkeypatch):
    from core.settings_service import settings

    monkeypatch.setenv(
        "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED",
        "true",
    )
    settings.invalidate()
    try:
        yield
    finally:
        settings.invalidate()


def _set_setting(db, key: str, value: object) -> None:
    from core.database import SystemSetting

    db.add(SystemSetting(key=key, value=str(value).lower()))


def _enable_sandbox(db, *, exec_enabled: bool = False) -> None:
    from core.database import (
        SandboxAccessGrant,
        Workspace,
        WorkspaceQuotaBinding,
        WorkspaceRuntimeQuotaBinding,
    )

    _set_setting(db, "sandbox.enabled", True)
    _set_setting(db, "sandbox.exec_enabled", exec_enabled)
    _set_setting(db, "sandbox.asset_token_secret", "s" * 32)
    grant_id = str(uuid4())
    workspace_id = str(uuid4())
    quota_bytes = 2 * 1024 * 1024 * 1024
    db.add(Workspace(
        id=workspace_id,
        platform="qq",
        owner_type="user",
        owner_id=grant_id,
        name="default",
        status="active",
        quota_bytes=quota_bytes,
        used_bytes=0,
    ))
    db.flush()
    db.add_all([
        WorkspaceQuotaBinding(
            workspace_id=workspace_id,
            project_id=10000,
            desired_quota_bytes=quota_bytes,
            applied_quota_bytes=quota_bytes,
            status="applied",
            generation=1,
        ),
        WorkspaceRuntimeQuotaBinding(
            workspace_id=workspace_id,
            project_id=10001,
            desired_quota_bytes=512 * 1024 * 1024,
            applied_quota_bytes=512 * 1024 * 1024,
            status="applied",
            generation=1,
        ),
        SandboxAccessGrant(
            id=grant_id,
            chat_stream_id="qq:super-1:private",
            platform="qq",
            chat_type="private",
            external_session_id="super-1",
            workspace_id=workspace_id,
            capability_level="exec",
            status="active",
            version=1,
        ),
    ])
    db.commit()


def _context(*, user_id: str = "super-1", superuser: bool = True):
    runtime_chat_type = "private_superuser" if superuser else "private"
    return SimpleNamespace(
        session=SimpleNamespace(
            extra={
                "nanobot_runtime_context": {
                    "chat_type": "private",
                    "runtime_chat_type": runtime_chat_type,
                    "is_super_user": superuser,
                    "user_id": user_id,
                    "group_id": "",
                    "platform": "qq",
                    "session_id": f"private_{user_id}",
                }
            }
        )
    )


def _tool_factory(tool_class, backend):
    return tool_class(
        service_factory=lambda db: SandboxToolService(db, backend),
    )


def test_sandbox_feature_flags_and_allowlist_control_wire_schema(db_session):
    from core.database import SystemSetting, ToolOverride
    from core.tool_plan import build_tool_plan

    disabled = build_tool_plan(
        chat_type="private_superuser",
        user_id="root",
        platform="qq",
        session_id="private_super-1",
        runtime_preset="full",
        db=db_session,
    )
    assert not SANDBOX_TOOL_NAMES & disabled.sent_tool_names

    _enable_sandbox(db_session, exec_enabled=False)
    workspace_only = build_tool_plan(
        chat_type="private_superuser",
        user_id="root",
        platform="qq",
        session_id="private_super-1",
        runtime_preset="full",
        db=db_session,
    )
    exec_tools = {"sandbox_exec", *LEASE_PROCESS_TOOL_NAMES}
    assert SANDBOX_TOOL_NAMES - exec_tools <= workspace_only.sent_tool_names
    assert not exec_tools & workspace_only.sent_tool_names

    exec_row = db_session.query(SystemSetting).filter_by(
        key="sandbox.exec_enabled",
    ).one()
    exec_row.value = "true"
    db_session.commit()
    all_enabled = build_tool_plan(
        chat_type="private_superuser",
        user_id="root",
        platform="qq",
        session_id="private_super-1",
        runtime_preset="full",
        db=db_session,
    )
    assert (
        SANDBOX_TOOL_NAMES - LEASE_PROCESS_TOOL_NAMES
        <= all_enabled.sent_tool_names
    )
    assert not LEASE_PROCESS_TOOL_NAMES & all_enabled.sent_tool_names

    ordinary = build_tool_plan(
        chat_type="private",
        user_id="user-1",
        platform="qq",
        session_id="private_user-1",
        runtime_preset="full",
        db=db_session,
    )
    assert not SANDBOX_TOOL_NAMES & ordinary.sent_tool_names

    db_session.add(ToolOverride(
        tool_name="workspace_read",
        scope_type="user",
        scope_id="user-1",
        enabled=1,
        reason="Sandbox 灰度 allowlist",
    ))
    db_session.commit()
    allowlisted = build_tool_plan(
        chat_type="private",
        user_id="user-1",
        platform="qq",
        session_id="private_user-1",
        runtime_preset="full",
        db=db_session,
    )
    assert not SANDBOX_TOOL_NAMES & allowlisted.sent_tool_names


def test_sandbox_schemas_are_strict_and_have_no_background_or_identity_fields():
    from core.tool_schema_preview import build_tool_schema

    forbidden = {
        "run_in_background",
        "user_id",
        "owner_id",
        "workspace_id",
        "image",
        "network",
        "volume",
        "devices",
        "capabilities",
    }
    for tool_name in SANDBOX_TOOL_NAMES:
        parameters = build_tool_schema(tool_name)["function"]["parameters"]
        assert parameters["additionalProperties"] is False
        assert not forbidden & set(parameters["properties"])


def test_old_host_file_tools_and_memory_subagents_are_removed_and_hard_disabled():
    from core.tool_registry import LEGACY_FILE_TOOL_NAMES, get_tool_def

    config = Path("creatures/nanobot/config.yaml").read_text(encoding="utf-8")
    for tool_name in LEGACY_FILE_TOOL_NAMES:
        assert get_tool_def(tool_name).force_disabled is True
        assert f"- name: {tool_name}\n" not in config


def test_workspace_write_uses_trusted_context_and_stable_envelope(db_session):
    from creatures.nanobot.prompts.skills.sandbox.tool import WorkspaceWriteTool

    _enable_sandbox(db_session)
    backend = FakeSandboxBackend()
    backend.set_response("write_file", {
        "status": "success",
        "summary": "文件写入完成",
        "next_actions": [],
        "artifacts": [{
            "type": "workspace_file",
            "ref": "workspace://current/result.txt",
            "path": "result.txt",
            "size_bytes": 6,
        }],
        "data": {
            "path": "result.txt",
            "size_bytes": 6,
            "previous_size_bytes": 0,
            "used_bytes": 6,
            "usage_delta_bytes": 6,
        },
    })
    tool = _tool_factory(WorkspaceWriteTool, backend)

    result = run_async(tool.execute(
        {"path": "result.txt", "content": "结果", "overwrite": False},
        context=_context(),
    ))
    body = json.loads(result.output)

    assert result.success
    assert body["status"] == "success"
    write_payload = next(payload for name, payload in backend.calls if name == "write_file")
    assert write_payload["path"] == "result.txt"
    assert write_payload["content"] == "结果"
    assert len(write_payload["workspace_id"]) == 36
    assert "owner_id" not in write_payload


@pytest.mark.parametrize(
    "forbidden_field",
    ["owner_id", "workspace_id", "user_id", "image", "network", "volume"],
)
def test_sandbox_exec_rejects_model_controlled_identity_and_docker_fields(
    db_session,
    forbidden_field,
):
    from creatures.nanobot.prompts.skills.sandbox.tool import SandboxExecTool

    _enable_sandbox(db_session, exec_enabled=True)
    backend = FakeSandboxBackend()
    tool = _tool_factory(SandboxExecTool, backend)
    result = run_async(tool.execute(
        {"command": "true", forbidden_field: "attacker-value"},
        context=_context(),
    ))
    body = json.loads(result.output)

    assert body["status"] == "error"
    assert body["error"]["code"] == "authorization_failed"
    assert backend.calls == []
    assert "attacker-value" not in result.output


def test_sandbox_exec_records_exact_trace_links_and_resource_summary(db_session):
    from core.database import SandboxRun, Workspace
    from core.tracing_context import (
        reset_tool_trace_context,
        reset_trace_context,
        set_tool_trace_context,
        set_trace_context,
    )
    from creatures.nanobot.prompts.skills.sandbox.tool import SandboxExecTool

    _enable_sandbox(db_session, exec_enabled=True)
    backend = FakeSandboxBackend()
    backend.set_response("ready", {
        "status": "success",
        "summary": "sandboxd 已就绪",
        "next_actions": [],
        "artifacts": [],
        "data": {"image_id": IMAGE_ID},
    })
    backend.set_response("run", {
        "status": "success",
        "summary": "Sandbox 执行完成",
        "next_actions": [],
        "artifacts": [],
        "data": {
            "exit_code": 0,
            "termination_reason": "completed",
            "oom_killed": False,
            "cpu_time_ms": 12,
            "peak_memory_bytes": 4096,
            "stdout": "ok\n",
            "stderr": "",
            "stdout_bytes": 3,
            "stderr_bytes": 0,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "workspace_used_bytes": 9,
            "workspace_usage_delta_bytes": 9,
        },
    })
    tool = _tool_factory(SandboxExecTool, backend)
    trace_tokens = set_trace_context("trace-1", "agent-run-1")
    tool_token = set_tool_trace_context("tool-call-1")
    try:
        result = run_async(tool.execute(
            {"command": "python -c 'print(1)'", "cwd": "", "timeout_seconds": 30},
            context=_context(),
        ))
    finally:
        reset_tool_trace_context(tool_token)
        reset_trace_context(trace_tokens)

    assert json.loads(result.output)["status"] == "success"
    db_session.rollback()
    row = db_session.query(SandboxRun).one()
    assert row.trace_id == "trace-1"
    assert row.agent_run_id == "agent-run-1"
    assert row.tool_call_id == "tool-call-1"
    assert row.image_digest == IMAGE_ID
    assert row.status == "completed"
    assert row.exit_code == 0
    assert row.stdout_bytes == 3
    assert row.peak_memory_bytes == 4096
    workspace = db_session.query(Workspace).filter_by(id=row.workspace_id).one()
    assert workspace.used_bytes == 9
    run_payload = next(payload for name, payload in backend.calls if name == "run")
    assert run_payload["run_id"].startswith("sbxrun_")
    assert run_payload["request_id"].startswith("sbxreq_")
    assert not {"image", "network", "volume", "user_id"} & set(run_payload)


def test_sandbox_exec_records_nonzero_exit_as_failed(db_session):
    from core.database import SandboxRun
    from creatures.nanobot.prompts.skills.sandbox.tool import SandboxExecTool

    _enable_sandbox(db_session, exec_enabled=True)
    backend = FakeSandboxBackend()
    backend.set_response("ready", {
        "status": "success",
        "summary": "sandboxd 已就绪",
        "next_actions": [],
        "artifacts": [],
        "data": {"image_id": IMAGE_ID},
    })
    backend.set_response("run", {
        "status": "success",
        "summary": "Sandbox 执行完成",
        "next_actions": [],
        "artifacts": [],
        "data": {
            "exit_code": 2,
            "termination_reason": "nonzero_exit",
            "stdout_bytes": 0,
            "stderr_bytes": 5,
            "workspace_used_bytes": 0,
            "workspace_usage_delta_bytes": 0,
        },
    })

    result = run_async(_tool_factory(SandboxExecTool, backend).execute(
        {"command": "exit 2"},
        context=_context(),
    ))

    assert json.loads(result.output)["data"]["exit_code"] == 2
    db_session.expire_all()
    row = db_session.query(SandboxRun).one()
    assert row.status == "failed"
    assert row.termination_reason == "nonzero_exit"


def test_sandbox_exec_uses_manifest_profile_image_when_legacy_absent(db_session):
    from core.database import SandboxRun
    from creatures.nanobot.prompts.skills.sandbox.tool import SandboxExecTool

    _enable_sandbox(db_session, exec_enabled=True)
    backend = FakeSandboxBackend()
    backend.set_response("ready", {
        "status": "success",
        "summary": "sandboxd 已就绪",
        "next_actions": [],
        "artifacts": [],
        "data": {
            "image_id": "",
            "profiles": {
                "restricted": {"ready": True, "image_id": IMAGE_ID},
            },
        },
    })
    backend.set_response("run", {
        "status": "success",
        "summary": "Sandbox 执行完成",
        "next_actions": [],
        "artifacts": [],
        "data": {
            "exit_code": 0,
            "termination_reason": "completed",
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "workspace_used_bytes": 0,
            "workspace_usage_delta_bytes": 0,
        },
    })

    result = run_async(_tool_factory(SandboxExecTool, backend).execute(
        {"command": "true"},
        context=_context(),
    ))

    assert json.loads(result.output)["status"] == "success"
    db_session.expire_all()
    row = db_session.query(SandboxRun).one()
    assert row.image_digest == IMAGE_ID
    assert row.status == "completed"


def test_sandbox_exec_failure_terminal_survives_outer_uow_rollback(db_session):
    from core.database import SandboxRun
    from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
    from creatures.nanobot.prompts.skills.sandbox.tool import SandboxExecTool

    _enable_sandbox(db_session, exec_enabled=True)
    backend = FakeSandboxBackend()
    backend.set_response("ready", {
        "status": "success",
        "summary": "sandboxd 已就绪",
        "next_actions": [],
        "artifacts": [],
        "data": {"image_id": IMAGE_ID},
    })

    def fail_run(_payload):
        raise SandboxServiceError(
            SandboxErrorCode.EXECUTION_TIMEOUT,
            "Sandbox 执行超时，整个容器已终止",
        )

    backend.run = fail_run
    result = run_async(_tool_factory(SandboxExecTool, backend).execute(
        {"command": "sleep 999"},
        context=_context(),
    ))

    assert json.loads(result.output)["error"]["code"] == "execution_timeout"
    db_session.expire_all()
    row = db_session.query(SandboxRun).one()
    assert row.status == "failed"
    assert row.termination_reason == "execution_timeout"


def test_run_ledger_retries_terminal_commit_and_does_not_leave_running(
    db_session,
):
    from sqlalchemy.orm import sessionmaker

    from core.database import SandboxRun
    from core.sandbox.identity import Principal
    from core.sandbox.run_ledger import SandboxRunLedger
    from core.sandbox.workspace_service import WorkspaceService

    workspace = WorkspaceService(db_session).ensure_default(
        Principal(platform="qq", owner_type="user", owner_id="owner-A"),
    )
    db_session.add(SandboxRun(
        run_id="sbxrun_retry",
        request_id="sbxreq_retry",
        workspace_id=workspace.id,
        image_digest=IMAGE_ID,
        status="running",
    ))
    db_session.commit()
    base_factory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
    )
    state = {"remaining_failures": 1, "commit_calls": 0}

    def flaky_factory():
        session = base_factory()
        commit = session.commit

        def flaky_commit():
            state["commit_calls"] += 1
            if state["remaining_failures"]:
                state["remaining_failures"] -= 1
                raise RuntimeError("模拟终态提交失败")
            commit()

        session.commit = flaky_commit
        return session

    SandboxRunLedger(flaky_factory).mark_failed(
        "sbxrun_retry",
        termination_reason="runtime_unavailable",
    )

    db_session.expire_all()
    row = db_session.get(SandboxRun, "sbxrun_retry")
    assert state["commit_calls"] == 2
    assert row.status == "failed"
    assert row.termination_reason == "runtime_unavailable"


def test_asset_publish_registers_authorized_immutable_reference(db_session):
    from core.database import Asset, WorkspaceAsset
    from creatures.nanobot.prompts.skills.sandbox.tool import AssetPublishTool

    _enable_sandbox(db_session)
    digest = "b" * 64
    backend = FakeSandboxBackend()
    backend.set_response("publish_asset", {
        "status": "success",
        "summary": "资产发布完成",
        "next_actions": [],
        "artifacts": [],
        "data": {
            "sha256": digest,
            "size_bytes": 128,
            "media_type": "text/csv",
            "storage_key": SandboxStorageLayout.asset_storage_key(digest),
        },
    })
    tool = _tool_factory(AssetPublishTool, backend)
    result = run_async(tool.execute(
        {"path": "results/report.csv", "media_type": "text/csv"},
        context=_context(),
    ))
    body = json.loads(result.output)

    assert body["status"] == "success"
    assert body["data"]["ref"] == f"asset://sha256/{digest}"
    assert body["data"]["transport_token"]
    assert body["data"]["reply_token"] == (
        f"[asset_download:{body['data']['transport_token']}]"
    )
    assert body["data"]["recipient_type"] == "session"
    assert body["data"]["recipient_id"] == "qq:super-1:private"
    assert body["data"]["expires_at"] > 0
    assert body["artifacts"][0]["transport_token"] == body["data"]["transport_token"]
    assert body["artifacts"][0]["reply_token"] == body["data"]["reply_token"]
    assert db_session.query(Asset).filter_by(sha256=digest).count() == 1
    assert db_session.query(WorkspaceAsset).filter_by(asset_sha256=digest).count() == 1
