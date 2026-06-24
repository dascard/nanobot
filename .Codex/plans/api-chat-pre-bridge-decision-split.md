# 普通 API Chat 私聊 Pre-Bridge 决策拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 `api.routes.proxy_chat()` 中私聊 pre-bridge 决策编排拆到 `api/chat_pre_bridge_decision.py`，父模块继续保留 DB、HTTP response、Prompt Runtime、Bridge、SSE、落库和 evolution。

**架构：** 新模块通过 `ChatPreBridgeServices` 接收父模块 patch point，只产出 `ChatPreBridgeEarlyReturn` 或 `ChatPreBridgeContinue`。父模块负责把 outcome 转换为 `_persist_chat_turn()`、`_chat_response_payload()`、`persist_req`、Prompt Runtime payload 和后续 Bridge 调用。

**技术栈：** Python 3.12、asyncio、dataclass、pytest、FastAPI route facade、源码静态扫描。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-24-api-chat-pre-bridge-decision-split-design.md`
- [x] 设计提交：`cacdcfa docs(普通API): 设计私聊前置决策拆分`
- [x] 计划写入日期：2026-06-24

## 边界与禁止事项

- 保留：`api.routes.proxy_chat.__module__ == "api.routes"`。
- 保留：`/api/v1/chat` route 继续由 `api.routes` 注册。
- 保留：`release_clean_session_transaction(db, label="chat_before_private_decision")` 在父模块。
- 保留：`_persist_chat_turn()`、`_chat_response_payload()` 和 `_clone_chat_request()` 在父模块。
- 保留：guardrail silent 的落库和 response 分支在父模块。
- 保留：persona lookup、`PersonaInjectionService`、`safe_user_input`、`enriched_query`、`bridge_meta` 和 Prompt Runtime payload 在父模块。
- 保留：`get_bridge()`、`_do_chat()`、`_stream_chat()`、SSE、非流式结果收尾和 evolution 在父模块。
- 保留：`api.routes.get_guardrail`、`api.routes._private_buffer_store`、`api.routes.asyncio.sleep`、`api.routes._time.time`、`api.routes._wait_private_buffer_deadline()` 和 `api.routes._finalize_private_buffer()` 作为测试 patch point。
- 禁止：新模块导入 `api.routes`。
- 禁止：新模块导入 FastAPI、`StreamingResponse`、`BackgroundTasks`、DB model、`SessionLocal` 或 `UnitOfWork`。
- 禁止：新模块调用 `get_bridge()`、Bridge handle、`build_chat_runtime_payload()` 或 Prompt Runtime 模板。
- 禁止：新模块调用 `_persist_chat_turn()`、`_chat_response_payload()`、`_clone_chat_request()` 或 `db.commit()`。
- 禁止：改 conversation 结构、历史注入、工具输出契约、message envelope、push envelope 或 response envelope。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：处理 WebUI / JS。

## 文件职责

- 创建：`api/chat_pre_bridge_decision.py`
  - 定义 `ChatPreBridgeServices`。
  - 定义 `ChatPreBridgeEarlyReturn` 和 `ChatPreBridgeContinue`。
  - 实现 `resolve_chat_pre_bridge_decision()`。
  - 只通过 services 访问 private timing、guardrail、private buffer、时间源和 logger。
- 修改：`api/routes.py`
  - 导入 `api.chat_pre_bridge_decision`。
  - 提供 `get_private_gate()`、`_get_casual_reply_for_pre_bridge()`、`_detect_guardrail_for_pre_bridge()`、`_chat_pre_bridge_services()` 和 `_resolve_chat_pre_bridge_decision()` 薄 wrapper。
  - 用 outcome 替换 `proxy_chat()` 中 private timing、guardrail 和 private buffer 内联编排。
  - 保留父模块的早返回落库、response、silent guardrail、Prompt Runtime 和 Bridge 后续流程。
- 创建：`tests/test_api_chat_pre_bridge_decision_split.py`
  - 锁定新模块源码边界。
  - 单测 group skip、private timing no_reply / casual / reply_now、private buffer follower、owner snapshot 合并、guardrail status 和父模块 wrapper。
- 修改：
  - `tests/test_api_group_message_routes_split.py`
  - `tests/test_api_agent_step_routes_split.py`
  - `tests/test_api_history_log_routes_split.py`
  - `tests/test_api_sticker_media_routes_split.py`
  - 将 `api/chat_pre_bridge_decision.py` 加入 chat split module 扫描清单。
- 修改：`.Codex/plans/api-chat-pre-bridge-decision-split.md`
  - 随执行更新任务状态、命令输出和提交号。
- 修改：`docs/todo.md`
  - 最终收口时记录 P3 `api/routes.py` 私聊 pre-bridge 决策拆分进展和行数。
- 修改：`docs/plan_walkthrough.md`
  - 最终收口时追加 2026-06-24 本阶段提交列表、验证结果和下一步建议。

---

## 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_pre_bridge_decision_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`
- 修改：`.Codex/plans/api-chat-pre-bridge-decision-split.md`

- [x] **步骤 1：创建测试文件基础结构**

创建 `tests/test_api_chat_pre_bridge_decision_split.py`，写入：

```python
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
```

- [x] **步骤 2：新增 fake service helper**

在同一文件追加：

```python
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

    async def begin_or_append(self, user_id: str, **kwargs: Any) -> Any:
        self.begin_calls.append({"user_id": user_id, "kwargs": kwargs})
        if self.begin_result is None:
            return chat_private_buffer.PrivateBufferOwnerStarted(buffer={})
        return self.begin_result

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
        return FakeGate(decision or SimpleNamespace(action="reply_now", effort="deep", reason="ok"), gate_calls)

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
```

- [x] **步骤 3：新增源码边界红灯**

在同一文件追加：

```python
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
```

- [x] **步骤 4：新增 group skip 红灯**

在同一文件追加：

```python
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
```

- [x] **步骤 5：新增 private timing 早返回红灯**

在同一文件追加：

```python
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
```

- [x] **步骤 6：新增 private buffer 和 guardrail 红灯**

在同一文件追加：

```python
@pytest.mark.asyncio
async def test_reply_now_uses_merged_messages_and_files_for_owner_continue():
    from api.chat_pre_bridge_decision import ChatPreBridgeContinue, resolve_chat_pre_bridge_decision

    decision = SimpleNamespace(action="reply_now", effort="deep", reason="需要回复")
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
```

- [x] **步骤 7：新增父模块 wrapper 红灯**

在同一文件追加：

```python
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
```

- [x] **步骤 8：更新 chat split module 扫描清单**

在以下 4 个文件的 `test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable()` 路径列表中加入 `"api/chat_pre_bridge_decision.py"`：

```python
        "api/chat_pre_bridge_decision.py",
```

修改文件：

- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 9：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
tests/test_api_chat_pre_bridge_decision_split.py \
tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
-v
```

预期：失败，核心原因是 `api/chat_pre_bridge_decision.py` 尚不存在，以及 `api.routes._resolve_chat_pre_bridge_decision` / `_chat_pre_bridge_services` 尚不存在。

实际：12 failed, 1 warning in 6.94s。失败原因符合预期，包括 `api/chat_pre_bridge_decision.py` 不存在、`api.routes._resolve_chat_pre_bridge_decision` 不存在、`api.routes._chat_pre_bridge_services` 不存在，以及 4 个 chat split module 扫描清单读取新模块失败。

- [x] **步骤 10：Commit 红灯测试**

```bash
git add tests/test_api_chat_pre_bridge_decision_split.py \
tests/test_api_group_message_routes_split.py \
tests/test_api_agent_step_routes_split.py \
tests/test_api_history_log_routes_split.py \
tests/test_api_sticker_media_routes_split.py \
.Codex/plans/api-chat-pre-bridge-decision-split.md
git commit -m "test(普通API): 锁定私聊前置决策契约"
```

实际提交：本阶段测试提交自身，message 为 `test(普通API): 锁定私聊前置决策契约`。

---

## 任务 2：实现新模块 helper

**文件：**
- 创建：`api/chat_pre_bridge_decision.py`
- 修改：`.Codex/plans/api-chat-pre-bridge-decision-split.md`

- [x] **步骤 1：创建新模块数据结构**

创建 `api/chat_pre_bridge_decision.py`，写入：

```python
"""Chat 私聊 pre-bridge 决策编排。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from api import chat_private_buffer


@dataclass(frozen=True)
class ChatPreBridgeServices:
    private_buffer_store: Any
    private_buffer_config: Callable[[], Any]
    private_buffer_follower_timeout_seconds: float
    now: Callable[[], float]
    sleep: Callable[[float], Awaitable[None]]
    wait_private_buffer_deadline: Callable[[str], Awaitable[bool]]
    finalize_private_buffer: Callable[[str], Awaitable[None]]
    normalize_files: Callable[[Any], list[str]]
    join_buffered_messages: Callable[[Sequence[str]], str]
    build_guardrail_input: Callable[[str, Any], str]
    get_guardrail: Callable[[], Any]
    detect_guardrail: Callable[[Any, str, bool], dict[str, Any]]
    guardrail_status_from_result: Callable[[dict[str, Any] | None], str]
    is_guardrail_superuser: Callable[[str], bool]
    get_private_gate: Callable[[], Any]
    get_casual_reply: Callable[[str, bool], str]
    private_timing_meta: Callable[[Any | None], dict[str, Any] | None]
    logger: Any


@dataclass(frozen=True)
class ChatPreBridgeEarlyReturn:
    status: str
    reason: str = ""
    answer: str = ""
    source: str = ""
    intent: str = ""
    guardrail_status: str | None = None
    persist_answer: str | None = None
    persist_guardrail_status: str | None = None
    persist_timing_meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChatPreBridgeContinue:
    final_query: str
    final_files: list[str]
    private_decision: Any | None
    private_timing_meta: dict[str, Any] | None
    guardrail_status: str | None
    classifier_ran: bool
```

- [x] **步骤 2：实现 private timing helper**

在同一文件追加：

```python
async def _classify_private_timing(
    req: Any,
    *,
    is_superuser: bool,
    services: ChatPreBridgeServices,
) -> tuple[Any | None, dict[str, Any] | None, ChatPreBridgeEarlyReturn | None, str | None, list[str] | None]:
    private_decision = None
    private_timing_meta = None
    buffered_query = None
    buffered_files = None

    try:
        private_gate = services.get_private_gate()
        try:
            private_decision = await private_gate.classify(
                req.query,
                user_id=req.user_id,
                has_files=bool(req.files),
                is_superuser=is_superuser,
            )
        except TypeError as exc:
            if "is_superuser" not in str(exc):
                raise
            private_decision = await private_gate.classify(
                req.query,
                user_id=req.user_id,
                has_files=bool(req.files),
            )
        private_timing_meta = services.private_timing_meta(private_decision)
        if private_decision.action == "no_reply":
            return (
                private_decision,
                private_timing_meta,
                ChatPreBridgeEarlyReturn(
                    status="no_reply",
                    reason=private_decision.reason,
                    persist_answer="",
                    persist_guardrail_status=None,
                    persist_timing_meta=private_timing_meta,
                ),
                None,
                None,
            )
        if private_decision.effort == "casual":
            reply = services.get_casual_reply(req.query, is_superuser)
            answer = reply if reply else ("你先说事" if req.query else "")
            return (
                private_decision,
                private_timing_meta,
                ChatPreBridgeEarlyReturn(
                    status="ok",
                    answer=answer,
                    source="casual_template",
                    intent=private_decision.reason,
                    guardrail_status="casual_template",
                    persist_answer=answer,
                    persist_guardrail_status="casual_template",
                    persist_timing_meta=private_timing_meta,
                ),
                None,
                None,
            )
        if private_decision.action == "reply_now":
            messages = req.merged_messages or [req.query]
            buffered_query = services.join_buffered_messages(messages)
            buffered_files = services.normalize_files(req.files)
    except Exception as exc:
        services.logger.warning("[/chat] PrivateGate classify failed user=%s: %s", req.user_id, exc)

    return private_decision, private_timing_meta, None, buffered_query, buffered_files
```

- [x] **步骤 3：实现 guardrail 与 private buffer helper**

在同一文件追加：

```python
async def _run_guardrail_buffer(
    req: Any,
    *,
    services: ChatPreBridgeServices,
) -> tuple[str | None, list[str] | None, str | None, ChatPreBridgeEarlyReturn | None]:
    guardrail = services.get_guardrail()
    messages = req.merged_messages or [req.query]
    merged = services.join_buffered_messages(messages)
    guardrail_input = services.build_guardrail_input(merged, req.files)

    def guardrail_task_factory() -> asyncio.Task[Any]:
        return asyncio.create_task(
            asyncio.to_thread(
                services.detect_guardrail,
                guardrail,
                guardrail_input,
                services.is_guardrail_superuser(req.user_id),
            )
        )

    buffer_result = await services.private_buffer_store.begin_or_append(
        req.user_id,
        merged_query=merged,
        files=services.normalize_files(req.files),
        guardrail_task_factory=guardrail_task_factory,
        now=services.now(),
        config=services.private_buffer_config(),
    )

    if isinstance(buffer_result, chat_private_buffer.PrivateBufferFollowerJoined):
        try:
            await asyncio.wait_for(
                buffer_result.done_event.wait(),
                timeout=services.private_buffer_follower_timeout_seconds,
            )
        except asyncio.TimeoutError:
            services.logger.warning(
                "[/chat] Private buffer follower timed out: user=%s",
                req.user_id,
            )
            await services.finalize_private_buffer(req.user_id)
        return (
            None,
            None,
            None,
            ChatPreBridgeEarlyReturn(
                status="silent",
                reason="private_buffer_follower",
            ),
        )

    if not await services.wait_private_buffer_deadline(req.user_id):
        return (
            None,
            None,
            None,
            ChatPreBridgeEarlyReturn(
                status="silent",
                reason="private_buffer_missing",
            ),
        )

    snapshot = await services.private_buffer_store.snapshot(req.user_id)
    if snapshot is None:
        return (
            None,
            None,
            None,
            ChatPreBridgeEarlyReturn(
                status="silent",
                reason="private_buffer_missing",
            ),
        )

    buffered_messages = snapshot.messages
    buffered_files = snapshot.files
    buffered_query = services.join_buffered_messages(buffered_messages)
    buffered_guardrail_input = services.build_guardrail_input(buffered_query, buffered_files)
    if len(buffered_messages) > 1:
        result = await asyncio.to_thread(
            services.detect_guardrail,
            guardrail,
            buffered_guardrail_input,
            services.is_guardrail_superuser(req.user_id),
        )
    else:
        result = await snapshot.guardrail_task

    await services.private_buffer_store.store_guardrail_result(req.user_id, result)
    guardrail_status = services.guardrail_status_from_result(result)
    services.logger.info(
        "[/chat] Guardrail result: injection=%s, passthrough=%s, user=%s",
        result.get("injection", False),
        result.get("passthrough", False),
        req.user_id,
    )
    return buffered_query, buffered_files, guardrail_status, None
```

- [x] **步骤 4：实现公开入口**

在同一文件追加：

```python
async def resolve_chat_pre_bridge_decision(
    req: Any,
    *,
    is_group: bool,
    is_superuser: bool,
    services: ChatPreBridgeServices,
) -> ChatPreBridgeEarlyReturn | ChatPreBridgeContinue:
    if is_group and not req.classification_request:
        return ChatPreBridgeContinue(
            final_query=req.query,
            final_files=services.normalize_files(req.files),
            private_decision=None,
            private_timing_meta=None,
            guardrail_status=None,
            classifier_ran=False,
        )

    guardrail_status: str | None = None
    classifier_ran = False
    buffered_query: str | None = None
    buffered_files: list[str] | None = None
    private_decision = None
    private_timing_meta: dict[str, Any] | None = None

    if not is_group and not req.classification_request:
        (
            private_decision,
            private_timing_meta,
            early_return,
            buffered_query,
            buffered_files,
        ) = await _classify_private_timing(
            req,
            is_superuser=is_superuser,
            services=services,
        )
        if early_return is not None:
            return early_return

    if not is_group or req.classification_request:
        try:
            classifier_ran = True
            (
                guardrail_buffered_query,
                guardrail_buffered_files,
                guardrail_status,
                early_return,
            ) = await _run_guardrail_buffer(req, services=services)
            if early_return is not None:
                return early_return
            buffered_query = guardrail_buffered_query or buffered_query
            buffered_files = guardrail_buffered_files if guardrail_buffered_files is not None else buffered_files
        except asyncio.CancelledError:
            await services.finalize_private_buffer(req.user_id)
            raise
        except Exception:
            await services.finalize_private_buffer(req.user_id)
            raise

    final_query = buffered_query or req.query
    final_files = buffered_files if buffered_files is not None else services.normalize_files(req.files)
    return ChatPreBridgeContinue(
        final_query=final_query,
        final_files=final_files,
        private_decision=private_decision,
        private_timing_meta=private_timing_meta,
        guardrail_status=guardrail_status,
        classifier_ran=classifier_ran,
    )
```

- [x] **步骤 5：运行新模块定向测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/test_api_chat_pre_bridge_decision_split.py -v
```

预期：新模块行为测试通过，父模块 wrapper 相关测试仍失败。

实际：6 passed, 2 failed, 1 warning in 6.57s。通过项覆盖新模块源码边界、group skip、private timing 早返回、owner 多消息重检和 follower silent；失败项仅为 `api.routes._resolve_chat_pre_bridge_decision` 与 `api.routes._chat_pre_bridge_services` 尚未接入。

- [x] **步骤 6：Commit 新模块 helper**

```bash
git add api/chat_pre_bridge_decision.py .Codex/plans/api-chat-pre-bridge-decision-split.md
git commit -m "refactor(普通API): 增加私聊前置决策助手"
```

实际提交：本阶段 helper 提交自身，message 为 `refactor(普通API): 增加私聊前置决策助手`。提交前 `compileall`、`git diff --check` 和新模块源码边界扫描均无输出。

---

## 任务 3：父模块接入

**文件：**
- 修改：`api/routes.py`
- 修改：`.Codex/plans/api-chat-pre-bridge-decision-split.md`

- [ ] **步骤 1：新增导入和 wrapper**

在 `api/routes.py` 的 chat helper 导入区域加入：

```python
from api import chat_pre_bridge_decision
```

在 private buffer / guardrail helper 附近加入：

```python
def get_private_gate() -> Any:
    from core.private_timing import get_private_gate as _core_get_private_gate

    return _core_get_private_gate()


def _get_casual_reply_for_pre_bridge(query: str, is_superuser: bool) -> str:
    from core.reply_templates import get_casual_reply as _core_get_casual_reply

    return _core_get_casual_reply(query, is_superuser=is_superuser)


def _detect_guardrail_for_pre_bridge(
    guardrail: Any,
    message: str,
    allow_passthrough: bool,
) -> dict[str, Any]:
    return _detect_guardrail(
        guardrail,
        message,
        allow_passthrough=allow_passthrough,
    )


def _chat_pre_bridge_services() -> chat_pre_bridge_decision.ChatPreBridgeServices:
    return chat_pre_bridge_decision.ChatPreBridgeServices(
        private_buffer_store=_private_buffer_store,
        private_buffer_config=_private_buffer_config,
        private_buffer_follower_timeout_seconds=PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS,
        now=_time.time,
        sleep=asyncio.sleep,
        wait_private_buffer_deadline=_wait_private_buffer_deadline,
        finalize_private_buffer=_finalize_private_buffer,
        normalize_files=_normalize_files,
        join_buffered_messages=_join_buffered_messages,
        build_guardrail_input=_build_guardrail_input,
        get_guardrail=get_guardrail,
        detect_guardrail=_detect_guardrail_for_pre_bridge,
        guardrail_status_from_result=chat_guardrail_facade.guardrail_status_from_result,
        is_guardrail_superuser=_is_guardrail_superuser,
        get_private_gate=get_private_gate,
        get_casual_reply=_get_casual_reply_for_pre_bridge,
        private_timing_meta=_private_timing_meta,
        logger=logger,
    )


async def _resolve_chat_pre_bridge_decision(
    req: ChatRequest,
    *,
    is_group: bool,
    is_superuser: bool,
) -> chat_pre_bridge_decision.ChatPreBridgeEarlyReturn | chat_pre_bridge_decision.ChatPreBridgeContinue:
    return await chat_pre_bridge_decision.resolve_chat_pre_bridge_decision(
        req,
        is_group=is_group,
        is_superuser=is_superuser,
        services=_chat_pre_bridge_services(),
    )
```

- [ ] **步骤 2：替换 `proxy_chat()` 内联 pre-bridge 区块**

删除 `proxy_chat()` 中从 `# 4a. 私聊三态分类：先分类再路由` 到 `if _classifier_ran and guardrail_status == "silent":` 之前的 private timing / guardrail / buffer 内联逻辑，替换为：

```python
    pre_bridge = await _resolve_chat_pre_bridge_decision(
        req,
        is_group=is_group,
        is_superuser=is_superuser,
    )

    if isinstance(pre_bridge, chat_pre_bridge_decision.ChatPreBridgeEarlyReturn):
        if pre_bridge.persist_answer is not None:
            _persist_chat_turn(
                db,
                req,
                pre_bridge.persist_answer,
                guardrail_status=pre_bridge.persist_guardrail_status,
                timing_meta=pre_bridge.persist_timing_meta,
            )
        return _chat_response_payload(
            req,
            status=pre_bridge.status,
            reason=pre_bridge.reason,
            answer=pre_bridge.answer,
            source=pre_bridge.source,
            intent=pre_bridge.intent,
            guardrail_status=pre_bridge.guardrail_status,
            include_answer_chunks=True,
        )

    final_query = pre_bridge.final_query
    final_files = pre_bridge.final_files
    _private_decision = pre_bridge.private_decision
    private_timing_meta = pre_bridge.private_timing_meta
    guardrail_status = pre_bridge.guardrail_status
    _classifier_ran = pre_bridge.classifier_ran
    persist_req = _clone_chat_request(req, query=final_query, files=final_files)
```

保留紧随其后的 guardrail silent 分支：

```python
    if _classifier_ran and guardrail_status == "silent":
        await _finalize_private_buffer(req.user_id)
        _persist_chat_turn(
            db,
            persist_req,
            "（数据中转，自动静默）",
            guardrail_status,
            timing_meta=private_timing_meta,
        )
        return _chat_response_payload(
            req,
            status="silent",
            reason="guardrail_silent",
            guardrail_status=guardrail_status,
            include_answer_chunks=True,
        )
```

- [ ] **步骤 3：移除父模块局部 import**

确认 `proxy_chat()` 中不再有以下局部 import：

```python
from core.private_timing import get_private_gate, PrivateDecision, get_effort_constraint
from core.reply_templates import get_casual_reply
```

如果 `PrivateDecision` 和 `get_effort_constraint` 只服务旧内联区块，应同步删除未使用引用。

- [ ] **步骤 4：运行父模块接入定向测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/test_api_chat_pre_bridge_decision_split.py -v
```

预期：全部通过。

- [ ] **步骤 5：Commit 父模块接入**

```bash
git add api/routes.py .Codex/plans/api-chat-pre-bridge-decision-split.md
git commit -m "refactor(普通API): 接入私聊前置决策助手"
```

---

## 任务 4：相邻回归和静态检查

**文件：**
- 修改：`.Codex/plans/api-chat-pre-bridge-decision-split.md`

- [ ] **步骤 1：运行 chat split module 扫描测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
-v
```

预期：4 个测试通过。

- [ ] **步骤 2：运行 private buffer / guardrail 相邻回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
tests/test_api_chat_private_buffer_split.py \
tests/test_api_chat_guardrail_facade_split.py \
tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta \
tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta \
tests/test_api.py::test_private_buffer_silent_releases_waiters \
tests/test_api.py::test_private_buffer_refreshes_window_and_persists_merged_messages \
tests/test_api.py::test_private_buffer_merges_files_for_final_bridge_request \
tests/test_api.py::test_private_buffer_text_after_files_shrinks_window_to_five_seconds \
tests/test_api.py::test_private_buffer_owner_cancel_releases_waiters_and_cleans_buffer \
tests/test_api.py::test_private_buffer_bridge_cancel_releases_waiters_and_cleans_buffer \
tests/test_asyncio_run_policy.py \
-v
```

预期：全部通过。

- [ ] **步骤 3：运行静态检查**

运行：

```bash
python -m compileall api/routes.py api/chat_pre_bridge_decision.py -q
git diff --check -- api/routes.py api/chat_pre_bridge_decision.py \
tests/test_api_chat_pre_bridge_decision_split.py \
tests/test_api_group_message_routes_split.py \
tests/test_api_agent_step_routes_split.py \
tests/test_api_history_log_routes_split.py \
tests/test_api_sticker_media_routes_split.py \
.Codex/plans/api-chat-pre-bridge-decision-split.md
rg -n "asyncio\\.run|run_awaitable_sync" api core clients nanobot_kt tests --glob "*.py"
```

预期：`compileall` 和 `git diff --check` 无输出；`rg` 只允许命中测试策略文件或 main guard 中既有允许项，不能在新模块和非 main 业务代码中出现新增同步 awaitable。

- [ ] **步骤 4：统计行数**

运行：

```bash
wc -l api/routes.py api/chat_pre_bridge_decision.py tests/test_api_chat_pre_bridge_decision_split.py
```

预期：`api/routes.py` 行数较 1098 明显下降。

---

## 任务 5：文档收口和全量验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/api-chat-pre-bridge-decision-split.md`

- [ ] **步骤 1：更新 `docs/todo.md`**

在 P3 超大文件拆分记录中追加 2026-06-24 私聊 pre-bridge 决策拆分结果：

```markdown
- 2026-06-24：完成 `api/routes.py` 私聊 pre-bridge 决策拆分，新增 `api/chat_pre_bridge_decision.py`，父模块保留 DB、response、Prompt Runtime、Bridge 和 SSE 边界。
```

- [ ] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加本阶段收口记录，包含：

```markdown
### 2026-06-24：普通 API Chat 私聊 Pre-Bridge 决策拆分

- 设计提交：`cacdcfa docs(普通API): 设计私聊前置决策拆分`
- 计划提交：记录实际提交号。
- 测试提交：记录实际提交号。
- Helper 提交：记录实际提交号。
- 接入提交：记录实际提交号。
- 验证：记录红灯、定向、相邻回归、静态检查和全量测试输出摘要。
- 行数：记录 `api/routes.py` 拆分前 1098 行和拆分后的实际行数。
- 下一步：继续寻找 `api/routes.py` 中剩余可拆连续区块，避免触碰 Prompt Runtime 模板契约。
```

- [ ] **步骤 3：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [ ] **步骤 4：运行最终文档检查**

运行：

```bash
rg -n "TO""DO|待""定|后续""实现|补充""细节|\\x3c[^>]+\\x3e|\\.\\.\\." \
.Codex/plans/api-chat-pre-bridge-decision-split.md \
docs/superpowers/specs/2026-06-24-api-chat-pre-bridge-decision-split-design.md || true
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-chat-pre-bridge-decision-split.md
```

预期：计划和设计文档没有占位红旗；`git diff --check` 无输出。

- [ ] **步骤 5：Commit 文档收口**

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-chat-pre-bridge-decision-split.md
git commit -m "docs(计划): 收口私聊前置决策拆分"
```

---

## 最终完成标准

- `api/chat_pre_bridge_decision.py` 不导入父模块、FastAPI、DB、Bridge 或 Prompt Runtime。
- `api/routes.py` 只保留 service factory、outcome 转换、silent guardrail、Prompt Runtime、Bridge、SSE 和 response 边界。
- private timing no_reply / casual / reply_now 行为不变。
- private buffer owner / follower、deadline、snapshot、文件合并、guardrail status 和取消清理行为不变。
- `tests/test_api_chat_pre_bridge_decision_split.py`、四个 chat split module 扫描测试、相邻回归和全量测试通过。
- `api/routes.py` 行数从 1098 继续下降。
- 每个阶段性改动均已按文件精确暂存并 commit。
