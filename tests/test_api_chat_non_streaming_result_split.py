from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


class FakeDb:
    pass


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _request(
    user_id: str = "u-non-stream",
    session_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        session_id=session_id or f"private_{user_id}",
        query="非流式请求",
        files=[],
        sender_name="",
        session_name=None,
        message_id="",
        source_message_ids=[],
        client_meta={"platform": "qq"},
    )


def _callbacks(
    calls: dict[str, list[Any]],
    *,
    reply_meta: dict[str, Any] | None = None,
    pending: int = 3,
    expand_raises: bool = False,
):
    from api.chat_non_streaming_result import ChatNonStreamingResultCallbacks

    def pop_bridge_reply_meta(bridge, session_id: str):
        calls.setdefault("pop_meta", []).append((bridge, session_id))
        return reply_meta or {}

    def private_prompt_audit_failure_meta():
        calls.setdefault("audit_meta", []).append(())
        return {"kind": "empty_reply", "no_context": True, "agent_result": "prompt_v2_audit_failed"}

    async def finalize_private_buffer(user_id: str, answer: str | None = None, *, clear_window: bool = True):
        calls.setdefault("finalize", []).append((user_id, answer, clear_window))

    def persist_chat_turn(db, req, answer, guardrail_status=None, **kwargs):
        calls.setdefault("persist", []).append((db, req, answer, guardrail_status, kwargs))
        return pending

    def expand_chat_transport_answer(answer: str) -> str:
        calls.setdefault("expand", []).append(answer)
        if expand_raises:
            raise RuntimeError("expand failed")
        return f"expanded:{answer}"

    def chat_response_payload(req, **kwargs):
        calls.setdefault("payload", []).append((req, kwargs))
        return {
            "status": kwargs["status"],
            "answer": kwargs.get("answer", ""),
            "reply": kwargs.get("answer", ""),
            "messages": [{"type": "text", "text": kwargs.get("answer", "")}],
            "reply_meta": kwargs.get("reply_meta"),
            "unprocessed_logs": kwargs.get("unprocessed_logs"),
            "guardrail_status": kwargs.get("guardrail_status"),
        }

    return ChatNonStreamingResultCallbacks(
        pop_bridge_reply_meta=pop_bridge_reply_meta,
        private_prompt_audit_failure_meta=private_prompt_audit_failure_meta,
        finalize_private_buffer=finalize_private_buffer,
        persist_chat_turn=persist_chat_turn,
        expand_chat_transport_answer=expand_chat_transport_answer,
        chat_response_payload=chat_response_payload,
    )


def _context(
    calls: dict[str, list[Any]],
    *,
    req: Any | None = None,
    answer: str = "最终答案",
    reply_meta: dict[str, Any] | None = None,
    pending: int = 3,
    evolution_threshold: int = 5,
    expand_raises: bool = False,
):
    from api.chat_non_streaming_result import ChatNonStreamingResultContext

    req = req or _request()
    return ChatNonStreamingResultContext(
        req=req,
        persist_req=req,
        bridge=object(),
        answer=answer,
        platform="qq",
        bridge_meta={"chat_type": "private", "is_group": False},
        guardrail_status="safe",
        private_timing_meta={"private_decision": "ok"},
        empty_assistant_placeholder="（无回复内容）",
        evolution_threshold=evolution_threshold,
        callbacks=_callbacks(
            calls,
            reply_meta=reply_meta,
            pending=pending,
            expand_raises=expand_raises,
        ),
    )


def test_chat_non_streaming_result_module_does_not_import_parent_routes_or_runtime_side_effects():
    source = _source("api/chat_non_streaming_result.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "StreamingResponse" not in source
    assert "APIRouter" not in source
    assert "BackgroundTasks" not in source
    assert "HTTPException" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source
    assert "bridge.handle_message" not in source
    assert "call_bridge_non_streaming" not in source
    assert "SessionLocal" not in source
    assert "UnitOfWork" not in source
    assert "db.commit(" not in source
    assert "push_envelope_to_qq" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


@pytest.mark.asyncio
async def test_finalize_non_streaming_success_persists_raw_answer_and_returns_transport_payload():
    from api.chat_non_streaming_result import finalize_non_streaming_chat_result

    calls: dict[str, list[Any]] = {}
    req = _request(user_id="u-success")
    context = _context(calls, req=req, pending=7, evolution_threshold=5)
    db = FakeDb()

    result = await finalize_non_streaming_chat_result(db, context)

    assert calls["pop_meta"] == [(context.bridge, "private_u-success")]
    assert calls["expand"] == ["最终答案"]
    assert calls["finalize"] == [("u-success", "最终答案", True)]
    assert calls["persist"] == [
        (
            db,
            req,
            "最终答案",
            "safe",
            {
                "timing_meta": {"private_decision": "ok"},
            },
        )
    ]
    assert calls["payload"][0][1]["answer"] == "expanded:最终答案"
    assert calls["payload"][0][1]["reply_meta"] == {}
    assert calls["payload"][0][1]["unprocessed_logs"] == 7
    assert result.payload is not None
    assert result.payload["status"] == "ok"
    assert result.payload["answer"] == "expanded:最终答案"
    assert result.pending == 7
    assert result.should_trigger_evolution is True
    assert result.prompt_audit_failed is False


@pytest.mark.asyncio
async def test_finalize_non_streaming_prompt_audit_failure_persists_placeholder_and_skips_payload():
    from api.chat_non_streaming_result import finalize_non_streaming_chat_result

    calls: dict[str, list[Any]] = {}
    context = _context(
        calls,
        req=_request(user_id="u-audit"),
        reply_meta={"_agent_result": "prompt_v2_audit_failed"},
        pending=9,
        evolution_threshold=5,
    )
    db = FakeDb()

    result = await finalize_non_streaming_chat_result(db, context)

    assert calls["audit_meta"] == [()]
    assert calls["finalize"] == [("u-audit", "（无回复内容）", True)]
    persisted = calls["persist"][0]
    assert persisted[0] is db
    assert persisted[2] == "（无回复内容）"
    assert persisted[3] == "safe"
    assert persisted[4] == {
        "assistant_meta": {
            "kind": "empty_reply",
            "no_context": True,
            "agent_result": "prompt_v2_audit_failed",
        },
        "assistant_processed": 1,
        "timing_meta": {"private_decision": "ok"},
    }
    assert calls.get("expand") is None
    assert calls.get("payload") is None
    assert result.payload is None
    assert result.pending is None
    assert result.should_trigger_evolution is False
    assert result.prompt_audit_failed is True


@pytest.mark.asyncio
async def test_finalize_non_streaming_keeps_raw_answer_when_transport_expand_fails():
    from api.chat_non_streaming_result import finalize_non_streaming_chat_result

    calls: dict[str, list[Any]] = {}
    context = _context(calls, answer="图片 [generated:image]", expand_raises=True)
    db = FakeDb()

    result = await finalize_non_streaming_chat_result(db, context)

    assert calls["expand"] == ["图片 [generated:image]"]
    assert calls["persist"][0][2] == "图片 [generated:image]"
    assert calls["payload"][0][1]["answer"] == "图片 [generated:image]"
    assert result.payload is not None
    assert result.payload["answer"] == "图片 [generated:image]"


def test_parent_non_streaming_chat_delegates_result_finalize_and_keeps_http_boundaries():
    source = _source("api/routes.py")

    assert "chat_route_runner" in source
    assert "chat_route_runner.run_non_streaming_chat_response" in source
    assert "HTTPException(" in source
    assert "async def _do_chat" not in source
    assert "chat_runtime_facade.call_bridge_non_streaming" not in source
    assert "except asyncio.CancelledError" not in source
    assert "SAFE_STREAM_ERROR_MESSAGE" in source
