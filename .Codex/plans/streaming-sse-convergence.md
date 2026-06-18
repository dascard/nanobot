# P3-1 SSE 流式收敛实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 收敛 `/chat` SSE 增量事件契约，减少 provider 小 chunk 对前端的影响，并让多工具回合能明确回到最终权威回复。

**架构：** 第一阶段在 `/chat` API 边界做事件 adapter 和连续 delta 合并，不碰 KT provider。第二阶段由 Bridge / `BufferedOutput` 发送 `final.replace` 收敛事件，API 层只负责规范化和透传。第三阶段把队列上限和 backpressure 策略固化，避免慢消费者导致内存无限增长。

**技术栈：** Python 3.12、FastAPI、Starlette `StreamingResponse`、asyncio、KT controller、pytest、in-memory SQLite。

---

## 当前状态

- [x] 流式主链路已落地：`ChatProxyRequest.stream` → BridgePool → Bridge → KT `Message` → `BufferedOutput.write_stream()`。
- [x] `/chat-step` 已支持 SSE 增量输出和流式 tool call 拼合。
- [x] SSE done 已接入标准响应信封，保留 `answer`，并新增 `reply`、`messages`、`reply_meta`、`meta`。
- [x] 第一阶段设计文档已提交：`bca50b8 docs(流式): 设计 SSE 收敛方案`。
- [x] 实现计划已提交：`e56a406 docs(计划): 记录 SSE 收敛计划`。
- [x] 任务 1 至任务 5 已完成，提交为 `d8e8703`、`84cb0cb`、`a987d31`、`88268a1`、`a5f705a`。
- [x] 任务 6 文档收口已完成，任务 7 最终验证待执行。

## 关键事实

- 设计文档：`docs/superpowers/specs/2026-06-18-streaming-sse-convergence-design.md`。
- 旧设计文档：`docs/superpowers/specs/2026-06-17-streaming-chat-design.md`。
- `/chat` SSE framing 保持 `data: <json>\n\n`，不改为命名 `event:`。
- `/chat` delta 字段是 `text`；`/chat-step` final-answer delta 字段是 `content`。本计划不强行统一两个接口。
- `/chat` 的 `done.answer` / `done.reply` 是权威结果；`delta.text` 只用于草稿预览。
- 增量事件不展开图片 token，不发送 base64。最终 `done.answer` 继续使用 `allow_base64=False`。
- 当前工作区有无关脏文件，包括 pycache、`docs/goal.md`、`tests/conftest.py`、`.agents/`、`.codex/`、历史待办清单、`nanobot.db` 等。执行计划时不得回滚、删除或暂存这些文件。

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `api/routes.py` | `/chat` SSE 消费队列、事件规范化、连续 delta 合并、bounded queue 常量 |
| `tests/test_streaming_api.py` | `/chat` SSE 事件顺序、delta 合并、final 透传和 queue 上限回归 |
| `tests/test_streaming_response_envelope.py` | 保护 `done` 信封和最终权威语义 |
| `tests/test_agent_step_api.py` | 保护 `/chat-step` 仍使用 `delta.content` |
| `nanobot_kt/output.py` | `BufferedOutput` 的 final/replace 事件方法和进度事件队列策略 |
| `nanobot_kt/bridge.py` | Bridge 在最终 response 确定后发送 final/replace 收敛事件 |
| `tests/test_streaming_output.py` | `BufferedOutput` 流式事件单元回归 |
| `tests/test_streaming_bridge.py` | Bridge 真实流式链路事件顺序回归 |
| `docs/message-field-standard.md` | 记录 `/chat` SSE 事件权威性、`final.replace` 和 `/chat-step` 差异 |
| `docs/todo.md` | 更新路线项 6 进度 |
| `docs/plan_walkthrough.md` | 记录 P3-1 阶段状态、验证命令和提交号 |

## 并行执行策略

默认采用主线程顺序执行，每个任务完成后验证并提交。需要提速时可按以下 owner 拆给子 agent，但主线程负责审查 diff、运行验证和提交。

| 角色 | 可修改文件 | 禁止修改 |
| --- | --- | --- |
| API owner | `api/routes.py`、`tests/test_streaming_api.py`、`tests/test_streaming_response_envelope.py`、`tests/test_agent_step_api.py` | `nanobot_kt/output.py`、`nanobot_kt/bridge.py` |
| Bridge owner | `nanobot_kt/output.py`、`nanobot_kt/bridge.py`、`tests/test_streaming_output.py`、`tests/test_streaming_bridge.py` | `api/routes.py`、文档 |
| 文档 owner | `docs/message-field-standard.md`、`docs/todo.md`、`docs/plan_walkthrough.md`、本计划 | 生产代码 |

子 agent 提示词：

```markdown
你只负责本任务列出的文件。不得修改未列入的文件。
先写红灯测试并运行指定命令，确认失败原因与计划一致。
再写最小实现，运行定向测试和任务指定回归。
不要暂存或提交无关脏文件。
返回：红灯输出摘要、绿灯输出摘要、改动文件列表、建议 commit message、仍需主线程处理的集成点。
```

## 任务 1：`/chat` API delta adapter 与连续合并

**文件：**
- 修改：`tests/test_streaming_api.py`
- 修改：`api/routes.py`

- [x] **步骤 1：把现有 delta 转发测试改成合并红灯**

修改 `tests/test_streaming_api.py::test_stream_chat_forwards_delta_events` 的断言：

```python
    delta_events = [item for item in events if item.get("status") == "delta"]
    assert delta_events == [{"status": "delta", "text": "你好"}]
```

删除旧断言：

```python
    assert {"status": "delta", "text": "你"} in events
    assert {"status": "delta", "text": "好"} in events
```

- [x] **步骤 2：新增 progress 打断合并红灯测试**

在 `tests/test_streaming_api.py` 增加：

```python
def test_stream_chat_flushes_delta_before_progress(client):
    from unittest.mock import patch

    async def fake_handle_message(*args, **kwargs):
        queue = kwargs.get("stream_queue")
        assert queue is not None
        await queue.put({"status": "delta", "text": "你"})
        await queue.put({"status": "delta", "text": "好"})
        await queue.put({"status": "progress", "text": "正在调用工具"})
        await queue.put({"status": "delta", "text": "！"})
        return "你好！"

    with patch("api.routes.get_bridge") as mock_get_bridge:
        mock_get_bridge.return_value.handle_message.side_effect = fake_handle_message
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_progress_break_user",
                "session_id": "group_1000",
                "query": "test",
                "stream": True,
            },
        ) as response:
            body = "".join(response.iter_text())

    events = [
        json.loads(chunk[6:])
        for chunk in body.split("\n\n")
        if chunk.startswith("data: ")
    ]

    assert response.status_code == 200
    assert [
        (event.get("status"), event.get("text") or event.get("answer"))
        for event in events
        if event.get("status") in {"delta", "progress", "done"}
    ] == [
        ("delta", "你好"),
        ("progress", "正在调用工具"),
        ("delta", "！"),
        ("done", "你好！"),
    ]
```

- [x] **步骤 3：新增 done 前 flush 红灯测试**

在 `tests/test_streaming_api.py` 增加：

```python
def test_stream_chat_flushes_pending_delta_before_done(client):
    from unittest.mock import patch

    async def fake_handle_message(*args, **kwargs):
        queue = kwargs.get("stream_queue")
        assert queue is not None
        await queue.put({"status": "delta", "text": "最"})
        await queue.put({"status": "delta", "text": "后"})
        return "最后答案"

    with patch("api.routes.get_bridge") as mock_get_bridge:
        mock_get_bridge.return_value.handle_message.side_effect = fake_handle_message
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_flush_before_done_user",
                "session_id": "group_1000",
                "query": "test",
                "stream": True,
            },
        ) as response:
            body = "".join(response.iter_text())

    events = [
        json.loads(chunk[6:])
        for chunk in body.split("\n\n")
        if chunk.startswith("data: ")
    ]
    statuses = [event.get("status") for event in events]

    assert response.status_code == 200
    assert events[statuses.index("delta")] == {"status": "delta", "text": "最后"}
    assert statuses.index("delta") < statuses.index("done")
    assert events[statuses.index("done")]["answer"] == "最后答案"
```

- [x] **步骤 4：运行红灯测试**

运行：

```bash
python -m pytest tests/test_streaming_api.py -v
```

预期：至少 `test_stream_chat_forwards_delta_events` 失败，因为当前实现仍发送 `{"text": "你"}` 和 `{"text": "好"}` 两个独立 delta。

- [x] **步骤 5：在 `api/routes.py` 增加事件规范化 helper**

在 `SAFE_STREAM_ERROR_MESSAGE` 附近或 `_stream_chat()` 之前增加：

```python
def _normalize_chat_stream_event(event: Any) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    status = str(event.get("status") or "")
    if status == "delta":
        text = event.get("text", "")
        if text is None:
            text = ""
        text = str(text)
        if not text:
            return None
        normalized = dict(event)
        normalized["status"] = "delta"
        normalized["text"] = text
        return normalized
    if status:
        normalized = dict(event)
        normalized["status"] = status
        return normalized
    return None
```

- [x] **步骤 6：在 `_stream_chat()` 中合并连续 delta**

在 `_stream_chat()` 内增加局部 pending buffer：

```python
        pending_delta_parts: list[str] = []

        async def _emit_sse(event: dict[str, Any]):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        async def _flush_pending_delta():
            if not pending_delta_parts:
                return
            text = "".join(pending_delta_parts)
            pending_delta_parts.clear()
            async for chunk in _emit_sse({"status": "delta", "text": text}):
                yield chunk

        async def _handle_queue_event(raw_event: Any):
            event = _normalize_chat_stream_event(raw_event)
            if event is None:
                return
            if event.get("status") == "delta":
                pending_delta_parts.append(str(event.get("text") or ""))
                while True:
                    try:
                        next_raw = stream_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    next_event = _normalize_chat_stream_event(next_raw)
                    if next_event is None:
                        continue
                    if next_event.get("status") == "delta":
                        pending_delta_parts.append(str(next_event.get("text") or ""))
                        continue
                    async for chunk in _flush_pending_delta():
                        yield chunk
                    async for chunk in _emit_sse(next_event):
                        yield chunk
                async for chunk in _flush_pending_delta():
                    yield chunk
                return

            async for chunk in _flush_pending_delta():
                yield chunk
            async for chunk in _emit_sse(event):
                yield chunk
```

把原来的直接透传：

```python
                        event = get_task.result()
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
```

替换为：

```python
                        async for chunk in _handle_queue_event(get_task.result()):
                            yield chunk
```

把 drain 队列时的直接透传也替换为：

```python
                async for chunk in _handle_queue_event(event):
                    yield chunk
            async for chunk in _flush_pending_delta():
                yield chunk
```

- [x] **步骤 7：运行任务 1 绿灯测试**

运行：

```bash
python -m pytest tests/test_streaming_api.py -v
```

预期：`3 passed` 或更多，取决于文件内测试数量；失败数为 0。

- [x] **步骤 8：运行 API 相关回归**

运行：

```bash
python -m pytest tests/test_streaming_api.py tests/test_streaming_response_envelope.py tests/test_chat_response_envelope.py -v
```

预期：全部通过，失败数为 0。

- [x] **步骤 9：提交任务 1**

运行：

```bash
git add api/routes.py tests/test_streaming_api.py
git commit -m "refactor(流式): 合并聊天增量事件"
```

## 任务 2：固化 `done` 权威与 `/chat-step` 字段差异

**文件：**
- 修改：`tests/test_streaming_response_envelope.py`
- 修改：`tests/test_agent_step_api.py`

- [x] **步骤 1：新增草稿 delta 与最终 done 不一致的保护测试**

在 `tests/test_streaming_response_envelope.py` 增加：

```python
def test_stream_chat_done_answer_remains_authoritative_when_delta_differs(client, monkeypatch):
    _fast_private_reply(monkeypatch)

    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            queue = kwargs.get("stream_queue")
            assert queue is not None
            await queue.put({"status": "delta", "text": "草稿"})
            return "最终回复"

        def pop_last_reply_meta(self, session_id):
            return {"send_mode": "normal"}

    with patch("api.routes.get_bridge", return_value=FakeBridge()):
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_done_authority_user",
                "session_id": "private_stream_done_authority_user",
                "query": "流式权威",
                "stream": True,
                "client_meta": {"platform": "web"},
            },
        ) as response:
            body = "".join(response.iter_text())

    events = [
        json.loads(chunk[6:])
        for chunk in body.split("\n\n")
        if chunk.startswith("data: ")
    ]
    delta_event = next(item for item in events if item.get("status") == "delta")
    done_event = next(item for item in events if item.get("status") == "done")

    assert response.status_code == 200
    assert delta_event["text"] == "草稿"
    assert done_event["answer"] == "最终回复"
    assert done_event["reply"] == "最终回复"
    assert done_event["messages"] == [{"type": "text", "text": "最终回复"}]
```

- [x] **步骤 2：新增 `/chat-step` 字段保护测试**

如果 `tests/test_agent_step_api.py::test_chat_step_stream_emits_final_answer_deltas` 已断言 `content`，只补充显式反向断言：

```python
    delta_events = [event for event in events if event["status"] == "delta"]
    assert delta_events[0]["content"] == "Maximum_Load 占比"
    assert "text" not in delta_events[0]
```

- [x] **步骤 3：运行保护测试**

运行：

```bash
python -m pytest tests/test_streaming_response_envelope.py tests/test_agent_step_api.py::test_chat_step_stream_emits_final_answer_deltas -v
```

预期：全部通过，失败数为 0。这里允许没有红灯，因为任务目标是固化当前兼容行为。

- [x] **步骤 4：提交任务 2**

运行：

```bash
git add tests/test_streaming_response_envelope.py tests/test_agent_step_api.py
git commit -m "test(流式): 固化完成信封权威性"
```

## 任务 3：Bridge 输出 final/replace 收敛事件

**文件：**
- 修改：`tests/test_streaming_output.py`
- 修改：`tests/test_streaming_bridge.py`
- 修改：`nanobot_kt/output.py`
- 修改：`nanobot_kt/bridge.py`

- [x] **步骤 1：新增 `BufferedOutput.write_final()` 红灯测试**

在 `tests/test_streaming_output.py` 增加：

```python
@pytest.mark.asyncio
async def test_buffered_output_write_final_emits_replace_event_without_mutating_buffer():
    from nanobot_kt.output import BufferedOutput

    output = BufferedOutput()
    queue = asyncio.Queue()
    output.enable_stream(queue)

    await output.write_stream("草稿")
    await output.write_final("最终回复", replace=True, source="bridge")

    first = await asyncio.wait_for(queue.get(), timeout=1)
    second = await asyncio.wait_for(queue.get(), timeout=1)

    assert output.get_response() == "草稿"
    assert first == {"status": "delta", "text": "草稿"}
    assert second == {
        "status": "final",
        "text": "最终回复",
        "replace": True,
        "source": "bridge",
    }
```

- [x] **步骤 2：新增 Bridge 事件顺序红灯测试**

修改 `tests/test_streaming_bridge.py::test_bridge_handle_message_streams_controller_text_deltas` 的事件断言为：

```python
    assert events == [
        {"status": "delta", "text": "你"},
        {"status": "delta", "text": "好"},
        {"status": "final", "text": "你好", "replace": True, "source": "bridge"},
    ]
```

- [x] **步骤 3：运行红灯测试**

运行：

```bash
python -m pytest tests/test_streaming_output.py tests/test_streaming_bridge.py::test_bridge_handle_message_streams_controller_text_deltas -v
```

预期：`BufferedOutput` 测试因 `write_final` 不存在失败；Bridge 测试因缺少 `final` 事件失败。

- [x] **步骤 4：实现 `BufferedOutput.write_final()`**

在 `nanobot_kt/output.py` 的 `write_stream()` 下方增加：

```python
    async def write_final(
        self,
        text: str,
        *,
        replace: bool = True,
        source: str = "bridge",
    ) -> None:
        if not text or self._stream_queue is None:
            return
        await self._stream_queue.put(
            {
                "status": "final",
                "text": str(text),
                "replace": bool(replace),
                "source": str(source or "bridge"),
            }
        )
```

该方法不修改 `_buffer`。最终持久化仍由 Bridge 返回值和 API `done` 信封负责。

- [x] **步骤 5：Bridge 返回前发送 final**

在 `nanobot_kt/bridge.py` 的 `NanobotBridge.handle_message()` 中，找到最终 `response` 已确定且即将 `return response` 的位置，加入：

```python
            if stream and stream_queue is not None and response:
                writer = getattr(self._output, "write_final", None)
                if writer is not None:
                    await writer(str(response), replace=True, source="bridge")
```

确保这段逻辑在 error fallback 之外执行，不改变非流式路径。

- [x] **步骤 6：运行任务 3 绿灯测试**

运行：

```bash
python -m pytest tests/test_streaming_output.py tests/test_streaming_bridge.py -v
```

预期：全部通过，失败数为 0。

- [x] **步骤 7：提交任务 3**

运行：

```bash
git add nanobot_kt/output.py nanobot_kt/bridge.py tests/test_streaming_output.py tests/test_streaming_bridge.py
git commit -m "feat(流式): 发送最终收敛事件"
```

## 任务 4：API 规范化并透传 final 事件

**文件：**
- 修改：`tests/test_streaming_api.py`
- 修改：`api/routes.py`

- [x] **步骤 1：新增 final 透传测试**

在 `tests/test_streaming_api.py` 增加：

```python
def test_stream_chat_forwards_final_replace_before_done(client):
    from unittest.mock import patch

    async def fake_handle_message(*args, **kwargs):
        queue = kwargs.get("stream_queue")
        assert queue is not None
        await queue.put({"status": "delta", "text": "草稿"})
        await queue.put({"status": "final", "text": "最终", "replace": True, "source": "bridge"})
        return "最终"

    with patch("api.routes.get_bridge") as mock_get_bridge:
        mock_get_bridge.return_value.handle_message.side_effect = fake_handle_message
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_final_user",
                "session_id": "group_1000",
                "query": "test",
                "stream": True,
            },
        ) as response:
            body = "".join(response.iter_text())

    events = [
        json.loads(chunk[6:])
        for chunk in body.split("\n\n")
        if chunk.startswith("data: ")
    ]
    final_index = next(i for i, item in enumerate(events) if item.get("status") == "final")
    done_index = next(i for i, item in enumerate(events) if item.get("status") == "done")

    assert response.status_code == 200
    assert events[final_index] == {
        "status": "final",
        "text": "最终",
        "replace": True,
        "source": "bridge",
    }
    assert final_index < done_index
    assert events[done_index]["answer"] == "最终"
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
python -m pytest tests/test_streaming_api.py::test_stream_chat_forwards_final_replace_before_done -v
```

预期：如果任务 1 helper 会透传未知事件，该测试可能直接通过；如果 helper 丢弃或改写 `final`，测试失败。直接通过时仍继续步骤 3，把规范化规则显式写清楚。

- [x] **步骤 3：扩展 `_normalize_chat_stream_event()` 的 final 分支**

在 `api/routes.py` 中加入明确分支：

```python
    if status == "final":
        text = event.get("text", "")
        if text is None:
            text = ""
        text = str(text)
        if not text:
            return None
        return {
            "status": "final",
            "text": text,
            "replace": bool(event.get("replace", True)),
            "source": str(event.get("source") or "bridge"),
        }
```

保持非 delta / final 事件的兼容透传。

- [x] **步骤 4：运行任务 4 绿灯测试**

运行：

```bash
python -m pytest tests/test_streaming_api.py tests/test_streaming_response_envelope.py -v
```

预期：全部通过，失败数为 0。

- [x] **步骤 5：提交任务 4**

运行：

```bash
git add api/routes.py tests/test_streaming_api.py
git commit -m "refactor(流式): 规范化最终收敛事件"
```

## 任务 5：bounded queue 与进度事件队列策略

**文件：**
- 修改：`tests/test_streaming_api.py`
- 修改：`tests/test_streaming_output.py`
- 修改：`api/routes.py`
- 修改：`nanobot_kt/output.py`

- [x] **步骤 1：新增 `/chat` 使用 bounded queue 的红灯测试**

在 `tests/test_streaming_api.py` 增加：

```python
def test_stream_chat_uses_bounded_stream_queue(client):
    from unittest.mock import patch

    captured = {}

    async def fake_handle_message(*args, **kwargs):
        queue = kwargs.get("stream_queue")
        assert queue is not None
        captured["maxsize"] = queue.maxsize
        return "ok"

    with patch("api.routes.get_bridge") as mock_get_bridge:
        mock_get_bridge.return_value.handle_message.side_effect = fake_handle_message
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_queue_bound_user",
                "session_id": "group_1000",
                "query": "test",
                "stream": True,
            },
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"status": "done"' in body
    assert captured["maxsize"] > 0
```

- [x] **步骤 2：新增进度事件满队列策略测试**

在 `tests/test_streaming_output.py` 增加：

```python
@pytest.mark.asyncio
async def test_buffered_output_drops_progress_when_stream_queue_is_full(caplog):
    from nanobot_kt.output import BufferedOutput

    output = BufferedOutput()
    queue = asyncio.Queue(maxsize=1)
    output.enable_stream(queue)
    await queue.put({"status": "delta", "text": "占位"})

    output.on_activity("tool_start", "[memory_read] query=hi")

    assert queue.qsize() == 1
    assert await asyncio.wait_for(queue.get(), timeout=1) == {"status": "delta", "text": "占位"}
```

再增加 error 不丢失测试：

```python
@pytest.mark.asyncio
async def test_buffered_output_keeps_error_when_stream_queue_is_full():
    from nanobot_kt.output import BufferedOutput

    output = BufferedOutput()
    queue = asyncio.Queue(maxsize=1)
    output.enable_stream(queue)
    await queue.put({"status": "delta", "text": "占位"})

    output.on_activity("processing_error", "boom")
    assert await asyncio.wait_for(queue.get(), timeout=1) == {"status": "delta", "text": "占位"}
    assert await asyncio.wait_for(queue.get(), timeout=1) == {"status": "error", "message": "boom"}
```

- [x] **步骤 3：运行红灯测试**

运行：

```bash
python -m pytest tests/test_streaming_api.py::test_stream_chat_uses_bounded_stream_queue tests/test_streaming_output.py::test_buffered_output_drops_progress_when_stream_queue_is_full tests/test_streaming_output.py::test_buffered_output_keeps_error_when_stream_queue_is_full -v
```

预期：bounded queue 测试因 `queue.maxsize == 0` 失败；进度策略测试可能因满队列上创建 pending task 而失败。

- [x] **步骤 4：给 API stream queue 增加上限常量**

在 `api/routes.py` 顶层增加：

```python
CHAT_STREAM_QUEUE_MAXSIZE = 128
```

把 `_stream_chat()` 中的队列创建改为：

```python
        stream_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=CHAT_STREAM_QUEUE_MAXSIZE)
```

- [x] **步骤 5：调整 `BufferedOutput._schedule_stream_event()`**

在 `nanobot_kt/output.py` 中把 progress 事件改为满队列时丢弃，error 仍排队：

```python
    def _schedule_stream_event(self, event: dict[str, Any]) -> None:
        if self._stream_queue is None:
            return
        if event.get("status") == "progress":
            try:
                self._stream_queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("[BufferedOutput] progress event dropped because stream queue is full: %s", event)
            return
        try:
            task = asyncio.create_task(self._stream_queue.put(event))
        except RuntimeError:
            logger.debug("[BufferedOutput] stream event dropped without running loop: %s", event)
            return
        self._stream_tasks.add(task)
        task.add_done_callback(self._discard_stream_task)
```

`write_stream()` 和 `write_final()` 继续 `await queue.put(...)`，让文本 delta 和 final 在慢消费者场景下自然 backpressure。

- [x] **步骤 6：运行任务 5 绿灯测试**

运行：

```bash
python -m pytest tests/test_streaming_api.py tests/test_streaming_output.py -v
```

预期：全部通过，失败数为 0。

- [x] **步骤 7：提交任务 5**

运行：

```bash
git add api/routes.py nanobot_kt/output.py tests/test_streaming_api.py tests/test_streaming_output.py tests/test_api.py
git commit -m "perf(流式): 限制聊天流队列增长"
```

## 任务 6：文档收口与计划状态更新

**文件：**
- 修改：`docs/message-field-standard.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/streaming-sse-convergence.md`

- [x] **步骤 1：更新消息字段标准**

在 `docs/message-field-standard.md` 的响应 / stream 相关章节补充：

```markdown
### `/chat` SSE 事件

`/chat` 流式响应使用 `data: <json>\n\n` framing。事件对象通过 `status` 区分：

- `delta`：草稿增量，字段为 `text`，服务端可合并连续 chunk。
- `progress`：工具或处理进度，不代表最终回复。
- `final`：展示收敛信号，字段为 `text`、`replace`、`source`，不替代 `done`。
- `done`：最终响应信封，`answer` / `reply` 是权威结果。
- `error`：安全错误信息，不暴露内部异常细节。

客户端必须以 `done.answer` / `done.reply` 更新最终业务状态。`delta` 与 `final` 只影响流式展示区。
```

- [x] **步骤 2：更新 `docs/todo.md` 路线项 6**

把路线项 6 的现状改为包含：

```markdown
- API 层已对 `/chat` SSE 事件做规范化，并合并连续 `delta.text`。
- Bridge 已发送 `final.replace` 收敛事件，`done` 信封仍是业务权威结果。
- `/chat` stream queue 已设置上限，文本 delta / final 采用自然 backpressure，progress 满队列可丢弃。
```

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

在 P3-1 行和阶段记录中补充每个任务的提交号与验证命令。提交号以实际提交为准，格式保持现有表格风格。

- [x] **步骤 4：标记本计划任务状态**

把本计划中已经完成的任务复选框从 `[ ]` 改成 `[x]`，并在「当前状态」中写入实际提交号。

- [x] **步骤 5：运行文档自检**

运行：

```bash
rg -n "T[O]DO|待[定]|后续[实]现|类似[任]务|添加[适]当|为上[述]" docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/streaming-sse-convergence.md
python - <<'PY'
from pathlib import Path

paths = [
    Path("docs/message-field-standard.md"),
    Path("docs/todo.md"),
    Path("docs/plan_walkthrough.md"),
    Path(".Codex/plans/streaming-sse-convergence.md"),
]
bad = "\ufffd"
for path in paths:
    text = path.read_text(encoding="utf-8")
    if bad in text:
        raise SystemExit(f"{path}: contains replacement character")
PY
git diff --check -- docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/streaming-sse-convergence.md
```

预期：占位符扫描无输出；`git diff --check` 无输出。

- [x] **步骤 6：运行 P3-1 定向回归**

运行：

```bash
python -m pytest tests/test_streaming_api.py tests/test_streaming_response_envelope.py tests/test_agent_step_api.py tests/test_streaming_output.py tests/test_streaming_bridge.py -v
```

预期：全部通过，失败数为 0。

- [x] **步骤 7：提交任务 6**

运行：

```bash
git add docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/streaming-sse-convergence.md
git commit -m "docs(流式): 收口 SSE 收敛状态"
```

## 任务 7：最终验证

**文件：**
- 不修改文件。

- [ ] **步骤 1：运行格式检查**

运行：

```bash
git diff --check
```

预期：无输出，退出码为 0。

- [ ] **步骤 2：运行流式相关回归**

运行：

```bash
python -m pytest tests/test_streaming_api.py tests/test_streaming_response_envelope.py tests/test_agent_step_api.py tests/test_streaming_output.py tests/test_streaming_bridge.py -v
```

预期：全部通过，失败数为 0。

- [ ] **步骤 3：运行 API / Bridge 相关回归**

运行：

```bash
python -m pytest tests/test_api.py tests/test_chat_response_envelope.py tests/test_api_push_envelope.py tests/test_kt_framework.py tests/test_streaming_bridge.py -v
```

预期：全部通过，失败数为 0。

- [ ] **步骤 4：运行全量测试**

运行：

```bash
python -m pytest tests/ -v
```

预期：全部通过，失败数为 0。若外部服务导致非代码失败，必须记录完整失败用例和根因，不得声明全量通过。

- [ ] **步骤 5：最终状态检查**

运行：

```bash
git status --short
git log -8 --oneline
```

预期：只剩仓库原有无关脏文件；最新提交覆盖任务 1 到任务 6。
