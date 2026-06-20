# EvalCandidate 运营趋势报表实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 增加只读 EvalCandidate 运营趋势报表，按候选创建日期展示当前状态、readiness 和阻断原因趋势。

**架构：** 在 `core/eval_sampling/store.py` 增加只读聚合函数，Admin API、CLI 和 WebUI 共用同一响应结构。第一版只按 `EvalCandidate.created_at` 做日粒度分桶，展示当前状态快照，不回放历史状态迁移。

**技术栈：** Python、SQLAlchemy、FastAPI、pytest、React、Vite。

---

## 文件结构

- 修改：`core/eval_sampling/store.py`
  - 新增 `candidate_trend_report()`。
  - 复用 `_candidate_query()`、`candidate_readiness()` 和 `_count_values()`。
- 修改：`api/admin_routes.py`
  - 新增 `GET /api/v1/admin/evals/candidates/trend`。
  - 路由放在 `/evals/candidates/{case_id}` 之前。
- 修改：`evals/candidates.py`
  - 新增 `trend` CLI 子命令。
- 修改：`webui/src/features/evals/EvalsPage.jsx`
  - 新增「趋势报表」tab、days 输入、刷新按钮、summary 和 bucket 表格。
- 修改：`tests/test_eval_candidate_contract.py`
  - 覆盖 store / API 只读趋势契约。
- 修改：`tests/test_eval_candidates_cli.py`
  - 覆盖 CLI `trend` 导出。
- 修改：`tests/test_webui_admin_redesign.py`
  - 覆盖 WebUI 静态入口与禁止批量操作。
- 修改：`docs/evals.md`
  - 新增「真实样本趋势报表」说明。
- 修改：`docs/todo.md`
  - 标记真实样本运营 6 完成后同步路线项 8。
- 修改：`docs/plan_walkthrough.md`
  - 记录设计、计划、实现提交和验证结果。

## 任务 1：后端趋势聚合与 Admin API

**文件：**
- 修改：`tests/test_eval_candidate_contract.py`
- 修改：`core/eval_sampling/store.py`
- 修改：`api/admin_routes.py`

- [ ] **步骤 1：编写失败的 store / API 测试**

在 `tests/test_eval_candidate_contract.py` 增加测试：

```python
def test_candidate_trend_report_groups_current_snapshot_by_created_date(db_session):
    from datetime import datetime, timedelta

    from core.database import EvalCandidate
    from core.eval_sampling.store import candidate_trend_report

    today = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    old_day = today - timedelta(days=1)
    rows = [
        EvalCandidate(
            case_id="cand_trend_blocked",
            suite="timing_gate",
            source="db",
            status="candidate",
            expected_json='{"needs_label": true}',
            created_at=old_day,
            updated_at=old_day,
        ),
        EvalCandidate(
            case_id="cand_trend_ready",
            suite="timing_gate",
            source="db",
            status="labeled",
            expected_json='{"timing_action": "continue"}',
            created_at=today,
            updated_at=today,
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()

    report = candidate_trend_report(
        db_session,
        days=2,
        suite="timing_gate",
        source="db",
        target_dataset="trend_target",
    )

    assert report["ok"] is True
    assert report["summary"]["total"] == 2
    assert report["summary"]["readiness"] == {"ready": 1, "blocked": 1}
    assert len(report["buckets"]) == 2
    assert report["buckets"][-1]["by_status"]["labeled"] == 1
    assert report["buckets"][0]["top_blocking_reasons"][0]["code"] == "invalid_status"
```

新增 API 测试：

```python
def test_candidates_trend_api_is_read_only(client, db_session, admin_headers):
    from datetime import datetime

    from core.database import AdminAuditLog, EvalCandidate

    db_session.add(EvalCandidate(
        case_id="cand_trend_api",
        suite="timing_gate",
        source="api",
        status="candidate",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    ))
    db_session.commit()

    response = client.get(
        "/api/v1/admin/evals/candidates/trend",
        params={"days": 30, "suite": "timing_gate", "source": "api"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["summary"]["total"] == 1
    assert db_session.query(AdminAuditLog).count() == 0
    assert db_session.query(EvalCandidate).filter_by(case_id="cand_trend_api").one().status == "candidate"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py::test_candidate_trend_report_groups_current_snapshot_by_created_date tests/test_eval_candidate_contract.py::test_candidates_trend_api_is_read_only -q -p no:cacheprovider
```

预期：失败，错误包含 `ImportError` 或 `405 Method Not Allowed`，因为函数和路由尚未实现。

- [ ] **步骤 3：实现最小后端代码**

在 `core/eval_sampling/store.py` 增加：

```python
def _candidate_trend_bucket_key(value: Any) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value or "")[:10]
```

增加：

```python
def candidate_trend_report(
    db,
    *,
    days: int = 30,
    suite: str = "",
    status: str = "",
    source: str = "",
    target_dataset: str = "",
) -> dict[str, Any]:
    capped_days = max(1, min(int(days or 30), 90))
    today = datetime.now().date()
    start_date = today - timedelta(days=capped_days - 1)
    rows = (
        _candidate_query(db, suite=suite, status=status, source=source)
        .filter(EvalCandidate.created_at >= datetime.combine(start_date, datetime.min.time()))
        .order_by(EvalCandidate.created_at.asc(), EvalCandidate.id.asc())
        .all()
    )

    buckets: dict[str, dict[str, Any]] = {}
    summary_statuses: list[str] = []
    summary_suites: list[str] = []
    summary_sources: list[str] = []
    summary_readiness = {"ready": 0, "blocked": 0}
    summary_blocking: list[str] = []

    for row in rows:
        key = _candidate_trend_bucket_key(row.created_at)
        bucket = buckets.setdefault(key, {
            "date": key,
            "created": 0,
            "by_status": {},
            "by_suite": {},
            "by_source": {},
            "readiness": {"ready": 0, "blocked": 0},
            "_blocking_reasons": [],
        })
        readiness = candidate_readiness(row, target_dataset=target_dataset or row.suite)
        bucket["created"] += 1
        bucket["by_status"][row.status] = bucket["by_status"].get(row.status, 0) + 1
        bucket["by_suite"][row.suite] = bucket["by_suite"].get(row.suite, 0) + 1
        bucket["by_source"][row.source] = bucket["by_source"].get(row.source, 0) + 1
        readiness_key = "ready" if readiness["ready"] else "blocked"
        bucket["readiness"][readiness_key] += 1
        summary_readiness[readiness_key] += 1

        summary_statuses.append(row.status)
        summary_suites.append(row.suite)
        summary_sources.append(row.source)
        for reason in readiness.get("blocking_reasons", []):
            code = str(reason.get("code") or "unknown")
            bucket["_blocking_reasons"].append(code)
            summary_blocking.append(code)

    bucket_list = []
    for item in buckets.values():
        blocking = item.pop("_blocking_reasons")
        item["top_blocking_reasons"] = [
            {"code": code, "count": count}
            for code, count in sorted(_count_values(blocking).items(), key=lambda pair: (-pair[1], pair[0]))
        ]
        bucket_list.append(item)

    return {
        "ok": True,
        "filters": {
            "days": capped_days,
            "bucket": "day",
            "suite": suite,
            "status": status,
            "source": source,
            "target_dataset": target_dataset,
        },
        "summary": {
            "total": len(rows),
            "by_status": _count_values(summary_statuses),
            "by_suite": _count_values(summary_suites),
            "by_source": _count_values(summary_sources),
            "readiness": summary_readiness,
            "top_blocking_reasons": [
                {"code": code, "count": count}
                for code, count in sorted(_count_values(summary_blocking).items(), key=lambda pair: (-pair[1], pair[0]))
            ],
        },
        "buckets": bucket_list,
    }
```

同时补充 `from datetime import date, datetime, timedelta` 或等效 import。

在 `api/admin_routes.py` 的动态 candidate 路由之前新增：

```python
@router.get("/evals/candidates/trend", dependencies=[Depends(_admin_required)])
def eval_candidates_trend(
    days: int = 30,
    suite: str = "",
    status: str = "",
    source: str = "",
    target_dataset: str = "",
    db: Session = Depends(get_db),
):
    try:
        return candidate_trend_report(
            db,
            days=days,
            suite=suite,
            status=status,
            source=source,
            target_dataset=target_dataset,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py::test_candidate_trend_report_groups_current_snapshot_by_created_date tests/test_eval_candidate_contract.py::test_candidates_trend_api_is_read_only -q -p no:cacheprovider
```

预期：`2 passed`。

- [ ] **步骤 5：运行后端相邻回归**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 6：Commit**

```bash
git add core/eval_sampling/store.py api/admin_routes.py tests/test_eval_candidate_contract.py
git commit -m "feat(评测): 增加候选趋势接口"
```

## 任务 2：CLI 趋势导出

**文件：**
- 修改：`tests/test_eval_candidates_cli.py`
- 修改：`evals/candidates.py`

- [ ] **步骤 1：编写失败的 CLI 测试**

在 `tests/test_eval_candidates_cli.py` 增加：

```python
def test_candidates_cli_trend_writes_read_only_report(tmp_path, db_session, monkeypatch, capsys):
    from datetime import datetime

    from core.database import AdminAuditLog, EvalCandidate
    from evals import candidates

    out = tmp_path / "trend.json"
    db_session.add(EvalCandidate(
        case_id="cand_cli_trend",
        suite="timing_gate",
        source="cli",
        status="candidate",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    ))
    db_session.commit()

    monkeypatch.setattr(candidates, "SessionLocal", lambda: _SessionWrapper(db_session))
    rc = candidates.main([
        "trend",
        "--days", "30",
        "--suite", "timing_gate",
        "--source", "cli",
        "--out", str(out),
    ])

    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["summary"]["total"] == 1
    assert "ready=" in capsys.readouterr().out
    assert db_session.query(AdminAuditLog).count() == 0
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_eval_candidates_cli.py::test_candidates_cli_trend_writes_read_only_report -q -p no:cacheprovider
```

预期：失败，提示 `invalid choice: 'trend'`。

- [ ] **步骤 3：实现 CLI 子命令**

在 `evals/candidates.py` 中 import `candidate_trend_report`，新增 `trend_candidates(args)`：

```python
def trend_candidates(args) -> int:
    db = SessionLocal()
    try:
        report = candidate_trend_report(
            db,
            days=args.days,
            suite=args.suite,
            status=args.status,
            source=args.source,
            target_dataset=args.target_dataset,
        )
    finally:
        db.close()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    readiness = report["summary"]["readiness"]
    print(
        "trend: "
        f"total={report['summary']['total']} "
        f"ready={readiness.get('ready', 0)} "
        f"blocked={readiness.get('blocked', 0)}"
    )
    return 0
```

在 argparse 中新增：

```python
trend = sub.add_parser("trend", help="Export read-only candidate trend report")
trend.add_argument("--days", type=int, default=30)
trend.add_argument("--suite", default="")
trend.add_argument("--status", default="")
trend.add_argument("--source", default="")
trend.add_argument("--target-dataset", default="")
trend.add_argument("--out", default="")
trend.set_defaults(func=trend_candidates)
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
python -B -m pytest tests/test_eval_candidates_cli.py::test_candidates_cli_trend_writes_read_only_report -q -p no:cacheprovider
```

预期：`1 passed`。

- [ ] **步骤 5：运行 CLI 相邻回归**

运行：

```bash
python -B -m pytest tests/test_eval_candidates_cli.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 6：Commit**

```bash
git add evals/candidates.py tests/test_eval_candidates_cli.py
git commit -m "feat(评测): 增加候选趋势导出"
```

## 任务 3：WebUI 趋势报表入口

**文件：**
- 修改：`tests/test_webui_admin_redesign.py`
- 修改：`webui/src/features/evals/EvalsPage.jsx`
- 修改：`webui/dist/index.html`
- 删除 / 新增：`webui/dist/assets/index-*.js`
- 删除 / 新增：`webui/dist/assets/index-*.css`

- [ ] **步骤 1：编写失败的 WebUI 静态测试**

在 `tests/test_webui_admin_redesign.py` 增加：

```python
def test_evals_page_exposes_candidate_trend_report():
    src = Path("webui/src/features/evals/EvalsPage.jsx").read_text(encoding="utf-8")
    assert "趋势报表" in src
    assert "/evals/candidates/trend" in src
    assert "candidateTrend" in src
    assert "top_blocking_reasons" in src
    assert "by_status" in src
    assert "批量拒绝" not in src
    assert "批量暂缓" not in src
    assert "批量应用" not in src
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_webui_admin_redesign.py::test_evals_page_exposes_candidate_trend_report -q -p no:cacheprovider
```

预期：失败，缺少「趋势报表」。

- [ ] **步骤 3：实现 WebUI 最小入口**

在 `EvalsPage.jsx` 中：

- 新增 `candidateTrend`、`trendDays` 和 `trendLoading` state。
- 新增 `loadCandidateTrend()`，调用：

```javascript
api.get('/evals/candidates/trend', {
  params: {
    days: trendDays,
    suite: suiteFilter || undefined,
    status: statusFilter || undefined,
    source: sourceFilter || undefined,
    target_dataset: suiteFilter || undefined,
  },
})
```

- tab 增加「趋势报表」。
- 趋势 tab 展示 `MiniStat`、days 输入、刷新按钮、bucket 表格和 `JsonBlock`。
- 不添加批量状态变更按钮。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
python -B -m pytest tests/test_webui_admin_redesign.py::test_evals_page_exposes_candidate_trend_report -q -p no:cacheprovider
```

预期：`1 passed`。

- [ ] **步骤 5：运行 WebUI 静态回归和构建**

运行：

```bash
python -B -m pytest tests/test_webui_admin_redesign.py -q -p no:cacheprovider
npm --prefix webui run build
```

预期：测试全部通过；build 退出码为 0。若 Vite 输出现有 chunk size warning，可记录为既有 warning。

- [ ] **步骤 6：Commit**

```bash
git add webui/src/features/evals/EvalsPage.jsx tests/test_webui_admin_redesign.py webui/dist/index.html
git status --short webui/dist/assets
```

根据 `git status --short webui/dist/assets` 的实际输出，逐个显式暂存新增和删除的 hash 资源文件。禁止使用 `git add .` 或 `git add -A`。

```bash
git commit -m "feat(评测): 展示候选趋势报表"
```

## 任务 4：文档收口与最终验证

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/eval-operations-trend-report.md`

- [ ] **步骤 1：更新用户文档**

在 `docs/evals.md` 的“候选批次审计”之后新增“真实样本趋势报表”：

````markdown
### 真实样本趋势报表

通用 `EvalCandidate` 支持只读运营趋势报表：

```http
GET /api/v1/admin/evals/candidates/trend?days=30&suite=timing_gate
```

CLI：

```bash
python -m evals.candidates trend --days 30 --suite timing_gate --out /tmp/candidate-trend.json
```

该报表按 `EvalCandidate.created_at` 分桶，但桶内 `status` 和 `readiness` 都是当前快照，不代表历史状态迁移。
````

- [ ] **步骤 2：同步路线文档**

在 `docs/todo.md` 和 `docs/plan_walkthrough.md` 中新增真实样本运营 6：

```markdown
| 真实样本运营 6 | 已完成 | EvalCandidate 运营趋势报表 | 按创建日期分桶展示当前候选状态、readiness 和阻断原因，只读不调参 | 写入本阶段实际提交哈希 |
```

- [ ] **步骤 3：勾选本计划完成项并写入验证记录**

在本计划底部新增验证记录：

```markdown
## 验证记录

- 后端红灯：记录命令、失败数量和首个失败原因。
- 后端绿灯：记录命令、通过数量和耗时。
- CLI 红灯：记录命令、失败数量和首个失败原因。
- CLI 绿灯：记录命令、通过数量和耗时。
- WebUI 红灯：记录命令、失败数量和首个失败原因。
- WebUI 绿灯：记录命令、通过数量和耗时。
- 最终组合回归：记录命令、通过数量和耗时。
- 全量回归：记录命令、通过 / skipped / warning 数量和耗时。
```

- [ ] **步骤 4：运行最终组合回归**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 5：运行全量回归**

运行：

```bash
python -B -m pytest tests/ -q -p no:cacheprovider
```

预期：全部通过。若存在 skip / warning，记录实际数量。

- [ ] **步骤 6：Commit**

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/eval-operations-trend-report.md
git commit -m "docs(计划): 收口候选趋势报表"
```
