import hashlib
import json
from datetime import datetime, timedelta

import pytest

from core.database import (
    ChatLog,
    ConversationTurn,
    OutboundDeliveryControl,
    OutboundGenerationAttempt,
    OutboundRun,
    ProactiveOutreachLog,
    User,
)


@pytest.fixture(autouse=True)
def _seed_proactive_outreach_delivery_control(db_session):
    db_session.add(OutboundDeliveryControl(
        source_type="proactive_outreach",
        mode="legacy_direct",
        cutover_epoch=0,
        effective_from=datetime(1970, 1, 1),
        protocol_version=2,
        writer_version=0,
    ))
    db_session.commit()


def _install_route_response(monkeypatch, body, *, raw_body: bytes | None = None):
    from clients import classifier_client
    from core.tracing import LLMRequestTracer

    route = {
        "provider_id": "unit",
        "base_url": "http://unit.test/v1",
        "api_key": "unit-key",
        "model": "unit-model",
        "timeout": 5,
        "temperature": 0,
        "max_tokens": 80,
        "enable_thinking": "false",
    }

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return self.status

        def read(self):
            return raw_body or json.dumps(body, ensure_ascii=False).encode("utf-8")

    class FakeOpener:
        def open(self, _request, timeout=0):
            assert timeout == 5
            return FakeResponse()

    monkeypatch.setattr(
        classifier_client,
        "ensure_model_route_enabled",
        lambda _route_key, _route=None: dict(route),
    )
    monkeypatch.setattr(
        classifier_client.urllib.request,
        "build_opener",
        lambda *_args, **_kwargs: FakeOpener(),
    )
    monkeypatch.setattr(
        LLMRequestTracer,
        "record_request",
        staticmethod(lambda **_kwargs: 0),
    )
    monkeypatch.setattr(
        LLMRequestTracer,
        "finish_request",
        staticmethod(lambda **_kwargs: None),
    )


def _model_response(*, content, finish_reason="stop", reasoning_content="模型推理"):
    from clients.classifier_client import ModelRouteResponse

    return ModelRouteResponse(
        content=content,
        reasoning_content=reasoning_content,
        finish_reason=finish_reason,
        usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        raw_response={},
    )


_SENSITIVE_GENERATION_ERRORS = (
    pytest.param(
        "Authorization: Bearer AUTH-GENERATION-SECRET",
        "AUTH-GENERATION-SECRET",
        id="authorization",
    ),
    pytest.param(
        "https://provider.test/fail?token=URL-GENERATION-SECRET",
        "URL-GENERATION-SECRET",
        id="credential-url",
    ),
    pytest.param(
        "上游异常 RANDOM-GENERATION-SECRET",
        "RANDOM-GENERATION-SECRET",
        id="unkeyed-random",
    ),
    pytest.param(
        "BODY-GENERATION-SECRET" + "x" * 5000,
        "BODY-GENERATION-SECRET",
        id="oversized-body",
    ),
)


@pytest.mark.asyncio
async def test_delivery_rechecks_research_control_syntax_before_state_or_publisher(
    db_session,
):
    from core.proactive_outreach import deliver_outreach_once

    published = []

    async def publisher(*args):
        published.append(args)
        return True

    result = await deliver_outreach_once(
        user_id="research-final-gate-user",
        idempotency_key="research-final-gate-key",
        grounding={
            "research": {
                "sources": [
                    {"url": "https://example.test/one"},
                    {"url": "https://example.test/two"},
                ]
            }
        },
        judge_should=True,
        judge_reason="研究候选",
        next_check_at=datetime(2026, 7, 10, 14, 0, 0),
        next_intent="",
        message=(
            "研究正文 [CQ:image,file=/etc/passwd]\n\n"
            "来源（本次真实检索）：\n"
            "1. 来源一\n   https://example.test/one\n"
            "2. 来源二\n   https://example.test/two"
        ),
        forced=False,
        db=db_session,
        publisher=publisher,
    )

    assert result["status"] == "generation_error"
    assert result["error_type"] == "unsafe_control_syntax"
    assert published == []
    assert db_session.query(ProactiveOutreachLog).count() == 0


@pytest.mark.asyncio
async def test_truncated_judge_remains_fail_closed_after_max_silence(
    db_session,
):
    from core import proactive_outreach

    first_at = datetime(2026, 7, 10, 12, 0, 0)
    judge_calls = []
    published = []

    def truncated_judge(_grounding, *, now, **_kwargs):
        judge_calls.append(now)
        return {
            "should_reach_out": None,
            "reason": "主动外呼 Judge 非正常结束: length",
            "next_check_at": (now + timedelta(minutes=30)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": "model_truncated",
        }

    async def publisher(*args):
        published.append(args)
        return True

    first = await proactive_outreach.run_outreach_once(
        "truncated-anchor-user",
        db=db_session,
        now=first_at,
        max_silence_min=60,
        thread_extractor=lambda _messages: [],
        judge_fn=truncated_judge,
        publisher=publisher,
    )
    second = await proactive_outreach.run_outreach_once(
        "truncated-anchor-user",
        db=db_session,
        now=first_at + timedelta(days=10),
        max_silence_min=60,
        thread_extractor=lambda _messages: [],
        judge_fn=truncated_judge,
        generator_fn=lambda *_args, **_kwargs: "最长沉默后的强制候选",
        publisher=publisher,
    )

    rows = db_session.query(ProactiveOutreachLog).filter_by(
        user_id="truncated-anchor-user"
    ).order_by(ProactiveOutreachLog.created_at.asc()).all()
    assert first["status"] == "judge_error"
    assert first["error_type"] == "model_truncated"
    assert first["log_id"] == rows[0].id
    assert rows[0].status == "evaluation_error"
    assert "judge:model_truncated" in rows[0].judge_reason
    assert second["status"] == "judge_error"
    assert second["error_type"] == "model_truncated"
    assert judge_calls == [first_at, first_at + timedelta(days=10)]
    assert published == []
    assert all(row.forced is False for row in rows)


def test_call_model_route_response_preserves_openai_completion_metadata(monkeypatch):
    from clients.classifier_client import call_model_route_response

    body = {
        "choices": [{
            "message": {
                "content": "<think>正文内推理</think>最终答案",
                "reasoning_content": "独立推理字段",
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    _install_route_response(monkeypatch, body)

    response = call_model_route_response(route_key="timing_proactive", user_message="测试")

    assert response.content == "最终答案"
    assert response.reasoning_content == "独立推理字段"
    assert response.finish_reason == "stop"
    assert response.usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


@pytest.mark.parametrize("content", [None, {"unexpected": "object"}, ["text"], 123, True])
def test_call_model_route_response_rejects_non_string_content(monkeypatch, content):
    from clients.classifier_client import call_model_route_response

    body = {
        "choices": [{
            "message": {"content": content},
            "finish_reason": "stop",
        }],
    }
    _install_route_response(monkeypatch, body)

    with pytest.raises(ValueError, match="message.content"):
        call_model_route_response(route_key="outreach_generate", user_message="测试")


def test_call_model_route_legacy_wrapper_returns_cleaned_text(monkeypatch):
    from clients.classifier_client import call_model_route

    body = {
        "choices": [{
            "message": {
                "content": "<think>不要暴露</think>最终答案",
                "reasoning_content": "独立推理字段",
            },
            "finish_reason": "stop",
        }],
        "usage": {"total_tokens": 5},
    }
    _install_route_response(monkeypatch, body)

    assert call_model_route(route_key="timing_proactive", user_message="测试") == "最终答案"


def test_call_model_route_response_marks_malformed_success_body_as_error(monkeypatch):
    from clients.classifier_client import call_model_route_response
    from core.tracing import LLMRequestTracer

    _install_route_response(monkeypatch, {"choices": []})
    finished = []
    monkeypatch.setattr(
        LLMRequestTracer,
        "finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )

    with pytest.raises(ValueError, match=r"choices\[0\]"):
        call_model_route_response(route_key="timing_proactive", user_message="测试")

    assert len(finished) == 1
    assert finished[0]["status"] == "error"
    assert finished[0]["response_status"] == 200


def test_call_model_route_response_omits_raw_body_for_invalid_json(monkeypatch):
    from clients.classifier_client import call_model_route_response
    from core.tracing import LLMRequestTracer

    raw_body = b'{"choices": ['
    _install_route_response(
        monkeypatch,
        {},
        raw_body=raw_body,
    )
    finished = []
    monkeypatch.setattr(
        LLMRequestTracer,
        "finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )

    with pytest.raises(ValueError, match="invalid JSON"):
        call_model_route_response(route_key="outreach_judge", user_message="测试")

    assert len(finished) == 1
    assert finished[0]["status"] == "error"
    response = finished[0]["response"]
    assert response["response_body_omitted"] is True
    assert response["response_body_chars"] == len(raw_body.decode("utf-8"))
    assert response["response_body_sha256"] == hashlib.sha256(raw_body).hexdigest()
    assert response["response_body_truncated"] is False
    assert "raw_body_preview" not in response
    assert raw_body.decode("utf-8") not in json.dumps(response, ensure_ascii=False)


@pytest.mark.parametrize(
    ("content", "finish_reason", "expected_error"),
    [
        ('{"should_speak": true, "reason": "可以补充"}', "stop", None),
        ('{"should_speak": "false", "reason": "类型错误"}', "stop", "contract_error"),
        ('{"should_speak": true, "reason": "被截断"}', "length", "model_truncated"),
    ],
)
def test_group_proactive_judge_uses_route_budget_and_strict_response_contract(
    monkeypatch,
    content,
    finish_reason,
    expected_error,
):
    from clients import classifier_client

    calls = []
    response = _model_response(content=content, finish_reason=finish_reason)
    monkeypatch.setattr(
        classifier_client,
        "call_model_route_response",
        lambda **kwargs: calls.append(kwargs) or response,
    )

    result = classifier_client.judge_proactive("群聊上下文")

    assert calls[0]["route_key"] == "timing_proactive"
    assert "max_tokens" not in calls[0]
    assert result["error_type"] == expected_error
    assert result["should_speak"] is (expected_error is None)


def test_group_proactive_accepts_legacy_completion_without_finish_reason(monkeypatch):
    from clients import classifier_client

    response = _model_response(
        content='{"should_speak": true, "reason": "可以补充"}',
        finish_reason=None,
    )
    monkeypatch.setattr(
        classifier_client,
        "call_model_route_response",
        lambda **_kwargs: response,
    )

    result = classifier_client.judge_proactive("有人问了技术问题")

    assert result["should_speak"] is True
    assert result["error_type"] is None


def test_recent_thread_extraction_exposes_truncation_diagnostics():
    from core.proactive_outreach import extract_recent_threads

    diagnostics = {}
    response = _model_response(content='["未完成话题"]', finish_reason="length")
    result = extract_recent_threads(
        [{"role": "user", "content": "昨天的项目还没做完", "created_at": "now"}],
        llm_call=lambda **_kwargs: response,
        diagnostics=diagnostics,
    )

    assert result == []
    assert diagnostics == {
        "status": "error",
        "error_type": "model_truncated",
        "finish_reason": "length",
    }


@pytest.mark.parametrize("error_text,secret", _SENSITIVE_GENERATION_ERRORS)
def test_recent_thread_extraction_omits_untrusted_exception_text(
    error_text,
    secret,
):
    from core.proactive_outreach import extract_recent_threads

    diagnostics = {}

    def failed_model_call(**_kwargs):
        raise RuntimeError(error_text)

    result = extract_recent_threads(
        [{"role": "user", "content": "昨天的项目还没做完", "created_at": "now"}],
        llm_call=failed_model_call,
        diagnostics=diagnostics,
    )

    assert result == []
    assert diagnostics == {
        "status": "error",
        "error_type": "model_error",
    }
    assert secret not in json.dumps(diagnostics, ensure_ascii=False)


@pytest.mark.parametrize("error_text,secret", _SENSITIVE_GENERATION_ERRORS)
def test_judge_outreach_omits_untrusted_exception_text(error_text, secret):
    from core.proactive_outreach import judge_outreach

    def failed_model_call(**_kwargs):
        raise RuntimeError(error_text)

    result = judge_outreach(
        {"user_id": "judge-runtime-error", "recent_messages": []},
        now=datetime(2026, 7, 10, 12, 0, 0),
        model_call=failed_model_call,
    )

    assert result["error_type"] == "model_error"
    assert result["reason"] == "主动外呼 Judge 调用失败"
    assert secret not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    ("content", "finish_reason"),
    [
        (
            '{"should_reach_out": false, "reason": "稍后", '
            '"next_check_in_hours": 2, "next_intent": "再问"}',
            "length",
        ),
        ("", "stop"),
        ('{"should_reach_out": false, "reason": "被截断"', "stop"),
        (
            '{"should_reach_out": "false", "reason": "类型错误", '
            '"next_check_in_hours": 2, "next_intent": "再问"}',
            "stop",
        ),
        (
            '{"should_reach_out": false, "reason": "缺少下次检查", '
            '"next_intent": "再问"}',
            "stop",
        ),
        (
            '{"should_reach_out": false, "reason": "数字字符串", '
            '"next_check_in_hours": "2", "next_intent": "再问", '
            '"outreach_kind": "message", "research_query": ""}',
            "stop",
        ),
        (
            '{"should_reach_out": false, "reason": "缺少完整字段", '
            '"next_check_in_hours": 2, "next_intent": "再问"}',
            "stop",
        ),
    ],
    ids=[
        "length",
        "empty",
        "partial-json",
        "string-bool",
        "missing-next-check",
        "string-hours",
        "missing-kind-fields",
    ],
)
def test_judge_outreach_reports_contract_error_for_invalid_completion(
    content,
    finish_reason,
):
    from core.proactive_outreach import judge_outreach

    response = _model_response(content=content, finish_reason=finish_reason)

    result = judge_outreach(
        {"user_id": "outreach-user", "recent_messages": []},
        now=datetime(2026, 7, 10, 12, 0, 0),
        model_call=lambda **_kwargs: response,
    )

    assert result["should_reach_out"] is None
    assert str(result.get("error_type") or "")


def test_judge_outreach_accepts_complete_stop_contract():
    from core.proactive_outreach import judge_outreach

    calls = []
    response = _model_response(
        content=(
            '{"should_reach_out": false, "reason": "下午再问", '
            '"next_check_in_hours": 2, "next_intent": "问项目进度", '
            '"outreach_kind": "message", "research_query": "", '
            '"topic_type":"none","topic":"","evidence_ids":[]}'
        ),
    )

    result = judge_outreach(
        {"user_id": "outreach-user", "recent_messages": []},
        now=datetime(2026, 7, 10, 12, 0, 0),
        model_call=lambda **kwargs: calls.append(kwargs) or response,
    )

    assert result["should_reach_out"] is False
    assert result["error_type"] is None
    assert result["next_check_at"] == "2026-07-10T14:00:00"
    assert result["next_intent"] == "问项目进度"
    assert calls[0]["route_key"] == "outreach_judge"


def test_private_outreach_judge_rejects_missing_finish_reason():
    from core.proactive_outreach import judge_outreach

    response = _model_response(
        content=(
            '{"should_reach_out": true, "reason": "看似完整但停止原因缺失", '
            '"next_check_in_hours": 2, "next_intent": "继续跟进", '
            '"outreach_kind": "message", "research_query": ""}'
        ),
        finish_reason=None,
    )

    result = judge_outreach(
        {"user_id": "outreach-user", "recent_messages": []},
        now=datetime(2026, 7, 10, 12, 0, 0),
        model_call=lambda **_kwargs: response,
    )

    assert result["should_reach_out"] is None
    assert result["error_type"] == "model_finish_error"
    assert result["finish_reason"] is None


def test_private_outreach_generator_rejects_missing_finish_reason():
    from core.proactive_outreach import (
        OutreachModelContractError,
        generate_outreach_message,
    )

    response = _model_response(
        content="看似完整但停止原因缺失",
        finish_reason=None,
    )

    with pytest.raises(OutreachModelContractError) as exc_info:
        generate_outreach_message(
            {"user_id": "outreach-user", "recent_messages": []},
            "测试",
            model_call=lambda **_kwargs: response,
        )

    assert exc_info.value.error_type == "model_finish_error"


@pytest.mark.asyncio
async def test_generator_truncation_error_type_reaches_normal_candidate_result(db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    response = _model_response(content="只生成了一半", finish_reason="length")

    def truncated_generator(grounding, reason):
        return proactive_outreach.generate_outreach_message(
            grounding,
            reason,
            model_call=lambda **_kwargs: response,
        )

    result = await proactive_outreach.run_outreach_once(
        "generator-truncated-normal",
        db=db_session,
        now=now,
        max_silence_min=999999,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: {
            "should_reach_out": True,
            "reason": "有具体话题",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
        generator_fn=truncated_generator,
    )

    assert result["status"] == "generation_error"
    assert result["error_type"] == "model_truncated"
    rows = db_session.query(ProactiveOutreachLog).filter_by(
        user_id="generator-truncated-normal"
    ).all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    run = db_session.query(OutboundRun).one()
    attempt = db_session.query(OutboundGenerationAttempt).one()
    assert rows[0].outbound_run_id == run.id
    assert run.status == "failed"
    assert run.failure_type == "model_truncated"
    assert attempt.status == "failed"
    assert attempt.error_type == "model_truncated"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_text,secret", _SENSITIVE_GENERATION_ERRORS)
async def test_generator_runtime_error_uses_fixed_diagnostic_everywhere(
    error_text,
    secret,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)

    class UntrustedGeneratorError(RuntimeError):
        error_type = "attacker_controlled_type"

    def failed_generator(*_args, **_kwargs):
        raise UntrustedGeneratorError(error_text)

    result = await proactive_outreach.run_outreach_once(
        "generator-runtime-error",
        db=db_session,
        now=now,
        max_silence_min=999999,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: {
            "should_reach_out": True,
            "reason": "有具体话题",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
        generator_fn=failed_generator,
    )

    row = db_session.query(ProactiveOutreachLog).one()
    run = db_session.query(OutboundRun).one()
    attempt = db_session.query(OutboundGenerationAttempt).one()
    combined = json.dumps(
        {
            "result": result,
            "run": {
                "failure_type": run.failure_type,
                "failure_summary": run.failure_summary,
            },
            "attempt": {
                "error_type": attempt.error_type,
                "error_summary": attempt.error_summary,
            },
            "grounding": json.loads(row.grounding_json),
        },
        ensure_ascii=False,
    )

    assert result["status"] == "generation_error"
    assert result["error_type"] == "generation_error"
    assert result["reason"] == "主动外呼正文生成失败"
    assert run.failure_type == "generation_error"
    assert run.failure_summary == "主动外呼正文生成失败"
    assert attempt.error_type == "generation_error"
    assert attempt.error_summary == "主动外呼正文生成失败"
    assert secret not in combined
    assert "attacker_controlled_type" not in combined


@pytest.mark.asyncio
async def test_max_silence_generator_truncation_fails_closed(db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    response = _model_response(content="只生成了一半", finish_reason="length")
    db_session.add(ProactiveOutreachLog(
        user_id="generator-truncated-forced",
        idempotency_key="outreach:generator-truncated-forced:pending",
        status="pending",
        created_at=now - timedelta(hours=49),
        next_check_at=now,
    ))
    db_session.commit()

    def truncated_generator(grounding, reason):
        return proactive_outreach.generate_outreach_message(
            grounding,
            reason,
            model_call=lambda **_kwargs: response,
        )

    published = []

    async def publisher(*args):
        published.append(args)
        return True

    result = await proactive_outreach.run_outreach_once(
        "generator-truncated-forced",
        db=db_session,
        now=now,
        max_silence_min=48 * 60,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: {
            "should_reach_out": True,
            "reason": "最长静默后仍有具体新话题",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
        generator_fn=truncated_generator,
        publisher=publisher,
    )

    assert result["status"] == "generation_error"
    assert result["error_type"] == "model_truncated"
    assert result["forced"] is False
    assert published == []
    row = db_session.query(ProactiveOutreachLog).filter_by(
        user_id="generator-truncated-forced",
        status="failed",
    ).one()
    grounding = json.loads(row.grounding_json)
    assert "forced_fallback" not in grounding
    assert row.forced is False
    attempt = db_session.query(OutboundGenerationAttempt).one()
    assert attempt.status == "failed"
    assert attempt.error_type == "model_truncated"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_text,secret", _SENSITIVE_GENERATION_ERRORS)
async def test_max_silence_generator_contract_error_uses_fixed_diagnostic_everywhere(
    error_text,
    secret,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    db_session.add(ProactiveOutreachLog(
        user_id="forced-contract-error",
        idempotency_key="outreach:forced-contract-error:pending",
        status="pending",
        created_at=now - timedelta(hours=49),
        next_check_at=now,
    ))
    db_session.commit()

    def failed_generator(*_args, **_kwargs):
        raise proactive_outreach.OutreachModelContractError(
            error_text,
            error_type="model_truncated",
        )

    result = await proactive_outreach.run_outreach_once(
        "forced-contract-error",
        db=db_session,
        now=now,
        max_silence_min=48 * 60,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: {
            "should_reach_out": True,
            "reason": "最长静默后仍有具体新话题",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
        generator_fn=failed_generator,
        publisher=lambda *_args: True,
    )

    row = db_session.query(ProactiveOutreachLog).filter_by(status="failed").one()
    run = db_session.get(OutboundRun, row.outbound_run_id)
    attempt = db_session.query(OutboundGenerationAttempt).one()
    combined = json.dumps(
        {
            "result": result,
            "run": {
                "failure_type": run.failure_type,
                "failure_summary": run.failure_summary,
            },
            "attempt": {
                "error_type": attempt.error_type,
                "error_summary": attempt.error_summary,
            },
            "grounding": json.loads(row.grounding_json),
        },
        ensure_ascii=False,
    )

    assert result["status"] == "generation_error"
    assert result["error_type"] == "model_truncated"
    assert result["forced"] is False
    assert attempt.status == "failed"
    assert attempt.error_type == "model_truncated"
    assert attempt.error_summary == "主动外呼正文生成被截断"
    assert "forced_fallback" not in json.loads(row.grounding_json)
    assert row.forced is False
    assert secret not in combined


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["   \n\t", "<think>只有推理，没有正文</think>"],
    ids=["blank", "think-only"],
)
async def test_deliver_outreach_once_rejects_empty_message_without_side_effects(
    message,
    db_session,
):
    from core.proactive_outreach import deliver_outreach_once

    published = []

    async def publisher(target_type, target_id, text):
        published.append((target_type, target_id, text))
        return True

    await deliver_outreach_once(
        user_id="outreach-user",
        idempotency_key=f"outreach:empty:{len(message)}",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="测试空输出",
        next_check_at=datetime(2026, 7, 10, 13, 0, 0),
        next_intent="",
        message=message,
        forced=False,
        db=db_session,
        publisher=publisher,
    )

    assert published == []
    assert db_session.query(ProactiveOutreachLog).count() == 0


@pytest.mark.asyncio
async def test_run_outreach_once_does_not_persist_pending_for_judge_error(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {"user_id": "outreach-user", "recent_messages": []},
    )
    monkeypatch.setattr(
        proactive_outreach,
        "judge_outreach",
        lambda *_args, **_kwargs: {
            "should_reach_out": None,
            "reason": "模型输出不符合合约",
            "next_check_at": (now + timedelta(hours=1)).isoformat(),
            "next_intent": "",
            "error_type": "contract_error",
        },
    )
    monkeypatch.setattr(
        proactive_outreach,
        "generate_outreach_message",
        lambda *_args, **_kwargs: pytest.fail("Judge error 不应调用 Generator"),
    )

    result = await proactive_outreach.run_outreach_once(
        "outreach-user",
        db=db_session,
        now=now,
        max_silence_min=999999,
    )

    assert result.get("error_type") == "contract_error"
    assert result.get("status") != "pending"
    rows = db_session.query(ProactiveOutreachLog).all()
    assert len(rows) == 1
    assert rows[0].status == "evaluation_error"
    assert db_session.query(ProactiveOutreachLog).filter_by(status="pending").count() == 0


@pytest.mark.asyncio
async def test_post_clear_pending_can_reuse_same_key_from_pre_clear_cancelled_row(db_session):
    from core import proactive_outreach

    user_id = "post-clear-same-key"
    clear_at = datetime(2026, 7, 10, 10, 0, 0)
    now = clear_at + timedelta(hours=1)
    next_check_at = now + timedelta(hours=2)
    idempotency_key = proactive_outreach._outreach_key(
        user_id,
        next_check_at,
        forced=False,
    )
    old_row = ProactiveOutreachLog(
        user_id=user_id,
        idempotency_key=idempotency_key,
        status="cancelled",
        created_at=clear_at - timedelta(minutes=1),
        next_check_at=next_check_at,
        message="",
    )
    db_session.add_all([
        User(id=user_id, history_clear_at=clear_at),
        old_row,
    ])
    db_session.commit()

    result = await proactive_outreach.run_outreach_once(
        user_id,
        db=db_session,
        now=now,
        max_silence_min=999999,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: {
            "should_reach_out": False,
            "reason": "清除后重新安排",
            "next_check_at": next_check_at.isoformat(),
            "next_intent": "等待新话题",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
    )

    db_session.expire_all()
    rows = db_session.query(ProactiveOutreachLog).filter_by(user_id=user_id).all()
    assert result == {"status": "pending", "forced": False, "log_id": old_row.id}
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].created_at > clear_at
    assert rows[0].idempotency_key == idempotency_key


@pytest.mark.asyncio
async def test_pending_can_reuse_same_key_from_non_clear_cancelled_row(db_session):
    from core import proactive_outreach

    user_id = "non-clear-cancelled-pending"
    now = datetime(2026, 7, 10, 11, 0, 0)
    next_check_at = now + timedelta(hours=2)
    idempotency_key = proactive_outreach._outreach_key(
        user_id,
        next_check_at,
        forced=False,
    )
    old_row = ProactiveOutreachLog(
        user_id=user_id,
        idempotency_key=idempotency_key,
        status="cancelled",
        created_at=now - timedelta(minutes=1),
        next_check_at=next_check_at,
        message="",
    )
    db_session.add(old_row)
    db_session.commit()

    result = await proactive_outreach.run_outreach_once(
        user_id,
        db=db_session,
        now=now,
        max_silence_min=999999,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: {
            "should_reach_out": False,
            "reason": "重新安排",
            "next_check_at": next_check_at.isoformat(),
            "next_intent": "等待新话题",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
    )

    db_session.expire_all()
    row = db_session.get(ProactiveOutreachLog, old_row.id)
    assert result == {"status": "pending", "forced": False, "log_id": old_row.id}
    assert row.status == "pending"
    assert row.created_at == now


@pytest.mark.asyncio
@pytest.mark.parametrize("outreach_kind", ["message", "research"])
async def test_candidate_delivery_reuses_same_key_from_cancelled_row(
    outreach_kind,
    db_session,
):
    from core import proactive_outreach
    from core.proactive_research import ResearchResult, ResearchSource

    user_id = f"cancelled-candidate-{outreach_kind}"
    now = datetime(2026, 7, 10, 11, 0, 0)
    idempotency_key = proactive_outreach._outreach_key(
        user_id,
        now,
        forced=False,
    )
    old_row = ProactiveOutreachLog(
        user_id=user_id,
        idempotency_key=idempotency_key,
        status="cancelled",
        created_at=now - timedelta(minutes=1),
        message="",
    )
    db_session.add(old_row)
    db_session.commit()
    published = []

    async def publisher(*args):
        published.append(args)
        return True

    async def research_fn(request):
        return ResearchResult(
            request_id=request.request_id,
            trace_id="trace-cancelled-reuse",
            status="draft_ready",
            draft="研究型候选正文",
            sources=(
                ResearchSource("tool-1", "来源一", "https://example.test/one"),
                ResearchSource("tool-2", "来源二", "https://example.test/two"),
            ),
        )

    result = await proactive_outreach.run_outreach_once(
        user_id,
        db=db_session,
        now=now,
        max_silence_min=999999,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: {
            "should_reach_out": True,
            "reason": "有可跟进内容",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": outreach_kind,
            "research_query": "Generative Agents memory" if outreach_kind == "research" else "",
            "error_type": None,
        },
        generator_fn=lambda *_args, **_kwargs: "普通候选正文",
        research_fn=research_fn,
        publisher=publisher,
    )

    db_session.expire_all()
    rows = db_session.query(ProactiveOutreachLog).filter_by(user_id=user_id).all()
    expected_message = "研究型候选正文" if outreach_kind == "research" else "普通候选正文"
    assert result["status"] == "sent"
    assert result["log_id"] == old_row.id
    assert result["forced"] is False
    assert result["deduplicated"] is False
    assert isinstance(result["run_id"], int)
    assert isinstance(result["outbox_id"], int)
    assert published == [("private", user_id, expected_message)]
    assert len(rows) == 1
    assert rows[0].status == "sent"


@pytest.mark.parametrize("terminal_status", ["sending", "sent", "failed"])
def test_pending_upsert_preserves_terminal_same_key_without_unique_error(
    terminal_status,
    db_session,
):
    from core import proactive_outreach

    user_id = f"terminal-pending-conflict-{terminal_status}"
    now = datetime(2026, 7, 10, 11, 0, 0)
    key = proactive_outreach._outreach_key(
        user_id,
        now + timedelta(hours=2),
        forced=False,
    )
    terminal = ProactiveOutreachLog(
        user_id=user_id,
        idempotency_key=key,
        status=terminal_status,
        created_at=now - timedelta(hours=1),
        message="终态内容",
    )
    db_session.add(terminal)
    db_session.commit()

    row = proactive_outreach._upsert_pending_schedule(
        db_session,
        user_id=user_id,
        idempotency_key=key,
        grounding={"user_id": user_id},
        judge_reason="不应覆盖终态",
        next_check_at=now + timedelta(hours=2),
        next_intent="",
        created_at=now,
    )

    db_session.expire_all()
    stored = db_session.get(ProactiveOutreachLog, terminal.id)
    assert row.id == terminal.id
    assert stored.status == terminal_status
    assert stored.message == "终态内容"
    assert db_session.query(ProactiveOutreachLog).filter_by(user_id=user_id).count() == 1


@pytest.mark.parametrize("target_status", ["cancelled", "sent"])
def test_pending_upsert_resolves_target_key_conflict_with_other_current_pending(
    target_status,
    db_session,
):
    from core import proactive_outreach

    user_id = f"pending-target-conflict-{target_status}"
    now = datetime(2026, 7, 10, 11, 0, 0)
    current = ProactiveOutreachLog(
        user_id=user_id,
        idempotency_key=f"outreach:{user_id}:current",
        status="pending",
        created_at=now - timedelta(hours=2),
        next_check_at=now,
    )
    target_key = proactive_outreach._outreach_key(
        user_id,
        now + timedelta(hours=2),
        forced=False,
    )
    target = ProactiveOutreachLog(
        user_id=user_id,
        idempotency_key=target_key,
        status=target_status,
        created_at=now - timedelta(hours=1),
        message="历史终态" if target_status == "sent" else "",
    )
    db_session.add_all([current, target])
    db_session.commit()

    row = proactive_outreach._upsert_pending_schedule(
        db_session,
        user_id=user_id,
        idempotency_key=target_key,
        grounding={"user_id": user_id},
        judge_reason="重新安排",
        next_check_at=now + timedelta(hours=2),
        next_intent="",
        created_at=now,
    )

    db_session.expire_all()
    stored_current = db_session.get(ProactiveOutreachLog, current.id)
    stored_target = db_session.get(ProactiveOutreachLog, target.id)
    if target_status == "cancelled":
        assert row.id == target.id
        assert stored_current.status == "cancelled"
        assert stored_target.status == "pending"
        assert stored_target.created_at == now - timedelta(hours=2)
    else:
        assert row.id == target.id
        assert stored_current.status == "pending"
        assert stored_target.status == "sent"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["sent", "failed"])
async def test_no_candidate_reports_terminal_same_key_as_duplicate(
    terminal_status,
    db_session,
):
    from core import proactive_outreach

    user_id = f"terminal-no-candidate-{terminal_status}"
    now = datetime(2026, 7, 10, 11, 0, 0)
    next_check_at = now + timedelta(hours=2)
    key = proactive_outreach._outreach_key(user_id, next_check_at, forced=False)
    terminal = ProactiveOutreachLog(
        user_id=user_id,
        idempotency_key=key,
        status=terminal_status,
        created_at=now - timedelta(hours=1),
        next_check_at=next_check_at,
        message="终态内容",
    )
    db_session.add(terminal)
    db_session.commit()

    result = await proactive_outreach.run_outreach_once(
        user_id,
        db=db_session,
        now=now,
        max_silence_min=999999,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: {
            "should_reach_out": False,
            "reason": "不再发送",
            "next_check_at": next_check_at.isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
    )

    assert result == {
        "status": "skipped_duplicate",
        "log_id": terminal.id,
        "forced": False,
    }


def test_build_outreach_grounding_uses_post_clear_private_conversation_turns(db_session):
    from core.proactive_outreach import build_outreach_grounding

    user_id = "outreach-user"
    session_id = f"private_{user_id}"
    cutoff = datetime(2026, 7, 10, 8, 0, 0)
    now = datetime(2026, 7, 10, 12, 0, 0)
    db_session.add(User(id=user_id, history_clear_at=cutoff))
    db_session.add_all([
        ConversationTurn(
            user_id=user_id,
            session_id=session_id,
            role="user",
            content="清除点前的私聊",
            created_at=cutoff - timedelta(minutes=1),
        ),
        ConversationTurn(
            user_id=user_id,
            session_id=session_id,
            role="user",
            content="清除后的用户消息",
            created_at=datetime(2026, 7, 10, 9, 0, 0),
        ),
        ConversationTurn(
            user_id=user_id,
            session_id=session_id,
            role="assistant",
            content="清除后的助手回复",
            created_at=datetime(2026, 7, 10, 9, 5, 0),
        ),
        ConversationTurn(
            user_id=user_id,
            session_id="group_100",
            role="user",
            content="群聊不应进入主动私聊 grounding",
            created_at=datetime(2026, 7, 10, 10, 0, 0),
        ),
        ConversationTurn(
            user_id=user_id,
            session_id=session_id,
            role="tool",
            content="非 user/assistant 不应进入 grounding",
            created_at=datetime(2026, 7, 10, 11, 0, 0),
        ),
        ChatLog(
            user_id=user_id,
            session_id=session_id,
            role="user",
            content="ChatLog 干扰项不应进入 grounding",
            created_at=datetime(2026, 7, 10, 11, 30, 0),
        ),
    ])
    db_session.commit()
    extracted = []

    grounding = build_outreach_grounding(
        user_id,
        db=db_session,
        now=now,
        thread_extractor=lambda messages: extracted.extend(messages) or ["清除后的话题"],
    )

    assert [item["content"] for item in grounding["recent_messages"]] == [
        "清除后的用户消息",
        "清除后的助手回复",
    ]
    assert [item["role"] for item in grounding["recent_messages"]] == ["user", "assistant"]
    assert grounding["last_user_message"]["content"] == "清除后的用户消息"
    assert grounding["hours_since_last_user_message"] == pytest.approx(3.0)
    assert [item["content"] for item in extracted] == [
        "清除后的用户消息",
        "清除后的助手回复",
    ]


def test_active_hours_uses_post_clear_private_user_conversation_turns(db_session):
    from core.proactive_outreach import active_hours

    user_id = "outreach-user"
    session_id = f"private_{user_id}"
    cutoff = datetime(2026, 7, 10, 8, 0, 0)
    db_session.add(User(id=user_id, history_clear_at=cutoff))
    db_session.add_all([
        ConversationTurn(
            user_id=user_id,
            session_id=session_id,
            role="user",
            content="旧私聊",
            created_at=datetime(2026, 7, 10, 2, 0, 0),
        ),
        ConversationTurn(
            user_id=user_id,
            session_id=session_id,
            role="user",
            content="有效私聊",
            created_at=datetime(2026, 7, 10, 9, 0, 0),
        ),
        ConversationTurn(
            user_id=user_id,
            session_id=session_id,
            role="assistant",
            content="助手回复不计活跃小时",
            created_at=datetime(2026, 7, 10, 10, 0, 0),
        ),
        ConversationTurn(
            user_id=user_id,
            session_id="group_100",
            role="user",
            content="群聊不计主动私聊活跃小时",
            created_at=datetime(2026, 7, 10, 3, 0, 0),
        ),
        ChatLog(
            user_id=user_id,
            session_id=session_id,
            role="user",
            content="ChatLog 干扰项",
            created_at=datetime(2026, 7, 10, 4, 0, 0),
        ),
    ])
    db_session.commit()

    assert active_hours(user_id, db=db_session, min_samples=1) == {8, 9, 10}


@pytest.mark.asyncio
async def test_run_outreach_once_threads_now_into_grounding_and_pending_created_at(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    observed_now = []

    def fake_build_grounding(user_id, *, db, now):
        observed_now.append(now)
        return {"user_id": user_id, "recent_messages": []}

    monkeypatch.setattr(proactive_outreach, "build_outreach_grounding", fake_build_grounding)
    monkeypatch.setattr(
        proactive_outreach,
        "judge_outreach",
        lambda *_args, **_kwargs: {
            "should_reach_out": False,
            "reason": "稍后再问",
            "next_check_at": (now + timedelta(hours=1)).isoformat(),
            "next_intent": "问进度",
            "error_type": None,
        },
    )

    result = await proactive_outreach.run_outreach_once(
        "outreach-user",
        db=db_session,
        now=now,
        max_silence_min=999999,
    )

    row = db_session.query(ProactiveOutreachLog).one()
    assert result["status"] == "pending"
    assert observed_now == [now]
    assert row.created_at == now


def test_private_conversation_filter_treats_underscore_as_literal(db_session):
    from core.proactive_outreach import build_outreach_grounding

    now = datetime(2026, 7, 10, 12, 0, 0)
    db_session.add_all([
        ConversationTurn(
            user_id="outreach-user",
            session_id="private_outreach-user",
            role="user",
            content="有效私聊",
            created_at=now - timedelta(hours=2),
        ),
        ConversationTurn(
            user_id="outreach-user",
            session_id="privateXdecoy",
            role="user",
            content="不应进入私聊 grounding",
            created_at=now - timedelta(hours=1),
        ),
    ])
    db_session.commit()

    grounding = build_outreach_grounding(
        "outreach-user",
        db=db_session,
        now=now,
        thread_extractor=lambda _messages: [],
    )

    assert [item["content"] for item in grounding["recent_messages"]] == ["有效私聊"]


@pytest.mark.asyncio
async def test_post_clear_run_cancels_old_candidate_before_judging(db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    cutoff = now - timedelta(hours=1)
    db_session.add(User(id="outreach-user", history_clear_at=cutoff))
    db_session.add(ProactiveOutreachLog(
        user_id="outreach-user",
        idempotency_key="outreach:old-candidate",
        grounding_json='{"recent_threads":["已清除话题"]}',
        judge_should=True,
        judge_reason="旧理由",
        message="清除历史前生成的旧消息",
        status="candidate",
        created_at=cutoff - timedelta(minutes=1),
    ))
    db_session.commit()
    judge_calls = []
    published = []

    async def publisher(*args):
        published.append(args)
        return True

    result = await proactive_outreach.run_outreach_once(
        "outreach-user",
        db=db_session,
        now=now,
        max_silence_min=999999,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: judge_calls.append(True) or {
            "should_reach_out": False,
            "reason": "清除后暂无话题",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "error_type": None,
        },
        publisher=publisher,
    )

    old_row = db_session.query(ProactiveOutreachLog).filter_by(
        idempotency_key="outreach:old-candidate"
    ).one()
    assert result["status"] == "pending"
    assert old_row.status == "cancelled"
    assert judge_calls == [True]
    assert published == []


@pytest.mark.asyncio
async def test_pending_refresh_preserves_anchor_and_later_rejudges(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    first_created_at = datetime(2026, 7, 8, 13, 0, 0)
    first_check_at = datetime(2026, 7, 10, 12, 0, 0)
    db_session.add(ProactiveOutreachLog(
        user_id="silence-user",
        idempotency_key="outreach:silence-pending",
        status="pending",
        created_at=first_created_at,
        next_check_at=first_check_at,
    ))
    db_session.commit()
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {"user_id": "silence-user", "recent_messages": []},
    )

    pending = await proactive_outreach.run_outreach_once(
        "silence-user",
        db=db_session,
        now=first_check_at,
        max_silence_min=48 * 60,
        judge_fn=lambda *_args, **_kwargs: {
            "should_reach_out": False,
            "reason": "暂时不联系",
            "next_check_at": (first_check_at + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
    )

    refreshed = db_session.query(ProactiveOutreachLog).filter_by(status="pending").one()
    assert pending["status"] == "pending"
    assert refreshed.created_at == first_created_at

    published = []

    async def publisher(*args):
        published.append(args)
        return True

    rejudged_groundings = []

    def accept_after_silence(grounding, *, now, **_kwargs):
        rejudged_groundings.append(grounding)
        return {
            "should_reach_out": True,
            "reason": "出现了具体的新联系理由",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    evaluated = await proactive_outreach.run_outreach_once(
        "silence-user",
        db=db_session,
        now=first_check_at + timedelta(hours=2),
        max_silence_min=48 * 60,
        judge_fn=accept_after_silence,
        generator_fn=lambda *_args, **_kwargs: "达到最长沉默后的候选",
        publisher=publisher,
    )

    assert evaluated["status"] == "sent"
    assert evaluated["forced"] is False
    assert rejudged_groundings[0]["trigger"] == {
        "kind": "max_silence_evaluation",
        "requires_delivery": False,
        "max_silence_min": 48 * 60,
    }
    assert published == [("private", "silence-user", "达到最长沉默后的候选")]


@pytest.mark.asyncio
async def test_failed_judged_delivery_schedules_a_new_semantic_attempt(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    anchor = datetime(2026, 7, 8, 10, 0, 0)
    first_run_at = datetime(2026, 7, 10, 12, 0, 0)
    db_session.add(ProactiveOutreachLog(
        user_id="retry-user",
        idempotency_key="outreach:retry-pending",
        status="pending",
        created_at=anchor,
        next_check_at=first_run_at,
    ))
    db_session.commit()
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {"user_id": "retry-user", "recent_messages": []},
    )
    outcomes = iter((False, True))
    published = []

    async def publisher(*args):
        published.append(args)
        return next(outcomes)

    def accept(grounding, *, now, **_kwargs):
        return {
            "should_reach_out": True,
            "reason": "有具体的新联系理由",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    first = await proactive_outreach.run_outreach_once(
        "retry-user",
        db=db_session,
        now=first_run_at,
        max_silence_min=48 * 60,
        judge_fn=accept,
        generator_fn=lambda *_args, **_kwargs: "强制触达重试候选",
        publisher=publisher,
    )
    second = await proactive_outreach.run_outreach_once(
        "retry-user",
        db=db_session,
        now=first_run_at + timedelta(hours=1),
        max_silence_min=48 * 60,
        judge_fn=accept,
        generator_fn=lambda *_args, **_kwargs: "强制触达重试候选",
        publisher=publisher,
    )

    assert first["status"] == "failed"
    assert second["status"] == "sent"
    assert len(published) == 2
    attempts = (
        db_session.query(ProactiveOutreachLog)
        .filter_by(user_id="retry-user", forced=False)
        .order_by(ProactiveOutreachLog.id)
        .all()
    )
    assert [row.status for row in attempts] == ["failed", "sent"]
    assert attempts[0].idempotency_key != attempts[1].idempotency_key


@pytest.mark.asyncio
async def test_unknown_publisher_outcome_is_ambiguous_and_same_key_is_not_republished(
    db_session,
):
    from core.proactive_outreach import deliver_outreach_once

    published = []

    async def publisher(*args):
        published.append(args)
        return None

    kwargs = {
        "user_id": "unknown-publish-user",
        "idempotency_key": "outreach:unknown-publish",
        "grounding": {"recent_messages": []},
        "judge_should": True,
        "judge_reason": "测试不确定投递结果",
        "next_check_at": datetime(2026, 7, 10, 13, 0, 0),
        "next_intent": "",
        "message": "这条消息的远端结果未知。",
        "forced": True,
        "db": db_session,
        "publisher": publisher,
    }

    first = await deliver_outreach_once(**kwargs)
    second = await deliver_outreach_once(**kwargs)

    assert first["status"] == "ambiguous"
    assert first["error_type"] == "publish_outcome_unknown"
    assert second["status"] == "skipped_duplicate"
    assert published == [(
        "private",
        "unknown-publish-user",
        "这条消息的远端结果未知。",
    )]
    row = db_session.query(ProactiveOutreachLog).filter_by(
        idempotency_key="outreach:unknown-publish"
    ).one()
    assert row.status == "ambiguous"
    assert "publish_outcome_unknown" not in json.loads(row.grounding_json)


@pytest.mark.asyncio
async def test_research_publisher_normalizes_verified_url_variant(db_session):
    from core.proactive_outreach import deliver_outreach_once

    canonical_url = "https://example.test/report"
    published = []

    async def publisher(*args):
        published.append(args)
        return True

    result = await deliver_outreach_once(
        user_id="research-publisher-canonical",
        idempotency_key="outreach:research-publisher-canonical",
        grounding={
            "research": {
                "sources": [
                    {"url": canonical_url},
                    {"url": "https://example.test/two"},
                ],
            },
        },
        judge_should=True,
        judge_reason="研究候选",
        next_check_at=None,
        next_intent="",
        message="研究正文 https://example.test/report/?utm_source=search。",
        forced=False,
        db=db_session,
        publisher=publisher,
    )

    assert result["status"] == "sent"
    assert published == [(
        "private",
        "research-publisher-canonical",
        f"研究正文 {canonical_url}。",
    )]


@pytest.mark.asyncio
async def test_unknown_judged_publish_uses_independent_ambiguity_hold(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    anchor = datetime(2026, 7, 8, 10, 0, 0)
    first_run_at = datetime(2026, 7, 10, 12, 0, 0)
    db_session.add(ProactiveOutreachLog(
        user_id="unknown-hold-user",
        idempotency_key="outreach:unknown-hold-pending",
        status="pending",
        created_at=anchor,
        next_check_at=first_run_at,
    ))
    db_session.commit()
    monkeypatch.setattr(
        proactive_outreach,
        "build_outreach_grounding",
        lambda *_args, **_kwargs: {
            "user_id": "unknown-hold-user",
            "recent_messages": [],
        },
    )
    published = []

    async def publisher(*args):
        published.append(args)
        return None if len(published) == 1 else True

    def accept(grounding, *, now, **_kwargs):
        return {
            "should_reach_out": True,
            "reason": "有具体的新联系理由",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    first = await proactive_outreach.run_outreach_once(
        "unknown-hold-user",
        db=db_session,
        now=first_run_at,
        max_silence_min=48 * 60,
        ambiguous_hold_min=120,
        judge_fn=accept,
        generator_fn=lambda *_args, **_kwargs: "不确定投递候选",
        publisher=publisher,
    )
    held = await proactive_outreach.run_outreach_once(
        "unknown-hold-user",
        db=db_session,
        now=first_run_at + timedelta(hours=1),
        max_silence_min=48 * 60,
        ambiguous_hold_min=120,
        judge_fn=accept,
        generator_fn=lambda *_args, **_kwargs: "不应立即重发",
        publisher=publisher,
    )
    resumed = await proactive_outreach.run_outreach_once(
        "unknown-hold-user",
        db=db_session,
        now=first_run_at + timedelta(minutes=121),
        max_silence_min=48 * 60,
        ambiguous_hold_min=120,
        judge_fn=accept,
        generator_fn=lambda *_args, **_kwargs: "冻结结束后的新候选",
        publisher=publisher,
    )

    assert first["status"] == "ambiguous"
    assert held["status"] == "skipped_ambiguous"
    assert held["next_check_at"] == (
        first_run_at + timedelta(minutes=120)
    ).isoformat()
    assert resumed["status"] == "sent"
    assert len(published) == 2
    rows = (
        db_session.query(ProactiveOutreachLog)
        .filter_by(user_id="unknown-hold-user", forced=False)
        .order_by(ProactiveOutreachLog.id)
        .all()
    )
    assert [row.status for row in rows] == ["ambiguous", "sent"]
    assert rows[0].idempotency_key != rows[1].idempotency_key


@pytest.mark.asyncio
async def test_existing_empty_candidate_is_cancelled_and_rejudged(db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    db_session.add(ProactiveOutreachLog(
        user_id="empty-candidate-user",
        idempotency_key="outreach:empty-candidate",
        grounding_json="{}",
        judge_should=True,
        judge_reason="旧空候选",
        message="<think>只有推理</think>",
        status="candidate",
        created_at=now - timedelta(hours=1),
    ))
    db_session.commit()
    judge_calls = []

    result = await proactive_outreach.run_outreach_once(
        "empty-candidate-user",
        db=db_session,
        now=now,
        max_silence_min=999999,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: judge_calls.append(True) or {
            "should_reach_out": False,
            "reason": "空候选清理后重新判断",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
    )

    rows = (
        db_session.query(ProactiveOutreachLog)
        .filter_by(user_id="empty-candidate-user")
        .order_by(ProactiveOutreachLog.id)
        .all()
    )
    assert result["status"] == "pending"
    assert judge_calls == [True]
    assert [row.status for row in rows] == ["cancelled", "pending"]


@pytest.mark.asyncio
async def test_run_outreach_fails_closed_for_unknown_persisted_state(db_session):
    from core import proactive_outreach

    db_session.add(ProactiveOutreachLog(
        user_id="unknown-state-user",
        idempotency_key="outreach:unknown-state",
        status="typo-state",
        created_at=datetime(2026, 7, 10, 10, 0, 0),
    ))
    db_session.commit()
    judge_calls = []

    result = await proactive_outreach.run_outreach_once(
        "unknown-state-user",
        db=db_session,
        now=datetime(2026, 7, 10, 12, 0, 0),
        thread_extractor=lambda _messages: [],
        judge_fn=lambda *_args, **_kwargs: judge_calls.append(True) or {},
    )

    assert result["status"] == "state_error"
    assert result["delivery_state"] == "typo-state"
    assert judge_calls == []
