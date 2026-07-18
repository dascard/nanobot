import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.async_helpers import run_async

from core.database import LLMApiRequestLog
from core.tracing import _json_dumps


def test_redact_authorization_header():
    text = _json_dumps({
        "Authorization": "Bearer abc123",
        "Content-Type": "application/json",
    }, max_chars=10000)
    assert "Bearer abc123" not in text
    assert "abc123" not in text
    assert "REDACTED" in text or "application/json" in text


def test_redact_api_key():
    text = _json_dumps({"api_key": "sk-secret-key"}, max_chars=10000)
    assert "sk-secret-key" not in text


def test_redact_preserves_only_numeric_token_estimates():
    payload = json.loads(_json_dumps({
        "token_estimate": 123,
        "message_token_estimate": 100,
        "tool_schema_token_estimate": 23,
        "access_token": "secret-access-token",
        "nested": {"token_estimate": "secret-disguised-as-metric"},
    }, max_chars=10000))

    assert payload["token_estimate"] == 123
    assert payload["message_token_estimate"] == 100
    assert payload["tool_schema_token_estimate"] == 23
    assert payload["access_token"] == "[REDACTED]"
    assert payload["nested"]["token_estimate"] == "[REDACTED]"


def test_request_json_preserves_messages():
    messages = [{"role": "user", "content": "hello"}]
    payload = {"model": "gpt-4", "messages": messages, "temperature": 0.7}
    text = _json_dumps(payload, max_chars=200000)
    assert "gpt-4" in text
    assert "hello" in text
    assert "temperature" in text


def test_record_request_returns_id_and_finish_updates_same_row(db_session):
    from core.tracing import LLMRequestTracer

    log_id = LLMRequestTracer.record_request(
        trace_id="trace-response",
        run_id="run-response",
        source="unit",
        provider="newapi",
        model="model-r",
        url="http://llm.test/v1/chat/completions",
        headers={"Authorization": "Bearer secret-token"},
        request={"model": "model-r", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert isinstance(log_id, int)
    assert log_id > 0

    LLMRequestTracer.finish_request(
        log_id=log_id,
        response={"choices": [{"message": {"content": "ok"}}]},
        response_status=200,
        status="success",
        latency_ms=123,
    )

    row = db_session.query(LLMApiRequestLog).filter_by(id=log_id).one()
    assert json.loads(row.request_json)["messages"][0]["content"] == "hi"
    assert json.loads(row.response_json)["choices"][0]["message"]["content"] == "ok"
    assert row.response_preview
    assert row.response_status == 200
    assert row.status == "success"
    assert row.latency_ms == 123
    assert row.finished_at is not None
    assert "secret-token" not in row.headers_json


def test_finish_request_records_error_status(db_session):
    from core.tracing import LLMRequestTracer

    log_id = LLMRequestTracer.record_request(
        trace_id="trace-error",
        run_id="run-error",
        source="unit",
        request={"model": "m"},
    )

    LLMRequestTracer.finish_request(
        log_id=log_id,
        response={"detail": "bad gateway"},
        response_status=502,
        status="error",
        error="upstream failed",
        latency_ms=45,
    )

    row = db_session.query(LLMApiRequestLog).filter_by(id=log_id).one()
    assert row.status == "error"
    assert row.response_status == 502
    assert row.error == "upstream failed"
    response_audit = json.loads(row.response_json)
    assert response_audit["response_body_omitted"] is True
    assert "bad gateway" in response_audit["safe_summary"]
    assert len(response_audit["response_body_sha256"]) == 64


def test_failure_trace_redacts_and_bounds_free_form_diagnostics(db_session):
    from core.database import LLMApiRequestLog
    from core.tracing import LLMRequestTracer

    url_password = "trace-url-password-secret"
    query_secret = "trace-query-secret"
    body_secret = "trace-response-secret"
    error_secret = "trace-error-secret"
    log_id = LLMRequestTracer.record_request(
        trace_id="trace-safe-failure",
        run_id="run-safe-failure",
        source="classifier.timing_gate",
        url=(
            f"https://trace-user:{url_password}@llm.test/v1/chat/completions"
            f"?api_key={query_secret}#fragment-secret"
        ),
        request={"model": "m"},
    )

    LLMRequestTracer.finish_request(
        log_id=log_id,
        response={"detail": f"token={body_secret}" + "x" * 100_000},
        response_status=502,
        status="error",
        error=f"Authorization: Bearer {error_secret}",
        latency_ms=45,
    )

    row = db_session.query(LLMApiRequestLog).filter_by(id=log_id).one()
    persisted = "\n".join([
        row.url or "",
        row.response_json or "",
        row.response_preview or "",
        row.error or "",
    ])
    for secret in (
        url_password,
        query_secret,
        "fragment-secret",
        body_secret,
        error_secret,
    ):
        assert secret not in persisted
    assert len(row.response_json) <= 8_000
    response_audit = json.loads(row.response_json)
    assert response_audit["response_body_omitted"] is True
    assert response_audit["response_body_chars"] > 100_000
    assert len(response_audit["response_body_sha256"]) == 64


def test_openai_sdk_tracer_records_non_stream_request(monkeypatch):
    from core.llm_trace_context import llm_trace_scope
    from core.llm_sdk_tracing import install_openai_chat_completion_tracer

    recorded = []
    finished = []
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs) or 456),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )

    create = AsyncMock(return_value={"choices": [{"message": {"content": "ok"}}]})
    llm = SimpleNamespace(
        _client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        _api_key="reply-key",
        _extra_headers={"X-Test": "1"},
        base_url="http://same-provider.test/v1",
        provider_name="newapi",
    )

    assert install_openai_chat_completion_tracer(
        llm,
        provider="newapi",
        base_url="http://same-provider.test/v1",
    )
    with llm_trace_scope(trace_id="trace-r", run_id="run-r", source="replyer"):
        result = run_async(llm._client.chat.completions.create(
            model="manual-model",
            messages=[{"role": "user", "content": "你好"}],
            temperature=0,
            extra_body={"reasoning": {"enabled": True}},
        ))

    assert result["choices"][0]["message"]["content"] == "ok"
    assert recorded
    row = recorded[0]
    assert row["trace_id"] == "trace-r"
    assert row["run_id"] == "run-r"
    assert row["source"] == "replyer"
    assert row["provider"] == "newapi"
    assert row["model"] == "manual-model"
    assert row["url"] == "http://same-provider.test/v1/chat/completions"
    assert row["headers"]["Authorization"].startswith("Bearer ")
    assert row["headers"]["X-Test"] == "1"
    assert row["request"]["messages"][0]["content"] == "你好"
    assert row["request"]["temperature"] == 0
    assert row["request"]["reasoning"] == {"enabled": True}
    assert "extra_body" not in row["request"]
    assert finished
    assert finished[0]["log_id"] == 456
    assert finished[0]["status"] == "success"
    assert finished[0]["response"]["choices"][0]["message"]["content"] == "ok"


def test_openai_sdk_tracer_recomputes_metrics_for_each_tool_loop_request(db_session):
    from core.llm_sdk_tracing import install_openai_chat_completion_tracer
    from core.llm_trace_context import llm_trace_scope
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.request_metrics import calculate_request_metrics
    from core.prompt_v2.schema import PromptCompileRequest

    tools = [{
        "type": "function",
        "function": {
            "name": "reply",
            "description": "发送最终回复",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
        },
    }]
    plan = run_async(compile_prompt_plan(PromptCompileRequest(
        chat_type="private",
        session_id="private_metrics",
        user_id="metrics-user",
        user_input="请回复",
        runtime_tool_prompt=(
            "[RuntimeTool]\n"
            "本轮真实可调用工具以 API tools schema 为准。"
        ),
        tool_schemas=tools,
    )))

    create = AsyncMock(side_effect=[
        {"choices": [{"message": {"content": "调用工具"}}]},
        {"choices": [{"message": {"content": "完成"}}]},
    ])
    llm = SimpleNamespace(
        _client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        _api_key="reply-key",
        _extra_headers={},
        base_url="http://metrics-provider.test/v1",
        provider_name="newapi",
    )
    assert install_openai_chat_completion_tracer(llm)

    second_messages = [
        *plan.messages,
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_reply",
                "type": "function",
                "function": {"name": "reply", "arguments": '{"content":"完成"}'},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "call_reply",
            "name": "reply",
            "content": "已发送",
        },
    ]
    with llm_trace_scope(trace_id="trace-metrics", run_id="run-metrics", source="replyer"):
        run_async(llm._client.chat.completions.create(
            model="metrics-model",
            messages=plan.messages,
            tools=plan.tool_schemas,
        ))
        run_async(llm._client.chat.completions.create(
            model="metrics-model",
            messages=second_messages,
            tools=plan.tool_schemas,
        ))

    db_session.expire_all()
    rows = (
        db_session.query(LLMApiRequestLog)
        .filter_by(run_id="run-metrics")
        .order_by(LLMApiRequestLog.id.asc())
        .all()
    )
    assert len(rows) == 2
    first_payload = json.loads(rows[0].request_json)
    second_payload = json.loads(rows[1].request_json)
    first_metrics = json.loads(rows[0].request_lint_json)["payload_metrics"]
    second_metrics = json.loads(rows[1].request_lint_json)["payload_metrics"]

    assert first_payload["messages"] == plan.messages
    assert first_payload["tools"] == plan.tool_schemas
    assert first_metrics["prompt_sha256"] == plan.prompt_sha256
    assert first_metrics == calculate_request_metrics(
        messages=first_payload["messages"],
        tools=first_payload["tools"],
    ).to_dict()
    assert second_metrics == calculate_request_metrics(
        messages=second_payload["messages"],
        tools=second_payload["tools"],
    ).to_dict()
    assert second_metrics["prompt_sha256"] != first_metrics["prompt_sha256"]


def test_openai_sdk_tracer_records_stream_request(monkeypatch):
    from core.llm_trace_context import llm_trace_scope
    from core.llm_sdk_tracing import install_openai_chat_completion_tracer

    recorded = []
    finished = []
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs) or 789),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )

    class _AsyncStream:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._chunks:
                raise StopAsyncIteration
            return self._chunks.pop(0)

    create = AsyncMock(return_value=_AsyncStream([
        {"choices": [{"delta": {"content": "流"}}]},
        {"choices": [{"delta": {"content": "式"}}]},
    ]))
    llm = SimpleNamespace(
        _client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        _api_key="reply-key",
        _extra_headers={},
        base_url="http://stream-provider.test/v1",
        provider_name="newapi",
    )

    assert install_openai_chat_completion_tracer(
        llm,
        provider="newapi",
        base_url="http://stream-provider.test/v1",
    )
    async def _run():
        with llm_trace_scope(trace_id="trace-s", run_id="run-s", source="replyer"):
            result = await llm._client.chat.completions.create(
                model="stream-model",
                messages=[{"role": "user", "content": "流式"}],
                stream=True,
                temperature=0.1,
            )
            return [chunk async for chunk in result]

    chunks = run_async(_run())

    assert recorded
    row = recorded[0]
    assert row["trace_id"] == "trace-s"
    assert row["run_id"] == "run-s"
    assert row["source"] == "replyer"
    assert row["request"]["stream"] is True
    assert row["request"]["model"] == "stream-model"
    assert row["request"]["messages"][0]["content"] == "流式"
    assert chunks
    assert finished
    assert finished[0]["log_id"] == 789
    assert finished[0]["status"] == "stream_success"
    assert finished[0]["response_status"] == 200
    assert finished[0]["response"]["content"] == "流式"
    assert "repr" not in finished[0]["response"]


def test_openai_sdk_tracer_records_stream_reasoning_metrics(monkeypatch):
    from core.llm_trace_context import llm_trace_scope
    from core.llm_sdk_tracing import install_openai_chat_completion_tracer

    recorded = []
    finished = []
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs) or 790),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )

    class _AsyncStream:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._chunks:
                raise StopAsyncIteration
            return self._chunks.pop(0)

    create = AsyncMock(return_value=_AsyncStream([
        {"choices": [{"delta": {"reasoning_content": "先想"}}]},
        {"choices": [{"delta": {"reasoning_content": "一下"}}]},
        {"choices": [{"delta": {"content": "答"}}]},
    ]))
    llm = SimpleNamespace(
        _client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        _api_key="reply-key",
        _extra_headers={},
        base_url="http://stream-provider.test/v1",
        provider_name="newapi",
    )

    assert install_openai_chat_completion_tracer(
        llm,
        provider="newapi",
        base_url="http://stream-provider.test/v1",
    )

    async def _run():
        with llm_trace_scope(trace_id="trace-sr", run_id="run-sr", source="replyer"):
            result = await llm._client.chat.completions.create(
                model="stream-model",
                messages=[{"role": "user", "content": "流式"}],
                stream=True,
                temperature=0.1,
            )
            return [chunk async for chunk in result]

    chunks = run_async(_run())

    assert chunks
    assert finished
    response = finished[0]["response"]
    assert response["content"] == "答"
    assert response["reasoning_content"] == "先想一下"
    assert response["stream_metrics"]["reasoning_char_count"] == 4
    assert response["stream_metrics"]["content_char_count"] == 1
    assert response["stream_metrics"]["reasoning_elapsed_ms"] >= 0


def test_compaction_direct_http_records_request(monkeypatch):
    from core import compaction
    from core.llm_trace_context import llm_trace_scope

    recorded = []
    finished = []
    monkeypatch.setattr(compaction, "COMPACT_API_KEY", "compact-key")
    monkeypatch.setattr(compaction, "COMPACT_BASE_URL", "http://compact.test/v1")
    monkeypatch.setattr(compaction, "COMPACT_MODEL", "compact-model")
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs) or 321),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "<summary>ok</summary>"}}]}

    monkeypatch.setattr(compaction.requests, "post", lambda *a, **kw: _Resp())

    with llm_trace_scope(trace_id="trace-c", run_id="run-c", source="replyer"):
        compaction.call_compaction_llm("需要折叠的上下文")

    assert recorded
    row = recorded[0]
    assert row["trace_id"] == "trace-c"
    assert row["run_id"] == "run-c"
    assert row["source"] == "compaction"
    assert row["model"] == "compact-model"
    assert row["request"]["messages"][1]["content"].endswith("需要折叠的上下文")
    assert finished
    assert finished[0]["log_id"] == 321
    assert finished[0]["status"] == "success"
    assert finished[0]["response"]["choices"][0]["message"]["content"] == "<summary>ok</summary>"


def test_new_api_chat_completion_finishes_request_on_success(monkeypatch):
    from clients.new_api_client import NewAPIClient

    recorded = []
    finished = []
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs) or 654),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )
    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock(return_value=None))
    monkeypatch.setattr(NewAPIClient, "estimate_complexity", lambda self, messages, tools=None: 1)
    monkeypatch.setattr(NewAPIClient, "get_ordered_candidates", lambda self, **kwargs: [{"id": "model-success", "intelligence": 7}])
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)

    class _FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"choices": [{"message": {"content": "成功"}}], "usage": {"total_tokens": 3}}

        async def text(self):
            return "ok"

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return _FakeResp()

    monkeypatch.setattr("clients.new_api_client.aiohttp.ClientSession", lambda: _FakeSession())

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
    result = run_async(client.chat_completion([{"role": "user", "content": "你好"}]))

    assert result["choices"][0]["message"]["content"] == "成功"
    assert recorded
    assert recorded[0]["request"]["model"] == "model-success"
    assert finished
    assert finished[0]["log_id"] == 654
    assert finished[0]["response_status"] == 200
    assert finished[0]["status"] == "success"
    assert finished[0]["response"]["choices"][0]["message"]["content"] == "成功"


def test_new_api_chat_completion_audits_invalid_json_response(monkeypatch):
    from clients.new_api_client import NewAPIClient

    finished = []
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **_kwargs: 655),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )
    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock(return_value=None))
    monkeypatch.setattr(NewAPIClient, "estimate_complexity", lambda self, messages, tools=None: 1)
    monkeypatch.setattr(
        NewAPIClient,
        "get_ordered_candidates",
        lambda self, **kwargs: [{"id": "model-invalid-json", "intelligence": 7}],
    )
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)
    monkeypatch.setattr(
        "core.settings_service.settings.get_int",
        lambda key, default=0: 1 if key == "new_api.max_retries" else default,
    )

    class _FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def read(self):
            return b'{"choices": ['

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return _FakeResp()

    monkeypatch.setattr("clients.new_api_client.aiohttp.ClientSession", lambda: _FakeSession())

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
    result = run_async(client.chat_completion([{"role": "user", "content": "你好"}]))

    assert result["error"] == "AllModelsFailed"
    assert "invalid JSON" in result["detail"]
    assert result["_nanobot_requested_models"] == ["model-invalid-json"]
    assert result["_nanobot_request_log_ids"] == [655]
    assert len(finished) == 1
    assert finished[0]["status"] == "error"
    assert finished[0]["response_status"] == 200
    assert finished[0]["response"]["raw_body_preview"] == '{"choices": ['
    assert finished[0]["response"]["raw_body_chars"] == 13
    assert len(finished[0]["response"]["raw_body_sha256"]) == 64


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": None, "tool_calls": [{}]}}]},
        {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{"id": "call-1", "type": "function"}],
                }
            }]
        },
        {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "", "arguments": "{}"},
                    }],
                }
            }]
        },
        {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": {}},
                    }],
                }
            }]
        },
        {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": "not-json"},
                    }],
                }
            }]
        },
    ],
    ids=[
        "list-root",
        "empty-object",
        "empty-choices",
        "missing-message",
        "empty-message",
        "empty-tool-call",
        "missing-function",
        "empty-function-name",
        "non-string-arguments",
        "invalid-json-arguments",
    ],
)
def test_new_api_chat_completion_rejects_malformed_success_contract(monkeypatch, body):
    from clients.new_api_client import NewAPIClient

    finished = []
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **_kwargs: 656),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )
    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock(return_value=None))
    monkeypatch.setattr(NewAPIClient, "estimate_complexity", lambda self, messages, tools=None: 1)
    monkeypatch.setattr(
        NewAPIClient,
        "get_ordered_candidates",
        lambda self, **kwargs: [{"id": "model-malformed-contract", "intelligence": 7}],
    )
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)
    monkeypatch.setattr(
        "core.settings_service.settings.get_int",
        lambda key, default=0: 1 if key == "new_api.max_retries" else default,
    )

    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    class _FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def read(self):
            return raw_body

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return _FakeResp()

    monkeypatch.setattr("clients.new_api_client.aiohttp.ClientSession", lambda: _FakeSession())

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
    result = run_async(client.chat_completion([{"role": "user", "content": "你好"}]))

    assert result["error"] == "AllModelsFailed"
    assert "completion contract" in result["detail"]
    assert len(finished) == 1
    assert finished[0]["status"] == "error"
    assert finished[0]["response_status"] == 200
    assert finished[0]["response"]["raw_body_preview"] == raw_body.decode("utf-8")
    assert finished[0]["response"]["raw_body_chars"] == len(raw_body.decode("utf-8"))
    assert len(finished[0]["response"]["raw_body_sha256"]) == 64


@pytest.mark.parametrize(
    ("body", "expected_error"),
    [
        (
            {
                "choices": [{
                    "message": {"content": "正文"},
                    "finish_reason": 123,
                }],
            },
            "finish_reason",
        ),
        (
            {
                "choices": [{
                    "message": {"content": "正文"},
                    "finish_reason": "stop",
                }],
                "usage": [],
            },
            "usage",
        ),
        (
            {
                "choices": [{
                    "message": {
                        "content": "正文",
                        "reasoning_content": ["非法推理"],
                    },
                    "finish_reason": "stop",
                }],
            },
            "reasoning_content",
        ),
        (
            {
                "choices": [{
                    "message": {
                        "content": "正文",
                        "reasoning": {"unexpected": "object"},
                    },
                    "finish_reason": "stop",
                }],
            },
            "reasoning",
        ),
    ],
    ids=[
        "non-string-finish-reason",
        "non-mapping-usage",
        "non-string-reasoning-content",
        "non-string-reasoning",
    ],
)
def test_new_api_chat_completion_rejects_malformed_metadata(
    monkeypatch,
    body,
    expected_error,
):
    from clients.new_api_client import NewAPIClient

    finished = []
    tracker = SimpleNamespace(
        record_failure=AsyncMock(return_value=None),
        record_success=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **_kwargs: 657),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )
    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock(return_value=None))
    monkeypatch.setattr(NewAPIClient, "estimate_complexity", lambda self, messages, tools=None: 1)
    monkeypatch.setattr(
        NewAPIClient,
        "get_ordered_candidates",
        lambda self, **kwargs: [{"id": "model-malformed-metadata", "intelligence": 7}],
    )
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: tracker)
    monkeypatch.setattr(
        "core.settings_service.settings.get_int",
        lambda key, default=0: 1 if key == "new_api.max_retries" else default,
    )

    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    class _FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def read(self):
            return raw_body

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return _FakeResp()

    monkeypatch.setattr("clients.new_api_client.aiohttp.ClientSession", lambda: _FakeSession())

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
    result = run_async(client.chat_completion([{"role": "user", "content": "你好"}]))

    assert result["error"] == "AllModelsFailed"
    assert expected_error in result["detail"]
    assert len(finished) == 1
    assert finished[0]["status"] == "error"
    assert finished[0]["response_status"] == 200
    assert finished[0]["response"]["raw_body_preview"] == raw_body.decode("utf-8")
    tracker.record_failure.assert_called_once_with("model-malformed-metadata")
    tracker.record_success.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {
            "choices": [{
                "message": {
                    "content": "正文",
                    "reasoning_content": "独立推理",
                },
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 3},
        },
        {
            "choices": [{
                "message": {
                    "content": "正文",
                    "reasoning": None,
                },
                "finish_reason": None,
            }],
            "usage": None,
        },
    ],
    ids=["string-metadata", "null-metadata"],
)
def test_new_api_chat_completion_accepts_valid_metadata(body):
    from clients.new_api_client import _read_response_json

    class _FakeResp:
        async def read(self):
            return json.dumps(body, ensure_ascii=False).encode("utf-8")

    assert run_async(_read_response_json(_FakeResp())) == body


def test_new_api_chat_completion_accepts_tool_call_message_with_null_content(monkeypatch):
    from clients.new_api_client import _read_response_json

    body = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query":"Nanobot"}',
                    },
                }],
            }
        }]
    }

    class _FakeResp:
        async def read(self):
            return json.dumps(body).encode("utf-8")

    assert run_async(_read_response_json(_FakeResp())) == body


def test_new_api_chat_completion_with_tools_requests_tool_capable_candidates(monkeypatch):
    from clients.new_api_client import NewAPIClient

    captured = {}
    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock(return_value=None))
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)

    def fake_candidates(self, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(NewAPIClient, "get_ordered_candidates", fake_candidates)

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
    result = run_async(client.chat_completion(
        [{"role": "user", "content": "查一下"}],
        tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
    ))

    assert result["error"] == "No candidates available"
    assert captured["required_capabilities"]["supports_tools"] is True


def test_new_api_chat_completion_with_image_url_requests_vision_candidates(monkeypatch):
    from clients.new_api_client import NewAPIClient

    captured = {}
    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock(return_value=None))
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)

    def fake_candidates(self, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(NewAPIClient, "get_ordered_candidates", fake_candidates)

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
    result = run_async(client.chat_completion([
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    ]))

    assert result["error"] == "No candidates available"
    assert captured["required_capabilities"]["supports_image"] is True


def test_new_api_client_uses_injected_session_for_model_fetch(monkeypatch):
    from clients.new_api_client import NewAPIClient

    class _FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"data": [{"id": "qwen/free-model"}]}

    class _InjectedSession:
        def __init__(self):
            self.get_calls = 0
            self.closed = False

        def get(self, *args, **kwargs):
            self.get_calls += 1
            return _FakeResp()

        async def close(self):
            self.closed = True

    def fail_client_session(*_args, **_kwargs):
        raise AssertionError("应复用注入 session，不应创建逐请求 ClientSession")

    session = _InjectedSession()
    monkeypatch.setattr("clients.new_api_client.aiohttp.ClientSession", fail_client_session)

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1", session=session)
    models = run_async(client.fetch_models())

    assert [m["id"] for m in models] == ["qwen/free-model"]
    assert session.get_calls == 1
    assert session.closed is False


def test_new_api_client_uses_shared_session_by_default(monkeypatch):
    from clients.new_api_client import NewAPIClient

    class _FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"data": [{"id": "qwen/shared-model"}]}

    class _SharedSession:
        def __init__(self):
            self.get_calls = 0

        def get(self, *args, **kwargs):
            self.get_calls += 1
            return _FakeResp()

    def fail_client_session(*_args, **_kwargs):
        raise AssertionError("已有共享 session 时不应创建逐请求 ClientSession")

    session = _SharedSession()
    monkeypatch.setattr("clients.new_api_client.aiohttp.ClientSession", fail_client_session)

    try:
        NewAPIClient.set_shared_session(session)
        client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
        models = run_async(client.fetch_models())
    finally:
        NewAPIClient.set_shared_session(None)

    assert [m["id"] for m in models] == ["qwen/shared-model"]
    assert session.get_calls == 1


def test_new_api_client_skips_shared_session_from_other_loop(monkeypatch):
    from clients.new_api_client import NewAPIClient

    class _FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"data": [{"id": "qwen/current-loop-model"}]}

    class _StaleSharedSession:
        closed = False

        def __init__(self, loop):
            self._loop = loop

        def get(self, *args, **kwargs):
            raise RuntimeError("不应复用其他事件循环的共享 session")

    class _CurrentLoopSession:
        created = 0
        get_calls = 0
        closed = False

        def __init__(self):
            type(self).created += 1
            self._loop = asyncio.get_running_loop()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.closed = True
            return False

        def get(self, *args, **kwargs):
            type(self).get_calls += 1
            return _FakeResp()

    stale_loop = asyncio.new_event_loop()
    stale_session = _StaleSharedSession(stale_loop)
    monkeypatch.setattr("clients.new_api_client.aiohttp.ClientSession", _CurrentLoopSession)

    try:
        NewAPIClient.set_shared_session(stale_session)
        client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
        models = run_async(client.fetch_models())
    finally:
        NewAPIClient.set_shared_session(None)
        stale_loop.close()

    assert [m["id"] for m in models] == ["qwen/current-loop-model"]
    assert _CurrentLoopSession.created == 1
    assert _CurrentLoopSession.get_calls == 1


def test_new_api_client_uses_injected_session_for_chat_completion(monkeypatch):
    from clients.new_api_client import NewAPIClient

    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **_kwargs: 321),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **_kwargs: None),
    )
    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock(return_value=None))
    monkeypatch.setattr(NewAPIClient, "estimate_complexity", lambda self, messages, tools=None: 1)
    monkeypatch.setattr(NewAPIClient, "get_ordered_candidates", lambda self, **kwargs: [{"id": "model-success", "intelligence": 7}])
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)

    class _FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"choices": [{"message": {"content": "复用"}}], "usage": {"total_tokens": 3}}

        async def text(self):
            return "ok"

    class _InjectedSession:
        def __init__(self):
            self.post_calls = 0
            self.closed = False

        def post(self, *args, **kwargs):
            self.post_calls += 1
            return _FakeResp()

        async def close(self):
            self.closed = True

    def fail_client_session(*_args, **_kwargs):
        raise AssertionError("应复用注入 session，不应创建逐请求 ClientSession")

    session = _InjectedSession()
    monkeypatch.setattr("clients.new_api_client.aiohttp.ClientSession", fail_client_session)

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1", session=session)
    first = run_async(client.chat_completion([{"role": "user", "content": "你好"}]))
    second = run_async(client.chat_completion([{"role": "user", "content": "再来"}]))

    assert first["choices"][0]["message"]["content"] == "复用"
    assert second["choices"][0]["message"]["content"] == "复用"
    assert session.post_calls == 2
    assert session.closed is False


def test_new_api_chat_completion_stream_finishes_request_on_success(monkeypatch):
    from clients.new_api_client import NewAPIClient

    recorded = []
    finished = []
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs) or 987),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )
    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock(return_value=None))
    monkeypatch.setattr(NewAPIClient, "estimate_complexity", lambda self, messages, tools=None: 1)
    monkeypatch.setattr(NewAPIClient, "get_ordered_candidates", lambda self, **kwargs: [{"id": "model-stream", "intelligence": 7}])
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)

    class _FakeContent:
        def __init__(self):
            self._lines = iter([
                'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'.encode("utf-8"),
                'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'.encode("utf-8"),
                b"data: [DONE]\n\n",
            ])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._lines)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class _FakeResp:
        status = 200
        content = _FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return "ok"

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return _FakeResp()

    monkeypatch.setattr("clients.new_api_client.aiohttp.ClientSession", lambda: _FakeSession())

    async def collect():
        client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
        return [chunk async for chunk in client.chat_completion_stream([{"role": "user", "content": "流式"}])]

    chunks = run_async(collect())

    assert len(chunks) == 2
    assert recorded
    assert recorded[0]["request"]["stream"] is True
    assert recorded[0]["request"]["model"] == "model-stream"
    assert finished
    assert finished[0]["log_id"] == 987
    assert finished[0]["response_status"] == 200
    assert finished[0]["status"] == "stream_success"
    assert finished[0]["response"]["content"] == "你好"


def test_new_api_chat_completion_stream_requests_stream_capable_candidates(monkeypatch):
    from clients.new_api_client import NewAPIClient

    captured = {}
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **_kwargs: 111),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **_kwargs: None),
    )
    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock(return_value=None))
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)

    def fake_candidates(self, **kwargs):
        captured.update(kwargs)
        return [{"id": "model-stream", "intelligence": 7}]

    monkeypatch.setattr(NewAPIClient, "get_ordered_candidates", fake_candidates)

    class _FakeContent:
        def __init__(self):
            self._lines = iter([
                'data: {"choices":[{"delta":{"content":"流"}}]}\n\n'.encode("utf-8"),
                b"data: [DONE]\n\n",
            ])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._lines)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class _FakeResp:
        status = 200

        def __init__(self):
            self.content = _FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return "ok"

    class _FakeSession:
        def post(self, *args, **kwargs):
            return _FakeResp()

    async def collect():
        client = NewAPIClient(
            api_key="key",
            base_url="http://newapi.test/v1",
            session=_FakeSession(),
        )
        return [chunk async for chunk in client.chat_completion_stream(
            [{"role": "user", "content": "流式"}],
        )]

    chunks = run_async(collect())

    assert chunks[0]["choices"][0]["delta"]["content"] == "流"
    assert captured["required_capabilities"]["supports_stream"] is True


def test_new_api_client_uses_injected_session_for_chat_completion_stream(monkeypatch):
    from clients.new_api_client import NewAPIClient

    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **_kwargs: 654),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **_kwargs: None),
    )
    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock(return_value=None))
    monkeypatch.setattr(NewAPIClient, "estimate_complexity", lambda self, messages, tools=None: 1)
    monkeypatch.setattr(NewAPIClient, "get_ordered_candidates", lambda self, **kwargs: [{"id": "model-stream", "intelligence": 7}])
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)

    class _FakeContent:
        def __init__(self):
            self._lines = iter([
                'data: {"choices":[{"delta":{"content":"流"}}]}\n\n'.encode("utf-8"),
                'data: {"choices":[{"delta":{"content":"式"}}]}\n\n'.encode("utf-8"),
                b"data: [DONE]\n\n",
            ])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._lines)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class _FakeResp:
        status = 200

        def __init__(self):
            self.content = _FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return "ok"

    class _InjectedSession:
        def __init__(self):
            self.post_calls = 0
            self.closed = False

        def post(self, *args, **kwargs):
            self.post_calls += 1
            return _FakeResp()

        async def close(self):
            self.closed = True

    def fail_client_session(*_args, **_kwargs):
        raise AssertionError("应复用注入 session，不应创建逐请求 ClientSession")

    session = _InjectedSession()
    monkeypatch.setattr("clients.new_api_client.aiohttp.ClientSession", fail_client_session)

    async def collect():
        client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1", session=session)
        return [chunk async for chunk in client.chat_completion_stream([{"role": "user", "content": "流式"}])]

    chunks = run_async(collect())

    assert [c["choices"][0]["delta"]["content"] for c in chunks] == ["流", "式"]
    assert session.post_calls == 1
    assert session.closed is False


def test_new_api_chat_completion_stream_records_reasoning_metrics(monkeypatch):
    from clients.new_api_client import NewAPIClient

    recorded = []
    finished = []
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs) or 988),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )
    monkeypatch.setattr(NewAPIClient, "sync_models_to_registry", AsyncMock(return_value=None))
    monkeypatch.setattr(NewAPIClient, "estimate_complexity", lambda self, messages, tools=None: 1)
    monkeypatch.setattr(NewAPIClient, "get_ordered_candidates", lambda self, **kwargs: [{"id": "model-stream", "intelligence": 7}])
    monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)

    class _FakeContent:
        def __init__(self):
            self._lines = iter([
                'data: {"choices":[{"delta":{"reasoning_content":"先想"}}]}\n\n'.encode("utf-8"),
                'data: {"choices":[{"delta":{"reasoning_content":"一下"}}]}\n\n'.encode("utf-8"),
                'data: {"choices":[{"delta":{"content":"答"}}]}\n\n'.encode("utf-8"),
                b"data: [DONE]\n\n",
            ])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._lines)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class _FakeResp:
        status = 200
        content = _FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return "ok"

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return _FakeResp()

    monkeypatch.setattr("clients.new_api_client.aiohttp.ClientSession", lambda: _FakeSession())

    async def collect():
        client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
        return [chunk async for chunk in client.chat_completion_stream([{"role": "user", "content": "流式"}])]

    chunks = run_async(collect())

    assert len(chunks) == 3
    assert recorded
    assert finished
    response = finished[0]["response"]
    assert response["content"] == "答"
    assert response["reasoning_content"] == "先想一下"
    assert response["stream_metrics"]["reasoning_char_count"] == 4
    assert response["stream_metrics"]["content_char_count"] == 1
    assert response["stream_metrics"]["reasoning_elapsed_ms"] >= 0


def test_new_api_payload_applies_enable_thinking_policy():
    from clients.new_api_client import NewAPIClient

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
    messages = [{"role": "user", "content": "你好"}]

    auto_payload = client._build_payload(
        messages, None, 0, False, "deepseek-r1",
        enable_thinking="auto",
    )
    assert auto_payload["thinking"] == {"type": "disabled"}

    enabled_payload = client._build_payload(
        messages, None, 0, False, "deepseek-r1",
        enable_thinking="true",
    )
    assert enabled_payload["enable_thinking"] is True
    assert "thinking" not in enabled_payload

    disabled_payload = client._build_payload(
        messages, None, 0, False, "qwen3",
        enable_thinking="false",
    )
    assert disabled_payload["enable_thinking"] is False
    assert disabled_payload["thinking"] == {"type": "disabled"}


def test_ai_daily_simple_llm_sets_ai_daily_source(monkeypatch):
    from core.llm_trace_context import get_llm_trace_vars
    from creatures.nanobot.prompts.skills.news_search import tool as news_tool

    seen = []

    async def fake_chat_completion(self, **kwargs):
        seen.append(get_llm_trace_vars())
        return {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}

    monkeypatch.setattr(
        "clients.new_api_client.NewAPIClient.chat_completion",
        fake_chat_completion,
    )

    raw = news_tool._call_llm_simple("sys", "prompt")

    assert json.loads(raw)["ok"] is True
    assert seen
    assert seen[0][2] == "ai_daily"


def test_news_daily_quality_sets_dedicated_source(monkeypatch):
    from core.llm_trace_context import get_llm_trace_vars
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline import summarize_quality

    seen = []

    async def fake_chat_completion(self, **kwargs):
        seen.append(get_llm_trace_vars())
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "title": "日报",
                                "subtitle": "摘要",
                                "verdict": "可用",
                                "top_story": {
                                    "title": "头条",
                                    "what_happened": "发生了事",
                                    "why_it_matters": "有影响",
                                    "source_ids": [1],
                                    "confidence": "high",
                                },
                                "highlights": [],
                                "details": [],
                                "watchlist": [],
                                "missing_info": [],
                                "closing": "完",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(
        "clients.new_api_client.NewAPIClient.chat_completion",
        fake_chat_completion,
    )

    result = summarize_quality.summarize_quality(
        [
            {
                "source_id": 1,
                "title": "标题",
                "source_name": "来源",
                "source_group": "official",
                "domain": "example.com",
                "summary": "摘要",
            }
        ],
        {"title": "fallback"},
    )

    assert result["title"] == "日报"
    assert seen
    assert seen[0][2] == "news_daily.summarize_quality"
