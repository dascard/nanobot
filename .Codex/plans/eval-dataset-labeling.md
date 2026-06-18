# P4-1 评测数据集与标注闭环实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 修复候选标注 / 晋升契约缺口，建立离线 `candidates → labeled → promoted` 闭环，并新增首个 per-capability 数据集。

**架构：** 先用 `expected_contract` 固定可评分 expected，再让 store / Admin / WebUI 都走同一校验；promote 增加 dry-run 和 `target_dataset`，CLI 负责离线导出、导入 labels、预检和晋升。RAG benchmark 保持独立，不并入通用 `EvalCase`。

**技术栈：** Python、pytest、SQLAlchemy、FastAPI、React、JSONL、现有 `evals` 框架。

---

## 文件职责

- `evals/expected_contract.py`：新增 expected 可评分契约，供 label / promote / 测试复用。
- `evals/schema.py`：给 `EvalCase` 增加可选 `meta`，保留来源追溯信息。
- `evals/scorers.py`：补齐 `served_sticker_id`、`send_source` 等历史 expected 的评分。
- `evals/runners/sticker_runner.py`：在 sticker runner 输出实际服务 sticker id 和 public proxy 来源。
- `core/eval_sampling/store.py`：校验候选标注、构建晋升计划、执行 dry-run / apply、写正式 case。
- `api/admin_routes.py`：修复 label 请求字段，promote 接收 `target_dataset` 和 dry-run。
- `webui/src/features/evals/EvalsPage.jsx`：前端 label 请求改发 `expected`，promote 文案显示目标 dataset。
- `evals/candidates.py`：新增离线 CLI，支持 export、import-labels、promote。
- `evals/cases/capability_model_routing/`：新增首个 per-capability 数据集。
- `evals/baselines/capability_model_routing.json`：新增该数据集 baseline。
- `docs/evals.md`、`docs/todo.md`、`docs/plan_walkthrough.md`：同步操作手册和路线状态。
- `tests/test_eval_candidate_contract.py`：新增候选契约、store、promote 和 dataset 测试。
- `tests/test_eval_candidates_cli.py`：新增 CLI helper / parser 测试。
- `tests/test_eval_baseline.py`：补 dataset / suite 语义守卫，必要时保留既有 baseline 测试。
- `tests/test_webui_admin_redesign.py` 或新增 WebUI 静态测试：守卫前端 label 字段。

---

## 任务 1：Expected 契约与历史未评分 key

**文件：**
- 创建：`evals/expected_contract.py`
- 修改：`evals/schema.py`
- 修改：`evals/scorers.py`
- 修改：`evals/runners/sticker_runner.py`
- 测试：`tests/test_eval_candidate_contract.py`

- [x] **步骤 1：编写 expected 契约红灯测试**

在 `tests/test_eval_candidate_contract.py` 新增：

```python
import pytest


def test_validate_expected_rejects_empty_needs_label_and_unknown_key():
    from evals.expected_contract import validate_expected_contract

    for expected in ({}, {"needs_label": True}, {"unscored_field": "x"}):
        with pytest.raises(ValueError):
            validate_expected_contract("timing_gate", expected)


def test_validate_expected_accepts_scored_keys():
    from evals.expected_contract import validate_expected_contract

    validate_expected_contract("timing_gate", {"timing_action": "continue", "should_reply": True})
    validate_expected_contract("model_routing", {"model_used": "vision-model"})
```

- [x] **步骤 2：编写 sticker 历史 expected 红灯测试**

同一测试文件新增：

```python
def test_sticker_expected_fields_are_scored():
    from evals.run import load_cases, run_case
    from evals.scorers import score_case

    cases = {
        case.id: case
        for case in load_cases("regression")
        if case.id in {
            "regression_sticker_duplicate_canonical_001",
            "regression_sticker_public_proxy_001",
        }
    }

    assert set(cases) == {
        "regression_sticker_duplicate_canonical_001",
        "regression_sticker_public_proxy_001",
    }
    for case in cases.values():
        output = run_case(case)
        score = score_case(case, output)
        assert score["passed"], score["errors"]
```

红灯预期：`ModuleNotFoundError: evals.expected_contract`，或 sticker case 因缺少 `served_sticker_id` / `send_source` 输出而失败。

- [x] **步骤 3：运行红灯测试**

运行：

```bash
python -m pytest tests/test_eval_candidate_contract.py::test_validate_expected_rejects_empty_needs_label_and_unknown_key tests/test_eval_candidate_contract.py::test_sticker_expected_fields_are_scored -v
```

预期：至少 1 个失败，失败点来自新契约或历史未评分字段。

- [x] **步骤 4：新增 expected 契约 helper**

创建 `evals/expected_contract.py`：

```python
"""Eval expected 字段契约。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SCOREABLE_EXPECTED_KEYS = frozenset({
    "should_reply",
    "timing_action",
    "scoring",
    "forbidden_tools",
    "required_tools",
    "send_mode",
    "reply_to_message_id",
    "mentions",
    "must_contain",
    "must_not_contain",
    "http_status",
    "content_type_prefix",
    "forbidden_terms",
    "should_create_jargon",
    "should_create_expression",
    "no_reply",
    "no_learn",
    "no_context",
    "should_enter_context",
    "should_write_chatlog",
    "should_write_conversation_turn",
    "model_used",
    "must_not_use",
    "should_call_auto_routing",
    "served_sticker_id",
    "send_source",
})


def validate_expected_contract(suite: str, expected: Mapping[str, Any]) -> None:
    if not expected:
        raise ValueError("expected must not be empty")
    if expected.get("needs_label"):
        raise ValueError("expected must not contain needs_label=true")

    unknown = sorted(str(key) for key in expected if key not in SCOREABLE_EXPECTED_KEYS)
    if unknown:
        raise ValueError(f"expected contains unscored keys for suite={suite}: {unknown}")
```

- [x] **步骤 5：给 EvalCase 增加 meta**

在 `evals/schema.py` 的 `EvalCase` 增加：

```python
    meta: dict[str, Any] = Field(default_factory=dict)
```

- [x] **步骤 6：补齐 scorer 对 sticker 字段的评分**

在 `evals/scorers.py` 末尾附近增加：

```python
    if "served_sticker_id" in exp:
        actual = output.raw.get("served_sticker_id")
        if str(actual or "") != str(exp["served_sticker_id"]):
            errors.append(
                f"served_sticker_id mismatch: expected={exp['served_sticker_id']} actual={actual}"
            )

    if "send_source" in exp:
        actual = output.raw.get("send_source")
        if actual != exp["send_source"]:
            errors.append(f"send_source mismatch: expected={exp['send_source']} actual={actual}")
```

- [x] **步骤 7：补齐 sticker runner 输出**

在 `evals/runners/sticker_runner.py`：

- public proxy 分支在 `out.raw["expanded_content"] = result` 后增加：

```python
                if "/api/v1/stickers/" in result and "gchat.qpic.cn" not in result:
                    out.raw["send_source"] = "public_proxy"
```

- image endpoint 分支在响应后增加：

```python
                out.raw["served_sticker_id"] = duplicate_of_id or sticker_id
```

如果实际 endpoint 暴露了 canonical id，应优先读取响应或 header 中的真实值；否则保持 runner 内部构造的 deterministic 值。

- [x] **步骤 8：运行契约测试绿灯**

运行：

```bash
python -m pytest tests/test_eval_candidate_contract.py -v
```

预期：本任务新增测试通过。

- [x] **步骤 9：运行相邻回归**

运行：

```bash
python -m pytest tests/test_eval_baseline.py tests/test_tools_package.py -k "sticker or eval" -v
```

预期：相关回归通过。

- [x] **步骤 10：提交任务 1**

运行：

```bash
git diff --check -- evals/expected_contract.py evals/schema.py evals/scorers.py evals/runners/sticker_runner.py tests/test_eval_candidate_contract.py
git add evals/expected_contract.py evals/schema.py evals/scorers.py evals/runners/sticker_runner.py tests/test_eval_candidate_contract.py
git commit -m "fix(评测): 校验可评分期望字段"
```

验证记录：

- 红灯：`python -m pytest tests/test_eval_candidate_contract.py::test_validate_expected_rejects_empty_needs_label_and_unknown_key tests/test_eval_candidate_contract.py::test_sticker_expected_fields_are_scored tests/test_eval_candidate_contract.py::test_sticker_runner_outputs_expected_contract_fields -v`，结果 `3 failed, 1 warning in 6.26s`；失败点为缺少 `evals.expected_contract`、scorer 忽略 `served_sticker_id/send_source`、runner 未输出 `served_sticker_id`。
- 绿灯：`python -m pytest tests/test_eval_candidate_contract.py -v`，结果 `4 passed, 1 warning in 0.93s`。
- 相邻回归：`python -m pytest tests/test_eval_baseline.py -v`，结果 `10 passed, 1 warning in 1.06s`。
- Regression eval：`PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run --suite regression`，结果 `total=11 passed=11 failed=0`。

---

## 任务 2：候选标注契约修复

**文件：**
- 修改：`core/eval_sampling/store.py`
- 修改：`api/admin_routes.py`
- 修改：`webui/src/features/evals/EvalsPage.jsx`
- 测试：`tests/test_eval_candidate_contract.py`
- 测试：`tests/test_webui_admin_redesign.py` 或现有 WebUI 静态测试文件

- [x] **步骤 1：编写 store 标注红灯测试**

在 `tests/test_eval_candidate_contract.py` 新增 helper 和测试：

```python
import json

import pytest

from core.database import EvalCandidate


def _insert_candidate(db_session, *, case_id="cand_timing_gate_1", suite="timing_gate"):
    row = EvalCandidate(
        case_id=case_id,
        suite=suite,
        source="db",
        source_ref="chatlog:1",
        description="candidate",
        input_json=json.dumps({"message": "nanobot 帮我看看"}, ensure_ascii=False),
        expected_json=json.dumps({"needs_label": True}, ensure_ascii=False),
        tags_json=json.dumps(["sampled", suite], ensure_ascii=False),
        status="candidate",
        fingerprint=f"fp-{case_id}",
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_label_candidate_rejects_empty_or_needs_label(db_session):
    from core.eval_sampling.store import label_candidate

    _insert_candidate(db_session)

    for expected in ({}, {"needs_label": True}):
        with pytest.raises(ValueError):
            label_candidate(db_session, "cand_timing_gate_1", expected)
```

- [x] **步骤 2：编写 API 字段兼容红灯测试**

在可复用 admin client 的测试文件中新增，或在 `tests/test_eval_candidate_contract.py` 里用 `TestClient`：

```python
def test_eval_label_candidate_accepts_expected_json_legacy_field(client, auth_header, db_session):
    _insert_candidate(db_session)

    response = client.post(
        "/api/v1/admin/evals/candidates/cand_timing_gate_1/label",
        headers=auth_header,
        json={"expected_json": {"timing_action": "continue"}},
    )

    assert response.status_code == 200
    assert response.json()["expected"] == {"timing_action": "continue"}
```

如果项目现有 admin 测试路径不带 `/api/v1/admin` 前缀，应按实际路由前缀调整。

- [x] **步骤 3：编写 WebUI 静态红灯测试**

在 `tests/test_webui_admin_redesign.py` 新增：

```python
def test_eval_candidate_label_posts_expected_field():
    text = Path("webui/src/features/evals/EvalsPage.jsx").read_text(encoding="utf-8")

    assert "expected_json: expectedJson" not in text
    assert "{ expected: expectedJson }" in text
```

- [x] **步骤 4：运行红灯测试**

运行：

```bash
python -m pytest tests/test_eval_candidate_contract.py -k "label_candidate or expected_json" -v
python -m pytest tests/test_webui_admin_redesign.py::test_eval_candidate_label_posts_expected_field -v
```

预期：当前实现失败于空 expected 未拒绝、legacy 字段未读取或前端仍发送 `expected_json`。

- [x] **步骤 5：修复 store 标注校验**

在 `core/eval_sampling/store.py` 引入：

```python
from evals.expected_contract import validate_expected_contract
```

修改 `label_candidate`：

```python
def label_candidate(db, case_id: str, expected_dict: dict, *, note: str | None = None):
    """标记候选：设置 expected_json 和 status=labeled。"""
    row = get_candidate(db, case_id)
    if not row:
        return None
    validate_expected_contract(row.suite, expected_dict)
    row.expected_json = json.dumps(expected_dict, ensure_ascii=False)
    row.status = "labeled"
    if note is not None:
        row.note = note
    row.updated_at = datetime.now()
    db.commit()
    return _candidate_dict(row)
```

- [x] **步骤 6：修复 Admin label 请求**

在 `api/admin_routes.py` 中扩展模型：

```python
class LabelRequest(BaseModel):
    expected: dict = Field(default_factory=dict)
    expected_json: Optional[dict] = None
    note: str = ""

    def normalized_expected(self) -> dict:
        return self.expected or self.expected_json or {}
```

修改 endpoint：

```python
    expected = body.normalized_expected()
    try:
        result = label_candidate(db, case_id, expected, note=body.note or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
```

审计信息使用 `expected.keys()`。

- [x] **步骤 7：修复 WebUI 请求字段**

在 `webui/src/features/evals/EvalsPage.jsx`：

```javascript
api.post(`/evals/candidates/${encodeURIComponent(caseId)}/label`, { expected: expectedJson })
```

- [x] **步骤 8：运行标注测试绿灯**

运行：

```bash
python -m pytest tests/test_eval_candidate_contract.py -k "label_candidate or expected_json" -v
python -m pytest tests/test_webui_admin_redesign.py::test_eval_candidate_label_posts_expected_field -v
```

预期：全部通过。

- [x] **步骤 9：提交任务 2**

运行：

```bash
git diff --check -- core/eval_sampling/store.py api/admin_routes.py webui/src/features/evals/EvalsPage.jsx tests/test_eval_candidate_contract.py tests/test_webui_admin_redesign.py
git add core/eval_sampling/store.py api/admin_routes.py webui/src/features/evals/EvalsPage.jsx tests/test_eval_candidate_contract.py tests/test_webui_admin_redesign.py
git commit -m "fix(评测): 修复候选标注契约"
```

验证记录：

- 红灯：`python -m pytest tests/test_eval_candidate_contract.py -k "label_candidate or expected_json" -v && python -m pytest tests/test_webui_admin_redesign.py::test_eval_candidate_label_posts_expected_field -v`，结果后端用例 `2 failed`；失败点为空 expected 未拒绝、API legacy `expected_json` 被吞掉。
- 绿灯：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_candidate_contract.py -k "label_candidate or expected_json" -v -p no:cacheprovider`，结果 `2 passed, 4 deselected, 21 warnings in 1.00s`。
- WebUI 静态守卫：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_webui_admin_redesign.py::test_eval_candidate_label_posts_expected_field -v -p no:cacheprovider`，结果 `1 passed, 1 warning in 0.47s`。
- 相关完整回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_candidate_contract.py -v -p no:cacheprovider`，结果 `6 passed, 21 warnings in 1.23s`。
- WebUI 完整静态回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_webui_admin_redesign.py -v -p no:cacheprovider`，结果 `17 passed, 1 warning in 0.71s`。

---

## 任务 3：Promote dry-run 与 dataset 目标

**文件：**
- 修改：`core/eval_sampling/store.py`
- 修改：`api/admin_routes.py`
- 测试：`tests/test_eval_candidate_contract.py`

- [x] **步骤 1：编写 promote 校验红灯测试**

在 `tests/test_eval_candidate_contract.py` 新增：

```python
def test_promote_candidate_rejects_unlabeled_empty_and_file_conflict(db_session, tmp_path, monkeypatch):
    from core.eval_sampling import store
    from core.eval_sampling.store import label_candidate, promote_candidate

    monkeypatch.setattr(store, "REPO_ROOT", tmp_path)
    _insert_candidate(db_session)

    with pytest.raises(ValueError, match="labeled"):
        promote_candidate(db_session, "cand_timing_gate_1")

    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})
    target = tmp_path / "evals" / "cases" / "regression" / "cand_timing_gate_1.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        promote_candidate(db_session, "cand_timing_gate_1")
```

- [x] **步骤 2：编写 dry-run 红灯测试**

```python
def test_promote_candidate_dry_run_does_not_write_or_change_status(db_session, tmp_path, monkeypatch):
    from core.eval_sampling import store
    from core.eval_sampling.store import get_candidate, label_candidate, plan_candidate_promotion

    monkeypatch.setattr(store, "REPO_ROOT", tmp_path)
    _insert_candidate(db_session)
    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})

    plan = plan_candidate_promotion(db_session, "cand_timing_gate_1", target_dataset="timing_gate")

    assert plan["target_dataset"] == "timing_gate"
    assert plan["path"].endswith("evals/cases/timing_gate/cand_timing_gate_1.json")
    assert not (tmp_path / "evals" / "cases" / "timing_gate" / "cand_timing_gate_1.json").exists()
    assert get_candidate(db_session, "cand_timing_gate_1").status == "labeled"
```

- [x] **步骤 3：运行红灯测试**

运行：

```bash
python -m pytest tests/test_eval_candidate_contract.py -k "promote_candidate" -v
```

预期：失败于缺少 `plan_candidate_promotion`、不拒绝冲突或 dry-run 仍写文件。

- [x] **步骤 4：新增 REPO_ROOT 和 dataset sanitizer**

在 `core/eval_sampling/store.py` 顶部增加：

```python
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
```

新增：

```python
def _safe_dataset_name(value: str) -> str:
    name = str(value or "regression").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError(f"invalid target_dataset: {value}")
    return name
```

需要同时 `import re`。

- [x] **步骤 5：实现晋升计划**

新增：

```python
def plan_candidate_promotion(db, case_id: str, *, target_dataset: str = "regression") -> dict:
    row = get_candidate(db, case_id)
    if not row:
        raise ValueError("candidate not found")
    if row.status != "labeled":
        raise ValueError("candidate must be labeled before promote")
    expected = _safe_json(row.expected_json, {})
    validate_expected_contract(row.suite, expected)

    dataset = _safe_dataset_name(target_dataset)
    target_dir = REPO_ROOT / "evals" / "cases" / dataset
    out_path = target_dir / f"{case_id}.json"
    if out_path.exists():
        raise ValueError(f"target case already exists: {out_path}")

    tags = _safe_json(row.tags_json, [])
    if "promoted" not in tags:
        tags = [*tags, "promoted"]

    case_data = {
        "id": case_id,
        "suite": row.suite,
        "description": row.description,
        "input": _safe_json(row.input_json, {}),
        "expected": expected,
        "tags": tags,
        "meta": {
            "origin": "eval_candidate",
            "source": row.source,
            "source_ref": row.source_ref or "",
            "fingerprint": row.fingerprint or "",
        },
    }
    return {
        "case_id": case_id,
        "suite": row.suite,
        "target_dataset": dataset,
        "path": str(out_path),
        "case": case_data,
    }
```

- [x] **步骤 6：改造 promote_candidate**

```python
def promote_candidate(db, case_id: str, *, target_dataset: str = "regression") -> str | None:
    try:
        plan = plan_candidate_promotion(db, case_id, target_dataset=target_dataset)
    except ValueError as e:
        if str(e) == "candidate not found":
            return None
        raise
    out_path = Path(plan["path"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plan["case"], indent=2, ensure_ascii=False), encoding="utf-8")

    row = get_candidate(db, case_id)
    row.status = "promoted"
    row.updated_at = datetime.now()
    db.commit()
    return str(out_path)
```

- [x] **步骤 7：扩展 Admin promote 请求**

在 `api/admin_routes.py` 增加：

```python
class PromoteRequest(BaseModel):
    target_dataset: str = "regression"
    dry_run: bool = False
```

修改 endpoint 支持 body 默认值：

```python
def eval_promote_candidate(
    case_id: str,
    request: Request,
    body: PromoteRequest | None = None,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    body = body or PromoteRequest()
    try:
        if body.dry_run:
            from core.eval_sampling.store import plan_candidate_promotion
            plan = plan_candidate_promotion(db, case_id, target_dataset=body.target_dataset)
            return {"dry_run": True, **plan}
        path = promote_candidate(db, case_id, target_dataset=body.target_dataset)
    except ValueError as e:
        raise HTTPException(400, str(e))
```

- [x] **步骤 8：运行 promote 测试绿灯**

运行：

```bash
python -m pytest tests/test_eval_candidate_contract.py -k "promote_candidate" -v
```

预期：全部通过。

- [x] **步骤 9：提交任务 3**

运行：

```bash
git diff --check -- core/eval_sampling/store.py api/admin_routes.py tests/test_eval_candidate_contract.py
git add core/eval_sampling/store.py api/admin_routes.py tests/test_eval_candidate_contract.py
git commit -m "feat(评测): 支持候选晋升预检"
```

验证记录：

- 红灯：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_candidate_contract.py -k "promote_candidate" -v -p no:cacheprovider`，结果 `4 failed, 6 deselected, 21 warnings in 6.35s`；失败点为文件冲突未拒绝、缺少 `plan_candidate_promotion`、`promote_candidate` 不接受 `target_dataset`、Admin dry-run 返回缺少 `dry_run`。
- 绿灯：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_candidate_contract.py -k "promote_candidate" -v -p no:cacheprovider`，结果 `4 passed, 6 deselected, 21 warnings in 1.24s`。
- 相关完整回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_candidate_contract.py -v -p no:cacheprovider`，结果 `10 passed, 21 warnings in 1.75s`。

---

## 任务 4：离线 candidates CLI

**文件：**
- 创建：`evals/candidates.py`
- 测试：`tests/test_eval_candidates_cli.py`

- [x] **步骤 1：编写 CLI helper 红灯测试**

创建 `tests/test_eval_candidates_cli.py`：

```python
import json

from tests.test_eval_candidate_contract import _insert_candidate


def test_export_candidates_writes_jsonl(db_session, tmp_path):
    from evals.candidates import export_candidates

    _insert_candidate(db_session)
    out = tmp_path / "candidates.jsonl"

    count = export_candidates(db_session, out, suite="timing_gate", status="candidate")

    assert count == 1
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["case_id"] == "cand_timing_gate_1"
    assert row["expected"] == {"needs_label": True}


def test_import_labels_updates_expected(db_session, tmp_path):
    from evals.candidates import import_labels

    _insert_candidate(db_session)
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps({
            "case_id": "cand_timing_gate_1",
            "expected": {"timing_action": "continue"},
            "note": "人工确认",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    result = import_labels(db_session, labels)

    assert result["updated"] == 1
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
python -m pytest tests/test_eval_candidates_cli.py -v
```

预期：失败于缺少 `evals.candidates`。

- [x] **步骤 3：实现 CLI helper**

创建 `evals/candidates.py`，核心函数：

```python
"""Eval candidates 离线导出、标注导入和晋升 CLI。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.eval_sampling.store import (
    label_candidate,
    list_candidates,
    plan_candidate_promotion,
    promote_candidate,
)


def _jsonl_rows(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


def export_candidates(db, out_path: str | Path, *, suite: str = "", status: str = "candidate") -> int:
    items, _ = list_candidates(db, suite=suite, status=status, limit=10000, offset=0)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )
    return len(items)


def import_labels(db, labels_path: str | Path) -> dict[str, int]:
    updated = 0
    for row in _jsonl_rows(labels_path):
        result = label_candidate(
            db,
            str(row["case_id"]),
            row.get("expected") or {},
            note=row.get("note"),
        )
        if result:
            updated += 1
    return {"updated": updated}


def promote_labeled(db, *, suite: str = "", target_dataset: str = "regression", apply: bool = False) -> dict:
    items, _ = list_candidates(db, suite=suite, status="labeled", limit=10000, offset=0)
    plans = []
    for item in items:
        if apply:
            path = promote_candidate(db, item["case_id"], target_dataset=target_dataset)
            plans.append({"case_id": item["case_id"], "path": path})
        else:
            plans.append(plan_candidate_promotion(db, item["case_id"], target_dataset=target_dataset))
    return {"count": len(plans), "items": plans}
```

- [x] **步骤 4：实现 CLI main**

在同一文件增加：

```python
def _open_db():
    from core.database import SessionLocal
    return SessionLocal()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    export_p = sub.add_parser("export")
    export_p.add_argument("--suite", default="")
    export_p.add_argument("--status", default="candidate")
    export_p.add_argument("--out", required=True)

    import_p = sub.add_parser("import-labels")
    import_p.add_argument("--labels", required=True)

    promote_p = sub.add_parser("promote")
    promote_p.add_argument("--suite", default="")
    promote_p.add_argument("--target-dataset", default="regression")
    promote_p.add_argument("--apply", action="store_true")
    promote_p.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    db = _open_db()
    try:
        if args.command == "export":
            count = export_candidates(db, args.out, suite=args.suite, status=args.status)
            print(f"exported={count} out={args.out}")
            return 0
        if args.command == "import-labels":
            result = import_labels(db, args.labels)
            print(f"updated={result['updated']}")
            return 0
        if args.command == "promote":
            result = promote_labeled(
                db,
                suite=args.suite,
                target_dataset=args.target_dataset,
                apply=bool(args.apply and not args.dry_run),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    finally:
        db.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **步骤 5：运行 CLI 测试绿灯**

运行：

```bash
python -m pytest tests/test_eval_candidates_cli.py -v
```

预期：全部通过。

- [x] **步骤 6：提交任务 4**

运行：

```bash
git diff --check -- evals/candidates.py tests/test_eval_candidates_cli.py
git add evals/candidates.py tests/test_eval_candidates_cli.py
git commit -m "feat(评测): 增加候选标注命令"
```

验证记录：

- 红灯：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_candidates_cli.py -v -p no:cacheprovider`，结果 `4 failed, 1 warning in 6.01s`；失败点为缺少 `evals.candidates`。
- 绿灯：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_candidates_cli.py -v -p no:cacheprovider`，结果 `4 passed, 1 warning in 0.87s`。
- 相关完整回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py -v -p no:cacheprovider`，结果 `14 passed, 21 warnings in 2.26s`。

---

## 任务 5：首个 per-capability 数据集

**文件：**
- 创建：`evals/cases/capability_model_routing/model_routing_stream_required_001.json`
- 创建：`evals/baselines/capability_model_routing.json`
- 测试：`tests/test_eval_baseline.py`

- [x] **步骤 1：编写 dataset / suite 语义红灯测试**

在 `tests/test_eval_baseline.py` 新增：

```python
def test_capability_dataset_uses_case_suite_as_runner():
    from evals.run import load_cases, run_suite

    cases = load_cases("capability_model_routing")

    assert cases
    assert {case.suite for case in cases} == {"model_routing"}
    report = run_suite("capability_model_routing")
    assert report.total == len(cases)
    assert report.failed == 0
```

红灯预期：缺少 `capability_model_routing` 数据集。

- [x] **步骤 2：运行红灯测试**

运行：

```bash
python -m pytest tests/test_eval_baseline.py::test_capability_dataset_uses_case_suite_as_runner -v
```

预期：失败于 `assert cases`。

- [x] **步骤 3：新增能力数据集 case**

创建 `evals/cases/capability_model_routing/model_routing_stream_required_001.json`：

```json
{
  "id": "model_routing_stream_required_001",
  "suite": "model_routing",
  "description": "流式请求必须过滤不支持 stream 的模型",
  "input": {
    "stream": true,
    "provider": "new-api",
    "models": [
      {
        "id": "text-fast-no-stream",
        "provider": "new-api",
        "tier": "fast",
        "enabled": true,
        "supports_stream": false,
        "intelligence": 8,
        "cost_input_1m": 0
      },
      {
        "id": "text-fast-stream",
        "provider": "new-api",
        "tier": "fast",
        "enabled": true,
        "supports_stream": true,
        "intelligence": 7,
        "cost_input_1m": 0
      }
    ]
  },
  "expected": {
    "model_used": "text-fast-stream",
    "should_call_auto_routing": true
  },
  "tags": ["capability:model_routing", "stream", "routing"]
}
```

- [x] **步骤 4：生成 baseline**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run --suite capability_model_routing
```

预期输出：

```text
Suite: capability_model_routing  total=1  passed=1  failed=0  pass_rate=100.0%
```

创建 `evals/baselines/capability_model_routing.json`：

```json
{
  "suite": "capability_model_routing",
  "total": 1,
  "passed": 1,
  "failed": 0,
  "pass_rate": 1.0,
  "failed_cases": []
}
```

- [x] **步骤 5：运行 dataset gate**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run --suite capability_model_routing --baseline evals/baselines/capability_model_routing.json --min-pass-rate 1.0 --max-new-failures 0
```

预期：`Gate passed`。

- [x] **步骤 6：运行测试绿灯**

运行：

```bash
python -m pytest tests/test_eval_baseline.py::test_capability_dataset_uses_case_suite_as_runner -v
```

预期：通过。

- [x] **步骤 7：提交任务 5**

运行：

```bash
git diff --check -- evals/cases/capability_model_routing/model_routing_stream_required_001.json evals/baselines/capability_model_routing.json tests/test_eval_baseline.py
git add evals/cases/capability_model_routing/model_routing_stream_required_001.json evals/baselines/capability_model_routing.json tests/test_eval_baseline.py
git commit -m "test(评测): 增加模型路由能力数据集"
```

验证记录：

- 红灯：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_baseline.py::test_capability_dataset_uses_case_suite_as_runner -v -p no:cacheprovider`，结果 `1 failed, 1 warning in 5.92s`；失败点为 `load_cases("capability_model_routing")` 返回空列表。
- Suite：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run --suite capability_model_routing`，结果 `total=1 passed=1 failed=0 pass_rate=100.0%`。
- Gate：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run --suite capability_model_routing --baseline evals/baselines/capability_model_routing.json --min-pass-rate 1.0 --max-new-failures 0`，结果 `Gate passed`。
- 绿灯：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_baseline.py::test_capability_dataset_uses_case_suite_as_runner -v -p no:cacheprovider`，结果 `1 passed, 1 warning in 0.75s`。
- 相关完整回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_baseline.py -v -p no:cacheprovider`，结果 `11 passed, 1 warning in 1.00s`。

---

## 任务 6：文档收口

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/eval-dataset-labeling.md`

- [x] **步骤 1：同步 eval 操作手册**

在 `docs/evals.md` 增加：

- candidates export / import-labels / promote 命令。
- `dataset` 与 `suite` 的区别。
- `expected` 可评分字段要求。
- `capability_model_routing` 本地 gate 命令。
- RAG benchmark 不并入通用 EvalCase 的边界。

- [x] **步骤 2：同步路线状态**

更新 `docs/todo.md` 路线项 8：

- 记录 P4-1 已建立候选标注契约。
- 记录首个 `capability_model_routing` 数据集和 baseline。
- 将更大的 Admin 工作台、RAG 标注闭环和更多 suite PR gate 留在 P4 后续阶段。

- [x] **步骤 3：同步 walkthrough**

更新 `docs/plan_walkthrough.md`：

- P4-1 设计文档提交：`docs(评测): 设计标注闭环`。
- P4-1 实现计划路径：`.Codex/plans/eval-dataset-labeling.md`。
- 记录每个任务提交和验证结果。

- [x] **步骤 4：运行文档扫描**

运行：

```bash
rg -n "T[O]DO|待[定]|后续[实]现|类似[任]务|添加[适]当|为上[述]" docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/eval-dataset-labeling.md
python - <<'PY'
from pathlib import Path

paths = [
    Path("docs/evals.md"),
    Path("docs/todo.md"),
    Path("docs/plan_walkthrough.md"),
    Path(".Codex/plans/eval-dataset-labeling.md"),
]
bad = []
for path in paths:
    text = path.read_text(encoding="utf-8")
    if "\ufffd" in text:
        bad.append(str(path))
if bad:
    raise SystemExit("U+FFFD found in: " + ", ".join(bad))
PY
git diff --check -- docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/eval-dataset-labeling.md
```

预期：三个命令都无有效错误输出；`rg` 无匹配时退出码为 1，属于通过。

- [x] **步骤 5：提交任务 6**

运行：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/eval-dataset-labeling.md
git commit -m "docs(评测): 收口标注闭环状态"
```

验证记录：

- 文档占位词扫描：`rg -n "T[O]DO|待[定]|后续[实]现|类似[任]务|添加[适]当|为上[述]" docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/eval-dataset-labeling.md`，结果无匹配；`rg` 退出码为 1，符合预期。
- U+FFFD 扫描：`python - <<'PY' ... PY`，结果无输出。
- Diff 空白检查：`git diff --check -- docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/eval-dataset-labeling.md`，结果无输出。
- 评测定向组合：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_baseline.py tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py tests/test_timing_gate_prompt_policy.py -v -p no:cacheprovider`，结果 `33 passed, 21 warnings in 3.36s`。
- TimingGate 门禁：`bash scripts/run_timing_gate_gate.sh`，结果 `Suite: timing_gate total=18 passed=18 failed=0 pass_rate=100.0%`，`Gate passed`。
- 能力数据集门禁：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run --suite capability_model_routing --baseline evals/baselines/capability_model_routing.json --min-pass-rate 1.0 --max-new-failures 0`，结果 `Suite: capability_model_routing total=1 passed=1 failed=0 pass_rate=100.0%`，`Gate passed`。
- 全量回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`，结果 `1338 passed, 6 skipped, 139 warnings in 98.50s`。

---

## 任务 7：最终验证与交接

**文件：**
- 修改：`.Codex/plans/eval-dataset-labeling.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：运行候选闭环定向回归**

运行：

```bash
python -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py -v
```

预期：全部通过。

- [ ] **步骤 2：运行 eval 相关回归**

运行：

```bash
python -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py -v
bash scripts/run_timing_gate_gate.sh
PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run --suite capability_model_routing --baseline evals/baselines/capability_model_routing.json --min-pass-rate 1.0 --max-new-failures 0
```

预期：pytest 通过，两个 gate 均输出 `Gate passed`。

- [ ] **步骤 3：运行 WebUI 静态回归**

运行：

```bash
python -m pytest tests/test_webui_admin_redesign.py -v
```

预期：通过。

- [ ] **步骤 4：运行全量验证**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failures。

- [ ] **步骤 5：同步最终验证记录**

更新 `.Codex/plans/eval-dataset-labeling.md` 和 `docs/plan_walkthrough.md`：

- 填入定向回归结果。
- 填入两个 gate 结果。
- 填入全量测试结果。
- 标记 P4-1 当前阶段完成。

- [ ] **步骤 6：最终提交**

运行：

```bash
git diff --check -- .Codex/plans/eval-dataset-labeling.md docs/plan_walkthrough.md
git add .Codex/plans/eval-dataset-labeling.md docs/plan_walkthrough.md
git commit -m "docs(计划): 完成标注闭环验证"
```

---

## 子 agent 分工建议

- Worker A（契约）：任务 1，只写 `evals/expected_contract.py`、`evals/schema.py`、`evals/scorers.py`、`evals/runners/sticker_runner.py`、`tests/test_eval_candidate_contract.py`。
- Worker B（标注接口）：任务 2，只写 `core/eval_sampling/store.py`、`api/admin_routes.py`、`webui/src/features/evals/EvalsPage.jsx` 和对应测试。
- Worker C（CLI）：任务 4，只写 `evals/candidates.py`、`tests/test_eval_candidates_cli.py`。
- 主线程：任务 3、任务 5、任务 6、最终验证和所有提交前审查。

共享文件 `core/eval_sampling/store.py`、`evals/scorers.py`、`api/admin_routes.py` 同一时间只允许一个 owner 编辑；并行时由主线程先确认前一任务已提交。
