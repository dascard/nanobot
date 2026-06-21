# Admin Models 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍未完成。当前剩余硬项为
`api/admin_routes.py` 3761 行和 `api/routes.py` 2822 行。管理端已经完成 DB Browser、
Sticker / Generated Images、Group Memory、Observability 和 Tools 五刀拆分，并形成稳定模式：

- `api.admin_routes.router` 继续作为 `/api/v1/admin` 聚合 router。
- 子模块暴露自己的 `router`，由 `api.admin_routes` include。
- 子模块使用 `api.admin.common.verify_admin`，兼容
  `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch。
- `api.admin_routes` re-export 迁移后的 request model、常量、helper 和 endpoint，
  保持旧导入路径。

本阶段继续沿管理端拆分。普通 `api/routes.py` 仍需先设计 `verify_token` 共享兼容层，
不在本阶段触碰。

## 候选方案

### 方案 A：拆分 Admin Models 路由（推荐）

新建 `api/admin/model_routes.py`，承载模型状态、模型目录、provider 管理、路由编辑、
路由诊断、路由连通性测试、本地组件测试、TimingGate 稳定性测试和模型健康检查。

优点：

- 行数收益最高，预计从 `api/admin_routes.py` 迁出约 1150 行，净减少约 1100 行。
- 前端已有独立 `webui/src/features/models/ModelsPage.jsx`，Dashboard 也只读取
  `/models/status`。
- 现有 `tests/test_admin_api.py::TestModelCatalog`、`TestModelRoutes`、
  `TestModelHealthCheck` 和 `TestModelRouteV2` 覆盖主要行为。
- HTTP 域集中在 `/models/*`，并包含历史兼容路径 `/model-catalog` 和 `/model-routes`。
- 拆分只移动 HTTP 层，不改变模型路由服务、Prompt Runtime 模板或请求构造契约。

风险：

- `POST /models/chat-test`、`POST /models/routes/{route_key}/test`、`GET /models/available`
  和 `POST /models/catalog/refresh` 可能触发真实网络；测试必须继续 monkeypatch。
- `POST /models/local/{component}/test` 和 `/warmup` 可能触发本地模型懒加载；测试必须
  隔离 HuggingFace / reranker / NLI 真实加载。
- `PUT /models/routes/{route_key}` 会写 `SystemSetting`、调用 `settings.invalidate()`，
  并清理 image summary route cache；迁移时不能改变副作用顺序。
- `POST /models/timing-gate-stability-test` 是模型诊断路径，但会调用 TimingGate；
  本阶段只迁移位置，不改变输出结构。
- 新模块不能反向导入 `api.admin_routes`。

### 方案 B：只拆核心 Models，留下 TimingGate 稳定性测试

迁移模型状态、provider、catalog、route 和 local component 路由，但保留
`/models/timing-gate-stability-test`。

优点：

- 风险更低，避免触碰 TimingGate 诊断路径。

风险：

- 拆完后 `api/admin_routes.py` 仍残留 `/models/*` 路由，命名空间边界不干净。
- 后续还要为单个诊断端点再处理一次兼容导出和拆分测试。

结论：不采用。`/models/timing-gate-stability-test` 纳入本阶段，但保持行为不变。

### 方案 C：拆分 Reply / Eval 路由

范围为 `POST /reply-test/run`、`/reply-eval/*` 和 `/evals/*`。

优点：

- 行数收益也较大。

风险：

- Reply test / eval 会进入 KT Bridge 和 Prompt Runtime，运行时编排比模型管理更深。
- `tests/test_reply_admin.py` 已直接从 `api.admin_routes` 导入
  `ReplyTestRunRequest` 和 `_resolve_reply_test_prompt_settings`，兼容面更敏感。
- Eval timing proposal 还有旧模块常量 monkeypatch 风险。

结论：保留为后续阶段。

## 目标

将 Admin Models HTTP 层从 `api/admin_routes.py` 拆到 `api/admin/model_routes.py`，
保持：

- 所有迁移端点的 HTTP path、method、status code 和 response shape 不变。
- 前端 `ModelsPage.jsx` 与 Dashboard 依赖的字段不变。
- legacy `/model-catalog` 与 `/model-routes` 行为不变。
- provider 写入、route 写入、audit action/detail、`settings.invalidate()` 和
  image summary route cache 清理语义不变。
- `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 继续影响拆分后的模型路由。
- `api.admin_routes` 继续 re-export 迁移后的 request model、常量、helper 和 endpoint。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、`enriched_query`、task template 变量、工具 usage 文档或
  prompt compile 输入。

## 模块边界

### 新增 `api/admin/model_routes.py`

职责：

- 查询模型状态、provider、route 和本地组件状态。
- 执行模型连通性测试和 route 诊断。
- 管理 provider 配置。
- 读取和刷新 provider catalog。
- 读取和编辑 legacy model catalog。
- 读取和编辑 legacy stage route。
- 读取和编辑 canonical model route。
- 查询可选模型列表。
- 测试和预热本地语义组件。
- 执行 TimingGate 稳定性诊断。
- 执行模型端点健康检查。

推荐模块头：

```python
"""Admin Models 路由。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import audit, audit_request, client_ip, verify_admin
from core.database import SystemSetting, get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(tags=["admin-models"])
```

不使用 `prefix="/models"`，因为本模块同时承载 legacy `/model-catalog` 和
`/model-routes`。

### 迁移端点

纳入 `api/admin/model_routes.py`：

- `GET /models/status` -> `models_status()`
- `POST /models/chat-test` -> `chat_model_test()`
- `GET /model-catalog` -> `get_model_catalog()`
- `PATCH /model-catalog/{model_id}` -> `patch_model_catalog()`
- `GET /models/providers` -> `list_model_providers()`
- `PUT /models/providers/{provider_id}` -> `update_model_provider()`
- `GET /models/catalog` -> `get_model_catalog_v2()`
- `GET /models/route-references` -> `get_route_references()`
- `POST /models/catalog/refresh` -> `refresh_model_catalog()`
- `GET /model-routes` -> `get_model_routes()`
- `PATCH /model-routes/{stage}` -> `patch_model_route()`
- `PUT /models/routes/{route_key}` -> `edit_model_route()`
- `POST /models/routes/{route_key}/test` -> `test_model_route()`
- `GET /models/routes/{route_key}/resolved` -> `get_resolved_route()`
- `GET /models/available` -> `list_available_models()`
- `POST /models/local/{component}/test` -> `test_local_component()`
- `POST /models/local/{component}/warmup` -> `warmup_local_component()`
- `POST /models/timing-gate-stability-test` -> `timing_gate_stability_test()`
- `POST /models/health-check` -> `model_health_check()`

不纳入：

- `GET /model-replies`：语义是回复日志观测，依赖 `ChatLog`、`_safe_dict()` 和 `_iso()`，
  后续应跟 Reply / Observability 边界处理。
- `POST /prompt/effective-preview` 和 legacy `/prompt(s)`：属于 Prompt Runtime 管理。
- `POST /reply-test/run`、`/reply-eval/*`、`/evals/*`：属于 Reply / Eval 工作台。
- `/settings/*`、`/db/backup`、`/db/vacuum`。

### 迁移 request model

- `ChatModelTestRequest`
- `ProviderUpdateBody`
- `ModelCatalogPatch`
- `ModelRoutePatch`
- `ModelRouteEditBody`
- `TimingGateStabilityRequest`

### 迁移常量和 helper

- `_ALLOWED_TIERS`
- `_STAGE_META`
- `_ROUTE_SETTING_MAP`
- `_CLASSIFIER_ROUTE_KEYS`
- `_ROUTE_ALIAS`
- `_CHAT_ROUTES`
- `_TINY_TEST_PNG`
- `_resolve_route_value()`
- `_resolve_route_key()`
- `_redact()`
- `_test_nli_contradiction()`

新模块使用 `api.admin.common.audit()`、`audit_request()` 和 `client_ip()`，不从父模块导入
`_audit()`、`_audit_request()` 或 `_client_ip()`。

### 修改 `api/admin_routes.py`

`api/admin_routes.py` 只做聚合和兼容：

- 导入 `router as model_router`。
- 在 include 区新增 `router.include_router(model_router)`。
- re-export 上述 request model、常量、helper 和 endpoint。
- 删除本地模型管理区块。
- 保留仍被其他子域使用的 `_safe_dict()`、`_iso()`、`_audit()`、`_audit_request()`、
  `_client_ip()`、`ChatLog`、`ConversationTurn`、`User`、`SystemSetting`、`AdminAuditLog`
  和 `row_to_dict`。

## 路由顺序

新模块内保持静态路径先于动态路径：

1. `GET /models/status`
2. `POST /models/chat-test`
3. `GET /model-catalog`
4. `GET /models/providers`
5. `PUT /models/providers/{provider_id}`
6. `GET /models/catalog`
7. `GET /models/route-references`
8. `POST /models/catalog/refresh`
9. `PATCH /model-catalog/{model_id}`
10. `GET /model-routes`
11. `PATCH /model-routes/{stage}`
12. `PUT /models/routes/{route_key}`
13. `POST /models/routes/{route_key}/test`
14. `GET /models/routes/{route_key}/resolved`
15. `GET /models/available`
16. `POST /models/local/{component}/test`
17. `POST /models/local/{component}/warmup`
18. `POST /models/timing-gate-stability-test`
19. `POST /models/health-check`

当前没有单段 `GET /models/{name}` catch-all，冲突风险低。仍用拆分测试固定每个签名只注册一次。

## 兼容策略

### 认证

新模块必须使用 `api.admin.common.verify_admin`。该函数通过
`sys.modules["api.admin_routes"].NANOBOT_ADMIN_TOKEN` 兼容旧测试 monkeypatch。

### 审计

保持原 action：

- `update_provider`
- `update_model_catalog`
- `update_model_route`
- `edit_model_route`

保持原 detail 结构：

- provider 写入使用 `_redact(written)`。
- catalog patch 写 `{"before": before, "after": updates}`。
- legacy model route 写 `{"value": body.value}`。
- canonical model route 写 `_redact(written)`，不在响应中暴露 `api_key`。

### Lazy import

以下 import 保持在函数体内，避免模块 import 阶段触发网络、模型加载、server 初始化或设置缓存副作用：

- `clients.classifier_client`
- `clients.model_registry.registry`
- `clients.new_api_client.NewAPIClient`
- `core.persona_preprocess`
- `core.semantic.provider_factory`
- `core.semantic.reranker`
- `core.settings_service.settings`
- `nanobot_kt.bridge._registry_provider_for_route`
- `creatures.nanobot.prompts.skills.image_summary.tool._get_image_summary_route`
- `aiohttp`
- `urllib.request`

### 旧导入路径

`api.admin_routes` re-export：

- `ChatModelTestRequest`
- `ProviderUpdateBody`
- `ModelCatalogPatch`
- `ModelRoutePatch`
- `ModelRouteEditBody`
- `TimingGateStabilityRequest`
- `_ALLOWED_TIERS`
- `_STAGE_META`
- `_ROUTE_SETTING_MAP`
- `_CLASSIFIER_ROUTE_KEYS`
- `_ROUTE_ALIAS`
- `_CHAT_ROUTES`
- `_TINY_TEST_PNG`
- `_resolve_route_value`
- `_resolve_route_key`
- `_redact`
- `_test_nli_contradiction`
- `models_status`
- `chat_model_test`
- `get_model_catalog`
- `patch_model_catalog`
- `list_model_providers`
- `update_model_provider`
- `get_model_catalog_v2`
- `get_route_references`
- `refresh_model_catalog`
- `get_model_routes`
- `patch_model_route`
- `edit_model_route`
- `test_model_route`
- `get_resolved_route`
- `list_available_models`
- `test_local_component`
- `warmup_local_component`
- `timing_gate_stability_test`
- `model_health_check`

## 前端契约

迁移不得改变以下字段：

- `/models/status` 返回 `providers`、`routes`、`local_components` 和 `unsupported`。
- `routes[*]` 保留 `route_key`、`label`、`route_type`、`provider_id`、`model`、
  `api_key_configured`、`route_api_key_configured`、`provider_enabled`、`timeout`、
  `temperature`、`max_tokens`、`enable_thinking`、`source`、`editable`、
  `inherited_from` 和 `overridden_fields`。
- `/models/catalog` 返回 `{"catalog": items}`。
- `/models/route-references` 返回 `{"route_references": items}`。
- `/models/catalog/refresh` 返回 `results` 和 `catalog`。
- `/models/routes/{route_key}/test` 返回 `ok`、`route_key`、`latency_ms`、`raw_output`
  和分支字段 `vision_payload_ok`、`note`、`error`。
- `/models/routes/{route_key}/resolved` 返回脱敏 route 诊断字段。
- `/models/local/{component}/{action}` 返回当前本地组件测试字段。

## 测试策略

### 新增拆分测试

新增 `tests/test_admin_model_routes_split.py`，覆盖：

- 所有迁移端点的 `route.endpoint.__module__ == "api.admin.model_routes"`。
- `api.admin_routes` legacy import 与 `api.admin.model_routes` 对象 identity 一致。
- `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对拆分路由仍生效。
- 所有迁移端点只注册一次。
- 新模块源码不包含 `from api.admin_routes`、`import api.admin_routes`、`asyncio.run` 或
  `run_awaitable_sync`。

拆分测试的认证 smoke 使用 `GET /api/v1/admin/model-catalog`，避免触发
`/models/status` 的本地组件探测。

### 行为回归

继续运行现有模型行为测试：

- `tests/test_admin_api.py::TestModelCatalog`
- `tests/test_admin_api.py::TestModelRoutes`
- `tests/test_admin_api.py::TestModelHealthCheck`
- `tests/test_admin_api.py::TestModelRouteV2`

这些测试已经隔离了：

- `aiohttp.ClientSession` 健康检查。
- `urllib.request.build_opener` 可选模型列表。
- `clients.classifier_client.call_model_route` 视觉 route 测试。
- `core.semantic.provider_factory.get_reranker_provider` 本地 reranker 测试。
- `clients.model_registry.registry.save_registry` 持久化写入。

### 分层验证命令

红灯：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  python -m pytest tests/test_admin_model_routes_split.py -v
```

绿灯：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  python -m pytest tests/test_admin_model_routes_split.py -v
```

模型行为回归：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  python -m pytest \
    tests/test_admin_api.py::TestModelCatalog \
    tests/test_admin_api.py::TestModelRoutes \
    tests/test_admin_api.py::TestModelHealthCheck \
    tests/test_admin_api.py::TestModelRouteV2 \
    -v
```

拆分兼容回归：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  python -m pytest \
    tests/test_admin_model_routes_split.py \
    tests/test_admin_tool_routes_split.py \
    tests/test_admin_sticker_routes_split.py \
    tests/test_admin_group_memory_routes_split.py \
    tests/test_admin_observability_routes_split.py \
    tests/test_admin_db_browser.py \
    -v
```

asyncio 策略回归：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  python -m pytest \
    tests/test_asyncio_run_policy.py \
    tests/test_admin_model_routes_split.py::test_admin_model_routes_do_not_import_parent_admin_routes_or_sync_awaitable \
    -v
```

静态检查：

```bash
python -m compileall api/admin_routes.py api/admin/model_routes.py -q
git diff --check -- api/admin_routes.py api/admin/model_routes.py tests/test_admin_model_routes_split.py
rg -n "from api\.admin_routes|import api\.admin_routes|asyncio\.run|run_awaitable_sync" api/admin/model_routes.py
```

最终全量：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  python -m pytest tests/ -v
```

## 非目标

- 不拆普通 `api/routes.py`。
- 不拆 Reply / Eval / Prompt Runtime 管理路由。
- 不迁移 `/model-replies`。
- 不修改 DB schema。
- 不重构 `clients.classifier_client`、`clients.model_registry`、`NewAPIClient`、
  `core.settings_service` 或本地语义组件加载逻辑。
- 不修复或改变当前同步 `urllib` 探测方式；如需异步化，应另起性能修复任务并走 TDD。
- 不修改 Prompt Runtime 模板或默认 runtime 模板目录。

## 预期结果

- 新增 `api/admin/model_routes.py`，约 1150 行。
- `api/admin_routes.py` 从 3761 行降至约 2650 行。
- 新增 `tests/test_admin_model_routes_split.py`。
- 所有模型管理端点仍按原路径工作。
- 旧 `api.admin_routes` 导入路径继续兼容。
- 全量测试保持 0 failures。
