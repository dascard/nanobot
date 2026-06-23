# 普通 API 私聊缓冲 Deadline 主动唤醒设计

日期：2026-06-23

## 背景

`docs/todo.md` 的 P3 超大文件拆分队列当前只剩普通 `api/routes.py`，文件为 1331 行。第十七刀已经把私聊缓冲基础件拆到 `api/chat_private_buffer.py`，但刻意保留了 owner deadline sleep、guardrail provider、Bridge 调用、聊天落库、SSE、push envelope 和 response envelope 在 `api.routes` 内。第十八刀已经把断连后台 push envelope 组装和传输层图片展开拆到 `api/chat_push_envelope.py`。

本阶段不迁移完整私聊 flow。只修复并抽小边界：私聊缓冲 owner 等待 deadline 时，如果 follower 消息把 deadline 缩短，owner 仍在旧的 `asyncio.sleep(remaining)` 中，不会主动醒来重新读取新 deadline。

## 根因

当前 `PrivateBufferStore.begin_or_append()` 在 owner 创建时写入：

- `deadline = now + window_seconds`
- `window_seconds = 10`（带文件）或 `5`（纯文本）

follower append 时会重新计算当前消息窗口，并直接覆盖：

- `buf["window_seconds"] = window_seconds`
- `buf["deadline"] = now + window_seconds`

`api.routes.proxy_chat()` 中 owner 负责等待静默窗口：

1. 每轮调用 `_private_buffer_store.deadline(req.user_id)`。
2. 计算 `remaining = deadline - _time.time()`。
3. 若 `remaining > 0`，直接 `await asyncio.sleep(remaining)`。

问题发生在 deadline 缩短场景：

1. `t=0` 第一条带文件，deadline 为 `t=10`，owner 开始 sleep 10 秒。
2. `t=3` follower 追加纯文本，store 把 deadline 缩短为 `t=8`。
3. owner 已经进入旧 sleep，没有任何 deadline change signal。
4. 实际到 `t=10` 才醒来，回复比当前静默窗口晚 2 秒。

现有 `test_private_buffer_text_after_files_shrinks_window_to_five_seconds` 只断言 `_private_buffers["deadline"] == 8.0`，并用测试事件人为释放第一段 sleep；它没有证明生产路径会在 deadline 缩短时主动醒来。

## 方案比较

### 方案 A：私聊缓冲 deadline wakeup 小刀（采用）

在 `api/chat_private_buffer.py` 内为每个 buffer 增加 deadline change signal。follower append 覆盖 deadline 后触发 signal；owner 等待时不再单纯 sleep，而是等待“deadline 到期或 deadline 变更”。owner 被唤醒后重新读取最新 deadline。

优点：

- 修复真实时序 bug。
- 继续服务 P3 超大文件拆分，减少 `api.routes` owner wait 细节。
- 为后续私聊 flow 拆分打基础。
- 不触碰 Bridge、guardrail、落库、SSE、push 或 response envelope。

代价：

- 需要修改异步等待语义，必须用 TDD 覆盖 deadline shrink 和 finalize wakeup。
- 需要保持 `asyncio.sleep` 与 `_time.time` 仍可在父模块测试中 monkeypatch。

### 方案 B：streaming finalizer 小内核

新增 `api/chat_stream_finalizer.py`，抽 `_persist_stream_result_after_runner_done()` 的结果决策、落库 callback 和 push callback。

优点：

- 清理 `api.routes` 中另一个高耦合块。
- 风险可控，主要是纯状态和 callback 设计。

代价：

- 不能解决 deadline shrink 的真实延迟 bug。
- 容易混淆正常 SSE done 路径和断连 finalizer 路径，需要额外保护。

### 方案 C：完整私聊 flow 迁移

把 PrivateTimingGate、guardrail、owner/follower 等待、snapshot、Bridge 前准备和持久化请求组装整体迁出。

不采用。该方案跨越太多父模块 patch point，也会牵动 SSE / 非流式 finalize 分散路径；在 deadline wakeup 修复前迁移会把现有时序风险带进新模块。

## 设计

### 新增信号

在 buffer dict 中新增内部字段：

- `deadline_changed: asyncio.Event`

owner 创建 buffer 时初始化该 event。follower append 每次覆盖 deadline 后调用 `deadline_changed.set()`。

该字段属于 `PrivateBufferStore` 内部运行时状态，不进入 `PrivateBufferSnapshot`，也不暴露给 Bridge、guardrail 或落库层。

### 新增等待接口

在 `PrivateBufferStore` 增加：

```python
async def wait_until_deadline(
    self,
    user_id: str,
    *,
    now: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> bool:
    ...
```

返回值：

- `True`：当前最新 deadline 已到，owner 可以继续 snapshot。
- `False`：buffer 已不存在或已 finalize，父模块返回 `private_buffer_missing`。

行为：

1. 在锁内读取当前 buffer、deadline 和 `deadline_changed`。
2. 若 buffer 不存在或 `done` 已 set，返回 `False`。
3. 若 `deadline - now() <= 0`，返回 `True`。
4. 否则同时等待 `sleep(remaining)` 和 `deadline_changed.wait()`。
5. 若 deadline changed 先完成，清理该 event 并回到第 1 步，重新读取最新 deadline。
6. 若 sleep 先完成，回到第 1 步，避免假唤醒或时间源变化导致提前通过。
7. 如果等待期间被取消，向上传播 `CancelledError`，由 `api.routes` 现有 `except asyncio.CancelledError` finalize 处理。

实现约束：

- 不在持有 `_private_lock` 时 await `sleep()` 或 event wait。
- 不直接调用 `asyncio.run()`。
- 不引入 `run_awaitable_sync` 或同步函数包装 awaitable。
- `sleep` 参数由 `api.routes` 传入 `asyncio.sleep`，保持现有测试可 monkeypatch。
- `now` 参数由 `api.routes` 传入 `_time.time`，保持 fake clock 测试边界。

### finalize 唤醒

`PrivateBufferStore.finalize()` 设置 `done` 并清理窗口前，也要唤醒 deadline waiter：

- 如果 buffer 有 `deadline_changed` event，则 set 它。
- 这样 owner 不会卡在旧 sleep；follower 仍由既有 `done` event 释放。

### 父模块接入

`api.routes.proxy_chat()` 中 owner wait 从手写 loop：

```python
while True:
    deadline = await _private_buffer_store.deadline(req.user_id)
    ...
    await asyncio.sleep(remaining)
```

改为调用父模块 wrapper：

```python
if not await _wait_private_buffer_deadline(req.user_id):
    return _chat_response_payload(... reason="private_buffer_missing" ...)
```

新增父模块 wrapper：

```python
async def _wait_private_buffer_deadline(user_id: str) -> bool:
    return await _private_buffer_store.wait_until_deadline(
        user_id,
        now=_time.time,
        sleep=asyncio.sleep,
    )
```

该 wrapper 保持在 `api.routes`，为测试和未来迁移保留 patch point。

## 保留边界

本阶段保留在 `api.routes`：

- `/chat` 路由本体。
- `PrivateTimingGate` 分类和 casual / no_reply 快返。
- `get_guardrail()`、`_detect_guardrail()` 和 `_build_guardrail_input()`。
- owner / follower HTTP response 语义。
- `get_bridge()`、Bridge 调用和 Prompt Runtime 输入。
- `_persist_chat_turn()`、`_chat_response_payload()`、`_finalize_private_buffer()`。
- SSE、stream finalizer、push envelope、response envelope。
- `_private_buffers`、`_private_lock`、`_private_buffer_store` 实例和窗口常量。
- `_time.time` 与 `asyncio.sleep` 父模块 patch point。

本阶段不引入 generation id，不改变 `_finalize_private_buffer(user_id)` 的 user-level 语义。

## 测试策略

新增或修改测试：

1. `tests/test_api_chat_private_buffer_split.py`
   - 新增 `test_private_buffer_store_wakes_owner_when_deadline_shrinks`。
   - 使用 `PrivateBufferStore.wait_until_deadline()` 直接复现 owner 先等旧 deadline，follower 缩短 deadline 后主动唤醒。
   - 当前实现红灯：`PrivateBufferStore` 没有 `wait_until_deadline()`，也没有 deadline change signal。

2. `tests/test_api_chat_private_buffer_split.py`
   - 新增 `test_private_buffer_store_finalize_wakes_deadline_waiter`。
   - 证明 finalize 会唤醒 owner deadline waiter 并返回 `False`。

3. `tests/test_api.py`
   - 加厚 `test_private_buffer_text_after_files_shrinks_window_to_five_seconds`。
   - 断言 follower 缩短 deadline 后 owner 会进入新的 wait 周期，而不是只能靠测试手动释放旧 sleep。

4. 现有回归继续运行：
   - `tests/test_api_chat_private_buffer_split.py`
   - 私聊 buffer 行为测试。
   - 断连相邻回归。
   - `tests/test_asyncio_run_policy.py`

红灯预期：

- 新 store 测试因 `wait_until_deadline()` 缺失失败。
- 如果先只加空方法，deadline shrink 测试会因未被 follower 主动唤醒而超时或断言失败。

## 验证命令

红灯：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_private_buffer_split.py -v
```

接入绿灯：

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

异步策略与相邻回归：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_asyncio_run_policy.py \
  tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder \
  tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner \
  tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder \
  tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send \
  -v
```

最终全量：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

## 风险与缓解

- **风险：wait 方法吞掉取消。** 缓解：不捕获 `CancelledError`，让父模块现有 cancel finalize 路径处理。
- **风险：持锁等待导致 follower 无法 append。** 缓解：只在锁内读取 state，实际等待在锁外执行。
- **风险：event set 后重复 set 导致下一轮误唤醒。** 缓解：owner 观察到 change 后在锁内替换为新的 `asyncio.Event()`，再重新读 deadline。
- **风险：测试绕过生产行为。** 缓解：store 级测试直接验证 `wait_until_deadline()`，route 级测试验证父模块 wrapper 使用新等待语义。

## 下一步

设计完成后，写入 `.Codex/plans/api-chat-private-buffer-deadline-wakeup.md`。实现必须先写红灯测试，再实现 `PrivateBufferStore.wait_until_deadline()` 和父模块 wrapper。每个阶段单独提交。
