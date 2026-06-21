# Admin Tools 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍未完成。当前剩余硬项为
`api/admin_routes.py` 4303 行和 `api/routes.py` 2822 行。上一阶段已经从
`api/admin_routes.py` 拆出 Observability 路由到 `api/admin/trace_routes.py` 与
`api/admin/log_routes.py`，管理端已经形成稳定拆分模式：

- `api.admin_routes.router` 继续作为 `/api/v1/admin` 聚合 router。
- 子模块暴露自己的 `router`，由 `api.admin_routes` include。
- 子模块使用 `api.admin.common.verify_admin`，兼容
  `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch。
- `api.admin_routes` re-export 迁移后的 request model、helper 和 endpoint，保持旧导入路径。

本阶段继续沿管理端拆分，而不是先切换普通 `api/routes.py`。原因是普通 API 侧尚未建立
`verify_token` 共享兼容层，直接拆 `/chat`、`/group/message` 或公开 media 端点的鉴权
兼容风险更高。

## 候选方案

### 方案 A：拆分 Admin Tools 路由（推荐）

范围为 `api/admin_routes.py` 中 `# ── 工具管理 ──` 到 `# ── Model Health Check ──`
之间的 `/tools*` 路由，约 559 行。新建 `api/admin/tool_routes.py`，承载工具配置、
作用域覆盖、schema override、生效预览、目标列表和 runtime tool 决策查询。

优点：

- HTTP 路径集中在 `/tools`、`/tools/targets`、`/tools/effective`、`/tools/decisions`
  和 `/tools/{tool_name}/*`。
- 前端已有独立 `webui/src/features/tools/ToolsPage.jsx`。
- 现有 `tests/test_admin_api.py::TestToolAdmin` 覆盖主要行为。
- 与 `docs/plan_walkthrough.md` 上一阶段「下一刀推荐先 Tools」一致。
- 行数收益明确，预计 `api/admin_routes.py` 净减少约 530 行。

风险：

- `GET /tools` 会 lazy import `server.app` 并调用 bridge 的 `ensure_registry_probe()`。
  拆分后仍必须保持请求内 lazy import，不能在模块 import 时触发 bridge 或 server 初始化。
- runtime tool 解析有多层语义：默认值、`force_enabled`、`force_disabled_group`、
  `runtime_preset`、`ToolOverride` 和最终硬约束兜底。拆分只移动 HTTP 编排，不重构
  `core.runtime_tool_service`。
- schema/default/override 写入会 `db.commit()`、写 audit log，并在默认值更新后调用
  `settings.invalidate()`；审计 action、detail 结构和提交顺序必须保持不变。
- `GET /tools/targets` 会读取真实 group/user/platform 目标，并扫描最多 5000 条
  `ChatLog` / `ConversationTurn`。拆分不能改变筛选规则或引入额外 DB 写入。
- 新模块不能反向导入 `api.admin_routes`，否则会破坏当前聚合 router 模式。

### 方案 B：拆分 Admin Models 路由

范围包括 `/models/status`、provider、catalog、route 编辑、route test、本地组件测试、
`/model-replies`、TimingGate stability test 和 `/models/health-check`，行数收益最大。

优点：

- 可一次迁出 1000 行以上。
- 前端 `ModelsPage.jsx` 是独立页面，业务域清晰。

风险：

- 涉及配置写入、provider 凭据脱敏、远端 `/models` 探测、本地模型懒加载、
  image summary route cache 清理和 Prompt Runtime 间接调用。
- `/model-replies` 与模型配置区物理相邻，但语义更接近观测日志，不适合无脑一起迁移。
- `test_model_route()` 涉及真实 LLM route、vision payload 和 `asyncio.to_thread()`，
  需要更细的单独设计。

结论：保留为后续单独阶段，不作为本阶段第一刀。

### 方案 C：拆分普通 `api/routes.py` 的公开 sticker / media 边界

范围为 `/stickers/register`、`/stickers/search`、`/stickers/{id}/image`、
`/generated-images/{id}/image` 和相关 disable 路由。

优点：

- 不触碰 `/chat`、SSE、私聊缓冲、`/group/message` 和 timing timer 主链路。
- 行数收益中等，风险低于直接拆聊天主流程。

风险：

- 普通 API 侧 `verify_token` 仍定义在 `api.routes`，现有测试和外部调用可能 monkeypatch
  `api.routes.NANOBOT_API_TOKEN` 或 `app.dependency_overrides[routes.verify_token]`。
- 拆分前需要先设计 public API shared auth facade，范围会扩大。

结论：普通 API 拆分需要单独设计 `verify_token` 兼容层，本阶段暂不处理。

## 目标

将 Admin Tools HTTP 层从 `api/admin_routes.py` 拆到 `api/admin/tool_routes.py`，保持：

- 所有 `/api/v1/admin/tools*` HTTP 路径、method、状态码和 response shape 不变。
- 工具默认值、runtime preset、生效预览、platform override、schema override 和决策查询语义不变。
- audit action 和 detail 结构不变。
- `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 继续影响拆分后的工具路由。
- `api.admin_routes` 继续 re-export 迁移后的 request model、helper 和 endpoint。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

## 模块边界

### 新增 `api/admin/tool_routes.py`

职责：

- 列出工具配置状态和 runtime preset 预览。
- 列出可配置的 group/user/platform 目标。
- 读取、保存和删除 tool schema override。
- 更新工具默认值与 lightweight preset。
- 创建、删除作用域覆盖。
- 查询指定上下文的实际生效工具和 tool schemas。
- 查询 runtime tool 决策记录。

推荐模块头：

```python
"""Admin Tools 路由。"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import audit, client_ip, verify_admin
from core.database import ChatLog, ChatStreamConfig, ConversationTurn, SystemSetting, User, get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(prefix="/tools", tags=["admin-tools"])
```

迁移 request model：

- `ToolUpdateBody`
- `ToolOverrideBody`
- `ToolSchemaOverrideBody`

迁移常量和 helper：

- `_TEMP_TOOL_TARGET_EXACT`
- `_TEMP_TOOL_TARGET_PREFIXES`
- `_is_temp_tool_target_id()`
- `_tool_target_label()`

新模块本地保留小 helper，避免反向导入父模块：

- `_iso()`
- `_raw_group_id()`
- `_runtime_snapshot()`

迁移 endpoint：

- `list_tools()` -> `GET ""`
- `list_tool_targets()` -> `GET "/targets"`
- `get_tool_schema_override()` -> `GET "/{tool_name}/schema"`
- `save_tool_schema_override_api()` -> `PUT "/{tool_name}/schema"`
- `delete_tool_schema_override_api()` -> `DELETE "/{tool_name}/schema"`
- `update_tool_defaults()` -> `PUT "/{tool_name}"`
- `set_tool_override()` -> `PUT "/{tool_name}/override"`
- `delete_tool_override()` -> `DELETE "/{tool_name}/override"`
- `get_effective_tools()` -> `GET "/effective"`
- `list_runtime_preset_decisions()` -> `GET "/decisions"`

### 修改 `api/admin_routes.py`

`api/admin_routes.py` 只做聚合和兼容：

- 导入 `router as tool_router`。
- 在已拆 router include 区新增 `router.include_router(tool_router)`。
- re-export 迁移符号。
- 删除本地 `# ── 工具管理 ──` 区域的 request model、helper 和 endpoint。
- 保留仍被其他子域使用的 `_raw_group_id()`、`_runtime_snapshot()`、`_iso()`、
  `_audit()`、`_client_ip()`、`SystemSetting`、`ChatLog`、`ConversationTurn` 和 `User`。

不移动：

- `/models/health-check`
- `/models/*`
- `/model-replies`
- TimingGate stability test
- reply/eval 工作台
- settings
- 普通 `api/routes.py`

## 路由顺序

新模块内必须先注册静态路径，再注册动态路径：

1. `GET /tools`
2. `GET /tools/targets`
3. `GET /tools/effective`
4. `GET /tools/decisions`
5. `GET /tools/{tool_name}/schema`
6. `PUT /tools/{tool_name}/schema`
7. `DELETE /tools/{tool_name}/schema`
8. `PUT /tools/{tool_name}`
9. `PUT /tools/{tool_name}/override`
10. `DELETE /tools/{tool_name}/override`

这样可以防止未来新增 `GET /tools/{tool_name}` 时吞掉 `/tools/targets`、
`/tools/effective` 或 `/tools/decisions`。

## 兼容策略

### 认证

新模块必须使用 `api.admin.common.verify_admin`。该函数会从
`sys.modules["api.admin_routes"].NANOBOT_ADMIN_TOKEN` 读取当前 token，因此现有
`monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "...")` 测试仍然生效。

### 审计

新模块使用：

- `api.admin.common.audit()` 替代旧 `_audit()`
- `api.admin.common.client_ip()` 替代旧 `_client_ip()`

保持原 action：

- `tool_schema_override`
- `tool_schema_override_delete`
- `tool_default_update`
- `tool_override`
- `tool_override_delete`

保持原 detail 字段：

- schema override 写 `{"schema": result["editable_schema"]}`
- default update 写本次更新字段
- override 写 `scope_type`、`scope_id`、`enabled`、`reason`

### Lazy import

以下 import 必须留在函数体内：

- `from server import app`
- `from core.tool_registry import TOOL_METADATA`
- `from core.runtime_tool_service import ...`
- `from core.tool_schema_preview import ...`
- `from core.settings_service import settings`

这样可以避免模块导入阶段触发 server、bridge、KT registry 或 settings 副作用。

### 旧导入路径

`api.admin_routes` re-export：

- `ToolUpdateBody`
- `ToolOverrideBody`
- `ToolSchemaOverrideBody`
- `_TEMP_TOOL_TARGET_EXACT`
- `_TEMP_TOOL_TARGET_PREFIXES`
- `_is_temp_tool_target_id`
- `_tool_target_label`
- `list_tools`
- `list_tool_targets`
- `get_tool_schema_override`
- `save_tool_schema_override_api`
- `delete_tool_schema_override_api`
- `update_tool_defaults`
- `set_tool_override`
- `delete_tool_override`
- `get_effective_tools`
- `list_runtime_preset_decisions`

## 测试策略

### 新增拆分测试

新增 `tests/test_admin_tool_routes_split.py`：

- `test_admin_tool_routes_are_registered_from_split_module`
  - 递归展开 `server.app.routes`。
  - 断言 10 个 `/api/v1/admin/tools*` route 存在。
  - 断言 endpoint module 为 `api.admin.tool_routes`。
- `test_legacy_admin_routes_tool_imports_still_work`
  - 断言 `api.admin_routes` re-export 的对象与 `api.admin.tool_routes` 对象相同。
- `test_split_tool_routes_use_legacy_admin_token_monkeypatch`
  - monkeypatch `api.admin_routes.NANOBOT_ADMIN_TOKEN = "split-token"`。
  - `GET /api/v1/admin/tools` 使用 split token 返回 200。
  - 使用旧默认 token 返回 401。
- `test_admin_tool_routes_are_not_registered_twice`
  - 对所有迁移路由按 method + path 断言只注册一次。
- `test_admin_tool_static_routes_before_dynamic_tool_name_routes`
  - 断言 `/tools/targets`、`/tools/effective`、`/tools/decisions` 位于动态
    `/{tool_name}` 系列之前。
- `test_admin_tool_routes_do_not_import_admin_routes`
  - 静态扫描 `api/admin/tool_routes.py`，确认没有 `api.admin_routes` 反向导入，
    没有 `asyncio.run` 或 `run_awaitable_sync`。

### 现有行为回归

定向运行：

- `tests/test_admin_api.py::TestToolAdmin`
- `tests/test_tool_plan.py`
- `tests/test_tool_schema_config.py`
- `tests/test_final_tools.py`
- `tests/test_admin_api.py::TestAuth`
- `tests/test_asyncio_run_policy.py`

### 静态验证

```bash
python -m compileall api/admin_routes.py api/admin/tool_routes.py -q
git diff --check -- api/admin_routes.py api/admin/tool_routes.py tests/test_admin_tool_routes_split.py
rg -n "from api\.admin_routes|import api\.admin_routes|asyncio\.run|run_awaitable_sync" api/admin/tool_routes.py
wc -l api/admin_routes.py api/admin/tool_routes.py tests/test_admin_tool_routes_split.py
```

### 全量验证

提交实现前运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

## 非目标

- 不重构 `core.runtime_tool_service` 的工具生效算法。
- 不改变 tool registry、tool schema preview 或 ToolOverride DB schema。
- 不改 Prompt Runtime 模板、`enriched_query`、工具 usage 文档或 Prompt Runtime 输入。
- 不拆 Admin Models、reply/eval、settings 或普通 API。
- 不移动 `api/admin_routes.py` 中仍被 group / config / model / eval 使用的公共 helper。
- 不新增异步桥接层，不引入新的同步函数包装 awaitable。

## 验收标准

- `api/admin_routes.py` 行数继续下降，预计从 4303 行降到约 3760-3770 行。
- `api/admin/tool_routes.py` 不反向导入 `api.admin_routes`。
- `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对拆分后的工具路由仍生效。
- `/tools*` 路径、response shape、audit action、settings invalidate 和 runtime preset 语义保持不变。
- 新增 split 测试先红后绿。
- 定向回归、`tests/test_asyncio_run_policy.py` 和全量测试通过。
