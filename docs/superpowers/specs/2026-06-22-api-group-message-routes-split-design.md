# 普通 API Group Message 路由拆分设计

日期：2026-06-22

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前仍只剩普通
`api/routes.py` 超过 800 行。前序已经完成 task、memory、models、evolution、
history / log、sticker / media、Agent Step / Render、group utility / legacy timing
拆分，父模块当前为 1754 行。

Group Utility 拆分后，`api/routes.py` 中剩余显式 route 只有：

- `POST /group/message`
- `POST /chat`
- `GET /health`

三路只读审计结论如下：

- `/health` 只有 3 行，收益极低，并且多个 split 测试把它作为父模块哨兵端点。
- 直接拆 `/chat` 收益最大，但风险最高：它包含私聊缓冲、guardrail、SSE、断连后台 push、
  Prompt Runtime metadata、聊天落库、进化触发和大量 `api.routes.*` monkeypatch 入口。
- `/group/message` 当前 HTTP shell 较薄，主体业务已经在
  `app.group_ingress.service.GroupIngressService`；只要保留旧 `api.routes` re-export、
  route order、普通 API 鉴权和 `api.routes.get_bridge` monkeypatch 兼容，风险可控。

因此本阶段选择拆 `/group/message`，把父模块的剩余 route 进一步收敛到 `/chat` 和
`/health`，为后续 chat helper / chat 主链路拆分建立更清晰的边界。

## 目标

新增 `api/group_message_routes.py`，迁移群聊入口 HTTP 层，继续降低 `api/routes.py` 的
职责密度，同时保持群聊业务流程、Prompt Runtime、message envelope、聊天落库和
`GroupIngressService` 行为不变。

本阶段迁移：

- `OneBotMessageSegmentPayload`
- `GroupMessageRequest`
- `group_message()`
- `POST /group/message`

## 非目标

- 不迁移 `/chat`。
- 不迁移 `ChatProxyRequest`、`proxy_chat()`、私聊缓冲、guardrail、流式响应、
  Prompt Runtime 输入组装、message envelope、`_persist_chat_turn()` 或 `_safe_meta()`。
- 不迁移 `/health`。
- 不迁移 `/group_timing`、`/group_timing/timer` 或 group utility。
- 不迁移 group ingress helper facade；父模块继续保留 `_normalize_onebot_segments`、
  `_build_group_message_text`、`_persist_group_bridge_reply` 等旧私有名称兼容。
- 不修改 `app.group_ingress.service.GroupIngressService` 或
  `app.group_ingress.helpers` 的业务语义。
- 不修改 Prompt Runtime 模板、`enriched_query`、conversation 结构或工具输出契约。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 方案比较

### 方案 A：拆 `/health`

新增 `api/health_routes.py`，只迁移 `health_check()`。

优点：实现最小。缺点：净收益接近 0，并会破坏或需要同步更新多个父模块哨兵测试，包括
task、memory、model、evolution、history / log split 测试。本阶段不采用。

### 方案 B：拆 `/group/message`（推荐）

新增 `api/group_message_routes.py`，迁移群聊入口 request model 和 endpoint。父模块在原
`/group/message` 所在位置 include 子 router，并 re-export 旧符号。

优点：风险低于 `/chat`，主体业务已经在 service 层；能把父模块显式 route 收敛到
`/chat` 和 `/health`。缺点：行数收益约 35-50 行，不足以直接接近 800 行目标。

### 方案 C：先拆 chat helper

新增 chat helper / contract / persistence 子模块，保留 `proxy_chat()` 在父模块。可迁移
`ChatProxyRequest`、响应 envelope helper、文件归档文本、Prompt 输入文本或聊天落库
helper 的一部分。

优点：行数收益明显高于 `/group/message`，可为最终拆 `/chat` 铺路。缺点：会碰到更多
既有父模块哨兵和 monkeypatch 点，需要更细的设计。该方案作为 `/group/message` 后的
下一阶段优先方向。

### 方案 D：直接拆 `/chat`

新增 `api/chat_routes.py`，迁移完整 `/chat` endpoint。

优点：收益最大，可能让 `api/routes.py` 直接接近 800 行以内。缺点：当前风险最高，会
同时触达私聊缓冲、SSE、guardrail、Prompt Runtime metadata、落库、断连 push 和大量
`api.routes.*` monkeypatch。本阶段不采用。

## 选定设计

采用方案 B，新增 `api/group_message_routes.py`。

### 新模块职责

`api/group_message_routes.py` 负责：

- 定义 `router = APIRouter(tags=["group-message"])`。
- 定义并注册 `POST /group/message`。
- 定义并 re-export：
  - `OneBotMessageSegmentPayload`
  - `GroupMessageRequest`
  - `group_message`
- 继续从 `api.common_auth` 导入 `verify_token`，保持
  `api.routes.NANOBOT_API_TOKEN` monkeypatch 兼容。
- 本地定义 `_normalize_request_client_meta()` 等价小 wrapper，用
  `core.client_meta.normalize_client_meta()` 和 `ClientMetaValidationError` 转换
  `HTTPException(400)`，避免反向导入 `api.routes`。
- 定义 `_current_bridge_provider()`，优先读取 `sys.modules["api.routes"].get_bridge`，
  否则使用 `nanobot_kt.bridge.get_bridge` 默认 provider。
- 在 endpoint 内实例化 `GroupIngressService`，并将 `_current_bridge_provider()` 注入为
  `bridge_provider`。

新模块不得出现 `from api.routes` 或 `import api.routes`。

### 父模块职责

`api/routes.py` 继续作为 `/api/v1` 聚合 router，并：

- 删除本地 `OneBotMessageSegmentPayload`、`GroupMessageRequest` 和 `group_message()`
  定义。
- 从 `api.group_message_routes` 导入并 re-export：
  - `OneBotMessageSegmentPayload`
  - `GroupMessageRequest`
  - `group_message`
  - `router as group_message_router`
- 在原 `/group/message` 所在位置 include `group_message_router`，即 group ingress helper
  facade 之后、`group_utility_router` 之前。
- 继续保留 group ingress helper facade 旧私有名称，包括 `_normalize_onebot_segments`、
  `_build_group_message_meta`、`_group_sticker_payloads`、`_pop_bridge_reply_meta`、
  `_persist_group_bridge_reply` 等。

迁移后实际 route order 必须保持：

1. `/api/v1/group/message`
2. `/api/v1/update_group_name`
3. `/api/v1/group_timing`
4. `/api/v1/group_timing/timer`
5. `/api/v1/render`
6. `/api/v1/chat-step`
7. `/api/v1/chat`

### `get_bridge` 兼容策略

现有群聊测试大量 monkeypatch `api.routes.get_bridge`，然后直接调用
`api.routes.group_message()` 或通过 HTTP 触发群聊入口。拆分后如果新模块静态使用自己的
`get_bridge`，这些测试替身和外部调试脚本会失效。

本阶段沿用 group utility 拆分中的轻量 provider 策略：

- 新模块默认导入 `nanobot_kt.bridge.get_bridge`，命名为 `_default_get_bridge`。
- 新模块内部定义 `_current_bridge_provider()`，通过 `sys.modules.get("api.routes")`
  查看旧父模块是否暴露 `get_bridge`。
- 如果旧父模块存在 `get_bridge`，endpoint 注入父模块 provider；否则使用
  `_default_get_bridge`。
- 新模块不反向导入父模块。

### client meta 兼容策略

`group_message()` 当前调用父模块 `_normalize_request_client_meta(req, expected_chat_type="group")`。
该 wrapper 逻辑很小，但直接从新模块导入父模块会制造循环依赖。本阶段在
`api/group_message_routes.py` 内定义等价小函数：

- 调用 `normalize_client_meta(getattr(req, "client_meta", None), expected_chat_type="group")`。
- 捕获 `ClientMetaValidationError` 并抛出 `HTTPException(400, f"invalid client_meta: {exc}")`。
- 将 normalized dict 写回 `req.client_meta`。

`/chat` 继续使用父模块原 `_normalize_request_client_meta()`，本阶段不抽共享模块。后续
chat helper 抽取时可以再把该 wrapper 收敛到共享边界。

## 兼容性约束

- `POST /api/v1/group/message` 的 path、method、鉴权、请求模型和响应结构不变。
- `GroupMessageRequest` 字段不变，包括 legacy 字段和结构化消息字段。
- `OneBotMessageSegmentPayload` 继续保留，用于区分 OneBot / NapCat 消息段模型。
- `api.routes.GroupMessageRequest`、`api.routes.OneBotMessageSegmentPayload` 和
  `api.routes.group_message` 继续可用，并与 `api.group_message_routes` 中对象相同。
- `api.routes.NANOBOT_API_TOKEN` monkeypatch 继续影响拆分后的 endpoint。
- `api.routes.get_bridge` monkeypatch 继续影响拆分后的 `group_message()`。
- `client_meta.chat_type` 与群聊入口冲突时继续返回 HTTP 400。
- `GroupIngressService` 的调用方式不变，仍传入 `db`、`background_tasks` 和
  `bridge_provider`。
- group ingress helper facade 继续留在 `api.routes`，并继续与
  `app.group_ingress.helpers` 中对象保持 identity alias。
- `/chat`、`proxy_chat()`、`ChatProxyRequest`、`_persist_chat_turn()`、`_safe_meta()` 和
  私聊 helper 继续留在 `api.routes`。
- `/health` 继续留在 `api.routes`。

## 测试策略

新增 `tests/test_api_group_message_routes_split.py`，覆盖：

- `/api/v1/group/message` endpoint module 为 `api.group_message_routes`。
- route 不重复注册。
- 旧 `api.routes` re-export 与新模块对象一致。
- `api.routes.NANOBOT_API_TOKEN` monkeypatch 继续影响拆分后的鉴权。
- `api.routes.get_bridge` monkeypatch 继续影响 `group_message()`。
- client meta 冲突继续返回 HTTP 400。
- route order 保持 `/group/message` -> `/update_group_name` -> `/group_timing` ->
  `/group_timing/timer` -> `/render` -> `/chat-step` -> `/chat`。
- `group_message()` 仍是 coroutine。
- `api/group_message_routes.py` 不包含 `from api.routes`、`import api.routes`、
  `asyncio.run` 或 `run_awaitable_sync`。
- `/chat`、`/health`、`_persist_chat_turn()`、`_safe_meta()`、`_build_multimodal_user_input_text()`
  继续留在父模块。

需要同步调整：

- `tests/test_api_agent_step_routes_split.py` 和
  `tests/test_api_sticker_media_routes_split.py` 当前断言 `group_message` 留在父模块；
  本阶段应改为断言 `routes.group_message is group_message_routes.group_message`。
- `tests/test_api_group_utility_routes_split.py` 中 route order 测试继续保留并可扩展，
  确认 group message router include 位置不变。

继续运行现有行为回归：

- `tests/test_api.py` 中 group message 相关测试。
- `tests/test_group_response_envelope.py`。
- `tests/test_api_routes_group_helper_facade.py`。
- 普通 API split 相邻测试和 `tests/test_asyncio_run_policy.py`。

## 验证计划

- 红灯：运行新增 split 测试和需要同步调整的相邻 split 测试，预期失败点为
  `/group/message` endpoint 仍来自 `api.routes`、`api.group_message_routes` 尚不存在，
  以及旧相邻 split 测试仍期待 `group_message` 留在父模块。
- 绿灯：迁移后运行新增 split 测试、Agent Step split、Sticker / Media split 和
  Group Utility split 测试。
- 行为回归：运行 group message 相关测试和 group response envelope 测试。
- 相邻回归：运行普通 API split 测试集合、group helper facade 和 asyncio policy。
- 静态检查：`py_compile`、源码禁用模式扫描、`git diff --check` 和行数检查。
- 最终全量：`python -B -m pytest -p no:cacheprovider tests/ -v`。

## 风险与缓解

- **`get_bridge` monkeypatch 失效**：通过 `_current_bridge_provider()` 保留
  `api.routes.get_bridge` monkeypatch，并用 split 测试显式验证。
- **循环导入父模块**：新模块本地实现 client meta wrapper，只用 `sys.modules` 做 provider
  lookup，不写 `import api.routes`。
- **route order 变化**：父模块在原位置 include `group_message_router`，不放到文件尾部。
- **helper facade 误迁移**：本阶段只迁 request model 和 endpoint，group ingress helper
  facade 继续留在父模块。
- **主链路误迁移**：split 测试继续断言 `/chat`、`_persist_chat_turn()`、`_safe_meta()` 和
  multimodal helper 属于 `api.routes`。
- **Prompt Runtime 或信封漂移**：本阶段不触碰 `/chat`、Prompt Runtime 输入组装、message
  envelope 或工具输出契约，因此不需要修改 canonical Prompt Runtime 模板。

## 提交拆分

本阶段按以下提交粒度推进：

1. 设计文档：`docs(普通API): 设计群消息路由拆分`
2. 实现计划：`docs(计划): 记录群消息路由拆分计划`
3. 红灯测试：`test(普通API): 锁定群消息路由拆分契约`
4. 代码拆分：`refactor(普通API): 拆分群消息路由`
5. 文档收口：`docs(计划): 收口群消息路由拆分`

## 下一步

完成本设计提交后，编写 `.Codex/plans/api-group-message-routes-split.md`。Group Message
拆分完成后，P3 后续优先进入 chat helper / contract / persistence 抽取，而不是拆
`/health` 或直接迁移完整 `/chat` endpoint。
