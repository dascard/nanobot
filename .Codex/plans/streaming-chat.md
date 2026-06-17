# 流式聊天重构实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 `/chat` 的 `stream` 参数从 API 贯穿到 bridge，并在 SSE 模式下输出增量文本事件。

**架构：** API 层把 `stream` 写入 `bridge_meta` 并显式传给 bridge；bridge/pool 签名接收并透传 `stream`；`BufferedOutput.write_stream()` 在启用 stream queue 时发送 `delta` 事件。最终 `done.answer` 仍是持久化和业务判断的权威结果。

**技术栈：** FastAPI `StreamingResponse`、asyncio Queue、pytest、KT `BufferedOutput`。

---

## 文件结构

- 修改：`api/routes.py`，负责把 `ChatProxyRequest.stream` 传入 bridge metadata 和 `handle_message()`。
- 修改：`nanobot_kt/bridge.py`，负责 `NanobotBridge` 与 `NanobotBridgePool` 的 `stream` 参数透传。
- 修改：`nanobot_kt/output.py`，负责把 KT provider 的文本 chunk 转换为 SSE `delta` 队列事件。
- 修改：`tests/test_api.py`，覆盖 API 到 bridge 的 `stream` 透传和 SSE `delta` 输出。
- 修改：`tests/test_kt_framework.py`，覆盖 bridge 参数透传和 `BufferedOutput` delta 事件。

### 任务 1：API 到 Bridge 的 Stream 透传

**文件：**
- 修改：`tests/test_api.py`
- 修改：`api/routes.py`

- [ ] **步骤 1：编写失败的 API 边界测试**

在 `tests/test_api.py` 的 `test_stream_chat_emits_progress_and_done_events` 附近新增：

```python
def test_stream_chat_passes_stream_flag_to_bridge(client):
    from unittest.mock import patch

    captured = {}

    async def fake_handle_message(*args, **kwargs):
        captured["metadata"] = kwargs.get("metadata") or {}
        captured["stream"] = kwargs.get("stream")
        captured["stream_queue"] = kwargs.get("stream_queue")
        return "最终答案"

    with patch("api.routes.get_bridge") as mock_get_bridge:
        mock_get_bridge.return_value.handle_message.side_effect = fake_handle_message
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_flag_user",
                "session_id": "group_1000",
                "query": "test",
                "stream": True,
            },
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert captured["stream"] is True
    assert captured["metadata"]["stream"] is True
    assert captured["stream_queue"] is not None
    assert '"status": "done"' in body
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_api.py::test_stream_chat_passes_stream_flag_to_bridge -q
```

预期：失败，原因是 fake bridge 捕获到的 `stream` 为 `None` 或 metadata 中没有 `stream`。

- [ ] **步骤 3：实现最小 API 透传**

在 `api/routes.py` 的 `bridge_meta` 中加入：

```python
"stream": bool(req.stream),
```

修改 `_do_chat()` 调用：

```python
stream=False,
```

修改 `_stream_chat()` runner 中的 bridge 调用：

```python
stream=True,
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
python -B -m pytest tests/test_api.py::test_stream_chat_passes_stream_flag_to_bridge -q
```

预期：`1 passed`。

- [ ] **步骤 5：Commit**

```bash
git add tests/test_api.py api/routes.py
git commit -m "feat(流式传输): 透传聊天 stream 参数"
```

### 任务 2：Bridge 与 Pool 支持 Stream 参数

**文件：**
- 修改：`tests/test_kt_framework.py`
- 修改：`nanobot_kt/bridge.py`

- [ ] **步骤 1：编写失败的 Pool 透传测试**

在 `tests/test_kt_framework.py` 的 `NanobotBridgePool` 相关测试附近新增：

```python
def test_bridge_pool_passes_stream_flag_to_child(monkeypatch):
    from nanobot_kt.bridge import NanobotBridgePool

    captured = {}

    class FakeBridge:
        async def start(self):
            return None

        async def stop(self):
            return None

        async def handle_message(self, query, **kwargs):
            captured.update(kwargs)
            return "ok"

    monkeypatch.setattr("nanobot_kt.bridge.NanobotBridge", lambda *_args, **_kwargs: FakeBridge())

    async def _run():
        pool = NanobotBridgePool()
        return await pool.handle_message(
            "你好",
            user_id="u1",
            session_id="private_u1",
            stream=True,
            stream_queue=asyncio.Queue(),
        )

    assert run_async(_run()) == "ok"
    assert captured["stream"] is True
    assert captured["stream_queue"] is not None
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_kt_framework.py::test_bridge_pool_passes_stream_flag_to_child -q
```

预期：失败，原因是 `NanobotBridgePool.handle_message()` 不接受 `stream` 参数，或没有传给 child bridge。

- [ ] **步骤 3：实现 Bridge 签名透传**

在 `NanobotBridge.handle_message()` 增加参数：

```python
stream: bool = False,
```

在函数内创建 metadata 副本后写入：

```python
meta = dict(metadata or {})
meta["stream"] = bool(stream or meta.get("stream"))
```

仅当 `stream_queue is not None and meta["stream"]` 时启用输出 stream：

```python
if stream_queue is not None and meta["stream"]:
    self._output.enable_stream(stream_queue)
else:
    self._output.disable_stream()
```

在 `NanobotBridgePool.handle_message()` 增加同名参数并传给 child bridge。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
python -B -m pytest tests/test_kt_framework.py::test_bridge_pool_passes_stream_flag_to_child -q
```

预期：`1 passed`。

- [ ] **步骤 5：Commit**

```bash
git add tests/test_kt_framework.py nanobot_kt/bridge.py
git commit -m "feat(流式传输): 支持 Bridge stream 参数"
```

### 任务 3：BufferedOutput 输出 Delta 事件

**文件：**
- 修改：`tests/test_kt_framework.py`
- 修改：`nanobot_kt/output.py`

- [ ] **步骤 1：编写失败的 Delta 测试**

在 `TestBufferedOutput` 中新增：

```python
def test_write_stream_emits_delta_event(self):
    from nanobot_kt.output import BufferedOutput

    async def _run():
        output = BufferedOutput()
        queue = asyncio.Queue()
        output.enable_stream(queue)
        await output.write_stream("你")
        await output.write_stream("好")
        first = await asyncio.wait_for(queue.get(), timeout=1)
        second = await asyncio.wait_for(queue.get(), timeout=1)
        return output.get_response(), first, second

    response, first, second = run_async(_run())
    assert response == "你好"
    assert first == {"status": "delta", "text": "你"}
    assert second == {"status": "delta", "text": "好"}
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_kt_framework.py::TestBufferedOutput::test_write_stream_emits_delta_event -q
```

预期：失败，原因是 `write_stream()` 没有向 queue 写事件。

- [ ] **步骤 3：实现 Delta 输出**

在 `BufferedOutput.write_stream()` 中保留 buffer 累积，并新增：

```python
if chunk and self._stream_queue is not None:
    await self._stream_queue.put({"status": "delta", "text": chunk})
```

同步把 `on_activity()` 中的 `asyncio.ensure_future(...)` 改为受控 helper：

```python
def _schedule_stream_event(self, event: dict[str, Any]) -> None:
    if self._stream_queue is None:
        return
    task = asyncio.create_task(self._stream_queue.put(event))
    self._stream_tasks.add(task)
    task.add_done_callback(self._discard_stream_task)
```

`_discard_stream_task()` 读取异常并记录日志，避免未消费异常。

- [ ] **步骤 4：运行输出层测试**

运行：

```bash
python -B -m pytest tests/test_kt_framework.py::TestBufferedOutput -q
```

预期：`TestBufferedOutput` 全部通过。

- [ ] **步骤 5：Commit**

```bash
git add tests/test_kt_framework.py nanobot_kt/output.py
git commit -m "feat(流式传输): 输出模型增量事件"
```

### 任务 4：SSE Delta 集成回归

**文件：**
- 修改：`tests/test_api.py`
- 修改：`api/routes.py`

- [ ] **步骤 1：扩展 SSE 测试**

更新 `test_stream_chat_emits_progress_and_done_events` 的 fake bridge，让它写入 delta：

```python
await queue.put({"status": "delta", "text": "增量"})
```

新增断言：

```python
assert {"status": "delta", "text": "增量"} in events
```

- [ ] **步骤 2：运行测试验证失败或确认已覆盖**

运行：

```bash
python -B -m pytest tests/test_api.py::test_stream_chat_emits_progress_and_done_events -q
```

预期：如果任务 1 到 3 已完成，测试应通过；如果 API generator 过滤了未知事件，会失败。

- [ ] **步骤 3：实现必要的 SSE 事件透传**

如果测试失败，确认 `_stream_chat()` 对 queue 事件仍直接：

```python
yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
```

不要改 `done` 的最终完整答案语义。

- [ ] **步骤 4：运行流式相关测试**

运行：

```bash
python -B -m pytest \
  tests/test_api.py::test_stream_chat_emits_progress_and_done_events \
  tests/test_api.py::test_stream_chat_passes_stream_flag_to_bridge \
  tests/test_kt_framework.py::TestBufferedOutput \
  tests/test_kt_framework.py::test_bridge_pool_passes_stream_flag_to_child \
  -q
```

预期：全部通过。

- [ ] **步骤 5：Commit**

```bash
git add tests/test_api.py api/routes.py
git commit -m "test(流式传输): 覆盖 SSE 增量事件"
```

### 任务 5：最终验证

**文件：**
- 检查：`api/routes.py`
- 检查：`nanobot_kt/bridge.py`
- 检查：`nanobot_kt/output.py`
- 检查：`tests/test_api.py`
- 检查：`tests/test_kt_framework.py`

- [ ] **步骤 1：运行相关测试**

```bash
python -B -m pytest tests/test_api.py tests/test_kt_framework.py -q
```

预期：全部通过。

- [ ] **步骤 2：运行静态搜索**

```bash
rg -n "stream=True|stream=False|metadata\\[\"stream\"\\]|\"status\": \"delta\"" api/routes.py nanobot_kt/bridge.py nanobot_kt/output.py tests/test_api.py tests/test_kt_framework.py
```

预期：能看到 API、bridge、output 和测试中的贯穿点。

- [ ] **步骤 3：运行完整测试**

```bash
python -B -m pytest tests/ -q --durations=20
```

预期：0 failures。若失败来自已有未提交工作区改动，记录失败测试名和错误摘要，不把它归因于流式重构。

- [ ] **步骤 4：检查提交范围**

```bash
git status --short
git diff --stat HEAD
```

预期：流式重构只涉及计划列出的文件；`webui/dist`、`nanobot.db`、`__pycache__` 不进入提交。

### 任务 6：Message 携带 Stream 内部字段

**文件：**
- 修改：`tests/test_streaming_bridge.py`
- 修改：`vendor/KohakuTerrarium/src/kohakuterrarium/llm/message.py`
- 修改：`vendor/KohakuTerrarium/src/kohakuterrarium/core/controller.py`
- 修改：`nanobot_kt/kt_adapter.py`
- 修改：`nanobot_kt/bridge.py`

- [ ] **步骤 1：编写失败的 Message 字段测试**

在 `tests/test_streaming_bridge.py` 中新增测试，证明 `Message` 对象保留 `stream=True`，但 `to_dict()` 不输出该字段：

```python
def test_message_stream_flag_is_internal_not_wire():
    from kohakuterrarium.llm.message import Message, UserMessage

    msg = UserMessage("你好", stream=True)
    assert msg.stream is True
    assert "stream" not in msg.to_dict()

    restored = Message.from_dict({"role": "user", "content": "你好", "stream": True})
    assert restored.stream is True
    assert "stream" not in restored.to_dict()
```

- [ ] **步骤 2：编写失败的 Controller 传递测试**

继续在 `tests/test_streaming_bridge.py` 中新增测试，证明事件上下文会写到用户 Message，同时 LLM wire messages 不含 `stream`：

```python
@pytest.mark.asyncio
async def test_controller_user_message_carries_stream_without_wire_leak():
    from kohakuterrarium.core.controller import Controller, ControllerConfig
    from kohakuterrarium.core.events import create_user_input_event

    class FakeLLM:
        provider_name = "fake"
        last_tool_calls = []
        last_assistant_extra_fields = {}
        last_assistant_content_parts = None

        def __init__(self):
            self.seen_messages = None

        async def chat(self, messages, **_kwargs):
            self.seen_messages = messages
            yield "ok"

    llm = FakeLLM()
    controller = Controller(llm, ControllerConfig(include_job_status=False))
    await controller.push_event(create_user_input_event("你好", stream=True))

    async for _event in controller.run_once():
        pass

    user_msg = controller.conversation.get_messages()[0]
    assert user_msg.stream is True
    assert llm.seen_messages[0] == {"role": "user", "content": "你好"}
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```bash
python -B -m pytest \
  tests/test_streaming_bridge.py::test_message_stream_flag_is_internal_not_wire \
  tests/test_streaming_bridge.py::test_controller_user_message_carries_stream_without_wire_leak \
  -q
```

预期：失败，原因是 `UserMessage` 不接受 `stream` 或 controller append 后的 Message 没有 stream 标记。

- [ ] **步骤 4：实现 Message 与事件传递**

在 `Message` 数据类增加：

```python
stream: bool = False
```

`Message.from_dict()` 读取并剔除内部字段：

```python
stream = bool(data.get("stream", False))
extras = {k: v for k, v in data.items() if k not in _STANDARD_MESSAGE_KEYS and k != "stream"}
```

Controller 在 append 用户消息时设置：

```python
stream=any(bool(e.context.get("stream")) for e in events)
```

`nanobot_kt.kt_adapter.create_user_event()` 接收 `**context` 并透传给 `create_user_input_event()`；`NanobotBridge.handle_message()` 所有聊天用户事件使用 `create_user_event(event_content, stream=meta["stream"])`。

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
python -B -m pytest \
  tests/test_streaming_bridge.py::test_message_stream_flag_is_internal_not_wire \
  tests/test_streaming_bridge.py::test_controller_user_message_carries_stream_without_wire_leak \
  -q
```

预期：`2 passed`。

- [ ] **步骤 6：运行流式回归测试并 Commit**

运行：

```bash
python -B -m pytest \
  tests/test_streaming_api.py \
  tests/test_streaming_bridge.py \
  tests/test_streaming_output.py \
  tests/test_api.py::test_stream_chat_passes_stream_flag_to_bridge \
  tests/test_api.py::test_stream_chat_emits_progress_and_done_events \
  -q
```

预期：全部通过。

提交：

```bash
git add tests/test_streaming_bridge.py \
  vendor/KohakuTerrarium/src/kohakuterrarium/llm/message.py \
  vendor/KohakuTerrarium/src/kohakuterrarium/core/controller.py \
  nanobot_kt/kt_adapter.py \
  nanobot_kt/bridge.py
git commit -m "feat(流式传输): 让 Message 携带 stream 标记"
```
