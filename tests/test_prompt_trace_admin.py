import json
from tests.async_helpers import run_async
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.sqlite_test_utils import install_base_schema


def _local_now() -> datetime:
    # Trace 测试 DB fixture 保持 naive 本地墙钟时间语义。
    return datetime.now()  # noqa: DTZ005


@pytest.fixture
def auth_header(monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    return {"Authorization": "Bearer test-token"}


def test_effective_prompt_preview_request_defaults_to_canonical_prompt():
    from api.admin_routes import EffectivePromptPreviewRequest

    body = EffectivePromptPreviewRequest()

    assert body.engine == "prompt"
    assert body.session_guidance_override is None
    assert EffectivePromptPreviewRequest(
        session_guidance_override="",
    ).session_guidance_override == ""
    assert EffectivePromptPreviewRequest(
        session_guidance_override="预览草稿",
    ).session_guidance_override == "预览草稿"


def test_tracer_records_runs_tools_and_prompt_logs(tmp_path, monkeypatch):
    from core import database
    from core.context_engine import ContextLayer, ContextLayerBudget, ContextManifest
    from core.tracing import PromptTracer, RunTracer, ToolTracer

    engine = create_engine(
        f"sqlite:///{tmp_path / 'trace.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    install_base_schema(engine)
    monkeypatch.setattr(database, "SessionLocal", TestingSessionLocal)

    long_path = "/runtime/" + ("resolution-path-" * 500) + "main.md"
    template_resolutions = {
        "base_contract": {
            "template_key": "chat/main",
            "active_source": "runtime",
            "active_path": long_path,
            "runtime_path": long_path,
            "default_path": "/default/chat/main.md",
            "active_sha256": "a" * 64,
            "runtime_sha256": "a" * 64,
            "default_sha256": "b" * 64,
            "baseline_version": None,
            "drift_status": "untracked_legacy",
        }
    }
    context_manifest = ContextManifest(
        policy_id="prompt-context-v1-private",
        request_prompt_sha256="d" * 64,
        entries=(),
        layer_budgets=tuple(
            ContextLayerBudget(layer, 1_000, 0)
            for layer in ContextLayer
        ),
    ).to_dict()

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
        prompt_template_resolutions=template_resolutions,
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
        variables={
            "user_input": "你好",
            "message_token_estimate": 6,
            "tool_schema_token_estimate": 2,
            "token_estimate": 8,
        },
        rendered_content="系统提示词不应完整入库",
        token_estimate=8,
        warnings=["unused: x"],
        prompt_source="PromptManager runtime template",
        prompt_runtime_path="/runtime/group_chat.md",
        prompt_default_path="/default/group_chat.md",
        prompt_sha256="b" * 64,
        prompt_template_resolutions=template_resolutions,
        context_manifest=context_manifest,
    )
    guidance_body = "TRACE_META_GUIDANCE_BODY_SENTINEL"
    RunTracer.finish_run(
        run.run_id,
        status="success",
        output_preview="回复",
        latency_ms=12,
        meta={
            "platform": "qq",
            "chat_type": "group",
            "session_guidance_chat_stream_id": "qq:1001:group",
            "session_guidance_configured": True,
            "session_guidance_chars": len(guidance_body),
            "session_guidance_sha256": "c" * 64,
            "session_guidance_resolution_status": "configured",
            "session_guidance_status": "emitted",
        },
    )

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
        assert json.loads(log.prompt_template_resolutions_json) == template_resolutions
        assert "truncated" not in log.prompt_template_resolutions_json
        assert json.loads(log.variables_json)["message_token_estimate"] == 6
        assert json.loads(log.variables_json)["tool_schema_token_estimate"] == 2
        assert json.loads(log.variables_json)["token_estimate"] == 8
        assert json.loads(log.context_manifest_json) == context_manifest
        assert log.token_estimate == 8
        assert log.rendered_preview != "系统提示词不应完整入库"
        assert "系统提示词" in log.rendered_preview

        run_row = db.query(database.AgentRun).first()
        assert run_row.status == "success"
        assert json.loads(run_row.prompt_template_resolutions_json) == template_resolutions
        assert "truncated" not in run_row.prompt_template_resolutions_json
        assert run_row.latency_ms == 12
        assert run_row.prompt_source == "Legacy runtime prompt"
        assert run_row.prompt_sha256 == "a" * 64
        run_meta = json.loads(run_row.meta_json)
        assert run_meta["session_guidance_chat_stream_id"] == "qq:1001:group"
        assert run_meta["session_guidance_chars"] == len(guidance_body)
        assert run_meta["session_guidance_sha256"] == "c" * 64
        assert guidance_body not in run_row.meta_json
        ledger_rows = (
            db.query(database.RunLedgerEventRow)
            .order_by(database.RunLedgerEventRow.sequence.asc())
            .all()
        )
        assert [row.event_type for row in ledger_rows] == [
            "run.accepted",
            "run.status_changed",
            "run.terminated",
        ]
        assert ledger_rows[1].previous_event_sha256 == ledger_rows[0].event_sha256
        assert ledger_rows[2].previous_event_sha256 == ledger_rows[1].event_sha256
        assert "你好" not in ledger_rows[0].payload_json
        assert "回复" not in ledger_rows[2].payload_json
    finally:
        db.close()


def test_tool_tracer_preserves_complete_bounded_web_search_evidence(tmp_path, monkeypatch):
    from core import database
    from core.tracing import MAX_PREVIEW_CHARS, ToolTracer
    from core.web_search.search_runtime import (
        WebSearchProviderResult,
        WebSearchResult,
        format_provider_result_for_model,
    )

    engine = create_engine(
        f"sqlite:///{tmp_path / 'web-search-trace.db'}",
        connect_args={"check_same_thread": False},
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    install_base_schema(engine)
    monkeypatch.setattr(database, "SessionLocal", testing_session)

    provider_result = WebSearchProviderResult(
        provider_id="searxng",
        results=[
            WebSearchResult(
                provider="searxng",
                title=f"Agent 记忆研究来源 {index} {'较长标题' * 20}",
                url=f"https://example.com/research/{index}",
                snippet="Agent 记忆研究的足够长搜索摘要" * 40,
            )
            for index in range(5)
        ],
    )
    evidence = format_provider_result_for_model("研究 Agent 记忆", provider_result, limit=5)
    assert len(evidence) > MAX_PREVIEW_CHARS

    tool_id = ToolTracer.start_tool_call(
        trace_id="trace-web-search",
        run_id="run-web-search",
        tool_name="web_search",
        args={"query": "研究 Agent 记忆"},
    )
    ToolTracer.finish_tool_call(tool_id, status="success", result=evidence)

    db = testing_session()
    try:
        row = db.query(database.ToolCall).one()
        assert row.result_preview == evidence
        assert row.result_preview.endswith("WEB_SEARCH_RESULTS_END")
        assert "...[truncated]" not in row.result_preview
    finally:
        db.close()
        engine.dispose()


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
    install_base_schema(engine)
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


def test_admin_prompt_and_trace_endpoints(
    client,
    auth_header,
    tmp_path,
    monkeypatch,
    db_session,
):
    from core import database
    from core.tracing import (
        LLMRequestTracer,
        PromptTracer,
        ReplyContractTracer,
        RunTracer,
        ToolTracer,
    )

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
    assert effective_v2_json["engine"] == "prompt"
    assert effective_v2_json["prompt_key"] == "chat_group"
    assert effective_v2_json["request_json"]["messages"] == effective_v2_json["messages"]
    assert effective_v2_json["request_json"]["tools"] == effective_v2_json["tool_schemas"]
    assert len(effective_v2_json["prompt_sha256"]) == 64
    template_resolutions = effective_v2_json["template_resolutions"]
    base_resolution = template_resolutions["base_contract"]
    assert effective_v2_json["prompt_source"] in {"runtime", "default", "mixed"}
    assert effective_v2_json["prompt_runtime_path"] == (base_resolution["runtime_path"] or "")
    assert effective_v2_json["prompt_default_path"] == (base_resolution["default_path"] or "")
    assert effective_v2_json["request_prompt_sha256"] == effective_v2_json["debug"][
        "request_prompt_sha256"
    ]
    assert effective_v2_json["request_prompt_sha256"] == effective_v2_json["prompt_sha256"]
    assert effective_v2_json["prompt_sha256"] != base_resolution["active_sha256"]
    assert effective_v2_json["section_hashes"]["base_contract"]
    assert "history_message_count" in effective_v2_json["debug"]
    rendered_v2_request = json.dumps(effective_v2_json["request_json"], ensure_ascii=False)
    assert rendered_v2_request.count("EFFECTIVE_PROMPT_V2_MARKER") == 1
    assert rendered_v2_request.count("[RuntimeTool]") == 0
    assert effective_v2_json["request_json"]["tools"]
    context_payloads = []
    for message in effective_v2_json["request_json"]["messages"]:
        content = str(message.get("content") or "")
        if not content.startswith("<context_data_json>\n"):
            continue
        payload_text = content.removeprefix("<context_data_json>\n").removesuffix(
            "\n</context_data_json>"
        )
        context_payloads.append(json.loads(payload_text))
    persona_payloads = [
        payload
        for payload in context_payloads
        if payload.get("section") == "persona_reference"
    ]
    assert len(persona_payloads) == 1
    assert persona_payloads[0]["trust"] == "untrusted_data"
    assert persona_payloads[0]["content"].count("<persona_reference>") == 1

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
    assert "super_user_id" not in variable_names
    assert "is_super_user" not in variable_names
    assert "platform" in variable_names
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
    RunTracer.update_prompt_source(
        run.run_id,
        prompt_source=effective_v2_json["prompt_source"],
        prompt_runtime_path=effective_v2_json["prompt_runtime_path"],
        prompt_default_path=effective_v2_json["prompt_default_path"],
        prompt_sha256=effective_v2_json["prompt_sha256"],
        prompt_template_resolutions=template_resolutions,
    )
    PromptTracer.record_render(
        trace_id="trace-admin",
        run_id=run.run_id,
        prompt_key=effective_v2_json["prompt_key"],
        mode="managed",
        variables={"token_estimate": effective_v2_json["token_estimate"]},
        rendered_content="仅用于验证摘要，不进入统一 Viewer",
        token_estimate=effective_v2_json["token_estimate"],
        prompt_source=effective_v2_json["prompt_source"],
        prompt_sha256=effective_v2_json["prompt_sha256"],
        prompt_template_resolutions=template_resolutions,
        context_manifest=effective_v2_json["context_manifest"],
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
        provider="newapi",
        model="model-a",
        request={"messages": [{"role": "user", "content": "输入"}]},
    )
    LLMRequestTracer.finish_request(
        log_id=llm_log_id,
        response={
            "choices": [{"message": {"content": "输出"}}],
            "usage": {
                "prompt_cache_hit_tokens": 16,
                "prompt_cache_miss_tokens": 4,
            },
        },
        response_status=200,
        status="success",
        latency_ms=7,
    )
    failed_llm_log_id = LLMRequestTracer.record_request(
        trace_id="trace-provider-failure",
        source="admin",
        provider="newapi",
        model="model-a",
        request={"messages": [{"role": "user", "content": "失败输入"}]},
    )
    LLMRequestTracer.finish_request(
        log_id=failed_llm_log_id,
        response={"error": "上游正文不应进入列表"},
        response_status=502,
        status="failed",
        error="HTTP 502 upstream failed",
        latency_ms=11,
    )
    RunTracer.finish_run(run.run_id, status="error", error="boom", finished_at=_local_now())

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
    assert detail_resp.json()["run"]["prompt_source"] == effective_v2_json[
        "prompt_source"
    ]
    assert detail_resp.json()["run"]["prompt_runtime_path"] == (
        effective_v2_json["prompt_runtime_path"]
    )
    assert detail_resp.json()["run"]["prompt_default_path"] == (
        effective_v2_json["prompt_default_path"]
    )
    assert detail_resp.json()["run"]["prompt_sha256"] == effective_v2_json[
        "prompt_sha256"
    ]
    assert detail_resp.json()["ledger"]["available"] is True
    assert detail_resp.json()["ledger"]["authoritative"] is True
    assert detail_resp.json()["ledger"]["source"] == "run_ledger"
    assert detail_resp.json()["ledger"]["projection_complete"] is True
    assert detail_resp.json()["ledger"]["projection"]["status"] == "failed"
    assert detail_resp.json()["ledger"]["projection"]["event_count"] == 5
    assert detail_resp.json()["ledger"]["projection"]["context_manifest"][
        "prompt_sha256"
    ] == effective_v2_json["prompt_sha256"]
    assert detail_resp.json()["ledger"]["projection"]["context_manifest"][
        "prompt_resolution_sha256"
    ]
    assert detail_resp.json()["ledger"]["readiness"] == {
        "run_id": run.run_id,
        "projection_consistent": True,
        "projection_complete": True,
        "reason_codes": [],
        "legacy_status": "failed",
        "ledger_status": "failed",
        "legacy_terminal": True,
        "ledger_terminal": True,
        "accepted_event_count": 1,
        "terminal_event_count": 1,
        "high_water_sequence": 5,
    }
    assert detail_resp.json()["run"]["status"] == "failed"
    assert detail_resp.json()["run"]["legacy_status"] == "error"
    assert detail_resp.json()["run"]["status_source"] == "run_ledger"
    assert detail_resp.json()["run"]["ledger_authoritative"] is True
    assert detail_resp.json()["ledger"]["projection"]["usage"] == {
        "input_tokens": 20,
        "output_tokens": 0,
        "cached_input_tokens": 16,
        "reasoning_tokens": 0,
        "cost_microunits": 0,
    }
    assert detail_resp.json()["tool_calls"][0]["tool_name"] == "reply"
    assert detail_resp.json()["reply_contract_check_logs"][0]["result"] == "no_tool_call"
    assert json.loads(detail_resp.json()["llm_api_request_logs"][0]["response_json"])["choices"][0]["message"]["content"] == "输出"
    assert detail_resp.json()["llm_api_request_logs"][0]["response_status"] == 200
    viewer = detail_resp.json()["viewer"]
    assert viewer["offline"] is True
    assert viewer["source"] == "persisted_evidence"
    assert viewer["summary"]["status"] == "failed"
    assert {span["kind"] for span in viewer["spans"]} >= {
        "cache",
        "llm",
        "prompt",
        "run",
        "tool",
    }
    assert viewer["context_manifest"]["available"] is True
    assert viewer["context_manifest"]["manifest"]["sha256"] == (
        effective_v2_json["context_manifest"]["sha256"]
    )
    assert viewer["redaction"]["hidden_reasoning"] == "omitted"
    serialized_viewer = json.dumps(viewer, ensure_ascii=False)
    assert "仅用于验证摘要" not in serialized_viewer
    assert "输入" not in serialized_viewer
    assert "输出" not in serialized_viewer

    ledger_resp = client.get(
        f"/api/v1/admin/agent-runs/{run.run_id}/events",
        params={"limit": 1},
        headers=auth_header,
    )
    assert ledger_resp.status_code == 200, ledger_resp.text
    assert ledger_resp.json()["items"][0]["event_type"] == "run.accepted"
    assert ledger_resp.json()["next_after_sequence"] == 1
    assert ledger_resp.json()["high_water_sequence"] == 5
    assert ledger_resp.json()["has_more"] is True
    ledger_next_resp = client.get(
        f"/api/v1/admin/agent-runs/{run.run_id}/events",
        params={"after_sequence": 1, "limit": 10},
        headers=auth_header,
    )
    assert ledger_next_resp.status_code == 200, ledger_next_resp.text
    prompt_events = [
        item
        for item in ledger_next_resp.json()["items"]
        if item["event_type"] == "run.prompt_resolved"
    ]
    assert len(prompt_events) == 1
    assert effective_v2_json["prompt_runtime_path"] not in str(
        prompt_events[0]["payload"]
    )
    assert [
        item["event_type"] for item in ledger_next_resp.json()["items"]
    ] == [
        "run.status_changed",
        "run.prompt_resolved",
        "usage.recorded",
        "run.terminated",
    ]

    legacy_row = db_session.get(database.AgentRun, run.run_id)
    assert legacy_row is not None
    legacy_row.status = "success"
    legacy_row.started_at = datetime(2035, 1, 1)
    legacy_row.finished_at = None
    db_session.commit()

    drifted_detail = client.get(
        f"/api/v1/admin/agent-runs/{run.run_id}",
        headers=auth_header,
    )
    assert drifted_detail.status_code == 200, drifted_detail.text
    assert drifted_detail.json()["run"]["status"] == "failed"
    assert drifted_detail.json()["run"]["legacy_status"] == "success"
    assert drifted_detail.json()["run"]["started_at"] != "2035-01-01T00:00:00"
    assert drifted_detail.json()["ledger"]["legacy_audit"][
        "projection_consistent"
    ] is False
    authority_filtered = client.get(
        "/api/v1/admin/agent-runs",
        params={"status": "failed", "trace_id": "trace-admin"},
        headers=auth_header,
    )
    assert authority_filtered.status_code == 200, authority_filtered.text
    assert authority_filtered.json()["total"] == 1
    assert authority_filtered.json()["items"][0]["status"] == "failed"
    stale_legacy_filtered = client.get(
        "/api/v1/admin/agent-runs",
        params={"status": "success", "trace_id": "trace-admin"},
        headers=auth_header,
    )
    assert stale_legacy_filtered.status_code == 200, stale_legacy_filtered.text
    assert stale_legacy_filtered.json()["total"] == 0

    llm_logs_resp = client.get("/api/v1/admin/llm-api-logs", params={"trace_id": "trace-admin"}, headers=auth_header)
    assert llm_logs_resp.status_code == 200, llm_logs_resp.text
    assert llm_logs_resp.json()["stats"]["total"] == 1
    assert llm_logs_resp.json()["stats"]["success"] == 1
    assert llm_logs_resp.json()["stats"]["avg_latency_ms"] == 7
    assert llm_logs_resp.json()["stats"]["cache_hit"] == 1
    assert llm_logs_resp.json()["stats"]["cache_hit_tokens"] == 16
    assert llm_logs_resp.json()["stats"]["cache_miss_tokens"] == 4
    assert llm_logs_resp.json()["stats"]["cache_hit_token_ratio"] == 0.8
    assert llm_logs_resp.json()["stats"]["input_tokens"] == 20
    assert llm_logs_resp.json()["stats"]["output_tokens"] == 0
    assert llm_logs_resp.json()["stats"]["cost_microusd"] == 0
    assert llm_logs_resp.json()["stats"]["by_error_category"] == {"none": 1}
    assert llm_logs_resp.json()["stats"]["by_provider"]["newapi"] == {
        "requests": 1,
        "successful_requests": 1,
        "failed_requests": 0,
        "incomplete_requests": 0,
        "success_rate": 1.0,
        "cache_hit_tokens": 16,
        "cache_miss_tokens": 4,
        "cache_write_tokens": 0,
        "cache_input_tokens": 20,
        "cache_denominator_unknown_requests": 0,
        "cache_hit_token_ratio": 0.8,
        "avg_first_token_latency_ms": 0,
        "avg_total_latency_ms": 7,
        "input_tokens": 20,
        "output_tokens": 0,
        "cost_microusd": 0,
        "by_error_category": {"none": 1},
    }
    llm_list_item = llm_logs_resp.json()["items"][0]
    assert llm_list_item["summary_only"] is True
    assert llm_list_item["cache_status"] == "hit"
    assert llm_list_item["cache_hit"] is True
    assert llm_list_item["cache_hit_tokens"] == 16
    assert llm_list_item["cache_miss_tokens"] == 4
    assert llm_list_item["input_tokens"] == 20
    assert llm_list_item["first_token_latency_ms"] == 0
    assert llm_list_item["cost_source"] == "not_available"
    assert llm_list_item["error_category"] == "none"
    assert "request_json" not in llm_list_item
    assert "response_json" not in llm_list_item

    cache_filtered_resp = client.get(
        "/api/v1/admin/llm-api-logs",
        params={"cache_status": "hit"},
        headers=auth_header,
    )
    assert cache_filtered_resp.status_code == 200
    assert cache_filtered_resp.json()["total"] == 1

    provider_error_filtered = client.get(
        "/api/v1/admin/llm-api-logs",
        params={
            "provider": "newapi",
            "error_category": "upstream",
        },
        headers=auth_header,
    )
    assert provider_error_filtered.status_code == 200
    assert provider_error_filtered.json()["total"] == 1
    assert provider_error_filtered.json()["items"][0]["error_category"] == (
        "upstream"
    )

    llm_detail_resp = client.get(f"/api/v1/admin/llm-api-logs/{llm_log_id}", headers=auth_header)
    assert llm_detail_resp.status_code == 200, llm_detail_resp.text
    assert json.loads(llm_detail_resp.json()["request_json"])["messages"][0]["content"] == "输入"
    assert json.loads(llm_detail_resp.json()["response_json"])["choices"][0]["message"]["content"] == "输出"
    assert llm_detail_resp.json()["cache_status"] == "hit"
    assert llm_detail_resp.json()["cache_hit_tokens"] == 16
    assert llm_detail_resp.json()["cache_miss_tokens"] == 4

    tools_resp = client.get("/api/v1/admin/tool-calls", headers=auth_header)
    assert tools_resp.status_code == 200, tools_resp.text
    assert tools_resp.json()["items"][0]["status"] == "error"


def test_admin_reads_independent_delivery_run_without_legacy_header(
    client,
    auth_header,
    db_session,
):
    from core.run_ledger.contracts import RunLedgerEventDraft
    from core.run_ledger.persistence import SqlAlchemyRunEventLedger
    from core.telemetry.contracts import TelemetryCorrelation

    run_id = "delivery:admin:test-1"
    occurred_at = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)
    correlation = TelemetryCorrelation(
        run_id=run_id,
        task_run_id="source-run-1",
        job_id="outbox-1",
        delivery_id="attempt-1",
    )
    ledger = SqlAlchemyRunEventLedger(db_session)
    ledger.append(RunLedgerEventDraft(
        event_id="delivery-admin-accepted",
        run_id=run_id,
        event_type="run.accepted",
        occurred_at=occurred_at,
        source="test.admin",
        correlation=correlation,
        status="accepted",
        payload={"run_type": "delivery", "event_name": "delivery.attempt"},
    ))
    ledger.append(RunLedgerEventDraft(
        event_id="delivery-admin-terminal",
        run_id=run_id,
        event_type="run.terminated",
        occurred_at=occurred_at,
        source="test.admin",
        correlation=correlation,
        status="ambiguous",
        payload={"failure_code": "transport_unknown"},
    ))
    db_session.commit()

    detail = client.get(
        f"/api/v1/admin/agent-runs/{run_id}",
        headers=auth_header,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["run"]["run_id"] == run_id
    assert detail.json()["run"]["run_type"] == "delivery"
    assert detail.json()["run"]["status"] == "ambiguous"
    assert detail.json()["run"]["legacy_status"] is None
    assert detail.json()["ledger"]["authoritative"] is True
    assert detail.json()["ledger"]["legacy_audit"] is None

    listed = client.get(
        "/api/v1/admin/agent-runs",
        params={"run_type": "delivery"},
        headers=auth_header,
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["run_id"] == run_id
    assert listed.json()["items"][0]["status_source"] == "run_ledger"

    events = client.get(
        f"/api/v1/admin/agent-runs/{run_id}/events",
        headers=auth_header,
    )
    assert events.status_code == 200, events.text
    assert [item["event_type"] for item in events.json()["items"]] == [
        "run.accepted",
        "run.terminated",
    ]


def test_agent_run_reconnect_is_read_only_and_cancel_is_idempotent(
    client,
    auth_header,
    db_session,
):
    from core.database import RunTaskControl
    from core.tracing import RunTracer

    handle = RunTracer.start_run(
        trace_id="trace-durable-reconnect",
        session_id="private_durable",
        user_id="durable-user",
        run_type="chat",
        input_preview="长任务",
        meta={
            "platform": "qq",
            "chat_type": "private",
            "message_id": "durable-message-1",
        },
    )
    path = f"/api/v1/admin/agent-runs/{handle.run_id}"

    first = client.get(path, headers=auth_header)
    second = client.get(path, headers=auth_header)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["durable_task"] == second.json()["durable_task"]
    assert first.json()["durable_task"]["status"] == "running"
    assert first.json()["durable_task"]["lease"]["generation"] == 1
    assert "token" not in first.json()["durable_task"]["lease"]
    db_session.expire_all()
    task_row = db_session.get(RunTaskControl, handle.run_id)
    assert task_row is not None
    assert task_row.attempt_count == 1

    first_cancel = client.post(
        f"{path}/cancel",
        json={"reason": "管理员取消"},
        headers=auth_header,
    )
    repeated_cancel = client.post(
        f"{path}/cancel",
        json={"reason": "管理员取消"},
        headers=auth_header,
    )
    conflict = client.post(
        f"{path}/cancel",
        json={"reason": "不同原因"},
        headers=auth_header,
    )
    missing = client.post(
        "/api/v1/admin/agent-runs/missing-run/cancel",
        json={"reason": "管理员取消"},
        headers=auth_header,
    )
    assert first_cancel.status_code == 200, first_cancel.text
    assert repeated_cancel.status_code == 200, repeated_cancel.text
    assert first_cancel.json() == repeated_cancel.json()
    assert conflict.status_code == 409
    assert missing.status_code == 404

    RunTracer.finish_run(
        handle.run_id,
        task_lease=handle.task_lease,
        status="cancelled",
        error="管理员取消",
    )
