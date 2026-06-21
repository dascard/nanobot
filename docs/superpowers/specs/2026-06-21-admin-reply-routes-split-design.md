# Admin Reply Eval 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍未完成。当前剩余硬项为
`api/admin_routes.py` 2647 行和 `api/routes.py` 2822 行。管理端已经完成 DB Browser、
Sticker / Generated Images、Group Memory、Observability、Tools 和 Models 多刀拆分，
并形成稳定模式：

- `api.admin_routes.router` 继续作为 `/api/v1/admin` 聚合 router。
- 子模块暴露自己的 `router`，由 `api.admin_routes` include。
- 子模块使用 `api.admin.common.verify_admin`，兼容
  `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch。
- `api.admin_routes` re-export 迁移后的 request model、常量、helper 和 endpoint，
  保持旧导入路径。

本阶段继续沿管理端拆分。普通 `api/routes.py` 暂不处理，因为 `verify_token()` 仍定义在
`api.routes` 内，现有测试和外部兼容面依赖 `api.routes.verify_token` dependency override
以及 `api.routes.NANOBOT_API_TOKEN` monkeypatch。普通 API 拆分前需要先设计 common auth
兼容层，避免子路由反向导入 `api.routes` 或意外改变鉴权对象。

## 候选方案

### 方案 A：拆分 Admin Reply Eval 路由（推荐）

范围为 `api/admin_routes.py` 中 `# Reply 手动测试 / A-B 评估` 区块。新建
`api/admin/reply_routes.py`，承载 `POST /reply-test/run` 与 `/reply-eval/*` 路由，
包括手动回复测试、评测 case CRUD、生成样本预览与保存、批量运行、运行记录查询和
真实 reply contract traffic 聚合。

优点：

- 行数收益最高，预计从 `api/admin_routes.py` 迁出约 735 行。
- HTTP 命名空间集中在 `/reply-test` 和 `/reply-eval`，没有 catch-all 路由顺序问题。
- WebUI 已有独立 `webui/src/features/reply-eval/ReplyEvalPage.jsx`。
- 现有 `tests/test_reply_admin.py` 覆盖旧父模块导入、手动测试、Prompt Runtime metadata、
  合约重试、case CRUD、生成样本、运行记录和 traffic 聚合。
- 拆分只移动 HTTP 层和本地 helper，不改变 KT Bridge、Prompt Runtime、ReplyContract
  tracer 或 eval 数据结构。

风险：

- `POST /reply-test/run` 和 `POST /reply-eval/run` 都会调用
  `nanobot_kt.bridge.get_bridge().handle_message()`。迁移后必须保持 async endpoint 直接
  `await`，不能引入 `asyncio.run()` 或同步 awaitable 包装。
- `_run_reply_test_once()` 会向 bridge metadata 注入
  `prompt_runtime_engine_override`、`enable_reply_contract_retry`、`persona_text`、
  `history_header`、`dry_run` 等字段。拆分不能改变字段名、默认值或组合逻辑。
- `tests/test_reply_admin.py` 直接从 `api.admin_routes` 导入 `ReplyTestRunRequest` 和
  `_resolve_reply_test_prompt_settings`；父模块必须继续 re-export。
- Reply Eval 会读写 `ReplyEvalCase`、`ReplyEvalRun`、`ReplyEvalResult`，并读取
  `AgentRun`、`LLMApiRequestLog`、`ReplyContractCheckLog`。迁移时不能改变查询排序、
  JSON 解析或 metrics 口径。
- 新模块不能反向导入 `api.admin_routes`。

### 方案 B：拆分 Admin Eval Workbench 路由

范围为 `/evals/*`，包括 expected contract、TimingGate 调参提案、候选样本运营、采样、
评测运行和运行记录查询。

优点：

- 主要依赖集中在 `core.eval_sampling.store`、`evals.expected_contract` 和 `evals.run`，
  不进入 KT Bridge 或 Prompt Runtime 编排。
- 现有 `tests/test_eval_candidate_contract.py` 与
  `tests/test_timing_tuning_proposal_admin.py` 覆盖较完整。

风险：

- `/evals/candidates/{case_id}` 与 `/evals/candidates/preflight`、`/batch-audit`、
  `/trend` 共用前缀，拆分时必须额外锁定静态路径先于动态路径。
- TimingGate proposal 测试会 monkeypatch 父模块常量
  `api.admin_routes.TIMING_TUNING_PROPOSAL_REPORT`。如果 endpoint 改读新模块常量，
  需要额外兼容旧路径 monkeypatch。

结论：保留为后续阶段。本阶段优先选择行数收益更高且 route 顺序风险更低的 Reply Eval。

### 方案 C：拆分 Runtime / Overview 路由

范围为 `/overview`、`/groups`、`/groups/{group_id:path}`、`/timing-gate/events` 和
`/timing-gate/test`。

优点：

- 管理端运行态面板边界清晰。
- 可以进一步清理 `_timing_*`、`_group_*` 和 `_runtime_snapshot()` helper。

风险：

- `/groups/{group_id:path}` 是 catch-all，必须继续排在 Group Memory 已拆路由之后。
- runtime 面板横跨 group、sticker、block、config、timing gate 和 prompt runtime 状态，
  helper 依赖面较宽。
- 行数收益低于 Reply Eval。

结论：后续再拆。

## 目标

将 Admin Reply Eval HTTP 层从 `api/admin_routes.py` 拆到 `api/admin/reply_routes.py`，
保持：

- 所有 `/api/v1/admin/reply-test/*` 与 `/api/v1/admin/reply-eval/*` HTTP path、
  method、status code 和 response shape 不变。
- `ReplyTestRunRequest` 默认值、variant 映射和 retry 开关语义不变。
- `_run_reply_test_once()` 传入 KT Bridge 的 metadata 字段不变。
- `reply_test_run()`、`reply_eval_run()` 继续是 async endpoint，直接 await 内部异步函数。
- Reply Eval case CRUD、生成样本、运行结果、metrics 和 traffic 聚合语义不变。
- `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 继续影响拆分后的 reply 路由。
- `api.admin_routes` 继续 re-export 迁移后的 request model、helper 和 endpoint。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、`enriched_query`、task template 变量、工具 usage 文档或
  prompt compile 输入。

## 模块边界

### 新增 `api/admin/reply_routes.py`

职责：

- 执行单条 reply 手动测试。
- 解析 reply contract check logs、LLM request logs 和 AgentRun metadata。
- 管理 Reply Eval case。
- 生成和保存推荐的 Reply Eval case。
- 批量运行 Reply Eval case，并保存 `ReplyEvalRun` 与 `ReplyEvalResult`。
- 统计真实 traffic 中的 reply contract 命中、miss、retry、overcall 和失败样本。
- 查询 Reply Eval run 列表和详情。

推荐模块头：

```python
"""Admin Reply Eval 路由。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.database import get_db
from core.tracing import row_to_dict

router = APIRouter(tags=["admin-reply"])
```

新模块不设置 prefix，因为它同时承载 `/reply-test` 和 `/reply-eval` 两个命名空间。新模块不导入
`api.admin_routes`。

### 迁移端点

纳入 `api/admin/reply_routes.py`：

- `POST /reply-test/run` -> `reply_test_run()`
- `GET /reply-eval/cases` -> `reply_eval_list_cases()`
- `POST /reply-eval/cases` -> `reply_eval_create_case()`
- `PUT /reply-eval/cases/{case_id}` -> `reply_eval_update_case()`
- `DELETE /reply-eval/cases/{case_id}` -> `reply_eval_delete_case()`
- `POST /reply-eval/generate-preview` -> `reply_eval_generate_preview()`
- `POST /reply-eval/save-generated` -> `reply_eval_save_generated()`
- `POST /reply-eval/run` -> `reply_eval_run()`
- `GET /reply-eval/traffic` -> `reply_eval_real_traffic()`
- `GET /reply-eval/runs` -> `reply_eval_list_runs()`
- `GET /reply-eval/runs/{run_id}` -> `reply_eval_get_run()`

不纳入：

- `/evals/*`：属于 Eval Workbench，后续单独拆分。
- `GET /model-replies`：属于回复日志观测，当前 `tests/test_admin_model_routes_split.py`
  已明确要求仍留在父模块。
- `/settings/*`：属于热重载配置。
- `/db/backup`、`/db/vacuum`：属于 DB 运维。
- 普通 `api/routes.py` 的聊天、群聊、memory、tasks 或公开 media endpoint。

### 迁移 request model

- `ReplyTestRunRequest`
- `ReplyEvalCaseIn`
- `ReplyEvalCasePatch`
- `ReplyEvalSaveGeneratedIn`
- `ReplyEvalRunIn`

### 迁移 helper

- `_loads_json_list()`
- `_reply_case_to_dict()`
- `_reply_eval_run_to_dict()`
- `_reply_log_attempt()`
- `_reply_contract_has_final_action()`
- `_reply_contract_run_key()`
- `_is_reply_eval_test_session()`
- `_safe_rate()`
- `_resolve_reply_test_prompt_settings()`
- `_run_reply_test_once()`
- `_upsert_reply_eval_case()`

这些 helper 只在 Reply Eval 域内使用，迁移后不从父模块导入。旧路径通过
`api.admin_routes` re-export 保持兼容。

### 修改 `api/admin_routes.py`

`api/admin_routes.py` 只做聚合和兼容：

- 导入 `router as reply_router`。
- 在 include 区新增 `router.include_router(reply_router)`。
- re-export 上述 request model、helper 和 endpoint。
- 删除本地 `# Reply 手动测试 / A-B 评估` 区块。
- 保留 `/model-replies`、`/evals/*`、Settings、DB 运维和其他本阶段外的本地实现。

## 路由顺序

新模块不包含 catch-all，但仍需保持静态路径先于动态路径：

1. `POST /reply-test/run`
2. `GET /reply-eval/cases`
3. `POST /reply-eval/cases`
4. `PUT /reply-eval/cases/{case_id}`
5. `DELETE /reply-eval/cases/{case_id}`
6. `POST /reply-eval/generate-preview`
7. `POST /reply-eval/save-generated`
8. `POST /reply-eval/run`
9. `GET /reply-eval/traffic`
10. `GET /reply-eval/runs`
11. `GET /reply-eval/runs/{run_id}`

`/reply-eval/runs` 必须早于 `/reply-eval/runs/{run_id}`。

## 兼容策略

### 认证

新模块必须使用 `api.admin.common.verify_admin`。该函数会从
`sys.modules["api.admin_routes"].NANOBOT_ADMIN_TOKEN` 读取当前 token，因此现有
`monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "...")` 测试仍然生效。

### Prompt Runtime metadata

拆分不改变 Prompt Runtime 输入契约。迁移时必须逐字段保持 `_run_reply_test_once()` 的
metadata：

- `trace_id`
- `chat_type`
- `is_group`
- `group_id`
- `sender_id`
- `sender_name`
- `character_name`
- `persona_text`
- `history_header`
- `variant`
- `prompt_runtime_engine_override`
- `enable_reply_contract_retry`
- `dry_run`

不得新增 `prompt_system_mode_override`，不得改变 `_resolve_reply_test_prompt_settings()` 对旧 variant
和 v2 命名 variant 的映射。

### 数据库与 tracing

迁移后保持：

- `AgentRun` 按 `trace_id` 和 `started_at.desc()` 取最新 run。
- `ReplyContractCheckLog` 按 `attempt.asc()`、`created_at.asc()` 排序。
- `LLMApiRequestLog` 按 `created_at.asc()` 排序。
- Reply Eval run 先创建 `ReplyEvalRun`，逐 case 写 `ReplyEvalResult`，最后汇总 metrics 并
  `db.commit()`。
- traffic 统计默认排除 reply-test、reply-eval、test、smoke 等测试 session。

### Legacy import

`api.admin_routes` 继续导出迁移符号，保证旧测试和潜在脚本继续工作：

- request model：`ReplyTestRunRequest`、`ReplyEvalCaseIn`、`ReplyEvalCasePatch`、
  `ReplyEvalSaveGeneratedIn`、`ReplyEvalRunIn`
- helper：`_loads_json_list`、`_reply_case_to_dict`、`_reply_eval_run_to_dict`、
  `_reply_log_attempt`、`_reply_contract_has_final_action`、`_reply_contract_run_key`、
  `_is_reply_eval_test_session`、`_safe_rate`、`_resolve_reply_test_prompt_settings`、
  `_run_reply_test_once`、`_upsert_reply_eval_case`
- endpoint：全部 `/reply-test/*` 与 `/reply-eval/*` endpoint 函数

## 测试计划

新增 `tests/test_admin_reply_routes_split.py`，覆盖：

- 11 个 `/api/v1/admin/reply-test/*` 和 `/api/v1/admin/reply-eval/*` endpoint 已注册，
  endpoint module 均为 `api.admin.reply_routes`。
- 所有迁移路由没有重复注册。
- `api.admin_routes` legacy import 与 `api.admin.reply_routes` 对应符号相同。
- `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 继续影响拆分后的 reply 路由。
- `/reply-eval/runs` 早于 `/reply-eval/runs/{run_id}`。
- `reply_test_run()`、`reply_eval_run()` 和 `_run_reply_test_once()` 仍是 coroutine function。
- 新模块源码不包含 `from api.admin_routes`、`import api.admin_routes`、`asyncio.run` 或
  `run_awaitable_sync`。

复用现有行为回归：

- `tests/test_reply_admin.py`
- `tests/test_admin_model_routes_split.py::test_model_replies_stays_in_parent_admin_routes`
- `tests/test_admin_api.py::TestAuth`
- `tests/test_asyncio_run_policy.py`
- 已有 admin split 测试集合

全量验证：

- `python -m pytest tests/ -v`

## 验证计划

设计阶段：

- `git diff --check -- docs/superpowers/specs/2026-06-21-admin-reply-routes-split-design.md`
- `python -m pytest tests/ -v`

实现阶段：

1. 先新增 `tests/test_admin_reply_routes_split.py`，运行并确认红灯。预期失败为 endpoint module
   仍是 `api.admin_routes`、新模块不存在或 legacy import 不存在。
2. 新增 `api/admin/reply_routes.py`，迁移 `/reply-test/*` 和 `/reply-eval/*` request model、
   helper 与 endpoint。
3. 修改 `api/admin_routes.py` include `reply_router` 并 re-export 迁移符号，删除父模块本地
   Reply Eval 区块。
4. 运行拆分测试并确认绿灯。
5. 运行行为回归、鉴权回归、asyncio 策略回归、静态扫描、compileall 和全量测试。

## 非目标

- 不拆普通 `api/routes.py`。
- 不迁移 `/evals/*`、`GET /model-replies`、`/settings/*`、`/db/backup` 或 `/db/vacuum`。
- 不改变 KT Bridge、Prompt Runtime、ReplyContractTracer、LLMRequestTracer 或 RunTracer。
- 不改变 Reply Eval case schema、metrics 口径、traffic 聚合口径或生成样本内容。
- 不改变 Prompt Runtime 模板、工具 usage 文档或 `enriched_query` 组装。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。
