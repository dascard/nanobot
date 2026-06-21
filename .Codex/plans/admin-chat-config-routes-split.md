# Admin Chat Config 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api/admin_routes.py` 中 Block / ContentBlock / ChatStreamConfig 管理端 HTTP 层拆到 `api/admin/chat_config_routes.py`，保持路径、响应结构、鉴权 monkeypatch、审计语义、配置 effective view 和旧导入路径不变，并把父模块降到 800 行以下。

**架构：** `api.admin_routes` 继续作为 `/api/v1/admin` 顶层聚合 router，新建 `api.admin.chat_config_routes.router` 并由父 router include。新模块承接聊天策略、内容拦截、chat stream 列表和群配置覆写管理；父模块保留 Prompt、Model Replies、DB 运维和 Settings。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 `api.admin.common` 鉴权与审计 helper。

---

## 当前状态（2026-06-21）

- [x] 已核对 `docs/todo.md` 剩余硬项：`api/admin_routes.py` 1009 行、`api/routes.py` 2822 行。
- [x] 已读取 Chat Config 源码区块：`api/admin_routes.py:329-758`。
- [x] 已核对现有行为测试：`tests/test_admin_api.py::TestBlockRule`、
  `tests/test_admin_api.py::TestPrivateBlockFlow::test_blocked_user_chat_writes_log_with_files`、
  `tests/test_api.py::test_effective_configs_returns_default_for_chatlog_groups`、
  `tests/test_api.py::test_effective_configs_shows_override_when_config_exists`、
  `tests/test_api.py::test_effective_configs_respects_search_filter`、
  `tests/test_api.py::test_effective_configs_paginates`。
- [x] 已核对现有 split 测试模式：`tests/test_admin_runtime_routes_split.py`、
  `tests/test_admin_tool_routes_split.py`、`tests/test_admin_group_memory_routes_split.py`。
- [x] 已写设计文档：
  `docs/superpowers/specs/2026-06-21-admin-chat-config-routes-split-design.md`。
- [x] 设计提交：`c3f2f7c docs(管理端): 设计聊天配置路由拆分`。
- [x] 计划提交：`94606ec docs(计划): 记录聊天配置路由拆分计划`。
- [x] 红灯测试提交：`ec9ef63 test(管理端): 锁定聊天配置路由拆分契约`。
- [x] 实现提交：`06d8aa6 refactor(管理端): 拆分聊天配置路由`。
- [x] 最终状态：`api/admin_routes.py` 已降至 632 行，低于 800 行；P3 超大文件队列
  当前只剩 `api/routes.py`。

## 执行记录（2026-06-21）

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_admin_chat_config_routes_split.py`
  -> `4 failed, 3 passed, 21 warnings in 6.32s`。失败点为 endpoint module 仍是
  `api.admin_routes`、新模块不存在、`/block-rules/test` 路由顺序仍错误，以及目标文件不存在。
- Split 绿灯：同一命令 -> `7 passed, 21 warnings in 1.19s`。
- 行为与相邻回归：计划中的 Block / Config / runtime / group-memory / tool / asyncio 组合
  -> `30 passed, 21 warnings in 7.53s`。
- 静态检查：`python -B -m compileall api/admin_routes.py api/admin/chat_config_routes.py`
  成功；`git diff --check -- api/admin_routes.py api/admin/chat_config_routes.py` 无输出；
  新模块反向导入 / `asyncio.run` / `run_awaitable_sync` 扫描无命中。
- 行数：`api/admin_routes.py` 632 行，`api/admin/chat_config_routes.py` 396 行，
  `tests/test_admin_chat_config_routes_split.py` 160 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1567 passed, 6 skipped, 139 warnings in 115.24s`。

## 子 agent 分工约定

主线程负责最终编辑、验证和提交。可用子 agent 任务必须互不覆盖写入范围：

- **Explorer A：迁移边界。** 只读检查 `api/admin_routes.py:329-758`、依赖 import、
  helper 共享和 route order 风险，输出文件名与行号。
- **Explorer B：测试契约。** 只读检查 split 测试模式、Block 行为测试、Configs 行为测试，
  输出新增测试建议和 pytest 命令。
- **Worker A：测试文件。** 只允许创建或修改 `tests/test_admin_chat_config_routes_split.py`。
- **Worker B：生产代码。** 只允许创建 `api/admin/chat_config_routes.py` 并修改
  `api/admin_routes.py`。必须在红灯测试验证后开始。
- **Reviewer：验证审查。** 只读检查 diff、route order、反向导入、asyncio 策略、行数和测试输出。

接口约定：

- `api/admin/chat_config_routes.py` 导出 `router`，不得带 `/api/v1/admin` 前缀。
- 新模块使用 `api.admin.common.verify_admin`、`audit()`、`audit_request()` 和 `client_ip()`。
- 新模块不得从 `api.admin_routes` 导入任何符号。
- `api.admin_routes` 必须 re-export 迁移后的 request model、helper 和 endpoint 函数。
- `GET /configs` 必须继续早于 `GET /configs/{chat_stream_id:path}`。
- `POST /block-rules/test` 必须在模块内早于动态 `/block-rules/{rule_id}` 路由声明。
- 生产代码不得新增 `asyncio.run()`，不得新增 `run_awaitable_sync`，不得新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、`enriched_query`、工具 usage 文档或 Prompt Runtime 输入。

## 文件职责

- 创建：`tests/test_admin_chat_config_routes_split.py`
  - 锁定 15 个 Chat Config route 的 endpoint module 为 `api.admin.chat_config_routes`。
  - 锁定 `api.admin_routes` 对迁移 request model、helper 和 endpoint 的旧导入兼容。
  - 锁定 `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对新模块路由仍生效。
  - 锁定迁移路由未重复注册。
  - 锁定 `/configs` 静态路由先于 `/configs/{chat_stream_id:path}`。
  - 锁定 `/block-rules/test` 静态路由先于动态 `/block-rules/{rule_id}`。
  - 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。
- 创建：`api/admin/chat_config_routes.py`
  - 定义 `router = APIRouter(tags=["admin-chat-config"])`。
  - 持有 `BlockRuleCreate`、`BlockRuleUpdate`、`ContentBlockRuleCreate`、
    `ContentBlockRuleUpdate`、`ContentBlockRuleTestRequest`、`ConfigUpdate`。
  - 持有 `_block_dict()`、`_content_block_dict()`、`_config_dict()`、`_config_default()`、
    `_raw_group_id()`、`_group_stream_id()` 和新模块私有 `_iso()`。
  - 持有 `list_block_rules()`、`create_block_rule()`、`update_block_rule()`、
    `delete_block_rule()`、`list_content_block_rules()`、`create_content_block_rule()`、
    `update_content_block_rule()`、`delete_content_block_rule()`、`toggle_content_block_rule()`、
    `test_block_rules()`、`list_chat_streams()`、`list_configs()`、`get_config()`、
    `update_config()`、`delete_config()`。
- 修改：`api/admin_routes.py`
  - 导入并 include `chat_config_router`。
  - re-export 迁移 endpoint、request model 和 helper。
  - 删除本地 Block / ContentBlock / ChatStreamConfig 区块。
  - 保留父模块仍使用的 `verify_admin()`、`_audit()`、`_client_ip()`、`_audit_request()`、
    `_safe_dict()`、`_iso()`、`EffectivePromptPreviewRequest` 和 `_legacy_prompt_routes_removed()`。
  - 删除确认不再使用的 import 和 helper。
- 收口阶段修改：`docs/todo.md`、`docs/plan_walkthrough.md`、
  `.Codex/plans/admin-chat-config-routes-split.md`。

## 任务 1：补 Chat Config 路由拆分红灯测试

**文件：**
- 创建：`tests/test_admin_chat_config_routes_split.py`

- [x] **步骤 1：创建 split 路由测试文件**

创建 `tests/test_admin_chat_config_routes_split.py`：

```python
from __future__ import annotations

from pathlib import Path


_ADMIN_CHAT_CONFIG_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/block-rules"),
    ("POST", "/api/v1/admin/block-rules"),
    ("PUT", "/api/v1/admin/block-rules/{rule_id}"),
    ("DELETE", "/api/v1/admin/block-rules/{rule_id}"),
    ("GET", "/api/v1/admin/content-block-rules"),
    ("POST", "/api/v1/admin/content-block-rules"),
    ("PUT", "/api/v1/admin/content-block-rules/{rule_id}"),
    ("DELETE", "/api/v1/admin/content-block-rules/{rule_id}"),
    ("POST", "/api/v1/admin/content-block-rules/{rule_id}/toggle"),
    ("POST", "/api/v1/admin/block-rules/test"),
    ("GET", "/api/v1/admin/chat-streams"),
    ("GET", "/api/v1/admin/configs"),
    ("GET", "/api/v1/admin/configs/{chat_stream_id:path}"),
    ("PUT", "/api/v1/admin/configs/{chat_stream_id:path}"),
    ("DELETE", "/api/v1/admin/configs/{chat_stream_id:path}"),
)


_CHAT_CONFIG_ROUTE_EXPORTS = (
    "BlockRuleCreate",
    "BlockRuleUpdate",
    "ContentBlockRuleCreate",
    "ContentBlockRuleUpdate",
    "ContentBlockRuleTestRequest",
    "ConfigUpdate",
    "_block_dict",
    "_content_block_dict",
    "_config_dict",
    "_config_default",
    "_raw_group_id",
    "_group_stream_id",
    "list_block_rules",
    "create_block_rule",
    "update_block_rule",
    "delete_block_rule",
    "list_content_block_rules",
    "create_content_block_rule",
    "update_content_block_rule",
    "delete_content_block_rule",
    "toggle_content_block_rule",
    "test_block_rules",
    "list_chat_streams",
    "list_configs",
    "get_config",
    "update_config",
    "delete_config",
)


def _admin_route_entries():
    from server import app

    def _iter_routes(routes, prefix: str = ""):
        for route in routes:
            endpoint = getattr(route, "endpoint", None)
            route_path = getattr(route, "path", None)
            if endpoint is not None and route_path is not None:
                yield prefix + route_path, route
                continue

            original_router = getattr(route, "original_router", None)
            if original_router is None:
                continue
            include_context = getattr(route, "include_context", None)
            include_prefix = getattr(include_context, "prefix", "")
            yield from _iter_routes(original_router.routes, prefix + include_prefix)

    return list(_iter_routes(app.routes))


def _admin_routes_for(path: str, method: str | None = None):
    return [
        route
        for route_path, route in _admin_route_entries()
        if route_path == path and (method is None or method in getattr(route, "methods", set()))
    ]


def test_admin_chat_config_routes_are_registered_from_split_module():
    for method, path in _ADMIN_CHAT_CONFIG_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.chat_config_routes"}


def test_legacy_admin_routes_chat_config_imports_still_work():
    from api import admin_routes
    from api.admin import chat_config_routes

    for name in _CHAT_CONFIG_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(chat_config_routes, name)

    assert admin_routes.BlockRuleCreate(user_id="u1").user_id == "u1"
    assert admin_routes.ContentBlockRuleTestRequest(message="测试").message == "测试"
    assert admin_routes.ConfigUpdate(talk_value=0.7).talk_value == 0.7
    assert admin_routes._raw_group_id("group_123") == "123"
    assert admin_routes._group_stream_id("123") == "qq:123:group"


def test_split_chat_config_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/block-rules",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/block-rules",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_chat_config_routes_are_not_registered_twice():
    for method, path in _ADMIN_CHAT_CONFIG_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_chat_config_static_configs_route_precedes_dynamic_path_route():
    route_paths = [path for path, _route in _admin_route_entries()]

    list_index = route_paths.index("/api/v1/admin/configs")
    detail_index = route_paths.index("/api/v1/admin/configs/{chat_stream_id:path}")

    assert list_index < detail_index


def test_admin_chat_config_block_rules_test_route_precedes_dynamic_rule_routes():
    route_paths = [path for path, _route in _admin_route_entries()]

    test_index = route_paths.index("/api/v1/admin/block-rules/test")
    dynamic_indices = [
        index
        for index, path in enumerate(route_paths)
        if path == "/api/v1/admin/block-rules/{rule_id}"
    ]

    assert dynamic_indices
    assert test_index < min(dynamic_indices)


def test_admin_chat_config_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    path = Path("api/admin/chat_config_routes.py")

    assert path.exists()
    source = path.read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
```

- [x] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_admin_chat_config_routes_split.py
```

预期：FAIL。失败点应来自 endpoint module 仍是 `api.admin_routes`、`api.admin.chat_config_routes`
尚不存在或 `api/admin/chat_config_routes.py` 文件尚不存在。

- [x] **步骤 3：提交红灯测试**

```bash
git add tests/test_admin_chat_config_routes_split.py
git commit -m "test(管理端): 锁定聊天配置路由拆分契约"
```

## 任务 2：迁移 Chat Config 路由到新模块

**文件：**
- 创建：`api/admin/chat_config_routes.py`
- 修改：`api/admin_routes.py`

- [x] **步骤 1：创建 `api/admin/chat_config_routes.py`**

从 `api/admin_routes.py` 迁移 `BlockRuleCreate` 至 `delete_config()` 的 Chat Config 区块。
模块骨架如下：

```python
"""Admin Chat Config 路由。"""

from __future__ import annotations

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

迁移时保留以下行为：

- `create_block_rule()`、`update_block_rule()`、`delete_block_rule()` 继续写入同样的 audit action。
- `create_content_block_rule()` 和 `update_content_block_rule()` 继续校验 `match_type` 与 `scope_type`。
- `test_block_rules()` 继续在函数内导入 `check_message_moderation_db`。
- `list_chat_streams()` 继续合并 DB 覆写、`ChatLog` 群 session 和 `_runtime_snapshot()`。
- `list_configs(effective=1)` 继续合并 DB 覆写、`User` 群、`ChatLog` 群和 `_runtime_snapshot()`。
- `update_config()` 继续保留 `enable_group_profile` 到 `group_profile_mode` 的兼容映射。

`/block-rules/test` 在新模块中放在动态 `/block-rules/{rule_id}` 路由声明之前。
`GET /configs` 在新模块中放在 `/configs/{chat_stream_id:path}` 路由声明之前。

- [x] **步骤 2：修改父模块聚合和 re-export**

在 `api/admin_routes.py` 中导入：

```python
from api.admin.chat_config_routes import (
    BlockRuleCreate,
    BlockRuleUpdate,
    ConfigUpdate,
    ContentBlockRuleCreate,
    ContentBlockRuleTestRequest,
    ContentBlockRuleUpdate,
    _block_dict,
    _config_default,
    _config_dict,
    _content_block_dict,
    _group_stream_id,
    _raw_group_id,
    create_block_rule,
    create_content_block_rule,
    delete_block_rule,
    delete_config,
    delete_content_block_rule,
    get_config,
    list_block_rules,
    list_chat_streams,
    list_configs,
    list_content_block_rules,
    router as chat_config_router,
    test_block_rules,
    toggle_content_block_rule,
    update_block_rule,
    update_config,
    update_content_block_rule,
)
```

include 顺序：

```python
router.include_router(session_memory_router)
router.include_router(chat_config_router)
router.include_router(sticker_router)
router.include_router(group_memory_router)
router.include_router(runtime_router)
```

删除父模块本地 Chat Config 区块。保留父模块仍使用的 `verify_admin()`、`_audit()`、
`_client_ip()`、`_audit_request()`、`_safe_dict()`、`_iso()`、`EffectivePromptPreviewRequest`
和 `_legacy_prompt_routes_removed()`。

确认父模块删除不再使用的 import：`ChatStreamConfig`、`UserBlockRule`、`ContentBlockRule`、
`User`、`Optional`、`Field`。若引用扫描显示仍有使用，按扫描结果保留。

- [x] **步骤 3：运行 split 绿灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_admin_chat_config_routes_split.py
```

预期：PASS，所有 Chat Config route endpoint module 均为 `api.admin.chat_config_routes`。

- [x] **步骤 4：运行行为回归**

运行：

```bash
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
```

预期：PASS。

- [x] **步骤 5：静态验证**

运行：

```bash
python -B -m compileall api/admin_routes.py api/admin/chat_config_routes.py
git diff --check
rg -n "from api\\.admin_routes|import api\\.admin_routes|asyncio\\.run|run_awaitable_sync" api/admin/chat_config_routes.py
wc -l api/admin_routes.py api/admin/chat_config_routes.py tests/test_admin_chat_config_routes_split.py
```

预期：`compileall` 和 `git diff --check` 退出码为 0；`rg` 无命中，退出码为 1；
`api/admin_routes.py` 少于 800 行。

- [x] **步骤 6：提交实现**

```bash
git add api/admin_routes.py api/admin/chat_config_routes.py
git commit -m "refactor(管理端): 拆分聊天配置路由"
```

## 任务 3：文档收口与最终验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/admin-chat-config-routes-split.md`

- [x] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」下追加本阶段进展：

```markdown
  - 进展：`api/admin_routes.py` 第十刀已拆出 Chat Config 管理端路由到
    `api/admin/chat_config_routes.py`；旧 `api.admin_routes` 继续 re-export
    迁移后的 request model、helper 和 15 个 endpoint，保留 HTTP 路径、
    admin token monkeypatch、Block / ContentBlock / Config response shape、
    audit action/detail 和 `/configs` 静态路由顺序。
```

- [x] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-21 Admin Chat Config 路由拆分` 小节，记录设计提交、计划提交、红灯测试提交、
实现提交、验证结果、行数变化和执行边界。

- [x] **步骤 3：勾选本计划当前任务状态**

将已经完成的步骤由 `- [ ]` 改为 `- [x]`，并补充实际验证输出和提交 SHA。

- [x] **步骤 4：运行最终验证**

运行：

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

预期：全部通过。若全量测试失败，先按失败信息修复并重新运行相关测试，不能只更新文档。

- [x] **步骤 5：提交文档收口**

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-chat-config-routes-split.md
git commit -m "docs(计划): 收口聊天配置路由拆分"
```
