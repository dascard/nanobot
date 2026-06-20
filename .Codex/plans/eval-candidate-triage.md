# EvalCandidate 候选仲裁状态实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:test-driven-development 执行实现步骤，并在提交前使用 superpowers:verification-before-completion 和 superpowers:chinese-commit-conventions。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为通用 `EvalCandidate` 候选队列增加 `reject / defer / reopen` 运营状态和统一审计，让真实样本可以被明确拒绝、暂缓和复开。

**架构：** 不改数据库 schema，复用 `EvalCandidate.status` 与 `note`；状态转换集中在 `core/eval_sampling/store.py`，Admin API 只暴露显式动作端点并写统一 audit detail；WebUI 只做单条操作；CLI 复用现有 `export --status` 做离线复核。

**技术栈：** FastAPI、Pydantic、SQLAlchemy、pytest、React、Vite、现有 `AdminAuditLog`、现有 `EvalCandidate` store。

---

## 执行记录

- [x] 设计阶段：写入 `docs/superpowers/specs/2026-06-20-eval-candidate-triage-design.md`。
- [x] 设计提交：`d53ba55 docs(评测): 设计候选仲裁状态`。
- [ ] 计划阶段：写入 `.Codex/plans/eval-candidate-triage.md` 并提交。
- [ ] 后端状态机与 Admin API：按 TDD 增加 `reject / defer / reopen`。
- [ ] CLI 与 WebUI：按 TDD 增加新状态导出守卫和单条仲裁入口。
- [ ] 文档收口：同步 `docs/evals.md`、`docs/plan_walkthrough.md` 和本计划验证记录。

## 文件结构

- 修改：`core/eval_sampling/store.py`
  - 新增状态常量和原因码常量。
  - 新增 `reject_candidate()`、`defer_candidate()`、`reopen_candidate()`。
  - 收紧 `label_candidate()` 与 `ignore_candidate()` 的来源状态。
  - 保持 readiness/preflight/promote 现有契约不变。
- 修改：`api/admin_routes.py`
  - 新增 `CandidateTriageRequest`。
  - 新增 `POST /evals/candidates/{case_id}/reject`。
  - 新增 `POST /evals/candidates/{case_id}/defer`。
  - 新增 `POST /evals/candidates/{case_id}/reopen`。
  - 审计 detail 使用 store 返回的统一 payload。
- 修改：`evals/candidates.py`
  - 保持 CLI 命令不扩展，必要时只调整帮助文案。
- 修改：`webui/src/features/evals/EvalsPage.jsx`
  - 状态筛选新增 `deferred`、`rejected`。
  - 候选行新增「暂缓」「拒绝」「复开」操作。
  - 新增单条仲裁 modal，包含 `reason_code`、`note`、`defer_until`。
- 修改：`tests/test_eval_candidate_contract.py`
  - 覆盖 store 状态转换、非法转换、Admin API 和审计 detail。
- 修改：`tests/test_eval_candidates_cli.py`
  - 覆盖 `export --status deferred/rejected`。
- 修改：`tests/test_webui_admin_redesign.py`
  - 静态守卫新状态筛选、动作端点和 modal 字段。
- 修改：`docs/evals.md`
  - 补候选运营状态机、原因码和 RAG 边界。
- 修改：`docs/plan_walkthrough.md`
  - 同步阶段状态、提交号和验证结果。
- 修改：`.Codex/plans/eval-candidate-triage.md`
  - 记录执行进度和验证证据。

## 接口契约

### 状态常量

```python
CANDIDATE_STATUS_CANDIDATE = "candidate"
CANDIDATE_STATUS_LABELED = "labeled"
CANDIDATE_STATUS_IGNORED = "ignored"
CANDIDATE_STATUS_DEFERRED = "deferred"
CANDIDATE_STATUS_REJECTED = "rejected"
CANDIDATE_STATUS_PROMOTED = "promoted"
```

### 原因码

```python
REJECT_REASON_CODES = frozenset({
    "unspecified",
    "duplicate",
    "low_value",
    "unsafe_or_sensitive",
    "not_reproducible",
    "out_of_scope",
    "bad_sample",
})

DEFER_REASON_CODES = frozenset({
    "unspecified",
    "needs_more_context",
    "needs_batch_review",
    "waiting_for_baseline",
    "needs_product_decision",
    "temporary_blocker",
})

REOPEN_REASON_CODES = frozenset({
    "unspecified",
    "new_evidence",
    "operator_correction",
    "defer_expired",
    "needs_relabel",
})
```

未知原因码拒绝，避免 summary 和 audit 后续难以聚合。

### Store 返回形态

```python
{
    "candidate": _candidate_dict(row),
    "audit": {
        "before_status": "candidate",
        "after_status": "deferred",
        "reason_code": "needs_more_context",
        "note": "缺少后续回复",
        "defer_until": "2026-06-30",
    },
}
```

`reject_candidate()` 和 `reopen_candidate()` 的 `defer_until` 固定为空字符串。

### API 请求

```json
{
  "reason_code": "needs_more_context",
  "note": "缺少后续回复",
  "defer_until": "2026-06-30"
}
```

`reason_code` 可省略，省略时归一为 `unspecified`。`note` 最多保留 1000 字符。`defer_until` 仅 `defer` 记录。

## 子 agent 分配

本阶段文件边界较清晰，可以并行，但主线程必须审查和集成。

- 后端 agent：只修改 `core/eval_sampling/store.py`、`api/admin_routes.py`、`tests/test_eval_candidate_contract.py`。不得修改 WebUI、CLI 或文档。
- WebUI agent：后端 API 契约稳定后启动，只修改 `webui/src/features/evals/EvalsPage.jsx` 和 `tests/test_webui_admin_redesign.py`。
- 文档 agent：实现稳定后启动，只修改 `docs/evals.md`、`docs/plan_walkthrough.md` 和本计划。

如果当前会话不启用 worker，则按下方任务顺序内联执行。每个任务都要先红灯、再绿灯。

## 任务 1：后端状态机红灯

**文件：**

- 修改：`tests/test_eval_candidate_contract.py`

- [ ] **步骤 1：新增 store 状态转换测试**

在 `test_eval_patch_candidate_rejects_direct_labeled_promoted_and_unknown_status` 后新增：

```python
def test_candidate_triage_transitions_return_audit_payload(db_session):
    from core.eval_sampling.store import (
        defer_candidate,
        get_candidate,
        reject_candidate,
        reopen_candidate,
    )

    _insert_candidate(db_session, case_id="cand_reject")
    rejected = reject_candidate(
        db_session,
        "cand_reject",
        reason_code="low_value",
        note="普通寒暄，不进入稳定集",
    )

    assert rejected["candidate"]["status"] == "rejected"
    assert rejected["audit"] == {
        "before_status": "candidate",
        "after_status": "rejected",
        "reason_code": "low_value",
        "note": "普通寒暄，不进入稳定集",
        "defer_until": "",
    }
    assert get_candidate(db_session, "cand_reject").status == "rejected"

    _insert_candidate(db_session, case_id="cand_defer")
    deferred = defer_candidate(
        db_session,
        "cand_defer",
        reason_code="needs_more_context",
        note="等后续对话补齐上下文",
        defer_until="2026-06-30",
    )

    assert deferred["candidate"]["status"] == "deferred"
    assert deferred["audit"]["before_status"] == "candidate"
    assert deferred["audit"]["after_status"] == "deferred"
    assert deferred["audit"]["reason_code"] == "needs_more_context"
    assert deferred["audit"]["defer_until"] == "2026-06-30"

    reopened = reopen_candidate(
        db_session,
        "cand_defer",
        reason_code="defer_expired",
        note="到期复核",
    )

    assert reopened["candidate"]["status"] == "candidate"
    assert reopened["audit"]["before_status"] == "deferred"
    assert reopened["audit"]["after_status"] == "candidate"
    assert reopened["audit"]["reason_code"] == "defer_expired"
```

- [ ] **步骤 2：新增非法转换测试**

在同一区域新增：

```python
def test_candidate_triage_rejects_invalid_transitions_and_reason_codes(
    db_session,
    tmp_path,
    monkeypatch,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import (
        defer_candidate,
        get_candidate,
        label_candidate,
        promote_candidate,
        reject_candidate,
        reopen_candidate,
    )

    _insert_candidate(db_session, case_id="cand_promoted")
    label_candidate(db_session, "cand_promoted", {"timing_action": "continue"})
    promote_candidate(db_session, "cand_promoted")

    for action in (
        lambda: reject_candidate(db_session, "cand_promoted", reason_code="low_value"),
        lambda: defer_candidate(db_session, "cand_promoted", reason_code="needs_more_context"),
        lambda: reopen_candidate(db_session, "cand_promoted", reason_code="new_evidence"),
    ):
        with pytest.raises(ValueError, match="invalid status transition"):
            action()

    _insert_candidate(db_session, case_id="cand_bad_reason")
    with pytest.raises(ValueError, match="invalid reason_code"):
        reject_candidate(db_session, "cand_bad_reason", reason_code="unknown_reason")

    _insert_candidate(db_session, case_id="cand_rejected")
    reject_candidate(db_session, "cand_rejected", reason_code="low_value")
    assert get_candidate(db_session, "cand_rejected").status == "rejected"
    with pytest.raises(ValueError, match="candidate status"):
        label_candidate(db_session, "cand_rejected", {"timing_action": "continue"})
```

- [ ] **步骤 3：运行红灯**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py::test_candidate_triage_transitions_return_audit_payload tests/test_eval_candidate_contract.py::test_candidate_triage_rejects_invalid_transitions_and_reason_codes -q -p no:cacheprovider
```

预期：失败，原因是 `reject_candidate`、`defer_candidate`、`reopen_candidate` 尚不存在，或 `label_candidate()` 尚未收紧状态。

## 任务 2：实现后端状态机

**文件：**

- 修改：`core/eval_sampling/store.py`

- [ ] **步骤 1：新增状态和原因码常量**

在 `RUNNABLE_EVAL_SUITES` 后新增：

```python
CANDIDATE_STATUS_CANDIDATE = "candidate"
CANDIDATE_STATUS_LABELED = "labeled"
CANDIDATE_STATUS_IGNORED = "ignored"
CANDIDATE_STATUS_DEFERRED = "deferred"
CANDIDATE_STATUS_REJECTED = "rejected"
CANDIDATE_STATUS_PROMOTED = "promoted"

PATCHABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_IGNORED,
}

LABELABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_DEFERRED,
}

IGNORABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_LABELED,
}

REJECTABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_LABELED,
    CANDIDATE_STATUS_DEFERRED,
    CANDIDATE_STATUS_IGNORED,
}

DEFERABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_LABELED,
}

REOPENABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_IGNORED,
    CANDIDATE_STATUS_DEFERRED,
    CANDIDATE_STATUS_REJECTED,
}
```

再加入原因码常量：

```python
REJECT_REASON_CODES = frozenset({
    "unspecified",
    "duplicate",
    "low_value",
    "unsafe_or_sensitive",
    "not_reproducible",
    "out_of_scope",
    "bad_sample",
})

DEFER_REASON_CODES = frozenset({
    "unspecified",
    "needs_more_context",
    "needs_batch_review",
    "waiting_for_baseline",
    "needs_product_decision",
    "temporary_blocker",
})

REOPEN_REASON_CODES = frozenset({
    "unspecified",
    "new_evidence",
    "operator_correction",
    "defer_expired",
    "needs_relabel",
})
```

- [ ] **步骤 2：新增归一化 helper**

在 `_readiness_reason()` 后新增：

```python
def _normalize_reason_code(value: str | None, allowed: frozenset[str]) -> str:
    code = str(value or "unspecified").strip() or "unspecified"
    if len(code) > 64 or code not in allowed:
        raise ValueError(f"invalid reason_code: {value}")
    return code


def _normalize_note(value: str | None) -> str:
    return str(value or "").strip()[:1000]


def _normalize_defer_until(value: str | None) -> str:
    return str(value or "").strip()[:64]


def _triage_payload(
    row: EvalCandidate,
    *,
    before_status: str,
    reason_code: str,
    note: str,
    defer_until: str = "",
) -> dict[str, Any]:
    return {
        "candidate": _candidate_dict(row),
        "audit": {
            "before_status": before_status,
            "after_status": row.status,
            "reason_code": reason_code,
            "note": note,
            "defer_until": defer_until,
        },
    }
```

- [ ] **步骤 3：收紧现有状态转换**

把 `update_candidate()` 中的状态集合替换为 `PATCHABLE_CANDIDATE_STATUSES`，保持 PATCH 只能做旧的轻量 `candidate/ignored` 转换。

在 `label_candidate()` 中，`validate_expected_contract()` 前新增：

```python
if row.status not in LABELABLE_CANDIDATE_STATUSES:
    raise ValueError(f"candidate status must be candidate or deferred before label: {row.status}")
```

在 `ignore_candidate()` 中新增：

```python
if row.status not in IGNORABLE_CANDIDATE_STATUSES:
    raise ValueError(f"invalid status transition: {row.status} -> ignored")
```

- [ ] **步骤 4：新增 triage 函数**

在 `ignore_candidate()` 后新增：

```python
def reject_candidate(
    db,
    case_id: str,
    *,
    reason_code: str | None = None,
    note: str | None = None,
):
    row = get_candidate(db, case_id)
    if not row:
        return None
    if row.status not in REJECTABLE_CANDIDATE_STATUSES:
        raise ValueError(f"invalid status transition: {row.status} -> rejected")
    before = row.status
    reason = _normalize_reason_code(reason_code, REJECT_REASON_CODES)
    normalized_note = _normalize_note(note)
    row.status = CANDIDATE_STATUS_REJECTED
    if normalized_note:
        row.note = normalized_note
    row.updated_at = datetime.now()
    db.commit()
    return _triage_payload(
        row,
        before_status=before,
        reason_code=reason,
        note=normalized_note,
    )
```

同文件新增 `defer_candidate()` 和 `reopen_candidate()`：

```python
def defer_candidate(
    db,
    case_id: str,
    *,
    reason_code: str | None = None,
    note: str | None = None,
    defer_until: str | None = None,
):
    row = get_candidate(db, case_id)
    if not row:
        return None
    if row.status not in DEFERABLE_CANDIDATE_STATUSES:
        raise ValueError(f"invalid status transition: {row.status} -> deferred")
    before = row.status
    reason = _normalize_reason_code(reason_code, DEFER_REASON_CODES)
    normalized_note = _normalize_note(note)
    normalized_defer_until = _normalize_defer_until(defer_until)
    row.status = CANDIDATE_STATUS_DEFERRED
    if normalized_note:
        row.note = normalized_note
    row.updated_at = datetime.now()
    db.commit()
    return _triage_payload(
        row,
        before_status=before,
        reason_code=reason,
        note=normalized_note,
        defer_until=normalized_defer_until,
    )


def reopen_candidate(
    db,
    case_id: str,
    *,
    reason_code: str | None = None,
    note: str | None = None,
):
    row = get_candidate(db, case_id)
    if not row:
        return None
    if row.status not in REOPENABLE_CANDIDATE_STATUSES:
        raise ValueError(f"invalid status transition: {row.status} -> candidate")
    before = row.status
    reason = _normalize_reason_code(reason_code, REOPEN_REASON_CODES)
    normalized_note = _normalize_note(note)
    row.status = CANDIDATE_STATUS_CANDIDATE
    if normalized_note:
        row.note = normalized_note
    row.updated_at = datetime.now()
    db.commit()
    return _triage_payload(
        row,
        before_status=before,
        reason_code=reason,
        note=normalized_note,
    )
```

- [ ] **步骤 5：运行后端状态机绿灯**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py::test_candidate_triage_transitions_return_audit_payload tests/test_eval_candidate_contract.py::test_candidate_triage_rejects_invalid_transitions_and_reason_codes -q -p no:cacheprovider
```

预期：通过。

## 任务 3：Admin API 红灯与实现

**文件：**

- 修改：`tests/test_eval_candidate_contract.py`
- 修改：`api/admin_routes.py`

- [ ] **步骤 1：新增 API 审计红灯测试**

在 `test_eval_candidates_preflight_returns_ready_and_blocked_items` 前新增：

```python
def test_eval_candidate_triage_endpoints_write_audit_detail(
    client,
    db_session,
    monkeypatch,
):
    from core.database import AdminAuditLog

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session, case_id="cand_defer_api")

    response = client.post(
        "/api/v1/admin/evals/candidates/cand_defer_api/defer",
        headers=_auth_header(),
        json={
            "reason_code": "needs_more_context",
            "note": "缺少后续上下文",
            "defer_until": "2026-06-30",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "deferred"

    audit = (
        db_session.query(AdminAuditLog)
        .filter(AdminAuditLog.action == "defer_candidate")
        .order_by(AdminAuditLog.id.desc())
        .first()
    )
    assert audit is not None
    detail = json.loads(audit.detail_json)
    assert detail["before_status"] == "candidate"
    assert detail["after_status"] == "deferred"
    assert detail["reason_code"] == "needs_more_context"
    assert detail["note"] == "缺少后续上下文"
    assert detail["defer_until"] == "2026-06-30"

    rejected = client.post(
        "/api/v1/admin/evals/candidates/cand_defer_api/reject",
        headers=_auth_header(),
        json={"reason_code": "low_value", "note": "复核后拒绝"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    reopened = client.post(
        "/api/v1/admin/evals/candidates/cand_defer_api/reopen",
        headers=_auth_header(),
        json={"reason_code": "operator_correction", "note": "恢复到候选"},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "candidate"
```

- [ ] **步骤 2：运行 API 红灯**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py::test_eval_candidate_triage_endpoints_write_audit_detail -q -p no:cacheprovider
```

预期：失败，接口返回 405 或 404。

- [ ] **步骤 3：实现 API 端点**

在 `core.eval_sampling.store` import 列表加入：

```python
    reject_candidate, defer_candidate, reopen_candidate,
```

在 `EvalCandidatePatch` 后新增 request model：

```python
class CandidateTriageRequest(BaseModel):
    reason_code: str = ""
    note: str = ""
    defer_until: str = ""
```

新增 helper：

```python
def _triage_response_or_404(result):
    if not result:
        raise HTTPException(404, "candidate not found")
    return result
```

在 `/ignore` 路由前新增 3 个端点：

```python
@router.post("/evals/candidates/{case_id}/reject")
def eval_reject_candidate(
    case_id: str,
    body: CandidateTriageRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = reject_candidate(
            db,
            case_id,
            reason_code=body.reason_code,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    result = _triage_response_or_404(result)
    _audit_request(db, request, "reject_candidate", "eval_candidate", case_id, result["audit"])
    return result["candidate"]
```

按同样结构实现 `eval_defer_candidate()` 和 `eval_reopen_candidate()`。`defer` 需要传入 `defer_until=body.defer_until`。

- [ ] **步骤 4：运行 API 绿灯与后端相邻回归**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py::test_eval_candidate_triage_endpoints_write_audit_detail -q -p no:cacheprovider
python -B -m pytest tests/test_eval_candidate_contract.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 5：Commit 后端阶段**

```bash
git add core/eval_sampling/store.py api/admin_routes.py tests/test_eval_candidate_contract.py
git commit -m "feat(评测): 增加候选仲裁状态"
```

## 任务 4：CLI 与 WebUI 红灯

**文件：**

- 修改：`tests/test_eval_candidates_cli.py`
- 修改：`tests/test_webui_admin_redesign.py`

- [ ] **步骤 1：新增 CLI 新状态导出测试**

在 `test_export_candidates_writes_jsonl` 后新增：

```python
def test_export_candidates_supports_deferred_and_rejected_statuses(db_session, tmp_path):
    from core.eval_sampling.store import defer_candidate, reject_candidate
    from evals.candidates import export_candidates

    _insert_candidate(db_session, case_id="cand_deferred")
    defer_candidate(db_session, "cand_deferred", reason_code="needs_more_context")
    _insert_candidate(db_session, case_id="cand_rejected")
    reject_candidate(db_session, "cand_rejected", reason_code="low_value")

    deferred_path = tmp_path / "deferred.jsonl"
    rejected_path = tmp_path / "rejected.jsonl"

    assert export_candidates(db_session, deferred_path, status="deferred") == 1
    assert export_candidates(db_session, rejected_path, status="rejected") == 1
    assert json.loads(deferred_path.read_text(encoding="utf-8"))["status"] == "deferred"
    assert json.loads(rejected_path.read_text(encoding="utf-8"))["status"] == "rejected"
```

这个测试依赖任务 2 的 store 函数，通常会直接通过；如果失败，修 CLI 导出路径。

- [ ] **步骤 2：新增 WebUI 静态红灯测试**

在 `test_evals_candidates_page_shows_summary_readiness_and_preflight` 后新增：

```python
def test_evals_candidates_page_exposes_triage_actions():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert '<option value="deferred">deferred</option>' in source
    assert '<option value="rejected">rejected</option>' in source
    assert "/reject" in source
    assert "/defer" in source
    assert "/reopen" in source
    assert "reason_code" in source
    assert "defer_until" in source
    assert "暂缓" in source
    assert "拒绝" in source
    assert "复开" in source
```

- [ ] **步骤 3：运行红灯**

运行：

```bash
python -B -m pytest tests/test_eval_candidates_cli.py::test_export_candidates_supports_deferred_and_rejected_statuses tests/test_webui_admin_redesign.py::test_evals_candidates_page_exposes_triage_actions -q -p no:cacheprovider
```

预期：CLI 测试可能通过；WebUI 测试失败，原因是页面还没有新状态和动作。

## 任务 5：实现 WebUI 仲裁入口

**文件：**

- 修改：`webui/src/features/evals/EvalsPage.jsx`

- [ ] **步骤 1：新增状态和 helper**

在组件顶部 state 区新增：

```javascript
  const [triageAction, setTriageAction] = useState(null)
  const [triageCandidate, setTriageCandidate] = useState(null)
  const [triageReason, setTriageReason] = useState('')
  const [triageNote, setTriageNote] = useState('')
  const [triageDeferUntil, setTriageDeferUntil] = useState('')
  const [triageError, setTriageError] = useState('')
  const [triageBusy, setTriageBusy] = useState(false)
```

新增 reason options：

```javascript
const TRIAGE_REASON_OPTIONS = {
  reject: [
    ['low_value', 'low_value'],
    ['duplicate', 'duplicate'],
    ['unsafe_or_sensitive', 'unsafe_or_sensitive'],
    ['not_reproducible', 'not_reproducible'],
    ['out_of_scope', 'out_of_scope'],
    ['bad_sample', 'bad_sample'],
  ],
  defer: [
    ['needs_more_context', 'needs_more_context'],
    ['needs_batch_review', 'needs_batch_review'],
    ['waiting_for_baseline', 'waiting_for_baseline'],
    ['needs_product_decision', 'needs_product_decision'],
    ['temporary_blocker', 'temporary_blocker'],
  ],
  reopen: [
    ['new_evidence', 'new_evidence'],
    ['operator_correction', 'operator_correction'],
    ['defer_expired', 'defer_expired'],
    ['needs_relabel', 'needs_relabel'],
  ],
}
```

新增 open/submit 函数：

```javascript
  const openTriage = (candidate, action) => {
    setTriageCandidate(candidate)
    setTriageAction(action)
    setTriageReason(TRIAGE_REASON_OPTIONS[action]?.[0]?.[0] || 'unspecified')
    setTriageNote('')
    setTriageDeferUntil('')
    setTriageError('')
  }

  const submitTriage = () => {
    if (!triageCandidate || !triageAction) return
    setTriageBusy(true)
    setTriageError('')
    api.post(`/evals/candidates/${encodeURIComponent(triageCandidate.case_id)}/${triageAction}`, {
      reason_code: triageReason,
      note: triageNote,
      defer_until: triageAction === 'defer' ? triageDeferUntil : '',
    }).then(() => {
      setTriageAction(null)
      setTriageCandidate(null)
      loadCandidates()
      if (detail?.case_id === triageCandidate.case_id) loadDetail(triageCandidate.case_id)
    }).catch(e => setTriageError(formatApiError(e))).finally(() => setTriageBusy(false))
  }
```

- [ ] **步骤 2：扩展筛选与按钮**

状态下拉增加：

```jsx
<option value="deferred">deferred</option>
<option value="rejected">rejected</option>
```

Badge tone 判断加入 `deferred` 和 `rejected`：

```javascript
candidate.status === 'deferred' ? 'amber' : candidate.status === 'rejected' ? 'red' : ...
```

候选操作区加入：

```jsx
{['candidate', 'labeled'].includes(candidate.status) && (
  <>
    <button onClick={() => openTriage(candidate, 'defer')}
      className="px-2 py-1 bg-amber-700/40 hover:bg-amber-700 text-amber-200 rounded text-xs">暂缓</button>
    <button onClick={() => openTriage(candidate, 'reject')}
      className="px-2 py-1 bg-red-700/40 hover:bg-red-700 text-red-200 rounded text-xs">拒绝</button>
  </>
)}
{['ignored', 'deferred', 'rejected'].includes(candidate.status) && (
  <button onClick={() => openTriage(candidate, 'reopen')}
    className="px-2 py-1 bg-sky-700/40 hover:bg-sky-700 text-sky-200 rounded text-xs">复开</button>
)}
```

- [ ] **步骤 3：新增 modal**

在 promote modal 后新增：

```jsx
{triageAction && triageCandidate && (
  <Modal onClose={() => setTriageAction(null)}>
    <div className="p-6">
      <h2 className="mb-2 text-lg font-bold">
        {triageAction === 'defer' ? '暂缓候选' : triageAction === 'reject' ? '拒绝候选' : '复开候选'}
      </h2>
      <p className="mb-4 text-xs text-slate-500">{triageCandidate.case_id}</p>
      {triageError && (
        <div className="mb-3 rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-200">
          {triageError}
        </div>
      )}
      <div className="space-y-3">
        <label className="block text-xs">
          <span className="mb-1 block text-slate-400">reason_code</span>
          <select value={triageReason} onChange={e => setTriageReason(e.target.value)}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 p-2">
            {(TRIAGE_REASON_OPTIONS[triageAction] || []).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        {triageAction === 'defer' && (
          <label className="block text-xs">
            <span className="mb-1 block text-slate-400">defer_until</span>
            <input value={triageDeferUntil} onChange={e => setTriageDeferUntil(e.target.value)}
              placeholder="2026-06-30"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 p-2" />
          </label>
        )}
        <label className="block text-xs">
          <span className="mb-1 block text-slate-400">备注</span>
          <textarea value={triageNote} onChange={e => setTriageNote(e.target.value)}
            rows={4}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 p-2" />
        </label>
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={() => setTriageAction(null)}
          className="rounded bg-slate-800 px-3 py-2 text-xs">取消</button>
        <button onClick={submitTriage} disabled={triageBusy}
          className="rounded bg-indigo-700 px-3 py-2 text-xs text-white disabled:opacity-50">
          {triageBusy ? '提交中...' : '确认'}
        </button>
      </div>
    </div>
  </Modal>
)}
```

- [ ] **步骤 4：运行 WebUI 绿灯**

运行：

```bash
python -B -m pytest tests/test_eval_candidates_cli.py::test_export_candidates_supports_deferred_and_rejected_statuses tests/test_webui_admin_redesign.py::test_evals_candidates_page_exposes_triage_actions -q -p no:cacheprovider
python -B -m pytest tests/test_webui_admin_redesign.py -q -p no:cacheprovider
npm --prefix webui run build
```

预期：全部通过，build 退出码 0。

- [ ] **步骤 5：Commit CLI / WebUI 阶段**

```bash
git add evals/candidates.py tests/test_eval_candidates_cli.py webui/src/features/evals/EvalsPage.jsx tests/test_webui_admin_redesign.py
git commit -m "feat(评测): 增加候选仲裁入口"
```

如果 `evals/candidates.py` 没有实际改动，不要暂存它。

## 任务 6：文档收口

**文件：**

- 修改：`docs/evals.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/eval-candidate-triage.md`

- [ ] **步骤 1：更新 `docs/evals.md`**

在「候选 readiness 与批量预检」后新增一节「候选仲裁状态」，内容包括：

- 通用 `EvalCandidate` 支持 `reject`、`defer`、`reopen` 3 个显式运营动作。
- `reject` 会把候选置为 `rejected`，表示人工确认不进入稳定样本。
- `defer` 会把候选置为 `deferred`，表示暂缓处理。
- `reopen` 会把 `ignored`、`deferred` 或 `rejected` 复开为 `candidate`。
- 动作端点为 `POST /api/v1/admin/evals/candidates/{case_id}/reject`、`POST /api/v1/admin/evals/candidates/{case_id}/defer` 和 `POST /api/v1/admin/evals/candidates/{case_id}/reopen`。
- 每次动作都会记录 `before_status`、`after_status`、`reason_code`、`note` 和 `defer_until` 到 Admin audit。
- `PATCH` 不能直接写入 `deferred` 或 `rejected`。
- RAG benchmark 的 generated / manual case 仍是独立体系，不并入通用 `EvalCandidate`；generated case 的单条提升继续使用 RAG Admin 的 `promote-manual` 接口。

- [ ] **步骤 2：更新 `docs/plan_walkthrough.md`**

在真实样本运营进度中新增「真实样本运营 4」行，状态为已完成或执行中，按实际提交号和验证结果记录。

- [ ] **步骤 3：更新本计划执行记录和验证记录**

勾选已完成任务，写入红灯、绿灯、build、全量回归结果。

- [ ] **步骤 4：运行最终验证**

运行：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py -q -p no:cacheprovider
npm --prefix webui run build
python -B -m pytest tests/ -q -p no:cacheprovider
```

预期：全部通过；若全量失败，先定位是否与本阶段有关，不得提交收口文档。

- [ ] **步骤 5：Commit 文档收口**

```bash
git add docs/evals.md docs/plan_walkthrough.md .Codex/plans/eval-candidate-triage.md
git commit -m "docs(计划): 收口候选仲裁状态"
```

## 最终核对清单

- [ ] `reject / defer / reopen` 只能走显式端点，不能通过 PATCH 绕过。
- [ ] `promoted` 保持终态。
- [ ] `label_candidate()` 不再允许从 `ignored/rejected/promoted` 直接打标。
- [ ] Admin audit detail 包含 `before_status`、`after_status`、`reason_code`、`note`、`defer_until`。
- [ ] WebUI 不做批量操作，不改 promote 和 preflight 的既有语义。
- [ ] CLI 可以导出 `deferred` 和 `rejected` 状态候选。
- [ ] RAG benchmark 边界在文档中保持独立。
- [ ] 每个阶段都有独立 commit，且只暂存本阶段文件。
