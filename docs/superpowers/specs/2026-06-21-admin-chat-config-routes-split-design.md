# Admin Chat Config 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍未完成。当前硬项为
`api/admin_routes.py` 1009 行和 `api/routes.py` 2822 行。管理端已经完成 DB Browser、
Sticker / Generated Images、Group Memory、Observability、Tools、Models、Reply Eval、
Eval Workbench 和 Runtime / Overview 多刀拆分，并形成稳定模式：

- `api.admin_routes.router` 继续作为 `/api/v1/admin` 聚合 router。
- 子模块暴露自己的 `router`，由 `api.admin_routes` include。
- 子模块使用 `api.admin.common.verify_admin`，兼容
  `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch。
- `api.admin_routes` re-export 迁移后的 request model、helper 和 endpoint，保持旧导入路径。
- 拆分测试锁定 endpoint module、legacy import、token monkeypatch、路由重复注册、
  静态路径顺序和禁止反向导入 / 同步 awaitable 包装。

当前 `api/admin_routes.py` 剩余职责主要集中在：

- Block / ContentBlock / ChatStreamConfig：`/block-rules`、`/content-block-rules`、
  `/block-rules/test`、`/chat-streams`、`/configs`。
- Prompt effective preview 和 legacy prompt tombstone。
- `/model-replies`。
- DB backup / vacuum。
- Settings。

普通 `api/routes.py` 仍是更大的文件，但直接拆普通业务路由前需要先抽
`verify_token` common auth，并保持 `api.routes.verify_token` 与新 common auth 函数对象
一致，否则 `app.dependency_overrides[routes.verify_token]` 会对新子路由失效。该前置设施
适合单独设计和提交，本阶段继续沿管理端成熟拆分模式推进。

## 候选方案

### 方案 A：拆分 Chat Config 路由（推荐）

范围为 `api/admin_routes.py` 中的聊天策略和配置区块：

- User block rule CRUD。
- Content block rule CRUD、toggle 和命中测试。
- Chat stream 列表。
- ChatStreamConfig list / get / update / delete。

优点：

- 能从父模块迁出约 400 行以上，预计把 `api/admin_routes.py` 降到 800 行以下。
- 该区块围绕聊天流策略、内容拦截、群配置覆写和管理端审计，依赖重叠明显。
- 现有行为测试已经覆盖 user block CRUD、私聊 block 命中、effective config 默认值、
  覆写、搜索和分页。
- 路由边界集中，拆出后父模块剩余的 Prompt、Model Replies、DB 运维和 Settings 更清晰。

风险：

- `/configs/{chat_stream_id:path}` 是 path catch-all，必须继续排在 `GET /configs` 后面。
- `/block-rules/test` 与 `/block-rules/{rule_id}` 共享前缀，虽然当前方法不冲突，但拆分测试应锁定
  `/block-rules/test` 静态路径先于动态路径。
- `list_chat_streams()` 和 `list_configs(effective=1)` 依赖 `_runtime_snapshot()` 和群 ID
  归一化 helper，需要避免新模块反向导入父模块。
- ContentBlock 行为测试相对少，split 测试需要额外锁定路由归属和旧导入兼容。

结论：采用。

### 方案 B：拆分 Settings

范围为 `/settings*` endpoint。

优点：

- 边界清楚，只依赖 `SystemSetting`、`SETTING_DEFS`、`settings`、认证和审计。
- 风险低，没有明显跨运行态 helper 耦合。

风险：

- 只有约 96 行，拆完 `api/admin_routes.py` 仍大概率超过 800 行。
- 后端行为测试较少，主要需要新增 split smoke。

结论：保留为后续清理候选，不作为本轮 P3 收口刀。

### 方案 C：先抽普通 API common auth

范围为 `api/routes.py` 的 `verify_token()` 与 `NANOBOT_API_TOKEN` 兼容层。新建普通 API
共享鉴权模块，并保持旧 dependency override 和 monkeypatch 兼容。

优点：

- 为后续拆普通 API 最大文件解除 blocker。

风险：

- 本阶段行数收益小，仍不能立即减少 `api/routes.py` 的主要业务体量。
- 鉴权函数对象身份、dependency override、生产 token 读取和旧测试 monkeypatch 都是公共兼容面，
  应单独设计和验证。

结论：后续作为普通 API 拆分前置阶段。

## 目标

将 Admin Chat Config HTTP 层从 `api/admin_routes.py` 拆到
`api/admin/chat_config_routes.py`，保持：

- 所有 `/api/v1/admin/block-rules*`、`/api/v1/admin/content-block-rules*`、
  `/api/v1/admin/chat-streams` 和 `/api/v1/admin/configs*` HTTP path、method、status code
  和 response shape 不变。
- User block rule create / list / update / delete 行为不变，审计 action 和 target 不变。
- Content block rule create / list / update / delete / toggle 行为不变，校验错误文案不变。
- `/block-rules/test` 继续调用 `core.moderation.check_message_moderation_db()` 并返回
  `matched`、`rules`、`final_effects`。
- `/chat-streams` 继续合并 `ChatStreamConfig`、`ChatLog` 群 session 和 runtime snapshot。
- `/configs?effective=1` 继续合并 DB 覆写、`User` 群、`ChatLog` 群和 runtime snapshot，
  并保留 search、pagination、`has_override` 和 `source` 字段。
- `/configs/{chat_stream_id:path}` get / update / delete 行为不变，`group_profile_mode`
  校验和 `enable_group_profile` 兼容逻辑不变。
- `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 继续影响拆分后的 Chat Config 路由。
- `api.admin_routes` 继续 re-export 迁移后的 request model、helper 和 endpoint。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。

## 模块边界

### 新增 `api/admin/chat_config_routes.py`

职责：

- 管理 UserBlockRule。
- 管理 ContentBlockRule。
- 提供内容规则命中测试。
- 提供 chat stream ID 列表。
- 管理 ChatStreamConfig 覆写和 effective view。

推荐模块头：

```python
"""Admin Chat Config 路由。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.admin.common import audit, audit_request, client_ip, verify_admin
from api.admin.runtime_routes import _runtime_snapshot
from core.database import (
    ChatLog,
    ChatStreamConfig,
    ContentBlockRule,
    User,
    UserBlockRule,
    get_db,
)

router = APIRouter(tags=["admin-chat-config"])
```

新模块不设置 `/api/v1/admin` prefix，因为父模块已经提供该 prefix。新模块不得导入
`api.admin_routes`。

### 迁移 endpoint

纳入 `api/admin/chat_config_routes.py`：

- `GET /block-rules` -> `list_block_rules()`
- `POST /block-rules` -> `create_block_rule()`
- `PUT /block-rules/{rule_id}` -> `update_block_rule()`
- `DELETE /block-rules/{rule_id}` -> `delete_block_rule()`
- `GET /content-block-rules` -> `list_content_block_rules()`
- `POST /content-block-rules` -> `create_content_block_rule()`
- `PUT /content-block-rules/{rule_id}` -> `update_content_block_rule()`
- `DELETE /content-block-rules/{rule_id}` -> `delete_content_block_rule()`
- `POST /content-block-rules/{rule_id}/toggle` -> `toggle_content_block_rule()`
- `POST /block-rules/test` -> `test_block_rules()`
- `GET /chat-streams` -> `list_chat_streams()`
- `GET /configs` -> `list_configs()`
- `GET /configs/{chat_stream_id:path}` -> `get_config()`
- `PUT /configs/{chat_stream_id:path}` -> `update_config()`
- `DELETE /configs/{chat_stream_id:path}` -> `delete_config()`

不纳入：

- Prompt effective preview 与 legacy prompt tombstone。
- `/model-replies`。
- DB backup / vacuum。
- Settings。
- 普通 `api/routes.py` 的聊天、群聊、memory、tasks 或公开 media endpoint。

### 迁移 request model 和 helper

迁移到 `api/admin/chat_config_routes.py`：

- `BlockRuleCreate`
- `BlockRuleUpdate`
- `ContentBlockRuleCreate`
- `ContentBlockRuleUpdate`
- `ContentBlockRuleTestRequest`
- `ConfigUpdate`
- `_block_dict()`
- `_content_block_dict()`
- `_config_dict()`
- `_config_default()`
- `_raw_group_id()`
- `_group_stream_id()`

在新模块内保留私有实现，但不要求父模块 legacy identity：

- `_iso()`：用于 ContentBlock response；父模块仍为 `/model-replies` 保留自己的 `_iso()`。

父模块保留：

- `NANOBOT_ADMIN_TOKEN`
- `verify_admin()`
- `_audit()`
- `_client_ip()`
- `_audit_request()`
- `_safe_dict()`
- `_iso()`
- `EffectivePromptPreviewRequest`
- `_legacy_prompt_routes_removed()`

`_safe_json()`、`_age_seconds()` 和 `_group_session_id()` 当前在父模块无有效调用。本阶段不把它们迁入
Chat Config；如果引用扫描确认不再需要，可以从父模块删除。

### 修改 `api/admin_routes.py`

`api/admin_routes.py` 只做聚合和兼容：

- 从 `api.admin.chat_config_routes` 导入 `router as chat_config_router`。
- 在父 router 中 include `chat_config_router`。
- re-export 迁移后的 request model、helper 和 endpoint。
- 删除父模块中对应 request model、helper 和 endpoint 的本地实现。
- 保留父模块剩余区块仍使用的 helper。
- 删除迁移后不再需要的 `ContentBlockRule`、`UserBlockRule`、`ChatStreamConfig`、`User` 等 import，
  仅在引用扫描确认父模块其他区块不再使用时删除。

推荐 include 顺序：

```python
router.include_router(session_memory_router)
router.include_router(chat_config_router)
router.include_router(sticker_router)
router.include_router(group_memory_router)
router.include_router(runtime_router)
```

该顺序不改变已有 group memory 与 runtime 的相对顺序，也让 chat config 在管理端功能 router
中靠前注册。`chat_config_routes.py` 内部必须保持静态路径先于动态路径：

- `GET /configs` 先于 `GET /configs/{chat_stream_id:path}`。
- `POST /block-rules/test` 先于任何未来可能新增的 `POST /block-rules/{rule_id}`。

## 测试策略

新增 `tests/test_admin_chat_config_routes_split.py`：

- 锁定 15 个 route 的 endpoint module 为 `api.admin.chat_config_routes`。
- 锁定 `api.admin_routes` 对迁移 request model、helper 和 endpoint 的旧导入兼容。
- 锁定 `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对新模块路由仍生效。
- 锁定迁移路由未重复注册。
- 锁定 `/configs` 静态路由先于 `/configs/{chat_stream_id:path}`。
- 锁定 `/block-rules/test` 静态路由先于动态 `/block-rules/{rule_id}` 路由。
- 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。

复用现有行为回归：

- `tests/test_admin_api.py::TestBlockRule`
- `tests/test_admin_api.py::TestPrivateBlockFlow::test_blocked_user_chat_writes_log_with_files`
- `tests/test_api.py::test_effective_configs_returns_default_for_chatlog_groups`
- `tests/test_api.py::test_effective_configs_shows_override_when_config_exists`
- `tests/test_api.py::test_effective_configs_respects_search_filter`
- `tests/test_api.py::test_effective_configs_paginates`
- `tests/test_asyncio_run_policy.py`

迁移后还需要运行相邻 split 回归，确认 include 顺序未影响已拆模块：

- `tests/test_admin_runtime_routes_split.py`
- `tests/test_admin_group_memory_routes_split.py`
- `tests/test_admin_tool_routes_split.py`

## 非目标

- 不拆 Settings。
- 不拆 DB backup / vacuum。
- 不迁移 Prompt effective preview 或 legacy prompt tombstone。
- 不迁移 `/model-replies`。
- 不抽普通 API `verify_token` common auth。
- 不改变 WebUI。
- 不改变数据库 schema。
- 不改变 Prompt Runtime 模板、变量、工具 usage 文档或 runtime 输入。

## 验证命令

红灯阶段：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_admin_chat_config_routes_split.py
```

实现阶段：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_admin_chat_config_routes_split.py
python -B -m pytest -q -p no:cacheprovider \
  tests/test_admin_api.py::TestBlockRule \
  tests/test_admin_api.py::TestPrivateBlockFlow::test_blocked_user_chat_writes_log_with_files \
  tests/test_api.py::test_effective_configs_returns_default_for_chatlog_groups \
  tests/test_api.py::test_effective_configs_shows_override_when_config_exists \
  tests/test_api.py::test_effective_configs_respects_search_filter \
  tests/test_api.py::test_effective_configs_paginates \
  tests/test_admin_runtime_routes_split.py \
  tests/test_admin_group_memory_routes_split.py \
  tests/test_admin_tool_routes_split.py \
  tests/test_asyncio_run_policy.py
python -B -m compileall api/admin_routes.py api/admin/chat_config_routes.py
git diff --check
python -B -m pytest -p no:cacheprovider tests/ -v
```

## 预期结果

- `api/admin_routes.py` 从 1009 行降到 800 行以下。
- `api/admin/chat_config_routes.py` 承载聊天策略和配置管理 HTTP 层。
- 旧 HTTP 契约、旧导入路径、admin token monkeypatch 和审计语义保持兼容。
- P3 管理端超大文件子项完成；P3 队列仍剩普通 `api/routes.py`。
