# 普通 API Chat Helper 拆分设计

日期：2026-06-22

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前仍只剩普通
`api/routes.py` 超过 800 行。前序已经完成 task、memory、models、evolution、
history / log、sticker / media、Agent Step / Render、group utility / legacy timing
和 group message 拆分，父模块当前为 1709 行。

Group Message 拆分后，`api/routes.py` 中剩余显式 route 只有：

- `POST /chat`
- `GET /health`

三路只读审计结论一致：

- `/health` 只有数行，收益极低，并且多个 split 测试把它作为父模块哨兵端点。
- 直接迁移完整 `/chat` 收益最大，但风险最高：它同时包含私聊缓冲、guardrail、
  TimingGate、Prompt Runtime 输入组装、KT bridge 调用、SSE、断连后台 push、聊天落库、
  进化触发和响应 envelope。
- 现有测试大量依赖 `api.routes.*` 旧入口 monkeypatch，包括 `get_bridge`、
  `get_guardrail`、`_persist_chat_turn`、`_schedule_image_precache`、
  `CHAT_STREAM_QUEUE_MAXSIZE`、`_private_buffers` 以及多个 helper 的
  `__module__ == "api.routes"` 断言。
- 文件、多模态内容和响应契约 helper 基本是纯函数，风险明显低于私聊缓冲状态机、
  streaming runner 和落库 writer。

因此本阶段不直接搬迁 `/chat` endpoint，而是先拆出 chat helper / contract 纯逻辑，
让父模块继续承载路由编排和旧兼容 facade。

## 目标

新增 chat helper 模块，降低 `api/routes.py` 的职责密度，并为后续拆分聊天落库和
streaming 编排建立清晰边界。

本阶段迁移实现逻辑：

- 文件与多模态内容 helper：
  - `_normalize_files`
  - `_build_guardrail_input`
  - `_build_multimodal_user_input_text`
  - `_build_file_archive_summary`
  - `_build_chatlog_user_content`
  - `_build_conversation_user_content`
- 响应契约 helper：
  - `_normalize_chat_stream_event`
  - `_split_chat_answer_chunks`
  - `_chat_response_meta`
  - `_chat_response_payload`
  - SSE 编码与安全错误事件的纯 helper

`api.routes` 继续保留旧同名函数作为薄 wrapper，使既有测试和外部调试脚本仍可从父模块访问。

## 非目标

- 不迁移 `proxy_chat()` 或 `POST /chat` 路由注册位置。
- 不迁移 `ChatProxyRequest`。
- 不迁移 `_private_buffers`、`_private_lock`、私聊缓冲窗口常量或私聊缓冲状态机。
- 不迁移 `_persist_chat_turn()`、`_safe_meta()` 或数据库写入逻辑。
- 不迁移 `_stream_chat()` 的 runner、bounded queue、heartbeat、断连后台任务、
  push 和持久化幂等逻辑。
- 不修改 `get_bridge`、`get_guardrail`、`get_timing_gate` 的旧父模块 monkeypatch 入口。
- 不修改 Prompt Runtime 模板、`enriched_query`、历史注入方式、conversation 结构或工具输出契约。
- 不修改 `core.message_envelope` 的协议字段过滤语义。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 方案比较

### 方案 A：拆 `/health`

新增 `api/health_routes.py`，只迁移 `health_check()`。

优点：实现极小。缺点：净收益接近 0，并会破坏多个父模块哨兵测试。本阶段不采用。

### 方案 B：直接拆完整 `/chat`

新增 `api/chat_routes.py`，迁移 `ChatProxyRequest`、`proxy_chat()` 和所有聊天 helper。

优点：行数收益最大。缺点：会一次性触达高风险闭包和旧 monkeypatch 合同，尤其是
`api.routes.get_bridge`、`api.routes.get_guardrail`、`api.routes._persist_chat_turn`、
`api.routes._schedule_image_precache`、`api.routes.CHAT_STREAM_QUEUE_MAXSIZE` 和
`api.routes._private_buffers`。本阶段不采用。

### 方案 C：先拆聊天落库 writer

新增 `api/chat_persistence.py` 或 `app/chat/persistence.py`，迁移 `_persist_chat_turn()`
主体。

优点：可减少约 100 行，并把 ChatLog / ConversationTurn 双写集中起来。缺点：
`_persist_chat_turn()` 被测试直接调用和 monkeypatch spy，且同时耦合敏感数据、
SQLite retry、HTML 摘要、processed 语义和 evolution pending 计数。该方案推迟到
content helper 拆分稳定后再做。

### 方案 D：拆 chat helper / contract（推荐）

新增纯 helper 模块，迁移文件、多模态内容、响应 envelope 和 SSE 事件规范化逻辑。
父模块保留同名 wrapper，`proxy_chat()` 继续调用父模块全局名称。

优点：边界清楚，副作用少；可以保留 `__module__ == "api.routes"` 的旧断言；
不碰私聊缓冲、bridge/SSE runner 和落库幂等；同时为后续落库 writer 与 streaming contract
拆分铺路。缺点：行数收益中等，仍不能单次把父模块降到 800 行以内。

## 选定设计

采用方案 D，新增两个 API 层 helper 模块：

- `api/chat_content_helpers.py`
- `api/chat_response_contract.py`

两个模块都不注册 FastAPI router，不访问数据库，不创建任务，不导入 `api.routes`。

### `api/chat_content_helpers.py`

该模块负责文件列表归一化和聊天内容文本构造。

迁移后的公开函数采用不带下划线的模块内名称，父模块 wrapper 继续使用旧下划线名称：

| 新模块函数 | 父模块兼容函数 |
|------------|----------------|
| `normalize_files()` | `_normalize_files()` |
| `build_guardrail_input()` | `_build_guardrail_input()` |
| `build_multimodal_user_input_text()` | `_build_multimodal_user_input_text()` |
| `build_file_archive_summary()` | `_build_file_archive_summary()` |
| `build_chatlog_user_content()` | `_build_chatlog_user_content()` |
| `build_conversation_user_content()` | `_build_conversation_user_content()` |

模块内部直接依赖 `core.context_builder.sanitize_prompt_text()`，不再依赖父模块的
`_sanitize_prompt_text`。

`_schedule_image_precache()` 暂时继续留在 `api.routes`，但内部改为调用
`_normalize_files()` wrapper。原因是现有测试会 monkeypatch
`api.routes._schedule_image_precache` 来避免图片预缓存副作用，`proxy_chat()` 必须继续调用父模块名称。

### `api/chat_response_contract.py`

该模块负责聊天响应 envelope 和 SSE 事件的纯契约逻辑。

迁移后的模块函数：

| 新模块函数 | 父模块兼容函数 |
|------------|----------------|
| `normalize_chat_stream_event()` | `_normalize_chat_stream_event()` |
| `chat_sse_data()` | 新增父模块 wrapper `_chat_sse_data()` |
| `stream_error_event()` | 新增父模块 wrapper `_stream_error_event()` |
| `split_chat_answer_chunks()` | `_split_chat_answer_chunks()` |
| `chat_response_meta()` | `_chat_response_meta()` |
| `chat_response_payload()` | `_chat_response_payload()` |

`chat_response_meta()` 和 `chat_response_payload()` 接收 duck-typed request 对象，只读取
`user_id`、`session_id` 和 `client_meta`，不导入 `ChatProxyRequest`。这样可以避免
`api.chat_response_contract` 反向依赖父模块。

`chat_sse_data()` 固定输出 `data: <json>\n\n`，并使用 `ensure_ascii=False`。
`stream_error_event()` 固定返回安全错误事件，不包含内部异常细节。

## 父模块兼容策略

`api/routes.py` 继续作为 `/api/v1` 聚合模块，并保留所有旧入口：

- `proxy_chat`
- `ChatProxyRequest`
- `_private_buffers`
- `CHAT_STREAM_QUEUE_MAXSIZE`
- `_persist_chat_turn`
- `_safe_meta`
- `_schedule_image_precache`
- `_normalize_files`
- `_build_multimodal_user_input_text`
- `_build_chatlog_user_content`
- `_build_conversation_user_content`
- `_normalize_chat_stream_event`
- `_chat_response_payload`

父模块中的 helper 改为薄 wrapper，而不是简单 `from ... import ... as ...`。这样旧断言
`routes._normalize_files.__module__ == "api.routes"` 仍然成立。

`proxy_chat()`、`_persist_chat_turn()` 和 `_stream_chat()` 继续调用父模块 wrapper 名称。
这样未来如果测试或调试脚本 monkeypatch 父模块 helper，执行路径仍可被截获。

## 兼容性约束

- `POST /api/v1/chat` 的 path、method、鉴权、请求模型和响应结构不变。
- `req.stream=True` 时继续返回 `StreamingResponse(..., media_type="text/event-stream")`。
- SSE 格式继续为 `data: <json>\n\n`，并保持 `ensure_ascii=False`。
- 连续 delta 仍合并；非 delta 事件前仍 flush pending delta；done 前仍 flush pending delta。
- 流式 done 事件继续使用标准 response envelope，且不包含 `answer_chunks`。
- 非流式成功响应继续包含 `answer_chunks`。
- 流式错误事件继续只返回安全中文错误消息，不回显内部异常。
- `reply_meta` 仍只透出 `send_mode`、`reply_to_message_id`、`mentions`、`quote`、
  `at_sender` 等协议字段，内部 `_agent_result` 不外泄。
- `ChatLog` 仍保存含图片引用的原始用户存档文本，`ConversationTurn` 仍只保存图片数量摘要。
- 图片 token 的传输层展开和持久化原始 answer 的边界不变。
- `api.routes.get_bridge`、`api.routes.get_guardrail`、`api.routes._persist_chat_turn`、
  `api.routes._schedule_image_precache` 和 `api.routes.CHAT_STREAM_QUEUE_MAXSIZE`
  的 monkeypatch 语义不变。
- `_private_buffers` 必须仍是父模块中的同一个 dict 对象。
- 新模块不得包含 `from api.routes`、`import api.routes`、`asyncio.run` 或
  `run_awaitable_sync`。

## 测试策略

新增 `tests/test_api_chat_helpers_split.py`，覆盖：

- 新模块文件存在，且不反向导入 `api.routes`。
- `api.routes` 父模块 wrapper 的 `__module__` 仍为 `"api.routes"`。
- 内容 helper wrapper 与新模块实现输出一致。
- `_normalize_files()` 过滤空白、非字符串和空列表。
- `_build_multimodal_user_input_text()` 覆盖纯文本、纯图片、图文混合和 `max_chars`。
- `_build_chatlog_user_content()` 保留图片引用，适合完整归档。
- `_build_conversation_user_content()` 只保留图片数量摘要，不暴露图片 URL。
- `_normalize_chat_stream_event()` 保持 delta、final、progress、heartbeat 和非法事件语义。
- `_chat_response_payload()` 保持标准 envelope、兼容 `answer` 字段和非流式
  `answer_chunks` 行为。
- `_chat_sse_data()` 输出格式固定为 `data: <json>\n\n` 且中文不转义。

需要同步扩展已有 split 测试：

- 保持 `tests/test_api_sticker_media_routes_split.py`、
  `tests/test_api_group_message_routes_split.py` 和
  `tests/test_api_agent_step_routes_split.py` 中父模块哨兵断言继续成立。
- 增加新模块禁用模式扫描：`from api.routes`、`import api.routes`、`asyncio.run`、
  `run_awaitable_sync`。

继续运行既有行为回归：

- `tests/test_streaming_api.py`
- `tests/test_streaming_response_envelope.py`
- `tests/test_api_push_envelope.py`
- `tests/test_chat_response_envelope.py`
- `tests/test_message_envelope.py`
- `/chat` 私聊缓冲、持久化、图片附件和 Prompt Runtime audit failure 相关 nodeid。

## 验证计划

- 红灯：先新增 split / helper 契约测试，预期失败点为新模块不存在、父模块 helper 尚未委托新模块、
  `_chat_sse_data()` 和 `_stream_error_event()` 尚不存在。
- 绿灯：迁移 helper 实现后运行新增 split 测试与相邻 split 测试。
- 行为回归：运行 streaming、response envelope、push envelope、message envelope 和 `/chat`
  关键 nodeid。
- 静态检查：`py_compile`、源码禁用模式扫描、`git diff --check` 和行数检查。
- 最终全量：`python -B -m pytest -p no:cacheprovider tests/ -v`。

## 风险与缓解

- **父模块 `__module__` 断言失效**：不使用简单 re-export，父模块保留 wrapper。
- **`_schedule_image_precache` monkeypatch 失效**：该函数继续留在父模块，
  `proxy_chat()` 继续调用父模块名称。
- **聊天响应 envelope 字段漂移**：新增 helper 测试和既有 envelope 测试同时覆盖。
- **流式 SSE 合并语义漂移**：只抽纯事件规范化和编码，不迁移 runner / queue / finally；
  继续跑 streaming 契约测试。
- **ChatLog / ConversationTurn 图片文本边界混淆**：内容 helper 测试分别锁定完整归档和上下文摘要。
- **循环导入**：新模块禁止导入 `api.routes`，响应 contract 通过 duck typing 读取 request 字段。
- **误触 Prompt Runtime 合同**：本阶段不改 `enriched_query`、history 注入、conversation 结构或工具输出。

## 阶段提交拆分

本设计对应后续四个原子阶段：

1. 设计文档提交：记录边界、兼容策略和验证计划。
2. 实现计划提交：写入 `.Codex/plans/api-chat-helper-split.md`，列出红绿重构步骤。
3. 测试提交：新增 chat helper split / contract 测试，验证红灯。
4. 实现提交：新增 helper 模块、改父模块 wrapper，并通过定向与全量验证。
