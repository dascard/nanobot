# 普通 API Chat Push Envelope 拆分设计

日期：2026-06-23

## 背景

`docs/todo.md` 中 P3「超大文件 >800 行拆分」当前仍剩
`api/routes.py`，第十七刀 Chat Private Buffer 基础件拆分后文件为
1351 行。剩余显式路由主要是 `/chat` 和 `/health`，其中 `/health` 收益很低，
继续推进应围绕 `/chat` 主链路做细粒度拆分。

本轮只读审计比较了三个候选边界：

- 私聊缓冲 flow：可以拆，但会同时暴露 deadline 缩短无法主动唤醒 owner 的时序风险，
  更像「时序修复 + 拆分」阶段。
- streaming finalizer：可以拆 `_persist_stream_result_after_runner_done()` 小内核，
  但绑定 DB、`UnitOfWork`、后台任务、runner task 和 queue drain 生命周期。
- Chat push envelope：只抽断连后台 push envelope 纯组装和传输层图片展开，行为面最窄，
  适合作为下一刀。

## 目标

新增 `api/chat_push_envelope.py`，承载 `/chat` 断连后台 push 所需的纯组装 helper：

- `ChatPushEnvelope`：包含 `target_type`、`target_id` 和标准响应信封。
- `build_chat_push_envelope()`：根据 `ChatProxyRequest`、最终传输文本、平台、
  会话类型和 `is_group` 构造 push 目标与 envelope。
- `expand_chat_transport_answer()`：封装 `expand_generated_image_refs_in_content(..., allow_base64=False)`，
  明确这是传输层展开，不影响数据库持久化的原始 answer。

`api.routes` 中断连后台 push 分支继续决定是否 push、何时 push、如何记录日志；
只把手写 envelope meta 和图片 token 展开委托给新模块。

## 非目标

- 不迁移 `proxy_chat()`。
- 不迁移 `_stream_chat()`、`StreamingResponse`、SSE 主循环或 heartbeat 逻辑。
- 不迁移 `_persist_stream_result_after_runner_done()` 整体。
- 不改变 `runner_task`、bounded queue drain、`UnitOfWork`、DB 持久化或后台任务语义。
- 不改变非流式 HTTP response、流式 done payload、message envelope 字段兼容策略。
- 不迁移 `push_envelope_to_qq()` 调用点；调用仍由 `api.routes` 根据 `should_push` 决定。
- 不改变 Prompt Runtime 模板、`enriched_query`、conversation 结构或工具输出契约。
- 不引入 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 接口设计

### `ChatPushEnvelope`

```python
@dataclass(frozen=True)
class ChatPushEnvelope:
    target_type: str
    target_id: str
    envelope: dict[str, Any]
```

### `build_chat_push_envelope()`

```python
def build_chat_push_envelope(
    req: ChatProxyRequest,
    *,
    answer: str,
    platform: str,
    chat_type: str,
    is_group: bool,
    status: str = "ok",
    reply_meta: Mapping[str, Any] | None = None,
    extra_meta: Mapping[str, Any] | None = None,
) -> ChatPushEnvelope:
    ...
```

行为：

- `target_type` 为 `"group"` 或 `"private"`。
- `target_id` 通过 `api.chat_request_contract.resolve_push_target_id(req, is_group)` 解析，
  避免在新模块重新实现群号 / 私聊 ID 规则。
- envelope 通过 `core.message_envelope.build_chat_response_envelope()` 构造。
- meta 至少包含 `platform`、`chat_type`、`user_id`、`session_id`、`target_type` 和 `target_id`。
- `extra_meta` 只作为附加字段合并，不覆盖上述目标字段。

### `expand_chat_transport_answer()`

```python
def expand_chat_transport_answer(answer: str) -> str:
    ...
```

行为：

- 调用 `core.generated_images.expand_generated_image_refs_in_content(answer, allow_base64=False)`。
- 展开失败时返回原文，并允许调用方记录 warning。
- 不修改 DB 持久化使用的原始 answer。

## 父模块保留点

`api.routes` 继续保留以下 patch point：

- `proxy_chat()` 和 `/chat` 路由注册。
- `_stream_chat()`、`StreamingResponse`、SSE event 输出和 `persisted` 标记。
- `_persist_stream_result_after_runner_done()` 调度点和主体生命周期。
- `push_envelope_to_qq()` 调用点。
- `_persist_chat_turn()`、`_finalize_private_buffer()`、`_private_prompt_audit_failure_meta()`、
  `_resolve_push_target_id()` 等父模块 wrapper。
- `get_bridge()`、Bridge reply meta 消费和 `BackgroundTasks.add_task()`。
- 非流式 `transport_answer` 和流式 done `transport_answer` 的调用位置可以委托
  `expand_chat_transport_answer()`，但是否输出、何时落库仍由父模块控制。

## 测试策略

新增 `tests/test_api_chat_push_envelope_split.py`：

- 静态边界：`api/chat_push_envelope.py` 不导入 `api.routes`，不包含
  `asyncio.run` 或 `run_awaitable_sync`。
- 私聊 push envelope：断言 `target_type`、`target_id`、`reply`、`messages`、
  `meta.user_id`、`meta.session_id`、`meta.platform`、`meta.chat_type`、
  `meta.target_type` 和 `meta.target_id`。
- 群聊 push envelope：覆盖 `group_987654` 和裸 `987654` 两种 session id。
- 传输层展开：断言调用 `expand_generated_image_refs_in_content(..., allow_base64=False)`。
- 父模块 wrapper / patch point：断言新增 wrapper 仍属于 `api.routes`，不移除
  `_chat_response_payload()` 等既有父模块 facade。

更新普通 API split 扫描测试：

- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

更新断连 push 集成测试：

- `tests/test_api_push_envelope.py` 补充断言 envelope meta 中的 `user_id`、
  `session_id`、`target_type` 和 `target_id`。

保持以下回归作为最终验证：

- `tests/test_api_chat_push_envelope_split.py`
- `tests/test_api_push_envelope.py`
- `tests/test_chat_response_envelope.py`
- `tests/test_streaming_response_envelope.py`
- 断连后台相关 `/chat` 测试
- `tests/test_asyncio_run_policy.py`
- 全量 `python -B -m pytest -p no:cacheprovider tests/ -v`

## 风险与控制

- 风险：push envelope meta 与 HTTP response meta 分叉。
  控制：新模块集中构造 push meta，并用单测固定字段。
- 风险：图片 token 展开误用于 DB 持久化。
  控制：helper 命名为 transport answer，父模块仍用原始 `answer` 落库。
- 风险：绕过父模块 monkeypatch。
  控制：新模块不导入 `api.routes`，通过 `ChatProxyRequest` 和公共 helper 完成纯组装。
- 风险：误迁移流式生命周期。
  控制：设计明确不迁移 `_stream_chat()` 和 `_persist_stream_result_after_runner_done()`。

## 验收标准

- `api/routes.py` 中断连后台 push envelope 手写 meta 被替换为新模块调用。
- 新模块不反向导入 `api.routes`，也没有 `asyncio.run` 或 `run_awaitable_sync`。
- 非流式与流式 done response envelope 契约不变。
- 断连后台 push 继续使用标准 envelope，且 `allow_base64=False` 的图片展开语义不变。
- 精确回归和全量测试通过。
