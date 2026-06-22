# 普通 API Agent Step 路由拆分设计

## 背景

`docs/todo.md` 的 P3 超大文件治理当前只剩 `api/routes.py` 超过 800 行。上一刀已把
sticker / media HTTP 层拆到 `api/sticker_media_routes.py`，父模块降至 1975 行。
当前继续沿普通 API route-only 边界小步拆分，避免直接改动 `/chat` 和
`/group/message` 主链路。

`api/routes.py` 中剩余可拆候选主要有三类：

- `/render` 与 `/chat-step`：独立外部编排端点，主要依赖 `core.agent_step`。
- `update_group_name` 与 group timing：行数收益更大，但和群 runtime、bridge、
  history context、群回复持久化 helper 交织。
- `/chat` 主链路：收益最大，但包含私聊缓冲、流式传输、历史注入、落库和 push，
  需要单独设计，不能作为本阶段的小刀。

## 目标

将 `api/routes.py` 中 `/render` 与 `/chat-step` HTTP 层拆到
`api/agent_step_routes.py`，保留外部协议、鉴权、SSE framing、旧导入路径和父模块
聊天主链路边界。

## 非目标

- 不迁移 `/chat`。
- 不迁移 `/group/message`。
- 不迁移 group timing、`update_group_name()` 或 `_build_group_timing_context()`。
- 不修改 `core/agent_step.py` 的协议解析、LLM 调用或 stream 累积逻辑。
- 不改变 Prompt Runtime 模板、conversation 结构、message envelope 或工具输出契约。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 方案比较

### 方案 A：拆出 `api/agent_step_routes.py`（推荐）

新模块承载 `render_markdown()`、`chat_step()` 和 `AgentStepRequest` re-export。父模块只
include 子 router 并 re-export 旧符号。`/chat-step` 继续使用
`api.common_auth.verify_token`，`Accept: text/event-stream` 和 request body `stream=true`
继续触发 `StreamingResponse`。

优点：耦合低、测试已集中在 `tests/test_agent_step_api.py`、风险可用 split contract
测试锁住。缺点：行数收益只有约 25-35 行。

### 方案 B：拆出 group utility / timing routes

迁移 `UpdateGroupNameRequest`、`GroupTimingRequest`、`GroupTimingTimerRequest`、
`update_group_name()`、`group_timing_deprecated()`、`group_timing_timer()` 和
`_build_group_timing_context()`。

优点：行数收益明显更大。缺点：`group_timing_timer()` 直接调用 bridge、history
context、群回复去重、群回复落库和 runtime state，拆分会产生父模块 helper 反向依赖，
更适合作为单独阶段先梳理依赖。

### 方案 C：开始拆 `/chat` 主链路

优点：长期收益最大。缺点：`/chat` 是当前最复杂入口，涉及私聊缓冲、流式响应、历史、
guardrail、Prompt V2 audit、generated image transport 和落库；需要更细设计和更多红灯
测试，不适合作为本阶段小刀。

## 选定设计

采用方案 A，新增 `api/agent_step_routes.py`。

新模块职责：

- 定义 `router = APIRouter(tags=["agent-step"])`。
- 从 `core.agent_step` 导入并 re-export：
  - `AgentStepRequest`
  - `agent_step_event_payload`
  - `run_agent_step`
  - `run_agent_step_stream`
  - `sse_data as agent_step_sse_data`
- 实现 `render_markdown(text: str)`，保持返回 `{"status": "deprecated"}`。
- 实现 `chat_step(req: AgentStepRequest, accept: str = Header(default=""))`：
  - `req.stream` 或 `Accept` 包含 `text/event-stream` 时返回 SSE。
  - SSE 首事件仍是 `{"status": "progress", "text": "正在判断需要的业务工具..."}`。
  - 后续事件继续由 `run_agent_step_stream(req)` 产生，并通过 `agent_step_sse_data()`
    复用 framing。
  - 非流式路径继续 `await run_agent_step(req)` 并通过 `agent_step_event_payload()`
    返回。
  - 继续在 route decorator 上依赖 `Depends(verify_token)`。

父模块 `api/routes.py` 职责：

- 删除本地 `render_markdown()` 和 `chat_step()`。
- 删除不再直接使用的 `core.agent_step` import，但通过新模块 re-export
  `AgentStepRequest`、`agent_step_event_payload`、`run_agent_step`、
  `run_agent_step_stream`、`agent_step_sse_data`、`render_markdown` 和 `chat_step`。
- 保留 `StreamingResponse` import，因为 `/chat` SSE 仍在父模块使用。
- `router.include_router(agent_step_router)` 放在原 `/render` 与 `/chat-step` 所在位置，
  也就是 `/chat` 前，保持 route order 为 `/render` → `/chat-step` → `/chat`。

## 兼容性约束

- `POST /api/v1/chat-step` 的 path、method、鉴权和 request / response shape 不变。
- `GET /api/v1/render` 继续无 bearer 鉴权，保持 deprecated 响应。
- `api.routes.AgentStepRequest`、`api.routes.agent_step_event_payload`、
  `api.routes.run_agent_step`、`api.routes.run_agent_step_stream`、
  `api.routes.agent_step_sse_data`、`api.routes.chat_step` 和
  `api.routes.render_markdown` 继续可用，并与 `api.agent_step_routes` 中对象相同。
- `api.routes.NANOBOT_API_TOKEN` monkeypatch 继续影响 `/chat-step` 鉴权，依赖
  `api.common_auth.verify_token` 的兼容逻辑。
- `api.agent_step_routes` 不导入 `api.routes`，避免 split router 反向依赖父模块。
- `/chat` 和 `/group/message` endpoint 来源仍为 `api.routes`。

## 测试策略

新增 `tests/test_api_agent_step_routes_split.py`，覆盖：

- `/api/v1/render` 与 `/api/v1/chat-step` 注册来源为 `api.agent_step_routes`。
- 旧 `api.routes` re-export 与新模块对象一致。
- `/chat-step` 继续兼容 `api.routes.NANOBOT_API_TOKEN` monkeypatch。
- `/api/v1/render` 仍早于 `/api/v1/chat-step`，且二者均早于 `/api/v1/chat`。
- `/render` 不要求 bearer token，且返回 deprecated 响应。
- `/chat-step` 在 `Accept: text/event-stream` 且 request body `stream=false` 时仍走
  SSE；request body `stream=true` 且不带 `Accept` 时也仍走 SSE。
- `/chat-step` endpoint 保持 coroutine。
- 新模块源码不包含 `from api.routes`、`import api.routes`、`asyncio.run` 或
  `run_awaitable_sync`。
- `/chat` 和 `/group/message` 继续留在父模块。
- route 不重复注册。

行为回归继续使用：

- `tests/test_agent_step_api.py`
- `tests/test_api.py::test_stream_chat_passes_stream_flag_to_bridge`
- `tests/test_streaming_api.py`
- `tests/test_asyncio_run_policy.py`

## 验证计划

- 红灯：运行新增 split 测试，预期失败点为 endpoint module 仍是 `api.routes`、
  `api.agent_step_routes` 尚不存在，或 `api/agent_step_routes.py` 文件不存在。
- 绿灯：迁移后运行新增 split 测试。
- 行为回归：运行 `tests/test_agent_step_api.py`。
- 相邻回归：运行新增 split 测试、现有普通 API split 测试和
  `tests/test_asyncio_run_policy.py`。
- 静态检查：`py_compile`、源码禁用模式扫描、`git diff --check` 和行数检查。
- 最终全量：`python -B -m pytest -p no:cacheprovider tests/ -v`。

## 风险与缓解

- **SSE 行为回归**：用现有 `tests/test_agent_step_api.py` 覆盖 tool call、final answer、
  split tool name、delta 和 no-tools stream。
- **鉴权 monkeypatch 失效**：split 测试直接 monkeypatch `api.routes.NANOBOT_API_TOKEN`
  后请求 `/chat-step`。
- **父模块 import 误删**：设计明确保留 `StreamingResponse`，因为 `/chat` SSE 仍使用。
- **隐性旧导入破坏**：父模块继续 re-export `AgentStepRequest`、`chat_step` 和
  `render_markdown`。

## 下一步

1. 编写 `.Codex/plans/api-agent-step-routes-split.md`。
2. 按 TDD 新增红灯测试并提交。
3. 创建 `api/agent_step_routes.py`，修改 `api/routes.py` include 和 re-export。
4. 完成定向、相邻、静态和全量验证后提交实现。
5. 更新 `docs/todo.md` 与 `docs/plan_walkthrough.md` 收口。
