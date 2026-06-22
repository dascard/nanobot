# 普通 API 聊天请求契约拆分设计

日期：2026-06-22

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前仍只剩普通
`api/routes.py` 超过 800 行。前序已经完成 task、memory、models、evolution、
history / log、sticker / media、Agent Step / Render、group utility / legacy timing、
group message、chat content helper、chat response contract 和 chat persistence 拆分。

当前 `api/routes.py` 为 1516 行，剩余显式 route 只有：

- `POST /chat`
- `GET /health`

`/health` 仍只有极少行数，并且多个 split 测试把它作为父模块哨兵端点，不作为本轮优先目标。
`/chat` 主链路仍同时包含私聊缓冲、guardrail、TimingGate、Prompt Runtime 输入组装、
KT bridge 调用、SSE、断连后台 push、聊天落库、进化触发和响应 envelope。
完整迁移 `/chat` 仍然风险过高。

本轮三路只读审计的结论是：

- 私聊缓冲状态机可以拆，但必须先补更强测试。它绑定 `_private_buffers`、
  `_private_lock`、`asyncio.sleep()`、`_time.time()`、follower 释放、
  取消清理和裸 dict 窗口结构。
- Streaming finalizer / 断连后台路径可以拆，但必须先补更强测试。它绑定
  bounded queue、SSE 顺序、后台 `UnitOfWork`、push envelope、断连 drain
  和父模块 `_persist_chat_turn()` spy。
- 低风险边界优先推荐 `ChatProxyRequest` 与请求元信息 helper。它们是纯请求契约与同步
  helper，不直接创建任务、不管理跨请求状态，也不触碰 Prompt Runtime 模板。

因此本阶段设计下一步拆分 `api/routes.py` 中聊天请求模型和请求元信息 helper 的实现主体，
但继续保留 `api.routes` 的旧导入面和 wrapper，保证现有测试与 monkeypatch 入口不变。

## 目标

新增 `api/chat_request_contract.py`，把 `/chat` 请求契约与请求元信息 helper
从 `api/routes.py` 迁入独立模块，降低父模块职责密度，并让请求 clone、平台识别、
chat type 识别、client meta 归一化和私聊辅助 meta 的行为可单独测试。

本阶段迁移实现逻辑：

- `ChatProxyRequest` Pydantic model。
- `_clone_chat_request()` 的完整字段 clone。
- `_resolve_push_target_id()` 的私聊 / 群聊目标 ID 解析。
- `_extract_group_id_from_chat_request()` 的群号提取。
- `_chat_request_platform()` 的 platform 默认值与归一化。
- `_chat_request_type()` 的 private / group 判定。
- `_normalize_request_client_meta()` 的 `core.client_meta` 调用与 HTTP 400 映射。
- `_private_prompt_audit_failure_meta()` 的空回复审计 meta。
- `_private_timing_meta()` 的 TimingGate 决策 meta 提取。

父模块 `api.routes` 继续保留：

- `ChatProxyRequest` 可导入。
- 上述所有 `_xxx` helper 的父模块 wrapper。
- `proxy_chat()` 和 `/chat` 路由注册位置。
- `/health` 路由注册位置。

## 非目标

- 不迁移 `proxy_chat()` 或 `POST /chat` 路由注册位置。
- 不迁移 `_private_buffers`、`_private_lock`、私聊缓冲窗口常量或私聊缓冲状态机。
- 不迁移 `_stream_chat()`、bounded queue、heartbeat、断连后台任务、push 和持久化幂等逻辑。
- 不迁移 `get_bridge`、`get_guardrail`、`get_timing_gate` 或旧父模块 monkeypatch 入口。
- 不修改 `chat_response_contract`、response envelope、SSE 事件格式或 `answer_chunks`。
- 不修改 `chat_persistence`、`_persist_chat_turn()` 调用时机或聊天落库语义。
- 不修改 Prompt Runtime 模板、`enriched_query`、历史注入方式、conversation 结构或工具输出契约。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 方案比较

### 方案 A：拆私聊缓冲状态机

新增 `api/chat_private_buffer.py`，迁移 `_private_buffers`、`_finalize_private_buffer()`
和 `proxy_chat()` 中私聊缓冲分支。

优点：行数收益较大。缺点：当前状态机没有窗口 handle / generation id，
旧后台任务和 follower timeout 都只按 `user_id` finalize；测试还 patch
`api.routes._time.time`、`api.routes.asyncio.sleep`，并断言裸 dict 的
`window_seconds`、`deadline` 字段。本阶段不采用。

### 方案 B：拆 Streaming finalizer / 断连后台路径

新增 `api/chat_streaming.py`，迁移 `_stream_chat()`、runner、queue drain 和后台 push。

优点：行数收益较大。缺点：该逻辑同时管理 `bridge.handle_message()` 生命周期、
bounded queue 背压、SSE 顺序、断连后台 `UnitOfWork`、push envelope、私聊 buffer
finalization、Prompt Runtime audit failure 和 `_persist_chat_turn()` spy。
整体迁移会破坏多个 monkeypatch 入口。本阶段不采用。

### 方案 C：拆 runtime / guardrail facade

新增 `api/chat_runtime_facade.py`，迁移 `get_bridge`、guardrail、TimingGate
和部分运行时组合逻辑。

优点：能给后续 `/chat` 主流程瘦身。缺点：容易触碰 Prompt Runtime 输入组装、
`enriched_query`、历史注入和 guardrail 预跑时机；这些行为需要更完整的集成测试保护。
本阶段不优先采用。

### 方案 D：拆聊天请求契约与请求元信息 helper（推荐）

新增 `api/chat_request_contract.py` 承载请求模型与纯 helper 实现，父模块保留
`ChatProxyRequest` re-export 和 `_xxx` wrapper。`proxy_chat()` 继续调用父模块全局名称，
确保 `monkeypatch.setattr("api.routes._normalize_request_client_meta", ...)` 等旧入口
仍能覆盖真实执行路径。

优点：边界清晰，不触碰异步状态机、数据库、Prompt Runtime 或 response envelope；
可以用小范围测试锁定父模块导入面、wrapper `__module__` 和请求字段完整性。
缺点：行数收益小于状态机拆分，但风险明显更低，适合作为继续压缩父模块的下一刀。

## 选定设计

采用方案 D，新增 `api/chat_request_contract.py`。

### 新模块职责

`api/chat_request_contract.py` 负责：

```python
class ChatProxyRequest(BaseModel):
    ...

def clone_chat_request(req: ChatProxyRequest, **updates) -> ChatProxyRequest:
    ...

def resolve_push_target_id(req: ChatProxyRequest, is_group: bool) -> str:
    ...

def extract_group_id_from_chat_request(req: ChatProxyRequest) -> str:
    ...

def chat_request_platform(req: ChatProxyRequest) -> str:
    ...

def chat_request_type(req: ChatProxyRequest) -> str:
    ...

def normalize_request_client_meta(req: Any, *, expected_chat_type: str) -> dict[str, Any]:
    ...

def private_prompt_audit_failure_meta() -> dict[str, Any]:
    ...

def private_timing_meta(decision: Any | None) -> dict[str, Any] | None:
    ...
```

新模块可以导入：

- `typing.Any`
- `pydantic.BaseModel`
- `fastapi.HTTPException`
- `core.client_meta.ClientMetaValidationError`
- `core.client_meta.normalize_client_meta`

新模块不能导入：

- `api.routes`
- `asyncio`
- `run_awaitable_sync`
- 数据库模型或 SQLAlchemy session
- KT bridge、guardrail、TimingGate provider

### 请求模型字段

`ChatProxyRequest` 字段和默认值必须保持不变：

```python
user_id: str = "default_user"
session_id: str = "default_session"
query: str = ""
files: Optional[List[str]] = None
sender_name: Optional[str] = None
session_name: Optional[str] = None
stream: bool = False
classification_request: bool = False
merged_messages: list[str] | None = None
message_id: str | None = None
source_message_ids: list[str] | None = None
client_meta: dict | None = None
```

`stream` 字段仍属于请求契约，不在本阶段改动流式传输行为。

### 父模块兼容门面

`api/routes.py` 保留旧入口：

```python
from api import chat_request_contract

ChatProxyRequest = chat_request_contract.ChatProxyRequest

def _clone_chat_request(req: ChatProxyRequest, **updates) -> ChatProxyRequest:
    return chat_request_contract.clone_chat_request(req, **updates)

def _resolve_push_target_id(req: ChatProxyRequest, is_group: bool) -> str:
    return chat_request_contract.resolve_push_target_id(req, is_group)

def _extract_group_id_from_chat_request(req: ChatProxyRequest) -> str:
    return chat_request_contract.extract_group_id_from_chat_request(req)

def _chat_request_platform(req: ChatProxyRequest) -> str:
    return chat_request_contract.chat_request_platform(req)

def _chat_request_type(req: ChatProxyRequest) -> str:
    return chat_request_contract.chat_request_type(req)

def _normalize_request_client_meta(req: Any, *, expected_chat_type: str) -> dict[str, Any]:
    return chat_request_contract.normalize_request_client_meta(
        req,
        expected_chat_type=expected_chat_type,
    )

def _private_prompt_audit_failure_meta() -> dict:
    return chat_request_contract.private_prompt_audit_failure_meta()

def _private_timing_meta(decision: Any | None) -> dict[str, Any] | None:
    return chat_request_contract.private_timing_meta(decision)
```

父模块 wrapper 使用函数定义而不是简单 re-export，使下列断言继续成立：

- `routes._clone_chat_request.__module__ == "api.routes"`
- `routes._resolve_push_target_id.__module__ == "api.routes"`
- `routes._extract_group_id_from_chat_request.__module__ == "api.routes"`
- `routes._chat_request_platform.__module__ == "api.routes"`
- `routes._chat_request_type.__module__ == "api.routes"`
- `routes._normalize_request_client_meta.__module__ == "api.routes"`
- `routes._private_prompt_audit_failure_meta.__module__ == "api.routes"`
- `routes._private_timing_meta.__module__ == "api.routes"`

`proxy_chat()` 内部继续调用父模块 `_xxx` 名称。这样现有或新增测试通过 monkeypatch
父模块 helper 时，仍能覆盖真实执行路径。

## 行为契约

### clone 语义

`clone_chat_request(req, **updates)` 必须使用 Pydantic v2 的 `model_dump()`；
如果运行环境是 Pydantic v1 测试桩，则回退到 `dict()`。clone 后必须保留所有请求字段，
尤其是 `client_meta`、`source_message_ids`、`merged_messages`、`message_id` 和 `stream`。

### target / group 解析

`resolve_push_target_id(req, is_group=False)` 返回 `req.user_id`。

`resolve_push_target_id(req, is_group=True)` 的顺序保持：

- `session_id` 以 `group_` 开头时，返回去掉前缀后的群号。
- 否则返回非空 `session_id`。
- `session_id` 为空时回退到 `req.user_id`。

`extract_group_id_from_chat_request(req)` 保持同样的 `group_` 前缀剥离和 user fallback 语义。

### platform / chat type

`chat_request_platform(req)` 只读取 dict 类型的 `client_meta`，默认返回 `"qq"`。
返回值必须 `strip().lower()`，空字符串继续回退到 `"qq"`。

`chat_request_type(req)` 保持现有判断：`session_id` 以 `private_` 开头时返回
`"private"`，否则返回 `"group"`。

### client meta 归一化

`normalize_request_client_meta(req, expected_chat_type=...)` 调用
`core.client_meta.normalize_client_meta()`。当 `core.client_meta` 抛出
`ClientMetaValidationError` 时，必须映射为 `HTTPException(400, "invalid client_meta: ...")`。

成功归一化后必须回写 `req.client_meta = normalized`，并返回 `normalized`。

### 私聊辅助 meta

`private_prompt_audit_failure_meta()` 必须保持精确输出：

```python
{
    "kind": "empty_reply",
    "no_context": True,
    "no_send": True,
    "agent_result": "prompt_v2_audit_failed",
}
```

`private_timing_meta(decision)` 的行为保持：

- `decision is None` 时返回 `None`。
- `decision.timing_scoring` 不是 dict 时返回 `None`。
- 有效时返回 `mode`、`action`、`reason`、`effort`、`runtime_preset`、`scoring`。
- 除 `scoring` 外，其余字段都使用 `str(... or "")`。

## 测试计划

新增 `tests/test_api_chat_request_contract_split.py`，先写红灯测试，再迁移实现。

测试覆盖：

- `api/chat_request_contract.py` 不导入 `api.routes`。
- 新模块不包含 `asyncio.run` 或 `run_awaitable_sync`。
- `routes.ChatProxyRequest` 仍可导入，字段默认值保持不变。
- 父模块 8 个 wrapper 的 `__module__` 仍是 `"api.routes"`。
- `_clone_chat_request()` 不丢 `client_meta`、`source_message_ids`、`merged_messages`、
  `message_id` 和 `stream`。
- `_resolve_push_target_id()` 保持 private、`group_987654` 和空 session fallback 语义。
- `_extract_group_id_from_chat_request()` 保持群号提取与 user fallback 语义。
- `_chat_request_platform()` 保持默认 `"qq"`、小写归一化和非 dict meta fallback。
- `_chat_request_type()` 保持 `private_` 前缀判定。
- `_normalize_request_client_meta()` 在 chat type 冲突时抛 `HTTPException` 且状态码为 400。
- `_normalize_request_client_meta()` 成功时回写 `req.client_meta`。
- `_private_prompt_audit_failure_meta()` 精确匹配现有 dict。
- `_private_timing_meta(None)` 返回 `None`。
- `_private_timing_meta()` 在 scoring 非 dict 时返回 `None`。
- `_private_timing_meta()` 在 scoring 为 dict 时输出完整 meta。

同时扩展已有 split 源码扫描，把 `api/chat_request_contract.py` 纳入禁止模式：

- 不导入父模块 `api.routes`。
- 不新增 `asyncio.run`。
- 不新增 `run_awaitable_sync`。

## 验证计划

文档阶段验证：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' \
  docs/superpowers/specs/2026-06-22-api-chat-request-contract-split-design.md
git diff --check -- \
  docs/superpowers/specs/2026-06-22-api-chat-request-contract-split-design.md
```

测试阶段验证：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_request_contract_split.py -v
```

实现阶段验证：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_request_contract_split.py -v
python -B -m pytest -p no:cacheprovider tests/test_api_chat_persistence_split.py -v
python -B -m pytest -p no:cacheprovider tests/ -v
```

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 父模块 wrapper 被简单 re-export，破坏 `__module__` 断言 | 测试显式锁定 8 个 wrapper 的 `__module__` |
| `proxy_chat()` 直接调用新模块，破坏父模块 monkeypatch 入口 | 设计要求 `proxy_chat()` 继续调用父模块 `_xxx` 名称 |
| `ChatProxyRequest` 字段遗漏，导致 QQbot 或 stream 请求丢参数 | 测试锁定字段默认值与 clone 后字段保留 |
| `client_meta` 错误从 HTTP 400 变成 500 | 测试覆盖 chat type 冲突的 `HTTPException(400)` |
| 新模块误导入 `api.routes`，形成循环依赖 | 源码扫描测试禁止 `from api.routes` 和 `import api.routes` |
| 顺手触碰 Prompt Runtime 或异步状态机 | 非目标明确禁止，本阶段只迁移请求契约与纯 helper |

## 阶段拆分

1. 设计文档阶段：提交本设计文档。
2. 计划阶段：写入 `.Codex/plans/api-chat-request-contract-split.md`，列出 TDD 执行步骤。
3. 红灯阶段：新增 `tests/test_api_chat_request_contract_split.py`，确认新模块缺失导致测试失败。
4. 实现阶段：新增 `api/chat_request_contract.py`，父模块保留 wrapper，并运行定向测试。
5. 收口阶段：运行全量测试，更新计划 walkthrough 与待办清单进度，并按阶段提交。
