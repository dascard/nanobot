# 普通 API 聊天落库拆分设计

日期：2026-06-22

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前仍只剩普通
`api/routes.py` 超过 800 行。前序已经完成 task、memory、models、evolution、
history / log、sticker / media、Agent Step / Render、group utility / legacy timing、
group message、chat content helper 和 chat response contract 拆分。

当前 `api/routes.py` 为 1604 行，剩余显式 route 只有：

- `POST /chat`
- `GET /health`

`/health` 只有极少行数，并且多个 split 测试把它作为父模块哨兵端点，不作为本轮优先目标。
`/chat` 主链路仍同时包含私聊缓冲、guardrail、TimingGate、Prompt Runtime 输入组装、
KT bridge 调用、SSE、断连后台 push、聊天落库、进化触发和响应 envelope。
完整迁移 `/chat` 仍然风险过高。

本轮三路只读审计的结论一致：

- 聊天落库 writer 是当前最清晰的下一刀边界。
- 私聊缓冲状态机依赖全局 `_private_buffers`、`_private_lock`、`_time.time()`、
  `asyncio.sleep()`、guardrail 预跑任务和 streaming finalizer；当前
  `_finalize_private_buffer(user_id)` 没有窗口身份，直接迁移会放大旧任务误操作新窗口的风险。
- Streaming runner / finalizer 是跨请求生命周期的异步状态机，绑定 bounded queue、
  `runner_task`、SSE 顺序、后台 `UnitOfWork`、push envelope、私聊 buffer finalization
  和 `_persist_chat_turn()` monkeypatch；不适合作为下一刀整体迁移。

因此本阶段设计下一步拆分 `api/routes.py` 中 `_persist_chat_turn()` 的实现主体，
但继续保留 `api.routes._persist_chat_turn()` 和 `api.routes._safe_meta()` 作为兼容门面。

## 目标

新增 `api/chat_persistence.py`，把聊天落库的同步写库逻辑从 `api/routes.py`
迁入独立模块，降低父模块职责密度，并让 ChatLog / ConversationTurn /
SensitiveData 的写入契约可单独测试。

本阶段迁移实现逻辑：

- `_safe_meta()` 的 JSON 容错解析。
- `source_message_ids` 归一化与 `message_id` 前置去重。
- guardrail 状态到落库内容与 `processed` 的映射。
- `SensitiveData` 写入。
- `ChatLog` 用户行与助手行写入。
- `ConversationTurn` 用户行与助手行写入。
- HTML / 超长助手回复的上下文摘要。
- `timing_meta` 与 `assistant_meta` 的 meta 合并。
- `run_sqlite_locked_retry(..., label="chat_turn_persist")`。
- `_evolution_running` 用户的 pending 计数短路，以及普通 pending 计数查询。

父模块 `api.routes` 继续保留：

- `_safe_meta()` wrapper。
- `_persist_chat_turn()` wrapper。
- `proxy_chat()` 的所有调用点和调用时机。
- `EVOLUTION_THRESHOLD` 判断与 `background_tasks.add_task(evolution_task, req.user_id)`。

## 非目标

- 不迁移 `proxy_chat()` 或 `POST /chat` 路由注册位置。
- 不迁移 `ChatProxyRequest`。
- 不迁移 `_private_buffers`、`_private_lock`、私聊缓冲窗口常量或私聊缓冲状态机。
- 不迁移 `_stream_chat()`、bounded queue、heartbeat、断连后台任务、push 和持久化幂等逻辑。
- 不迁移 `get_bridge`、`get_guardrail`、`get_timing_gate` 或旧父模块 monkeypatch 入口。
- 不修改 Prompt Runtime 模板、`enriched_query`、历史注入方式、conversation 结构或工具输出契约。
- 不修改 response envelope、SSE 事件格式、`answer_chunks` 或 QQ push 渲染逻辑。
- 不改变图片 token 传输层展开与数据库保存原始 answer 的边界。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 方案比较

### 方案 A：拆 `/health`

新增 `api/health_routes.py`，只迁移 `health_check()`。

优点：实现极小。缺点：净收益接近 0，并会主动修改多个父模块哨兵测试。
本阶段不采用。

### 方案 B：直接拆私聊缓冲状态机

新增 `api/chat_private_flow.py`，迁移 `_private_buffers`、`_finalize_private_buffer()`
和 `proxy_chat()` 中私聊缓冲分支。

优点：能减少较多行数。缺点：当前状态机没有窗口 handle / generation id，
旧后台任务和 follower timeout 都只按 `user_id` finalize；测试还直接 patch
`api.routes._time.time`、`api.routes.asyncio.sleep`，并断言裸 dict 的
`window_seconds`、`deadline` 字段。本阶段不采用。

### 方案 C：直接拆 Streaming runner / finalizer

新增 `api/chat_streaming.py`，迁移 `_stream_chat()`、runner、queue drain 和后台 push。

优点：行数收益最大之一。缺点：该逻辑同时管理 `bridge.handle_message()` 生命周期、
bounded queue 背压、SSE 顺序、断连后台 `UnitOfWork`、push envelope、私聊 buffer
finalization、Prompt Runtime audit failure 和 `_persist_chat_turn()` spy。
整体迁移会破坏多个 monkeypatch 入口。本阶段不采用。

### 方案 D：拆聊天落库 writer（推荐）

新增 `api/chat_persistence.py` 承载同步落库实现，父模块保留 `_safe_meta()` 和
`_persist_chat_turn()` wrapper。`proxy_chat()` 继续调用父模块全局名称，确保
`monkeypatch.setattr("api.routes._persist_chat_turn", ...)` 仍覆盖真实执行路径。

优点：边界清晰，副作用集中在同步 DB session；不触碰私聊缓冲和 streaming runner；
可以补齐 `SensitiveData`、HTML 摘要、source ids、timing meta 和 pending 计数测试。
缺点：仍需谨慎保留父模块 `__module__ == "api.routes"` 断言和旧调用路径。

## 选定设计

采用方案 D，新增 `api/chat_persistence.py`。

### 输入模型

新模块不导入 `api.routes`，也不导入 `ChatProxyRequest`。由父模块 wrapper 将
`ChatProxyRequest` 适配成独立输入模型：

```python
@dataclass(frozen=True)
class ChatTurnPersistenceInput:
    user_id: str
    session_id: str
    query: str
    files: list[str] | None = None
    sender_name: str | None = None
    session_name: str | None = None
    message_id: str | None = None
    source_message_ids: list[str] | None = None
    client_meta: dict[str, Any] | None = None
```

该类型只表达落库所需字段，不承担请求校验、HTTP 语义或 Prompt Runtime 输入组装。

### 新模块函数

`api/chat_persistence.py` 暴露：

```python
def safe_meta(meta_json: str | None) -> dict[str, Any]:
    ...

def persist_chat_turn(
    db: Session,
    req: ChatTurnPersistenceInput,
    answer: str,
    guardrail_status: str | None = None,
    *,
    assistant_meta: dict[str, Any] | None = None,
    assistant_processed: int | None = None,
    timing_meta: dict[str, Any] | None = None,
) -> int:
    ...
```

模块内部可使用私有 helper 分解：

- `_source_message_ids_json(req)`
- `_chat_turn_answer(answer, guardrail_status)`
- `_chatlog_and_context_user_content(req, guardrail_status)`
- `_user_meta(req, timing_meta)`
- `_assistant_meta(answer_kind, assistant_meta, timing_meta)`

这些私有 helper 不注册路由、不创建任务、不访问 `api.routes`。

### 父模块 wrapper

`api/routes.py` 保留旧入口：

```python
def _safe_meta(meta_json: str) -> dict:
    return chat_persistence.safe_meta(meta_json)

def _persist_chat_turn(
    db: Session,
    req: ChatProxyRequest,
    answer: str,
    guardrail_status: str | None = None,
    *,
    assistant_meta: dict | None = None,
    assistant_processed: int | None = None,
    timing_meta: dict | None = None,
) -> int:
    return chat_persistence.persist_chat_turn(
        db,
        chat_persistence.ChatTurnPersistenceInput(
            user_id=req.user_id,
            session_id=req.session_id,
            query=req.query,
            files=req.files,
            sender_name=req.sender_name,
            session_name=req.session_name,
            message_id=req.message_id,
            source_message_ids=req.source_message_ids,
            client_meta=req.client_meta,
        ),
        answer,
        guardrail_status,
        assistant_meta=assistant_meta,
        assistant_processed=assistant_processed,
        timing_meta=timing_meta,
    )
```

父模块 wrapper 使用函数定义而不是简单 re-export，使
`routes._persist_chat_turn.__module__ == "api.routes"` 和
`routes._safe_meta.__module__ == "api.routes"` 继续成立。

`proxy_chat()`、`_stream_chat()` 和断连后台 finalizer 继续调用父模块 `_persist_chat_turn()`。
这样现有 stream 断连测试的 spy 能继续观察后台落库是否使用新的 DB session。

## 行为契约

### 写入行数

普通一轮落库写入：

- 1 条 user `ChatLog`
- 1 条 assistant `ChatLog`
- 1 条 user `ConversationTurn`
- 1 条 assistant `ConversationTurn`

`guardrail_status == "silent"` 时额外写入 1 条 `SensitiveData`。

### 用户内容

user `ChatLog.content` 使用完整归档文本：

- 文本保留原文。
- 图片附件保留数量和最多 3 个文件引用。

user `ConversationTurn.content` 使用上下文文本：

- 文本保留原文。
- 图片附件只保留数量摘要，不写文件引用。

该差异继续复用 `api.chat_content_helpers.build_chatlog_user_content()` 和
`api.chat_content_helpers.build_conversation_user_content()`。

### Guardrail 状态

`guardrail_status == "silent"`：

- `SensitiveData.content` 保存完整归档文本。
- user `ChatLog.content` 写 `[敏感数据]`。
- user `ConversationTurn.content` 写 `[敏感数据]`。
- 默认 `processed=0`。

`guardrail_status == "injection"`：

- user `ChatLog.content` 写 `[安全提示: 检测到注入已被拦截]`。
- user `ConversationTurn.content` 写 `[安全提示: 检测到注入已被拦截]`。
- 不写 `SensitiveData`。
- user 与 assistant 默认 `processed=-1`。

`guardrail_status == "casual_template"`：

- assistant `ConversationTurn.meta_json.kind` 为 `casual_template`。
- 其余写入结构与普通回复一致。

普通回复：

- user meta 强制 `kind="chat"`。
- assistant `ConversationTurn.meta_json.kind` 默认为 `chat`。

### 助手内容

assistant `ChatLog.content` 始终保存原始 `answer`。

assistant `ConversationTurn.content`：

- HTML / artifact 类回答写 `[HTML报告: 已渲染为图片/HTML，N字符]`。
- HTML / artifact 类回答的 `kind` 为 `artifact_summary`。
- 超过 2000 字的非 HTML 回答保存前 2000 字并追加截断标记。
- 其他回答保存原文。

HTML 识别前缀保持现有集合：

- `<!doctype`
- `<html`
- `<head`
- `<body`
- `<article`
- `<style`

### Meta 合并

user meta：

- 从 `client_meta` JSON 容错解析得到 dict。
- 强制写入 `kind="chat"`。
- 如果传入 `timing_meta`，写入 `timing_gate`。

assistant `ConversationTurn.meta_json`：

- 先写入 `kind`。
- 如果传入 `timing_meta`，写入 `timing_gate`。
- 如果传入 `assistant_meta`，再合并到同一 dict。

assistant `ChatLog.meta_json`：

- 默认来自 `assistant_meta`。
- 如果传入 `timing_meta`，写入 `timing_gate`。

Prompt Runtime audit failure 仍由父模块 `_private_prompt_audit_failure_meta()` 生成
`kind="empty_reply"`、`no_context=True`、`no_send=True`、
`agent_result="prompt_v2_audit_failed"`，并通过 `assistant_meta` 传入 writer。
writer 只负责持久化这些 meta，不判断 audit failure 分支。

### Source IDs

`source_message_ids_json` 必须继续写到 user `ChatLog` 和 user `ConversationTurn`。

规则：

- 从 `req.source_message_ids` 复制列表。
- 如果 `req.message_id` 非空且列表中没有该值，则插入到列表首位。
- 如果 `req.message_id` 已存在，则不重复写入。
- 列表为空时写 `"[]"`。

### SQLite Retry

DB 写入必须继续包在：

```python
run_sqlite_locked_retry(
    operation,
    rollback=db.rollback,
    label="chat_turn_persist",
    logger=logger,
)
```

`operation()` 内部继续负责 `db.add(...)` 和 `db.commit()`。

### Pending 计数

`persist_chat_turn()` 返回 pending count，语义保持不变：

- 写入完成后，如果 `req.user_id in core.evolution._evolution_running`，返回 `0`。
- 否则返回该用户 `ChatLog.processed == 0` 的数量。

`EVOLUTION_THRESHOLD` 比较与 `background_tasks.add_task(evolution_task, req.user_id)`
继续由 `proxy_chat()` 管理，不放入 writer 模块。

## 兼容性约束

- `api/chat_persistence.py` 不包含 `from api.routes` 或 `import api.routes`。
- `api/chat_persistence.py` 不包含 `asyncio.run` 或 `run_awaitable_sync`。
- `api.routes._persist_chat_turn.__module__` 仍为 `"api.routes"`。
- `api.routes._safe_meta.__module__` 仍为 `"api.routes"`。
- `proxy_chat()` 的所有 `_persist_chat_turn()` 调用点继续调用父模块 wrapper。
- stream 断连后台路径继续允许 `monkeypatch.setattr("api.routes._persist_chat_turn", ...)`
  观察真实执行。
- 新模块不创建 async task、不调用 bridge、不发送 push、不触发 evolution。
- 新模块不改变 `ChatProxyRequest`、HTTP 请求字段、响应结构或 SSE 结构。

## 测试策略

新增 `tests/test_api_chat_persistence_split.py`，覆盖：

- 新模块文件存在，且不反向导入 `api.routes`。
- 新模块源码不包含 `asyncio.run` 或 `run_awaitable_sync`。
- 父模块 `_persist_chat_turn()` 与 `_safe_meta()` wrapper 的 `__module__` 仍为 `"api.routes"`。
- `api.routes._persist_chat_turn()` 与 `api.chat_persistence.persist_chat_turn()` 输出行为一致。
- `api.routes._safe_meta()` 与 `api.chat_persistence.safe_meta()` 输出行为一致。
- silent 分支保存 `SensitiveData`，同时 `ChatLog` / `ConversationTurn` 不暴露原文。
- injection 分支写安全提示，user / assistant `processed=-1`，且不写 `SensitiveData`。
- HTML 回答在 `ChatLog` 保留完整原文，在 `ConversationTurn` 写摘要并标记 `artifact_summary`。
- `source_message_ids_json` 前置 `message_id` 且不重复。
- Prompt Runtime audit failure meta 被持久化，assistant `processed=1`。
- `timing_meta` 写入 user / assistant 的 `ChatLog` 和 `ConversationTurn` meta。
- `_evolution_running` 中的用户落库后 pending count 返回 `0`。

扩展已有相邻 split 测试：

- 将 `api/chat_persistence.py` 加入禁用模式扫描。
- 保持 `_persist_chat_turn()` 和 `_safe_meta()` 父模块哨兵断言。

继续运行既有回归：

- `tests/test_tracing_sqlite_retry.py::test_chat_turn_persist_retries_sqlite_locked_commit`
- `/chat` Prompt Runtime audit failure 相关 nodeid
- `/chat` 私聊 timing meta 相关 nodeid
- `/chat` stream disconnect 相关 nodeid
- `tests/test_api_chat_helpers_split.py`
- `tests/test_asyncio_run_policy.py`

## 验证计划

红灯：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_chat_persistence_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py
```

预期失败点集中在 `api/chat_persistence.py` 尚不存在、父模块尚未委托新模块、
新模块源码扫描失败。

绿灯：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_persistence_split.py
```

相邻回归：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_chat_persistence_split.py \
  tests/test_api_chat_helpers_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_tracing_sqlite_retry.py \
  tests/test_asyncio_run_policy.py
```

`/chat` 行为回归：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder \
  tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder \
  tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send \
  tests/test_api.py::test_private_prompt_v2_audit_failure_is_not_context_chat \
  tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta \
  tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta \
  tests/test_api.py::test_private_buffer_refreshes_window_and_persists_merged_messages \
  tests/test_api.py::test_private_buffer_merges_files_for_final_bridge_request
```

静态检查：

```bash
python -B -m py_compile api/routes.py api/chat_persistence.py tests/test_api_chat_persistence_split.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/chat_persistence.py
git diff --check -- api/routes.py api/chat_persistence.py tests/test_api_chat_persistence_split.py
```

最终全量：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

## 子 Agent 分工建议

实现阶段可以并行分派互不冲突的任务：

- Worker A：只负责 `tests/test_api_chat_persistence_split.py` 红灯测试，不改生产代码。
- Worker B：只负责只读复核 `_persist_chat_turn()` 调用点和相邻 split 测试更新点。
- 主线程：审查红灯测试、实现 `api/chat_persistence.py`、修改 `api/routes.py` wrapper、
  运行验证并提交。

不建议多个 worker 同时编辑 `api/routes.py` 或同一个测试文件，避免拆分阶段产生冲突。

## 验收清单

- [ ] `api/chat_persistence.py` 承载落库实现，且不反向导入父模块。
- [ ] `api.routes._persist_chat_turn()` 和 `_safe_meta()` 仍是父模块 wrapper。
- [ ] `proxy_chat()` 的调用点仍走父模块 wrapper。
- [ ] silent / injection / HTML / audit failure / timing meta / source ids / pending count 契约均有测试。
- [ ] SQLite locked retry 仍使用 `chat_turn_persist` label。
- [ ] 新模块没有 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- [ ] 定向回归、相邻回归、静态检查和全量回归均通过后再提交实现。
