from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from api import chat_private_buffer


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _request(
    *,
    user_id: str = "u-pre-bridge",
    session_id: str = "private_u-pre-bridge",
    query: str = "你好",
    files: list[str] | None = None,
    merged_messages: list[str] | None = None,
    classification_request: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        session_id=session_id,
        query=query,
        files=files,
        merged_messages=merged_messages,
        classification_request=classification_request,
    )


class FakeLogger:
    def __init__(self) -> None:
        self.warning_calls: list[Any] = []
        self.info_calls: list[Any] = []

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.warning_calls.append(args)

    def info(self, *args: Any, **kwargs: Any) -> None:
        self.info_calls.append(args)


class FakeGate:
    def __init__(self, decision: Any, calls: list[dict[str, Any]]) -> None:
        self.decision = decision
        self.calls = calls

    async def classify(self, query: str, **kwargs: Any) -> Any:
        self.calls.append({"query": query, "kwargs": kwargs})
        return self.decision


class FakeStore:
    def __init__(self) -> None:
        self.begin_result: Any = None
        self.snapshot_result: Any = None
        self.begin_calls: list[dict[str, Any]] = []
        self.store_guardrail_calls: list[tuple[str, dict[str, Any]]] = []
        self.created_guardrail_tasks: list[asyncio.Task[Any]] = []

    async def begin_or_append(self, user_id: str, **kwargs: Any) -> Any:
        self.begin_calls.append({"user_id": user_id, "kwargs": kwargs})
        if self.begin_result is not None:
            return self.begin_result
        task = kwargs["guardrail_task_factory"]()
        self.created_guardrail_tasks.append(task)
        await task
        return chat_private_buffer.PrivateBufferOwnerStarted(buffer={})

    async def snapshot(self, user_id: str) -> Any:
        return self.snapshot_result

    async def store_guardrail_result(self, user_id: str, result: dict[str, Any]) -> None:
        self.store_guardrail_calls.append((user_id, result))


def _services(
    *,
    decision: Any | None = None,
    store: FakeStore | None = None,
    detect_results: list[dict[str, Any]] | None = None,
    wait_deadline_result: bool = True,
    casual_reply: str = "",
    superuser: bool = False,
    calls: dict[str, list[Any]] | None = None,
):
    from api.chat_pre_bridge_decision import ChatPreBridgeServices

    calls = calls if calls is not None else {}
    store = store or FakeStore()
    detect_results = list(detect_results or [{"status": "safe"}])
    gate_calls: list[dict[str, Any]] = []

    async def sleep(seconds: float) -> None:
        calls.setdefault("sleep", []).append(seconds)

    async def wait_private_buffer_deadline(user_id: str) -> bool:
        calls.setdefault("wait_deadline", []).append(user_id)
        return wait_deadline_result

    async def finalize_private_buffer(user_id: str) -> None:
        calls.setdefault("finalize", []).append(user_id)

    def normalize_files(files: Any) -> list[str]:
        calls.setdefault("normalize", []).append(files)
        return list(files or [])

    def join_buffered_messages(messages: Any) -> str:
        calls.setdefault("join", []).append(list(messages))
        return "\n---\n".join(message for message in messages if message)

    def build_guardrail_input(query: str, files: Any) -> str:
        calls.setdefault("guardrail_input", []).append((query, list(files or [])))
        return f"{query}|files={len(list(files or []))}"

    def get_guardrail() -> object:
        calls.setdefault("get_guardrail", []).append(True)
        return object()

    def detect_guardrail(guardrail: Any, message: str, allow_passthrough: bool) -> dict[str, Any]:
        calls.setdefault("detect", []).append((message, allow_passthrough))
        return detect_results.pop(0) if detect_results else {"status": "safe"}

    def guardrail_status_from_result(result: dict[str, Any] | None) -> str:
        calls.setdefault("status", []).append(result)
        return str((result or {}).get("status", "safe"))

    def get_private_gate() -> FakeGate:
        return FakeGate(
            decision or SimpleNamespace(action="reply_now", effort="deep", reason="ok"),
            gate_calls,
        )

    def get_casual_reply(query: str, is_superuser: bool) -> str:
        calls.setdefault("casual", []).append((query, is_superuser))
        return casual_reply

    def private_timing_meta(value: Any | None) -> dict[str, Any] | None:
        calls.setdefault("timing_meta", []).append(value)
        if value is None:
            return None
        return {"action": value.action, "effort": value.effort, "reason": value.reason}

    services = ChatPreBridgeServices(
        private_buffer_store=store,
        private_buffer_config=lambda: SimpleNamespace(max_messages=5),
        private_buffer_follower_timeout_seconds=0.01,
        now=lambda: 123.0,
        sleep=sleep,
        wait_private_buffer_deadline=wait_private_buffer_deadline,
        finalize_private_buffer=finalize_private_buffer,
        normalize_files=normalize_files,
        join_buffered_messages=join_buffered_messages,
        build_guardrail_input=build_guardrail_input,
        get_guardrail=get_guardrail,
        detect_guardrail=detect_guardrail,
        guardrail_status_from_result=guardrail_status_from_result,
        is_guardrail_superuser=lambda user_id: superuser,
        get_private_gate=get_private_gate,
        get_casual_reply=get_casual_reply,
        private_timing_meta=private_timing_meta,
        logger=FakeLogger(),
    )
    return services, store, calls, gate_calls


def test_chat_pre_bridge_decision_module_does_not_import_parent_routes_or_runtime_side_effects():
    path = ROOT / "api/chat_pre_bridge_decision.py"
    assert path.exists()
    source = _source("api/chat_pre_bridge_decision.py")

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
    assert "_persist_chat_turn" not in source
    assert "_chat_response_payload" not in source
    assert "_clone_chat_request" not in source
    assert "build_chat_runtime_payload" not in source
    assert "ChatRuntimeInput" not in source
    assert "enriched_query" not in source
    assert "get_bridge(" not in source
    assert "bridge.handle_message" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


@pytest.mark.asyncio
async def test_group_chat_skips_private_timing_guardrail_and_buffer():
    from api.chat_pre_bridge_decision import ChatPreBridgeContinue, resolve_chat_pre_bridge_decision

    services, store, calls, gate_calls = _services()
    req = _request(session_id="group_123", files=["a.png"], query="群聊")

    result = await resolve_chat_pre_bridge_decision(
        req,
        is_group=True,
        is_superuser=False,
        services=services,
    )

    assert isinstance(result, ChatPreBridgeContinue)
    assert result.final_query == "群聊"
    assert result.final_files == ["a.png"]
    assert result.private_decision is None
    assert result.private_timing_meta is None
    assert result.guardrail_status is None
    assert result.classifier_ran is False
    assert gate_calls == []
    assert store.begin_calls == []
    assert "get_guardrail" not in calls


@pytest.mark.asyncio
async def test_private_no_reply_returns_early_outcome_without_guardrail_or_buffer():
    from api.chat_pre_bridge_decision import ChatPreBridgeEarlyReturn, resolve_chat_pre_bridge_decision

    decision = SimpleNamespace(action="no_reply", effort="none", reason="无需回复")
    services, store, calls, gate_calls = _services(decision=decision)
    req = _request(query="嗯")

    result = await resolve_chat_pre_bridge_decision(
        req,
        is_group=False,
        is_superuser=True,
        services=services,
    )

    assert isinstance(result, ChatPreBridgeEarlyReturn)
    assert result.status == "no_reply"
    assert result.reason == "无需回复"
    assert result.persist_answer == ""
    assert result.persist_guardrail_status is None
    assert result.persist_timing_meta == {"action": "no_reply", "effort": "none", "reason": "无需回复"}
    assert gate_calls == [
        {"query": "嗯", "kwargs": {"user_id": "u-pre-bridge", "has_files": False, "is_superuser": True}}
    ]
    assert store.begin_calls == []
    assert "get_guardrail" not in calls


@pytest.mark.asyncio
async def test_private_casual_returns_template_or_fallback_without_guardrail_or_buffer():
    from api.chat_pre_bridge_decision import ChatPreBridgeEarlyReturn, resolve_chat_pre_bridge_decision

    decision = SimpleNamespace(action="reply_later", effort="casual", reason="寒暄")
    services, store, calls, gate_calls = _services(decision=decision, casual_reply="")
    req = _request(query="在吗")

    result = await resolve_chat_pre_bridge_decision(
        req,
        is_group=False,
        is_superuser=False,
        services=services,
    )

    assert isinstance(result, ChatPreBridgeEarlyReturn)
    assert result.status == "ok"
    assert result.answer == "你先说事"
    assert result.source == "casual_template"
    assert result.intent == "寒暄"
    assert result.guardrail_status == "casual_template"
    assert result.persist_answer == "你先说事"
    assert result.persist_guardrail_status == "casual_template"
    assert result.persist_timing_meta == {"action": "reply_later", "effort": "casual", "reason": "寒暄"}
    assert calls["casual"] == [("在吗", False)]
    assert store.begin_calls == []
    assert "get_guardrail" not in calls


@pytest.mark.asyncio
async def test_reply_now_uses_merged_messages_and_files_for_owner_continue():
    from api.chat_pre_bridge_decision import ChatPreBridgeContinue, resolve_chat_pre_bridge_decision

    decision = SimpleNamespace(
        action="reply_now",
        effort="deep",
        runtime_preset="lightweight",
        reason="需要回复",
    )
    store = FakeStore()
    task = asyncio.create_task(asyncio.to_thread(lambda: {"status": "safe"}))
    store.snapshot_result = chat_private_buffer.PrivateBufferSnapshot(
        messages=["第一句", "第二句"],
        files=["a.png", "b.png"],
        guardrail_task=task,
    )
    services, store, calls, gate_calls = _services(
        decision=decision,
        store=store,
        detect_results=[{"status": "safe"}, {"status": "silent"}],
        superuser=True,
    )
    req = _request(query="原始", files=["a.png"], merged_messages=["第一句"])

    result = await resolve_chat_pre_bridge_decision(
        req,
        is_group=False,
        is_superuser=True,
        services=services,
    )

    assert isinstance(result, ChatPreBridgeContinue)
    assert result.final_query == "第一句\n---\n第二句"
    assert result.final_files == ["a.png", "b.png"]
    assert result.private_decision is decision
    assert result.private_decision.runtime_preset == "lightweight"
    assert result.private_timing_meta == {"action": "reply_now", "effort": "deep", "reason": "需要回复"}
    assert result.guardrail_status == "silent"
    assert result.classifier_ran is True
    assert calls["detect"] == [
        ("第一句|files=1", True),
        ("第一句\n---\n第二句|files=2", True),
    ]
    assert store.store_guardrail_calls == [("u-pre-bridge", {"status": "silent"})]


@pytest.mark.asyncio
async def test_follower_waits_and_returns_silent_without_parent_response_side_effects():
    from api.chat_pre_bridge_decision import ChatPreBridgeEarlyReturn, resolve_chat_pre_bridge_decision

    done = asyncio.Event()
    done.set()
    store = FakeStore()
    store.begin_result = chat_private_buffer.PrivateBufferFollowerJoined(done_event=done)
    services, store, calls, gate_calls = _services(store=store)
    req = _request(query="补充一句")

    result = await resolve_chat_pre_bridge_decision(
        req,
        is_group=False,
        is_superuser=False,
        services=services,
    )

    assert isinstance(result, ChatPreBridgeEarlyReturn)
    assert result.status == "silent"
    assert result.reason == "private_buffer_follower"
    assert result.persist_answer is None
    assert result.persist_guardrail_status is None
    assert store.store_guardrail_calls == []


def test_parent_routes_keep_pre_bridge_wrapper_patch_points():
    from api import routes

    assert routes._resolve_chat_pre_bridge_decision.__module__ == "api.routes"
    assert routes._chat_pre_bridge_services.__module__ == "api.routes"
    assert routes._private_buffer_config.__module__ == "api.routes"
    assert routes._wait_private_buffer_deadline.__module__ == "api.routes"
    assert routes._finalize_private_buffer.__module__ == "api.routes"


def test_parent_pre_bridge_services_uses_routes_patch_points(monkeypatch):
    from api import routes

    marker = object()
    monkeypatch.setattr(routes, "_private_buffer_store", marker)
    monkeypatch.setattr(routes, "PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS", 0.25)

    services = routes._chat_pre_bridge_services()

    assert services.private_buffer_store is marker
    assert services.private_buffer_follower_timeout_seconds == 0.25
    assert services.sleep is routes.asyncio.sleep
    assert services.now is routes._time.time
    assert services.wait_private_buffer_deadline is routes._wait_private_buffer_deadline
    assert services.finalize_private_buffer is routes._finalize_private_buffer
