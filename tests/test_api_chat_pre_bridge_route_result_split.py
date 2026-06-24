from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        user_id="u-route-result",
        session_id="private_u-route-result",
        query="原始问题",
        files=["old.png"],
    )


def _callbacks(calls: dict[str, list[Any]]):
    from api.chat_pre_bridge_route_result import ChatPreBridgeRouteCallbacks

    def clone_chat_request(req: Any, **updates: Any) -> Any:
        calls.setdefault("clone", []).append((req, updates))
        data = dict(vars(req))
        data.update(updates)
        return SimpleNamespace(**data)

    def persist_chat_turn(req: Any, answer: str, guardrail_status: str | None = None, **kwargs: Any) -> int:
        calls.setdefault("persist", []).append((req, answer, guardrail_status, kwargs))
        return 7

    def chat_response_payload(req: Any, **kwargs: Any) -> dict[str, Any]:
        calls.setdefault("payload", []).append((req, kwargs))
        return {"payload": kwargs}

    async def finalize_private_buffer(user_id: str, answer: str | None = None, *, clear_window: bool = True) -> None:
        calls.setdefault("finalize", []).append((user_id, answer, clear_window))

    return ChatPreBridgeRouteCallbacks(
        clone_chat_request=clone_chat_request,
        persist_chat_turn=persist_chat_turn,
        chat_response_payload=chat_response_payload,
        finalize_private_buffer=finalize_private_buffer,
    )


def test_chat_pre_bridge_route_result_module_does_not_import_parent_routes_or_runtime_side_effects():
    path = ROOT / "api/chat_pre_bridge_route_result.py"
    assert path.exists()
    source = _source("api/chat_pre_bridge_route_result.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "FastAPI" not in source
    assert "APIRouter" not in source
    assert "StreamingResponse" not in source
    assert "BackgroundTasks" not in source
    assert "HTTPException" not in source
    assert "SessionLocal" not in source
    assert "UnitOfWork" not in source
    assert "ChatLog" not in source
    assert "ConversationTurn" not in source
    assert "db.commit(" not in source
    assert "build_chat_runtime_payload" not in source
    assert "ChatRuntimeInput" not in source
    assert "get_bridge(" not in source
    assert "bridge.handle_message" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


@pytest.mark.asyncio
async def test_early_return_persists_when_answer_is_present_and_builds_payload():
    from api.chat_pre_bridge_decision import ChatPreBridgeEarlyReturn
    from api.chat_pre_bridge_route_result import (
        ChatPreBridgeRouteEarlyResponse,
        resolve_pre_bridge_route_result,
    )

    calls: dict[str, list[Any]] = {}
    req = _request()
    pre_bridge = ChatPreBridgeEarlyReturn(
        status="ok",
        reason="casual",
        answer="传输回复",
        source="casual_template",
        intent="寒暄",
        guardrail_status="casual_template",
        persist_answer="原始回复",
        persist_guardrail_status="casual_template",
        persist_timing_meta={"action": "reply_later"},
    )

    result = await resolve_pre_bridge_route_result(
        req,
        pre_bridge,
        callbacks=_callbacks(calls),
    )

    assert isinstance(result, ChatPreBridgeRouteEarlyResponse)
    assert calls["persist"] == [
        (
            req,
            "原始回复",
            "casual_template",
            {"timing_meta": {"action": "reply_later"}},
        )
    ]
    assert calls["payload"] == [
        (
            req,
            {
                "status": "ok",
                "reason": "casual",
                "answer": "传输回复",
                "source": "casual_template",
                "intent": "寒暄",
                "guardrail_status": "casual_template",
                "include_answer_chunks": True,
            },
        )
    ]
    assert result.payload["payload"]["answer"] == "传输回复"


@pytest.mark.asyncio
async def test_early_return_without_persist_only_builds_payload():
    from api.chat_pre_bridge_decision import ChatPreBridgeEarlyReturn
    from api.chat_pre_bridge_route_result import resolve_pre_bridge_route_result

    calls: dict[str, list[Any]] = {}
    req = _request()
    pre_bridge = ChatPreBridgeEarlyReturn(
        status="silent",
        reason="private_buffer_follower",
        persist_answer=None,
    )

    result = await resolve_pre_bridge_route_result(
        req,
        pre_bridge,
        callbacks=_callbacks(calls),
    )

    assert calls.get("persist") is None
    assert calls["payload"][0][1] == {
        "status": "silent",
        "reason": "private_buffer_follower",
        "answer": "",
        "source": "",
        "intent": "",
        "guardrail_status": None,
        "include_answer_chunks": True,
    }
    assert result.payload["payload"]["status"] == "silent"


@pytest.mark.asyncio
async def test_continue_outcome_clones_persist_request_and_exposes_fields():
    from api.chat_pre_bridge_decision import ChatPreBridgeContinue
    from api.chat_pre_bridge_route_result import (
        ChatPreBridgeRouteContinue,
        resolve_pre_bridge_route_result,
    )

    calls: dict[str, list[Any]] = {}
    req = _request()
    decision = SimpleNamespace(action="reply_now")
    pre_bridge = ChatPreBridgeContinue(
        final_query="合并问题",
        final_files=["new.png"],
        private_decision=decision,
        private_timing_meta={"action": "reply_now"},
        guardrail_status="safe",
        classifier_ran=True,
    )

    result = await resolve_pre_bridge_route_result(
        req,
        pre_bridge,
        callbacks=_callbacks(calls),
    )

    assert isinstance(result, ChatPreBridgeRouteContinue)
    assert calls["clone"] == [(req, {"query": "合并问题", "files": ["new.png"]})]
    assert result.final_query == "合并问题"
    assert result.final_files == ["new.png"]
    assert result.private_decision is decision
    assert result.private_timing_meta == {"action": "reply_now"}
    assert result.guardrail_status == "safe"
    assert result.classifier_ran is True
    assert result.persist_req.query == "合并问题"
    assert result.persist_req.files == ["new.png"]
    assert calls.get("persist") is None
    assert calls.get("payload") is None


@pytest.mark.asyncio
async def test_guardrail_silent_finalizes_buffer_persists_silent_answer_and_returns_payload():
    from api.chat_pre_bridge_decision import ChatPreBridgeContinue
    from api.chat_pre_bridge_route_result import (
        ChatPreBridgeRouteEarlyResponse,
        resolve_pre_bridge_route_result,
    )

    calls: dict[str, list[Any]] = {}
    req = _request()
    pre_bridge = ChatPreBridgeContinue(
        final_query="合并后问题",
        final_files=["safe.png"],
        private_decision=SimpleNamespace(action="reply_now"),
        private_timing_meta={"action": "reply_now"},
        guardrail_status="silent",
        classifier_ran=True,
    )

    result = await resolve_pre_bridge_route_result(
        req,
        pre_bridge,
        callbacks=_callbacks(calls),
    )

    assert isinstance(result, ChatPreBridgeRouteEarlyResponse)
    persist_req = calls["clone"][0][0]
    cloned_req = calls["persist"][0][0]
    assert persist_req is req
    assert cloned_req.query == "合并后问题"
    assert cloned_req.files == ["safe.png"]
    assert calls["finalize"] == [("u-route-result", None, True)]
    assert calls["persist"] == [
        (
            cloned_req,
            "（数据中转，自动静默）",
            "silent",
            {"timing_meta": {"action": "reply_now"}},
        )
    ]
    assert calls["payload"][0][1] == {
        "status": "silent",
        "reason": "guardrail_silent",
        "guardrail_status": "silent",
        "include_answer_chunks": True,
    }
    assert result.payload["payload"]["reason"] == "guardrail_silent"


@pytest.mark.asyncio
async def test_parent_pre_bridge_route_result_wrapper_remains_patchable(monkeypatch):
    from api import chat_pre_bridge_route_result
    from api import routes

    calls: list[tuple[Any, Any, Any]] = []
    persist_calls: list[tuple[Any, Any, str, str | None, dict[str, Any]]] = []

    async def fake_resolver(req: Any, pre_bridge: Any, *, callbacks: Any) -> Any:
        calls.append((req, pre_bridge, callbacks))
        return chat_pre_bridge_route_result.ChatPreBridgeRouteEarlyResponse(
            payload={"status": "patched"}
        )

    def fake_persist_chat_turn(
        db: Any,
        req: Any,
        answer: str,
        guardrail_status: str | None = None,
        **kwargs: Any,
    ) -> int:
        persist_calls.append((db, req, answer, guardrail_status, kwargs))
        return 9

    monkeypatch.setattr(chat_pre_bridge_route_result, "resolve_pre_bridge_route_result", fake_resolver)
    monkeypatch.setattr(routes, "_persist_chat_turn", fake_persist_chat_turn)
    db = object()
    req = _request()
    pre_bridge = object()

    assert routes._chat_pre_bridge_route_callbacks.__module__ == "api.routes"
    assert routes._resolve_pre_bridge_route_result.__module__ == "api.routes"

    result = await routes._resolve_pre_bridge_route_result(db, req, pre_bridge)

    assert result.payload == {"status": "patched"}
    assert calls[0][0] is req
    assert calls[0][1] is pre_bridge
    assert calls[0][2].clone_chat_request is routes._clone_chat_request
    assert calls[0][2].chat_response_payload is routes._chat_response_payload
    assert calls[0][2].finalize_private_buffer is routes._finalize_private_buffer

    callbacks = routes._chat_pre_bridge_route_callbacks(db)
    assert callbacks.persist_chat_turn(
        req,
        "持久化回复",
        guardrail_status="silent",
        timing_meta={"action": "reply_now"},
    ) == 9
    assert persist_calls == [
        (
            db,
            req,
            "持久化回复",
            "silent",
            {"timing_meta": {"action": "reply_now"}},
        )
    ]
