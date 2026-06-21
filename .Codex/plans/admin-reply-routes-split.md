# Admin Reply Eval 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api/admin_routes.py` 中 Reply 手动测试与 Reply Eval 管理端 HTTP 层拆到 `api/admin/reply_routes.py`，保持路径、响应结构、Prompt Runtime metadata、鉴权 monkeypatch、评测 metrics、traffic 聚合和旧导入路径不变。

**架构：** `api.admin_routes` 继续作为 `/api/v1/admin` 顶层聚合 router，新建 `api.admin.reply_routes.router` 并由父 router include。新模块只承接 `/reply-test/*` 与 `/reply-eval/*` HTTP 编排、request model 和域内 helper；KT Bridge、Prompt Runtime、tracing、Reply Eval 数据表和现有评测逻辑保持原实现语义。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 `api.admin.common.verify_admin` 鉴权 helper。

---

## 当前状态（2026-06-21）

- [x] 已核对 `docs/todo.md` 剩余硬项：`api/admin_routes.py` 2647 行、`api/routes.py` 2822 行。
- [x] 已完成只读子 agent 分析：普通 API 拆分前需要 `verify_token` common auth，管理端下一刀优先 Reply Eval。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-admin-reply-routes-split-design.md`。
- [x] 设计提交：`73f6f81 docs(管理端): 设计回复评测路由拆分`。
- [x] 设计阶段全量验证：`python -m pytest tests/ -v` ->
  `1535 passed, 6 skipped, 139 warnings in 109.99s`。
- [ ] 计划提交。
- [ ] 红灯测试。
- [ ] 实现提交。
- [ ] 文档收口提交。

## 子 agent 分工约定

- **Agent A：测试契约。** 只修改 `tests/test_admin_reply_routes_split.py`，不改生产代码。输出红灯测试结果、失败数量和主要失败原因。
- **Agent B：Reply 路由模块草稿。** 只创建 `api/admin/reply_routes.py`，不改 `api/admin_routes.py`。按本计划迁移 request model、helper 和 `/reply-test/*`、`/reply-eval/*` endpoint。
- **Agent C：父模块集成。** 只修改 `api/admin_routes.py`。负责 include `reply_router`、re-export 旧符号、删除旧 Reply Eval 区块，并保留其他管理端子域仍使用的 helper。
- **Agent D：验证审查。** 只读检查 `git diff`、路由注册、反向导入、`asyncio.run` 策略、Prompt Runtime metadata、行数和测试输出。不得修改代码。

接口约定：

- `api/admin/reply_routes.py` 导出 `router`，不得带 `/api/v1/admin` 前缀。
- `router` 不使用 prefix，因为模块同时承载 `/reply-test` 和 `/reply-eval`。
- 新模块使用 `api.admin.common.verify_admin`，不得从 `api.admin_routes` 导入任何符号。
- `api.admin_routes` 必须 re-export 迁移后的 request model、helper 和 endpoint 函数。
- `/model-replies`、`/evals/*`、`/settings/*`、`/db/*` 保留在 `api.admin_routes`，不进入本阶段。
- 生产代码不得新增 `asyncio.run()`，不得新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、`enriched_query`、工具 usage 文档或 Prompt Runtime 输入。

## 文件职责

- 创建：`tests/test_admin_reply_routes_split.py`
  - 锁定 11 个 Reply route 的 endpoint module 为 `api.admin.reply_routes`。
  - 锁定 `api.admin_routes` 对迁移符号的旧导入兼容。
  - 锁定 `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对新模块路由仍生效。
  - 锁定迁移路由未重复注册。
  - 锁定 `/reply-eval/runs` 先于 `/reply-eval/runs/{run_id}` 注册。
  - 锁定 `reply_test_run()`、`reply_eval_run()` 和 `_run_reply_test_once()` 仍是 coroutine function。
  - 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。
- 创建：`api/admin/reply_routes.py`
  - 定义 `router = APIRouter(tags=["admin-reply"])`。
  - 持有 `ReplyTestRunRequest`、`ReplyEvalCaseIn`、`ReplyEvalCasePatch`、`ReplyEvalSaveGeneratedIn`、`ReplyEvalRunIn`。
  - 持有 `_loads_json_list()`、`_reply_case_to_dict()`、`_reply_eval_run_to_dict()`、`_reply_log_attempt()`、`_reply_contract_has_final_action()`、`_reply_contract_run_key()`、`_is_reply_eval_test_session()`、`_safe_rate()`、`_resolve_reply_test_prompt_settings()`、`_run_reply_test_once()`、`_upsert_reply_eval_case()`。
  - 持有 `reply_test_run()`、`reply_eval_list_cases()`、`reply_eval_create_case()`、`reply_eval_update_case()`、`reply_eval_delete_case()`、`reply_eval_generate_preview()`、`reply_eval_save_generated()`、`reply_eval_run()`、`reply_eval_real_traffic()`、`reply_eval_list_runs()`、`reply_eval_get_run()`。
- 修改：`api/admin_routes.py`
  - 导入并 include `reply_router`。
  - re-export 迁移符号。
  - 删除本地 `# Reply 手动测试 / A-B 评估` 到 `# Eval 系统 API` 前的 Reply Eval 实现。
  - 保留仍被其他子域使用的 `json`、`uuid`、`datetime`、`timedelta`、`Any`、`Literal`、`Optional`、`row_to_dict` 和数据库模型 import。
- 后续文档收口阶段修改：`docs/todo.md`、`docs/plan_walkthrough.md`、`.Codex/plans/admin-reply-routes-split.md`。

## 任务 1：补 Admin Reply Eval 路由拆分红灯测试

**文件：**
- 创建：`tests/test_admin_reply_routes_split.py`

- [ ] **步骤 1：创建 split 路由测试文件**

创建 `tests/test_admin_reply_routes_split.py`：

```python
from __future__ import annotations

import inspect
from pathlib import Path


_ADMIN_REPLY_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/admin/reply-test/run"),
    ("GET", "/api/v1/admin/reply-eval/cases"),
    ("POST", "/api/v1/admin/reply-eval/cases"),
    ("PUT", "/api/v1/admin/reply-eval/cases/{case_id}"),
    ("DELETE", "/api/v1/admin/reply-eval/cases/{case_id}"),
    ("POST", "/api/v1/admin/reply-eval/generate-preview"),
    ("POST", "/api/v1/admin/reply-eval/save-generated"),
    ("POST", "/api/v1/admin/reply-eval/run"),
    ("GET", "/api/v1/admin/reply-eval/traffic"),
    ("GET", "/api/v1/admin/reply-eval/runs"),
    ("GET", "/api/v1/admin/reply-eval/runs/{run_id}"),
)


_REPLY_ROUTE_EXPORTS = (
    "ReplyTestRunRequest",
    "ReplyEvalCaseIn",
    "ReplyEvalCasePatch",
    "ReplyEvalSaveGeneratedIn",
    "ReplyEvalRunIn",
    "_loads_json_list",
    "_reply_case_to_dict",
    "_reply_eval_run_to_dict",
    "_reply_log_attempt",
    "_reply_contract_has_final_action",
    "_reply_contract_run_key",
    "_is_reply_eval_test_session",
    "_safe_rate",
    "_resolve_reply_test_prompt_settings",
    "_run_reply_test_once",
    "_upsert_reply_eval_case",
    "reply_test_run",
    "reply_eval_list_cases",
    "reply_eval_create_case",
    "reply_eval_update_case",
    "reply_eval_delete_case",
    "reply_eval_generate_preview",
    "reply_eval_save_generated",
    "reply_eval_run",
    "reply_eval_real_traffic",
    "reply_eval_list_runs",
    "reply_eval_get_run",
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


def test_admin_reply_routes_are_registered_from_split_module():
    for method, path in _ADMIN_REPLY_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.reply_routes"}


def test_legacy_admin_routes_reply_imports_still_work():
    from api import admin_routes
    from api.admin import reply_routes

    for name in _REPLY_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(reply_routes, name)

    body = admin_routes.ReplyTestRunRequest(message="你在吗")
    assert body.prompt_engine == "prompt"
    assert body.variant == "v2_code_retry"
    assert admin_routes._resolve_reply_test_prompt_settings(body) == ("prompt", "prompt", True)
    assert admin_routes._safe_rate(1, 4) == 0.25


def test_split_reply_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/reply-eval/cases",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/reply-eval/cases",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_reply_routes_are_not_registered_twice():
    for method, path in _ADMIN_REPLY_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_reply_static_routes_before_dynamic_run_id_route():
    route_paths = [path for path, _route in _admin_route_entries()]

    runs_index = route_paths.index("/api/v1/admin/reply-eval/runs")
    run_id_index = route_paths.index("/api/v1/admin/reply-eval/runs/{run_id}")

    assert runs_index < run_id_index


def test_admin_reply_async_boundaries_remain_coroutines():
    from api.admin import reply_routes

    assert inspect.iscoroutinefunction(reply_routes._run_reply_test_once)
    assert inspect.iscoroutinefunction(reply_routes.reply_test_run)
    assert inspect.iscoroutinefunction(reply_routes.reply_eval_run)


def test_admin_reply_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    path = Path("api/admin/reply_routes.py")

    assert path.exists()
    source = path.read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
```

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_reply_routes_split.py -q
```

预期：FAIL。主要失败点应为 endpoint module 仍是 `api.admin_routes`、`api.admin.reply_routes`
尚不存在或 `api/admin/reply_routes.py` 文件不存在。

- [ ] **步骤 3：记录红灯结果**

在本计划「当前状态」中记录失败数量、失败用例和主要失败原因。红灯测试不单独提交；按项目门禁，
失败状态只作为 TDD 证据，绿灯后与实现一起提交。

## 任务 2：创建 `api/admin/reply_routes.py`

**文件：**
- 创建：`api/admin/reply_routes.py`

- [ ] **步骤 1：创建新模块骨架**

创建 `api/admin/reply_routes.py`，模块头使用：

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

- [ ] **步骤 2：迁移 request model**

从 `api/admin_routes.py` 的 Reply Eval 区块移动以下类到新模块，类定义内容保持逐字一致：

```python
class ReplyTestRunRequest(BaseModel):
    chat_type: Literal["group", "private"] = "group"
    session_id: str = "reply-test"
    sender_id: str = "admin"
    sender_name: str = "admin"
    character_name: str = ""
    message: str
    recent_context: str = ""
    persona_text: str = ""
    prompt_engine: Literal["v1", "v2", "prompt"] = "prompt"
    variant: Literal[
        "baseline",
        "prompt_only",
        "code_retry",
        "v1_baseline",
        "v2_prompt_only",
        "v2_code_retry",
    ] = "v2_code_retry"
    enable_reply_contract_retry: bool = True
    dry_run: bool = True
```

同时迁移 `ReplyEvalCaseIn`、`ReplyEvalCasePatch`、`ReplyEvalSaveGeneratedIn` 和
`ReplyEvalRunIn`，字段、默认值、`Literal` 选项和 `Field(default_factory=...)` 保持一致。

- [ ] **步骤 3：迁移 helper**

移动以下 helper，函数体保持行为一致：

```python
def _loads_json_list(raw: str) -> list: ...
def _reply_case_to_dict(row) -> dict: ...
def _reply_eval_run_to_dict(row) -> dict: ...
def _reply_log_attempt(log) -> dict: ...
def _reply_contract_has_final_action(log) -> bool: ...
def _reply_contract_run_key(log) -> str: ...
def _is_reply_eval_test_session(session_id: str) -> bool: ...
def _safe_rate(numerator: int, denominator: int) -> float: ...
def _resolve_reply_test_prompt_settings(body: ReplyTestRunRequest) -> tuple[str, str, bool]: ...
async def _run_reply_test_once(body: ReplyTestRunRequest, db: Session) -> dict: ...
def _upsert_reply_eval_case(db: Session, item: ReplyEvalCaseIn): ...
```

迁移注意事项：

- `_run_reply_test_once()` 内部继续 lazy import `AgentRun`、`LLMApiRequestLog`、
  `ReplyContractCheckLog`、`new_trace_id` 和 `get_bridge`。
- `_upsert_reply_eval_case()` 内部继续 lazy import `ReplyEvalCase`。
- 不从 `api.admin_routes` 导入 `_loads_json_list`、`row_to_dict` 或任何 helper。
- 保持 `metadata` 字段、`reply_logs` 排序、`llm_logs` 排序、metrics key 和 response key 不变。

- [ ] **步骤 4：迁移 endpoint**

移动 11 个 endpoint，装饰器和函数体保持行为一致：

```python
@router.post("/reply-test/run")
async def reply_test_run(...): ...

@router.get("/reply-eval/cases")
def reply_eval_list_cases(...): ...

@router.post("/reply-eval/cases")
def reply_eval_create_case(...): ...

@router.put("/reply-eval/cases/{case_id}")
def reply_eval_update_case(...): ...

@router.delete("/reply-eval/cases/{case_id}")
def reply_eval_delete_case(...): ...

@router.post("/reply-eval/generate-preview")
def reply_eval_generate_preview(...): ...

@router.post("/reply-eval/save-generated")
def reply_eval_save_generated(...): ...

@router.post("/reply-eval/run")
async def reply_eval_run(...): ...

@router.get("/reply-eval/traffic")
def reply_eval_real_traffic(...): ...

@router.get("/reply-eval/runs")
def reply_eval_list_runs(...): ...

@router.get("/reply-eval/runs/{run_id}")
def reply_eval_get_run(...): ...
```

迁移后运行：

```bash
python -m compileall api/admin/reply_routes.py -q
```

预期：无输出，退出码为 0。

## 任务 3：接入父模块并删除本地实现

**文件：**
- 修改：`api/admin_routes.py`

- [ ] **步骤 1：导入迁移符号**

在既有 admin 子路由 import 区加入：

```python
from api.admin.reply_routes import (
    ReplyEvalCaseIn,
    ReplyEvalCasePatch,
    ReplyEvalRunIn,
    ReplyEvalSaveGeneratedIn,
    ReplyTestRunRequest,
    _is_reply_eval_test_session,
    _loads_json_list,
    _reply_case_to_dict,
    _reply_contract_has_final_action,
    _reply_contract_run_key,
    _reply_eval_run_to_dict,
    _reply_log_attempt,
    _resolve_reply_test_prompt_settings,
    _run_reply_test_once,
    _safe_rate,
    _upsert_reply_eval_case,
    reply_eval_create_case,
    reply_eval_delete_case,
    reply_eval_generate_preview,
    reply_eval_get_run,
    reply_eval_list_cases,
    reply_eval_list_runs,
    reply_eval_real_traffic,
    reply_eval_run,
    reply_eval_save_generated,
    reply_eval_update_case,
    reply_test_run,
    router as reply_router,
)
```

- [ ] **步骤 2：include 新 router**

在 include 区加入：

```python
router.include_router(reply_router)
```

放在 `model_router` 之后、`trace_router` 之前即可；本模块没有和其他已拆模块冲突的 catch-all。

- [ ] **步骤 3：删除父模块 Reply Eval 本地实现**

删除 `api/admin_routes.py` 中从：

```python
# Reply 手动测试 / A-B 评估
```

到：

```python
# Eval 系统 API
```

前一行之间的本地实现。保留 `# Eval 系统 API` 区块及其 imports。

- [ ] **步骤 4：清理父模块 import**

检查 `api/admin_routes.py` 顶部 imports。只有在确认父模块其他区块不再使用时，才删除多余 import。

必须保留：

- `json`：其他区块仍使用。
- `hashlib`、`Path`、`Any`：Eval 系统 API 仍使用。
- `datetime`、`timedelta`：运行态、Reply 外其他区块或 Eval 区块可能使用，删除前用 `rg` 确认。
- `Literal`、`Optional`：父模块剩余 request model 仍可能使用，删除前用 `rg` 确认。
- `row_to_dict`：`/model-replies` 和 Eval 系统 API 仍使用。

运行：

```bash
python -m compileall api/admin_routes.py api/admin/reply_routes.py -q
```

预期：无输出，退出码为 0。

## 任务 4：验证实现并提交

**文件：**
- 修改：`tests/test_admin_reply_routes_split.py`
- 修改：`api/admin/reply_routes.py`
- 修改：`api/admin_routes.py`
- 修改：`.Codex/plans/admin-reply-routes-split.md`

- [ ] **步骤 1：运行 split 测试绿灯**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_reply_routes_split.py -q
```

预期：`7 passed`，允许已有 warnings。

- [ ] **步骤 2：运行 Reply 行为回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_reply_admin.py \
  tests/test_admin_model_routes_split.py::test_model_replies_stays_in_parent_admin_routes \
  -q
```

预期：全部通过，确认旧父模块导入、Prompt Runtime metadata、合约重试、case CRUD、run 和 traffic
语义不变，且 `/model-replies` 仍留在父模块。

- [ ] **步骤 3：运行拆分兼容回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_reply_routes_split.py \
  tests/test_admin_model_routes_split.py \
  tests/test_admin_tool_routes_split.py \
  tests/test_admin_sticker_routes_split.py \
  tests/test_admin_group_memory_routes_split.py \
  tests/test_admin_observability_routes_split.py \
  tests/test_admin_db_browser.py \
  -q
```

预期：全部通过，确认新 router 没有破坏既有管理端拆分合同。

- [ ] **步骤 4：运行鉴权与 asyncio 策略回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_api.py::TestAuth \
  tests/test_asyncio_run_policy.py \
  tests/test_admin_reply_routes_split.py::test_admin_reply_routes_do_not_import_parent_admin_routes_or_sync_awaitable \
  -q
```

预期：全部通过。

- [ ] **步骤 5：运行静态检查**

运行：

```bash
python -m compileall api/admin_routes.py api/admin/reply_routes.py -q
git diff --check -- api/admin_routes.py api/admin/reply_routes.py tests/test_admin_reply_routes_split.py .Codex/plans/admin-reply-routes-split.md
rg -n "from api\\.admin_routes|import api\\.admin_routes|asyncio\\.run|run_awaitable_sync" api/admin/reply_routes.py
wc -l api/admin_routes.py api/admin/reply_routes.py tests/test_admin_reply_routes_split.py
```

预期：

- `compileall` 无输出。
- `git diff --check` 无输出。
- `rg` 无输出，退出码为 1。
- `api/admin_routes.py` 行数明显低于 2647 行。

- [ ] **步骤 6：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

- [ ] **步骤 7：更新计划执行结果**

在本计划「当前状态」中记录：

- 红灯测试输出。
- split 绿灯输出。
- Reply 行为回归输出。
- 拆分兼容回归输出。
- 鉴权与 asyncio 策略回归输出。
- 静态检查输出。
- 行数检查输出。
- 全量测试输出。

- [ ] **步骤 8：提交实现**

只暂存本阶段文件：

```bash
git add api/admin_routes.py api/admin/reply_routes.py tests/test_admin_reply_routes_split.py .Codex/plans/admin-reply-routes-split.md
git commit -m "refactor(管理端): 拆分回复评测路由"
```

## 任务 5：文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/admin-reply-routes-split.md`

- [ ] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」当前状态中记录：

- Admin Reply Eval 已拆到 `api/admin/reply_routes.py`。
- `api/admin_routes.py`、`api/admin/reply_routes.py` 和 split 测试的新行数。
- 验证命令和结果。
- 下一刀候选仍为 Eval Workbench、Runtime / Overview、Settings 或普通 API common auth。

- [ ] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-21 Admin Reply Eval 路由拆分` 小节，记录：

- 设计文档路径。
- 实现计划路径。
- 阶段提交 hash。
- 红灯、绿灯、定向回归、全量测试和行数结果。
- 执行约束：不拆普通 API、不迁移 `/evals/*`、不改变 Prompt Runtime 模板、不新增
  `asyncio.run()` 或同步 awaitable 包装。

- [ ] **步骤 3：更新本计划最终状态**

把本计划顶部状态改为已完成，并补充文档收口验证结果。

- [ ] **步骤 4：验证文档收口**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-reply-routes-split.md
python -m pytest tests/ -v
```

预期：`git diff --check` 无输出，全量测试 0 failures。

- [ ] **步骤 5：提交文档收口**

只暂存本阶段文件：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-reply-routes-split.md
git commit -m "docs(计划): 收口回复评测路由拆分"
```
