# Admin Eval Workbench 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api/admin_routes.py` 中 `/evals/*` 管理端 HTTP 层拆到 `api/admin/eval_routes.py`，保持路径、响应结构、鉴权 monkeypatch、TimingGate proposal report monkeypatch、候选运营语义、评测 run 语义和旧导入路径不变。

**架构：** `api.admin_routes` 继续作为 `/api/v1/admin` 顶层聚合 router，新建 `api.admin.eval_routes.router` 并由父 router include。新模块只承接 Eval Workbench HTTP 编排、request model、常量和域内 helper；eval storage、expected contract、sampling scheduler、suite runner 和 WebUI 页面保持既有实现语义。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 `api.admin.common` 鉴权与审计 helper。

---

## 当前状态（2026-06-21）

- [x] 已核对 `docs/todo.md` 剩余硬项：`api/admin_routes.py` 1935 行、`api/routes.py` 2822 行。
- [x] 已完成只读子 agent 分析：普通 API 拆分前需要 `verify_token` common auth，管理端下一刀优先 Eval Workbench。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-admin-eval-routes-split-design.md`。
- [x] 设计提交：`febd9f6 docs(管理端): 设计评测工作台路由拆分`。
- [x] 计划提交：`7c94a00 docs(计划): 记录评测工作台路由拆分计划`。
- [x] 红灯测试：`tests/test_admin_eval_routes_split.py -q` ->
  `4 failed, 5 passed, 21 warnings in 6.89s`；失败点为 endpoint module 仍是
  `api.admin_routes`、`api.admin.eval_routes` 尚不存在，以及
  `api/admin/eval_routes.py` 文件不存在。
- [x] 实现提交：`c2f042b refactor(管理端): 拆分评测工作台路由`。
  - split 绿灯：`9 passed, 21 warnings in 1.53s`。
  - Eval / Timing proposal 行为回归：`40 passed, 21 warnings in 7.05s`。
  - WebUI 与 asyncio 策略回归：`26 passed, 1 warning in 1.87s`。
  - 静态检查：`compileall` / `git diff --check` 无输出；反向导入与
    `asyncio.run` / `run_awaitable_sync` 扫描无命中。
  - 行数：`api/admin_routes.py` 1390 行、`api/admin/eval_routes.py` 614 行、
    `tests/test_admin_eval_routes_split.py` 194 行。
  - 全量测试：`1551 passed, 6 skipped, 139 warnings in 112.38s`。
- [x] 文档收口提交：本提交 `docs(计划): 收口评测工作台路由拆分`。
  - `docs/todo.md` 已同步 P3 超大文件队列行数、Admin Eval Workbench 第八刀记录、
    验证结果和下一刀候选。
  - `docs/plan_walkthrough.md` 已追加 `2026-06-21 Admin Eval Workbench 路由拆分`
    小节，记录设计 / 计划 / 实现提交、验证结果、行数和执行约束。

## 子 agent 分工约定

本阶段主线程负责最终编辑、验证和提交。若需要并行分工，使用只读或严格分离写入范围：

- **Agent A：测试契约。** 只修改 `tests/test_admin_eval_routes_split.py`，不改生产代码。输出红灯测试结果、失败数量和主要失败原因。
- **Agent B：Eval 路由模块草稿。** 只创建 `api/admin/eval_routes.py`，不改 `api/admin_routes.py`。按本计划迁移 request model、常量、helper 和 `/evals/*` endpoint。
- **Agent C：父模块集成。** 只修改 `api/admin_routes.py`。负责 include `eval_router`、re-export 旧符号、删除旧 Eval Workbench 区块，并保留其他管理端子域仍使用的 helper。
- **Agent D：验证审查。** 只读检查 `git diff`、路由注册、反向导入、`asyncio.run` 策略、proposal monkeypatch 兼容、行数和测试输出。不得修改代码。

接口约定：

- `api/admin/eval_routes.py` 导出 `router`，不得带 `/api/v1/admin` 前缀。
- `router` 不使用 `prefix="/evals"`，保持 endpoint path 与旧父模块声明一致。
- 新模块使用 `api.admin.common.verify_admin`、`audit_request()` 和 `client_ip()`，不得从 `api.admin_routes` 导入任何符号。
- `api.admin_routes` 必须 re-export 迁移后的 request model、常量、helper 和 endpoint 函数。
- `api.admin_routes.TIMING_TUNING_PROPOSAL_REPORT` monkeypatch 必须继续影响拆分后的 proposal endpoint。
- `/model-replies`、Runtime / Overview、Settings、Configs、Prompt effective preview、Block / ContentBlock、DB backup / vacuum 保留在 `api.admin_routes`，不进入本阶段。
- 生产代码不得新增 `asyncio.run()`，不得新增 `run_awaitable_sync`，不得新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、`enriched_query`、工具 usage 文档或 Prompt Runtime 输入。

## 文件职责

- 创建：`tests/test_admin_eval_routes_split.py`
  - 锁定 21 个 Eval Workbench route 的 endpoint module 为 `api.admin.eval_routes`。
  - 锁定 `api.admin_routes` 对迁移符号的旧导入兼容。
  - 锁定 `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对新模块路由仍生效。
  - 锁定 `api.admin_routes.TIMING_TUNING_PROPOSAL_REPORT` monkeypatch 对 proposal endpoint 仍生效。
  - 锁定迁移路由未重复注册。
  - 锁定 `/evals/candidates/preflight`、`/evals/candidates/batch-audit`、`/evals/candidates/trend` 先于 `/evals/candidates/{case_id}` 注册。
  - 锁定 `/evals/runs` 先于 `/evals/runs/{run_id}` 注册。
  - 锁定 `eval_run_sample()` 仍是 coroutine function。
  - 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。
- 创建：`api/admin/eval_routes.py`
  - 定义 `router = APIRouter(tags=["admin-eval"])`。
  - 持有 `TimingTuningProposalReviewRequest`、`CandidatePreflightRequest`、`CandidateBatchAuditDecision`、`CandidateBatchAuditRequest`、`EvalCandidatePatch`、`LabelRequest`、`PromoteRequest`、`CandidateTriageRequest`、`EvalRunRequest`。
  - 持有 `TIMING_TUNING_PROPOSAL_REPORT`、`TIMING_TUNING_REVIEW_DECISIONS`。
  - 持有 `_current_timing_tuning_proposal_report()`、`_proposal_sha256()`、`_proposal_missing_response()`、`_proposal_review_from_audit()`、`_triage_response_or_404()`。
  - 持有 21 个 `/evals/*` endpoint。
- 修改：`api/admin_routes.py`
  - 导入并 include `eval_router`。
  - re-export 迁移符号。
  - 删除本地 `# Eval 系统 API` 区块。
  - 保留仍被其他子域使用的 `json`、`Any`、`Optional`、`AdminAuditLog`、`get_db`、`_client_ip()` 和 `_audit_request()`，删除不再使用的 `hashlib`、`Path` 和 eval store import。
- 实现收口阶段修改：`docs/todo.md`、`docs/plan_walkthrough.md`、`.Codex/plans/admin-eval-routes-split.md`。

## 任务 1：补 Admin Eval Workbench 路由拆分红灯测试

**文件：**
- 创建：`tests/test_admin_eval_routes_split.py`

- [x] **步骤 1：创建 split 路由测试文件**

创建 `tests/test_admin_eval_routes_split.py`：

```python
from __future__ import annotations

import inspect
from pathlib import Path


_ADMIN_EVAL_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/evals/expected-contract"),
    ("GET", "/api/v1/admin/evals/timing-tuning/proposal"),
    ("GET", "/api/v1/admin/evals/timing-tuning/proposal/review"),
    ("POST", "/api/v1/admin/evals/timing-tuning/proposal/reviews"),
    ("GET", "/api/v1/admin/evals/candidates"),
    ("POST", "/api/v1/admin/evals/candidates/preflight"),
    ("POST", "/api/v1/admin/evals/candidates/batch-audit"),
    ("GET", "/api/v1/admin/evals/candidates/trend"),
    ("GET", "/api/v1/admin/evals/candidates/{case_id}"),
    ("PATCH", "/api/v1/admin/evals/candidates/{case_id}"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/label"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/reject"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/defer"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/reopen"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/ignore"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/promote"),
    ("POST", "/api/v1/admin/evals/sample/run"),
    ("GET", "/api/v1/admin/evals/sample/status"),
    ("POST", "/api/v1/admin/evals/run"),
    ("GET", "/api/v1/admin/evals/runs"),
    ("GET", "/api/v1/admin/evals/runs/{run_id}"),
)


_EVAL_ROUTE_EXPORTS = (
    "TimingTuningProposalReviewRequest",
    "CandidatePreflightRequest",
    "CandidateBatchAuditDecision",
    "CandidateBatchAuditRequest",
    "EvalCandidatePatch",
    "LabelRequest",
    "PromoteRequest",
    "CandidateTriageRequest",
    "EvalRunRequest",
    "TIMING_TUNING_PROPOSAL_REPORT",
    "TIMING_TUNING_REVIEW_DECISIONS",
    "_current_timing_tuning_proposal_report",
    "_proposal_sha256",
    "_proposal_missing_response",
    "_proposal_review_from_audit",
    "_triage_response_or_404",
    "eval_expected_contract",
    "eval_timing_tuning_proposal",
    "eval_timing_tuning_proposal_review_state",
    "eval_timing_tuning_proposal_review",
    "eval_list_candidates",
    "eval_preflight_candidates",
    "eval_candidate_batch_audit",
    "eval_candidates_trend",
    "eval_get_candidate",
    "eval_patch_candidate",
    "eval_label_candidate",
    "eval_reject_candidate",
    "eval_defer_candidate",
    "eval_reopen_candidate",
    "eval_ignore_candidate",
    "eval_promote_candidate",
    "eval_run_sample",
    "eval_sample_status",
    "eval_run_suite",
    "eval_list_runs",
    "eval_get_run",
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


def test_admin_eval_routes_are_registered_from_split_module():
    for method, path in _ADMIN_EVAL_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.eval_routes"}


def test_legacy_admin_routes_eval_imports_still_work():
    from api import admin_routes
    from api.admin import eval_routes

    for name in _EVAL_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(eval_routes, name)

    body = admin_routes.LabelRequest(expected_json={"timing_action": "continue"})
    assert body.normalized_expected() == {"timing_action": "continue"}
    assert admin_routes.CandidatePreflightRequest().status == "labeled"
    assert "needs_data" in admin_routes.TIMING_TUNING_REVIEW_DECISIONS


def test_split_eval_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/evals/expected-contract",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/evals/expected-contract",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_split_eval_routes_use_legacy_proposal_report_monkeypatch(client, monkeypatch, tmp_path):
    from api import admin_routes

    report = tmp_path / "proposal.json"
    report.write_text('{"proposal_version": 1}', encoding="utf-8")
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT", report, raising=False)

    response = client.get(
        "/api/v1/admin/evals/timing-tuning/proposal",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json()["report_path"] == str(report)


def test_admin_eval_routes_are_not_registered_twice():
    for method, path in _ADMIN_EVAL_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_eval_static_candidate_routes_before_dynamic_case_id_route():
    route_paths = [path for path, _route in _admin_route_entries()]

    dynamic_index = route_paths.index("/api/v1/admin/evals/candidates/{case_id}")
    assert route_paths.index("/api/v1/admin/evals/candidates/preflight") < dynamic_index
    assert route_paths.index("/api/v1/admin/evals/candidates/batch-audit") < dynamic_index
    assert route_paths.index("/api/v1/admin/evals/candidates/trend") < dynamic_index


def test_admin_eval_static_runs_route_before_dynamic_run_id_route():
    route_paths = [path for path, _route in _admin_route_entries()]

    runs_index = route_paths.index("/api/v1/admin/evals/runs")
    run_id_index = route_paths.index("/api/v1/admin/evals/runs/{run_id}")

    assert runs_index < run_id_index


def test_admin_eval_async_boundaries_remain_coroutines():
    from api.admin import eval_routes

    assert inspect.iscoroutinefunction(eval_routes.eval_run_sample)


def test_admin_eval_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    path = Path("api/admin/eval_routes.py")

    assert path.exists()
    source = path.read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_eval_routes_split.py -q
```

预期：FAIL。主要失败点应为 endpoint module 仍是 `api.admin_routes`、`api.admin.eval_routes`
尚不存在或 `api/admin/eval_routes.py` 文件不存在。

## 任务 2：迁移 Eval Workbench 路由模块

**文件：**
- 创建：`api/admin/eval_routes.py`

- [x] **步骤 1：创建新模块并迁移 import / 常量 / request model**

创建 `api/admin/eval_routes.py`。模块头必须包含：

```python
"""Admin Eval Workbench 路由。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import audit_request, client_ip, verify_admin
from core.database import AdminAuditLog, get_db
from core.eval_sampling.store import (
    candidate_queue_summary,
    candidate_trend_report,
    defer_candidate,
    get_candidate,
    get_run,
    get_runs,
    ignore_candidate,
    label_candidate,
    list_candidates,
    plan_candidate_batch_audit,
    plan_candidate_promotion,
    preflight_candidate_promotions,
    promote_candidate,
    record_candidate_batch_audit,
    reject_candidate,
    reopen_candidate,
    save_run,
    save_run_results,
    update_candidate,
)
from evals.expected_contract import expected_contract_payload

router = APIRouter(tags=["admin-eval"])

TIMING_TUNING_PROPOSAL_REPORT = Path("evals/reports/timing_tuning_proposal_latest.json")
TIMING_TUNING_REVIEW_DECISIONS = {
    "needs_data",
    "rejected",
    "approved_for_manual_experiment",
    "reviewed_no_change",
}
```

将以下 request model 从 `api/admin_routes.py` 原样迁入：

- `TimingTuningProposalReviewRequest`
- `CandidatePreflightRequest`
- `CandidateBatchAuditDecision`
- `CandidateBatchAuditRequest`
- `EvalCandidatePatch`
- `LabelRequest`
- `PromoteRequest`
- `CandidateTriageRequest`
- `EvalRunRequest`

- [x] **步骤 2：实现 proposal report 兼容 helper**

新增 helper：

```python
def _current_timing_tuning_proposal_report() -> Path:
    admin_routes = sys.modules.get("api.admin_routes")
    if admin_routes is not None and hasattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT"):
        return Path(getattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT"))
    return Path(TIMING_TUNING_PROPOSAL_REPORT)
```

将 proposal endpoint 中的 `Path(TIMING_TUNING_PROPOSAL_REPORT)` 改为：

```python
path = _current_timing_tuning_proposal_report()
```

`_proposal_sha256()`、`_proposal_missing_response()`、`_proposal_review_from_audit()` 和
`_triage_response_or_404()` 从父模块原样迁入。

- [x] **步骤 3：迁移 21 个 `/evals/*` endpoint**

从 `api/admin_routes.py` 的 `# Eval 系统 API` 区块原样迁入 21 个 endpoint。迁入时只做必要替换：

- `_client_ip(request)` -> `client_ip(request)`。
- `_audit_request(...)` -> `audit_request(...)`。
- proposal report path 使用 `_current_timing_tuning_proposal_report()`。
- 保留 `eval_run_sample()` 为 `async def` 并继续 `await run_sampling_cycle()`。
- 保留 `eval_run_suite()` 的同步 `def` 形态，不引入 `asyncio.run()` 或 awaitable 包装。

- [x] **步骤 4：运行新模块语法检查**

运行：

```bash
python -m compileall api/admin/eval_routes.py -q
```

预期：exit 0，无输出。

## 任务 3：集成父模块并保留旧导入面

**文件：**
- 修改：`api/admin_routes.py`

- [x] **步骤 1：导入新 eval 模块符号**

在 `api/admin_routes.py` 已拆模块 import 区增加：

```python
from api.admin.eval_routes import (
    CandidateBatchAuditDecision,
    CandidateBatchAuditRequest,
    CandidatePreflightRequest,
    CandidateTriageRequest,
    EvalCandidatePatch,
    EvalRunRequest,
    LabelRequest,
    PromoteRequest,
    TIMING_TUNING_PROPOSAL_REPORT,
    TIMING_TUNING_REVIEW_DECISIONS,
    TimingTuningProposalReviewRequest,
    _current_timing_tuning_proposal_report,
    _proposal_missing_response,
    _proposal_review_from_audit,
    _proposal_sha256,
    _triage_response_or_404,
    eval_candidate_batch_audit,
    eval_candidates_trend,
    eval_defer_candidate,
    eval_expected_contract,
    eval_get_candidate,
    eval_get_run,
    eval_ignore_candidate,
    eval_label_candidate,
    eval_list_candidates,
    eval_list_runs,
    eval_patch_candidate,
    eval_preflight_candidates,
    eval_promote_candidate,
    eval_reject_candidate,
    eval_reopen_candidate,
    eval_run_sample,
    eval_run_suite,
    eval_sample_status,
    eval_timing_tuning_proposal,
    eval_timing_tuning_proposal_review,
    eval_timing_tuning_proposal_review_state,
    router as eval_router,
)
```

- [x] **步骤 2：include 新 router**

在 `reply_router` 附近加入：

```python
router.include_router(eval_router)
```

`eval_router` 与现有 `/groups/{group_id:path}` catch-all 没有路径冲突；放在 `reply_router`
后、`trace_router` 前即可。

- [x] **步骤 3：删除父模块本地 Eval Workbench 区块**

删除 `api/admin_routes.py` 中从：

```python
# Eval 系统 API
```

到文件末尾的旧 `/evals/*` 实现，并保留父模块中 Settings 及之前的区块。删除后检查父模块 import：

- `hashlib` 若父模块不再使用则删除。
- `EvalCandidate`、`EvalRun`、`EvalRunResult` 导入删除。
- `expected_contract_payload` 与 `core.eval_sampling.store` 相关 import 删除。
- `Path` 若仍被父模块其他区块使用则保留。
- `Any`、`Optional` 若仍被父模块其他 request model 使用则保留。

- [x] **步骤 4：运行语法检查**

运行：

```bash
python -m compileall api/admin_routes.py api/admin/eval_routes.py -q
```

预期：exit 0，无输出。

## 任务 4：验证拆分测试绿灯与行为回归

**文件：**
- 验证：`tests/test_admin_eval_routes_split.py`
- 验证：`tests/test_eval_candidate_contract.py`
- 验证：`tests/test_timing_tuning_proposal_admin.py`
- 验证：`tests/test_webui_admin_redesign.py`
- 验证：`tests/test_asyncio_run_policy.py`

- [x] **步骤 1：运行 split 测试绿灯**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_eval_routes_split.py -q
```

预期：PASS。

- [x] **步骤 2：运行 Eval 行为回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_eval_candidate_contract.py tests/test_timing_tuning_proposal_admin.py -q
```

预期：PASS。

- [x] **步骤 3：运行相邻回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_webui_admin_redesign.py tests/test_asyncio_run_policy.py -q
```

预期：PASS。

- [x] **步骤 4：运行静态检查**

运行：

```bash
python -m compileall api/admin_routes.py api/admin/eval_routes.py -q
git diff --check -- api/admin_routes.py api/admin/eval_routes.py tests/test_admin_eval_routes_split.py .Codex/plans/admin-eval-routes-split.md docs/superpowers/specs/2026-06-21-admin-eval-routes-split-design.md
rg -n "from api\.admin_routes|import api\.admin_routes|asyncio\.run|run_awaitable_sync" api/admin/eval_routes.py
```

预期：`compileall` 和 `git diff --check` exit 0 且无输出；`rg` 无命中，退出码为 1。

- [x] **步骤 5：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

## 任务 5：实现阶段提交

**文件：**
- 暂存：`api/admin_routes.py`
- 暂存：`api/admin/eval_routes.py`
- 暂存：`tests/test_admin_eval_routes_split.py`

- [x] **步骤 1：检查待提交差异**

运行：

```bash
git diff -- api/admin_routes.py api/admin/eval_routes.py tests/test_admin_eval_routes_split.py
git status --short -- api/admin_routes.py api/admin/eval_routes.py tests/test_admin_eval_routes_split.py
```

预期：只包含本阶段三个实现相关文件。

- [x] **步骤 2：按文件暂存并提交**

运行：

```bash
git add api/admin_routes.py api/admin/eval_routes.py tests/test_admin_eval_routes_split.py
git commit -m "refactor(管理端): 拆分评测工作台路由"
```

预期：commit 成功。禁止使用 `git add .` 或 `git add -A`。

## 任务 6：文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/admin-eval-routes-split.md`

- [x] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」下追加 Admin Eval Workbench 拆分进展，记录：

- 新模块 `api/admin/eval_routes.py`。
- `api/admin_routes.py` 拆分后的行数。
- split 测试、行为回归、asyncio 策略回归和全量测试结果。
- 下一刀候选：Runtime / Overview、Settings，或普通 API common auth。

- [x] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-21 Admin Eval Workbench 路由拆分` 小节，记录设计提交、计划提交、实现提交、验证结果、行数和执行约束。

- [x] **步骤 3：更新本计划当前状态**

将本计划「当前状态」中的计划提交、红灯测试、实现提交、文档收口提交相关条目补齐实际 commit hash 和测试输出。

- [x] **步骤 4：验证文档格式和全量测试**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-eval-routes-split.md
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：`git diff --check` exit 0 且无输出；全量测试 0 failures。

- [x] **步骤 5：按文件暂存并提交文档收口**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-eval-routes-split.md
git commit -m "docs(计划): 收口评测工作台路由拆分"
```

预期：commit 成功。禁止使用 `git add .` 或 `git add -A`。
