# TimingGate 调参提案运营链路实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把已完成的只读 TimingGate proposal 链路接入真实 run-scoped audit、`final_timing_action` 人工 truth、候选参数治理和 record-only 审核状态，同时保持不自动应用参数、不更新 baseline、不改变 gate。

**架构：** 以 run-scoped TimingSignal audit 为原始证据，JSONL sidecar 合并人工最终动作 truth，proposal 只读聚合 readiness、候选参数、simulation 和审核状态。Admin API 只写审核记录，WebUI 只展示和记录人工审核结论；任何证据不足都通过稳定 blocking reason 表达。

**技术栈：** Python 标准库、pytest、JSON / JSONL artifact、FastAPI Admin 路由、SQLAlchemy `AdminAuditLog`、React WebUI 静态测试、现有 `evals.timing_signal_audit` / `evals.timing_tuning_proposal` / `evals.timing_score_simulation`。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-21-timing-tuning-operations-design.md`
- 已完成前置：
  - `evals.timing_tuning_proposal` 第一版只读报告。
  - `evals.timing_score_simulation` 第一版 eval case what-if 模拟。
  - TimingSignal audit latest / dated / run-scoped artifact。
  - Admin 只读 proposal API。
  - WebUI「调参提案」只读 tab。
- 本计划路径：`.Codex/plans/timing-tuning-operations.md`

## 进度总览

- [x] 设计：写入 `docs/superpowers/specs/2026-06-21-timing-tuning-operations-design.md`，提交 `4f7d13a docs(时机): 设计调参提案运营链路`。
- [x] 任务 1：TimingSignal audit 支持 run source 与 `final_timing_action` 合同。
- [x] 任务 2：proposal 收紧 run-scoped 输入与候选参数治理。
- [x] 任务 3：simulation 标识真实 audit 样本来源并守住 `timing_input`。
- [x] 任务 4：Admin 增加 record-only proposal review API。
- [ ] 任务 5：WebUI 展示审核状态并提供记录型审核入口。
- [ ] 任务 6：文档收口、计划勾选和最终验证。

## 子 agent 分工

推荐使用 5 个子 agent 并行执行只读或互不冲突的实现任务。主线程负责审查 diff、解决接口冲突、跑全量测试和提交。

- Agent A owner：`core/eval_sampling/timing_signal_audit.py`、`evals/timing_signal_audit.py`、`scripts/run_timing_signal_audit_periodic.sh`、`tests/test_timing_signal_audit.py`、`tests/test_timing_signal_audit_periodic.py`。
- Agent B owner：`evals/timing_tuning_proposal.py`、`tests/test_timing_tuning_proposal.py`。
- Agent C owner：`evals/timing_score_simulation.py`、`tests/test_timing_score_simulation.py`、必要时追加 `tests/test_timing_tuning_proposal.py` 的 integration 用例。
- Agent D owner：`api/admin_routes.py`、`tests/test_timing_tuning_proposal_admin.py`。
- Agent E owner：`webui/src/features/evals/EvalsPage.jsx`、`tests/test_webui_admin_redesign.py`。

禁止子 agent 修改 owner 之外的文件。若任务需要跨 owner 改动，返回主线程协调。

## 文件结构

- 修改：`core/eval_sampling/timing_signal_audit.py`
  - 增加 `FINAL_TIMING_ACTIONS` 常量和 truth 校验 helper。
  - `merge_timing_signal_labels()` 合并 sidecar 后保留合法 `final_timing_action`，非法值由 proposal readiness 识别。
- 修改：`evals/timing_signal_audit.py`
  - `run_audit()` 与 `run_labeled_audit()` 在 `source` 中记录 `run_id`。
  - CLI 增加 `--run-id`，默认从 `TIMING_SIGNAL_AUDIT_RUN_ID` 或空字符串读取。
- 修改：`scripts/run_timing_signal_audit_periodic.sh`
  - 调用 CLI 时传入 `--run-id "${PERIODIC_RUN_ID:-}"`。
- 修改：`tests/test_timing_signal_audit.py`
  - 覆盖 sidecar 合并 `final_timing_action`。
  - 覆盖非法 truth 被保留但不计为合法 readiness。
- 修改：`tests/test_timing_signal_audit_periodic.py`
  - 覆盖 skipped 报告也带 `source.run_id`。
- 修改：`evals/timing_tuning_proposal.py`
  - 增加 `--run-id`、默认 params 路径、run-scoped audit path 解析、candidate governance 和新增 blocking code。
- 修改：`tests/test_timing_tuning_proposal.py`
  - 覆盖 explicit latest、audit run mismatch、truth 非法、候选重复 ID、空 diff、证据引用透传。
- 修改：`evals/timing_score_simulation.py`
  - simulation 输出 `sources`。
  - audit sample flips 增加 `source_type`、`log_id`、`signal_name`。
  - 缺 `timing_input` 时只统计 skipped，不用 `text_preview` 模拟。
- 修改：`tests/test_timing_score_simulation.py`
  - 覆盖 audit sample replay 和 missing replay input。
- 修改：`api/admin_routes.py`
  - 新增 proposal review GET / POST。
  - POST 写 `AdminAuditLog(action="review_timing_tuning_proposal")`，不修改 proposal report。
- 修改：`tests/test_timing_tuning_proposal_admin.py`
  - 覆盖 review missing、review POST、invalid decision、只写 audit log。
- 修改：`webui/src/features/evals/EvalsPage.jsx`
  - 展示 review 状态。
  - 增加 record-only 表单。
  - 不提供生产应用按钮。
- 修改：`tests/test_webui_admin_redesign.py`
  - 静态断言 review endpoint 和 decision 文案存在。
  - 静态断言「应用参数」「更新 baseline」「写入配置」仍不存在于可执行按钮文案。
- 修改：`docs/evals.md`
  - 记录 run-scoped proposal、sidecar truth、review API 和禁止动作。
- 修改：`docs/todo.md`
  - 同步路线项 8 / 10 的下一步状态。
- 修改：`docs/plan_walkthrough.md`
  - 记录本计划、阶段提交和验证结果。

## 接口约定

### `final_timing_action`

合法值：

```python
FINAL_TIMING_ACTIONS = {"continue", "wait", "no_reply"}
```

sidecar JSONL 单行：

```json
{"log_id":101,"signal_name":"s_ack","final_timing_action":"continue","label":"false_positive","note":"后半句继续提出请求","annotator":"human-a"}
```

### Proposal CLI

```bash
python -B -m evals.timing_tuning_proposal \
  --run-id 20260621_120000_local \
  --manifest evals/reports/runs/20260621_120000_local/manifest.json \
  --trends evals/reports/artifact_trends_latest.json \
  --analysis evals/reports/tuning_analysis_latest.json \
  --params tmp/timing_gate/param_candidates.json \
  --out evals/reports/timing_tuning_proposal_latest.json
```

### 新增 blocking code

- `explicit_latest_audit`
- `audit_not_run_scoped`
- `audit_run_mismatch`
- `invalid_action_truth`
- `missing_replay_input`
- `duplicate_candidate_id`
- `empty_candidate_param_diff`
- `missing_candidate_id`

### Review API

`GET /api/v1/admin/evals/timing-tuning/proposal/review` 返回：

```json
{
  "exists": true,
  "proposal_sha256": "abc123",
  "latest_review": {
    "decision": "needs_data",
    "reason_code": "missing_action_truth",
    "note": "需要补人工 final_timing_action",
    "reviewer": "admin",
    "created_at": "2026-06-21T12:00:00"
  }
}
```

`POST /api/v1/admin/evals/timing-tuning/proposal/reviews` 请求：

```json
{
  "decision": "needs_data",
  "reason_code": "missing_action_truth",
  "note": "需要补人工 final_timing_action",
  "reviewer": "admin"
}
```

允许 decision：

- `needs_data`
- `rejected`
- `approved_for_manual_experiment`
- `reviewed_no_change`

## 任务 1：TimingSignal audit 支持 run source 与 truth 合同

**文件：**

- 修改：`core/eval_sampling/timing_signal_audit.py`
- 修改：`evals/timing_signal_audit.py`
- 修改：`scripts/run_timing_signal_audit_periodic.sh`
- 测试：`tests/test_timing_signal_audit.py`
- 测试：`tests/test_timing_signal_audit_periodic.py`

- [x] **步骤 1：编写 sidecar truth 合并测试**

在 `tests/test_timing_signal_audit.py` 新增：

```python
def test_timing_signal_audit_merges_final_action_truth_from_labels():
    from core.eval_sampling.timing_signal_audit import merge_timing_signal_labels

    samples = [{"log_id": 101, "signal_name": "s_ack", "label": "unknown"}]
    labels = [
        {
            "log_id": 101,
            "signal_name": "s_ack",
            "final_timing_action": "continue",
            "label": "false_positive",
            "note": "后半句继续提出请求",
            "annotator": "human-a",
        }
    ]

    merged = merge_timing_signal_labels(samples, labels)

    assert merged == [
        {
            "log_id": 101,
            "signal_name": "s_ack",
            "label": "false_positive",
            "final_timing_action": "continue",
            "note": "后半句继续提出请求",
            "annotator": "human-a",
        }
    ]
```

- [x] **步骤 2：编写 run_id source 测试**

在 `tests/test_timing_signal_audit.py` 新增：

```python
def test_timing_signal_audit_run_labeled_audit_records_run_id(tmp_path):
    import json
    from evals.timing_signal_audit import run_labeled_audit

    src = tmp_path / "audit.json"
    labels = tmp_path / "labels.jsonl"
    out = tmp_path / "out.json"
    src.write_text(
        json.dumps(
            {
                "samples": [{"log_id": 101, "signal_name": "s_ack"}],
                "source": {"run_id": "run_1"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    labels.write_text(
        '{"log_id":101,"signal_name":"s_ack","final_timing_action":"continue"}\n',
        encoding="utf-8",
    )

    report = run_labeled_audit(
        input_report_path=src,
        labels_path=labels,
        output_path=out,
        run_id="run_1",
    )

    assert report["source"]["run_id"] == "run_1"
    assert report["samples"][0]["final_timing_action"] == "continue"
```

- [x] **步骤 3：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_timing_signal_audit.py::test_timing_signal_audit_merges_final_action_truth_from_labels tests/test_timing_signal_audit.py::test_timing_signal_audit_run_labeled_audit_records_run_id -q -p no:cacheprovider
```

预期：第二个测试失败，报错包含 `unexpected keyword argument 'run_id'` 或 `KeyError: 'run_id'`。

- [x] **步骤 4：实现 run_id 参数和 source 字段**

在 `evals/timing_signal_audit.py` 中调整函数签名与 source：

```python
def run_audit(
    db,
    *,
    output_path: str | Path = DEFAULT_REPORT,
    after_id: int = 0,
    limit: int = 200,
    signal_names: tuple[str, ...] = SIGNAL_NAMES,
    db_path: str = "",
    run_id: str = "",
) -> dict:
    ...
    "source": {
        "db": db_path,
        "run_id": run_id,
        "after_id": after_id,
        "limit": limit,
        "signals": list(signal_names),
    }
```

同文件 `run_labeled_audit()` 增加 `run_id: str = ""`，source 中写入：

```python
"run_id": run_id or str(safe_source.get("run_id") or "")
```

其中 `safe_source` 从 input report 读取；如果不想重复读文件，可让 `_load_report_samples()` 返回 `(samples, source)`，并同步更新调用点。

- [x] **步骤 5：实现 CLI `--run-id`**

在 `evals/timing_signal_audit.py` parser 增加：

```python
parser.add_argument("--run-id", default="", help="周期运行 ID，写入 report source.run_id")
```

调用 `run_audit()` 和 `run_labeled_audit()` 时传入 `run_id=args.run_id`。

- [x] **步骤 6：让周期脚本传 run_id**

在 `scripts/run_timing_signal_audit_periodic.sh` 的 Python CLI 调用中加入：

```bash
--run-id "${PERIODIC_RUN_ID:-}"
```

缺 DB 的 skipped payload source 中也加入：

```python
"run_id": os.environ.get("PERIODIC_RUN_ID", ""),
```

- [x] **步骤 7：运行任务测试**

运行：

```bash
python -B -m pytest tests/test_timing_signal_audit.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 8：提交任务 1**

运行：

```bash
python -m pytest tests/ -v
git add core/eval_sampling/timing_signal_audit.py evals/timing_signal_audit.py scripts/run_timing_signal_audit_periodic.sh tests/test_timing_signal_audit.py tests/test_timing_signal_audit_periodic.py
git commit -m "feat(评测): 记录时机审计运行来源"
```

## 任务 2：Proposal 收紧 run-scoped 输入与候选参数治理

**文件：**

- 修改：`evals/timing_tuning_proposal.py`
- 测试：`tests/test_timing_tuning_proposal.py`

- [x] **步骤 1：编写拒绝 explicit latest 的测试**

在 `tests/test_timing_tuning_proposal.py` 新增：

```python
def test_proposal_rejects_explicit_latest_timing_audit(tmp_path):
    from evals.timing_tuning_proposal import resolve_proposal_timing_audit_path

    latest = tmp_path / "timing_signal_audit_latest.json"
    latest.write_text("{}", encoding="utf-8")

    resolved, blocking = resolve_proposal_timing_audit_path(
        _manifest(str(latest)),
        latest,
        run_id="run_1",
    )

    assert resolved is None
    assert blocking[0]["code"] == "explicit_latest_audit"
```

调整 helper 预期：`resolve_proposal_timing_audit_path()` 从返回 `Path | None` 升级为 `(Path | None, list[dict])`。

- [x] **步骤 2：编写 run mismatch 与候选治理测试**

在 `tests/test_timing_tuning_proposal.py` 新增：

```python
def test_proposal_blocks_audit_run_mismatch_and_invalid_truth():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    report = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(
            samples=[{"log_id": 1, "signal_name": "s_ack", "final_timing_action": "maybe"}],
            source={"mode": "sampled", "run_id": "other_run"},
        ),
        baseline={"suite": "timing_gate"},
        params=_params(),
        source_paths={"timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json"},
    )

    assert "audit_run_mismatch" in _reason_codes(report)
    assert "invalid_action_truth" in _reason_codes(report)
```

再新增候选治理测试：

```python
def test_proposal_candidate_governance_blocks_duplicate_id_and_empty_diff():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    params = _params()
    params["candidates"].append({
        "id": "ack_threshold_soften_v1",
        "description": "重复候选",
        "scope": "timing_score",
        "param_diff": {},
        "risk_level": "low",
        "expected_effect": "无",
        "evidence_refs": [{"type": "timing_signal_audit_sample", "log_id": 101}],
    })

    report = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(samples=[{"final_timing_action": "continue"}], source={"mode": "sampled", "run_id": "run_1"}),
        baseline={"suite": "timing_gate"},
        params=params,
        source_paths={"timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json"},
    )

    assert "duplicate_candidate_id" in _reason_codes(report)
    assert "empty_candidate_param_diff" in _reason_codes(report)
    assert report["candidate_sets"][1]["expected_effect"] == "无"
    assert report["candidate_sets"][1]["evidence_refs"] == [
        {"type": "timing_signal_audit_sample", "log_id": 101}
    ]
```

- [x] **步骤 3：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_timing_tuning_proposal.py -q -p no:cacheprovider
```

预期：新增测试失败，原因是函数签名、blocking code 和透传字段尚未实现。

- [x] **步骤 4：实现 path resolution 合同**

在 `evals/timing_tuning_proposal.py` 中新增：

```python
DEFAULT_PARAMS = Path("tmp/timing_gate/param_candidates.json")

def _is_latest_audit_path(path: Path) -> bool:
    return path.name == "timing_signal_audit_latest.json"

def _is_run_scoped_audit_path(path: Path, run_id: str) -> bool:
    normalized = path.as_posix()
    return normalized.endswith(f"evals/reports/runs/{run_id}/timing_signal_audit.json")
```

把 `resolve_proposal_timing_audit_path()` 改为返回 `(path, blocking)`，显式 latest 返回 `None` 和 `explicit_latest_audit`，manifest 中非 run-scoped 返回 `audit_not_run_scoped`。

- [x] **步骤 5：实现 truth 和 run mismatch readiness**

新增 helper：

```python
FINAL_TIMING_ACTIONS = {"continue", "wait", "no_reply"}

def _truth_stats(payload: dict[str, Any] | None) -> dict[str, int]:
    stats = {"valid": 0, "invalid": 0}
    ...
    return stats
```

`build_timing_tuning_proposal()` 中：

```python
audit_run_id = str((timing_audit.get("source") or {}).get("run_id") or "")
manifest_run_id = str((manifest or {}).get("run_id") or "")
if manifest_run_id and audit_run_id and manifest_run_id != audit_run_id:
    blocking.append(_reason("audit_run_mismatch", "manifest run_id 与 TimingSignal audit run_id 不一致"))
```

truth stats 为 `valid == 0` 时保留 `missing_action_truth`；`invalid > 0` 时增加 `invalid_action_truth`。

- [x] **步骤 6：实现候选治理**

`parse_candidate_sets()` 增加：

```python
seen_ids: set[str] = set()
...
if not candidate_id:
    blocking.append(_reason("missing_candidate_id", "候选参数缺少 id"))
elif candidate_id in seen_ids:
    blocking.append(_reason("duplicate_candidate_id", "候选参数 id 重复", candidate_id=candidate_id))
seen_ids.add(candidate_id)
if not param_diff:
    blocking.append(_reason("empty_candidate_param_diff", "候选参数 param_diff 不能为空", candidate_id=candidate_id))
```

`candidate_sets[]` 透传：

```python
"expected_effect": str(item.get("expected_effect") or ""),
"evidence_refs": item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else [],
```

- [x] **步骤 7：更新 CLI 默认 params 和调用点**

`build_parser()` 中：

```python
parser.add_argument("--run-id", default="")
parser.add_argument("--params", default=str(DEFAULT_PARAMS))
```

CLI 读取 audit path：

```python
audit_path, path_blocking = resolve_proposal_timing_audit_path(
    manifest,
    args.timing_audit or None,
    run_id=args.run_id or str((manifest or {}).get("run_id") or ""),
)
```

把 `path_blocking` 传入 `build_timing_tuning_proposal(extra_blocking=path_blocking)`，或在构建前合并到 `source_paths` 后由 build 函数生成相同 blocking。推荐新增 `extra_blocking`，保持 path 解析和 readiness 归因清楚。

- [x] **步骤 8：运行任务测试**

运行：

```bash
python -B -m pytest tests/test_timing_tuning_proposal.py tests/test_periodic_tuning_analysis.py tests/test_eval_artifact_trends.py -q -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 9：提交任务 2**

运行：

```bash
python -m pytest tests/ -v
git add evals/timing_tuning_proposal.py tests/test_timing_tuning_proposal.py
git commit -m "feat(评测): 收紧调参提案输入合同"
```

## 任务 3：Simulation 标识真实样本来源并守住 `timing_input`

**文件：**

- 修改：`evals/timing_score_simulation.py`
- 修改：`evals/timing_tuning_proposal.py`
- 测试：`tests/test_timing_score_simulation.py`
- 测试：`tests/test_timing_tuning_proposal.py`

- [x] **步骤 1：编写 audit sample simulation 测试**

在 `tests/test_timing_score_simulation.py` 新增：

```python
def test_simulation_replays_audit_sample_with_timing_input():
    from evals.timing_score_simulation import simulate_timing_candidates

    audit_samples = [
        {
            "log_id": 101,
            "signal_name": "s_ack",
            "final_timing_action": "continue",
            "timing_input": {
                "text": "好的，帮我查下明天安排",
                "is_group": True,
                "is_at_bot": True,
                "trigger_reason": "at_bot",
            },
        }
    ]
    candidates = [{"id": "ack_v1", "param_diff": {"s_ack": 0.5}, "risk_level": "medium"}]

    report = simulate_timing_candidates([], candidates, audit_samples=audit_samples)

    assert report["sources"]["audit_sample_count"] == 1
    assert report["sources"]["skipped_audit_sample_count"] == 0
    assert all(item["source_type"] == "timing_signal_audit_sample" for item in report["flips"])
```

再新增缺 input 测试：

```python
def test_simulation_skips_audit_sample_without_timing_input():
    from evals.timing_score_simulation import simulate_timing_candidates

    report = simulate_timing_candidates(
        [],
        [{"id": "ack_v1", "param_diff": {"s_ack": 0.5}}],
        audit_samples=[{"log_id": 101, "signal_name": "s_ack", "text_preview": "好的"}],
    )

    assert report["sources"]["audit_sample_count"] == 0
    assert report["sources"]["skipped_audit_sample_count"] == 1
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_timing_score_simulation.py -q -p no:cacheprovider
```

预期：新增测试失败，报错包含 `unexpected keyword argument 'audit_samples'` 或缺 `sources`。

- [x] **步骤 3：实现 audit sample 输入**

在 `evals/timing_score_simulation.py` 中把函数签名改为：

```python
def simulate_timing_candidates(
    cases: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    audit_samples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
```

新增 `sources`：

```python
sources = {
    "eval_case_count": len(cases),
    "audit_sample_count": 0,
    "skipped_audit_sample_count": 0,
}
```

只对含 `timing_input` 的 sample 调用已有 `_decision_kwargs()` 等价 helper。生成 flip 时增加：

```python
"source_type": "timing_signal_audit_sample",
"log_id": sample.get("log_id"),
"signal_name": sample.get("signal_name"),
```

- [x] **步骤 4：proposal 注入 audit samples**

在 `evals/timing_tuning_proposal.py` CLI 中：

```python
audit_samples = (
    timing_audit.get("samples")
    if isinstance(timing_audit, dict) and isinstance(timing_audit.get("samples"), list)
    else []
)
simulation = simulate_timing_candidates(
    load_timing_cases(cases_path),
    raw_candidates,
    audit_samples=audit_samples,
)
```

如果 `timing_audit["samples"]` 中存在合法 `final_timing_action` 但缺 `timing_input`，`build_timing_tuning_proposal()` 增加 `missing_replay_input` blocking。

- [x] **步骤 5：运行任务测试**

运行：

```bash
python -B -m pytest tests/test_timing_score_simulation.py tests/test_timing_tuning_proposal.py -q -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 6：提交任务 3**

运行：

```bash
python -m pytest tests/ -v
git add evals/timing_score_simulation.py evals/timing_tuning_proposal.py tests/test_timing_score_simulation.py tests/test_timing_tuning_proposal.py
git commit -m "feat(评测): 纳入时机审计样本模拟"
```

## 任务 4：Admin record-only 审核 API

**文件：**

- 修改：`api/admin_routes.py`
- 测试：`tests/test_timing_tuning_proposal_admin.py`

- [x] **步骤 1：编写 review GET / POST 测试**

在 `tests/test_timing_tuning_proposal_admin.py` 新增：

```python
def test_timing_tuning_proposal_review_records_admin_audit(
    client,
    db_session,
    monkeypatch,
    tmp_path,
):
    import json
    from api import admin_routes
    from core.database import AdminAuditLog

    report = tmp_path / "proposal.json"
    report.write_text(
        json.dumps(
            {
                "proposal_version": 1,
                "generated_at": "2026-06-21T12:00:00",
                "source": {"run_id": "run_1"},
                "readiness": {"ready": False, "blocking_reasons": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT", report, raising=False)

    response = client.post(
        "/api/v1/admin/evals/timing-tuning/proposal/reviews",
        headers=_auth_header(),
        json={
            "decision": "needs_data",
            "reason_code": "missing_action_truth",
            "note": "需要补人工 truth",
            "reviewer": "admin",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"] == "needs_data"
    audit = db_session.query(AdminAuditLog).filter_by(action="review_timing_tuning_proposal").one()
    detail = json.loads(audit.detail_json)
    assert detail["decision"] == "needs_data"
    assert detail["report_path"] == str(report)
```

再新增 invalid decision 测试：

```python
def test_timing_tuning_proposal_review_rejects_invalid_decision(client, monkeypatch, tmp_path):
    from api import admin_routes

    report = tmp_path / "proposal.json"
    report.write_text('{"proposal_version":1}', encoding="utf-8")
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT", report, raising=False)

    response = client.post(
        "/api/v1/admin/evals/timing-tuning/proposal/reviews",
        headers=_auth_header(),
        json={"decision": "apply_now", "reason_code": "x", "note": "", "reviewer": "admin"},
    )

    assert response.status_code == 422
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_timing_tuning_proposal_admin.py -q -p no:cacheprovider
```

预期：新增测试失败，POST 返回 404 或 405。

- [x] **步骤 3：实现 request model 和 hash helper**

在 `api/admin_routes.py` 中新增：

```python
TIMING_TUNING_REVIEW_DECISIONS = {
    "needs_data",
    "rejected",
    "approved_for_manual_experiment",
    "reviewed_no_change",
}

class TimingTuningProposalReviewRequest(BaseModel):
    decision: str
    reason_code: str = ""
    note: str = ""
    reviewer: str = ""
```

hash helper：

```python
def _proposal_sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()
```

- [x] **步骤 4：实现 POST**

新增 endpoint：

```python
@router.post("/evals/timing-tuning/proposal/reviews")
def eval_timing_tuning_proposal_review(
    payload: TimingTuningProposalReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    if payload.decision not in TIMING_TUNING_REVIEW_DECISIONS:
        raise HTTPException(status_code=422, detail="invalid review decision")
    ...
```

写入 `AdminAuditLog`：

```python
row = AdminAuditLog(
    action="review_timing_tuning_proposal",
    target_type="timing_tuning_proposal",
    target_id=proposal_sha256,
    detail_json=json.dumps(detail, ensure_ascii=False),
    ip_address=request.client.host if request.client else "",
)
db.add(row)
db.commit()
```

- [x] **步骤 5：实现 GET**

GET 从 `AdminAuditLog` 查询最新 `action="review_timing_tuning_proposal"` 且 `target_id=proposal_sha256` 的记录：

```python
@router.get("/evals/timing-tuning/proposal/review")
def eval_timing_tuning_proposal_review_state(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    ...
```

缺 proposal report 时返回 `exists=false` 和 `proposal_report_missing`。

- [x] **步骤 6：运行任务测试**

运行：

```bash
python -B -m pytest tests/test_timing_tuning_proposal_admin.py tests/test_eval_candidate_contract.py::test_candidate_batch_audit_apply_writes_single_audit_log -q -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 7：提交任务 4**

运行：

```bash
python -m pytest tests/ -v
git add api/admin_routes.py tests/test_timing_tuning_proposal_admin.py
git commit -m "feat(评测): 记录调参提案人工审核"
```

## 任务 5：WebUI 展示审核状态并提供记录入口

**文件：**

- 修改：`webui/src/features/evals/EvalsPage.jsx`
- 测试：`tests/test_webui_admin_redesign.py`

- [ ] **步骤 1：编写静态测试**

在 `tests/test_webui_admin_redesign.py` 的 `test_evals_page_exposes_timing_tuning_proposal_report()` 中追加：

```python
    assert "/evals/timing-tuning/proposal/review" in source
    assert "/evals/timing-tuning/proposal/reviews" in source
    assert "approved_for_manual_experiment" in source
    assert "进入人工实验" in source
    assert "应用参数" not in source
    assert "更新 baseline" not in source
    assert "写入配置" not in source
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_webui_admin_redesign.py::test_evals_page_exposes_timing_tuning_proposal_report -q -p no:cacheprovider
```

预期：测试失败，缺 review endpoint 字符串或人工实验文案。

- [ ] **步骤 3：实现 state 和加载函数**

在 `EvalsPage.jsx` 中增加 state：

```javascript
const [timingProposalReview, setTimingProposalReview] = useState(null)
const [timingProposalReviewDraft, setTimingProposalReviewDraft] = useState({
  decision: 'needs_data',
  reason_code: '',
  note: '',
  reviewer: ''
})
const [timingProposalReviewSaving, setTimingProposalReviewSaving] = useState(false)
```

新增加载函数：

```javascript
const loadTimingProposalReview = async () => {
  const res = await api.get('/evals/timing-tuning/proposal/review')
  setTimingProposalReview(res.data)
}
```

在 `loadTimingProposal()` 成功后调用 `loadTimingProposalReview()`。

- [ ] **步骤 4：实现 record-only 表单**

在 `timingProposal` tab 中增加一个 Card：

```jsx
<Card className="p-4">
  <div className="mb-3 text-sm font-medium text-slate-200">人工审核记录</div>
  <select value={timingProposalReviewDraft.decision} onChange={...}>
    <option value="needs_data">needs_data</option>
    <option value="rejected">rejected</option>
    <option value="approved_for_manual_experiment">approved_for_manual_experiment</option>
    <option value="reviewed_no_change">reviewed_no_change</option>
  </select>
  <p className="mt-2 text-xs text-slate-500">
    approved_for_manual_experiment 仅表示进入人工实验，不代表生产参数已变更。
  </p>
</Card>
```

保存按钮调用：

```javascript
await api.post('/evals/timing-tuning/proposal/reviews', timingProposalReviewDraft)
await loadTimingProposalReview()
```

按钮文案使用「记录审核」，不要使用「应用」。

- [ ] **步骤 5：运行 WebUI 静态测试和构建**

运行：

```bash
python -B -m pytest tests/test_webui_admin_redesign.py -q -p no:cacheprovider
npm --prefix webui run build
```

预期：测试通过，构建退出码 0。

- [ ] **步骤 6：提交任务 5**

运行：

```bash
python -m pytest tests/ -v
git add webui/src/features/evals/EvalsPage.jsx tests/test_webui_admin_redesign.py webui/dist
git commit -m "feat(评测): 展示调参提案审核状态"
```

## 任务 6：文档收口、计划勾选和最终验证

**文件：**

- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/timing-tuning-operations.md`

- [ ] **步骤 1：更新 docs/evals.md**

新增「TimingGate 调参提案运营」小节，包含：

```markdown
### TimingGate 调参提案运营

proposal 只读取 run-scoped TimingSignal audit、artifact trends、tuning analysis、候选参数文件和 timing_gate baseline。审核入口只记录人工结论，不会应用参数。

推荐命令：

```bash
python -B -m evals.timing_tuning_proposal \
  --run-id <run_id> \
  --manifest evals/reports/runs/<run_id>/manifest.json \
  --params tmp/timing_gate/param_candidates.json
```

`final_timing_action` 是人工最终动作 truth 的 canonical 字段，合法值为 `continue`、`wait`、`no_reply`。
```

- [ ] **步骤 2：更新 docs/todo.md**

在路线项 10 与路线项 8 的「下一步」中写明：

```markdown
TimingGate 调参提案运营链路已补齐 run-scoped audit、final action truth、候选参数治理和 record-only 人工审核；仍不自动应用参数、不更新 baseline、不改变 gate。
```

- [ ] **步骤 3：更新 docs/plan_walkthrough.md**

新增 2026-06-21 阶段记录，列出设计提交、各实现提交、验证结果和剩余边界。

- [ ] **步骤 4：勾选本计划已完成任务**

把本计划「进度总览」和每个任务步骤按实际完成情况从 `[ ]` 改为 `[x]`。未执行的步骤保持 `[ ]`。

- [ ] **步骤 5：文档自检**

运行：

```bash
rg -n "TO[D]O|TB[D]|待[定]|后续实[现]|FIXM[E]|占[位]" docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/timing-tuning-operations.md
git diff --check -- docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/timing-tuning-operations.md
```

预期：无输出。

- [ ] **步骤 6：最终验证**

运行：

```bash
bash scripts/run_timing_gate_gate.sh
bash scripts/run_eval_periodic.sh
npm --prefix webui run build
python -m pytest tests/ -v
```

预期：所有命令退出码为 0。

- [ ] **步骤 7：提交任务 6**

运行：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/timing-tuning-operations.md
git commit -m "docs(计划): 收口调参提案运营状态"
```

## 阶段验证底线

每个阶段提交前必须至少运行：

```bash
python -m pytest tests/ -v
```

涉及 WebUI 的阶段额外运行：

```bash
npm --prefix webui run build
```

涉及 TimingGate gate 或周期 artifact 的阶段额外运行：

```bash
bash scripts/run_timing_gate_gate.sh
bash scripts/run_eval_periodic.sh
```

提交时必须显式列出 `git add` 文件，禁止 `git add .` 和 `git add -A`。
