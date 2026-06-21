# Admin Observability 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍未完成。当前剩余硬项为
`api/admin_routes.py` 和 `api/routes.py`。上一阶段已经从 `api/admin_routes.py`
拆出 Group Memory 管理端路由，文件仍有 4731 行，需要继续按职责拆分。

本阶段只设计下一刀拆分，不直接修改生产代码。目标是选一个低风险、边界清晰、
测试可锁定的管理端子域，继续降低 `api/admin_routes.py` 的复杂度。

## 候选方案

### 方案 A：拆分 Admin Observability 路由（推荐）

范围拆成两个新模块：

- `api/admin/trace_routes.py`
  - `GET /agent-runs`
  - `GET /agent-runs/{run_id}`
  - `GET /tool-calls`
  - `GET /tool-calls/{tool_call_id}`
  - `GET /llm-api-logs`
  - `GET /llm-api-logs/{log_id}`
- `api/admin/log_routes.py`
  - `GET /audit-logs`
  - `GET /logs`
  - `POST /logs/frontend-error`
  - `GET /logs/{name}`

优点：

- 主要是只读 DB 查询和日志读取，业务副作用少。
- 现有测试已经覆盖核心后端合约：`tests/test_prompt_trace_admin.py`、
  `tests/test_admin_logs_viewer.py`、`tests/test_admin_api.py::TestToolAdmin`
  的审计日志读取路径。
- 与上一阶段 `docs/plan_walkthrough.md` 的「trace / observability 只读边界」
  建议一致。
- 不触碰普通 `api/routes.py` 的 `verify_token` 兼容问题。
- 不涉及 `asyncio.run()`，也不需要同步函数包装 awaitable。

风险：

- `GET /logs/{name}` 是动态路由，必须保证 `GET /logs` 和静态子路径先注册。
- 现有 `tests/test_admin_logs_viewer.py` monkeypatch
  `api.admin_routes.__file__` 来构造临时日志目录。迁移后需要通过日志目录 helper
  兼容旧 monkeypatch，或更新测试 patch 新模块。为降低兼容风险，设计要求 helper
  优先识别 `api.admin_routes.__file__` 的测试 patch。
- `AdminAuditLog` 仍被其他 eval / timing proposal 路由使用，不能从
  `api/admin_routes.py` 顶层无脑删除。

### 方案 B：拆分 Tools 管理路由

范围为 `GET /tools` 到 `GET /tools/decisions`，可新建 `api/admin/tool_routes.py`。

优点：

- 前端已经有独立 `ToolsPage.jsx`，接口调用集中。
- 现有 `tests/test_admin_api.py::TestToolAdmin` 覆盖主要读写行为。
- 边界比 reply/eval 工作台更干净。

风险：

- 依赖 runtime snapshot、`server.app.state.bridge`、tool registry、runtime tool
  service 和审计写入，行为副作用多于 Observability。
- 需要复制或抽取 `_raw_group_id()`、`_runtime_snapshot()`、`_iso()` 等 helper，
  容易牵动当前仍留在 `admin_routes.py` 的 group / timing / model replies 路径。

结论：作为下一阶段候选保留，但不作为本阶段第一刀。

### 方案 C：拆分 Models 管理路由

范围为模型状态、provider、catalog、route 编辑、route test、本地组件测试和
`/models/health-check`。可新建 `api/admin/model_routes.py`。

优点：

- 行数收益最大，预计可迁出 1100 行以上。
- 模型管理是清晰业务域，前端 `ModelsPage.jsx` 也相对集中。

风险：

- 配置写入、settings invalidate、Prompt Runtime 间接调用、local model 懒加载、
  TimingGate stability test 和 route test 都有运行时副作用。
- `/model-replies` 物理夹在模型区间中，但语义是观测日志，不应硬切。
- 测试需要大量 monkeypatch，设计和实现成本高于当前需要的低风险推进。

结论：适合后续单独设计，不作为本阶段第一刀。

## 目标

将管理端观测相关 HTTP 层从 `api/admin_routes.py` 拆到专用模块，同时保持所有
HTTP 路径、响应结构、鉴权行为、旧导入路径和测试 monkeypatch 兼容。

拆分后：

- `api/admin_routes.py` 继续作为 `/api/v1/admin` 聚合 router。
- `api/admin_routes.py` include `trace_router` 和 `log_router`。
- 旧 `api.admin_routes` re-export 迁移后的 request model、helper 和 endpoint，
  兼容历史测试或外部导入。
- 新模块使用 `api.admin.common.verify_admin`，不反向导入 `api.admin_routes`。
- 不改变 DB schema、response shape、状态码、日志文件读取策略、审计过滤语义。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

## 模块边界

### `api/admin/trace_routes.py`

职责：

- 查询 agent run 列表和详情。
- 查询 tool call 列表和详情。
- 查询 LLM API request log 列表、统计和详情。

迁移符号：

- `list_agent_runs`
- `get_agent_run`
- `list_tool_calls`
- `get_tool_call`
- `list_llm_api_logs`
- `get_llm_api_log`

依赖：

- `APIRouter`、`Depends`、`HTTPException`、`Query`
- `func`
- `Session`
- `datetime`
- `api.admin.common.verify_admin`
- `core.database.get_db`
- `core.database.AgentRun`
- `core.database.ToolCall`
- `core.database.PromptRenderLog`
- `core.database.LLMApiRequestLog`
- `core.database.ReplyContractCheckLog`
- `core.tracing.row_to_dict`

约束：

- `GET /agent-runs` 必须先于 `GET /agent-runs/{run_id}` 注册。
- `GET /tool-calls` 必须先于 `GET /tool-calls/{tool_call_id}` 注册。
- `GET /llm-api-logs` 必须先于 `GET /llm-api-logs/{log_id}` 注册。
- `include_payload=false` 时继续返回 summary item，不暴露 `request_json` 和
  `response_json`。
- `stats.success`、`stats.failed_error`、`stats.created`、`stats.avg_latency_ms` 和
  `stats.unbound_run_count` 字段保持不变。

### `api/admin/log_routes.py`

职责：

- 查询 admin audit log。
- 列出 `data/` 目录中的日志文件。
- 读取日志尾部、增量读取、按 level / query 过滤、聚合 ERROR 事件上下文。
- 接收前端错误日志并写入 `nanobot.admin` logger。

迁移符号：

- `FrontendErrorBody`
- `_is_allowed_log_name`
- `_log_level_of`
- `_group_log_level_events`
- `list_audit_logs`
- `list_log_files`
- `read_log`
- `log_frontend_error`

依赖：

- `APIRouter`、`Depends`、`HTTPException`
- `BaseModel`、`Field`
- `Any`
- `datetime`
- `logging`
- `os`
- `glob`
- `re`
- `deque`
- `api.admin.common.verify_admin`
- `core.database.get_db`
- `core.database.AdminAuditLog`

约束：

- `POST /logs/frontend-error` 在 `GET /logs/{name}` 之前注册，避免后续新增静态
  `GET /logs/...` 时形成顺序坏味道。
- `GET /logs` 必须先于 `GET /logs/{name}` 注册。
- 路径安全策略保持不变：文件名取 `basename`，只允许 `nanobot.log`、
  `nanobot.log.*`、`*.log` 或包含 `.log.` 的单段文件名，最终路径必须位于
  项目 `data/` 目录内。
- `lines=all`、非法 `lines` 返回 400、`since_bytes` 增量读取、`q` / `level`
  过滤和 `group_errors` 聚合语义保持不变。
- 兼容旧测试：日志目录解析 helper 需要能响应
  `api.admin_routes.__file__` 被 monkeypatch 后的路径。

## `api/admin_routes.py` 聚合职责

`api/admin_routes.py` 只做以下改动：

- 导入 `trace_router` 和 `log_router`。
- 在合适位置 include 两个 router。
- 从新模块 re-export 迁移符号。
- 删除旧文件中对应 route 和 helper 的本地定义。
- 保留其他子域仍使用的顶层 import 和 helper，例如 `_safe_json()`、`row_to_dict`、
  `AdminAuditLog`、`_audit_request()`、`_client_ip()`。

include 顺序：

- `trace_router` 可以放在已拆 router 之后、仍留在本地 `/models/status` 之前。
- `log_router` 可以放在 `trace_router` 附近或已拆 router 之后。由于其路径不与
  `/groups/{group_id:path}` 等 catch-all 冲突，只需保证子 router 内部顺序正确。

## 测试策略

### 新增拆分测试

新增 `tests/test_admin_observability_routes_split.py`，覆盖：

- 所有 trace 路由 endpoint module 为 `api.admin.trace_routes`。
- 所有 log / audit 路由 endpoint module 为 `api.admin.log_routes`。
- 迁移路由没有重复注册。
- 旧 `api.admin_routes` re-export 与新模块对象相同。
- monkeypatch `api.admin_routes.NANOBOT_ADMIN_TOKEN` 后，新模块路由接受新 token、
  拒绝旧 token。
- `POST /logs/frontend-error` 和 `GET /logs/{name}` 均存在，且静态路由不会被动态
  日志路由影响。

### 现有行为回归

定向运行：

- `tests/test_prompt_trace_admin.py`
- `tests/test_admin_logs_viewer.py`
- `tests/test_admin_api.py::TestObservabilityAPI`
- `tests/test_admin_api.py::TestToolAdmin::test_tools_have_separate_superuser_private_default_template`
  中的审计日志读取路径可作为 audit log 兼容烟测。
- `tests/test_asyncio_run_policy.py`
- `tests/test_admin_api.py::TestAuth`

### 全量验证

提交实现前运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

同时运行：

```bash
python -m compileall api/admin_routes.py api/admin/trace_routes.py api/admin/log_routes.py -q
git diff --check -- api/admin_routes.py api/admin/trace_routes.py api/admin/log_routes.py tests/test_admin_observability_routes_split.py
```

## 非目标

- 不拆 `api/routes.py`。
- 不迁移模型管理、工具管理、reply-test、reply-eval 或 eval 工作台。
- 不迁移 `/db/backup` 和 `/db/vacuum`。
- 不迁移 `/model-replies`。
- 不调整 Prompt Runtime 模板、`enriched_query`、工具 usage 文档或 prompt runtime
  输入变量。
- 不改变日志内容脱敏策略，不扩大 payload 暴露范围。
- 不新增 `asyncio.run()` 或 `run_awaitable_sync()`。

## 后续顺序建议

完成 Observability 拆分后，继续 P3 超大文件队列：

1. 拆分 Tools 管理路由到 `api/admin/tool_routes.py`。
2. 单独设计 Models 管理路由拆分，明确 `/model-replies` 留在观测域。
3. 再评估 reply-eval / eval 工作台是否需要分多刀拆分。
4. 普通 `api/routes.py` 拆分前，先设计共享 `verify_token` 兼容层。
