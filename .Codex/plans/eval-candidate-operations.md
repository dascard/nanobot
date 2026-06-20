# EvalCandidate 运营规则实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:test-driven-development 执行实现步骤，并在提交前使用 superpowers:verification-before-completion 和 superpowers:chinese-commit-conventions。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为通用 `EvalCandidate` 候选队列增加 summary、readiness、批量 preflight 和安全晋升规则，避免不可运行 suite 或非法状态绕过进入正式 eval case。

**架构：** 不改数据库表结构，在 `core/eval_sampling/store.py` 中派生 readiness、summary 和 preflight 结果；Admin API 只扩展返回字段并新增只读 preflight；CLI 复用同一规则做批量 dry-run 聚合；WebUI 在候选列表展示资格和当前页预检，不做批量 apply。

**技术栈：** FastAPI、Pydantic、SQLAlchemy、pytest、React、Vite、现有 Admin audit、现有 EvalCandidate store。

---

## 执行记录

- [x] 设计阶段：写入 `docs/superpowers/specs/2026-06-20-eval-candidate-operations-design.md`。
- [x] 设计提交：`8dc41f5 docs(评测): 设计候选运营规则`。
- [ ] 计划阶段：写入 `.Codex/plans/eval-candidate-operations.md` 并提交。
- [ ] 后端 readiness / summary / 状态约束。
- [ ] 后端 preflight API 与 CLI 聚合 dry-run。
- [ ] WebUI summary、资格列和当前页 preflight。
- [ ] 文档收口、全量验证和 walkthrough 更新。

## 文件结构

- 修改：`core/eval_sampling/store.py`
  - 新增 `RUNNABLE_EVAL_SUITES`、readiness reason helper、`candidate_readiness()`、`candidate_queue_summary()`、`preflight_candidate_promotions()`。
  - 让 `_candidate_dict()` 附带 readiness。
  - 让 `list_candidates()` 按 `priority desc, id desc` 排序。
  - 让 `plan_candidate_promotion()` 复用 readiness，阻止不可运行 suite、非法 expected、目标冲突和非法 dataset。
  - 收窄 `update_candidate()` 的 `status` 变更。
- 修改：`api/admin_routes.py`
  - `GET /evals/candidates` 返回 `summary`。
  - 新增 `POST /evals/candidates/preflight`。
  - `PATCH /evals/candidates/{case_id}` 拒绝直接写 `labeled` / `promoted` / 未知状态。
- 修改：`evals/candidates.py`
  - `promote_labeled()` dry-run 返回 ready / blocked 聚合。
  - `--apply` 遇到 blocked 批次整体拒绝，不做部分写入。
- 修改：`webui/src/features/evals/EvalsPage.jsx`
  - 候选页展示 summary。
  - 表格展示 readiness badge 和首个阻断原因。
  - blocked 的 labeled 行禁用「提升」。
  - 详情弹窗展示完整 readiness。
  - 增加「预检当前页」只读弹窗。
- 修改：`tests/test_eval_candidate_contract.py`
  - 后端 readiness、summary、preflight、PATCH 状态约束和不可运行 suite 晋升阻断。
- 修改：`tests/test_eval_candidates_cli.py`
  - CLI mixed readiness dry-run 和 blocked apply 拒绝。
- 修改：`tests/test_webui_admin_redesign.py`
  - 静态守卫 WebUI summary、readiness、preflight 和禁用提升。
- 修改：`docs/evals.md`
  - 记录通用候选 readiness / preflight 操作方式。
- 修改：`docs/plan_walkthrough.md`
  - 同步本阶段状态、提交号和验证结果。
- 修改：`.Codex/plans/eval-candidate-operations.md`
  - 勾选执行记录，补充红灯、绿灯、回归和提交号。

## 子 agent 分配

可以把实现拆给多个子 agent，但必须固定接口并避免同时编辑同一文件。

- 后端 agent：只修改 `core/eval_sampling/store.py`、`api/admin_routes.py` 和 `tests/test_eval_candidate_contract.py`。输出 `readiness`、`summary`、`preflight` 的最终响应样例，不修改 CLI、WebUI 或文档。
- CLI agent：在后端 store 契约确定后启动，只修改 `evals/candidates.py` 和 `tests/test_eval_candidates_cli.py`。不得修改后端规则，只复用 store 函数。
- WebUI agent：在 API 契约确定后启动，只修改 `webui/src/features/evals/EvalsPage.jsx` 和 `tests/test_webui_admin_redesign.py`。不得修改后端或 CLI。
- 文档 agent：实现稳定后启动，只修改 `docs/evals.md`、`docs/plan_walkthrough.md` 和本计划文件。

主线程职责：

- 审查所有子 agent 的 diff，确认接口命名一致。
- 运行红灯、绿灯、相邻回归、WebUI build 和全量测试。
- 每个阶段按文件显式暂存并单独 commit。

如果当前会话不启用子 agent，则按任务顺序内联执行；不得跳过 TDD 红灯。

## 接口约定

### Readiness

每条 candidate dict 新增：

```json
{
  "readiness": {
    "ready": false,
    "can_label": true,
    "can_promote": false,
    "status": "blocked",
    "suite": "timing_gate",
    "target_dataset": "timing_gate",
    "target_path": "/repo/evals/cases/timing_gate/cand_1.json",
    "blocking_reasons": [
      {"code": "invalid_status", "message": "candidate status must be labeled before promote"}
    ],
    "warnings": []
  }
}
```

稳定 reason code：

- `candidate_not_found`
- `invalid_status`
- `suite_not_runnable`
- `expected_invalid`
- `target_dataset_invalid`
- `target_case_exists`

### Summary

`GET /evals/candidates` 返回：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "summary": {
    "total": 0,
    "filters": {"suite": "", "status": "", "source": "", "target_dataset": ""},
    "by_status": {},
    "by_suite": {},
    "by_source": {},
    "readiness": {"ready": 0, "blocked": 0},
    "top_blocking_reasons": []
  }
}
```

### Preflight

`POST /api/v1/admin/evals/candidates/preflight` 请求：

```json
{
  "case_ids": ["cand_1"],
  "suite": "timing_gate",
  "status": "labeled",
  "source": "db",
  "target_dataset": "timing_gate",
  "limit": 200
}
```

响应：

```json
{
  "ok": false,
  "total": 2,
  "ready": 1,
  "blocked": 1,
  "target_dataset": "timing_gate",
  "items": []
}
```

## 任务 1：后端红灯测试

**文件：**

- 修改：`tests/test_eval_candidate_contract.py`

- [ ] **步骤 1：新增 readiness 阻断测试**

在 `test_promote_candidate_rejects_unlabeled_and_file_conflict` 前新增：

```python
def test_candidate_readiness_blocks_status_error_suite_invalid_expected_and_existing_target(
    db_session,
    tmp_path,
    monkeypatch,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import candidate_readiness, get_candidate, label_candidate

    _insert_candidate(db_session, case_id="cand_needs_label")
    needs_label = get_candidate(db_session, "cand_needs_label")
    readiness = candidate_readiness(needs_label, target_dataset="timing_gate")
    assert readiness["ready"] is False
    assert readiness["can_label"] is True
    assert [reason["code"] for reason in readiness["blocking_reasons"]] == ["invalid_status", "expected_invalid"]

    _insert_candidate(db_session, case_id="cand_error", suite="error")
    label_candidate(db_session, "cand_error", {"timing_action": "continue"})
    error_readiness = candidate_readiness(get_candidate(db_session, "cand_error"), target_dataset="timing_gate")
    assert error_readiness["ready"] is False
    assert any(reason["code"] == "suite_not_runnable" for reason in error_readiness["blocking_reasons"])

    _insert_candidate(db_session, case_id="cand_ready")
    label_candidate(db_session, "cand_ready", {"timing_action": "continue"})
    target = tmp_path / "evals" / "cases" / "timing_gate" / "cand_ready.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    conflict = candidate_readiness(get_candidate(db_session, "cand_ready"), target_dataset="timing_gate")
    assert conflict["ready"] is False
    assert any(reason["code"] == "target_case_exists" for reason in conflict["blocking_reasons"])
```

- [ ] **步骤 2：新增列表 summary 与 readiness API 测试**

在 Admin API 测试区域新增：

```python
def test_eval_list_candidates_returns_summary_and_readiness(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session, case_id="cand_a")
    _insert_candidate(db_session, case_id="cand_b")

    response = client.get(
        "/api/v1/admin/evals/candidates",
        headers=_auth_header(),
        params={"suite": "timing_gate", "limit": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["by_status"]["candidate"] == 2
    assert payload["summary"]["by_suite"]["timing_gate"] == 2
    assert payload["summary"]["readiness"]["blocked"] == 2
    assert payload["items"][0]["readiness"]["ready"] is False
    assert payload["items"][0]["readiness"]["blocking_reasons"]
```

- [ ] **步骤 3：新增 PATCH 状态约束测试**

在 `test_eval_label_candidate_rejects_conflicting_expected_fields` 后新增：

```python
def test_eval_patch_candidate_rejects_direct_labeled_promoted_and_unknown_status(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session)

    for status in ("labeled", "promoted", "invalid"):
        response = client.patch(
            "/api/v1/admin/evals/candidates/cand_timing_gate_1",
            headers=_auth_header(),
            json={"status": status},
        )
        assert response.status_code == 400
        assert status in response.text

    ok = client.patch(
        "/api/v1/admin/evals/candidates/cand_timing_gate_1",
        headers=_auth_header(),
        json={"priority": 10, "note": "优先处理"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["priority"] == 10
    assert ok.json()["note"] == "优先处理"
```

- [ ] **步骤 4：新增不可运行 suite 晋升拒绝测试**

在 promote 测试区域新增：

```python
def test_promote_candidate_rejects_non_runnable_suite(db_session, tmp_path, monkeypatch):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import get_candidate, label_candidate, promote_candidate

    _insert_candidate(db_session, case_id="cand_error", suite="error")
    label_candidate(db_session, "cand_error", {"timing_action": "continue"})

    with pytest.raises(ValueError, match="suite_not_runnable"):
        promote_candidate(db_session, "cand_error", target_dataset="timing_gate")

    assert get_candidate(db_session, "cand_error").status == "labeled"
    assert not (tmp_path / "evals" / "cases" / "timing_gate" / "cand_error.json").exists()
```

- [ ] **步骤 5：运行红灯**

运行：

```bash
python -B -m pytest \
  tests/test_eval_candidate_contract.py::test_candidate_readiness_blocks_status_error_suite_invalid_expected_and_existing_target \
  tests/test_eval_candidate_contract.py::test_eval_list_candidates_returns_summary_and_readiness \
  tests/test_eval_candidate_contract.py::test_eval_patch_candidate_rejects_direct_labeled_promoted_and_unknown_status \
  tests/test_eval_candidate_contract.py::test_promote_candidate_rejects_non_runnable_suite \
  -q -p no:cacheprovider
```

预期：失败。失败原因应包括 `ImportError` / `AttributeError` 缺少 `candidate_readiness`、列表缺少 `summary` 或 PATCH 仍返回 200。

## 任务 2：后端 readiness、summary 与状态约束实现

**文件：**

- 修改：`core/eval_sampling/store.py`
- 修改：`api/admin_routes.py`

- [ ] **步骤 1：在 store 中新增常量和 reason helper**

在 `REPO_ROOT` 后新增：

```python
RUNNABLE_EVAL_SUITES = frozenset({
    "sticker",
    "memory_learning",
    "moderation",
    "model_routing",
    "group_reply",
    "reply_contract",
    "rendering_contract",
    "timing_gate",
})

VALID_CANDIDATE_STATUSES = frozenset({"candidate", "labeled", "ignored", "promoted"})


def _readiness_reason(code: str, message: str, **extra: Any) -> dict[str, Any]:
    reason: dict[str, Any] = {"code": code, "message": message}
    reason.update(extra)
    return reason
```

- [ ] **步骤 2：新增 dataset 校验辅助**

把 `_safe_dataset_name()` 改为复用一个不抛错 helper：

```python
def _validate_dataset_name(value: str) -> tuple[str, dict[str, Any] | None]:
    name = str(value or "regression").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return name, _readiness_reason(
            "target_dataset_invalid",
            f"invalid target_dataset: {value}",
            field="target_dataset",
        )
    return name, None


def _safe_dataset_name(value: str) -> str:
    name, reason = _validate_dataset_name(value)
    if reason:
        raise ValueError(reason["message"])
    return name
```

- [ ] **步骤 3：新增 `candidate_readiness()`**

在 `_safe_dataset_name()` 后新增：

```python
def candidate_readiness(
    row: EvalCandidate | None,
    *,
    target_dataset: str | None = None,
) -> dict[str, Any]:
    dataset, dataset_reason = _validate_dataset_name(
        target_dataset or (row.suite if row is not None else "regression")
    )
    target_path = ""
    blocking_reasons: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if row is None:
        blocking_reasons.append(_readiness_reason("candidate_not_found", "candidate not found"))
        return {
            "ready": False,
            "can_label": False,
            "can_promote": False,
            "status": "blocked",
            "suite": "",
            "target_dataset": dataset,
            "target_path": "",
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
        }

    if dataset_reason:
        blocking_reasons.append(dataset_reason)
    else:
        target_path = str(REPO_ROOT / "evals" / "cases" / dataset / f"{row.case_id}.json")

    if row.status != "labeled":
        blocking_reasons.append(_readiness_reason(
            "invalid_status",
            "candidate status must be labeled before promote",
            status=row.status,
        ))

    if row.suite not in RUNNABLE_EVAL_SUITES:
        blocking_reasons.append(_readiness_reason(
            "suite_not_runnable",
            "suite is not runnable",
            suite=row.suite,
        ))

    expected = _safe_json(row.expected_json, {})
    try:
        validate_expected_contract(row.suite, expected)
    except ValueError as exc:
        blocking_reasons.append(_readiness_reason(
            "expected_invalid",
            str(exc),
            suite=row.suite,
        ))

    if target_path and Path(target_path).exists():
        blocking_reasons.append(_readiness_reason(
            "target_case_exists",
            f"target case already exists: {target_path}",
            path=target_path,
        ))

    ready = not blocking_reasons
    return {
        "ready": ready,
        "can_label": row.status == "candidate",
        "can_promote": ready,
        "status": "ready" if ready else "blocked",
        "suite": row.suite,
        "target_dataset": dataset,
        "target_path": target_path,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
    }
```

- [ ] **步骤 4：让 `_candidate_dict()` 附带 readiness**

把返回 dict 扩展为：

```python
"readiness": candidate_readiness(r),
```

- [ ] **步骤 5：新增 summary 函数**

在 `list_candidates()` 前后新增可复用查询 helper 与 summary：

```python
def _candidate_query(db, *, suite: str = "", status: str = "", source: str = ""):
    q = db.query(EvalCandidate)
    if suite:
        q = q.filter(EvalCandidate.suite == suite)
    if status:
        q = q.filter(EvalCandidate.status == status)
    if source:
        q = q.filter(EvalCandidate.source == source)
    return q


def candidate_queue_summary(
    db,
    *,
    suite: str = "",
    status: str = "",
    source: str = "",
    target_dataset: str = "",
) -> dict[str, Any]:
    rows = _candidate_query(db, suite=suite, status=status, source=source).all()
    by_status: dict[str, int] = {}
    by_suite: dict[str, int] = {}
    by_source: dict[str, int] = {}
    readiness_counts = {"ready": 0, "blocked": 0}
    reason_counts: dict[str, int] = {}

    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        by_suite[row.suite] = by_suite.get(row.suite, 0) + 1
        by_source[row.source] = by_source.get(row.source, 0) + 1
        readiness = candidate_readiness(row, target_dataset=target_dataset or row.suite)
        if readiness["ready"]:
            readiness_counts["ready"] += 1
        else:
            readiness_counts["blocked"] += 1
        for reason in readiness["blocking_reasons"]:
            code = str(reason["code"])
            reason_counts[code] = reason_counts.get(code, 0) + 1

    return {
        "total": len(rows),
        "filters": {
            "suite": suite,
            "status": status,
            "source": source,
            "target_dataset": target_dataset,
        },
        "by_status": by_status,
        "by_suite": by_suite,
        "by_source": by_source,
        "readiness": readiness_counts,
        "top_blocking_reasons": [
            {"code": code, "count": count}
            for code, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    }
```

- [ ] **步骤 6：调整列表排序**

把 `list_candidates()` 查询改为：

```python
q = _candidate_query(db, suite=suite, status=status, source=source)
total = q.count()
rows = q.order_by(EvalCandidate.priority.desc(), EvalCandidate.id.desc()).offset(offset).limit(limit).all()
```

- [ ] **步骤 7：让 promote 复用 readiness**

在 `plan_candidate_promotion()` 中，`row` 存在后替换状态、expected、dataset 和冲突检查为：

```python
readiness = candidate_readiness(row, target_dataset=target_dataset)
if not readiness["ready"]:
    reason = readiness["blocking_reasons"][0]
    raise ValueError(f"{reason['code']}: {reason['message']}")

expected = _safe_json(row.expected_json, {})
dataset = readiness["target_dataset"]
out_path = Path(readiness["target_path"])
```

- [ ] **步骤 8：收窄 `update_candidate()` 状态写入**

在 `update_candidate()` 的字段循环前处理 `status`：

```python
next_status = fields.get("status")
if next_status is not None:
    if next_status not in {"candidate", "ignored"}:
        raise ValueError(f"invalid status transition: {next_status}")
    if row.status == "ignored" and next_status == "candidate":
        row.status = "candidate"
    elif row.status in {"candidate", "labeled"} and next_status == "ignored":
        row.status = "ignored"
    elif row.status != next_status:
        raise ValueError(f"invalid status transition: {row.status} -> {next_status}")
    fields = {key: value for key, value in fields.items() if key != "status"}
```

- [ ] **步骤 9：扩展 Admin API 返回 summary 并捕获 PATCH 错误**

在 `api/admin_routes.py` import 中加入 `candidate_queue_summary`。

`eval_list_candidates()` 返回：

```python
summary = candidate_queue_summary(db, suite=suite, status=status, source=source)
return {"items": items, "total": total, "page": page, "summary": summary}
```

`eval_patch_candidate()` 包裹 `update_candidate()`：

```python
try:
    result = update_candidate(db, case_id, **updates)
except ValueError as e:
    raise HTTPException(400, str(e))
```

- [ ] **步骤 10：运行后端绿灯**

运行任务 1 的同一 pytest 命令。

预期：`4 passed`。

- [ ] **步骤 11：运行后端相邻回归**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 12：提交后端 readiness 阶段**

```bash
git add core/eval_sampling/store.py api/admin_routes.py tests/test_eval_candidate_contract.py
git commit -m "feat(评测): 增加候选晋升资格"
```

## 任务 3：Preflight API 与 CLI 红灯测试

**文件：**

- 修改：`tests/test_eval_candidate_contract.py`
- 修改：`tests/test_eval_candidates_cli.py`

- [ ] **步骤 1：新增 Admin preflight mixed 测试**

在 `tests/test_eval_candidate_contract.py` 新增：

```python
def test_eval_candidates_preflight_returns_ready_and_blocked_items(
    client,
    db_session,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import label_candidate

    _insert_candidate(db_session, case_id="cand_ready")
    label_candidate(db_session, "cand_ready", {"timing_action": "continue"})
    _insert_candidate(db_session, case_id="cand_error", suite="error")
    label_candidate(db_session, "cand_error", {"timing_action": "continue"})

    response = client.post(
        "/api/v1/admin/evals/candidates/preflight",
        headers=_auth_header(),
        json={
            "case_ids": ["cand_ready", "cand_error", "missing_case"],
            "target_dataset": "timing_gate",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is False
    assert payload["total"] == 3
    assert payload["ready"] == 1
    assert payload["blocked"] == 2
    by_id = {item["case_id"]: item for item in payload["items"]}
    assert by_id["cand_ready"]["readiness"]["ready"] is True
    assert by_id["cand_error"]["readiness"]["blocking_reasons"][0]["code"] == "suite_not_runnable"
    assert by_id["missing_case"]["readiness"]["blocking_reasons"][0]["code"] == "candidate_not_found"
```

- [ ] **步骤 2：新增 CLI dry-run 聚合和 apply 阻断测试**

在 `tests/test_eval_candidates_cli.py` 新增：

```python
def test_promote_labeled_dry_run_reports_ready_and_blocked_without_writing(
    db_session,
    tmp_path,
    monkeypatch,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import get_candidate, label_candidate
    from evals.candidates import promote_labeled

    _insert_candidate(db_session, case_id="cand_ready")
    label_candidate(db_session, "cand_ready", {"timing_action": "continue"})
    _insert_candidate(db_session, case_id="cand_error", suite="error")
    label_candidate(db_session, "cand_error", {"timing_action": "continue"})

    dry_run = promote_labeled(
        db_session,
        target_dataset="timing_gate",
        apply=False,
    )

    assert dry_run["count"] == 2
    assert dry_run["ready"] == 1
    assert dry_run["blocked"] == 1
    by_id = {item["case_id"]: item for item in dry_run["items"]}
    assert by_id["cand_ready"]["ready"] is True
    assert by_id["cand_error"]["ready"] is False
    assert by_id["cand_error"]["error"] == "suite_not_runnable"
    assert not (tmp_path / "evals" / "cases" / "timing_gate" / "cand_ready.json").exists()
    assert get_candidate(db_session, "cand_ready").status == "labeled"


def test_promote_labeled_apply_rejects_blocked_batch_without_partial_write(
    db_session,
    tmp_path,
    monkeypatch,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import get_candidate, label_candidate
    from evals.candidates import promote_labeled

    _insert_candidate(db_session, case_id="cand_ready")
    label_candidate(db_session, "cand_ready", {"timing_action": "continue"})
    _insert_candidate(db_session, case_id="cand_error", suite="error")
    label_candidate(db_session, "cand_error", {"timing_action": "continue"})

    result = promote_labeled(
        db_session,
        target_dataset="timing_gate",
        apply=True,
    )

    assert result["count"] == 2
    assert result["ready"] == 1
    assert result["blocked"] == 1
    assert result["applied"] == 0
    assert result["ok"] is False
    assert not (tmp_path / "evals" / "cases" / "timing_gate" / "cand_ready.json").exists()
    assert get_candidate(db_session, "cand_ready").status == "labeled"
```

- [ ] **步骤 3：运行红灯**

运行：

```bash
python -B -m pytest \
  tests/test_eval_candidate_contract.py::test_eval_candidates_preflight_returns_ready_and_blocked_items \
  tests/test_eval_candidates_cli.py::test_promote_labeled_dry_run_reports_ready_and_blocked_without_writing \
  tests/test_eval_candidates_cli.py::test_promote_labeled_apply_rejects_blocked_batch_without_partial_write \
  -q -p no:cacheprovider
```

预期：失败。失败原因应包括 preflight 接口返回 `404` / `405`，或 CLI 输出仍没有 `ready` / `blocked` 字段。

## 任务 4：实现 Preflight API 与 CLI 聚合 dry-run

**文件：**

- 修改：`core/eval_sampling/store.py`
- 修改：`api/admin_routes.py`
- 修改：`evals/candidates.py`

- [ ] **步骤 1：在 store 中新增 preflight 函数**

在 summary 后新增：

```python
def preflight_candidate_promotions(
    db,
    *,
    case_ids: list[str] | None = None,
    suite: str = "",
    status: str = "labeled",
    source: str = "",
    target_dataset: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 200), 500))
    rows_by_id: dict[str, EvalCandidate] = {}
    ordered_ids: list[str] = []
    if case_ids:
        ordered_ids = [str(case_id) for case_id in case_ids]
        rows = db.query(EvalCandidate).filter(EvalCandidate.case_id.in_(ordered_ids)).all()
        rows_by_id = {row.case_id: row for row in rows}
    else:
        rows = (
            _candidate_query(db, suite=suite, status=status or "labeled", source=source)
            .order_by(EvalCandidate.priority.desc(), EvalCandidate.id.desc())
            .limit(limit)
            .all()
        )
        ordered_ids = [row.case_id for row in rows]
        rows_by_id = {row.case_id: row for row in rows}

    items = []
    ready_count = 0
    blocked_count = 0
    for case_id in ordered_ids[:limit]:
        row = rows_by_id.get(case_id)
        readiness = candidate_readiness(row, target_dataset=target_dataset or (row.suite if row else "regression"))
        if readiness["ready"]:
            ready_count += 1
        else:
            blocked_count += 1
        items.append({
            "case_id": case_id,
            "suite": row.suite if row else "",
            "status": row.status if row else "",
            "target_dataset": readiness["target_dataset"],
            "path": readiness["target_path"],
            "readiness": readiness,
        })

    return {
        "ok": blocked_count == 0,
        "total": len(items),
        "ready": ready_count,
        "blocked": blocked_count,
        "target_dataset": target_dataset,
        "items": items,
    }
```

- [ ] **步骤 2：新增 Admin request model 与路由**

在 `PromoteRequest` 后新增：

```python
class CandidatePreflightRequest(BaseModel):
    case_ids: list[str] = Field(default_factory=list)
    suite: str = ""
    status: str = "labeled"
    source: str = ""
    target_dataset: str = ""
    limit: int = 200
```

在 promote 单条路由前新增：

```python
@router.post("/evals/candidates/preflight")
def eval_preflight_candidates(
    body: CandidatePreflightRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        return preflight_candidate_promotions(
            db,
            case_ids=body.case_ids,
            suite=body.suite,
            status=body.status,
            source=body.source,
            target_dataset=body.target_dataset,
            limit=body.limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
```

同时在 import 列表加入 `preflight_candidate_promotions`。

- [ ] **步骤 3：改造 CLI `promote_labeled()`**

在 `evals/candidates.py` import 中加入 `preflight_candidate_promotions`。

把 dry-run 和 apply 前置预检改为：

```python
preflight = preflight_candidate_promotions(
    db,
    suite=suite,
    status="labeled",
    target_dataset=target_dataset,
    limit=10000,
)
items = []
for item in preflight["items"]:
    readiness = item["readiness"]
    first_reason = readiness["blocking_reasons"][0]["code"] if readiness["blocking_reasons"] else ""
    items.append({
        "case_id": item["case_id"],
        "ready": readiness["ready"],
        "path": item["path"],
        "target_dataset": item["target_dataset"],
        "error": first_reason,
        "readiness": readiness,
    })

result = {
    "ok": preflight["ok"],
    "count": preflight["total"],
    "ready": preflight["ready"],
    "blocked": preflight["blocked"],
    "applied": 0,
    "items": items,
}
if not apply:
    return result
if preflight["blocked"]:
    return result
```

然后只对 ready item 写入：

```python
applied_items = []
for item in preflight["items"]:
    if not item["readiness"]["ready"]:
        continue
    path = promote_candidate(db, item["case_id"], target_dataset=target_dataset)
    applied_items.append({"case_id": item["case_id"], "path": path})
result["applied"] = len(applied_items)
result["items"] = applied_items
result["ok"] = True
return result
```

- [ ] **步骤 4：运行绿灯**

运行任务 3 的同一 pytest 命令。

预期：`3 passed`。

- [ ] **步骤 5：运行相邻回归**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 6：提交 preflight / CLI 阶段**

```bash
git add core/eval_sampling/store.py api/admin_routes.py evals/candidates.py tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py
git commit -m "feat(评测): 支持候选批量预检"
```

## 任务 5：WebUI 红灯静态测试

**文件：**

- 修改：`tests/test_webui_admin_redesign.py`

- [ ] **步骤 1：新增候选 readiness UI 静态守卫**

在 evals 相关测试后新增：

```python
def test_evals_candidates_page_shows_summary_readiness_and_preflight():
    source = _read("webui/src/features/evals/EvalsPage.jsx")

    assert "summary" in source
    assert "readiness" in source
    assert "blocking_reasons" in source
    assert "预检当前页" in source
    assert "/evals/candidates/preflight" in source
    assert "disabled={candidate.status === 'labeled' && !candidate.readiness?.ready}" in source
```

如果 JSX 最终实现不适合完全相同的 `disabled={candidate.status === 'labeled' && !candidate.readiness?.ready}` 字符串，静态测试必须同时断言源码包含 `candidate.readiness?.ready`、`disabled` 和「提升」三个片段。

- [ ] **步骤 2：运行红灯**

运行：

```bash
python -B -m pytest tests/test_webui_admin_redesign.py::test_evals_candidates_page_shows_summary_readiness_and_preflight -q -p no:cacheprovider
```

预期：失败。失败原因是页面尚未引用 summary、preflight 或 readiness 禁用逻辑。

## 任务 6：WebUI summary、资格列和当前页 preflight

**文件：**

- 修改：`webui/src/features/evals/EvalsPage.jsx`
- 修改：`tests/test_webui_admin_redesign.py`

- [ ] **步骤 1：新增 state**

在候选状态区域新增：

```javascript
const [candidateSummary, setCandidateSummary] = useState(null)
const [preflightOpen, setPreflightOpen] = useState(false)
const [preflightResult, setPreflightResult] = useState(null)
const [preflightLoading, setPreflightLoading] = useState(false)
```

- [ ] **步骤 2：让 `loadCandidates()` 保存 summary**

在读取 candidates 后：

```javascript
setCandidates(payload)
setCandidateSummary(payload.summary || null)
```

- [ ] **步骤 3：新增 readiness 文案 helper**

在组件内新增：

```javascript
const readinessReason = (candidate) => {
  const reasons = candidate.readiness?.blocking_reasons || []
  return reasons[0]?.code || ''
}
```

- [ ] **步骤 4：新增当前页 preflight 函数**

```javascript
const preflightCurrentPage = async () => {
  setPreflightLoading(true)
  setPreflightResult(null)
  try {
    const caseIds = candidates.items.map((candidate) => candidate.case_id)
    const result = await api('/evals/candidates/preflight', {
      method: 'POST',
      body: JSON.stringify({
        case_ids: caseIds,
        target_dataset: suiteFilter || '',
      }),
    })
    setPreflightResult(result)
    setPreflightOpen(true)
  } finally {
    setPreflightLoading(false)
  }
}
```

- [ ] **步骤 5：候选页展示 summary 和按钮**

在候选页过滤器下方新增一行 summary：

```jsx
{candidateSummary && (
  <div className="summary-row">
    <span>total: {candidateSummary.total}</span>
    <span>candidate: {candidateSummary.by_status?.candidate || 0}</span>
    <span>labeled: {candidateSummary.by_status?.labeled || 0}</span>
    <span>ready: {candidateSummary.readiness?.ready || 0}</span>
    <span>blocked: {candidateSummary.readiness?.blocked || 0}</span>
    <span>ignored: {candidateSummary.by_status?.ignored || 0}</span>
    <span>promoted: {candidateSummary.by_status?.promoted || 0}</span>
    <button onClick={preflightCurrentPage} disabled={preflightLoading || !candidates.items.length}>
      预检当前页
    </button>
  </div>
)}
```

- [ ] **步骤 6：表格新增资格列并禁用 blocked 提升**

表头新增：

```jsx
<th>资格</th>
```

行内新增：

```jsx
<td>
  <span className={candidate.readiness?.ready ? 'badge success' : 'badge muted'}>
    {candidate.readiness?.ready ? 'ready' : 'blocked'}
  </span>
  {!candidate.readiness?.ready && <small>{readinessReason(candidate)}</small>}
</td>
```

提升按钮改为：

```jsx
<button
  onClick={() => openPromote(candidate)}
  disabled={candidate.status === 'labeled' && !candidate.readiness?.ready}
  title={readinessReason(candidate)}
>
  提升
</button>
```

- [ ] **步骤 7：详情弹窗展示 readiness JSON**

在详情 modal 中新增：

```jsx
<h4>readiness</h4>
<pre>{JSON.stringify(detail.readiness || {}, null, 2)}</pre>
```

- [ ] **步骤 8：新增 preflight 结果弹窗**

在 JSX 末尾新增：

```jsx
{preflightOpen && preflightResult && (
  <div className="modal">
    <h3>预检当前页</h3>
    <p>ready: {preflightResult.ready} / blocked: {preflightResult.blocked}</p>
    <pre>{JSON.stringify(preflightResult.items, null, 2)}</pre>
    <button onClick={() => setPreflightOpen(false)}>关闭</button>
  </div>
)}
```

- [ ] **步骤 9：运行 WebUI 静态绿灯**

运行：

```bash
python -B -m pytest tests/test_webui_admin_redesign.py::test_evals_candidates_page_shows_summary_readiness_and_preflight -q -p no:cacheprovider
```

预期：`1 passed`。

- [ ] **步骤 10：运行 WebUI 相邻静态回归**

运行：

```bash
python -B -m pytest tests/test_webui_admin_redesign.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 11：运行 WebUI build**

运行：

```bash
npm --prefix webui run build
```

预期：退出码 0。允许现有 Vite chunk size / timing warning。

- [ ] **步骤 12：提交 WebUI 阶段**

如果 build 更新 `webui/dist`，按实际文件名显式暂存。

```bash
git add webui/src/features/evals/EvalsPage.jsx tests/test_webui_admin_redesign.py
git commit -m "feat(评测): 展示候选运营预检"
```

## 任务 7：文档收口和最终验证

**文件：**

- 修改：`docs/evals.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/eval-candidate-operations.md`

- [ ] **步骤 1：更新 `docs/evals.md`**

在 Eval candidates 操作说明中补充：

````markdown
### 候选 readiness 与批量预检

`GET /api/v1/admin/evals/candidates` 会返回 `summary` 和每条候选的 `readiness`。
`readiness.ready=true` 表示当前候选可以晋升；blocked 时查看
`readiness.blocking_reasons[].code`。

批量预检使用：

```bash
python -m evals.candidates promote --suite timing_gate --target-dataset timing_gate --dry-run
```

WebUI「Eval 评测」候选页提供「预检当前页」，该操作只读，不写入 case 文件。
批量 apply 当前保持严格语义：只要批次存在 blocked candidate，就不做部分写入。
````

- [ ] **步骤 2：更新 `docs/plan_walkthrough.md`**

在真实样本运营区域新增本阶段。提交号必须使用实际 `git log --oneline` 输出，不保留占位符。推荐文字结构如下：

```markdown
同日真实样本运营第三步已完成：EvalCandidate 运营规则设计提交 `8dc41f5 docs(评测): 设计候选运营规则`，计划提交为实际计划提交号。后端 readiness / summary、preflight / CLI、WebUI 运营预检和文档收口分别记录实际提交号。本阶段让候选列表返回 readiness 与 summary，阻止不可运行 suite 晋升，禁止 PATCH 直接写 `labeled` / `promoted`，并提供只读批量 preflight。
```

写入后检查该段没有尖括号占位。

- [ ] **步骤 3：更新本计划执行记录**

在「执行记录」填入实际红灯、绿灯、相邻回归、WebUI build 和全量测试结果，并把已完成任务勾选为 `[x]`。

- [ ] **步骤 4：运行定向组合验证**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 5：运行 WebUI build**

运行：

```bash
npm --prefix webui run build
```

预期：退出码 0。允许现有 Vite chunk size / timing warning。

- [ ] **步骤 6：运行全量回归**

运行：

```bash
python -B -m pytest tests/ -q -p no:cacheprovider
```

预期：0 failures。

- [ ] **步骤 7：提交文档收口**

```bash
git add docs/evals.md docs/plan_walkthrough.md .Codex/plans/eval-candidate-operations.md
git commit -m "docs(计划): 收口候选运营规则"
```

## 最终验收清单

- [ ] 不可运行 suite 不能晋升为正式 eval case。
- [ ] PATCH 不能直接把候选改为 `labeled` 或 `promoted`。
- [ ] 候选列表 API 保留旧字段，并新增 `summary` 和 `readiness`。
- [ ] preflight 能返回 mixed ready / blocked，不因第一条错误中断。
- [ ] CLI dry-run 可作为批量预检使用，apply 遇 blocked 不做部分写入。
- [ ] WebUI 列表和详情能解释阻断原因。
- [ ] 定向测试、相邻回归、WebUI build 和全量测试都有新鲜通过证据。
