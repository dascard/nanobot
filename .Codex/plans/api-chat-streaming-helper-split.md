# 聊天流式助手拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api.routes._stream_chat()` 内的连续 delta 合并和 bounded queue drain 逻辑拆到 `api/chat_streaming_helpers.py`，保留 `/chat` 路由本体、后台落库、push 和所有父模块 patch point。

**架构：** 新模块只提供 streaming event 纯 helper 和一个 async queue drain helper，不导入 `api.routes`，不做 SSE 序列化，不访问数据库，不触发 push。`api.routes` 继续创建 `stream_queue`、读取 `CHAT_STREAM_QUEUE_MAXSIZE`、调用 `_chat_sse_data()`，并保留 `_persist_stream_result_after_runner_done()`。

**技术栈：** Python 3.12、FastAPI / Starlette StreamingResponse、pytest、asyncio Queue。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-23-api-chat-streaming-helper-split-design.md`
- [x] 设计提交：`d17b484 docs(普通API): 设计聊天流式助手拆分`

## 边界与禁止事项

- 保留：`api.routes.proxy_chat.__module__ == "api.routes"`。
- 保留：`StreamingResponse(_stream_chat(), media_type="text/event-stream")` 在父模块。
- 保留：`api.routes.CHAT_STREAM_QUEUE_MAXSIZE` 作为 queue maxsize 的读取点。
- 保留：`api.routes._normalize_chat_stream_event()`、`_chat_sse_data()`、`_stream_error_event()`、`_chat_response_payload()` wrapper。
- 保留：`_persist_stream_result_after_runner_done()`、断连后台落库、QQ push envelope、Prompt audit meta、private buffer finalize 和 evolution 触发。
- 禁止：新模块导入 `api.routes`。
- 禁止：新模块调用 `get_bridge()`、`get_guardrail()`、`_persist_chat_turn()` 或 push 函数。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：改变 done 权威语义；最终 `done.answer` 继续来自 `bridge.handle_message()` 返回值。

## 文件职责

- 创建：`tests/test_api_chat_streaming_helpers_split.py`
  - 锁定新模块 import hygiene。
  - 锁定 delta coalescing、progress / final 前 flush、尾部 flush、queue ready drain 和 bounded queue drain。
- 创建：`api/chat_streaming_helpers.py`
  - 提供 `StreamEventCoalescer`。
  - 提供 `collect_ready_stream_events()`。
  - 提供 `drain_stream_queue_until_task_done()`。
- 修改：`api/routes.py`
  - 导入 `chat_streaming_helpers`。
  - 在 `_stream_chat()` 中用新 helper 替代内联 pending delta / drain helper。
- 修改：`tests/test_streaming_api.py`
  - 收紧 `CHAT_STREAM_QUEUE_MAXSIZE` monkeypatch 精确契约。
- 修改：4 个普通 API split 扫描测试：
  - `tests/test_api_history_log_routes_split.py`
  - `tests/test_api_agent_step_routes_split.py`
  - `tests/test_api_group_message_routes_split.py`
  - `tests/test_api_sticker_media_routes_split.py`
- 修改：`.Codex/plans/api-chat-streaming-helper-split.md`
  - 随执行记录验证结果。
- 修改：`docs/todo.md`
  - 记录 P3 中 `api/routes.py` streaming helper 小刀进展和行数。
- 修改：`docs/plan_walkthrough.md`
  - 追加 2026-06-23 streaming helper 执行记录、提交列表和验证证据。

---

### 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_streaming_helpers_split.py`
- 修改：`tests/test_streaming_api.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 1：编写 helper 契约测试**

在 `tests/test_api_chat_streaming_helpers_split.py` 写入：

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_streaming_helpers_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_streaming_helpers.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source
    assert "_persist_chat_turn(" not in source


def test_stream_event_coalescer_merges_delta_until_non_delta():
    from api.chat_streaming_helpers import StreamEventCoalescer

    coalescer = StreamEventCoalescer()

    assert coalescer.feed({"status": "delta", "text": "你"}) == []
    assert coalescer.feed({"status": "delta", "text": "好"}) == []
    assert coalescer.feed({"status": "progress", "text": "工具"}) == [
        {"status": "delta", "text": "你好"},
        {"status": "progress", "text": "工具"},
    ]


def test_stream_event_coalescer_flushes_tail_delta_once():
    from api.chat_streaming_helpers import StreamEventCoalescer

    coalescer = StreamEventCoalescer()
    coalescer.feed({"status": "delta", "text": "最"})
    coalescer.feed({"status": "delta", "text": "后"})

    assert coalescer.flush() == {"status": "delta", "text": "最后"}
    assert coalescer.flush() is None


def test_collect_ready_stream_events_drains_current_queue_for_delta_first_event():
    from api.chat_streaming_helpers import (
        StreamEventCoalescer,
        collect_ready_stream_events,
    )

    queue: asyncio.Queue[object] = asyncio.Queue()
    queue.put_nowait({"status": "delta", "text": "好"})
    queue.put_nowait("skip")
    queue.put_nowait({"status": "progress", "text": "工具"})
    queue.put_nowait({"status": "delta", "text": "！"})

    def normalize(raw: object) -> dict[str, object] | None:
        if raw == "skip":
            return None
        assert isinstance(raw, dict)
        return raw

    events = collect_ready_stream_events(
        {"status": "delta", "text": "你"},
        queue,
        normalize_event=normalize,
        coalescer=StreamEventCoalescer(),
    )

    assert events == [
        {"status": "delta", "text": "你好"},
        {"status": "progress", "text": "工具"},
        {"status": "delta", "text": "！"},
    ]
    assert queue.empty()


def test_collect_ready_stream_events_does_not_drain_for_non_delta_first_event():
    from api.chat_streaming_helpers import (
        StreamEventCoalescer,
        collect_ready_stream_events,
    )

    queue: asyncio.Queue[object] = asyncio.Queue()
    queue.put_nowait({"status": "delta", "text": "later"})

    events = collect_ready_stream_events(
        {"status": "progress", "text": "先发"},
        queue,
        normalize_event=lambda raw: raw if isinstance(raw, dict) else None,
        coalescer=StreamEventCoalescer(),
    )

    assert events == [{"status": "progress", "text": "先发"}]
    assert not queue.empty()


@pytest.mark.asyncio
async def test_drain_stream_queue_until_task_done_drains_bounded_queue():
    from api.chat_streaming_helpers import drain_stream_queue_until_task_done

    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)

    async def producer() -> None:
        await queue.put({"status": "delta", "text": "A"})
        await queue.put({"status": "delta", "text": "B"})
        await queue.put({"status": "final", "text": "AB"})

    runner_task = asyncio.create_task(producer())

    await drain_stream_queue_until_task_done(
        queue,
        runner_task,
        poll_timeout=0.001,
    )

    assert runner_task.done()
    assert queue.empty()
```

- [x] **步骤 2：将新模块加入普通 API split 扫描清单**

在以下 4 个文件的 `path` tuple 中追加：

```python
"api/chat_streaming_helpers.py",
```

文件：

- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 3：收紧 streaming queue maxsize 行为测试**

在 `tests/test_streaming_api.py::test_stream_chat_uses_bounded_stream_queue` 中加入 `monkeypatch` 参数，导入 `api.routes`，并把断言改为精确值：

```python
def test_stream_chat_uses_bounded_stream_queue(client, monkeypatch):
    from unittest.mock import patch

    from api import routes

    monkeypatch.setattr(routes, "CHAT_STREAM_QUEUE_MAXSIZE", 7, raising=False)
    captured = {}
    ...
    assert captured["maxsize"] == 7
```

- [x] **步骤 4：运行 helper 红灯测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_streaming_helpers_split.py -v
```

预期：失败，原因是 `api/chat_streaming_helpers.py` 尚不存在。

- [x] **步骤 5：运行扫描红灯测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：4 个测试失败，原因是扫描清单中的 `api/chat_streaming_helpers.py` 尚不存在。

- [x] **步骤 6：提交红灯测试**

运行：

```bash
git add tests/test_api_chat_streaming_helpers_split.py \
  tests/test_streaming_api.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py \
  .Codex/plans/api-chat-streaming-helper-split.md
git diff --cached --check
git commit -m "test(普通API): 锁定聊天流式助手契约"
```

---

### 任务 2：新增 streaming helper 模块

**文件：**
- 创建：`api/chat_streaming_helpers.py`

- [x] **步骤 1：编写 helper 实现**

在 `api/chat_streaming_helpers.py` 写入：

```python
"""聊天流式事件辅助函数。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any


class StreamEventCoalescer:
    def __init__(self) -> None:
        self._delta_parts: list[str] = []

    def feed(self, event: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        if event is None:
            return []

        if event.get("status") == "delta":
            self._delta_parts.append(str(event.get("text") or ""))
            return []

        output: list[dict[str, Any]] = []
        pending_delta = self.flush()
        if pending_delta is not None:
            output.append(pending_delta)
        output.append(dict(event))
        return output

    def flush(self) -> dict[str, str] | None:
        if not self._delta_parts:
            return None
        text = "".join(self._delta_parts)
        self._delta_parts.clear()
        return {"status": "delta", "text": text}


def collect_ready_stream_events(
    raw_event: Any,
    stream_queue: asyncio.Queue[Any],
    *,
    normalize_event: Callable[[Any], dict[str, Any] | None],
    coalescer: StreamEventCoalescer,
) -> list[dict[str, Any]]:
    event = normalize_event(raw_event)
    output = coalescer.feed(event)
    if event is None or event.get("status") != "delta":
        return output

    while True:
        try:
            next_raw = stream_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        output.extend(coalescer.feed(normalize_event(next_raw)))

    pending_delta = coalescer.flush()
    if pending_delta is not None:
        output.append(pending_delta)
    return output


async def drain_stream_queue_until_task_done(
    stream_queue: asyncio.Queue[Any],
    runner_task: asyncio.Task[Any],
    *,
    poll_timeout: float = 0.1,
) -> None:
    while True:
        while True:
            try:
                stream_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        if runner_task.done():
            return

        try:
            await asyncio.wait_for(stream_queue.get(), timeout=poll_timeout)
        except asyncio.TimeoutError:
            continue
```

- [x] **步骤 2：运行 helper 测试验证绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_streaming_helpers_split.py -v
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
git add api/chat_streaming_helpers.py .Codex/plans/api-chat-streaming-helper-split.md
git diff --cached --check
git commit -m "refactor(普通API): 增加聊天流式助手"
```

---

### 任务 3：父模块接入

**文件：**
- 修改：`api/routes.py`

- [x] **步骤 1：导入新模块**

在 `api/routes.py` 的 `from api import (...)` 列表中加入：

```python
chat_streaming_helpers,
```

- [x] **步骤 2：替换 pending delta 内联逻辑**

在 `_stream_chat()` 中将 `pending_delta_parts`、`_pop_pending_delta_event()` 和 `_yield_queue_event()` 的内联实现替换为：

```python
coalescer = chat_streaming_helpers.StreamEventCoalescer()

async def _yield_queue_event(raw_event: Any):
    events = chat_streaming_helpers.collect_ready_stream_events(
        raw_event,
        stream_queue,
        normalize_event=_normalize_chat_stream_event,
        coalescer=coalescer,
    )
    for event in events:
        yield _chat_sse_data(event)
```

- [x] **步骤 3：替换尾部 delta flush**

将 `_stream_chat()` 尾部：

```python
pending_delta = _pop_pending_delta_event()
if pending_delta is not None:
    yield _chat_sse_data(pending_delta)
```

替换为：

```python
pending_delta = coalescer.flush()
if pending_delta is not None:
    yield _chat_sse_data(pending_delta)
```

- [x] **步骤 4：替换 queue drain helper**

将 `_drain_stream_queue_until_runner_done()` 改为：

```python
async def _drain_stream_queue_until_runner_done() -> None:
    await chat_streaming_helpers.drain_stream_queue_until_task_done(
        stream_queue,
        runner_task,
    )
```

- [x] **步骤 5：运行 streaming helper 与行为回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_streaming_helpers_split.py \
  tests/test_streaming_api.py \
  tests/test_streaming_response_envelope.py \
  -v
```

预期：全部通过。

- [x] **步骤 6：运行断连后台相邻回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder \
  tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner \
  tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder \
  tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send \
  tests/test_asyncio_run_policy.py \
  -v
```

预期：全部通过。

- [x] **步骤 7：提交父模块接入**

运行：

```bash
git add api/routes.py .Codex/plans/api-chat-streaming-helper-split.md
git diff --cached --check
git commit -m "refactor(普通API): 接入聊天流式助手"
```

---

### 任务 4：文档收口与全量验证

**文件：**
- 修改：`.Codex/plans/api-chat-streaming-helper-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：记录行数变化**

运行：

```bash
wc -l api/routes.py api/chat_streaming_helpers.py tests/test_api_chat_streaming_helpers_split.py
```

将输出写入本计划、`docs/todo.md` 和 `docs/plan_walkthrough.md`。

- [x] **步骤 2：记录阶段提交与验证结果**

在本计划和 `docs/plan_walkthrough.md` 中记录：

- 设计提交 `d17b484 docs(普通API): 设计聊天流式助手拆分`
- 计划提交
- 红灯测试提交
- 新模块提交
- 父模块接入提交
- 各阶段 pytest 命令和结果

- [x] **步骤 3：运行文档自检**

运行：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-streaming-helper-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-chat-streaming-helper-split.md docs/todo.md docs/plan_walkthrough.md
```

预期：扫描无输出，diff 检查无输出。

- [x] **步骤 4：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：全部通过，跳过数量和警告数量记录到 `docs/plan_walkthrough.md`。

- [x] **步骤 5：提交文档收口**

运行：

```bash
git add .Codex/plans/api-chat-streaming-helper-split.md docs/todo.md docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口聊天流式助手拆分"
```

---

## 验证记录

- 阶段提交：
  - 设计提交：`d17b484 docs(普通API): 设计聊天流式助手拆分`。
  - 计划提交：`4f4f0da docs(计划): 记录聊天流式助手计划`。
  - 红灯测试提交：`e6554a0 test(普通API): 锁定聊天流式助手契约`。
  - 新模块提交：`ff95753 refactor(普通API): 增加聊天流式助手`。
  - 父模块接入提交：`a6d2a8b refactor(普通API): 接入聊天流式助手`。
  - 文档收口提交：随本次 `docs(计划): 收口聊天流式助手拆分` 完成。
- 红灯测试：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_chat_streaming_helpers_split.py -v`
  - 结果：6 failed、1 warning；失败原因是 `api/chat_streaming_helpers.py` 不存在，符合预期红灯。
- 扫描红灯：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable -v`
  - 结果：4 failed、1 warning；失败原因是扫描清单中的 `api/chat_streaming_helpers.py` 不存在，符合预期红灯。
- 新模块绿灯：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_chat_streaming_helpers_split.py -v`
  - 结果：6 passed、1 warning。
- 扫描绿灯：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable -v`
  - 结果：4 passed、1 warning。
- 父模块 streaming 回归：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_chat_streaming_helpers_split.py tests/test_streaming_api.py tests/test_streaming_response_envelope.py -v`
  - 结果：15 passed、21 warnings。
- 断连后台与 asyncio 策略回归：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send tests/test_asyncio_run_policy.py -v`
  - 结果：7 passed、1 warning。
- 行数检查：
  - 命令：`wc -l api/routes.py api/chat_streaming_helpers.py tests/test_api_chat_streaming_helpers_split.py`
  - 结果：`api/routes.py` 1408 行，`api/chat_streaming_helpers.py` 81 行，
    `tests/test_api_chat_streaming_helpers_split.py` 122 行。
- 全量回归：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/ -v`
  - 结果：`1722 passed, 6 skipped, 139 warnings in 131.90s`。
- 计划文档自检：
  - 命令：`rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-streaming-helper-split.md docs/todo.md docs/plan_walkthrough.md`
  - 结果：无输出，命令退出码为 1，表示未命中文档缺陷模式。
  - 命令：`git diff --check -- .Codex/plans/api-chat-streaming-helper-split.md docs/todo.md docs/plan_walkthrough.md`
  - 结果：无输出，命令退出码为 0。
