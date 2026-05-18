import json
import asyncio
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, get_db
from server import app


@pytest.fixture
def auth_header(monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    return {"Authorization": "Bearer test-token"}


def test_tracer_records_runs_tools_and_prompt_logs(tmp_path, monkeypatch):
    from core import database
    from core.tracing import PromptTracer, RunTracer, ToolTracer

    engine = create_engine(
        f"sqlite:///{tmp_path / 'trace.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", TestingSessionLocal)

    run = RunTracer.start_run(
        trace_id="trace-a",
        session_id="group_1001",
        user_id="u1",
        run_type="chat",
        prompt_mode="shadow",
        prompt_key="group_chat",
        model="model-a",
        input_preview="你好",
    )
    tool_id = ToolTracer.start_tool_call(
        trace_id="trace-a",
        run_id=run.run_id,
        tool_name="reply",
        args={"text": "hello", "api_key": "secret"},
    )
    ToolTracer.finish_tool_call(tool_id, status="success", result={"ok": True, "text": "done"})
    PromptTracer.record_render(
        trace_id="trace-a",
        run_id=run.run_id,
        prompt_key="group_chat",
        mode="shadow",
        variables={"user_input": "你好"},
        rendered_content="系统提示词不应完整入库",
        token_estimate=8,
        warnings=["unused: x"],
    )
    RunTracer.finish_run(run.run_id, status="success", output_preview="回复", latency_ms=12)

    db = TestingSessionLocal()
    try:
        tool = db.query(database.ToolCall).first()
        assert tool.trace_id == "trace-a"
        assert tool.status == "success"
        assert "secret" not in tool.args_json
        assert json.loads(tool.args_json)["api_key"] == "[REDACTED]"

        log = db.query(database.PromptRenderLog).first()
        assert log.prompt_key == "group_chat"
        assert log.rendered_preview != "系统提示词不应完整入库"
        assert "系统提示词" in log.rendered_preview

        run_row = db.query(database.AgentRun).first()
        assert run_row.status == "success"
        assert run_row.latency_ms == 12
    finally:
        db.close()


def test_executor_records_tool_call_with_contextvars(tmp_path, monkeypatch):
    from core import database
    from core.tool_tracing import install_executor_tracing
    from core.tracing_context import reset_trace_context, set_trace_context
    from kohakuterrarium.core.executor import Executor
    from kohakuterrarium.modules.tool.base import BaseTool, ToolConfig, ToolResult

    engine = create_engine(
        f"sqlite:///{tmp_path / 'executor_trace.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", TestingSessionLocal)

    class EchoTool(BaseTool):
        def __init__(self):
            super().__init__(ToolConfig())

        @property
        def tool_name(self):
            return "echo_trace"

        @property
        def description(self):
            return "echo"

        async def _execute(self, args, **kwargs):
            return ToolResult(output=f"echo:{args['text']}", exit_code=0)

    async def run_tool():
        tokens = set_trace_context("trace-tool", "run-tool")
        try:
            executor = Executor()
            install_executor_tracing(executor)
            executor.register_tool(EchoTool())
            job_id = await executor.submit("echo_trace", {"text": "hi", "token": "secret"}, is_direct=True)
            return await executor.wait_for(job_id)
        finally:
            reset_trace_context(tokens)

    result = asyncio.run(run_tool())
    assert result.output == "echo:hi"

    db = TestingSessionLocal()
    try:
        row = db.query(database.ToolCall).first()
        assert row.trace_id == "trace-tool"
        assert row.run_id == "run-tool"
        assert row.tool_name == "echo_trace"
        assert row.status == "success"
        assert "secret" not in row.args_json
        assert "echo:hi" in row.result_preview
    finally:
        db.close()


def test_admin_prompt_and_trace_endpoints(client, auth_header, tmp_path, monkeypatch):
    from core import database
    from core.tracing import RunTracer, ToolTracer

    prompt_dir = tmp_path / "prompts"
    backup_dir = tmp_path / "backups"
    prompt_dir.mkdir()
    (prompt_dir / "group_chat.md").write_text(
        """---
name: 群聊回复
required_vars:
  - user_input
optional_vars:
  - history_context
---
{{ history_context }}
用户: {{ user_input }}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_DIR", str(prompt_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("api.admin_routes.get_prompt_manager", lambda: __import__("core.prompts", fromlist=["PromptManager"]).PromptManager(prompt_dir=prompt_dir, backup_dir=backup_dir))

    list_resp = client.get("/api/v1/admin/prompts", headers=auth_header)
    assert list_resp.status_code == 200, list_resp.text
    assert list_resp.json()["items"][0]["prompt_key"] == "group_chat"

    preview = client.post(
        "/api/v1/admin/prompts/group_chat/preview",
        json={"variables": {"user_input": "你好", "history_context": "历史"}, "mode": "shadow"},
        headers=auth_header,
    )
    assert preview.status_code == 200, preview.text
    assert "用户: 你好" in preview.json()["content"]

    put_resp = client.put(
        "/api/v1/admin/prompts/group_chat",
        json={"content": "---\nname: 群聊回复\nrequired_vars:\n  - user_input\n---\n更新 {{ user_input }}\n"},
        headers=auth_header,
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["saved"] is True
    assert client.get("/api/v1/admin/prompts/group_chat/history", headers=auth_header).json()["items"]

    run = RunTracer.start_run(
        trace_id="trace-admin",
        session_id="s1",
        user_id="u1",
        run_type="chat",
        prompt_mode="shadow",
        prompt_key="group_chat",
        model="model-a",
        input_preview="输入",
    )
    tool_id = ToolTracer.start_tool_call("trace-admin", run.run_id, "reply", {"text": "ok"})
    ToolTracer.finish_tool_call(tool_id, status="error", error="boom")
    RunTracer.finish_run(run.run_id, status="error", error="boom", finished_at=datetime.now())

    runs_resp = client.get("/api/v1/admin/agent-runs", headers=auth_header)
    assert runs_resp.status_code == 200, runs_resp.text
    assert runs_resp.json()["items"][0]["trace_id"] == "trace-admin"

    detail_resp = client.get(f"/api/v1/admin/agent-runs/{run.run_id}", headers=auth_header)
    assert detail_resp.status_code == 200, detail_resp.text
    assert detail_resp.json()["tool_calls"][0]["tool_name"] == "reply"

    tools_resp = client.get("/api/v1/admin/tool-calls", headers=auth_header)
    assert tools_resp.status_code == 200, tools_resp.text
    assert tools_resp.json()["items"][0]["status"] == "error"
