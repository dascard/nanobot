# Admin Eval Workbench 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍未完成。当前剩余硬项为
`api/admin_routes.py` 1935 行和 `api/routes.py` 2822 行。管理端已经完成 DB Browser、
Sticker / Generated Images、Group Memory、Observability、Tools、Models 和 Reply Eval
多刀拆分，并形成稳定模式：

- `api.admin_routes.router` 继续作为 `/api/v1/admin` 聚合 router。
- 子模块暴露自己的 `router`，由 `api.admin_routes` include。
- 子模块使用 `api.admin.common.verify_admin`，兼容
  `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch。
- `api.admin_routes` re-export 迁移后的 request model、常量、helper 和 endpoint，
  保持旧导入路径。
- 拆分测试锁定 endpoint module、legacy import、token monkeypatch、路由重复注册、
  静态路径顺序和禁止反向导入 / 同步 awaitable 包装。

普通 `api/routes.py` 仍是更大的文件，但直接拆普通业务路由前需要先抽
`verify_token` common auth，并保持 `api.routes.verify_token` 与新 common auth 函数对象
一致，否则 `app.dependency_overrides[routes.verify_token]` 会对新子路由失效。该前置设施
适合单独设计和提交，本阶段继续沿管理端拆分。

## 候选方案

### 方案 A：拆分 Admin Eval Workbench 路由（推荐）

范围为 `api/admin_routes.py` 中 `# Eval 系统 API` 区块。新建
`api/admin/eval_routes.py`，承载 `/evals/*` 路由，包括 expected contract、TimingGate
调参提案、候选样本运营、采样、suite run 和 run 查询。

优点：

- 可从 `api/admin_routes.py` 迁出约 570 行，收益明确。
- 依赖集中在 `core.eval_sampling.store`、`evals.expected_contract` 和 `evals.run`，
  不进入 KT Bridge、Prompt Runtime 编排或聊天主链路。
- 现有 `tests/test_eval_candidate_contract.py` 与
  `tests/test_timing_tuning_proposal_admin.py` 覆盖 expected contract、候选列表、
  trend、patch、label、preflight、triage、batch audit、promote 和调参提案审查。
- 不触碰 `enriched_query`、conversation 结构、工具输出契约或 Prompt Runtime 模板。

风险：

- `/evals/candidates/{case_id}` 与 `/evals/candidates/preflight`、`/batch-audit`、
  `/trend` 共用前缀，拆分时必须锁定静态路径先于动态路径。
- `/evals/runs` 必须先于 `/evals/runs/{run_id}` 注册。
- 现有测试会 monkeypatch 父模块常量
  `api.admin_routes.TIMING_TUNING_PROPOSAL_REPORT`。迁移后 endpoint 不能只读取新模块
  本地常量，否则旧 monkeypatch 失效。
- 新模块不能反向导入 `api.admin_routes`，否则会重新制造聚合模块循环依赖。

结论：采用。

### 方案 B：先抽普通 API common auth

范围为 `api/routes.py` 的 `verify_token()` 与 `NANOBOT_API_TOKEN` 兼容层。新建
`api/common_auth.py`，让 `api.routes.verify_token is api.common_auth.verify_token`，并让
common auth 优先读取 `sys.modules["api.routes"].NANOBOT_API_TOKEN`。

优点：

- 为后续拆普通 API 的 stickers/media、tasks、history 等子路由解除 blocker。
- 能更早处理当前最大文件 `api/routes.py`。

风险：

- 本阶段行数收益小，仍不能立即拆业务区块。
- 鉴权对象身份、dependency override、生产 token 读取和测试 monkeypatch 都是公共 API
  兼容面，应该单独做设计和回归。

结论：保留为后续普通 API 拆分的前置阶段。

### 方案 C：拆分 Runtime / Overview 或 Settings

Runtime / Overview 范围为 `/overview`、`/groups`、`/groups/{group_id:path}`、
`/timing-gate/events` 和 `/timing-gate/test`。Settings 范围为 `/settings*`。

优点：

- Runtime / Overview 是管理端运行态面板边界，Settings 是热配置边界。
- Settings 风险低。

风险：

- Runtime / Overview 的 `/groups/{group_id:path}` 是 catch-all，必须继续排在已拆
  Group Memory 路由之后，顺序风险高于 Eval Workbench。
- Runtime / Overview 依赖面横跨 ChatLog、User、Config、Sticker、Block、Prompt 模板
  健康检查和 TimingGate。
- Settings 行数收益较低，且缺少直接 HTTP 回归锚点，需要先补行为测试。

结论：后续阶段再拆。

## 目标

将 Admin Eval Workbench HTTP 层从 `api/admin_routes.py` 拆到
`api/admin/eval_routes.py`，保持：

- 所有 `/api/v1/admin/evals/*` HTTP path、method、status code 和 response shape 不变。
- expected contract payload 不变。
- TimingGate 调参提案 report 读取、缺失响应、JSON 错误响应、review audit 和 review
  state 行为不变。
- Candidate list、summary、trend、preflight、batch audit、get、patch、label、reject、
  defer、reopen、ignore 和 promote 行为不变。
- Eval sampling run、sampling status、suite run、run list 和 run detail 行为不变。
- `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 继续影响拆分后的 eval 路由。
- `api.admin_routes.TIMING_TUNING_PROPOSAL_REPORT` monkeypatch 继续影响拆分后的 proposal
  endpoint。
- `api.admin_routes` 继续 re-export 迁移后的 request model、常量、helper 和 endpoint。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime
  输入。

## 模块边界

### 新增 `api/admin/eval_routes.py`

职责：

- 暴露 Eval expected contract。
- 读取 TimingGate 调参提案报告，并记录人工 review audit。
- 管理 Eval candidate 的查询、标注、triage、batch audit 和 promote。
- 触发 eval sampling cycle，并查询 sampling cursor。
- 运行 eval suite，保存 run 与 case results。
- 查询 eval run 列表和详情。

推荐模块头：

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
from evals.expected_contract import expected_contract_payload

router = APIRouter(tags=["admin-eval"])
```

新模块不设置 prefix，因为父模块已经提供 `/api/v1/admin` prefix。新模块不导入
`api.admin_routes`。

### 迁移端点

纳入 `api/admin/eval_routes.py`：

- `GET /evals/expected-contract` -> `eval_expected_contract()`
- `GET /evals/timing-tuning/proposal` -> `eval_timing_tuning_proposal()`
- `GET /evals/timing-tuning/proposal/review` ->
  `eval_timing_tuning_proposal_review_state()`
- `POST /evals/timing-tuning/proposal/reviews` ->
  `eval_timing_tuning_proposal_review()`
- `GET /evals/candidates` -> `eval_list_candidates()`
- `POST /evals/candidates/preflight` -> `eval_preflight_candidates()`
- `POST /evals/candidates/batch-audit` -> `eval_candidate_batch_audit()`
- `GET /evals/candidates/trend` -> `eval_candidates_trend()`
- `GET /evals/candidates/{case_id}` -> `eval_get_candidate()`
- `PATCH /evals/candidates/{case_id}` -> `eval_patch_candidate()`
- `POST /evals/candidates/{case_id}/label` -> `eval_label_candidate()`
- `POST /evals/candidates/{case_id}/reject` -> `eval_reject_candidate()`
- `POST /evals/candidates/{case_id}/defer` -> `eval_defer_candidate()`
- `POST /evals/candidates/{case_id}/reopen` -> `eval_reopen_candidate()`
- `POST /evals/candidates/{case_id}/ignore` -> `eval_ignore_candidate()`
- `POST /evals/candidates/{case_id}/promote` -> `eval_promote_candidate()`
- `POST /evals/sample/run` -> `eval_run_sample()`
- `GET /evals/sample/status` -> `eval_sample_status()`
- `POST /evals/run` -> `eval_run_suite()`
- `GET /evals/runs` -> `eval_list_runs()`
- `GET /evals/runs/{run_id}` -> `eval_get_run()`

不纳入：

- `/reply-test/*` 和 `/reply-eval/*`：已经属于 `api/admin/reply_routes.py`。
- `GET /model-replies`：仍是回复日志观测边界，留在父模块。
- Runtime / Overview、Block / ContentBlock、Configs、Prompt effective preview、Settings、
  DB backup / vacuum。
- 普通 `api/routes.py` 的聊天、群聊、memory、tasks 或公开 media endpoint。

### 迁移 request model、常量和 helper

迁移到 `api/admin/eval_routes.py`：

- `TimingTuningProposalReviewRequest`
- `CandidatePreflightRequest`
- `CandidateBatchAuditDecision`
- `CandidateBatchAuditRequest`
- `EvalCandidatePatch`
- `LabelRequest`
- `PromoteRequest`
- `CandidateTriageRequest`
- `EvalRunRequest`
- `TIMING_TUNING_PROPOSAL_REPORT`
- `TIMING_TUNING_REVIEW_DECISIONS`
- `_proposal_sha256()`
- `_proposal_missing_response()`
- `_proposal_review_from_audit()`
- `_triage_response_or_404()`

新增内部 helper：

- `_current_timing_tuning_proposal_report()`：优先读取
  `sys.modules["api.admin_routes"].TIMING_TUNING_PROPOSAL_REPORT`，回退到新模块常量。
  这样保持旧父模块 monkeypatch 兼容，同时避免新模块反向导入父模块。

### 修改 `api/admin_routes.py`

`api/admin_routes.py` 只做聚合和兼容：

- 导入 `router as eval_router`。
- 在 include 区新增 `router.include_router(eval_router)`。
- re-export 上述 request model、常量、helper 和 endpoint。
- 删除本地 `# Eval 系统 API` 区块。
- 保留 `/model-replies`、Settings、DB 运维和其他本阶段外的本地实现。

## 路由顺序

新模块必须按以下顺序注册，避免动态路由吞掉静态路由：

1. `/evals/expected-contract`
2. `/evals/timing-tuning/proposal`
3. `/evals/timing-tuning/proposal/review`
4. `/evals/timing-tuning/proposal/reviews`
5. `/evals/candidates`
6. `/evals/candidates/preflight`
7. `/evals/candidates/batch-audit`
8. `/evals/candidates/trend`
9. `/evals/candidates/{case_id}`
10. `/evals/candidates/{case_id}/label`
11. `/evals/candidates/{case_id}/reject`
12. `/evals/candidates/{case_id}/defer`
13. `/evals/candidates/{case_id}/reopen`
14. `/evals/candidates/{case_id}/ignore`
15. `/evals/candidates/{case_id}/promote`
16. `/evals/sample/run`
17. `/evals/sample/status`
18. `/evals/run`
19. `/evals/runs`
20. `/evals/runs/{run_id}`

`/evals/candidates/preflight`、`/evals/candidates/batch-audit` 和
`/evals/candidates/trend` 必须早于 `/evals/candidates/{case_id}`。`/evals/runs` 必须早于
`/evals/runs/{run_id}`。

## 兼容策略

### 认证

新模块必须使用 `api.admin.common.verify_admin`。该函数会从
`sys.modules["api.admin_routes"].NANOBOT_ADMIN_TOKEN` 读取当前 token，因此现有
`monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "...")` 测试仍然生效。

### Proposal report monkeypatch

拆分后 endpoint 读取 report path 时必须调用 `_current_timing_tuning_proposal_report()`，
不能直接使用 `TIMING_TUNING_PROPOSAL_REPORT` 常量。旧测试和潜在调用方可以继续
monkeypatch `api.admin_routes.TIMING_TUNING_PROPOSAL_REPORT`，新模块也可以直接
monkeypatch `api.admin.eval_routes.TIMING_TUNING_PROPOSAL_REPORT`。

### Legacy import

`api.admin_routes` 必须继续 re-export 迁移后的 request model、常量、helper 和 endpoint。
现有测试或脚本若继续 `from api import admin_routes` 后访问这些符号，应拿到与
`api.admin.eval_routes` 同一个对象。

### Prompt Runtime

本阶段不修改 Prompt Runtime 编排。`/evals/*` 只是管理评测数据和运行 eval suite，不改变
`PromptRuntimeInput`、模板 selector、变量注册、工具 usage 文档、history 注入或
`enriched_query` 组装。无需同步 `prompts.v2.default/*`、`data/prompts_v2/*`、
`core/prompt_v2/variables.py` 或 `core/prompt_v2/template_registry.py`。

## 测试策略

新增 `tests/test_admin_eval_routes_split.py`，覆盖：

- 所有 `/api/v1/admin/evals*` endpoint 注册到 `api.admin.eval_routes`。
- 每条 eval route 没有重复注册。
- `api.admin_routes` 继续 re-export 迁移后的 request model、常量、helper 和 endpoint。
- 拆分后的 eval route 仍使用旧 `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch。
- `api.admin_routes.TIMING_TUNING_PROPOSAL_REPORT` monkeypatch 仍影响 proposal endpoint。
- `/evals/candidates/preflight`、`/batch-audit`、`/trend` 早于
  `/evals/candidates/{case_id}`。
- `/evals/runs` 早于 `/evals/runs/{run_id}`。
- 新模块源码不包含 `from api.admin_routes`、`import api.admin_routes`、`asyncio.run` 或
  `run_awaitable_sync`。

定向回归：

- `python -m pytest tests/test_admin_eval_routes_split.py -q`
- `python -m pytest tests/test_eval_candidate_contract.py tests/test_timing_tuning_proposal_admin.py -q`
- `python -m pytest tests/test_webui_admin_redesign.py tests/test_asyncio_run_policy.py -q`
- `python -m compileall api/admin_routes.py api/admin/eval_routes.py -q`
- `git diff --check -- api/admin_routes.py api/admin/eval_routes.py tests/test_admin_eval_routes_split.py .Codex/plans/admin-eval-routes-split.md docs/superpowers/specs/2026-06-21-admin-eval-routes-split-design.md`

提交前全量回归：

- `python -m pytest tests/ -v`

## 不做事项

- 不拆普通 `api/routes.py`。
- 不抽 `verify_token` common auth。
- 不迁移 Runtime / Overview、Settings、Configs、Prompt effective preview、Block /
  ContentBlock、DB backup / vacuum 或 `/model-replies`。
- 不改变 eval storage、scorer、runner、dataset 文件格式或 WebUI 页面。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 或 conversation 注入。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

## 成功标准

- `api/admin/eval_routes.py` 独立承载 `/evals/*`。
- `api/admin_routes.py` 行数明显下降，并继续作为聚合 router 与 legacy export 面。
- 新拆分测试先红后绿，失败原因是路由尚未迁移或模块尚不存在。
- Eval 行为回归、TimingGate proposal 回归、asyncio 策略回归和全量测试通过。
- `docs/todo.md` 与 `docs/plan_walkthrough.md` 在实现阶段收口时同步最新进度。
