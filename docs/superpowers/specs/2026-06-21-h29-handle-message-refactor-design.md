# H29 handle_message 拆分设计

日期：2026-06-21

## 背景

`NanobotBridge.handle_message()` 当前约 1080 行，集中在 `nanobot_kt/bridge.py:866-1947`。它同时承担会话锁、trace 生命周期、Prompt Runtime 输入、工具计划、附件事件、模型路由、模型重试、reply contract、stream final、群聊冷却通知和工具状态恢复。

这个函数已经成为 H29 的主要维护风险：每次改动模型路由、流式输出或 reply contract 时，都需要跨越大量局部变量和 early return。现有测试覆盖较多，但模型重试与 trace 收尾幂等仍有缺口，直接大拆容易把隐藏契约打断。

## 目标

- 保留 `handle_message()` 作为外部编排入口，但把内部阶段拆成可命名、可测试的 helper。
- 先补齐模型 retry 和 trace cleanup 的测试缺口，再拆高风险块。
- 让第一批代码改动保持在 `nanobot_kt.bridge` 模块内，避免破坏现有 monkeypatch 路径。
- 保持 HTTP 请求无状态：每次请求仍重建 KT conversation，历史继续通过 Prompt Runtime 输入注入。
- 不引入新的同步桥接、`asyncio.run()` 或同步函数包 awaitable。

## 非目标

- 不改变 `NanobotBridge.handle_message()` 或 `NanobotBridgePool.handle_message()` 的 public signature。
- 不把 `files` 从 `metadata["files"]` 提升为顶层参数。
- 不重写 Prompt Runtime 模板、不改变 prompt 输入语义。
- 不把模型路由拆到新模块作为第一步。
- 不重构 `api/routes.py`、`GroupIngressService`、`daily_digest` 或 BridgePool facade。
- 不在 H29 内解决 H30 RAG `query()` 巨函数问题。

## 接口不变声明

H29 只拆分 `NanobotBridge.handle_message()` 的内部实现，不改变 `nanobot_kt.bridge` 的外部 facade。`NanobotBridge.handle_message()` 与 `NanobotBridgePool.handle_message()` 必须保持以下签名、默认值和 keyword-only 参数不变：

```python
async def handle_message(
    query: str,
    *,
    user_id: str = "",
    session_id: str = "",
    sender_name: str = "",
    metadata: dict[str, Any] | None = None,
    stream_queue: asyncio.Queue[dict[str, Any]] | None = None,
    stream: bool = False,
) -> str:
    ...
```

`NanobotBridgePool` 必须继续按 `session_id > user_id > "_default"` 选择 child bridge，并把上述参数原样透传给 child `NanobotBridge`。

`metadata` 是当前 public contract 的扩展承载层。H29 不新增必填顶层参数，不重命名现有 metadata 字段。当前调用方依赖的字段包括但不限于：

- `chat_type`
- `platform`
- `user_id`
- `session_id`
- `sender_name`
- `sender_id`
- `session_name`
- `message_id`
- `source_message_ids`
- `files`
- `persona_text`
- `raw_query`
- `history_header`
- `history_messages`
- `is_group`
- `is_superuser`
- `group_id`
- `trigger_reason`
- `timing_decision`
- `context_debug`
- `self_id`
- `bot_id`
- `bot_name`
- `bot_aliases`
- `stream`
- `complexity`
- `effort_constraint`
- `runtime_preset`
- `trace_id`
- `prompt_runtime_engine_override`
- `enable_reply_contract_retry`
- `dry_run`

返回值仍为最终回复字符串。`stream_queue` 仅是流式事件侧通道，不替代返回值；`stream=True` 时仍要返回最终字符串，供 API 持久化、SSE `done` envelope 和断连后台 push 使用。

空字符串返回继续表示 no-reply、suppressed、audit-failure 或 empty 等不发送结果。调用方通过 `pop_last_reply_meta(session_id)` 读取 `_agent_result`、`_no_reply`、`_no_tool_call` 及发送控制字段来区分原因。

`pop_last_reply_meta(session_id)` 是 `handle_message()` 的配套外部契约，拆分后仍必须可在调用返回后使用，并保持弹出式语义：存在则返回 dict，不存在或 mock 不支持时调用方可视为 `None`。

## 审计结论

`handle_message()` 当前可以划分为 14 个阶段：

1. agent 初始化检查。
2. session lock 获取和 interrupt flag 设置。
3. trace/run 初始化与内部 `_finish_agent_trace()` 闭包。
4. output stream、reply runtime cache 初始化。
5. conversation reset 与 controller 残留状态清理。
6. runtime context、ToolPlan 和 executor session extra 准备。
7. Prompt Runtime 输入组装、audit failure 处理和 pre-event messages 应用。
8. 图片附件、multimodal event 与 required capabilities 准备。
9. reply route provider、NewAPIClient、registry sync 和候选模型生成。
10. vision 降级、兜底模型和 context budget 记录。
11. 模型候选 retry loop、LLM config 切换、process_event、失败记账和 rollback。
12. loop 后 response 汇总和 rich HTML 补提取。
13. reply contract 出口治理、structured fallback 和一次 retry repair。
14. 空响应、session lock cleanup、群聊 cooldown、stream final、工具恢复和 trace finish。

现有测试覆盖强的区域：

- stream 侧通道和 final replace。
- Prompt Runtime helper 和 live 路径。
- reply contract 多数分支。
- 图片能力路由、手动 reply model、provider sync。
- history metadata 注入到 Prompt Runtime 输入。

测试缺口：

- 候选模型 A 失败后记录 failure、rollback conversation、切换候选 B 成功。
- trace cleanup 幂等：多条 early return 路径下只能 finish 和 reset 一次。
- structured fallback 的 `no_reply` 分支可再补一个轻量用例。

## 方案对比

### 方案 A：模块内小步抽 helper

第一阶段只在 `nanobot_kt.bridge` 内新增 dataclass 和私有 helper。保持 patch 路径、导入路径和 public facade 不变。先抽无 contextvar 的低风险块，再补测试并拆模型 loop。

优点：

- 行为风险最低。
- 现有 monkeypatch 路径继续有效。
- 每一步都能用当前测试套件验证。
- 更适合按阶段 commit。

缺点：

- `bridge.py` 短期内仍然很大。
- 需要后续阶段继续拆，不能一次解决所有代码组织问题。

### 方案 B：按职责拆到新模块

直接创建 `nanobot_kt/bridge_runtime.py`、`bridge_routing.py`、`bridge_reply_contract.py` 等模块，把大型代码块搬出 `bridge.py`。

优点：

- 文件职责更清晰。
- 最终结构更接近理想状态。

缺点：

- 会破坏现有测试中大量 `nanobot_kt.bridge.NewAPIClient`、`nanobot_kt.bridge.registry`、`nanobot_kt.bridge.AsyncOpenAI` 等 monkeypatch 路径。
- 首次改动范围大，容易把路由、stream、reply contract 同时打断。
- 需要同步大量测试 patch 路径，噪声高。

### 方案 C：先加外层 orchestrator class

新增请求级 `HandleMessageRun` 或 `BridgeRequestContext` 对象，把所有局部变量移入对象，再逐步把方法搬过去。

优点：

- 能显式管理 trace tokens、tool plan tokens、route state 和 response state。
- 对后续拆模型 loop、reply contract 有帮助。

缺点：

- 第一步会触碰几乎所有局部变量。
- 如果没有先补 retry / cleanup 测试，行为回归不容易定位。
- 早期收益不如方案 A 明确。

## 推荐方案

采用方案 A。第一轮保持 helper 在 `nanobot_kt.bridge` 内，不改 public API，不迁移依赖模块。等模型 loop 和 reply contract 拆出稳定接口并有测试守护后，再评估是否进入方案 C 的请求上下文对象，最后才考虑跨文件拆分。

## 目标结构

`handle_message()` 保留为顺序编排入口，阶段名要能直接对应执行流程：

```python
async def handle_message(...):
    if not self._agent:
        return "Error: Agent not initialized"

    async with session_lock:
        trace = ...
        self._prepare_output_for_request(...)
        self._reset_request_conversation(...)
        runtime_state = self._prepare_runtime_tool_state(...)
        prompt_build = await self._build_and_apply_prompt_runtime(...)
        event_payload = await self._prepare_event_payload(...)
        route_state = await self._resolve_reply_route(...)
        model_result = await self._run_model_loop(...)
        reply = await self._check_reply_contract(...)
        return self._finalize_message_result(...)
```

第一阶段不强制完整达到上述形态。它只要求新增 helper 后，`handle_message()` 中对应代码块明显变短，且每个 helper 的输入输出边界清晰。

## 阶段拆分

### 阶段 1：低风险 helper 抽取

新增模块内 dataclass：

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
```

```python
@dataclass
class BridgeEventPayload:
    event_content: Any
    image_parts: list[Any]
    required_capabilities: dict[str, bool]
```

抽取 helper：

- `_prepare_output_for_request(stream_queue, stream_enabled) -> None`
- `_prepare_runtime_tool_state(meta, session_id, user_id_arg, sender_name) -> BridgeRuntimeToolState`
- `_prepare_event_payload(prompt_event_content, files, tool_plan) -> BridgeEventPayload`

边界说明：

- `meta["stream"]` 的计算仍留在 `handle_message()`。
- `final_tools_token` 和 `tool_plan_token` 仍回填给 `handle_message()` 局部变量，由现有 trace closure reset。
- `create_user_event(..., stream=meta["stream"])` 先留在主流程，避免影响 retry loop 的 event 重建。
- `UnitOfWork`、`build_tool_plan`、`record_runtime_tool_decision`、`set_current_final_tools` 和 `set_current_tool_plan` 的导入路径保持当前测试可 patch 的形态。

### 阶段 2：补模型 retry 测试并拆 `_run_model_loop`

先新增红灯测试，锁定以下行为：

- 候选 `model-a` 失败或空响应时调用 failure tracker。
- 失败后 rollback conversation，不把失败轮的 assistant/tool 残留带到下一轮。
- 候选 `model-b` 成功后调用 success tracker。
- 最终回复仍从 `reply()` tool 或 rich HTML 中提取。

然后抽：

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

```python
async def _run_model_loop(...) -> ModelLoopResult:
    ...
```

边界说明：

- 第一版仍在 `nanobot_kt.bridge` 内，继续使用现有 `process_event` adapter。
- 不绕过 `nanobot_kt.kt_adapter.process_event`。
- 不把 `NewAPIClient`、`registry`、`AsyncOpenAI` 迁移到新模块。
- LLM config 热切换、OpenAI tracer install、conversation rollback 和 failure tracker 必须保持原语义。

### 阶段 3：拆 reply contract 出口治理

在现有 `TestReplyContract` 基础上补 structured `no_reply` fallback 用例，然后抽：

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

```python
async def _check_reply_contract(...) -> ReplyResolution:
    ...
```

边界说明：

- `_extract_reply_from_tool_output()`、`_record_reply_contract_check()`、`_run_reply_contract_retry_once()`、`_parse_structured_final_action()` 等现有 helper 不改 public 行为。
- `reply_meta_store` 写入时机不变。
- no_reply / suppress 继续返回空字符串。
- reasoning content 仍不得外泄。

### 阶段 4：收敛 trace cleanup

在阶段 1-3 把 early return 数量压低后，再把 `_finish_agent_trace()` 从闭包收敛为请求级 finalizer。目标不是引入同步桥接，而是让 cleanup 的状态和幂等性更可测。

候选结构：

```python
@dataclass
class BridgeTraceFinalizer:
    run_id: str
    trace_tokens: Any
    run_meta: dict[str, Any]
    started_at: float
    closed: bool = False

    def finish(...):
        ...
```

边界说明：

- 只在 async `handle_message()` 内同步调用 `finish()`，不创建事件循环，不调用 `asyncio.run()`。
- `RunTracer.finish_run()`、`reset_current_final_tools()`、`reset_current_tool_plan()`、`reset_trace_context()` 和 `_restore_saved_tools()` 保持最多一次。

## 并行 agent 分工

H29 可以在实现阶段拆给多个子 agent，但必须保证写入范围互不冲突。

- Agent A：只负责模型 retry 红灯测试和 `_run_model_loop` 设计验证。写入范围建议为 `tests/test_kt_framework.py` 中新增 retry 用例，生产代码先只读。
- Agent B：只负责 reply contract 测试缺口盘点和 structured `no_reply` fallback 用例。写入范围建议为 `tests/test_kt_framework.py::TestReplyContract` 的新增用例。
- Agent C：只负责文档与计划同步，写入范围为 `.Codex/plans/h29-handle-message-refactor.md` 和 `docs/plan_walkthrough.md`。
- 主线程：负责 `nanobot_kt/bridge.py` 生产代码改动、集成审查、定向回归、全量测试和 commit。

如果需要多个 agent 同时写测试，必须按测试类或文件分配，避免同时编辑同一段代码。生产代码只由主线程或单一 worker 持有，不能多人同时改 `nanobot_kt/bridge.py`。

## 测试策略

阶段 1 定向门禁：

```bash
python -m pytest \
  tests/test_bridge_prompt_v2.py::test_bridge_build_prompt_runtime_input_maps_v2_alias_to_prompt \
  tests/test_bridge_prompt_v2.py::test_bridge_build_prompt_runtime_input_passes_platform \
  tests/test_bridge_prompt_v2.py::test_bridge_build_prompt_runtime_input_falls_back_when_tool_schemas_unavailable \
  tests/test_streaming_bridge.py \
  -q
```

模型路由门禁：

```bash
python -m pytest \
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

reply contract 门禁：

```bash
python -m pytest tests/test_kt_framework.py::TestReplyContract -q
```

Prompt Runtime live 门禁：

```bash
python -m pytest \
  tests/test_bridge_prompt_v2.py::test_bridge_engine_v2_uses_prompt_plan_for_conversation_and_user_event \
  tests/test_bridge_prompt_v2.py::test_bridge_engine_v2_uses_character_name_as_bot_name \
  tests/test_bridge_prompt_v2.py::test_bridge_engine_v2_fails_fast_when_prompt_audit_fails \
  tests/test_bridge_prompt_v2.py::test_bridge_engine_v2_ignores_fallback_v1_policy_when_audit_fails \
  tests/test_bridge_prompt_v2.py::test_bridge_tool_plan_does_not_mutate_registry_tools \
  -q
```

H29 阶段回归：

```bash
python -m pytest \
  tests/test_kt_framework.py::TestNanobotBridge \
  tests/test_kt_framework.py::TestReplyContract \
  tests/test_streaming_bridge.py \
  tests/test_bridge_prompt_v2.py \
  tests/test_history.py \
  -q
```

提交前仍必须运行：

```bash
python -m pytest tests/ -v
```

## 风险与约束

- 不要在第一阶段创建新模块并重新 import `NewAPIClient`、`registry` 或 `AsyncOpenAI`，否则现有 monkeypatch 路径会失效。
- 不要强依赖 `BufferedOutput` 具体类型，测试中存在 `MagicMock` output。
- conversation 消息读取必须继续兼容 dict、`SimpleNamespace` 和 KT Message 对象。
- Prompt Runtime 的函数调用必须保持运行时取模块函数，不能在 helper import 时缓存不可 patch 的引用。
- `stream` 是 Message 内部字段，不能进入 LLM wire payload。
- `stream_queue` 满队列策略由 `BufferedOutput` 维护，H29 不改变 progress 可丢弃、delta/final 保留的语义。
- 任何新增 async helper 都直接 `await`，不能添加同步 wrapper。

## 验收标准

- `handle_message()` 的每个抽取阶段都有清晰 helper 名称和数据对象。
- public signature、metadata 字段、stream 侧通道、返回值和 reply_meta 弹出语义均不变。
- 模型 retry 和 trace cleanup 至少各有一个针对性测试守住关键行为。
- `tests/test_kt_framework.py::TestNanobotBridge`、`TestReplyContract`、`tests/test_streaming_bridge.py`、`tests/test_bridge_prompt_v2.py` 和 `tests/test_history.py` 通过。
- 全量 `python -m pytest tests/ -v` 通过后才能提交实现阶段。
