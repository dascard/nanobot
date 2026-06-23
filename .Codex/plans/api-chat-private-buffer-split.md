# 聊天私聊缓冲基础件拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api.routes` 中私聊缓冲的纯 helper、裸 dict 状态转移和 finalize 基础件拆到 `api/chat_private_buffer.py`，保留 `/chat` 路由本体和父模块 patch point。

**架构：** 新模块承载 `PrivateBufferConfig`、`PrivateBufferStore` 和私聊缓冲纯 helper，不导入 `api.routes`，不获取 guardrail / bridge，不落库，不处理 SSE 或 push。`api.routes` 继续持有 `_private_buffers`、常量、fake clock / fake sleep patch point、guardrail task 创建、deadline sleep、HTTP response、Bridge、落库和 streaming finalizer。

**技术栈：** Python 3.12、FastAPI、pytest、asyncio Event / Lock。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-23-api-chat-private-buffer-split-design.md`
- [x] 设计提交：`1ae8aa7 docs(普通API): 设计私聊缓冲拆分`

## 边界与禁止事项

- 保留：`api.routes.proxy_chat.__module__ == "api.routes"`。
- 保留：`api.routes._private_buffers` 作为实际 runtime 使用的同一个 dict。
- 保留：`api.routes._private_lock` 作为实际 runtime 使用的同一个 lock。
- 保留：`api.routes.PRIVATE_BUFFER_WINDOW_SECONDS`、`PRIVATE_BUFFER_WINDOW_WITH_FILES_SECONDS`、`PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS` 和 `MAX_BUFFERED_MESSAGES` monkeypatch 生效。
- 保留：owner deadline loop 继续在父模块使用 `api.routes._time.time()` 和 `api.routes.asyncio.sleep()`。
- 保留：`get_guardrail()`、`_detect_guardrail()`、`get_bridge()`、`_persist_chat_turn()`、`_chat_response_payload()`、`_stream_chat()`、`StreamingResponse`、push envelope 和 response envelope。
- 禁止：新模块导入 `api.routes`。
- 禁止：新模块调用 `get_bridge()`、`get_guardrail()`、`_detect_guardrail()`、`_persist_chat_turn()` 或 push 函数。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：本阶段引入 generation id 或改变 `_finalize_private_buffer(user_id)` 的 user-level 语义。

## 文件职责

- 创建：`tests/test_api_chat_private_buffer_split.py`
  - 锁定新模块 import hygiene。
  - 锁定 helper、store、finalize 和父模块 wrapper 契约。
- 修改：4 个普通 API split 扫描测试：
  - `tests/test_api_history_log_routes_split.py`
  - `tests/test_api_agent_step_routes_split.py`
  - `tests/test_api_group_message_routes_split.py`
  - `tests/test_api_sticker_media_routes_split.py`
- 创建：`api/chat_private_buffer.py`
  - 提供 `PrivateBufferConfig`。
  - 提供 `PrivateBufferOwnerStarted`、`PrivateBufferFollowerJoined`、`PrivateBufferSnapshot`。
  - 提供 `join_buffered_messages()`、`merge_buffered_files()`、`private_buffer_window_seconds()`。
  - 提供 `PrivateBufferStore`。
- 修改：`api/routes.py`
  - 导入 `chat_private_buffer`。
  - 创建 `_private_buffer_store`。
  - 保留父模块 wrapper 并委托新模块。
  - 在 `proxy_chat()` 私聊缓冲分支使用 store 方法。
- 修改：`.Codex/plans/api-chat-private-buffer-split.md`
  - 随执行记录验证结果。
- 修改：`docs/todo.md`
  - 记录 P3 中 `api/routes.py` private buffer 小刀进展和行数。
- 修改：`docs/plan_walkthrough.md`
  - 追加 2026-06-23 private buffer 执行记录、提交列表和验证证据。

---

### 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_private_buffer_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 1：编写 helper / store 契约测试**

在 `tests/test_api_chat_private_buffer_split.py` 写入：

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_private_buffer_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_private_buffer.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source
    assert "_detect_guardrail(" not in source
    assert "_persist_chat_turn(" not in source


def test_private_buffer_helpers_preserve_message_file_and_window_contracts():
    from api.chat_private_buffer import (
        PrivateBufferConfig,
        join_buffered_messages,
        merge_buffered_files,
        private_buffer_window_seconds,
    )

    config = PrivateBufferConfig(
        max_messages=10,
        window_seconds=5.0,
        window_with_files_seconds=10.0,
        follower_timeout_seconds=900.0,
    )

    assert join_buffered_messages(["第一句", "", "第二句"]) == "第一句\n---\n第二句"
    assert merge_buffered_files(["a.png", "b.png"], ["", "b.png", "c.png"]) == [
        "a.png",
        "b.png",
        "c.png",
    ]
    assert private_buffer_window_seconds([], config) == 5.0
    assert private_buffer_window_seconds(["a.png"], config) == 10.0


def test_parent_private_buffer_wrappers_remain_in_routes_and_patchable(monkeypatch):
    from api import routes

    assert routes._join_buffered_messages.__module__ == "api.routes"
    assert routes._merge_buffered_files.__module__ == "api.routes"
    assert routes._private_buffer_window_seconds.__module__ == "api.routes"
    assert routes._finalize_private_buffer.__module__ == "api.routes"

    monkeypatch.setattr(routes, "PRIVATE_BUFFER_WINDOW_SECONDS", 0.25)
    monkeypatch.setattr(routes, "PRIVATE_BUFFER_WINDOW_WITH_FILES_SECONDS", 0.75)

    assert routes._private_buffer_window_seconds(None) == 0.25
    assert routes._private_buffer_window_seconds(["https://example.com/a.png"]) == 0.75


@pytest.mark.asyncio
async def test_private_buffer_store_creates_appends_snapshots_and_finalizes():
    from api.chat_private_buffer import (
        PrivateBufferConfig,
        PrivateBufferFollowerJoined,
        PrivateBufferOwnerStarted,
        PrivateBufferStore,
    )

    buffers: dict[str, dict] = {}
    store = PrivateBufferStore(buffers, asyncio.Lock())
    config = PrivateBufferConfig(
        max_messages=3,
        window_seconds=5.0,
        window_with_files_seconds=10.0,
        follower_timeout_seconds=900.0,
    )
    created_tasks: list[asyncio.Task[dict[str, str]]] = []

    def task_factory() -> asyncio.Task[dict[str, str]]:
        task = asyncio.create_task(asyncio.sleep(0, result={"status": "reply"}))
        created_tasks.append(task)
        return task

    owner = await store.begin_or_append(
        "u1",
        merged_query="第一句",
        files=[],
        guardrail_task_factory=task_factory,
        now=1.0,
        config=config,
    )
    assert isinstance(owner, PrivateBufferOwnerStarted)
    assert buffers["u1"]["queries"] == ["第一句"]
    assert buffers["u1"]["files"] == []
    assert buffers["u1"]["deadline"] == 6.0
    assert buffers["u1"]["window_seconds"] == 5.0
    assert len(created_tasks) == 1

    follower = await store.begin_or_append(
        "u1",
        merged_query="第二句",
        files=["a.png"],
        guardrail_task_factory=task_factory,
        now=3.0,
        config=config,
    )
    assert isinstance(follower, PrivateBufferFollowerJoined)
    assert follower.done_event is buffers["u1"]["done"]
    assert buffers["u1"]["queries"] == ["第一句", "第二句"]
    assert buffers["u1"]["files"] == ["a.png"]
    assert buffers["u1"]["deadline"] == 13.0
    assert buffers["u1"]["window_seconds"] == 10.0
    assert len(created_tasks) == 1

    snapshot = await store.snapshot("u1")
    assert snapshot is not None
    assert snapshot.messages == ["第一句", "第二句"]
    assert snapshot.files == ["a.png"]
    assert snapshot.guardrail_task is created_tasks[0]
    snapshot.messages.append("不会写回")
    assert buffers["u1"]["queries"] == ["第一句", "第二句"]

    assert await store.deadline("u1") == 13.0
    await store.store_guardrail_result("u1", {"status": "reply"})
    assert buffers["u1"]["result"] == {"status": "reply"}

    await store.finalize("u1", "答案", clear_window=False)
    assert buffers["u1"]["answer"] == "答案"
    assert buffers["u1"]["done"].is_set()

    await store.finalize("u1")
    assert "u1" not in buffers
    await asyncio.gather(*created_tasks)


@pytest.mark.asyncio
async def test_private_buffer_store_overflow_coalesces_latest_message():
    from api.chat_private_buffer import PrivateBufferConfig, PrivateBufferStore

    buffers: dict[str, dict] = {}
    store = PrivateBufferStore(buffers, asyncio.Lock())
    config = PrivateBufferConfig(
        max_messages=2,
        window_seconds=5.0,
        window_with_files_seconds=10.0,
        follower_timeout_seconds=900.0,
    )

    def task_factory() -> asyncio.Task[dict[str, str]]:
        return asyncio.create_task(asyncio.sleep(0, result={"status": "reply"}))

    await store.begin_or_append(
        "u-overflow",
        merged_query="第一句",
        files=[],
        guardrail_task_factory=task_factory,
        now=0.0,
        config=config,
    )
    await store.begin_or_append(
        "u-overflow",
        merged_query="第二句",
        files=[],
        guardrail_task_factory=task_factory,
        now=1.0,
        config=config,
    )
    await store.begin_or_append(
        "u-overflow",
        merged_query="第三句",
        files=[],
        guardrail_task_factory=task_factory,
        now=2.0,
        config=config,
    )

    assert buffers["u-overflow"]["queries"] == ["第一句", "第二句\n---\n第三句"]
    await store.finalize("u-overflow")
```

- [x] **步骤 2：将新模块加入普通 API split 扫描清单**

在以下 4 个文件的 chat split module tuple 中追加：

```python
"api/chat_private_buffer.py",
```

文件：

- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 3：运行 helper 红灯测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_private_buffer_split.py -v
```

预期：失败，原因是 `api/chat_private_buffer.py` 尚不存在。

- [x] **步骤 4：运行扫描红灯测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：4 个测试失败，原因是扫描清单中的 `api/chat_private_buffer.py` 尚不存在。

- [x] **步骤 5：提交红灯测试**

运行：

```bash
git add tests/test_api_chat_private_buffer_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py \
  .Codex/plans/api-chat-private-buffer-split.md
git diff --cached --check
git commit -m "test(普通API): 锁定私聊缓冲基础件契约"
```

---

### 任务 2：新增 private buffer 基础件模块

**文件：**
- 创建：`api/chat_private_buffer.py`

- [x] **步骤 1：编写新模块实现**

在 `api/chat_private_buffer.py` 写入：

```python
"""私聊缓冲状态辅助模块。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PrivateBufferConfig:
    max_messages: int
    window_seconds: float
    window_with_files_seconds: float
    follower_timeout_seconds: float


@dataclass(frozen=True)
class PrivateBufferOwnerStarted:
    buffer: dict[str, Any]


@dataclass(frozen=True)
class PrivateBufferFollowerJoined:
    done_event: asyncio.Event


@dataclass(frozen=True)
class PrivateBufferSnapshot:
    messages: list[str]
    files: list[str]
    guardrail_task: asyncio.Task[Any]


def join_buffered_messages(messages: Sequence[str]) -> str:
    return "\n---\n".join(message for message in messages if message)


def merge_buffered_files(existing: Sequence[str], incoming: Sequence[str] | None) -> list[str]:
    merged = list(existing)
    for file in incoming or []:
        if file and file not in merged:
            merged.append(file)
    return merged


def private_buffer_window_seconds(files: Sequence[str] | None, config: PrivateBufferConfig) -> float:
    return config.window_with_files_seconds if list(files or []) else config.window_seconds


class PrivateBufferStore:
    def __init__(self, buffers: dict[str, dict[str, Any]], lock: asyncio.Lock) -> None:
        self.buffers = buffers
        self._lock = lock

    async def begin_or_append(
        self,
        user_id: str,
        *,
        merged_query: str,
        files: Sequence[str],
        guardrail_task_factory: Callable[[], asyncio.Task[Any]],
        now: float,
        config: PrivateBufferConfig,
    ) -> PrivateBufferOwnerStarted | PrivateBufferFollowerJoined:
        incoming_files = list(files)
        async with self._lock:
            buf = self.buffers.get(user_id)
            if buf is None or buf["done"].is_set():
                if buf is not None:
                    self.buffers.pop(user_id, None)
                window_seconds = private_buffer_window_seconds(incoming_files, config)
                buf = self.buffers[user_id] = {
                    "queries": [merged_query],
                    "files": incoming_files,
                    "qwen_task": guardrail_task_factory(),
                    "done": asyncio.Event(),
                    "result": None,
                    "answer": None,
                    "deadline": now + window_seconds,
                    "window_seconds": window_seconds,
                }
                return PrivateBufferOwnerStarted(buf)

            if len(buf["queries"]) < config.max_messages:
                buf["queries"].append(merged_query)
            else:
                buf["queries"][-1] = join_buffered_messages(
                    [buf["queries"][-1], merged_query]
                )
            buf["files"] = merge_buffered_files(buf.get("files", []), incoming_files)
            window_seconds = private_buffer_window_seconds(incoming_files, config)
            buf["window_seconds"] = window_seconds
            buf["deadline"] = now + window_seconds
            return PrivateBufferFollowerJoined(buf["done"])

    async def deadline(self, user_id: str) -> float | None:
        async with self._lock:
            buf = self.buffers.get(user_id)
            if buf is None:
                return None
            return float(buf["deadline"])

    async def snapshot(self, user_id: str) -> PrivateBufferSnapshot | None:
        async with self._lock:
            buf = self.buffers.get(user_id)
            if buf is None:
                return None
            return PrivateBufferSnapshot(
                messages=list(buf["queries"]),
                files=list(buf.get("files", [])),
                guardrail_task=buf["qwen_task"],
            )

    async def store_guardrail_result(self, user_id: str, result: dict[str, Any]) -> None:
        async with self._lock:
            buf = self.buffers.get(user_id)
            if buf is not None:
                buf["result"] = result

    async def finalize(
        self,
        user_id: str,
        answer: str | None = None,
        *,
        clear_window: bool = True,
    ) -> None:
        async with self._lock:
            buf = self.buffers.get(user_id)
            if not buf:
                return
            if answer is not None:
                buf["answer"] = answer
            if not buf["done"].is_set():
                buf["done"].set()
            if clear_window:
                self.buffers.pop(user_id, None)
```

- [x] **步骤 2：运行新模块测试验证绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_private_buffer_split.py -v
```

预期：全部通过。

- [x] **步骤 3：运行扫描测试验证绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：全部通过。

- [x] **步骤 4：提交新模块**

运行：

```bash
git add api/chat_private_buffer.py .Codex/plans/api-chat-private-buffer-split.md
git diff --cached --check
git commit -m "refactor(普通API): 增加私聊缓冲基础件"
```

---

### 任务 3：父模块接入

**文件：**
- 修改：`api/routes.py`

- [x] **步骤 1：导入新模块并创建 store**

在 `api/routes.py` 的 `from api import (...)` 列表中加入：

```python
chat_private_buffer,
```

在 `_private_lock` 后创建 store：

```python
_private_buffer_store = chat_private_buffer.PrivateBufferStore(
    _private_buffers,
    _private_lock,
)
```

- [x] **步骤 2：保留父模块 wrapper 并委托新模块**

将 helper 改为：

```python
def _private_buffer_config() -> chat_private_buffer.PrivateBufferConfig:
    return chat_private_buffer.PrivateBufferConfig(
        max_messages=MAX_BUFFERED_MESSAGES,
        window_seconds=PRIVATE_BUFFER_WINDOW_SECONDS,
        window_with_files_seconds=PRIVATE_BUFFER_WINDOW_WITH_FILES_SECONDS,
        follower_timeout_seconds=PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS,
    )


def _join_buffered_messages(messages: list[str]) -> str:
    return chat_private_buffer.join_buffered_messages(messages)


def _merge_buffered_files(existing: list[str], incoming: Optional[List[str]]) -> list[str]:
    return chat_private_buffer.merge_buffered_files(existing, _normalize_files(incoming))


def _private_buffer_window_seconds(files: Optional[List[str]]) -> float:
    return chat_private_buffer.private_buffer_window_seconds(
        _normalize_files(files),
        _private_buffer_config(),
    )


async def _finalize_private_buffer(
    user_id: str,
    answer: str | None = None,
    *,
    clear_window: bool = True,
) -> None:
    await _private_buffer_store.finalize(
        user_id,
        answer,
        clear_window=clear_window,
    )
```

- [x] **步骤 3：替换 `proxy_chat()` 私聊缓冲原子字典操作**

在 guardrail 分支中保留父模块 task factory：

```python
def _guardrail_task_factory() -> asyncio.Task[Any]:
    return asyncio.create_task(
        asyncio.to_thread(
            _detect_guardrail,
            guardrail,
            guardrail_input,
            allow_passthrough=_is_guardrail_superuser(req.user_id),
        )
    )
```

用 store 替代锁内 create / append：

```python
buffer_result = await _private_buffer_store.begin_or_append(
    req.user_id,
    merged_query=merged,
    files=_normalize_files(req.files),
    guardrail_task_factory=_guardrail_task_factory,
    now=_time.time(),
    config=_private_buffer_config(),
)
```

如果返回 `PrivateBufferFollowerJoined`，使用 `buffer_result.done_event` 等待并返回 `private_buffer_follower`。

owner deadline loop 改为：

```python
while True:
    deadline = await _private_buffer_store.deadline(req.user_id)
    if deadline is None:
        return _chat_response_payload(
            req,
            status="silent",
            reason="private_buffer_missing",
            include_answer_chunks=True,
        )
    remaining = deadline - _time.time()
    if remaining <= 0:
        break
    await asyncio.sleep(remaining)
```

到期后 snapshot：

```python
snapshot = await _private_buffer_store.snapshot(req.user_id)
if snapshot is None:
    return _chat_response_payload(
        req,
        status="silent",
        reason="private_buffer_missing",
        include_answer_chunks=True,
    )
buffered_messages = snapshot.messages
buffered_files = snapshot.files
qwen_task = snapshot.guardrail_task
```

guardrail result 写回：

```python
await _private_buffer_store.store_guardrail_result(req.user_id, result)
```

- [x] **步骤 4：运行 split 与 private buffer 回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_private_buffer_split.py \
  tests/test_api.py::test_private_buffer_silent_releases_waiters \
  tests/test_api.py::test_private_buffer_refreshes_window_and_persists_merged_messages \
  tests/test_api.py::test_private_buffer_merges_files_for_final_bridge_request \
  tests/test_api.py::test_private_buffer_text_after_files_shrinks_window_to_five_seconds \
  tests/test_api.py::test_private_buffer_owner_cancel_releases_waiters_and_cleans_buffer \
  tests/test_api.py::test_private_buffer_bridge_cancel_releases_waiters_and_cleans_buffer \
  -v
```

预期：全部通过。

- [x] **步骤 5：运行 asyncio 策略与相邻 `/chat` 回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_asyncio_run_policy.py \
  tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta \
  tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta \
  tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder \
  tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner \
  tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder \
  tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send \
  -v
```

预期：全部通过。

- [x] **步骤 6：记录行数并提交父模块接入**

运行：

```bash
wc -l api/routes.py api/chat_private_buffer.py tests/test_api_chat_private_buffer_split.py
git add api/routes.py .Codex/plans/api-chat-private-buffer-split.md
git diff --cached --check
git commit -m "refactor(普通API): 接入私聊缓冲基础件"
```

---

### 任务 4：文档收口与全量验证

**文件：**
- 修改：`.Codex/plans/api-chat-private-buffer-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：记录阶段提交与验证结果**

在本计划和 `docs/plan_walkthrough.md` 中记录：

- 设计提交 `1ae8aa7 docs(普通API): 设计私聊缓冲拆分`
- 计划提交
- 红灯测试提交
- 新模块提交
- 父模块接入提交
- 各阶段 pytest 命令和结果
- 行数检查结果

- [ ] **步骤 2：更新 P3 进度**

在 `docs/todo.md` 的 P3 超大文件拆分条目中追加 `api/routes.py` 第十七刀进展，说明：

- 新增 `api/chat_private_buffer.py`。
- 父模块 `_private_buffers`、常量、`asyncio.sleep`、`_time.time`、guardrail / bridge / persistence patch point 均保留。
- 本阶段不引入 generation id。
- 全量回归结果。

- [ ] **步骤 3：运行文档自检**

运行：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-private-buffer-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-chat-private-buffer-split.md docs/todo.md docs/plan_walkthrough.md
```

预期：扫描无输出，diff 检查无输出。

- [ ] **步骤 4：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：全部通过，跳过数量和警告数量记录到 `docs/plan_walkthrough.md`。

- [ ] **步骤 5：提交文档收口**

运行：

```bash
git add .Codex/plans/api-chat-private-buffer-split.md docs/todo.md docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口私聊缓冲拆分"
```

---

## 验证记录

- 红灯测试：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_chat_private_buffer_split.py -v`
  - 结果：`4 failed, 1 passed, 1 warning`；失败原因是
    `api/chat_private_buffer.py` 不存在，符合预期红灯。
- 扫描红灯：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable -v`
  - 结果：`4 failed, 1 warning`；失败原因是扫描清单中的
    `api/chat_private_buffer.py` 不存在，符合预期红灯。
- 新模块绿灯：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_chat_private_buffer_split.py -v`
  - 结果：`5 passed, 1 warning`。
- 扫描绿灯：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable -v`
  - 结果：`4 passed, 1 warning`。
- 父模块接入回归：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_chat_private_buffer_split.py tests/test_api.py::test_private_buffer_silent_releases_waiters tests/test_api.py::test_private_buffer_refreshes_window_and_persists_merged_messages tests/test_api.py::test_private_buffer_merges_files_for_final_bridge_request tests/test_api.py::test_private_buffer_text_after_files_shrinks_window_to_five_seconds tests/test_api.py::test_private_buffer_owner_cancel_releases_waiters_and_cleans_buffer tests/test_api.py::test_private_buffer_bridge_cancel_releases_waiters_and_cleans_buffer -v`
  - 结果：`11 passed, 1 warning`。
- asyncio 策略与相邻 `/chat` 回归：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_asyncio_run_policy.py tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send -v`
  - 结果：`9 passed, 21 warnings`。
- 接入后扫描绿灯：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable -v`
  - 结果：`4 passed, 1 warning`。
- 行数检查：
  - 命令：`wc -l api/routes.py api/chat_private_buffer.py tests/test_api_chat_private_buffer_split.py`
  - 结果：`api/routes.py` 1351 行，`api/chat_private_buffer.py` 138 行，
    `tests/test_api_chat_private_buffer_split.py` 184 行。
- 计划文档自检：
  - 命令：`rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-private-buffer-split.md`
  - 结果：无输出，命令退出码为 1，表示未命中计划缺陷模式。
  - 命令：`git diff --check -- .Codex/plans/api-chat-private-buffer-split.md`
  - 结果：无输出，命令退出码为 0。
