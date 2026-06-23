from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@dataclass
class FakePushEnvelope:
    target_type: str
    target_id: str
    envelope: dict[str, Any]


class FakeDb:
    pass


def _request(user_id: str = "u-stream", session_id: str = "private_u-stream") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        session_id=session_id,
        query="流式请求",
        files=[],
        sender_name="",
        session_name=None,
        message_id="",
        source_message_ids=[],
        client_meta={"platform": "qq"},
    )


async def _runner_sets(result_holder: dict[str, Any], key: str, value: Any) -> None:
    result_holder[key] = value


def _callbacks(
    calls: dict[str, list[Any]],
    *,
    reply_meta: dict[str, Any] | None = None,
    push_ok: bool = True,
):
    from api.chat_streaming_result import ChatStreamResultCallbacks

    async def drain_stream_queue_until_task_done(stream_queue, runner_task):
        calls.setdefault("drain", []).append((stream_queue, runner_task))

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
        return 3

    def expand_chat_transport_answer(answer: str) -> str:
        calls.setdefault("expand", []).append(answer)
        return f"expanded:{answer}"

    def build_chat_push_envelope(req, **kwargs):
        calls.setdefault("envelope", []).append((req, kwargs))
        return FakePushEnvelope(
            target_type="private",
            target_id=req.user_id,
            envelope={"reply": kwargs["answer"], "meta": {"chat_type": kwargs["chat_type"]}},
        )

    async def push_envelope_to_qq(target_type: str, target_id: str, envelope: dict[str, Any]) -> bool:
        calls.setdefault("push", []).append((target_type, target_id, envelope))
        return push_ok

    return ChatStreamResultCallbacks(
        drain_stream_queue_until_task_done=drain_stream_queue_until_task_done,
        pop_bridge_reply_meta=pop_bridge_reply_meta,
        private_prompt_audit_failure_meta=private_prompt_audit_failure_meta,
        finalize_private_buffer=finalize_private_buffer,
        persist_chat_turn=persist_chat_turn,
        expand_chat_transport_answer=expand_chat_transport_answer,
        build_chat_push_envelope=build_chat_push_envelope,
        push_envelope_to_qq=push_envelope_to_qq,
    )


def _context(
    result_holder: dict[str, Any],
    runner_task: asyncio.Task[Any],
    calls: dict[str, list[Any]],
    *,
    req: Any | None = None,
    reply_meta: dict[str, Any] | None = None,
):
    from api.chat_streaming_result import ChatStreamResultContext

    req = req or _request()
    return ChatStreamResultContext(
        req=req,
        persist_req=req,
        bridge=object(),
        result_holder=result_holder,
        runner_task=runner_task,
        stream_queue=asyncio.Queue(maxsize=1),
        platform="qq",
        bridge_meta={"chat_type": "private", "is_group": False},
        guardrail_status="safe",
        private_timing_meta={"private_decision": "ok"},
        empty_assistant_placeholder="（无回复内容）",
        callbacks=_callbacks(calls, reply_meta=reply_meta),
    )


def test_chat_streaming_result_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_streaming_result.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source
    assert "from core.daily_digest import push_envelope_to_qq" not in source
    assert "import core.daily_digest" not in source


@pytest.mark.asyncio
async def test_persist_stream_result_success_uses_result_holder_and_request_db():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", "最终答案"))
    req = _request(user_id="u-success")
    context = _context(result_holder, runner_task, calls, req=req)
    db = FakeDb()

    await persist_stream_result_after_runner_done(context, push=False, persist_db=db)

    assert calls["finalize"] == [("u-success", "最终答案", True)]
    assert calls["persist"] == [
        (
            db,
            req,
            "最终答案",
            "safe",
            {
                "assistant_meta": None,
                "assistant_processed": None,
                "timing_meta": {"private_decision": "ok"},
            },
        )
    ]
    assert calls.get("push") is None


@pytest.mark.asyncio
async def test_persist_stream_result_prompt_audit_failure_uses_meta_and_skips_push():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", ""))
    context = _context(
        result_holder,
        runner_task,
        calls,
        req=_request(user_id="u-audit"),
        reply_meta={"_agent_result": "prompt_v2_audit_failed"},
    )
    db = FakeDb()

    await persist_stream_result_after_runner_done(context, push=True, persist_db=db)

    assert calls["audit_meta"] == [()]
    assert calls["finalize"] == [("u-audit", "（无回复内容）", True)]
    persisted = calls["persist"][0]
    assert persisted[0] is db
    assert persisted[2] == "（无回复内容）"
    assert persisted[4]["assistant_meta"] == {
        "kind": "empty_reply",
        "no_context": True,
        "agent_result": "prompt_v2_audit_failed",
    }
    assert persisted[4]["assistant_processed"] == 1
    assert calls.get("push") is None


@pytest.mark.asyncio
async def test_persist_stream_result_background_push_uses_unit_of_work_and_drain(monkeypatch):
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", "后台答案"))
    context = _context(result_holder, runner_task, calls, req=_request(user_id="u-bg"))
    uow_db = FakeDb()

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = uow_db
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)

    await persist_stream_result_after_runner_done(
        context,
        push=True,
        persist_db=None,
        drain_stream=True,
    )

    assert calls["drain"] == [(context.stream_queue, runner_task)]
    assert calls["persist"][0][0] is uow_db
    assert calls["expand"] == ["后台答案"]
    assert calls["envelope"][0][1]["answer"] == "expanded:后台答案"
    assert calls["push"] == [
        ("private", "u-bg", {"reply": "expanded:后台答案", "meta": {"chat_type": "private"}})
    ]
