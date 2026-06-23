# 普通 API Chat SSE Loop 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 `api.routes._stream_chat()` 内纯 SSE queue pump 拆到 `api/chat_sse_loop.py`，保留 `/chat` route、`StreamingResponse` 和业务收尾逻辑在父模块。

**架构：** 新模块只把 `stream_queue` 与 `done` event 转换为规范化 event dict，负责 queue wait、heartbeat、delta coalescing、tail drain 和 pending delta flush。父模块继续创建 queue、runner、coalescer 和 result context，并继续处理 error / audit / done / disconnect 分支。

**技术栈：** Python 3.12、asyncio queue / event / task、async iterator、pytest、源码静态扫描。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-23-api-chat-sse-loop-split-design.md`
- [x] 设计提交：`da2f761 docs(普通API): 设计聊天 SSE 循环拆分`

## 边界与禁止事项

- 保留：`api.routes.proxy_chat.__module__ == "api.routes"`。
- 保留：`/api/v1/chat` route 继续由 `api.routes` 注册。
- 保留：`StreamingResponse(_stream_chat(), media_type="text/event-stream")` 在父模块。
- 保留：`CHAT_STREAM_QUEUE_MAXSIZE` 在父模块读取并影响 `stream_queue`。
- 保留：runner task 创建、`bridge.handle_message(..., stream_queue=..., stream=True)` 调用在父模块。
- 保留：error path、Prompt V2 audit path、success done payload、evolution trigger、断连后台收尾调度在父模块。
- 保留：`_normalize_chat_stream_event()`、`_chat_sse_data()`、`_stream_error_event()`、`_chat_response_payload()`、`_persist_chat_turn()`、`_finalize_private_buffer()`、`_pop_bridge_reply_meta()` 等父模块 facade。
- 禁止：迁移完整 `_stream_chat()`。
- 禁止：迁移 `proxy_chat()` 或 `/chat` route。
- 禁止：新模块导入 `api.routes`、FastAPI、DB、`core.daily_digest`、`get_bridge()` 或 `get_guardrail()`。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：改 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约、message envelope、push envelope 或 response envelope。
- 禁止：处理 WebUI / JS。

## 文件职责

- 创建：`api/chat_sse_loop.py`
  - 定义 `ChatSseLoopCallbacks`。
  - 实现 `iter_chat_stream_events()`。
  - 只处理 SSE queue pump，不产生 SSE 字符串。
- 修改：`api/routes.py`
  - 导入 `chat_sse_loop`。
  - 在 `_stream_chat()` 内构造 callbacks。
  - 用 `async for event in chat_sse_loop.iter_chat_stream_events(...)` 替换原手写 loop。
- 创建：`tests/test_api_chat_sse_loop_split.py`
  - 锁定新模块源码边界。
  - 单测 heartbeat、done 快速结束、delta / progress 顺序、tail flush。
  - 锁定父模块仍保留 `StreamingResponse` 且委托新 loop。
- 修改：
  - `tests/test_api_group_message_routes_split.py`
  - `tests/test_api_agent_step_routes_split.py`
  - `tests/test_api_history_log_routes_split.py`
  - `tests/test_api_sticker_media_routes_split.py`
  - 将 `api/chat_sse_loop.py` 加入 chat split module 扫描清单。
- 修改：本计划文件，随执行更新复选框、验证结果和提交号。
- 修改：`docs/todo.md` 与 `docs/plan_walkthrough.md`，在最终文档收口阶段记录第二十四刀进展。

---

## 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_sse_loop_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`
- 修改：`.Codex/plans/api-chat-sse-loop-split.md`

- [x] **步骤 1：创建 SSE loop split 测试文件**

创建 `tests/test_api_chat_sse_loop_split.py`，写入：

```python
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _normalize(raw: Any) -> dict[str, Any] | None:
    if raw == "skip":
        return None
    if isinstance(raw, dict):
        return dict(raw)
    return None
```

- [x] **步骤 2：新增源码约束红灯**

在同一文件追加：

```python
def test_chat_sse_loop_module_does_not_import_parent_routes_or_runtime_side_effects():
    source = _source("api/chat_sse_loop.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "StreamingResponse" not in source
    assert "APIRouter" not in source
    assert "BackgroundTasks" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source
    assert "_persist_chat_turn(" not in source
    assert "push_envelope_to_qq" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
```

- [x] **步骤 3：新增 heartbeat 红灯**

在同一文件追加：

```python
@pytest.mark.asyncio
async def test_iter_chat_stream_events_emits_heartbeat_when_idle():
    from api.chat_sse_loop import ChatSseLoopCallbacks, iter_chat_stream_events
    from api.chat_streaming_helpers import StreamEventCoalescer

    queue: asyncio.Queue[Any] = asyncio.Queue()
    done = asyncio.Event()
    iterator = iter_chat_stream_events(
        queue,
        done,
        heartbeat_interval=0.001,
        coalescer=StreamEventCoalescer(),
        callbacks=ChatSseLoopCallbacks(normalize_event=_normalize),
    )

    event = await asyncio.wait_for(anext(iterator), timeout=1)
    done.set()
    await iterator.aclose()

    assert event == {"status": "heartbeat"}
```

- [x] **步骤 4：新增 done 快速结束红灯**

在同一文件追加：

```python
@pytest.mark.asyncio
async def test_iter_chat_stream_events_stops_after_done_without_waiting_for_heartbeat():
    from api.chat_sse_loop import ChatSseLoopCallbacks, iter_chat_stream_events
    from api.chat_streaming_helpers import StreamEventCoalescer

    queue: asyncio.Queue[Any] = asyncio.Queue()
    done = asyncio.Event()
    done.set()

    started = time.perf_counter()
    events = [
        event
        async for event in iter_chat_stream_events(
            queue,
            done,
            heartbeat_interval=5.0,
            coalescer=StreamEventCoalescer(),
            callbacks=ChatSseLoopCallbacks(normalize_event=_normalize),
        )
    ]
    elapsed = time.perf_counter() - started

    assert events == []
    assert elapsed < 0.2
```

- [x] **步骤 5：新增 delta / progress 和 tail flush 红灯**

在同一文件追加：

```python
@pytest.mark.asyncio
async def test_iter_chat_stream_events_coalesces_delta_before_progress_and_flushes_tail():
    from api.chat_sse_loop import ChatSseLoopCallbacks, iter_chat_stream_events
    from api.chat_streaming_helpers import StreamEventCoalescer

    queue: asyncio.Queue[Any] = asyncio.Queue()
    queue.put_nowait({"status": "delta", "text": "你"})
    queue.put_nowait({"status": "delta", "text": "好"})
    queue.put_nowait("skip")
    queue.put_nowait({"status": "progress", "text": "工具"})
    queue.put_nowait({"status": "delta", "text": "！"})
    done = asyncio.Event()
    done.set()

    events = [
        event
        async for event in iter_chat_stream_events(
            queue,
            done,
            heartbeat_interval=5.0,
            coalescer=StreamEventCoalescer(),
            callbacks=ChatSseLoopCallbacks(normalize_event=_normalize),
        )
    ]

    assert events == [
        {"status": "delta", "text": "你好"},
        {"status": "progress", "text": "工具"},
        {"status": "delta", "text": "！"},
    ]
```

- [x] **步骤 6：新增父模块接入红灯**

在同一文件追加：

```python
def test_parent_stream_chat_delegates_sse_loop_and_keeps_streaming_response_boundary():
    source = _source("api/routes.py")

    assert "chat_sse_loop" in source
    assert "chat_sse_loop.iter_chat_stream_events" in source
    assert "StreamingResponse(_stream_chat(), media_type=\"text/event-stream\")" in source
    assert "asyncio.wait(\n                        {get_task, done_task}," not in source
```

- [x] **步骤 7：更新 split module 扫描清单**

在以下文件的 `test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable()` 路径列表中追加：

```python
"api/chat_sse_loop.py",
```

需要修改的文件：

- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 8：运行红灯**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_sse_loop_split.py \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：

- 失败。
- 失败点为 `api/chat_sse_loop.py` 不存在，或 `api.chat_sse_loop` 无法导入，父模块尚未委托新 loop。

- [x] **步骤 9：提交红灯测试**

```bash
git add tests/test_api_chat_sse_loop_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_sticker_media_routes_split.py \
  .Codex/plans/api-chat-sse-loop-split.md
git commit -m "test(普通API): 锁定聊天 SSE 循环契约"
```

---

## 任务 2：新增 SSE loop 模块

**文件：**
- 创建：`api/chat_sse_loop.py`
- 修改：`.Codex/plans/api-chat-sse-loop-split.md`

- [ ] **步骤 1：创建新模块**

创建 `api/chat_sse_loop.py`：

```python
"""聊天 SSE 事件循环 helper。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from api.chat_streaming_helpers import (
    StreamEventCoalescer,
    collect_ready_stream_events,
)


@dataclass(frozen=True)
class ChatSseLoopCallbacks:
    normalize_event: Callable[[Any], dict[str, Any] | None]


async def _yield_ready_events(
    raw_event: Any,
    stream_queue: asyncio.Queue[Any],
    *,
    coalescer: StreamEventCoalescer,
    callbacks: ChatSseLoopCallbacks,
) -> AsyncIterator[dict[str, Any]]:
    events = collect_ready_stream_events(
        raw_event,
        stream_queue,
        normalize_event=callbacks.normalize_event,
        coalescer=coalescer,
    )
    for event in events:
        yield event


async def iter_chat_stream_events(
    stream_queue: asyncio.Queue[Any],
    done: asyncio.Event,
    *,
    heartbeat_interval: float,
    coalescer: StreamEventCoalescer,
    callbacks: ChatSseLoopCallbacks,
) -> AsyncIterator[dict[str, Any]]:
    while True:
        if done.is_set() and stream_queue.empty():
            break

        get_task = asyncio.create_task(stream_queue.get())
        done_task = asyncio.create_task(done.wait())
        try:
            completed, pending = await asyncio.wait(
                {get_task, done_task},
                timeout=heartbeat_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for pending_task in pending:
                pending_task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if not completed:
                yield {"status": "heartbeat"}
                continue

            if get_task in completed:
                async for event in _yield_ready_events(
                    get_task.result(),
                    stream_queue,
                    coalescer=coalescer,
                    callbacks=callbacks,
                ):
                    yield event
                continue

            if done_task in completed:
                break
        finally:
            for task in (get_task, done_task):
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    await asyncio.sleep(0)
    while True:
        try:
            event = stream_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        async for ready_event in _yield_ready_events(
            event,
            stream_queue,
            coalescer=coalescer,
            callbacks=callbacks,
        ):
            yield ready_event

    pending_delta = coalescer.flush()
    if pending_delta is not None:
        yield pending_delta
```

- [ ] **步骤 2：运行新模块定向测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/test_api_chat_sse_loop_split.py -v
```

预期：

- 新模块行为测试通过。
- 父模块接入测试仍失败，因为 `api/routes.py` 尚未委托新 loop。

- [ ] **步骤 3：静态检查**

运行：

```bash
python -m compileall api/chat_sse_loop.py -q
git diff --check -- api/chat_sse_loop.py tests/test_api_chat_sse_loop_split.py \
  tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py \
  tests/test_api_history_log_routes_split.py tests/test_api_sticker_media_routes_split.py \
  .Codex/plans/api-chat-sse-loop-split.md
```

预期：

- `compileall` 退出码 0。
- `git diff --check` 无输出，退出码 0。

- [ ] **步骤 4：提交新模块**

```bash
git add api/chat_sse_loop.py .Codex/plans/api-chat-sse-loop-split.md
git commit -m "refactor(普通API): 增加聊天 SSE 循环助手"
```

---

## 任务 3：接入父模块 `_stream_chat()`

**文件：**
- 修改：`api/routes.py`
- 修改：`.Codex/plans/api-chat-sse-loop-split.md`

- [ ] **步骤 1：导入新模块**

在 `api/routes.py` 的 `from api import (...)` 导入列表中加入：

```python
chat_sse_loop,
```

- [ ] **步骤 2：构造 SSE callbacks**

在 `_stream_chat()` 内创建 `coalescer` 后增加：

```python
sse_loop_callbacks = chat_sse_loop.ChatSseLoopCallbacks(
    normalize_event=_normalize_chat_stream_event,
)
```

- [ ] **步骤 3：替换手写 SSE 主循环**

删除 `_yield_queue_event()` 局部函数，以及从 `while True:` 到 `pending_delta` flush 的手写 loop，替换为：

```python
            async for event in chat_sse_loop.iter_chat_stream_events(
                stream_queue,
                done,
                heartbeat_interval=heartbeat_interval,
                coalescer=coalescer,
                callbacks=sse_loop_callbacks,
            ):
                yield _chat_sse_data(event)
```

保留后续 `if "error" in result_holder:`、success done、audit、error 和 `finally` 分支不变。

- [ ] **步骤 4：运行 SSE loop split 绿灯**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/test_api_chat_sse_loop_split.py -v
```

预期：

- 全部通过。

- [ ] **步骤 5：运行 split 扫描绿灯**

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

预期：

- 全部通过。

- [ ] **步骤 6：运行 streaming 相邻回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
  tests/test_streaming_api.py \
  tests/test_streaming_response_envelope.py \
  tests/test_api_chat_streaming_helpers_split.py \
  tests/test_api_chat_streaming_result_split.py \
  tests/test_chat_response_envelope.py \
  tests/test_asyncio_run_policy.py \
  -v
```

预期：

- 全部通过。

- [ ] **步骤 7：运行断连路径回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
  tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder \
  tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner \
  tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder \
  tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send \
  -v
```

预期：

- 全部通过。

- [ ] **步骤 8：静态检查和行数记录**

运行：

```bash
python -m compileall api/routes.py api/chat_sse_loop.py -q
wc -l api/routes.py api/chat_sse_loop.py tests/test_api_chat_sse_loop_split.py
git diff --check -- api/routes.py api/chat_sse_loop.py tests/test_api_chat_sse_loop_split.py \
  tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py \
  tests/test_api_history_log_routes_split.py tests/test_api_sticker_media_routes_split.py \
  .Codex/plans/api-chat-sse-loop-split.md
```

预期：

- `compileall` 退出码 0。
- `api/routes.py` 行数下降。
- `git diff --check` 无输出，退出码 0。

- [ ] **步骤 9：提交父模块接入**

```bash
git add api/routes.py .Codex/plans/api-chat-sse-loop-split.md
git commit -m "refactor(普通API): 接入聊天 SSE 循环助手"
```

---

## 任务 4：文档收口与最终验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/api-chat-sse-loop-split.md`

- [ ] **步骤 1：运行最终全量验证**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：

- 0 failures。
- 记录 passed / skipped / warnings 和耗时。

- [ ] **步骤 2：更新计划执行记录**

在本计划底部追加执行记录，至少包含：

- 红灯输出摘要。
- 新模块定向输出摘要。
- split 扫描输出摘要。
- streaming 相邻回归输出摘要。
- 断连路径回归输出摘要。
- 行数检查。
- 全量测试结果。
- 提交列表。

- [ ] **步骤 3：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」中追加第二十四刀进展，记录：

- 新模块路径。
- 父模块保留 `/chat` route、`StreamingResponse`、queue 创建、runner、业务收尾和断连后台调度。
- 新模块只负责 SSE queue pump。
- 新模块没有反向导入父模块，也没有同步包装 awaitable。
- `api/routes.py` 的真实行数变化和验证结果。

- [ ] **步骤 4：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-23 普通 API Chat SSE Loop 拆分` 小节，包含：

- 状态。
- 设计文档路径。
- 实现计划路径。
- 阶段提交列表。
- 计划列表完成状态。
- 验证记录。
- 执行约束和下一步建议。

- [ ] **步骤 5：文档自检**

运行：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-sse-loop-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-chat-sse-loop-split.md docs/todo.md docs/plan_walkthrough.md
```

预期：

- `rg` 无输出，退出码 1。
- `git diff --check` 无输出，退出码 0。

- [ ] **步骤 6：提交文档收口**

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-chat-sse-loop-split.md
git commit -m "docs(计划): 收口聊天 SSE 循环拆分"
```

---

## 执行记录

- 2026-06-23 设计阶段：
  写入 `docs/superpowers/specs/2026-06-23-api-chat-sse-loop-split-design.md`，
  并随 `da2f761 docs(普通API): 设计聊天 SSE 循环拆分` 提交。
- 2026-06-23 任务 1 红灯：
  `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -B -m pytest -p no:cacheprovider tests/test_api_chat_sse_loop_split.py tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable -v`
  退出码 1，`9 failed, 1 warning`。失败点为 `api/chat_sse_loop.py` 不存在、
  `api.chat_sse_loop` 无法导入，以及父模块尚未委托新 loop，符合红灯预期。
- 2026-06-23 任务 1 提交：
  随本次 `test(普通API): 锁定聊天 SSE 循环契约` 提交。
