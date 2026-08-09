"""Provider 运行证据汇总测试。"""

from __future__ import annotations

import json

from core.database import LLMApiRequestLog
from clients.provider_runtime_evidence import (
    summarize_provider_runtime_evidence,
)
from core.time_utils import db_now_naive


def test_runtime_evidence_merges_alias_metrics_and_observes_capabilities(
    db_session,
):
    now = db_now_naive()
    db_session.add_all([
        LLMApiRequestLog(
            provider="new-api",
            model="operator-model",
            status="stream_success",
            error_category="none",
            request_json=json.dumps({
                "messages": [{
                    "role": "user",
                    "content": [{
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,secret-image"},
                    }],
                }],
                "tools": [{"type": "function", "function": {"name": "lookup"}}],
            }),
            response_json=json.dumps({
                "choices": [{
                    "message": {
                        "content": "结果正文不得出现在汇总中",
                        "reasoning_content": "隐藏推理不得出现在汇总中",
                    },
                }],
            }),
            actual_sent_tools_json=json.dumps(["lookup"]),
            cache_status="hit",
            cache_hit_tokens=60,
            cache_miss_tokens=40,
            cache_write_tokens=10,
            input_tokens=100,
            output_tokens=20,
            first_token_latency_ms=100,
            latency_ms=400,
            cost_microusd=123,
            created_at=now,
        ),
        LLMApiRequestLog(
            provider="newapi",
            model="operator-model",
            status="failed",
            error_category="authentication",
            request_json="{}",
            response_json="{}",
            cache_status="error",
            latency_ms=200,
            created_at=now,
        ),
        LLMApiRequestLog(
            provider="custom-registry",
            model="plain-model",
            status="success",
            error_category="none",
            request_json="{}",
            response_json="{}",
            cache_status="not_reported",
            created_at=now,
        ),
        LLMApiRequestLog(
            provider="not-configured",
            model="ignored-model",
            status="success",
            error_category="none",
            created_at=now,
        ),
    ])
    db_session.commit()

    result = summarize_provider_runtime_evidence(
        db_session,
        ["newapi", "custom", "empty"],
        aliases_by_provider={"custom": ["custom-registry"]},
    )

    newapi = result["newapi"]
    assert newapi["requests"] == 2
    assert newapi["successful_requests"] == 1
    assert newapi["failed_requests"] == 1
    assert newapi["incomplete_requests"] == 0
    assert newapi["success_rate"] == 0.5
    assert newapi["avg_first_token_latency_ms"] == 100
    assert newapi["avg_total_latency_ms"] == 300
    assert newapi["input_tokens"] == 100
    assert newapi["output_tokens"] == 20
    assert newapi["cache_hit_tokens"] == 60
    assert newapi["cache_miss_tokens"] == 40
    assert newapi["cache_write_tokens"] == 10
    assert newapi["cache_hit_token_ratio"] == 0.6
    assert newapi["cost_microusd"] == 123
    assert newapi["by_error_category"] == {
        "authentication": 1,
        "none": 1,
    }
    assert newapi["observed_capabilities"] == [
        "cache_usage",
        "chat_completion",
        "reasoning_content",
        "streaming",
        "tool_calling",
        "vision",
    ]
    assert set(newapi["capability_evidence"]) == set(
        newapi["observed_capabilities"]
    )
    assert all(
        item["source"] == "successful_llm_trace"
        for item in newapi["capability_evidence"].values()
    )
    assert newapi["last_observed_at"] is not None

    serialized = json.dumps(result, ensure_ascii=False)
    assert "secret-image" not in serialized
    assert "结果正文" not in serialized
    assert "隐藏推理" not in serialized
    assert "not-configured" not in result
    assert result["custom"]["requests"] == 1


def test_runtime_evidence_keeps_missing_capabilities_unknown(db_session):
    result = summarize_provider_runtime_evidence(db_session, ["newapi"])

    assert result["newapi"]["requests"] == 0
    assert result["newapi"]["success_rate"] is None
    assert result["newapi"]["observed_capabilities"] == []
    assert result["newapi"]["capability_evidence"] == {}
