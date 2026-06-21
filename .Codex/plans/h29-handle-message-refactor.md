# H29 handle_message 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不改变 Bridge 外部契约的前提下，分阶段拆分 `NanobotBridge.handle_message()`，降低模型路由、重试、reply contract、trace 和 stream 改动的维护风险。

**架构：** 第一阶段在 `nanobot_kt.bridge` 模块内抽低风险 helper，保持现有 monkeypatch 路径。第二阶段先补模型 retry 行为测试，再抽 `_run_model_loop()`。第三阶段抽 reply contract 出口治理，最后把 trace cleanup 收敛为幂等 finalizer。

**技术栈：** Python 3.12、asyncio、KT controller、FastAPI Bridge facade、pytest、in-memory SQLite。

---

## 当前状态

- [x] 已完成 H29 只读审计，覆盖职责切片、测试策略和外部契约。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-h29-handle-message-refactor-design.md`。
- [x] 设计阶段已提交：`e6cd2b5 docs(桥接): 设计消息处理拆分`。
- [x] 设计阶段验证已运行：`python -m pytest tests/ -v`，结果 `1461 passed, 6 skipped, 139 warnings in 108.74s`。
- [x] 计划阶段已提交：`a9f0dbb docs(计划): 记录消息处理拆分计划`。
- [x] 任务 1：抽低风险 helper，提交 `e65575c refactor(桥接): 抽取消息准备辅助函数`。
- [x] 任务 2：补模型 retry 回归并拆 `_run_model_loop()`，提交 `1da43fb refactor(桥接): 拆分回复模型重试循环`。
- [x] 任务 3：补 structured `no_reply` 回归并拆 `_check_reply_contract()`，提交 `786e707 refactor(桥接): 拆分回复合同检查`。
- [x] 任务 4：补 trace cleanup 幂等回归并收敛 finalizer，提交 `1612158 refactor(桥接): 收敛运行追踪收尾`。
- [x] 任务 5：同步文档状态、运行最终验证并提交文档收口。

## 阶段验证摘要

- 任务 1：定向回归 `8 passed, 1 warning`，相邻回归 `36 passed, 1 warning`，全量回归 `1464 passed, 6 skipped, 139 warnings in 108.57s`。
- 任务 2：模型门禁 `10 passed, 1 warning`，相邻回归 `98 passed, 1 warning`，全量回归 `1465 passed, 6 skipped, 139 warnings in 110.41s`。
- 任务 3：reply contract 门禁 `9 passed, 1 warning`，Bridge 相邻回归 `57 passed, 1 warning`，全量回归 `1466 passed, 6 skipped, 139 warnings in 112.65s`。
- 任务 4：trace 与 stream 回归 `15 passed, 21 warnings`，Bridge 相邻回归 `67 passed, 1 warning`，全量回归 `1467 passed, 6 skipped, 139 warnings in 114.10s`。
- 任务 5：文档扫描无输出，文档格式检查无输出，H29 定向回归 `80 passed, 1 warning in 21.03s`，最终全量回归 `1467 passed, 6 skipped, 139 warnings in 111.26s`。

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-21-h29-handle-message-refactor-design.md`。
- 待办来源：`docs/todo.md` 中 H29 条目。
- 关键实现范围：`nanobot_kt/bridge.py:866-1947`。
- 强约束：不新增 `asyncio.run()`，不添加同步函数包 awaitable，所有新增 async helper 都直接 `await`。

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `nanobot_kt/bridge.py` | 保留 public facade，新增 dataclass 和私有 helper，逐步缩短 `handle_message()` 主流程 |
| `tests/test_streaming_bridge.py` | 保护 stream flag、output 初始化、event payload 和 final event 行为 |
| `tests/test_kt_framework.py` | 保护 Bridge 主链路、模型路由、模型 retry、reply contract 和 trace cleanup 行为 |
| `tests/test_bridge_prompt_v2.py` | 保护 Prompt Runtime 输入、live plan 应用和 tool schema 语义 |
| `tests/test_history.py` | 保护历史 metadata 注入和 conversation 重建 |
| `docs/todo.md` | H29 进度说明，只有全部实现完成后才能标为已完成 |
| `docs/plan_walkthrough.md` | 记录阶段提交、验证命令和下一步边界 |
| `.Codex/plans/h29-handle-message-refactor.md` | 本计划唯一 source of truth |

## 接口不变约束

- `NanobotBridge.handle_message()` 与 `NanobotBridgePool.handle_message()` 的签名、默认值和 keyword-only 参数保持不变。
- `NanobotBridgePool` 继续按 `session_id > user_id > "_default"` 选择 child bridge，并原样透传参数。
- `metadata` 继续是开放 dict，现有字段名不能重命名。
- `stream_queue` 继续是侧通道，不能替代最终字符串返回值。
- 空字符串返回继续表达 no-reply、suppressed、audit-failure 或 empty。
- `pop_last_reply_meta(session_id)` 继续在 `handle_message()` 返回后可用，并保持弹出式语义。
- `files` 继续从 `metadata["files"]` 进入 Bridge，不提升为顶层参数。

## 并行执行策略

默认由主线程顺序执行，因为 `nanobot_kt/bridge.py` 是共享高冲突文件。需要提速时，只把测试或文档分派给子 agent。

| 角色 | 可修改文件 | 禁止修改 |
| --- | --- | --- |
| Agent A | `tests/test_kt_framework.py` 中模型 retry 新用例 | `nanobot_kt/bridge.py`、其他测试类 |
| Agent B | `tests/test_kt_framework.py::TestReplyContract` 新用例 | `nanobot_kt/bridge.py`、模型路由测试 |
| Agent C | `docs/todo.md`、`docs/plan_walkthrough.md`、本计划 | 生产代码和测试 |
| 主线程 | `nanobot_kt/bridge.py`、集成所有测试与文档 | 回滚无关脏项 |

子 agent 提示词模板：

```markdown
你只负责本任务列出的文件。不得修改未列入的文件，不得暂存或提交。
先写测试并运行指定命令，记录失败或通过的真实输出。
如果测试已覆盖当前行为，说明它是 characterization guard；如果失败，说明真实缺口。
返回：改动文件、测试命令、输出摘要、风险点、建议 commit message。
```

## 任务 1：抽低风险 helper

**文件：**
- 修改：`nanobot_kt/bridge.py`
- 修改：`tests/test_streaming_bridge.py`
- 修改：`tests/test_kt_framework.py`

- [x] **步骤 1：为 output 初始化 helper 写失败测试**

在 `tests/test_streaming_bridge.py` 增加：

```python
def test_prepare_output_for_request_enables_stream_and_clears_reply_cache(monkeypatch):
    from unittest.mock import MagicMock

    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge._output = MagicMock()
    cleared = []
    monkeypatch.setattr("core.reply_runtime_cache.clear_last_reply", lambda: cleared.append(True))
    queue = asyncio.Queue()

    bridge._prepare_output_for_request(stream_queue=queue, stream_enabled=True)

    bridge._output.clear.assert_called_once_with()
    bridge._output.enable_stream.assert_called_once_with(queue)
    assert cleared == [True]
```

同文件增加关闭分支：

```python
def test_prepare_output_for_request_disables_stream_when_not_streaming(monkeypatch):
    from unittest.mock import MagicMock

    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge._output = MagicMock()
    monkeypatch.setattr("core.reply_runtime_cache.clear_last_reply", lambda: None)

    bridge._prepare_output_for_request(stream_queue=None, stream_enabled=False)

    bridge._output.clear.assert_called_once_with()
    bridge._output.enable_stream.assert_not_called()
    bridge._output.disable_stream.assert_called_once_with()
```

- [x] **步骤 2：为 event payload helper 写失败测试**

在 `tests/test_kt_framework.py::TestNanobotBridge` 增加：

```python
@pytest.mark.asyncio
async def test_prepare_event_payload_builds_multimodal_capabilities(self, monkeypatch):
    from types import SimpleNamespace

    from kohakuterrarium.llm.message import ImagePart
    from nanobot_kt.bridge import NanobotBridge

    image = ImagePart(
        url="data:image/jpeg;base64,ZmFrZQ==",
        detail="low",
        source_type="qq",
        source_name="attachment_1",
    )

    def fake_prepare_image_parts(files, **kwargs):
        assert files == ["https://example.com/a.png"]
        assert kwargs == {
            "source_type": "qq",
            "source_name_prefix": "attachment",
            "detail": "low",
        }
        return [image]

    monkeypatch.setattr("nanobot_kt.bridge.prepare_image_parts", fake_prepare_image_parts)

    bridge = NanobotBridge.__new__(NanobotBridge)
    payload = await bridge._prepare_event_payload(
        prompt_event_content="看看图",
        files=["https://example.com/a.png"],
        tool_plan=SimpleNamespace(sent_tool_schemas=[{"name": "reply"}]),
    )

    assert payload.image_parts == [image]
    assert payload.required_capabilities == {
        "supports_stream": True,
        "supports_image": True,
        "supports_tools": True,
    }
    assert payload.event_content != "看看图"
```

- [x] **步骤 3：运行失败测试**

运行：

```bash
python -m pytest \
  tests/test_streaming_bridge.py::test_prepare_output_for_request_enables_stream_and_clears_reply_cache \
  tests/test_streaming_bridge.py::test_prepare_output_for_request_disables_stream_when_not_streaming \
  tests/test_kt_framework.py::TestNanobotBridge::test_prepare_event_payload_builds_multimodal_capabilities \
  -q
```

预期：失败，原因是 `NanobotBridge` 还没有 `_prepare_output_for_request()` 和 `_prepare_event_payload()`。

- [x] **步骤 4：新增 dataclass**

在 `nanobot_kt/bridge.py` 顶部已有 import 区确认存在 `dataclass`、`Any`。如果缺少，补齐：

```python
from dataclasses import dataclass
from typing import Any
```

在 `PromptRuntimeAssemblyContext` 附近新增：

```python
@dataclass
class BridgeRuntimeToolState:
    persona_text: str
    history_messages: Any
    history_header: str
    is_group: bool
    effort_constraint: str
    runtime_preset: str
    chat_type: str
    runtime_chat_type: str
    group_id: str
    user_id: str
    platform: str
    tool_plan: Any
    runtime_tool_prompt: str
    effective_tools: list[str]
    final_tools_token: Any
    tool_plan_token: Any


@dataclass
class BridgeEventPayload:
    event_content: Any
    image_parts: list[Any]
    required_capabilities: dict[str, bool]
```

- [x] **步骤 5：实现 `_prepare_output_for_request()`**

在 `NanobotBridge` 类中、`handle_message()` 之前新增：

```python
    def _prepare_output_for_request(
        self,
        *,
        stream_queue: asyncio.Queue[dict[str, Any]] | None,
        stream_enabled: bool,
    ) -> None:
        self._output.clear()
        if stream_queue is not None and stream_enabled:
            self._output.enable_stream(stream_queue)
        else:
            disable_stream = getattr(self._output, "disable_stream", None)
            if callable(disable_stream):
                disable_stream()
        try:
            from core.reply_runtime_cache import clear_last_reply

            clear_last_reply()
        except Exception:
            pass
```

- [x] **步骤 6：实现 `_prepare_event_payload()`**

在 `NanobotBridge` 类中、`handle_message()` 之前新增：

```python
    async def _prepare_event_payload(
        self,
        *,
        prompt_event_content: str,
        files: Any,
        tool_plan: Any,
    ) -> BridgeEventPayload:
        image_parts: list[Any] = []
        if files:
            image_parts = await asyncio.to_thread(
                prepare_image_parts,
                files,
                source_type="qq",
                source_name_prefix="attachment",
                detail="low",
            )
            event_content = make_multimodal_content(prompt_event_content, images=image_parts)
        else:
            event_content = prompt_event_content
        try:
            has_tool_schemas = bool(list(getattr(tool_plan, "sent_tool_schemas", []) or []))
        except Exception:
            has_tool_schemas = False
        required_capabilities = {"supports_stream": True}
        if image_parts:
            required_capabilities["supports_image"] = True
        if has_tool_schemas:
            required_capabilities["supports_tools"] = True
        return BridgeEventPayload(
            event_content=event_content,
            image_parts=image_parts,
            required_capabilities=required_capabilities,
        )
```

- [x] **步骤 7：机械替换 `handle_message()` 中 output 和 event payload 代码**

把原 `self._output.clear()` 到 `clear_last_reply()` 的代码块替换为：

```python
            self._prepare_output_for_request(
                stream_queue=stream_queue,
                stream_enabled=meta["stream"],
            )
```

把原 `files = meta.get("files")` 到 `required_capabilities` 构造结束的代码块替换为：

```python
            event_payload = await self._prepare_event_payload(
                prompt_event_content=prompt_build.event_content,
                files=meta.get("files"),
                tool_plan=tool_plan,
            )
            image_parts = event_payload.image_parts
            event_content = event_payload.event_content
            required_capabilities = event_payload.required_capabilities
```

- [x] **步骤 8：运行任务 1 定向测试**

运行：

```bash
python -m pytest \
  tests/test_streaming_bridge.py::test_prepare_output_for_request_enables_stream_and_clears_reply_cache \
  tests/test_streaming_bridge.py::test_prepare_output_for_request_disables_stream_when_not_streaming \
  tests/test_kt_framework.py::TestNanobotBridge::test_prepare_event_payload_builds_multimodal_capabilities \
  tests/test_kt_framework.py::TestNanobotBridge::test_handle_message_uses_multimodal_event_for_files \
  tests/test_streaming_bridge.py \
  -q
```

预期：全部通过。

- [x] **步骤 9：运行任务 1 相邻回归**

运行：

```bash
python -m pytest \
  tests/test_kt_framework.py::TestNanobotBridge::test_handle_message_with_files_requests_vision_candidates \
  tests/test_kt_framework.py::TestNanobotBridge::test_handle_message_with_files_degrades_to_text_without_vision_candidate \
  tests/test_bridge_prompt_v2.py \
  tests/test_history.py \
  -q
```

预期：全部通过。

- [x] **步骤 10：提交任务 1**

运行：

```bash
python -m pytest tests/ -v
git add nanobot_kt/bridge.py tests/test_streaming_bridge.py tests/test_kt_framework.py
git commit -m "refactor(桥接): 抽取消息准备辅助函数"
```

提交前确认全量测试为 0 failures。

## 任务 2：补模型 retry 回归并拆 `_run_model_loop()`

**文件：**
- 修改：`nanobot_kt/bridge.py`
- 修改：`tests/test_kt_framework.py`

- [x] **步骤 1：新增 retry 行为测试**

在 `tests/test_kt_framework.py::TestNanobotBridge` 增加一个测试，使用两个候选模型并让第一轮返回空响应：

```python
@patch("nanobot_kt.bridge.registry")
@patch("nanobot_kt.bridge.NewAPIClient")
@patch("nanobot_kt.bridge.load_agent_config")
@patch("nanobot_kt.bridge.Agent")
def test_handle_message_retries_next_model_after_empty_response(
    self, MockAgent, mock_load, MockClient, mock_registry, monkeypatch
):
    from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
    from nanobot_kt.bridge import NanobotBridge
    import json

    monkeypatch.setattr("core.settings_service.settings.get", lambda key, default=None: default)
    monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "", raising=False)
    mock_registry.get_models_by_provider.return_value = [{"id": "model-a"}, {"id": "model-b"}]

    route_client = MagicMock()
    route_client.sync_models_to_registry = AsyncMock()
    route_client.estimate_complexity.return_value = 3
    route_client.get_ordered_candidates.return_value = [
        {"id": "model-a", "intelligence": 10, "context_window": 128000},
        {"id": "model-b", "intelligence": 10, "context_window": 128000},
    ]
    MockClient.return_value = route_client

    failure_tracker = MagicMock(record_success=AsyncMock(), record_failure=AsyncMock())
    MockClient.get_failure_tracker.return_value = failure_tracker

    mock_config = MagicMock()
    mock_config.name = "test"
    mock_load.return_value = mock_config

    reply_output = json.dumps({REPLY_MARKER: {"content": "第二个模型回复"}}, ensure_ascii=False)
    mock_conv = MagicMock()
    mock_conv._messages = []
    mock_conv.to_messages.return_value = []
    mock_conv.find_last_user_index.return_value = 0
    mock_conv.get_messages.side_effect = [
        [],
        [{"role": "tool", "content": reply_output}],
        [{"role": "tool", "content": reply_output}],
    ]

    async def fake_process(_event):
        return None

    llm = MagicMock(config=MagicMock(model="old-model"))
    mock_agent = MagicMock()
    mock_agent.start = AsyncMock()
    mock_agent.registry.list_tools.return_value = []
    mock_agent.controller = MagicMock(conversation=mock_conv, llm=llm)
    mock_agent._process_event = AsyncMock(side_effect=fake_process)
    MockAgent.return_value = mock_agent

    bridge = NanobotBridge()

    async def _run():
        await bridge.start()
        return await bridge.handle_message(
            "你好",
            user_id="u1",
            session_id="private_u1",
            metadata={"complexity": 3},
        )

    result = run_async(_run())

    assert result == "第二个模型回复"
    failure_tracker.record_failure.assert_awaited_once_with("model-a")
    failure_tracker.record_success.assert_awaited_once_with("model-b")
    assert llm.config.model == "model-b"
    assert mock_conv.truncate_from.called
```

- [x] **步骤 2：运行 retry 测试**

运行：

```bash
python -m pytest tests/test_kt_framework.py::TestNanobotBridge::test_handle_message_retries_next_model_after_empty_response -q
```

预期：若当前行为已满足则通过，并作为 characterization guard；若失败，先修复当前行为再拆 helper。

- [x] **步骤 3：新增 `ModelLoopResult`**

在 `BridgeEventPayload` 后新增：

```python
@dataclass
class ModelLoopResult:
    response: str
    result: Any
    target_model: str
    preserved_html: str
    selected_candidate: dict[str, Any] | None
    attempts: int
```

- [x] **步骤 4：抽 `_run_model_loop()`**

在 `NanobotBridge` 类中新增 async helper。第一版只做机械搬移，不改算法。输入必须显式传入 `event`、`next_event`、`candidate_models`、`route_plan`、`required_capabilities`、`prompt_build`、`run_handle`、`trace_id`、`route_client`、`failure_tracker`、`manual_reply_model` 和 `meta` 中当前 loop 使用的值。

helper 返回：

```python
            return ModelLoopResult(
                response=response,
                result=result,
                target_model=target_model,
                preserved_html=preserved_html,
                selected_candidate=selected_candidate,
                attempts=attempts,
            )
```

主流程替换为：

```python
            model_loop = await self._run_model_loop(
                event=event,
                candidate_models=candidate_models,
                route_plan=route_plan,
                route_client=route_client,
                failure_tracker=failure_tracker,
                required_capabilities=required_capabilities,
                prompt_event_content=prompt_build.event_content,
                meta=meta,
                trace_id=trace_id,
                run_id=run_handle.run_id,
                create_user_event=create_user_event,
                process_event=process_event,
            )
            response = model_loop.response
            result = model_loop.result
            target_model = model_loop.target_model
            preserved_html = model_loop.preserved_html
```

如果某个变量还在主流程后续被使用，必须从 `ModelLoopResult` 补字段，而不是依赖闭包。

- [x] **步骤 5：运行模型路由和 retry 门禁**

运行：

```bash
python -m pytest \
  tests/test_kt_framework.py::TestNanobotBridge::test_handle_message_retries_next_model_after_empty_response \
  tests/test_kt_framework.py::TestNanobotBridge::test_handle_message_with_files_requests_vision_candidates \
  tests/test_kt_framework.py::TestNanobotBridge::test_handle_message_with_files_degrades_to_text_without_vision_candidate \
  tests/test_kt_framework.py::TestNanobotBridge::test_handle_message_uses_reply_model_intel_floor \
  tests/test_kt_framework.py::TestNanobotBridge::test_reply_model_uses_settings_override \
  tests/test_kt_framework.py::TestNanobotBridge::test_reply_model_disabled_falls_back_to_auto \
  tests/test_kt_framework.py::TestNanobotBridge::test_reply_model_lacking_required_capability_falls_back_to_auto \
  tests/test_kt_framework.py::TestNanobotBridge::test_reply_route_uses_route_provider_for_registry_candidates \
  tests/test_kt_framework.py::TestNanobotBridge::test_reply_route_rebuilds_controller_client_when_api_key_changes \
  tests/test_kt_framework.py::TestNanobotBridge::test_reply_route_syncs_controller_model_params \
  -q
```

预期：全部通过。

- [x] **步骤 6：提交任务 2**

运行：

```bash
python -m pytest tests/ -v
git add nanobot_kt/bridge.py tests/test_kt_framework.py
git commit -m "refactor(桥接): 拆分回复模型重试循环"
```

提交前确认全量测试为 0 failures。

## 任务 3：补 structured no_reply 回归并拆 `_check_reply_contract()`

**文件：**
- 修改：`nanobot_kt/bridge.py`
- 修改：`tests/test_kt_framework.py`

- [x] **步骤 1：新增 structured no_reply fallback 测试**

在 `tests/test_kt_framework.py::TestReplyContract` 增加：

```python
@patch("nanobot_kt.bridge.load_agent_config")
@patch("nanobot_kt.bridge.Agent")
def test_structured_final_action_no_reply_returns_empty(self, MockAgent, mock_load, monkeypatch):
    from nanobot_kt.bridge import NanobotBridge
    import json

    mock_config = MagicMock()
    mock_config.name = "test"
    mock_load.return_value = mock_config

    structured = json.dumps(
        {"final_action": "no_reply", "reason": "not addressed to bot"},
        ensure_ascii=False,
    )
    mock_conv = MagicMock()
    mock_conv._messages = []
    mock_conv.get_messages.return_value = [{"role": "assistant", "content": structured}]
    mock_conv.to_messages.return_value = []
    mock_conv.find_last_user_index.return_value = -1

    mock_agent = MagicMock()
    mock_agent.start = AsyncMock()
    mock_agent.registry.list_tools.return_value = []
    mock_agent.controller = MagicMock(
        conversation=mock_conv,
        llm=MagicMock(config=MagicMock(model="test-model")),
    )
    mock_agent._process_event = AsyncMock(return_value=None)
    MockAgent.return_value = mock_agent

    bridge = NanobotBridge()

    async def _run():
        await bridge.start()
        return await bridge.handle_message(
            "ambient",
            user_id="u1",
            session_id="private_u1",
            metadata={"enable_reply_contract_retry": False},
        )

    result = run_async(_run())

    assert result == ""
    reply_meta = bridge.pop_last_reply_meta("private_u1")
    assert reply_meta["_agent_result"] == "no_reply_structured"
    assert reply_meta["_no_reply"] is True
```

- [x] **步骤 2：运行 structured no_reply 测试**

运行：

```bash
python -m pytest tests/test_kt_framework.py::TestReplyContract::test_structured_final_action_no_reply_returns_empty -q
```

预期：通过或暴露真实缺口。若失败，先修复 structured no_reply 语义，再抽 helper。

- [x] **步骤 3：新增 `ReplyResolution`**

在 `ModelLoopResult` 后新增：

```python
@dataclass
class ReplyResolution:
    response: str
    agent_result: str
    no_reply: bool
    no_tool_call: bool
    output_preview: str
    finish_status: str
```

- [x] **步骤 4：抽 `_check_reply_contract()`**

把 `# no_reply 优先` 到 reply contract retry 结束的出口治理代码抽为 async helper。helper 必须接收当前逻辑实际使用的状态，包括 `session_id`、`response`、`result`、`preserved_html`、`target_model`、`query`、`meta`、`event_content`、`create_user_event`、`process_event` 和 `enable_reply_contract_retry`。

主流程替换为：

```python
            reply_resolution = await self._check_reply_contract(
                session_id=session_id,
                response=response,
                result=result,
                preserved_html=preserved_html,
                target_model=target_model,
                query=query,
                meta=meta,
                event_content=event_content,
                create_user_event=create_user_event,
                process_event=process_event,
            )
            response = reply_resolution.response
```

如果 helper 需要提前终止，使用 `ReplyResolution` 表达结果，不在 helper 内调用 `_finish_agent_trace()`。

- [x] **步骤 5：运行 reply contract 门禁**

运行：

```bash
python -m pytest tests/test_kt_framework.py::TestReplyContract -q
```

预期：全部通过。

- [x] **步骤 6：运行 Bridge 相邻回归**

运行：

```bash
python -m pytest \
  tests/test_kt_framework.py::TestNanobotBridge \
  tests/test_streaming_bridge.py \
  tests/test_bridge_integration.py \
  tests/test_reply_contract.py \
  -q
```

预期：全部通过。

- [x] **步骤 7：提交任务 3**

运行：

```bash
python -m pytest tests/ -v
git add nanobot_kt/bridge.py tests/test_kt_framework.py
git commit -m "refactor(桥接): 拆分回复合同检查"
```

提交前确认全量测试为 0 failures。

## 任务 4：收敛 trace cleanup

**文件：**
- 修改：`nanobot_kt/bridge.py`
- 修改：`tests/test_kt_framework.py`

- [x] **步骤 1：新增 trace cleanup 幂等测试**

在 `tests/test_kt_framework.py::TestNanobotBridge` 增加：

```python
def test_bridge_trace_finalizer_finishes_once(monkeypatch):
    from nanobot_kt.bridge import BridgeTraceFinalizer, NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)
    restore_calls = []
    bridge._restore_saved_tools = lambda: restore_calls.append(True)
    finish_calls = []
    reset_trace_calls = []
    reset_final_tools_calls = []
    reset_tool_plan_calls = []

    monkeypatch.setattr(
        "core.tracing.RunTracer.finish_run",
        lambda *args, **kwargs: finish_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "core.tracing_context.reset_trace_context",
        lambda token: reset_trace_calls.append(token),
    )
    monkeypatch.setattr(
        "core.final_tools.reset_current_final_tools",
        lambda token: reset_final_tools_calls.append(token),
    )
    monkeypatch.setattr(
        "core.tool_plan.reset_current_tool_plan",
        lambda token: reset_tool_plan_calls.append(token),
    )

    finalizer = BridgeTraceFinalizer(
        bridge=bridge,
        run_id="run-1",
        trace_tokens="trace-token",
        run_meta={"message_id": "m1"},
        started_at=100.0,
        now=lambda: 100.2,
        final_tools_token="final-token",
        tool_plan_token="tool-token",
    )

    finalizer.finish(status="success", output_preview="ok", model="model-a")
    finalizer.finish(status="error", error="late")

    assert len(finish_calls) == 1
    assert restore_calls == [True]
    assert reset_trace_calls == ["trace-token"]
    assert reset_final_tools_calls == ["final-token"]
    assert reset_tool_plan_calls == ["tool-token"]
```

- [x] **步骤 2：运行失败测试**

运行：

```bash
python -m pytest tests/test_kt_framework.py::TestNanobotBridge::test_bridge_trace_finalizer_finishes_once -q
```

预期：失败，原因是 `BridgeTraceFinalizer` 尚不存在。

- [x] **步骤 3：实现 `BridgeTraceFinalizer`**

在 `ReplyResolution` 后新增：

```python
@dataclass
class BridgeTraceFinalizer:
    bridge: Any
    run_id: str
    trace_tokens: Any
    run_meta: dict[str, Any]
    started_at: float
    now: Any
    final_tools_token: Any = None
    tool_plan_token: Any = None
    closed: bool = False

    def set_tool_tokens(self, *, final_tools_token: Any, tool_plan_token: Any) -> None:
        self.final_tools_token = final_tools_token
        self.tool_plan_token = tool_plan_token

    def finish(
        self,
        status: str,
        *,
        output_preview: str = "",
        error: str = "",
        model: str = "",
    ) -> None:
        if self.closed:
            return
        self.closed = True
        self.bridge._restore_saved_tools()
        from core.tracing import RunTracer
        from core.tracing_context import reset_trace_context

        RunTracer.finish_run(
            self.run_id,
            status=status,
            output_preview=output_preview,
            error=error,
            latency_ms=int((self.now() - self.started_at) * 1000),
            model=model,
            meta=self.run_meta,
        )
        if self.final_tools_token is not None:
            try:
                from core.final_tools import reset_current_final_tools

                reset_current_final_tools(self.final_tools_token)
            except Exception:
                pass
            self.final_tools_token = None
        if self.tool_plan_token is not None:
            try:
                from core.tool_plan import reset_current_tool_plan

                reset_current_tool_plan(self.tool_plan_token)
            except Exception:
                pass
            self.tool_plan_token = None
        reset_trace_context(self.trace_tokens)
```

- [x] **步骤 4：替换内部 `_finish_agent_trace()` 闭包**

在 `handle_message()` 中创建 finalizer：

```python
            trace_finalizer = BridgeTraceFinalizer(
                bridge=self,
                run_id=run_handle.run_id,
                trace_tokens=trace_tokens,
                run_meta=run_meta,
                started_at=t_start,
                now=_time.time,
            )
```

把原 `_finish_agent_trace(...)` 调用替换为：

```python
                trace_finalizer.finish("error", error=str(e))
```

或：

```python
            trace_finalizer.finish(
                "success",
                output_preview=response[:500],
                model=target_model,
            )
```

在 tool plan token 设置后调用：

```python
            trace_finalizer.set_tool_tokens(
                final_tools_token=final_tools_token,
                tool_plan_token=tool_plan_token,
            )
```

删除旧闭包和 `trace_closed` 变量。保留局部 `final_tools_token` / `tool_plan_token` 的后续引用时，必须确认不再被 reset 两次。

- [x] **步骤 5：运行 trace 与 stream 回归**

运行：

```bash
python -m pytest \
  tests/test_kt_framework.py::TestNanobotBridge::test_bridge_trace_finalizer_finishes_once \
  tests/test_streaming_bridge.py \
  tests/test_prompt_trace_admin.py \
  tests/test_llm_trace_context.py \
  -q
```

预期：全部通过。

- [x] **步骤 6：提交任务 4**

运行：

```bash
python -m pytest tests/ -v
git add nanobot_kt/bridge.py tests/test_kt_framework.py
git commit -m "refactor(桥接): 收敛运行追踪收尾"
```

提交前确认全量测试为 0 failures。

## 任务 5：文档收口与最终验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/h29-handle-message-refactor.md`

- [x] **步骤 1：同步 `docs/todo.md` H29 状态**

如果任务 1-4 均完成，把 H29 条目从未完成改为已完成，并追加实施状态：

```markdown
- [x] **H29 handle_message 1080 行 + 深嵌套** · `nanobot_kt/bridge.py:860-1940` · HIGH(可维护) · L ·〔呼应路线图 §1〕
  已完成第一轮拆分：低风险 request helper、模型重试循环、reply contract 出口治理和 trace cleanup 已拆成私有边界；public signature、metadata、stream 侧通道和 `pop_last_reply_meta()` 语义保持不变。
```

如果只完成任务 1 或任务 2，不要把 H29 勾选为完成，只追加阶段状态。

- [x] **步骤 2：同步 `docs/plan_walkthrough.md`**

追加 2026-06-21 H29 章节，至少记录：

- 设计文档路径和 commit。
- 计划文件路径。
- 每个任务的完成状态和 commit。
- 每个阶段验证命令与结果。
- 下一步从哪一个未完成任务继续。

- [x] **步骤 3：更新本计划状态**

把已完成任务的复选框改为 `[x]`，并在「当前状态」补充提交号。未执行任务保持 `[ ]`。

- [x] **步骤 4：文档扫描**

运行：

```bash
rg -n "T[O]DO|待[定]|后续[实]现|F[I]XME|占[位]|类似[任]务|添加[适]当|为上[述]" \
  docs/todo.md \
  docs/plan_walkthrough.md \
  .Codex/plans/h29-handle-message-refactor.md \
  docs/superpowers/specs/2026-06-21-h29-handle-message-refactor-design.md
```

预期：无输出，退出码 1。

- [x] **步骤 5：文档格式检查**

运行：

```bash
git diff --check -- \
  docs/todo.md \
  docs/plan_walkthrough.md \
  .Codex/plans/h29-handle-message-refactor.md \
  docs/superpowers/specs/2026-06-21-h29-handle-message-refactor-design.md
```

预期：无输出，退出码 0。

- [x] **步骤 6：最终 H29 定向回归**

运行：

```bash
python -m pytest \
  tests/test_kt_framework.py::TestNanobotBridge \
  tests/test_kt_framework.py::TestReplyContract \
  tests/test_streaming_bridge.py \
  tests/test_bridge_prompt_v2.py \
  tests/test_history.py \
  -q
```

预期：全部通过。

- [x] **步骤 7：全量测试**

运行：

```bash
python -m pytest tests/ -v
```

预期：0 failures。

- [x] **步骤 8：提交任务 5**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/h29-handle-message-refactor.md
git commit -m "docs(计划): 收口消息处理拆分状态"
```

提交前确认暂存区只有上述文档文件。

## 执行顺序

1. 先执行任务 1，获得低风险 helper 和最小拆分收益。
2. 任务 2 和任务 3 可以让不同子 agent 先写测试，但生产代码抽取只能由一个 owner 串行合并。
3. 任务 4 必须在任务 2 和任务 3 后执行，因为 early return 减少后 trace cleanup 风险更低。
4. 每个任务完成后必须运行指定定向测试、全量测试并单独 commit。
5. 任一任务失败时，不进入下一任务，先修复当前任务或回到设计文档补充边界说明。
