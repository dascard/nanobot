# 普通 API Group Utility 路由拆分设计

日期：2026-06-22

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前仍只剩普通
`api/routes.py` 超过 800 行。前序已完成 task、memory、models、evolution、
history / log、sticker / media、Agent Step / Render 拆分，父模块当前为 1954 行。

Agent Step 拆分后，`api/routes.py` 中剩余显式 route 只有：

- `POST /group/message`
- `POST /update_group_name`
- `POST /group_timing`
- `POST /group_timing/timer`
- `POST /chat`
- `GET /health`

其中 `/chat` 和 `/group/message` 是明确保留的主链路，`/health` 是现有 split 测试中的
父模块哨兵端点，不适合作为独立收益很低的小刀。剩余可拆边界集中在群工具和遗留
timing 端点。

## 目标

新增 `api/group_utility_routes.py`，迁移 group utility / legacy timing HTTP 层，继续
降低 `api/routes.py` 的职责密度，同时保持 `/chat`、`/group/message`、Prompt Runtime、
message envelope、聊天落库和群聊主流程不变。

本阶段迁移：

- `UpdateGroupNameRequest`
- `update_group_name()`
- `GroupTimingRequest`
- `_build_group_timing_context()`
- `GroupTimingTimerRequest`
- `group_timing_deprecated()`
- `group_timing_timer()`
- `POST /update_group_name`
- `POST /group_timing`
- `POST /group_timing/timer`

## 非目标

- 不迁移 `/chat`。
- 不迁移 `/group/message`。
- 不迁移 `ChatProxyRequest`、`GroupMessageRequest` 或 OneBot 段模型。
- 不迁移私聊缓冲、guardrail、流式响应、Prompt Runtime 输入组装、message envelope、
  `_persist_chat_turn()` 或 `_safe_meta()`。
- 不迁移 group ingress helper facade 本身；父模块继续保留 `_pop_bridge_reply_meta`、
  `_persist_group_bridge_reply`、`_find_recent_duplicate_group_reply` 等旧私有名称兼容。
- 不 service 化 `group_timing_timer()`，不合并到 `GroupIngressService`。
- 不修改 `core.group_runtime`、`app.group_ingress.service`、Prompt Runtime 模板、
  conversation 结构或工具输出契约。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 方案比较

### 方案 A：只拆 `update_group_name`

新增 `api/group_meta_routes.py`，只迁移 `UpdateGroupNameRequest` 和
`update_group_name()`。

优点：风险最低，只依赖 `User`、`get_db` 和 `verify_token`。缺点：父模块净减少约
14-16 行，对 800 行目标推进很弱，下一步仍要面对 group timing。

### 方案 B：拆出 group utility / legacy timing（推荐）

新增 `api/group_utility_routes.py`，迁移 `/update_group_name`、`/group_timing` 和
`/group_timing/timer` 这组连续路由切片。父模块 re-export 旧符号，并在原位置 include
子 router。

优点：行数收益约 185-200 行，仍避开 `/chat` 与 `/group/message` 主链路；`update`
和 legacy timing 在文件中连续，route order 易于保持。缺点：`group_timing_timer()`
不是纯 HTTP shell，会触达 runtime、history context、bridge、重复回复抑制和群回复落库，
需要用红灯测试锁住边界。

### 方案 C：拆 `/health`

新增 `api/health_routes.py`，迁移 `health_check()`。

优点：实现最小。缺点：收益接近 0，且已有多个普通 API split 测试把 `/health` 当作
父模块哨兵端点。主动移动它会增加测试维护成本，不推进核心目标。

### 方案 D：开始拆 `/chat` 或 `/group/message`

收益最大，但这是主链路重构：`/chat` 涉及私聊缓冲、SSE、guardrail、Prompt Runtime、
落库和图片 token 传输；`/group/message` 涉及群聊入口、runtime、bridge、落库和出站
信封。本阶段不采用。

## 选定设计

采用方案 B，新增 `api/group_utility_routes.py`。

### 新模块职责

`api/group_utility_routes.py` 负责：

- 定义 `router = APIRouter(tags=["group-utility"])`。
- 定义并注册：
  - `POST /update_group_name`
  - `POST /group_timing`
  - `POST /group_timing/timer`
- 定义并 re-export：
  - `UpdateGroupNameRequest`
  - `GroupTimingRequest`
  - `GroupTimingTimerRequest`
  - `_build_group_timing_context`
  - `update_group_name`
  - `group_timing_deprecated`
  - `group_timing_timer`
- 继续从 `api.common_auth` 导入 `verify_token`，保持
  `api.routes.NANOBOT_API_TOKEN` monkeypatch 兼容。
- 继续直接复用 `core.context_builder.build_timing_recent_context` 和
  `core.context_builder.build_chat_context`。
- 继续复用 `app.group_ingress.helpers` 中的 transport、reply meta、重复回复抑制、
  no-reply log 和群回复持久化 helper。

### 父模块职责

`api/routes.py` 继续作为 `/api/v1` 聚合 router，并：

- 删除本地 `UpdateGroupNameRequest`、`GroupTimingRequest`、`GroupTimingTimerRequest`、
  `_build_group_timing_context()`、`update_group_name()`、`group_timing_deprecated()` 和
  `group_timing_timer()` 定义。
- 从 `api.group_utility_routes` 导入并 re-export：
  - `UpdateGroupNameRequest`
  - `GroupTimingRequest`
  - `GroupTimingTimerRequest`
  - `_build_group_timing_context`
  - `update_group_name`
  - `group_timing_deprecated`
  - `group_timing_timer`
  - `router as group_utility_router`
- 在原 `/update_group_name` 与 `/group_timing*` 所在位置 include
  `group_utility_router`，即 `group_message()` 之后、`agent_step_router` 之前。

迁移后实际 route order 必须保持：

1. `/api/v1/group/message`
2. `/api/v1/update_group_name`
3. `/api/v1/group_timing`
4. `/api/v1/group_timing/timer`
5. `/api/v1/render`
6. `/api/v1/chat-step`
7. `/api/v1/chat`

### `get_bridge` 兼容策略

`group_timing_timer()` 当前测试会 monkeypatch `api.routes.get_bridge`。拆分后如果新模块
直接静态使用自己的 `get_bridge`，会破坏旧测试替身和外部调试脚本。

本阶段设计一个轻量 provider：

- 新模块默认导入 `nanobot_kt.bridge.get_bridge`，命名为 `_default_get_bridge`。
- 新模块内部定义 `_current_bridge_provider()`，通过 `sys.modules.get("api.routes")`
  查看旧父模块是否暴露 `get_bridge`。
- 如果旧父模块存在 `get_bridge`，timer 使用该对象；否则使用 `_default_get_bridge`。
- 新模块不出现 `from api.routes` 或 `import api.routes`，避免反向导入父模块。

这与 `api.common_auth` 的旧 token monkeypatch 兼容思路一致。

## 兼容性约束

- `POST /api/v1/update_group_name` 的 path、method、鉴权和响应结构不变。
- `update_group_name()` 继续把裸 `group_id` 写为 `users.id = "group_<id>"`；已带
  `group_` 前缀时不重复添加。
- `POST /api/v1/group_timing` 的 path、method、鉴权和 async runtime 调用不变。
- `POST /api/v1/group_timing/timer` 的 path、method、鉴权、返回 payload 和 async
  行为不变。
- `_build_group_timing_context()` 继续从 `core.timing_runtime` 懒加载
  `PendingMessage` 与 `GroupRuntime`，保持旧路径导入语义。
- `group_timing_timer()` 继续在 runtime 前释放干净 DB 事务，并在 bridge 前再次释放
  干净 DB 事务。
- `group_timing_timer()` 继续传递 `recent_context`、`history_header`、
  `history_messages`、`source_message_ids`、`bot_id`、`bot_name`、`bot_aliases`、
  `trigger_reason`、`context_debug` 和 identity vars。
- timer 成功回复仍走重复回复抑制；非重复且非空回复仍写群 reply log 和
  `ConversationTurn`，并调用 `runtime.note_bot_replied()`。
- timer 空回复或 bridge 异常仍返回 `no_reply` 语义，并保留现有日志风格。
- `api.routes.GroupTimingRequest`、`api.routes.GroupTimingTimerRequest`、
  `api.routes.UpdateGroupNameRequest`、`api.routes._build_group_timing_context`、
  `api.routes.update_group_name`、`api.routes.group_timing_deprecated` 和
  `api.routes.group_timing_timer` 继续可用，并与 `api.group_utility_routes` 中对象相同。
- `/chat` 和 `/group/message` endpoint 来源继续是 `api.routes`。
- `/health` 继续留在 `api.routes`。

## 测试策略

新增 `tests/test_api_group_utility_routes_split.py`，覆盖：

- `/api/v1/update_group_name`、`/api/v1/group_timing`、
  `/api/v1/group_timing/timer` 注册来源均为 `api.group_utility_routes`。
- 3 个 route 不重复注册。
- 旧 `api.routes` re-export 与新模块对象一致。
- `api.routes.NANOBOT_API_TOKEN` monkeypatch 继续影响拆分后的鉴权。
- route order 保持 `/group/message` -> `/update_group_name` -> `/group_timing` ->
  `/group_timing/timer` -> `/render` -> `/chat-step` -> `/chat`。
- `group_timing_deprecated()` 与 `group_timing_timer()` 仍是 coroutine；
  `update_group_name()` 仍是同步函数。
- `api/group_utility_routes.py` 不包含 `from api.routes`、`import api.routes`、
  `asyncio.run` 或 `run_awaitable_sync`。
- `update_group_name()` 行为：
  - `group_id="123"` 创建 `User(id="group_123")`。
  - 再次请求同一 group 更新 `name`。
  - `group_id="group_123"` 不生成 `group_group_123`。
- `api.routes.get_bridge` monkeypatch 仍能影响 `group_timing_timer()`，通过轻量 fake
  runtime 和 fake bridge 锁定 timer provider 兼容。
- `/chat`、`/group/message`、`_persist_chat_turn()` 和 `_safe_meta()` 继续留在父模块。

需要同步调整：

- `tests/test_api_agent_step_routes_split.py` 当前断言 `group_timing_timer` 留在父模块；
  本阶段应改为断言 `/chat`、`/group/message`、`_persist_chat_turn()`、`_safe_meta()`
  留在父模块，并额外断言 group utility 已拆出。

继续运行现有行为回归：

- `tests/test_timing_gate.py::TestRouteContext::test_group_timing_context_sanitizes_pending_messages`
- `tests/test_api.py::test_group_timer_returns_full_html_reply_without_truncation`
- `tests/test_api.py::test_group_message_returns_full_html_reply_without_truncation`
- `tests/test_api_routes_group_helper_facade.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_asyncio_run_policy.py`

## 验证计划

- 红灯：运行新增 split 测试和需要同步调整的 Agent Step split 测试，预期失败点为
  group utility endpoint 仍来自 `api.routes`、`api.group_utility_routes` 尚不存在，
  以及旧 Agent Step split 测试仍期待 `group_timing_timer` 留在父模块。
- 绿灯：迁移后运行新增 split 测试和 `tests/test_api_agent_step_routes_split.py`。
- 行为回归：运行 timing context、group timer HTML、group message HTML 和 group helper
  facade 相关测试。
- 相邻回归：运行普通 API split 测试集合和 `tests/test_asyncio_run_policy.py`。
- 静态检查：`py_compile`、源码禁用模式扫描、`git diff --check` 和行数检查。
- 最终全量：`python -B -m pytest -p no:cacheprovider tests/ -v`。

## 风险与缓解

- **Timer 耦合较高**：不在本阶段 service 化，只做文件迁移，并用行为回归覆盖 HTML
  不截断、bridge provider、runtime 调用和父模块边界。
- **旧 monkeypatch 失效**：通过 `_current_bridge_provider()` 保留
  `api.routes.get_bridge` monkeypatch；通过 split 测试显式验证。
- **反向导入父模块**：新模块只用 `sys.modules` 做兼容 lookup，不写 `import api.routes`。
- **误迁移主链路**：split 测试继续断言 `/chat` 与 `/group/message` 属于 `api.routes`。
- **Prompt Runtime 或信封漂移**：本阶段不触碰 `/chat`、`/group/message` 主流程和 prompt
  输入组装，因此不需要修改 canonical Prompt Runtime 模板。

## 提交拆分

本阶段按以下提交粒度推进：

1. 设计文档：`docs(普通API): 设计群工具路由拆分`
2. 实现计划：`docs(计划): 记录群工具路由拆分计划`
3. 红灯测试：`test(普通API): 锁定群工具路由拆分契约`
4. 代码拆分：`refactor(普通API): 拆分群工具路由`
5. 文档收口：`docs(计划): 收口群工具路由拆分`

## 下一步

1. 编写 `.Codex/plans/api-group-utility-routes-split.md`。
2. 按 TDD 新增红灯测试并提交。
3. 创建 `api/group_utility_routes.py`，修改 `api/routes.py` include 和 re-export。
4. 完成定向、相邻、静态和全量验证后提交实现。
5. 更新 `docs/todo.md` 与 `docs/plan_walkthrough.md` 收口。
