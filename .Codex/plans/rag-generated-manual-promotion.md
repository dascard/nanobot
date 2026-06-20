# RAG 样本仲裁入口实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:test-driven-development 执行实现步骤，并在提交前使用 superpowers:verification-before-completion 和 superpowers:chinese-commit-conventions。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 RAG benchmark 增加单条 generated case 提升为 manual case 的 dry-run/apply 仲裁入口，并在 WebUI 中提供人工确认流程。

**架构：** 后端在现有 Admin RAG Benchmark 路由中新增 `promote-manual` API，复用 case id 校验、manual 路径和 backup 目录，dry-run 只返回将要写入的 case。WebUI 在现有 `CaseEditor` 中增加 generated case 的二阶段提升 UI，不改 sampler、runner、scoring、fixture、baseline 或 gate 脚本。

**技术栈：** FastAPI、Pydantic、pytest、React、Vite、现有 Admin audit、RAG benchmark schema。

---

## 执行记录

- 设计提交：`e0d537d docs(评测): 设计 RAG 样本仲裁入口`。
- 计划提交：待本计划提交后补记。
- 后端实现提交：待实现后补记。
- WebUI / 文档实现提交：待实现后补记。
- 收口提交：待最终验证后补记。

## 文件结构

- 修改：`api/admin/rag_benchmark_routes.py`
  - 新增 `BenchmarkCasePromoteRequest`。
  - 新增 generated case 查找、stale 校验、manual case 转换和 `POST /cases/{case_id}/promote-manual`。
  - apply 时写入 manual JSON，覆盖时备份旧文件，并记录 `promote_rag_benchmark_generated_case` audit。
- 修改：`tests/test_rag_benchmark_admin.py`
  - 覆盖 dry-run、apply、stale、目标冲突、manual 源拒绝和 unsafe target id。
- 修改：`webui/src/features/rag/RagBenchmarkPage.jsx`
  - 在 generated case 详情弹窗中加入「提升为 Manual」入口。
  - dry-run 展示目标 id、目标 path 和 case JSON，确认后 apply。
  - manual 目录不可写或 case stale 时禁用入口并显示原因。
- 修改：`tests/test_rag_benchmark_webui.py`
  - 用静态守卫固定入口文案、API 路径、`dry_run`、`target_case_id`、stale/manual dir writable 禁用逻辑。
- 修改：`docs/evals.md`
  - 说明 generated → manual promote 边界：不提交 `tmp/`，不自动更新 baseline，stale 需刷新。
- 修改：`.Codex/plans/rag-generated-manual-promotion.md`
  - 执行中勾选任务，记录红灯、绿灯、回归和提交号。
- 修改：`docs/plan_walkthrough.md`
  - 最终收口时同步状态、验证结果和下一阶段。
- 可能修改：`webui/dist/index.html`、`webui/dist/assets/index-*.js`
  - 仅在 `npm --prefix webui run build` 生成新 bundle 时按实际文件名暂存。

## 子 agent 分配决策

可并行但必须先固定接口契约：

- 后端实现 agent：只修改 `api/admin/rag_benchmark_routes.py` 和 `tests/test_rag_benchmark_admin.py`，不得修改 WebUI、baseline、sampler、runner、scoring、fixture 或 gate 脚本。
- WebUI 实现 agent：只修改 `webui/src/features/rag/RagBenchmarkPage.jsx` 和 `tests/test_rag_benchmark_webui.py`，必须遵守后端接口：`POST /rag/benchmark/cases/{case_id}/promote-manual`，body 使用 `target_case_id`、`note`、`dry_run`、`overwrite`。
- 文档 agent：只修改 `docs/evals.md`，不得把 `tmp/rag_benchmark/generated/*` 或 baseline 更新写入本阶段。

主线程职责：

- 合并接口命名，审查子 agent 输出。
- 运行定向测试、WebUI build、相邻回归和全量测试。
- 按阶段单独 commit，且只按文件显式暂存。

如果当前会话不实际启用子 agent，则按下方任务顺序内联执行；不要跳过 TDD 红灯。

## 任务 1：后端红灯测试

**文件：**

- 修改：`tests/test_rag_benchmark_admin.py`

- [ ] **步骤 1：新增 dry-run 与 apply 测试**

在 `test_benchmark_sample_marks_fingerprint_and_run_skips_stale_generated_without_overwriting_latest` 后面新增测试。测试先通过 `/sample` 生成 generated case，再调用 promote API。

```python
def test_generated_case_promote_manual_dry_run_and_apply(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    _engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    _routes, manual, _generated, _reports, _backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)

    sampled = client.post(
        "/api/v1/admin/rag/benchmark/sample",
        headers=_auth_header(),
        json={"per_source": 1},
    )
    assert sampled.status_code == 200
    items = client.get(
        "/api/v1/admin/rag/benchmark/cases",
        headers=_auth_header(),
        params={"origin": "generated"},
    ).json()["items"]
    source_id = items[0]["id"]

    dry_run = client.post(
        f"/api/v1/admin/rag/benchmark/cases/{source_id}/promote-manual",
        headers=_auth_header(),
        json={"target_case_id": "memory_manual_promoted_001", "dry_run": True, "note": "人工确认"},
    )

    assert dry_run.status_code == 200
    dry_payload = dry_run.json()
    assert dry_payload["ok"] is True
    assert dry_payload["dry_run"] is True
    assert dry_payload["source_case_id"] == source_id
    assert dry_payload["target_case_id"] == "memory_manual_promoted_001"
    assert dry_payload["baseline_update_required"] is True
    assert dry_payload["case"]["id"] == "memory_manual_promoted_001"
    assert dry_payload["case"]["meta"]["origin"] == "manual"
    assert dry_payload["case"]["meta"]["promoted_from_case_id"] == source_id
    assert dry_payload["case"]["meta"]["review_note"] == "人工确认"
    assert not (manual / "memory_manual_promoted_001.json").exists()
    assert db_session.query(AdminAuditLog).count() == 0

    applied = client.post(
        f"/api/v1/admin/rag/benchmark/cases/{source_id}/promote-manual",
        headers=_auth_header(),
        json={"target_case_id": "memory_manual_promoted_001", "dry_run": False, "note": "人工确认"},
    )

    assert applied.status_code == 200
    assert (manual / "memory_manual_promoted_001.json").exists()
    persisted = json.loads((manual / "memory_manual_promoted_001.json").read_text(encoding="utf-8"))
    assert persisted["id"] == "memory_manual_promoted_001"
    assert persisted["query"] == dry_payload["case"]["query"]
    assert persisted["expected"] == dry_payload["case"]["expected"]
    assert persisted["meta"]["origin"] == "manual"
    assert persisted["meta"]["promoted_from_case_id"] == source_id
    assert persisted["meta"]["promoted_from_origin"].startswith("generated")
    assert persisted["meta"]["review_note"] == "人工确认"
    assert persisted["meta"]["db_fingerprint"]
    actions = [row.action for row in db_session.query(AdminAuditLog).order_by(AdminAuditLog.id).all()]
    assert actions == ["promote_rag_benchmark_generated_case"]
```

- [ ] **步骤 2：新增 stale、冲突、manual 源和 unsafe id 测试**

在同一区域继续追加：

```python
def test_generated_case_promote_manual_rejects_stale_existing_manual_source_and_unsafe_target(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    _routes, manual, _generated, _reports, _backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)

    sampled = client.post(
        "/api/v1/admin/rag/benchmark/sample",
        headers=_auth_header(),
        json={"per_source": 1},
    )
    assert sampled.status_code == 200
    source_id = client.get(
        "/api/v1/admin/rag/benchmark/cases",
        headers=_auth_header(),
        params={"origin": "generated"},
    ).json()["items"][0]["id"]

    unsafe = client.post(
        f"/api/v1/admin/rag/benchmark/cases/{source_id}/promote-manual",
        headers=_auth_header(),
        json={"target_case_id": ".hidden", "dry_run": True},
    )
    assert unsafe.status_code == 400

    (manual / "memory_manual_existing.json").write_text(json.dumps({
        "id": "memory_manual_existing",
        "suite": "rag_benchmark",
        "source_type": "memory",
        "case_type": "positive",
        "query": "existing",
        "expected": {"candidate_ids": ["memory_digest:42:digest:level2"]},
        "meta": {"origin": "manual"},
    }, ensure_ascii=False), encoding="utf-8")
    exists = client.post(
        f"/api/v1/admin/rag/benchmark/cases/{source_id}/promote-manual",
        headers=_auth_header(),
        json={"target_case_id": "memory_manual_existing", "dry_run": True},
    )
    assert exists.status_code == 409

    manual_source = client.post(
        "/api/v1/admin/rag/benchmark/cases/memory_manual_existing/promote-manual",
        headers=_auth_header(),
        json={"target_case_id": "memory_manual_copy", "dry_run": True},
    )
    assert manual_source.status_code == 409

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO semantic_index_items(id, source_type, source_id, source_sub_id, status, visibility, title, text) "
            "VALUES (999, 'memory_digest', '999', 'digest:level2', 'active', 'recall', 'new', 'new')"
        ))
    stale = client.post(
        f"/api/v1/admin/rag/benchmark/cases/{source_id}/promote-manual",
        headers=_auth_header(),
        json={"target_case_id": "memory_manual_after_stale", "dry_run": True},
    )
    assert stale.status_code == 409
    assert "stale" in stale.json()["detail"].lower()
```

- [ ] **步骤 3：新增 overwrite 备份测试**

追加覆盖 apply 覆盖已有 manual case 的备份语义：

```python
def test_generated_case_promote_manual_overwrite_backs_up_existing_case(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    _engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    _routes, manual, _generated, _reports, backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)

    client.post("/api/v1/admin/rag/benchmark/sample", headers=_auth_header(), json={"per_source": 1})
    source_id = client.get(
        "/api/v1/admin/rag/benchmark/cases",
        headers=_auth_header(),
        params={"origin": "generated"},
    ).json()["items"][0]["id"]
    (manual / "memory_manual_replace.json").write_text(json.dumps({
        "id": "memory_manual_replace",
        "suite": "rag_benchmark",
        "source_type": "memory",
        "case_type": "positive",
        "query": "old",
        "expected": {"candidate_ids": ["memory_digest:42:digest:level2"]},
        "meta": {"origin": "manual"},
    }, ensure_ascii=False), encoding="utf-8")

    response = client.post(
        f"/api/v1/admin/rag/benchmark/cases/{source_id}/promote-manual",
        headers=_auth_header(),
        json={"target_case_id": "memory_manual_replace", "dry_run": False, "overwrite": True},
    )

    assert response.status_code == 200
    assert list(backups.glob("memory_manual_replace.*.json"))
    persisted = json.loads((manual / "memory_manual_replace.json").read_text(encoding="utf-8"))
    assert persisted["query"] != "old"
```

- [ ] **步骤 4：运行红灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark_admin.py::test_generated_case_promote_manual_dry_run_and_apply tests/test_rag_benchmark_admin.py::test_generated_case_promote_manual_rejects_stale_existing_manual_source_and_unsafe_target tests/test_rag_benchmark_admin.py::test_generated_case_promote_manual_overwrite_backs_up_existing_case -q -p no:cacheprovider
```

预期：失败于 `404 Not Found` 或路由不存在。若失败于测试拼写或 fixture 错误，先修测试直到它们因功能缺失失败。

## 任务 2：后端最小实现

**文件：**

- 修改：`api/admin/rag_benchmark_routes.py`
- 测试：`tests/test_rag_benchmark_admin.py`

- [ ] **步骤 1：新增请求模型**

在 `BenchmarkCaseSaveRequest` 后新增：

```python
class BenchmarkCasePromoteRequest(BaseModel):
    target_case_id: str = ""
    dry_run: bool = True
    note: str = ""
    overwrite: bool = False
```

- [ ] **步骤 2：新增查找和转换 helper**

在 `_load_cases_with_origin()` 后新增：

```python
def _find_case_with_origin(case_id: str) -> tuple[BenchmarkCase, str, bool]:
    case_id = _case_id_or_400(case_id)
    for case, origin, editable in _load_cases_with_origin():
        if case.id == case_id:
            return case, origin, editable
    raise HTTPException(404, "benchmark case not found")


def _current_db_fingerprint() -> dict[str, Any]:
    db_path = get_benchmark_db_path()
    if not db_path or not db_path.exists():
        return {}
    return db_fingerprint(db_path)


def _manual_case_from_generated(
    source: BenchmarkCase,
    *,
    target_case_id: str,
    note: str,
    promoted_at: str,
) -> BenchmarkCase:
    meta = {
        **source.meta,
        "origin": "manual",
        "promoted_from_case_id": source.id,
        "promoted_from_origin": source.meta.get("origin") or "generated",
        "promoted_at": promoted_at,
        "review_note": note,
    }
    return source.model_copy(update={"id": target_case_id, "meta": meta}, deep=True)
```

如果本地 Pydantic 版本不支持 `model_copy(..., deep=True)` 的现有行为，使用已在项目内兼容的写法，不引入新依赖。

- [ ] **步骤 3：新增 promote route**

在 `get_benchmark_case()` 和 `save_benchmark_case()` 之间增加 route，确保 `/cases/{case_id}/promote-manual` 在路径上明确。

```python
@router.post("/cases/{case_id}/promote-manual")
def promote_generated_case_to_manual(
    case_id: str,
    body: BenchmarkCasePromoteRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    source_case, origin, _editable = _find_case_with_origin(case_id)
    if origin != "generated":
        raise HTTPException(409, "only generated benchmark cases can be promoted")

    current_fp = _current_db_fingerprint()
    if not _same_fingerprint(source_case.meta.get("db_fingerprint"), current_fp):
        raise HTTPException(409, "generated benchmark case is stale; refresh generated cases before promoting")

    target_case_id = _case_id_or_400(body.target_case_id or source_case.id)
    path = _manual_case_path(target_case_id)
    existed = path.exists()
    if existed and not body.overwrite:
        raise HTTPException(409, "manual benchmark case already exists")

    promoted_at = datetime.now().isoformat(timespec="seconds")
    case = _manual_case_from_generated(
        source_case,
        target_case_id=target_case_id,
        note=body.note,
        promoted_at=promoted_at,
    )
    payload = {
        "ok": True,
        "dry_run": body.dry_run,
        "source_case_id": source_case.id,
        "target_case_id": target_case_id,
        "path": _safe_rel_path(path),
        "baseline_update_required": True,
        "case": case.model_dump(),
    }
    if body.dry_run:
        return payload

    path.parent.mkdir(parents=True, exist_ok=True)
    if existed:
        BENCHMARK_CASE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = BENCHMARK_CASE_BACKUP_DIR / f"{target_case_id}.{_now_id()}.json"
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(case.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    audit_request(db, request, "promote_rag_benchmark_generated_case", "rag_benchmark_case", target_case_id, {
        "path": _safe_rel_path(path),
        "source_case_id": source_case.id,
        "overwrite": bool(existed),
    })
    return payload
```

- [ ] **步骤 4：运行后端绿灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark_admin.py::test_generated_case_promote_manual_dry_run_and_apply tests/test_rag_benchmark_admin.py::test_generated_case_promote_manual_rejects_stale_existing_manual_source_and_unsafe_target tests/test_rag_benchmark_admin.py::test_generated_case_promote_manual_overwrite_backs_up_existing_case -q -p no:cacheprovider
```

预期：3 个测试通过。

- [ ] **步骤 5：运行后端相邻回归**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark_admin.py -q -p no:cacheprovider
```

预期：该文件全部通过。

## 任务 3：WebUI 红灯测试

**文件：**

- 修改：`tests/test_rag_benchmark_webui.py`

- [ ] **步骤 1：新增静态守卫**

在 `test_rag_benchmark_page_exposes_provider_modes_and_case_controls()` 后新增：

```python
def test_rag_benchmark_page_exposes_generated_promote_manual_flow():
    source = PAGE.read_text(encoding="utf-8")

    assert "提升为 Manual" in source
    assert "promote-manual" in source
    assert "dry_run" in source
    assert "target_case_id" in source
    assert "overwrite" in source
    assert "promotePlan" in source
    assert "promoteTargetId" in source
    assert "promoteNote" in source
    assert "baseline_update_required" in source
    assert "generated case 已 stale，请重新刷新 generated 后再提升。" in source
    assert "manual case 目录不可写，无法提升 generated case。" in source
```

- [ ] **步骤 2：运行 WebUI 红灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark_webui.py::test_rag_benchmark_page_exposes_generated_promote_manual_flow -q -p no:cacheprovider
```

预期：失败于缺少「提升为 Manual」或 `promote-manual`。

## 任务 4：WebUI 实现

**文件：**

- 修改：`webui/src/features/rag/RagBenchmarkPage.jsx`
- 测试：`tests/test_rag_benchmark_webui.py`

- [ ] **步骤 1：在 `CaseEditor` 中新增 promote 状态**

在 `saving` state 后新增：

```javascript
  const [promoting, setPromoting] = useState(false)
  const [promotePlan, setPromotePlan] = useState(null)
  const [promoteTargetId, setPromoteTargetId] = useState(() => initialCase.id || '')
  const [promoteNote, setPromoteNote] = useState('')
```

在 `manualWritable` 和 `canEdit` 附近新增：

```javascript
  const isGenerated = !editable
  const isStale = item?.stale === true
  const canPromote = isGenerated && manualWritable && !isStale
  const promoteDisabledReason = !manualWritable
    ? 'manual case 目录不可写，无法提升 generated case。'
    : isStale
      ? 'generated case 已 stale，请重新刷新 generated 后再提升。'
      : ''
```

- [ ] **步骤 2：新增 dry-run/apply 方法**

在 `remove` 函数后新增：

```javascript
  const requestPromote = (dryRun) => {
    if (!form.id) return
    if (!promoteTargetId.trim()) {
      alert('target_case_id 不能为空')
      return
    }
    setPromoting(true)
    api.post(`/rag/benchmark/cases/${encodeURIComponent(form.id)}/promote-manual`, {
      target_case_id: promoteTargetId.trim(),
      note: promoteNote,
      dry_run: dryRun,
      overwrite: false,
    })
      .then(r => {
        if (dryRun) {
          setPromotePlan(r.data)
        } else {
          onSaved()
          onClose()
        }
      })
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setPromoting(false))
  }
```

- [ ] **步骤 3：新增 generated 提升 UI**

在 `高级 JSON` details 后、manual writable 提示前插入：

```jsx
        {isGenerated && (
          <div className="rounded-lg border border-slate-800 bg-slate-950 p-3">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-xs font-medium text-slate-200">提升为 Manual</div>
                <div className="mt-1 text-[11px] text-slate-500">先 dry-run 预检目标文件，再确认写入 manual case。</div>
              </div>
              <ActionButton onClick={() => requestPromote(true)} disabled={!canPromote || promoting} tone="emerald">
                提升为 Manual
              </ActionButton>
            </div>
            {promoteDisabledReason && <div className="mb-3 text-xs text-amber-300">{promoteDisabledReason}</div>}
            <div className="grid gap-3 md:grid-cols-2">
              <Field id="benchmark-promote-target-case-id" label="target_case_id">
                <input id="benchmark-promote-target-case-id" value={promoteTargetId}
                  onChange={e => setPromoteTargetId(e.target.value)}
                  disabled={!manualWritable}
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-200" />
              </Field>
              <Field id="benchmark-promote-note" label="review note">
                <input id="benchmark-promote-note" value={promoteNote}
                  onChange={e => setPromoteNote(e.target.value)}
                  disabled={!manualWritable}
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200" />
              </Field>
            </div>
            {promotePlan && (
              <div className="mt-3 rounded border border-emerald-500/30 bg-emerald-500/5 p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-300">
                  <span>target: <span className="font-mono">{promotePlan.target_case_id}</span></span>
                  <span>path: <span className="font-mono">{promotePlan.path}</span></span>
                  {promotePlan.baseline_update_required && <Badge tone="amber">baseline update required</Badge>}
                </div>
                <JsonBlock value={promotePlan.case} className="max-h-64" />
                <div className="mt-3 flex justify-end">
                  <ActionButton onClick={() => requestPromote(false)} disabled={promoting} tone="emerald">
                    确认写入 Manual
                  </ActionButton>
                </div>
              </div>
            )}
          </div>
        )}
```

如果 JSX 中 `ActionButton` 的 `tone="emerald"` 样式已存在则复用；若不存在，沿用当前保存按钮风格。

- [ ] **步骤 4：确保 `openCase` 保留 stale 字段**

确认 `openCase(item)` 的 `setEditing` 把列表 `item.stale` 合并进详情对象。若当前实现已经 `setEditing({ ...item, ...r.data })`，无需修改；否则改为：

```javascript
  const openCase = (item) => {
    api.get(`/rag/benchmark/cases/${encodeURIComponent(item.id)}`)
      .then(r => setEditing({ ...item, ...r.data }))
      .catch(e => alert(e.response?.data?.detail || e.message))
  }
```

- [ ] **步骤 5：运行 WebUI 静态绿灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark_webui.py -q -p no:cacheprovider
```

预期：该文件全部通过。

## 任务 5：文档更新

**文件：**

- 修改：`docs/evals.md`

- [ ] **步骤 1：补充 RAG Benchmark 边界**

在 `Generated case 只作为本地 DB 采样候选` 段落后追加：

```markdown
Admin API 提供 `POST /api/v1/admin/rag/benchmark/cases/{case_id}/promote-manual` 作为单条 generated → manual 仲裁入口。该接口支持 `dry_run=true` 预检目标 `target_case_id`、目标路径和转换后的 case JSON；`dry_run=false` 才会写入 `evals/cases/rag_benchmark/manual/{target_case_id}.json`，并记录 `promote_rag_benchmark_generated_case` 审计。stale generated case 必须先重新刷新 generated，避免把已过期 DB fingerprint 的样本提升为稳定样本。

Promote 不会自动更新 `evals/baselines/rag_benchmark.json`，也不代表样本已进入稳定 gate。只有人工确认 manual case 应纳入稳定门禁时，才同步 baseline 并运行 RAG stable gate。`tmp/rag_benchmark/generated/*` 仍是本地派生产物，不应提交。
```

- [ ] **步骤 2：运行文档 diff 检查**

运行：

```bash
git diff --check -- docs/evals.md
```

预期：无输出。

## 任务 6：定向验证与相邻回归

**文件：**

- 读取：后端、前端、文档 diff。

- [ ] **步骤 1：运行核心定向测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark_admin.py tests/test_rag_benchmark_webui.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 2：运行 RAG 相邻回归**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -q -p no:cacheprovider
```

预期：全部通过，确认未改变 stable gate、baseline 合同或 fixture 语义。

- [ ] **步骤 3：运行 WebUI build**

运行：

```bash
npm --prefix webui run build
```

预期：退出码 0。允许现有 Vite chunk size warning，但不能有编译错误。

- [ ] **步骤 4：检查 diff 边界**

运行：

```bash
git diff -- api/admin/rag_benchmark_routes.py tests/test_rag_benchmark_admin.py webui/src/features/rag/RagBenchmarkPage.jsx tests/test_rag_benchmark_webui.py docs/evals.md
git status --short
```

预期：只看到本阶段文件和既有无关脏项；不得出现 `tmp/rag_benchmark/generated/*`、`tmp/rag_benchmark/reports/*`、`evals/reports/*.json` 或 baseline 更新。

## 任务 7：实现阶段提交

**文件：**

- 暂存：`api/admin/rag_benchmark_routes.py`
- 暂存：`tests/test_rag_benchmark_admin.py`
- 暂存：`webui/src/features/rag/RagBenchmarkPage.jsx`
- 暂存：`tests/test_rag_benchmark_webui.py`
- 暂存：`docs/evals.md`
- 暂存：实际生成的 `webui/dist/index.html`
- 暂存：实际生成的 `webui/dist/assets/index-*.js`
- 不暂存：`tmp/`、`evals/reports/`、`evals/baselines/rag_benchmark.json`。

- [ ] **步骤 1：按文件暂存**

运行：

```bash
git add api/admin/rag_benchmark_routes.py tests/test_rag_benchmark_admin.py webui/src/features/rag/RagBenchmarkPage.jsx tests/test_rag_benchmark_webui.py docs/evals.md
```

如果 WebUI build 更新 dist，按实际文件名运行：

```bash
git add webui/dist/index.html webui/dist/assets/<actual-index-bundle>.js
```

- [ ] **步骤 2：提交实现**

运行：

```bash
git commit -m "feat(评测): 支持 RAG 样本提升为 manual"
```

预期：只包含本阶段实现文件。

## 任务 8：最终全量验证与计划收口

**文件：**

- 修改：`.Codex/plans/rag-generated-manual-promotion.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：运行全量测试**

运行：

```bash
python -B -m pytest tests/ -q -p no:cacheprovider
```

预期：0 failures。记录通过数、skip 数、warning 数和耗时。

- [ ] **步骤 2：更新计划执行记录**

在本文件 `执行记录` 写入：

- 计划提交号。
- 后端红灯结果。
- WebUI 红灯结果。
- 定向绿灯结果。
- 相邻回归结果。
- WebUI build 结果。
- 全量回归结果。
- 实现提交号。

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

在真实样本运营动作之后新增 RAG 样本仲裁入口状态，记录：

- 设计提交。
- 计划提交。
- 实现提交。
- 验证命令与结果。
- 明确下一步候选：通用 EvalCandidate 运营规则或 RAG 仲裁批量化，不在本阶段实现。

- [ ] **步骤 4：检查文档 diff**

运行：

```bash
git diff --check -- .Codex/plans/rag-generated-manual-promotion.md docs/plan_walkthrough.md
```

预期：无输出。

- [ ] **步骤 5：提交收口文档**

运行：

```bash
git add .Codex/plans/rag-generated-manual-promotion.md docs/plan_walkthrough.md
git commit -m "docs(计划): 收口 RAG 样本仲裁状态"
```

预期：只包含计划和 walkthrough 文档。

## 验收清单

- [ ] `POST /api/v1/admin/rag/benchmark/cases/{case_id}/promote-manual` 支持 dry-run 和 apply。
- [ ] 只允许 generated case 提升，manual 源返回 `409`。
- [ ] stale generated case 返回 `409`，提示刷新 generated。
- [ ] unsafe `target_case_id` 返回 `400`。
- [ ] 目标 manual 已存在且 `overwrite=false` 返回 `409`。
- [ ] `overwrite=true` apply 会备份旧 manual 文件。
- [ ] apply 写入 manual JSON，保留 query、filters、expected 和 generated provenance。
- [ ] apply 写 `promote_rag_benchmark_generated_case` audit。
- [ ] WebUI generated case 详情提供「提升为 Manual」二阶段流程。
- [ ] manual 目录不可写或 stale generated 时 WebUI 禁用入口并显示原因。
- [ ] 文档说明 promote 不自动更新 baseline，不提交 `tmp/` generated 产物。
- [ ] 未修改 sampler、runner、scoring、fixture、baseline 和 gate 脚本。
- [ ] 定向测试、相邻回归、WebUI build 和全量测试都有新鲜验证结果。
