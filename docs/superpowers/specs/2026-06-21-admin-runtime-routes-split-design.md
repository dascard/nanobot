# Admin Runtime / Overview 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍未完成。上一阶段已经将
Admin Eval Workbench 从 `api/admin_routes.py` 拆到 `api/admin/eval_routes.py`，
父模块行数降至 1390 行。当前管理端聚合模块仍保留多块职责：

- Runtime / Overview：`/overview`、`/groups`、`/groups/{group_id:path}`、
  `/timing-gate/events`、`/timing-gate/test`。
- Block / ContentBlock。
- Configs / Prompt effective preview。
- `/model-replies`。
- DB backup / vacuum。
- Settings。

已完成拆分形成了稳定模式：

- `api.admin_routes.router` 继续作为 `/api/v1/admin` 聚合 router。
- 子模块暴露自己的 `router`，由 `api.admin_routes` include。
- 子模块使用 `api.admin.common.verify_admin`，兼容
  `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch。
- `api.admin_routes` re-export 迁移后的 request model、helper 和 endpoint，保持旧导入路径。
- 拆分测试锁定 endpoint module、legacy import、token monkeypatch、重复注册、动态路由顺序、
  协程边界和禁止反向导入 / 同步 awaitable 包装。

普通 `api/routes.py` 当前仍是更大的文件，但拆普通业务路由前需要先抽
`verify_token` common auth，并保持 `api.routes.verify_token is api.common_auth.verify_token`，
否则 FastAPI dependency override 与旧 monkeypatch 容易失效。该前置设施适合单独设计。
本阶段继续沿管理端成熟拆分模式推进。

## 候选方案

### 方案 A：拆分 Runtime / Overview 路由（推荐）

范围为 `api/admin_routes.py` 中 `# Observability / Runtime` 区块：

- `GET /overview`
- `GET /groups`
- `GET /groups/{group_id:path}`
- `GET /timing-gate/events`
- `POST /timing-gate/test`

优点：

- 能继续降低 `api/admin_routes.py` 行数，并把运行态面板与配置、block、DB 管理等职责分开。
- 现有 `tests/test_admin_api.py` 已覆盖 overview counters、群列表 / 详情、TimingGate events
  stats / scoring、`timing_gate_test()` 协程边界和 `repeats <= 5` 校验。
- 迁移对象集中在运行态只读查询和一个异步 TimingGate 手测 endpoint，不触碰聊天主链路。
- 可复用现有 split 测试模式，锁定旧导入兼容和路由顺序。

风险：

- `/groups/{group_id:path}` 是 catch-all，必须继续排在已拆的
  `/groups/{group_id:path}/memories` 与 `/groups/{group_id:path}/memories/extract` 之后。
- `group_detail()` 会调用 `list_groups()`，迁移后这两个 endpoint 必须留在同一模块或通过明确
  helper 调用，避免反向导入父模块。
- 该区块依赖 `ChatLog`、`User`、`ChatStreamConfig`、`StickerMemory`、`UserBlockRule`、
  `config` 健康检查、`core.timing_runtime`、`core.prompt_v2.template_registry` 和
  `core.sticker_preview`，测试需要覆盖导入边界。
- `timing_gate_test()` 必须保持 `async def`，继续使用 `await asyncio.to_thread(...)`，
  不允许退回同步路由或引入 `asyncio.run()`。

结论：采用。

### 方案 B：拆分 Settings

范围为 `/settings*` 相关 endpoint。

优点：

- 路由前缀相对独立，没有 catch-all 顺序风险。
- 配置读写职责明确。

风险：

- 行数收益低于 Runtime / Overview。
- 现有测试锚点相对分散，需要先补更多行为回归。

结论：保留为后续管理端拆分候选。

### 方案 C：先抽普通 API common auth

范围为 `api/routes.py` 的 `verify_token()` 与 `NANOBOT_API_TOKEN` 兼容层。

优点：

- 为后续拆普通 API 最大文件解除 blocker。

风险：

- 本阶段行数收益小。
- 鉴权函数对象身份、dependency override、生产 token 读取和旧测试 monkeypatch 都是公共兼容面，
  应单独设计和验证。

结论：后续作为普通 API 拆分前置阶段。

## 目标

将 Admin Runtime / Overview HTTP 层从 `api/admin_routes.py` 拆到
`api/admin/runtime_routes.py`，保持：

- 所有 `/api/v1/admin/overview`、`/api/v1/admin/groups*`、
  `/api/v1/admin/timing-gate/*` HTTP path、method、status code 和 response shape 不变。
- `overview()` 的 counters、health、model summary 与 TimingGate stats 字段不变。
- `list_groups()` 的群 ID 归一化、runtime snapshot 合并、消息计数和最近决策字段不变。
- `group_detail()` 的 group、runtime、ambient messages、bot replies、timing events、blocked rules
  和 sticker records 字段不变。
- `timing_gate_events()` 的分页、错误过滤、parse error 过滤、stats 和 scoring 字段不变。
- `timing_gate_test()` 保持 coroutine function，`TimingGateTestRequest.repeats` 仍限制为 1 到 5。
- `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 继续影响拆分后的 runtime 路由。
- `api.admin_routes` 继续 re-export 迁移后的 request model、helper 和 endpoint。
- Group Memory 子路由继续先于 `/groups/{group_id:path}` catch-all 注册。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。

## 模块边界

### 新增 `api/admin/runtime_routes.py`

职责：

- 暴露管理端运行态 overview。
- 聚合群运行时列表与群详情。
- 查询 TimingGate 事件与统计。
- 提供 TimingGate 手动测试 endpoint。

推荐模块头：

```python
"""Admin Runtime / Overview 路由。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from api.admin.sticker_routes import _sticker_dict
from core.database import (
    ChatLog,
    ChatStreamConfig,
    StickerMemory,
    User,
    UserBlockRule,
    get_db,
)

router = APIRouter(tags=["admin-runtime"])
```

新模块不设置 `/api/v1/admin` prefix，因为父模块已经提供该 prefix。新模块不得导入
`api.admin_routes`。

### 迁移 endpoint

纳入 `api/admin/runtime_routes.py`：

- `GET /overview` -> `overview()`
- `GET /groups` -> `list_groups()`
- `GET /groups/{group_id:path}` -> `group_detail()`
- `GET /timing-gate/events` -> `timing_gate_events()`
- `POST /timing-gate/test` -> `timing_gate_test()`

不纳入：

- `/groups/{group_id:path}/memories` 与 `/groups/{group_id:path}/memories/extract`：
  继续属于 `api/admin/group_memory_routes.py`，并且必须先于 catch-all 注册。
- `/traces/*`、`/agent-runs/*`、`/tool-calls/*`、`/llm-api-logs/*`：
  已属于 `api/admin/trace_routes.py`。
- `/logs/*` 与 `/frontend-error`：已属于 `api/admin/log_routes.py`。
- `/models/timing-gate-stability-test`：已属于 `api/admin/model_routes.py`，不是本阶段手测 endpoint。
- Block / ContentBlock、Configs、Prompt effective preview、Settings、DB backup / vacuum、
  `/model-replies`。

### 迁移 request model 和 helper

迁移到 `api/admin/runtime_routes.py`：

- `TimingGateTestRequest`
- `_timing_meta()`
- `_timing_event_dict()`
- `_timing_stats()`
- `_runtime_snapshot()`

在新模块内保留私有实现，但不要求父模块 legacy identity：

- `_safe_dict()`
- `_iso()`
- `_age_seconds()`
- `_raw_group_id()`
- `_group_session_id()`
- `_group_stream_id()`

`_block_dict()` 暂不迁移为公共 block 模块，因为 Block / ContentBlock CRUD 仍留在父模块。本阶段在
`api/admin/runtime_routes.py` 内保留一份仅用于 `group_detail()` response 的私有 `_block_dict()`，
并让 `api.admin_routes._block_dict` 继续服务父模块 block CRUD。`_safe_dict()`、`_iso()`、
`_raw_group_id()` 和 `_group_stream_id()` 也仍被父模块后续 Configs / ContentBlock /
`/model-replies` 区块使用，因此父模块保留旧实现；新模块的同名 helper 作为 Runtime 私有副本。
该重复只限临时拆分过渡，后续拆 Block / Configs 路由时再收敛。

`_sticker_dict()` 已在 `api/admin/sticker_routes.py` 导出，`group_detail()` 继续复用该 helper，
保持 sticker record response shape 不变。

### 修改 `api/admin_routes.py`

`api/admin_routes.py` 只做聚合和兼容：

- 从 `api.admin.runtime_routes` 导入 `router as runtime_router`。
- 在 `group_memory_router` 之后 include `runtime_router`，确保 group memory 子路由先注册。
- re-export 迁移后的 `TimingGateTestRequest`、Runtime 专属 helper 和 endpoint。
- 删除本地 `# Observability / Runtime` 区块中的迁移代码。
- 保留父模块仍使用的 `_safe_json()`、`_block_dict()`、`_config_dict()`、`_safe_dict()`、
  `_iso()`、`_raw_group_id()`、`_group_stream_id()` 等 helper，除非引用扫描确认无用。
- 删除迁移后不再需要的 `asyncio`、`os`、`timedelta`、`text` 等父模块 import；仅在引用扫描确认
  父模块其他区块不再使用时删除。

include 顺序目标：

```python
router.include_router(sticker_router)
router.include_router(group_memory_router)
router.include_router(runtime_router)
router.include_router(tool_router)
```

该顺序要求 `/groups/{group_id:path}/memories*` 仍先于 `/groups/{group_id:path}`。

## 测试策略

新增 `tests/test_admin_runtime_routes_split.py`：

- 锁定 5 个 route 的 endpoint module 为 `api.admin.runtime_routes`。
- 锁定 `api.admin_routes` 对迁移符号的旧导入兼容。
- 锁定 `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对新模块路由仍生效。
- 锁定迁移路由未重复注册。
- 锁定 Group Memory 子路由仍先于 `/groups/{group_id:path}` catch-all。
- 锁定 `timing_gate_test()` 仍是 coroutine function。
- 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。

复用现有行为回归：

- `tests/test_admin_api.py::TestAdminApi::test_overview_counts_recent_runtime_signals`
- `tests/test_admin_api.py::TestAdminApi::test_group_list_and_detail_expose_recent_decision`
- `tests/test_admin_api.py::TestAdminApi::test_timing_gate_events_returns_stats`
- `tests/test_admin_api.py::TestAdminApi::test_timing_gate_events_returns_scoring`
- `tests/test_admin_api.py::TestAdminApi::test_timing_gate_test_route_is_async`
- `tests/test_admin_api.py::TestAdminApi::test_timing_gate_test_repeats_is_capped_to_five`
- `tests/test_admin_group_memory_routes_split.py`
- `tests/test_asyncio_run_policy.py`

红灯预期：

- 新测试在迁移前运行时，endpoint module 仍为 `api.admin_routes`，`api.admin.runtime_routes`
  文件不存在，因此 route module、legacy import 和静态扫描相关断言失败。

绿灯预期：

- 迁移后新 split 测试通过。
- Admin API 行为回归通过。
- Group Memory 路由顺序回归通过。
- asyncio 策略回归通过。

最终验证：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_admin_runtime_routes_split.py
python -B -m pytest -q -p no:cacheprovider \
  tests/test_admin_api.py \
  tests/test_admin_group_memory_routes_split.py \
  tests/test_asyncio_run_policy.py
python -B -m compileall api/admin_routes.py api/admin/runtime_routes.py
git diff --check
python -B -m pytest -p no:cacheprovider tests/ -v
```

## 子 agent 分工

本阶段允许并行只读和分离写入：

- **Explorer A：迁移边界。** 只读检查 `api/admin_routes.py` Runtime / Overview 区块、
  依赖 import、helper 共享和 route order 风险，输出文件名与行号。
- **Explorer B：测试契约。** 只读检查现有 `tests/test_admin_api.py` 和 split 测试模式，
  输出新增测试建议与红灯预期。
- **Worker A：测试文件。** 如启用写入，范围仅限 `tests/test_admin_runtime_routes_split.py`。
- **Worker B：生产代码。** 如启用写入，范围仅限 `api/admin/runtime_routes.py` 与
  `api/admin_routes.py`；必须等红灯测试已提交或至少已验证失败后再开始。
- **Reviewer：验证审查。** 只读检查 diff、route order、反向导入、asyncio 策略和测试输出。

主线程负责审查子 agent 结论、集成代码、运行验证和提交。

## 非目标

- 不拆普通 `api/routes.py`。
- 不抽普通 API `verify_token` common auth。
- 不拆 Block / ContentBlock、Configs、Settings、DB backup / vacuum 或 `/model-replies`。
- 不修改 Prompt Runtime 模板、runtime template 文件或 prompt 变量注册。
- 不调整 TimingGate scoring、模型路由、prompt 文案或评测 baseline。
- 不改变 DB schema。

## 风险与缓解

- **catch-all 吞路由：** include `runtime_router` 必须晚于 `group_memory_router`，新增测试比较
  route index。
- **旧导入失效：** 父模块 re-export 迁移后的 request model、helper 和 endpoint，新增测试逐项比较
  `api.admin_routes` 与 `api.admin.runtime_routes` 对象身份；仍被父模块其他区块使用的通用 helper
  不纳入 identity 比较。
- **鉴权 monkeypatch 失效：** 新模块使用 `api.admin.common.verify_admin`，该 helper 已优先读取
  `sys.modules["api.admin_routes"].NANOBOT_ADMIN_TOKEN`。
- **同步阻塞回退：** `timing_gate_test()` 必须保持 `async def` 与 `asyncio.to_thread()`，
  新测试锁定 coroutine boundary，asyncio 策略测试锁定禁用模式。
- **父模块循环依赖：** 新模块不得导入 `api.admin_routes`；静态扫描锁定。
- **Prompt Runtime 误改：** 本阶段只读取模板目录健康状态，不改变模板变量、输入结构或默认模板内容。
