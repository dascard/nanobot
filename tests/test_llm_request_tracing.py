import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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


def test_openai_sdk_tracer_records_non_stream_request(monkeypatch):
    from core.llm_trace_context import llm_trace_scope
    from core.llm_sdk_tracing import install_openai_chat_completion_tracer

    recorded = []
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs)),
    )

    create = AsyncMock(side_effect=RuntimeError("stop after trace"))
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
        with pytest.raises(RuntimeError, match="stop after trace"):
            asyncio.run(llm._client.chat.completions.create(
                model="manual-model",
                messages=[{"role": "user", "content": "你好"}],
                temperature=0,
                extra_body={"reasoning": {"enabled": True}},
            ))

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


def test_openai_sdk_tracer_records_stream_request(monkeypatch):
    from core.llm_trace_context import llm_trace_scope
    from core.llm_sdk_tracing import install_openai_chat_completion_tracer

    recorded = []
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs)),
    )

    create = AsyncMock(side_effect=RuntimeError("stop after trace"))
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
        with pytest.raises(RuntimeError, match="stop after trace"):
            asyncio.run(llm._client.chat.completions.create(
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


def test_compaction_direct_http_records_request(monkeypatch):
    from core import compaction
    from core.llm_trace_context import llm_trace_scope

    recorded = []
    monkeypatch.setattr(compaction, "COMPACT_API_KEY", "compact-key")
    monkeypatch.setattr(compaction, "COMPACT_BASE_URL", "http://compact.test/v1")
    monkeypatch.setattr(compaction, "COMPACT_MODEL", "compact-model")
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs)),
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
