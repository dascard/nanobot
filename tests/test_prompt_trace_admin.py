import json
import asyncio
from tests.async_helpers import run_async
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


def test_effective_prompt_preview_request_defaults_to_v2():
    from api.admin_routes import EffectivePromptPreviewRequest

    body = EffectivePromptPreviewRequest()

    assert body.engine == "v2"


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
        prompt_source="Legacy runtime prompt",
        prompt_runtime_path="/runtime/prompt.md",
        prompt_default_path="/default/prompt.md",
        prompt_sha256="a" * 64,
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
        prompt_source="PromptManager runtime template",
        prompt_runtime_path="/runtime/group_chat.md",
        prompt_default_path="/default/group_chat.md",
        prompt_sha256="b" * 64,
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
        assert log.prompt_source == "PromptManager runtime template"
        assert log.prompt_runtime_path == "/runtime/group_chat.md"
        assert log.prompt_default_path == "/default/group_chat.md"
        assert log.prompt_sha256 == "b" * 64
        assert log.rendered_preview != "系统提示词不应完整入库"
        assert "系统提示词" in log.rendered_preview

        run_row = db.query(database.AgentRun).first()
        assert run_row.status == "success"
        assert run_row.latency_ms == 12
        assert run_row.prompt_source == "Legacy runtime prompt"
        assert run_row.prompt_sha256 == "a" * 64
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

    result = run_async(run_tool())
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
    from core.tracing import LLMRequestTracer, ReplyContractTracer, RunTracer, ToolTracer

    legacy_prompt = tmp_path / "runtime_prompt.md"
    legacy_prompt.write_text("旧 Prompt 运行时标记 LEGACY_RUNTIME_MARKER", encoding="utf-8")
    monkeypatch.setenv("NANOBOT_LEGACY_PROMPT_OUTPUT", str(legacy_prompt))

    list_resp = client.get("/api/v1/admin/prompts", headers=auth_header)
    assert list_resp.status_code in {404, 410}, list_resp.text

    effective = client.post(
        "/api/v1/admin/prompt/effective-preview",
        json={
            "engine": "v1",
            "chat_type": "group",
            "session_id": "group_1001",
            "user_id": "u1",
            "group_id": "1001",
            "prompt_key": "group_chat",
            "mode": "managed",
            "user_input": "EFFECTIVE_PROMPT_MARKER",
        },
        headers=auth_header,
    )
    assert effective.status_code == 410, effective.text
    assert "Prompt V1" in effective.text

    effective_v2 = client.post(
        "/api/v1/admin/prompt/effective-preview",
        json={
            "engine": "v2",
            "chat_type": "group",
            "session_id": "group_1001",
            "user_id": "u1",
            "group_id": "1001",
            "user_input": "EFFECTIVE_PROMPT_V2_MARKER",
        },
        headers=auth_header,
    )
    assert effective_v2.status_code == 200, effective_v2.text
    effective_v2_json = effective_v2.json()
    assert effective_v2_json["engine"] == "v2"
    assert effective_v2_json["prompt_key"] == "chat_group"
    assert effective_v2_json["request_json"]["messages"] == effective_v2_json["messages"]
    assert effective_v2_json["request_json"]["tools"] == effective_v2_json["tool_schemas"]
    assert len(effective_v2_json["prompt_sha256"]) == 64
    assert effective_v2_json["section_hashes"]["base_contract"]
    assert "history_message_count" in effective_v2_json["debug"]
    rendered_v2_request = json.dumps(effective_v2_json["request_json"], ensure_ascii=False)
    assert rendered_v2_request.count("EFFECTIVE_PROMPT_V2_MARKER") == 1
    assert rendered_v2_request.count("[RuntimeTool]") == 1
    assert rendered_v2_request.count("<persona_reference") == 1

    variables_resp = client.get(
        "/api/v1/admin/prompt-v2/variables?template=identity_context",
        headers=auth_header,
    )
    assert variables_resp.status_code == 200, variables_resp.text
    variables_json = variables_resp.json()
    variable_names = {item["name"] for item in variables_json["items"]}
    assert "character_name" in variable_names
    assert "name_hint" in variable_names
    assert "alias_names" in variable_names
    assert "super_user_id" in variable_names
    assert "user_input" not in variable_names

    run = RunTracer.start_run(
        trace_id="trace-admin",
        session_id="s1",
        user_id="u1",
        run_type="chat",
        prompt_mode="shadow",
        prompt_key="group_chat",
        prompt_source="Legacy runtime prompt",
        prompt_runtime_path="/runtime/prompt.md",
        prompt_default_path="/default/prompt.md",
        prompt_sha256="c" * 64,
        model="model-a",
        input_preview="输入",
    )
    tool_id = ToolTracer.start_tool_call("trace-admin", run.run_id, "reply", {"text": "ok"})
    ToolTracer.finish_tool_call(tool_id, status="error", error="boom")
    ReplyContractTracer.record_check(
        trace_id="trace-admin",
        run_id=run.run_id,
        session_id="s1",
        attempt=0,
        raw_output="模型输出",
        has_reply_tool=False,
        result="no_tool_call",
    )
    llm_log_id = LLMRequestTracer.record_request(
        trace_id="trace-admin",
        run_id=run.run_id,
        source="replyer",
        model="model-a",
        request={"messages": [{"role": "user", "content": "输入"}]},
    )
    LLMRequestTracer.finish_request(
        log_id=llm_log_id,
        response={"choices": [{"message": {"content": "输出"}}]},
        response_status=200,
        status="success",
        latency_ms=7,
    )
    RunTracer.finish_run(run.run_id, status="error", error="boom", finished_at=datetime.now())

    runs_resp = client.get("/api/v1/admin/agent-runs", headers=auth_header)
    assert runs_resp.status_code == 200, runs_resp.text
    assert runs_resp.json()["items"][0]["trace_id"] == "trace-admin"

    filtered = client.get(
        "/api/v1/admin/agent-runs",
        params={"trace_id": "trace-admin", "user_id": "u1", "prompt_key": "group_chat"},
        headers=auth_header,
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 1

    empty_filtered = client.get(
        "/api/v1/admin/agent-runs",
        params={"trace_id": "missing-trace"},
        headers=auth_header,
    )
    assert empty_filtered.status_code == 200, empty_filtered.text
    assert empty_filtered.json()["total"] == 0

    detail_resp = client.get(f"/api/v1/admin/agent-runs/{run.run_id}", headers=auth_header)
    assert detail_resp.status_code == 200, detail_resp.text
    assert detail_resp.json()["run"]["prompt_source"] == "Legacy runtime prompt"
    assert detail_resp.json()["run"]["prompt_runtime_path"] == "/runtime/prompt.md"
    assert detail_resp.json()["run"]["prompt_default_path"] == "/default/prompt.md"
    assert detail_resp.json()["run"]["prompt_sha256"] == "c" * 64
    assert detail_resp.json()["tool_calls"][0]["tool_name"] == "reply"
    assert detail_resp.json()["reply_contract_check_logs"][0]["result"] == "no_tool_call"
    assert json.loads(detail_resp.json()["llm_api_request_logs"][0]["response_json"])["choices"][0]["message"]["content"] == "输出"
    assert detail_resp.json()["llm_api_request_logs"][0]["response_status"] == 200

    llm_logs_resp = client.get("/api/v1/admin/llm-api-logs", params={"trace_id": "trace-admin"}, headers=auth_header)
    assert llm_logs_resp.status_code == 200, llm_logs_resp.text
    assert llm_logs_resp.json()["stats"]["total"] == 1
    assert llm_logs_resp.json()["stats"]["success"] == 1
    assert llm_logs_resp.json()["stats"]["avg_latency_ms"] == 7
    llm_list_item = llm_logs_resp.json()["items"][0]
    assert llm_list_item["summary_only"] is True
    assert "request_json" not in llm_list_item
    assert "response_json" not in llm_list_item

    llm_detail_resp = client.get(f"/api/v1/admin/llm-api-logs/{llm_log_id}", headers=auth_header)
    assert llm_detail_resp.status_code == 200, llm_detail_resp.text
    assert json.loads(llm_detail_resp.json()["request_json"])["messages"][0]["content"] == "输入"
    assert json.loads(llm_detail_resp.json()["response_json"])["choices"][0]["message"]["content"] == "输出"

    tools_resp = client.get("/api/v1/admin/tool-calls", headers=auth_header)
    assert tools_resp.status_code == 200, tools_resp.text
    assert tools_resp.json()["items"][0]["status"] == "error"
