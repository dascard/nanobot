# P4-5C RAG manual 样本扩充实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 扩充 RAG benchmark 的稳定 manual case，并让 baseline 与 manual case 集合保持一致。

**架构：** 保持 `evals.rag_benchmark` 专用体系不变，不改 Admin / WebUI。新增仓库内 `constraint_only` manual JSON，补强 baseline 合同测试，再同步 `evals/baselines/rag_benchmark.json` 和文档状态。

**技术栈：** Python、pytest、JSON、`evals.rag_benchmark.run`、Bash gate 脚本。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-18-rag-manual-case-expansion-design.md`
- 当前范围：扩充稳定 manual case、更新 baseline、补 baseline 合同测试、文档收口。
- 不纳入本计划：positive fixture DB、runtime provider、generated case 入库、Admin / WebUI 改造、RAG 主链路重构。

## 实际执行记录

- 设计提交：`de97759 docs(评测): 设计 RAG manual 扩样`。
- 计划提交：`5511a50 docs(计划): 记录 RAG manual 扩样计划`。
- 任务 1 提交：`2189391 test(评测): 收紧 RAG baseline 合同`。
- 插入修复提交：`93fe947 fix(知识库): 过滤未知发布时间资料`。新增 `knowledge_manual_future_publish_filter_constraint_001` 后，manual gate 暴露 `published_after` 会放行未知发布时间资料；已补回归测试并修复过滤逻辑。
- 任务 2 提交：`dcf492b test(评测): 扩充 RAG manual 样本`。
- 任务 2 红灯：新增 6 个 manual case 后，`test_rag_benchmark_baseline_file_matches_manual_gate_contract` 失败于 `assert 3 == 9`。
- 任务 2 gate：`python -B -m evals.rag_benchmark.run --manual evals/cases/rag_benchmark/manual --generated tmp/rag_benchmark/empty --provider-mode deterministic --manual-only --baseline evals/baselines/rag_benchmark.json --min-pass-rate 1.0 --max-new-failures 0 --max-degraded-rate 0.0 --max-unexpected-source-rate 0.0` 输出 `cases=9 passed=9 failed=0` 和 `Gate passed`。
- 任务 2 相邻回归：`python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider` 结果 `32 passed, 1 warning in 1.96s`。
- 任务 3 文档自检：占位词扫描通过，U+FFFD 扫描通过，`git diff --check` 无输出。
- 任务 3 定向回归：`python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider` 结果 `32 passed, 1 warning in 1.90s`。
- 任务 3 PR gate：`bash scripts/run_eval_pr_gate.sh` 结果为评测守卫 `27 passed, 1 warning in 1.81s`，各子 gate 均输出 `Gate passed`，RAG gate 输出 `cases=9 passed=9 failed=0`。
- 任务 3 周期性 gate：`bash scripts/run_eval_periodic.sh` 结果为评测守卫 `27 passed, 1 warning in 1.75s`，各子 gate 均输出 `Gate passed`，RAG gate 输出 `cases=9 passed=9 failed=0`。
- 任务 3 全量回归：`python -B -m pytest tests/ -v -p no:cacheprovider` 结果 `1367 passed, 6 skipped, 139 warnings in 99.86s`。

## 文件结构

- 修改：`tests/test_rag_benchmark.py`
  - 职责：baseline 文件必须与 enabled manual case 集合一致，避免新增 case 后忘记更新 baseline。
- 创建：`evals/cases/rag_benchmark/manual/memory_empty_user_session_constraint.json`
  - 职责：验证 memory 查询不会跨 sentinel user/session 泄漏候选。
- 创建：`evals/cases/rag_benchmark/manual/memory_digest_source_constraint.json`
  - 职责：验证 memory `source=digest` 结果只允许 `memory_digest`。
- 创建：`evals/cases/rag_benchmark/manual/knowledge_future_publish_filter_constraint.json`
  - 职责：验证 future publish filter 不应返回候选。
- 创建：`evals/cases/rag_benchmark/manual/knowledge_high_trust_citation_constraint.json`
  - 职责：验证 high trust knowledge 结果仍必须带 citation。
- 创建：`evals/cases/rag_benchmark/manual/sticker_empty_scope_constraint.json`
  - 职责：验证 sticker 在 sentinel stream 且禁用 global 时不泄漏候选。
- 创建：`evals/cases/rag_benchmark/manual/group_memory_empty_group_filter_constraint.json`
  - 职责：验证 group memory 在 sentinel group 下不泄漏其他群记忆。
- 修改：`evals/baselines/rag_benchmark.json`
  - 职责：同步 manual deterministic baseline，case 数从 3 增加到 9。
- 修改：`docs/evals.md`
  - 职责：记录 P4-5C 样本扩充原则、baseline 一致性规则和 positive 样本边界。
- 修改：`docs/todo.md`
  - 职责：同步 P4-5C 阶段状态和验证记录。
- 修改：`docs/plan_walkthrough.md`
  - 职责：记录 P4-5C 的提交边界、红绿灯和最终验证。
- 修改：`.Codex/plans/rag-manual-case-expansion.md`
  - 职责：执行完成后勾选步骤并记录实际验证结果。

## 任务 1：baseline 合同守卫

**文件：**
- 修改：`tests/test_rag_benchmark.py`

- [x] **步骤 1：编写 baseline 合同红灯测试**

替换 `test_rag_benchmark_baseline_file_matches_manual_gate_contract`：

```python
def test_rag_benchmark_baseline_file_matches_manual_gate_contract():
    from evals.rag_benchmark.baseline import load_rag_baseline
    from evals.rag_benchmark.cases import load_cases

    baseline = load_rag_baseline("evals/baselines/rag_benchmark.json")
    manual_cases = [
        case
        for case in load_cases(
            manual_dir="evals/cases/rag_benchmark/manual",
            generated_dir="tmp/rag_benchmark/__contract_empty__",
        )
        if case.status == "enabled"
    ]
    baseline_case_ids = {str(item.get("case_id") or "") for item in baseline["case_scores"]}
    manual_case_ids = {case.id for case in manual_cases}

    assert baseline["suite"] == "rag_benchmark"
    assert baseline["provider_mode"] == "deterministic"
    assert baseline["case_scope"] == "manual"
    assert baseline["metrics"]["overall"]["total_cases"] == len(manual_cases)
    assert baseline_case_ids == manual_case_ids
    assert "case_scores" in baseline
    assert all("case_id" in item and "ok" in item for item in baseline["case_scores"])
```

- [x] **步骤 2：运行当前绿灯保护**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：PASS。当前 manual case 和 baseline 都是 3 个，说明增强后的合同测试不会误伤现状。

- [x] **步骤 3：提交任务 1**

运行：

```bash
git add tests/test_rag_benchmark.py
git commit -m "test(评测): 收紧 RAG baseline 合同"
```

## 任务 2：扩充 manual case 并更新 baseline

**文件：**
- 创建：`evals/cases/rag_benchmark/manual/memory_empty_user_session_constraint.json`
- 创建：`evals/cases/rag_benchmark/manual/memory_digest_source_constraint.json`
- 创建：`evals/cases/rag_benchmark/manual/knowledge_future_publish_filter_constraint.json`
- 创建：`evals/cases/rag_benchmark/manual/knowledge_high_trust_citation_constraint.json`
- 创建：`evals/cases/rag_benchmark/manual/sticker_empty_scope_constraint.json`
- 创建：`evals/cases/rag_benchmark/manual/group_memory_empty_group_filter_constraint.json`
- 修改：`evals/baselines/rag_benchmark.json`
- 修改：`tests/test_rag_benchmark.py`

- [x] **步骤 1：新增 6 个 manual case**

创建 `memory_empty_user_session_constraint.json`：

```json
{
  "id": "memory_manual_empty_user_session_constraint_001",
  "suite": "rag_benchmark",
  "source_type": "memory",
  "case_type": "constraint_only",
  "query": "RAG benchmark",
  "filters": {
    "source": "all",
    "user_id": "__rag_benchmark_empty_user__",
    "session_id": "__rag_benchmark_empty_session__"
  },
  "expected": {
    "candidate_ids": [],
    "allow_empty": true,
    "max_merged_candidates": 0,
    "max_reranker_candidates": 0
  },
  "meta": {
    "origin": "manual_hard",
    "sensitivity": "safe",
    "notes": "sentinel user/session 不应召回任何记忆候选。"
  }
}
```

创建 `memory_digest_source_constraint.json`：

```json
{
  "id": "memory_manual_digest_source_constraint_001",
  "suite": "rag_benchmark",
  "source_type": "memory",
  "case_type": "constraint_only",
  "query": "RAG benchmark",
  "filters": {
    "source": "digest"
  },
  "expected": {
    "candidate_ids": [],
    "allow_empty": true,
    "expected_source_type": "memory_digest",
    "max_reranker_candidates": 100
  },
  "meta": {
    "origin": "manual_hard",
    "sensitivity": "safe",
    "notes": "source=digest 时不应混入 session_summary 候选。"
  }
}
```

创建 `knowledge_future_publish_filter_constraint.json`：

```json
{
  "id": "knowledge_manual_future_publish_filter_constraint_001",
  "suite": "rag_benchmark",
  "source_type": "knowledge",
  "case_type": "constraint_only",
  "query": "AI daily RAG benchmark",
  "filters": {
    "min_trust_level": "low",
    "published_after": "2999-01-01"
  },
  "expected": {
    "candidate_ids": [],
    "allow_empty": true,
    "expected_source_type": "knowledge",
    "requires_citation": true,
    "max_merged_candidates": 0,
    "max_reranker_candidates": 0
  },
  "meta": {
    "origin": "manual_hard",
    "sensitivity": "safe",
    "notes": "未来发布时间过滤应稳定返回空结果。"
  }
}
```

创建 `knowledge_high_trust_citation_constraint.json`：

```json
{
  "id": "knowledge_manual_high_trust_citation_constraint_001",
  "suite": "rag_benchmark",
  "source_type": "knowledge",
  "case_type": "constraint_only",
  "query": "RAG benchmark citation",
  "filters": {
    "min_trust_level": "high"
  },
  "expected": {
    "candidate_ids": [],
    "allow_empty": true,
    "expected_source_type": "knowledge",
    "requires_citation": true,
    "max_reranker_candidates": 100
  },
  "meta": {
    "origin": "manual_hard",
    "sensitivity": "safe",
    "notes": "高信任知识库结果仍必须携带 citation。"
  }
}
```

创建 `sticker_empty_scope_constraint.json`：

```json
{
  "id": "sticker_manual_empty_scope_constraint_001",
  "suite": "rag_benchmark",
  "source_type": "sticker",
  "case_type": "constraint_only",
  "query": "开心 表情包",
  "filters": {
    "chat_stream_id": "__rag_benchmark_empty_stream__",
    "include_global": false
  },
  "expected": {
    "candidate_ids": [],
    "allow_empty": true,
    "expected_source_type": "sticker",
    "requires_sendable": true,
    "max_merged_candidates": 0,
    "max_reranker_candidates": 0
  },
  "meta": {
    "origin": "manual_hard",
    "sensitivity": "safe",
    "notes": "不存在 stream 且不含 global 时不应泄漏表情候选。"
  }
}
```

创建 `group_memory_empty_group_filter_constraint.json`：

```json
{
  "id": "group_memory_manual_empty_group_filter_constraint_001",
  "suite": "rag_benchmark",
  "source_type": "group_memory",
  "case_type": "constraint_only",
  "query": "RAG benchmark",
  "filters": {
    "group_id": "group_rag_benchmark_empty"
  },
  "expected": {
    "candidate_ids": [],
    "allow_empty": true,
    "expected_source_type": "group_memory",
    "requires_group_id": true,
    "max_merged_candidates": 0,
    "max_reranker_candidates": 0
  },
  "meta": {
    "origin": "manual_hard",
    "sensitivity": "safe",
    "notes": "不存在 group 时不应泄漏其他群记忆。"
  }
}
```

- [x] **步骤 2：运行 baseline 合同红灯**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：FAIL。失败点为 `total_cases` 或 case id 集合不一致，因为新增 6 个 manual case 后 baseline 仍只有 3 个。

- [x] **步骤 3：更新 baseline**

将 `evals/baselines/rag_benchmark.json` 更新为 9 个 case 的稳定 baseline。`case_scores` 至少包含以下 9 个 id，全部 `ok=true`：

```json
[
  "group_memory_manual_empty_group_filter_constraint_001",
  "group_memory_manual_filter_constraint_001",
  "knowledge_manual_citation_constraint_001",
  "knowledge_manual_future_publish_filter_constraint_001",
  "knowledge_manual_high_trust_citation_constraint_001",
  "memory_manual_digest_source_constraint_001",
  "memory_manual_empty_user_session_constraint_001",
  "sticker_manual_empty_scope_constraint_001",
  "sticker_manual_generic_constraint_001"
]
```

`metrics.overall.total_cases` 和 `passed_cases` 设为 `9`，`pass_rate` 设为 `1.0`，`positive_cases`、`hit@1`、`hit@3`、`hit@5`、`mrr` 保持 `0.0`，`failed_cases` 保持空数组。

- [x] **步骤 4：运行 RAG 单文件绿灯**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py -v -p no:cacheprovider
```

预期：PASS。

- [x] **步骤 5：运行 RAG manual deterministic gate**

运行：

```bash
python -B -m evals.rag_benchmark.run \
  --manual evals/cases/rag_benchmark/manual \
  --generated tmp/rag_benchmark/empty \
  --provider-mode deterministic \
  --manual-only \
  --baseline evals/baselines/rag_benchmark.json \
  --min-pass-rate 1.0 \
  --max-new-failures 0 \
  --max-degraded-rate 0.0 \
  --max-unexpected-source-rate 0.0
```

预期：退出码 0，输出包含 `cases=9 passed=9 failed=0` 和 `Gate passed`。

- [x] **步骤 6：运行评测守卫相邻回归**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
```

预期：PASS。

- [x] **步骤 7：提交任务 2**

运行：

```bash
git add \
  evals/cases/rag_benchmark/manual/memory_empty_user_session_constraint.json \
  evals/cases/rag_benchmark/manual/memory_digest_source_constraint.json \
  evals/cases/rag_benchmark/manual/knowledge_future_publish_filter_constraint.json \
  evals/cases/rag_benchmark/manual/knowledge_high_trust_citation_constraint.json \
  evals/cases/rag_benchmark/manual/sticker_empty_scope_constraint.json \
  evals/cases/rag_benchmark/manual/group_memory_empty_group_filter_constraint.json \
  evals/baselines/rag_benchmark.json \
  tests/test_rag_benchmark.py
git commit -m "test(评测): 扩充 RAG manual 样本"
```

## 任务 3：文档收口与最终验证

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/rag-manual-case-expansion.md`

- [x] **步骤 1：更新 `docs/evals.md`**

在 RAG Benchmark 边界章节补充：

```markdown
P4-5C 已将 manual deterministic gate 的样本从 3 个扩充到 9 个。当前新增样本仍全部是 `constraint_only`，用于覆盖 memory、knowledge、sticker 和 group_memory 的过滤 / scope / citation / sendable 约束；positive exact 样本需要固定 fixture DB 后再纳入稳定 gate。

更新 manual case 时必须同步 `evals/baselines/rag_benchmark.json`，并保证 baseline 的 `case_scores[*].case_id` 集合与 enabled manual case 集合一致。
```

- [x] **步骤 2：更新 `docs/todo.md`**

把路线项 8 的 P4-5C 状态改为已完成，并记录：

- manual case 数从 3 增加到 9。
- baseline 合同测试已收紧。
- RAG manual deterministic gate 输出 `cases=9 passed=9 failed=0` 和 `Gate passed`。
- 下一步转为 fixture-backed positive RAG case 或更多真实样本运营动作。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

新增「已完成阶段详情：P4-5C RAG manual 样本扩充」章节，记录：

- 设计文档路径。
- 实现计划路径。
- 任务 1 / 任务 2 / 任务 3 的提交边界。
- 红灯 / 绿灯 / gate / 全量回归输出。
- Positive exact 样本被排除在本阶段外的原因。

- [x] **步骤 4：勾选本计划已完成步骤**

在 `.Codex/plans/rag-manual-case-expansion.md` 中勾选已完成步骤，并记录实际验证结果。

- [x] **步骤 5：运行文档自检**

运行：

```bash
set -euo pipefail
if rg -n "T[O]DO|待[定]|后续[实]现|类似[任]务|添加[适]当|为上[述]" \
  .Codex/plans/rag-manual-case-expansion.md docs/evals.md docs/todo.md docs/plan_walkthrough.md; then
  echo "placeholder scan found matches"
  exit 1
else
  status=$?
  if [ "$status" -eq 1 ]; then
    echo "placeholder scan passed"
  else
    echo "placeholder scan failed with exit $status"
    exit "$status"
  fi
fi
python - <<'PY'
from pathlib import Path
for path in [
    Path('.Codex/plans/rag-manual-case-expansion.md'),
    Path('docs/evals.md'),
    Path('docs/todo.md'),
    Path('docs/plan_walkthrough.md'),
]:
    data = path.read_text(encoding='utf-8')
    if '\ufffd' in data:
        raise SystemExit(f'U+FFFD found in {path}')
print('U+FFFD scan passed')
PY
git diff --check -- .Codex/plans/rag-manual-case-expansion.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
```

预期：占位词扫描通过，U+FFFD 扫描通过，`git diff --check` 无输出。

- [x] **步骤 6：运行最终验证**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
bash scripts/run_eval_pr_gate.sh
bash scripts/run_eval_periodic.sh
python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：全部退出码为 0，全量 pytest 无 failure。

- [x] **步骤 7：提交任务 3**

运行：

```bash
git add .Codex/plans/rag-manual-case-expansion.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(评测): 收口 RAG manual 扩样状态"
```
