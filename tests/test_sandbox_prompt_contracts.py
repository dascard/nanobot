from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from core.database import (
    SandboxAccessGrant,
    SystemSetting,
    Workspace,
    WorkspaceQuotaBinding,
    WorkspaceRuntimeQuotaBinding,
)
from core.settings_service import settings


MIB = 1024 * 1024


@pytest.fixture(autouse=True)
def _sandbox_infrastructure_ceiling(monkeypatch):
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE",
        str(Path("config/sandbox-execution-profiles.v1.json").resolve()),
    )
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
    settings.invalidate()
    try:
        yield
    finally:
        settings.invalidate()


def _grant(db, *, suffix: str, profile_id: str) -> str:
    workspace_id = str(uuid4())
    session_id = f"prompt-{suffix}"
    runtime_quota = (
        10 * 1024 * MIB
        if profile_id == "developer"
        else 512 * MIB
    )
    db.add(Workspace(
        id=workspace_id,
        platform="qq",
        owner_type="user",
        owner_id=session_id,
        name="default",
        status="active",
        quota_bytes=32 * MIB,
        used_bytes=0,
    ))
    db.flush()
    db.add_all([
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
            desired_quota_bytes=runtime_quota,
            applied_quota_bytes=runtime_quota,
            status="applied",
            generation=1,
        ),
        SandboxAccessGrant(
            id=str(uuid4()),
            chat_stream_id=f"qq:{session_id}:private",
            platform="qq",
            chat_type="private",
            external_session_id=session_id,
            workspace_id=workspace_id,
            capability_level="exec",
            execution_profile=profile_id,
            status="active",
            version=1,
        ),
        SystemSetting(key="sandbox.enabled", value="true"),
        SystemSetting(key="sandbox.exec_enabled", value="true"),
    ])
    db.commit()
    return session_id


def _plan(db, *, suffix: str, profile_id: str):
    from core.tool_plan import build_tool_plan

    session_id = _grant(db, suffix=suffix, profile_id=profile_id)
    return build_tool_plan(
        chat_type="private",
        platform="qq",
        session_id=f"private_{session_id}",
        runtime_preset="full",
        db=db,
    )


def test_restricted_schema_uses_actual_oneshot_profile_capabilities(
    db_session,
):
    plan = _plan(
        db_session,
        suffix="restricted",
        profile_id="restricted",
    )
    schema_guidance = next(
        schema["function"]["description"]
        for schema in plan.sent_tool_schemas
        if schema["function"]["name"] == "sandbox_exec"
    )

    assert plan.runtime_tool_prompt == ""
    assert "当前 Profile：restricted" in schema_guidance
    assert "单条命令最大时长：120 秒" in schema_guidance
    assert "无网络（network=none）" in schema_guidance
    assert "公网可用" not in schema_guidance
    assert "命令与文件写并发时不保证一致快照" in schema_guidance
    assert "一次性执行 Profile" in schema_guidance
    assert "不支持长进程、detached、stdin" in schema_guidance
    assert "sandbox_poll" not in plan.sent_tool_names
    assert "sandbox_write_stdin" not in plan.sent_tool_names
    assert "sandbox_terminate" not in plan.sent_tool_names
    assert "workspace_edit" in plan.sent_tool_names
    assert "workspace_apply_patch" not in plan.sent_tool_names
    assert "workspace_list" not in plan.sent_tool_names


def test_developer_schema_explains_lease_shell_storage_and_termination(
    db_session,
):
    plan = _plan(
        db_session,
        suffix="developer",
        profile_id="developer",
    )
    schema_guidance = next(
        schema["function"]["description"]
        for schema in plan.sent_tool_schemas
        if schema["function"]["name"] == "sandbox_exec"
    )

    assert plan.runtime_tool_prompt == ""
    assert "当前 Profile：developer" in schema_guidance
    assert "单条命令最大时长：1800 秒" in schema_guidance
    assert "只能经受控代理访问以下域名" in schema_guidance
    assert "github.com" in schema_guidance
    assert "codeload.github.com" in schema_guidance
    assert "registry.npmjs.org" in schema_guidance
    assert "IP 直连、私网、宿主和其他容器均不可达" in schema_guidance
    assert "HTTP_PROXY/HTTPS_PROXY 只负责应用选路，不是安全边界" in schema_guidance
    assert "公网可用" not in schema_guidance
    assert "git" in schema_guidance
    assert "rg" in schema_guidance
    assert "pytest" in schema_guidance
    assert "Docker 不可用" in schema_guidance
    assert "root 或 sudo" in schema_guidance
    assert "支持；detached 后台进程：不支持；stdin：支持" in schema_guidance
    assert "命令与文件写并发时不保证一致快照" in schema_guidance
    assert "前台命令" in schema_guidance
    assert "yield_time_ms" in schema_guidance
    assert "不要使用 `cmd &`" in schema_guidance
    assert "当前 Lease" in schema_guidance
    assert "全部活动进程" in schema_guidance
    assert "/workspace 与 /runtime 保留" in schema_guidance
    assert "/tmp 和全部旧 process_id 失效" in schema_guidance
    assert "新的 `/bin/bash -lc`" in schema_guidance
    assert "`cd`、`export`、`alias`、`source activate` 不跨命令保留" in schema_guidance
    assert "cwd" in schema_guidance
    assert "`FOO=bar cmd`" in schema_guidance
    assert "`.venv/bin/python`" in schema_guidance
    assert "HOME=/runtime/home" in schema_guidance
    assert "`~/.profile`" in schema_guidance
    assert "/tmp 只在同一 Lease 内持久" in schema_guidance
    assert {
        "sandbox_exec",
        "sandbox_poll",
        "sandbox_write_stdin",
        "sandbox_terminate",
        "workspace_edit",
    } <= plan.sent_tool_names


def test_default_and_runtime_prompts_are_in_sync():
    """受版本管理的 Prompt 契约必须同时进入 canonical 与宿主 Runtime。"""

    relative_paths = (
        Path("chat/main.md"),
        Path("tasks/group_memory_learning.md"),
        Path("tasks/outreach_generate.md"),
        Path("tasks/outreach_judge.md"),
        Path("tasks/private_decision.md"),
        Path("tools/group_analysis/usage.md"),
        Path("tools/sandbox_exec/usage.md"),
        Path("tools/sql_analysis/usage.md"),
        Path("tools/workspace_read/usage.md"),
        Path("tools/workspace_search/usage.md"),
        Path("tools/workspace_write/usage.md"),
        Path("tools/workspace_edit/usage.md"),
        Path("tools/workspace_list/usage.md"),
    )
    for relative_path in relative_paths:
        default_content = (
            Path("prompts.v2.default") / relative_path
        ).read_text(encoding="utf-8")
        runtime_content = (
            Path("data/prompts_v2") / relative_path
        ).read_text(encoding="utf-8")
        assert runtime_content == default_content, relative_path
