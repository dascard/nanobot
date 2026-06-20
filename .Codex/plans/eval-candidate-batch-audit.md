# EvalCandidate 人工仲裁批次审计实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为通用 `EvalCandidate` 队列增加 record-only 人工仲裁批次审计能力，支持只读计划、Admin 审计落库、CLI 导出和 WebUI 当前页只读审计视图。

**架构：** 后端 store 层生成批次审计 plan，并在 apply 时写入一条 `AdminAuditLog`，不修改 `EvalCandidate` 状态。Admin API 暴露 `POST /api/v1/admin/evals/candidates/batch-audit`，CLI 与 WebUI 复用同一批次快照语义。

**技术栈：** Python、FastAPI、SQLAlchemy、pytest、React、Vite。

---

## 文件职责

- 修改 `core/eval_sampling/store.py`：新增批次审计 decision 校验、plan 构建、counts 聚合和 `AdminAuditLog` 记录函数。
- 修改 `api/admin_routes.py`：新增 `CandidateBatchAuditRequest`、`POST /evals/candidates/batch-audit` 端点。
- 修改 `tests/test_eval_candidate_contract.py`：覆盖后端 plan、API dry-run、审计落库和非法请求。
- 修改 `evals/candidates.py`：新增只读 `audit` 子命令。
- 修改 `tests/test_eval_candidates_cli.py`：覆盖 CLI audit 输出与只读保证。
- 修改 `webui/src/features/evals/EvalsPage.jsx`：新增「批次审计」只读弹窗，复用当前页 preflight。
- 修改 `tests/test_webui_admin_redesign.py`：新增静态守卫，确认只读入口存在且无批量写入入口。
- 修改 `docs/evals.md`：记录批次审计 API、CLI 和 WebUI 行为。
- 修改 `docs/plan_walkthrough.md`：追加真实样本运营 5 的进度、验证记录和下一步。
- 修改 `docs/todo.md`：同步路线项 8 的剩余重点。

## 任务 1：后端批次审计 plan 与 API

**文件：**

- 修改：`tests/test_eval_candidate_contract.py`
- 修改：`core/eval_sampling/store.py`
- 修改：`api/admin_routes.py`

- [ ] **步骤 1：编写失败的后端测试**

在 `tests/test_eval_candidate_contract.py` 增加测试：

```python
def test_candidate_batch_audit_dry_run_is_read_only(client, db_session, monkeypatch):
    from core.database import AdminAuditLog
    from core.eval_sampling.store import get_candidate, label_candidate

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session, case_id="cand_batch_ready")
    label_candidate(db_session, "cand_batch_ready", {"timing_action": "continue"})
    _insert_candidate(db_session, case_id="cand_batch_error", suite="error")
    label_candidate(db_session, "cand_batch_error", {"timing_action": "continue"})

    response = client.post(
        "/api/v1/admin/evals/candidates/batch-audit",
        headers=_auth_header(),
        json={
            "dry_run": True,
            "case_ids": ["cand_batch_ready", "cand_batch_error"],
            "target_dataset": "timing_gate",
            "batch_note": "人工复核",
            "decisions": [
                {"case_id": "cand_batch_ready", "decision": "promote_ready"},
                {
                    "case_id": "cand_batch_error",
                    "decision": "defer",
                    "reason_code": "needs_batch_review",
                    "defer_until": "2026-06-30",
                },
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["audit_log_id"] is None
    assert payload["total"] == 2
    assert payload["ready"] == 1
    assert payload["blocked"] == 1
    assert payload["counts"]["by_decision"]["promote_ready"] == 1
    assert payload["counts"]["by_decision"]["defer"] == 1
    assert payload["counts"]["by_blocking_reason"]["suite_not_runnable"] == 1
    assert db_session.query(AdminAuditLog).count() == 0
    assert get_candidate(db_session, "cand_batch_ready").status == "labeled"
    assert get_candidate(db_session, "cand_batch_error").status == "labeled"
```

继续增加：

```python
def test_candidate_batch_audit_apply_writes_single_audit_log(client, db_session, monkeypatch):
    from core.database import AdminAuditLog

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session, case_id="cand_batch_audit_1")
    _insert_candidate(db_session, case_id="cand_batch_audit_2")

    response = client.post(
        "/api/v1/admin/evals/candidates/batch-audit",
        headers=_auth_header(),
        json={
            "dry_run": False,
            "case_ids": ["cand_batch_audit_1", "cand_batch_audit_2"],
            "target_dataset": "timing_gate",
            "batch_note": "写入审计",
            "decisions": [
                {"case_id": "cand_batch_audit_1", "decision": "needs_label"},
                {
                    "case_id": "cand_batch_audit_2",
                    "decision": "reject",
                    "reason_code": "low_value",
                    "note": "价值较低",
                },
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["dry_run"] is False
    assert payload["audit_log_id"]

    audit = db_session.query(AdminAuditLog).filter_by(action="audit_eval_candidate_batch").one()
    assert audit.target_type == "eval_candidate_batch"
    assert audit.target_id == payload["batch_id"]
    detail = json.loads(audit.detail_json)
    assert detail["batch_id"] == payload["batch_id"]
    assert detail["batch_note"] == "写入审计"
    assert detail["counts"]["by_decision"]["needs_label"] == 1
    assert detail["counts"]["by_reason_code"]["low_value"] == 1
    assert [item["case_id"] for item in detail["items"]] == ["cand_batch_audit_1", "cand_batch_audit_2"]
```

继续增加非法请求测试：

```python
def test_candidate_batch_audit_rejects_invalid_scope_decision_and_stale_status(client, db_session, monkeypatch):
    from core.database import AdminAuditLog

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session, case_id="cand_batch_invalid")

    cases = [
        {"dry_run": True},
        {"dry_run": True, "case_ids": ["cand_batch_invalid", "cand_batch_invalid"]},
        {
            "dry_run": True,
            "case_ids": ["cand_batch_invalid"],
            "decisions": [{"case_id": "cand_batch_invalid", "decision": "unknown"}],
        },
        {
            "dry_run": True,
            "case_ids": ["cand_batch_invalid"],
            "decisions": [{"case_id": "cand_batch_invalid", "decision": "reject", "reason_code": "needs_batch_review"}],
        },
        {
            "dry_run": False,
            "case_ids": ["missing_case"],
        },
        {
            "dry_run": False,
            "case_ids": ["cand_batch_invalid"],
            "decisions": [{"case_id": "cand_batch_invalid", "decision": "noop", "expected_status": "labeled"}],
        },
    ]

    for body in cases:
        response = client.post(
            "/api/v1/admin/evals/candidates/batch-audit",
            headers=_auth_header(),
            json=body,
        )
        assert response.status_code == 400, response.text

    assert db_session.query(AdminAuditLog).count() == 0
```

- [ ] **步骤 2：运行后端红灯**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py::test_candidate_batch_audit_dry_run_is_read_only tests/test_eval_candidate_contract.py::test_candidate_batch_audit_apply_writes_single_audit_log tests/test_eval_candidate_contract.py::test_candidate_batch_audit_rejects_invalid_scope_decision_and_stale_status -q -p no:cacheprovider
```

预期：失败。失败点应是 `/batch-audit` 端点不存在或 store 函数不存在。

- [ ] **步骤 3：实现 store 层最小能力**

在 `core/eval_sampling/store.py`：

- 从 `core.database` import `AdminAuditLog`。
- 新增常量：
  - `BATCH_AUDIT_DECISIONS`
  - `BATCH_AUDIT_DECISION_REASON_CODES`
- 新增 helper：
  - `_normalize_batch_note()`
  - `_batch_audit_decision_by_case_id()`
  - `_validate_batch_audit_scope()`
  - `_normalize_batch_audit_decision()`
  - `_count_values()`
  - `_candidate_batch_id()`
- 新增函数：
  - `plan_candidate_batch_audit(...)`
  - `record_candidate_batch_audit(db, plan, *, ip_address="")`

实现要点：

- `case_ids` 去重失败直接 `ValueError`。
- 无 `case_ids` 且无 `suite/status/source` 直接 `ValueError`。
- `case_ids` 优先于过滤条件。
- `dry_run` 计划可以包含 missing case，`ok=false`。
- API apply 层遇 `ok=false` 拒绝写 audit。
- `expected_status` 不匹配时 item 增加 `errors`，plan `ok=false`。
- `defer_until` 只允许 decision `defer`。
- `note` 裁剪到 1000 字符。
- `batch_id` 根据 case_ids、filters 和当前秒级时间生成，格式类似 `batch_20260620_ab12cd34`。

- [ ] **步骤 4：实现 Admin API**

在 `api/admin_routes.py`：

- import `plan_candidate_batch_audit`、`record_candidate_batch_audit`。
- 新增 Pydantic model：
  - `CandidateBatchAuditDecision`
  - `CandidateBatchAuditRequest`
- 新增：

```python
@router.post("/evals/candidates/batch-audit")
def eval_candidate_batch_audit(...):
    ...
```

行为：

- `dry_run=true` 返回 plan。
- `dry_run=false` 且 `plan["ok"] is False` 返回 400。
- `dry_run=false` 调用 `record_candidate_batch_audit()`，返回带 `audit_log_id` 的结果。
- 捕获 `ValueError` 返回 400。

- [ ] **步骤 5：运行后端绿灯与相邻回归**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py::test_candidate_batch_audit_dry_run_is_read_only tests/test_eval_candidate_contract.py::test_candidate_batch_audit_apply_writes_single_audit_log tests/test_eval_candidate_contract.py::test_candidate_batch_audit_rejects_invalid_scope_decision_and_stale_status -q -p no:cacheprovider
python -B -m pytest tests/test_eval_candidate_contract.py -q -p no:cacheprovider
```

预期：新增测试通过；相邻回归 0 failures。

- [ ] **步骤 6：Commit 后端阶段**

```bash
git add core/eval_sampling/store.py api/admin_routes.py tests/test_eval_candidate_contract.py
git commit -m "feat(评测): 增加候选批次审计接口"
```

## 任务 2：CLI 与 WebUI 只读批次审计入口

**文件：**

- 修改：`tests/test_eval_candidates_cli.py`
- 修改：`evals/candidates.py`
- 修改：`tests/test_webui_admin_redesign.py`
- 修改：`webui/src/features/evals/EvalsPage.jsx`

- [ ] **步骤 1：编写 CLI 红灯测试**

在 `tests/test_eval_candidates_cli.py` 增加：

```python
def test_candidates_cli_audit_writes_read_only_batch_report(db_session, tmp_path, monkeypatch):
    from core.database import AdminAuditLog
    from core.eval_sampling.store import get_candidate, label_candidate
    from evals import candidates

    _redirect_promote_root(monkeypatch, tmp_path)
    _insert_candidate(db_session, case_id="cand_cli_audit")
    label_candidate(db_session, "cand_cli_audit", {"timing_action": "continue"})
    monkeypatch.setattr(candidates, "_open_db", lambda: _SessionWrapper(db_session))

    out = tmp_path / "candidate-audit.json"
    exit_code = candidates.main([
        "audit",
        "--suite",
        "timing_gate",
        "--status",
        "labeled",
        "--target-dataset",
        "timing_gate",
        "--out",
        str(out),
    ])

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["total"] == 1
    assert payload["counts"]["by_status"]["labeled"] == 1
    assert payload["items"][0]["readiness"]["ready"] is True
    assert db_session.query(AdminAuditLog).count() == 0
    assert get_candidate(db_session, "cand_cli_audit").status == "labeled"
```

- [ ] **步骤 2：编写 WebUI 静态红灯测试**

在 `tests/test_webui_admin_redesign.py` 增加：

```python
def test_evals_candidates_page_exposes_read_only_batch_audit():
    source = EVALS_PAGE.read_text(encoding="utf-8")

    assert "批次审计" in source
    assert "batchAudit" in source
    assert "/evals/candidates/preflight" in source
    assert "top_blocking_reasons" in source
    assert "blocking_reasons" in source
    assert "/evals/candidates/batch-triage" not in source
    assert "批量拒绝" not in source
    assert "批量暂缓" not in source
    assert "批量应用" not in source
```

- [ ] **步骤 3：运行 CLI / WebUI 红灯**

运行：

```bash
python -B -m pytest tests/test_eval_candidates_cli.py::test_candidates_cli_audit_writes_read_only_batch_report tests/test_webui_admin_redesign.py::test_evals_candidates_page_exposes_read_only_batch_audit -q -p no:cacheprovider
```

预期：失败。失败点为 CLI `audit` 子命令不存在、WebUI 缺少批次审计入口。

- [ ] **步骤 4：实现 CLI**

在 `evals/candidates.py`：

- import `plan_candidate_batch_audit`。
- 新增 `audit_candidates()`。
- 新增 argparse 子命令 `audit`：
  - `--suite`
  - `--status`
  - `--source`
  - `--target-dataset`
  - `--limit`
  - `--out`
- 输出 JSON；有 `--out` 写文件，无 `--out` 打印 stdout。
- 不提供 `--apply`。

- [ ] **步骤 5：实现 WebUI**

在 `webui/src/features/evals/EvalsPage.jsx`：

- 新增 state：
  - `batchAuditOpen`
  - `batchAuditResult`
  - `batchAuditError`
  - `batchAuditLoading`
- 新增 `runBatchAuditCurrentPage()`，复用当前页 `case_ids` 调 `/evals/candidates/preflight`，把返回数据包装成只读审计视图：
  - `counts.by_status` 来自 `candidateSummary.by_status`
  - `counts.by_suite` / `by_source` 来自 `candidateSummary`
  - `counts.by_blocking_reason` 来自 `candidateSummary.top_blocking_reasons`
  - `items` 来自 preflight items
- summary 区域新增「批次审计」按钮。
- 新增 modal 展示 `batchAuditResult.counts` 和 `batchAuditResult.items`。
- 不加入任何批量 apply / 批量 triage 按钮。

- [ ] **步骤 6：运行 CLI / WebUI 绿灯与构建**

运行：

```bash
python -B -m pytest tests/test_eval_candidates_cli.py::test_candidates_cli_audit_writes_read_only_batch_report tests/test_webui_admin_redesign.py::test_evals_candidates_page_exposes_read_only_batch_audit -q -p no:cacheprovider
python -B -m pytest tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py -q -p no:cacheprovider
npm --prefix webui run build
```

预期：测试 0 failures；build 退出码 0，允许现有 Vite chunk size / plugin timing warning。

- [ ] **步骤 7：Commit CLI / WebUI 阶段**

```bash
git status --short webui/dist
git add evals/candidates.py tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py webui/src/features/evals/EvalsPage.jsx webui/dist/index.html
git add -u webui/dist/assets
# 对 `git status --short webui/dist` 输出中的新增 hash 产物逐个执行显式 `git add`。
git commit -m "feat(评测): 增加候选批次审计入口"
```

## 任务 3：文档收口与最终验证

**文件：**

- 修改：`docs/evals.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`docs/todo.md`
- 修改：`.Codex/plans/eval-candidate-batch-audit.md`

- [ ] **步骤 1：同步文档**

`docs/evals.md` 新增「候选批次审计」小节，记录：

- Admin API `/evals/candidates/batch-audit`
- `dry_run` / apply 审计语义
- CLI `python -m evals.candidates audit`
- WebUI 只读弹窗
- 不批量变更状态的边界

`docs/plan_walkthrough.md`：

- 顶部追加真实样本运营第五步完成状态。
- 当前目标从「可优先考虑人工仲裁批次审计或真实样本趋势报表」更新为「批次审计已完成，下一步优先真实样本趋势报表或按周期报告调参」。
- 进度总览新增「真实样本运营 5」。
- 新增「已完成阶段详情：EvalCandidate 人工仲裁批次审计」。

`docs/todo.md`：

- 路线项 8 现状 / 痛点 / 粗略路径追加批次审计完成状态。
- 剩余重点移除「人工仲裁批次审计」。

- [ ] **步骤 2：更新计划复选框与验证记录**

在本文件中把已完成步骤勾选，并写入实际验证命令和结果。

- [ ] **步骤 3：最终组合验证**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py -q -p no:cacheprovider
python -B -m pytest tests/ -q -p no:cacheprovider
```

预期：0 failures。

- [ ] **步骤 4：Commit 文档收口**

```bash
git add docs/evals.md docs/plan_walkthrough.md docs/todo.md .Codex/plans/eval-candidate-batch-audit.md
git commit -m "docs(计划): 收口候选批次审计"
```
