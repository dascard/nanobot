import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.database import LLMApiRequestLog
from core.tracing import _json_dumps, _redact


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
    assert json.loads(row.response_json)["detail"] == "bad gateway"


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
        result = asyncio.run(llm._client.chat.completions.create(
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

    create = AsyncMock(return_value=iter([{"choices": [{"delta": {"content": "流式"}}]}]))
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
    with llm_trace_scope(trace_id="trace-s", run_id="run-s", source="replyer"):
        result = asyncio.run(llm._client.chat.completions.create(
            model="stream-model",
            messages=[{"role": "user", "content": "流式"}],
            stream=True,
            temperature=0.1,
        ))

    assert recorded
    row = recorded[0]
    assert row["trace_id"] == "trace-s"
    assert row["run_id"] == "run-s"
    assert row["source"] == "replyer"
    assert row["request"]["stream"] is True
    assert row["request"]["model"] == "stream-model"
    assert row["request"]["messages"][0]["content"] == "流式"
    assert list(result)
    assert finished
    assert finished[0]["log_id"] == 789
    assert finished[0]["status"] == "stream_created"


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
    result = asyncio.run(client.chat_completion([{"role": "user", "content": "你好"}]))

    assert result["choices"][0]["message"]["content"] == "成功"
    assert recorded
    assert recorded[0]["request"]["model"] == "model-success"
    assert finished
    assert finished[0]["log_id"] == 654
    assert finished[0]["response_status"] == 200
    assert finished[0]["status"] == "success"
    assert finished[0]["response"]["choices"][0]["message"]["content"] == "成功"


def test_news_search_simple_llm_sets_news_search_source(monkeypatch):
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
    assert seen[0][2] == "news_search"


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
