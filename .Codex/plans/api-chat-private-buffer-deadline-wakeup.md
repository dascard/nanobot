# 私聊缓冲 Deadline 主动唤醒实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 修复私聊缓冲 owner 在 deadline 被 follower 缩短后不会主动醒来的时序问题，并把等待 deadline 的细节收口到 `api/chat_private_buffer.py`。

**架构：** `PrivateBufferStore` 为每个 buffer 管理 `deadline_changed` signal，并提供 `wait_until_deadline()`；`api.routes` 保留 `/chat` 主链路和父模块 patch point，只新增 `_wait_private_buffer_deadline()` 薄 wrapper 并替换 owner 手写 sleep loop。

**技术栈：** Python 3.12、FastAPI、asyncio、pytest、SQLAlchemy 测试 fixture。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-23-api-chat-private-buffer-deadline-wakeup-design.md`
- [x] 设计提交：`90953f0 docs(普通API): 设计私聊缓冲唤醒`

## 边界与禁止事项

- 保留：`api.routes.proxy_chat.__module__ == "api.routes"`。
- 保留：`api.routes._private_buffers`、`api.routes._private_lock` 和 `_private_buffer_store` 作为 runtime 状态入口。
- 保留：`_join_buffered_messages()`、`_merge_buffered_files()`、`_private_buffer_window_seconds()`、`_finalize_private_buffer()` 父模块 wrapper。
- 新增：`api.routes._wait_private_buffer_deadline()` 父模块 wrapper。
- 保留：`get_guardrail()`、`_detect_guardrail()`、`get_bridge()`、`_persist_chat_turn()`、`_chat_response_payload()` patch point。
- 保留：PrivateTimingGate、guardrail、Bridge、落库、SSE、push envelope 和 response envelope 均在父模块。
- 禁止：迁移完整私聊 flow。
- 禁止：迁移 `_stream_chat()`、`StreamingResponse` 或 stream finalizer。
- 禁止：新模块导入 `api.routes`。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：引入 generation id 或改变 `_finalize_private_buffer(user_id)` 的 user-level 语义。

## 文件职责

- 修改：`tests/test_api_chat_private_buffer_split.py`
  - 新增 store 级 deadline shrink wakeup 红灯。
  - 新增 finalize 唤醒 deadline waiter 红灯。
  - 加厚父模块 wrapper 契约，锁定 `_wait_private_buffer_deadline()` 仍属于 `api.routes`。
- 修改：`tests/test_api.py`
  - 加厚 `test_private_buffer_text_after_files_shrinks_window_to_five_seconds`，验证 owner 不再依赖测试手动释放旧 sleep。
- 修改：`api/chat_private_buffer.py`
  - 为 buffer 增加 `deadline_changed` 内部 event。
  - follower append 更新 deadline 后触发 event。
  - 新增 `wait_until_deadline()`。
  - finalize 时唤醒 deadline waiter。
- 修改：`api/routes.py`
  - 新增 `_wait_private_buffer_deadline()` wrapper。
  - 将 owner 手写 deadline / sleep loop 改为 wrapper 调用。
- 修改：`.Codex/plans/api-chat-private-buffer-deadline-wakeup.md`
  - 随执行记录验证结果。
- 修改：`docs/todo.md`
  - 记录 P3 中私聊缓冲 deadline wakeup 小刀。
- 修改：`docs/plan_walkthrough.md`
  - 追加 2026-06-23 私聊缓冲 deadline wakeup 执行记录、提交列表和验证证据。

---

## 任务 1：红灯测试

**文件：**
- 修改：`tests/test_api_chat_private_buffer_split.py`
- 修改：`tests/test_api.py`

- [x] **步骤 1：新增 store 级 deadline shrink 红灯**

在 `tests/test_api_chat_private_buffer_split.py` 追加：

```python
@pytest.mark.asyncio
async def test_private_buffer_store_wakes_owner_when_deadline_shrinks():
    from api.chat_private_buffer import PrivateBufferConfig, PrivateBufferStore

    buffers: dict[str, dict] = {}
    store = PrivateBufferStore(buffers, asyncio.Lock())
    config = PrivateBufferConfig(
        max_messages=3,
        window_seconds=5.0,
        window_with_files_seconds=10.0,
        follower_timeout_seconds=900.0,
    )
    fake_now = {"value": 0.0}
    old_sleep_started = asyncio.Event()
    old_sleep_cancelled = asyncio.Event()
    real_sleep = asyncio.sleep

    def task_factory() -> asyncio.Task[dict[str, str]]:
        return asyncio.create_task(asyncio.sleep(0, result={"status": "reply"}))

    async def controlled_sleep(delay: float) -> None:
        assert delay == 10.0
        old_sleep_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            old_sleep_cancelled.set()
            raise

    await store.begin_or_append(
        "u-wakeup",
        merged_query="先看图片",
        files=["a.png"],
        guardrail_task_factory=task_factory,
        now=0.0,
        config=config,
    )

    waiter = asyncio.create_task(
        store.wait_until_deadline(
            "u-wakeup",
            now=lambda: fake_now["value"],
            sleep=controlled_sleep,
        )
    )
    await asyncio.wait_for(old_sleep_started.wait(), timeout=1)

    fake_now["value"] = 3.0
    await store.begin_or_append(
        "u-wakeup",
        merged_query="然后看文本",
        files=[],
        guardrail_task_factory=task_factory,
        now=3.0,
        config=config,
    )

    await asyncio.wait_for(old_sleep_cancelled.wait(), timeout=1)
    assert not waiter.done()

    fake_now["value"] = 8.0
    assert await asyncio.wait_for(waiter, timeout=1) is True
```

- [x] **步骤 2：新增 finalize 唤醒红灯**

在 `tests/test_api_chat_private_buffer_split.py` 追加：

```python
@pytest.mark.asyncio
async def test_private_buffer_store_finalize_wakes_deadline_waiter():
    from api.chat_private_buffer import PrivateBufferConfig, PrivateBufferStore

    buffers: dict[str, dict] = {}
    store = PrivateBufferStore(buffers, asyncio.Lock())
    config = PrivateBufferConfig(
        max_messages=3,
        window_seconds=5.0,
        window_with_files_seconds=10.0,
        follower_timeout_seconds=900.0,
    )
    old_sleep_started = asyncio.Event()
    old_sleep_cancelled = asyncio.Event()

    def task_factory() -> asyncio.Task[dict[str, str]]:
        return asyncio.create_task(asyncio.sleep(0, result={"status": "reply"}))

    async def controlled_sleep(delay: float) -> None:
        assert delay == 5.0
        old_sleep_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            old_sleep_cancelled.set()
            raise

    await store.begin_or_append(
        "u-finalize-wakeup",
        merged_query="第一句",
        files=[],
        guardrail_task_factory=task_factory,
        now=0.0,
        config=config,
    )
    waiter = asyncio.create_task(
        store.wait_until_deadline(
            "u-finalize-wakeup",
            now=lambda: 0.0,
            sleep=controlled_sleep,
        )
    )
    await asyncio.wait_for(old_sleep_started.wait(), timeout=1)

    await store.finalize("u-finalize-wakeup")

    await asyncio.wait_for(old_sleep_cancelled.wait(), timeout=1)
    assert await asyncio.wait_for(waiter, timeout=1) is False
```

- [x] **步骤 3：加厚父模块 wrapper 契约**

在 `test_parent_private_buffer_wrappers_remain_in_routes_and_patchable()` 中加入：

```python
    assert routes._wait_private_buffer_deadline.__module__ == "api.routes"
```

- [x] **步骤 4：加厚 route 级 shrink 测试**

在 `tests/test_api.py::test_private_buffer_text_after_files_shrinks_window_to_five_seconds`
中把第一段 fake sleep 的手动释放改成由 deadline change 主动取消旧 sleep。红灯阶段只需要加入断言：

```python
    old_sleep_cancelled = asyncio.Event()
```

并在 `fake_sleep()` 的第一段等待中捕获取消：

```python
        try:
            await release_first_sleep.wait()
        except asyncio.CancelledError:
            old_sleep_cancelled.set()
            raise
```

在 follower append 后断言：

```python
    await asyncio.wait_for(old_sleep_cancelled.wait(), timeout=1)
```

红灯阶段保留 `release_first_sleep.set()` 之后的旧测试收尾，保证当前实现会因为没有主动取消旧 sleep 而失败。

- [x] **步骤 5：运行红灯测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_private_buffer_split.py -v
```

预期：失败原因是 `PrivateBufferStore.wait_until_deadline` 和父模块 `_wait_private_buffer_deadline` 不存在。

- [x] **步骤 6：运行 route 红灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api.py::test_private_buffer_text_after_files_shrinks_window_to_five_seconds \
  -v
```

预期：当前实现不会主动取消旧 sleep，测试失败。

- [x] **步骤 7：提交红灯测试**

运行：

```bash
git add tests/test_api_chat_private_buffer_split.py tests/test_api.py .Codex/plans/api-chat-private-buffer-deadline-wakeup.md
git diff --cached --check
git commit -m "test(普通API): 锁定私聊缓冲唤醒契约"
```

---

## 任务 2：Store 实现

**文件：**
- 修改：`api/chat_private_buffer.py`
- 修改：`.Codex/plans/api-chat-private-buffer-deadline-wakeup.md`

- [x] **步骤 1：补类型导入**

将 `api/chat_private_buffer.py` 的导入改为：

```python
from collections.abc import Awaitable, Callable, Sequence
```

- [x] **步骤 2：owner 创建 deadline signal**

在 owner 创建 buffer dict 时加入：

```python
"deadline_changed": asyncio.Event(),
```

- [x] **步骤 3：follower append 唤醒 owner**

在 follower 更新 deadline 后加入：

```python
changed = buf.get("deadline_changed")
if isinstance(changed, asyncio.Event):
    changed.set()
```

- [x] **步骤 4：新增 wait_until_deadline**

在 `PrivateBufferStore.deadline()` 后加入：

```python
    async def wait_until_deadline(
        self,
        user_id: str,
        *,
        now: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> bool:
        while True:
            async with self._lock:
                buf = self.buffers.get(user_id)
                if buf is None or buf["done"].is_set():
                    return False
                deadline = float(buf["deadline"])
                remaining = deadline - now()
                if remaining <= 0:
                    return True
                changed = buf.get("deadline_changed")
                if not isinstance(changed, asyncio.Event):
                    changed = asyncio.Event()
                    buf["deadline_changed"] = changed

            sleep_task = asyncio.create_task(sleep(remaining))
            changed_task = asyncio.create_task(changed.wait())
            try:
                done, pending = await asyncio.wait(
                    {sleep_task, changed_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                if sleep_task in done:
                    sleep_task.result()
                if changed_task in done:
                    changed_task.result()
                    async with self._lock:
                        buf = self.buffers.get(user_id)
                        if buf is not None and buf.get("deadline_changed") is changed:
                            buf["deadline_changed"] = asyncio.Event()
            finally:
                for task in (sleep_task, changed_task):
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
```

- [x] **步骤 5：finalize 唤醒 deadline waiter**

在 `finalize()` 中 `done.set()` 前后加入：

```python
changed = buf.get("deadline_changed")
if isinstance(changed, asyncio.Event):
    changed.set()
```

- [x] **步骤 6：运行 store 绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_private_buffer_split.py -v
```

预期：只剩父模块 `_wait_private_buffer_deadline` wrapper 相关断言失败；store 新增测试通过。

- [x] **步骤 7：提交 Store 实现**

运行：

```bash
git add api/chat_private_buffer.py .Codex/plans/api-chat-private-buffer-deadline-wakeup.md
git diff --cached --check
git commit -m "fix(普通API): 增加私聊缓冲唤醒"
```

---

## 任务 3：父模块接入

**文件：**
- 修改：`api/routes.py`
- 修改：`tests/test_api.py`
- 修改：`.Codex/plans/api-chat-private-buffer-deadline-wakeup.md`

- [x] **步骤 1：增加父模块 wrapper**

在 `_private_buffer_window_seconds()` 后加入：

```python
async def _wait_private_buffer_deadline(user_id: str) -> bool:
    return await _private_buffer_store.wait_until_deadline(
        user_id,
        now=_time.time,
        sleep=asyncio.sleep,
    )
```

- [x] **步骤 2：替换 owner 手写 loop**

将 `proxy_chat()` 中 owner 等待 deadline 的 `while True` 手写 loop 替换为：

```python
            if not await _wait_private_buffer_deadline(req.user_id):
                return _chat_response_payload(
                    req,
                    status="silent",
                    reason="private_buffer_missing",
                    include_answer_chunks=True,
                )
```

- [x] **步骤 3：收紧 route 级红灯测试**

如果任务 1 为了让当前实现完成测试收尾保留了 `release_first_sleep.set()`，在接入后删除对旧 sleep 的手动释放依赖，保留 `old_sleep_cancelled` 断言，让测试只依赖 deadline change 主动唤醒。

- [x] **步骤 4：运行私聊缓冲组合绿灯**

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

- [x] **步骤 5：运行断连与 asyncio 策略回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_asyncio_run_policy.py \
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
git add api/routes.py tests/test_api.py .Codex/plans/api-chat-private-buffer-deadline-wakeup.md
git diff --cached --check
git commit -m "fix(普通API): 接入私聊缓冲唤醒"
```

---

## 任务 4：文档收口与全量验证

**文件：**
- 修改：`.Codex/plans/api-chat-private-buffer-deadline-wakeup.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：全部通过，跳过数量和警告数量记录到本计划与 `docs/plan_walkthrough.md`。

- [x] **步骤 2：更新 P3 进度**

在 `docs/todo.md` 的 P3 超大文件拆分条目中追加 `api/routes.py` 第十九刀进展，说明：

- `api/chat_private_buffer.py` 增加 deadline change signal。
- owner deadline wait 改为 `PrivateBufferStore.wait_until_deadline()`。
- follower 缩短 deadline 后会主动唤醒 owner。
- finalize 会唤醒 deadline waiter。
- `/chat` 路由本体、PrivateTimingGate、guardrail、Bridge、落库、SSE、push 和 response envelope 均保持父模块边界。
- 全量回归结果。

- [x] **步骤 3：更新 walkthrough**

在 `docs/plan_walkthrough.md` 追加 `2026-06-23 普通 API 私聊缓冲 Deadline 主动唤醒` 章节，记录：

- 设计文档路径。
- 实现计划路径。
- 阶段提交列表。
- 计划列表全部勾选。
- 验证记录。
- 执行约束。
- 下一步候选。

- [x] **步骤 4：运行文档自检**

运行：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-private-buffer-deadline-wakeup.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-chat-private-buffer-deadline-wakeup.md docs/todo.md docs/plan_walkthrough.md
```

预期：扫描无输出，diff 检查无输出。

- [x] **步骤 5：提交文档收口**

运行：

```bash
git add .Codex/plans/api-chat-private-buffer-deadline-wakeup.md docs/todo.md docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口私聊缓冲唤醒"
```

---

## 验证记录

执行过程中把每条命令、退出状态和结果摘要记录在这里。

- 2026-06-23 任务 1 红灯测试：
  `python -B -m pytest -p no:cacheprovider tests/test_api_chat_private_buffer_split.py -v`
  退出码 1，7 项收集，4 passed / 3 failed；失败均为预期红灯：
  `api.routes._wait_private_buffer_deadline` 缺失、
  `PrivateBufferStore.wait_until_deadline` 缺失。
- 2026-06-23 任务 1 route 红灯：
  `python -B -m pytest -p no:cacheprovider tests/test_api.py::test_private_buffer_text_after_files_shrinks_window_to_five_seconds -v`
  首次运行暴露测试补丁变量位置错误，修正后重跑退出码 1；
  失败原因为 `old_sleep_cancelled.wait()` 超时，证明当前实现不会在 follower
  缩短 deadline 后主动取消旧 sleep。
- 2026-06-23 任务 2 store 绿灯：
  `python -B -m pytest -p no:cacheprovider tests/test_api_chat_private_buffer_split.py -v`
  退出码 1，7 项收集，6 passed / 1 failed；新增 store 级 deadline shrink
  与 finalize 唤醒测试均通过，唯一失败为预期中的
  `api.routes._wait_private_buffer_deadline` 尚未接入。
- 2026-06-23 任务 3 私聊缓冲组合绿灯：
  `python -B -m pytest -p no:cacheprovider tests/test_api_chat_private_buffer_split.py tests/test_api.py::test_private_buffer_silent_releases_waiters tests/test_api.py::test_private_buffer_refreshes_window_and_persists_merged_messages tests/test_api.py::test_private_buffer_merges_files_for_final_bridge_request tests/test_api.py::test_private_buffer_text_after_files_shrinks_window_to_five_seconds tests/test_api.py::test_private_buffer_owner_cancel_releases_waiters_and_cleans_buffer tests/test_api.py::test_private_buffer_bridge_cancel_releases_waiters_and_cleans_buffer -v`
  退出码 0，13 passed / 1 warning。
- 2026-06-23 任务 3 asyncio 与断连流式回归：
  `python -B -m pytest -p no:cacheprovider tests/test_asyncio_run_policy.py tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send -v`
  退出码 0，7 passed / 1 warning。
- 2026-06-23 任务 3 行数：
  `wc -l api/routes.py api/chat_private_buffer.py tests/test_api_chat_private_buffer_split.py`
  输出为 `1333 api/routes.py`、`198 api/chat_private_buffer.py`、
  `311 tests/test_api_chat_private_buffer_split.py`。
- 2026-06-23 任务 4 全量验证：
  `python -B -m pytest -p no:cacheprovider tests/ -v`
  退出码 0，`1734 passed, 6 skipped, 139 warnings in 124.18s`。
- 2026-06-23 任务 4 文档自检：
  `rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-private-buffer-deadline-wakeup.md docs/todo.md docs/plan_walkthrough.md`
  无输出，退出码 1，表示未命中文档缺陷模式。
  `git diff --check -- .Codex/plans/api-chat-private-buffer-deadline-wakeup.md docs/todo.md docs/plan_walkthrough.md`
  无输出，退出码 0。
