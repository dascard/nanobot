# P4-5G RAG group_memory fixture 正例门禁实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 RAG benchmark 的 `positive_v1` fixture preset 中新增 group memory 正例，固定验证 `requires_group_id=true` 的群体记忆候选。

**架构：** 复用现有 `positive_v1` preset，不新增 CLI 参数或新 preset。fixture builder 同时写入 memory、knowledge、sticker 与 group memory fixture 数据；group memory fixture 只写 `GroupMemory` 行，不写 semantic index，运行时通过 `GroupMemoryRetrievalService.select()` 进入现有 benchmark adapter，再由 scoring 检查 fixed candidate、forbidden decoy 与 group filter。

**技术栈：** Python、pytest、SQLAlchemy SQLite fixture DB、RAG benchmark、GroupMemoryRetrievalService、现有 deterministic reranker provider。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-20-rag-group-memory-fixture-design.md`
- 设计提交：`fa1f387 docs(评测): 设计 group_memory fixture 正例`
- 当前范围：fixture builder、RAG benchmark 测试、baseline 合同、文档状态同步。
- 不纳入本计划：通用过滤约束 fixture、真实样本运营、Admin / WebUI、runtime provider、生产 DB schema、RAG 主流程重构、阈值调参。

## 子 agent 分工约定

本阶段核心写入集中在 `tests/test_rag_benchmark.py`、`evals/rag_benchmark/fixtures.py` 和 `evals/baselines/rag_benchmark.json`。为了减少冲突，默认由主线程完成测试与实现；可委派的任务只限以下类型：

- 只读核对 agent：检查 `tmp/rag_benchmark/reports/latest.json` 与 baseline 更新是否一致，不修改文件。
- 验证 agent：在主线程实现后运行相邻测试或 gate 脚本，返回命令、退出码和关键输出，不修改文件。
- 禁止多个 worker 同时修改 `tests/test_rag_benchmark.py` 或 `evals/rag_benchmark/fixtures.py`。
- 若使用 worker 写代码，必须把写入范围限定为一个互不冲突文件；worker 不得回滚其他人的修改。

## 文件结构

- 修改：`tests/test_rag_benchmark.py`
  - 职责：新增 group memory fixture 正例红灯测试，更新 CLI fixture gate 和 baseline 合同断言。
- 修改：`evals/rag_benchmark/fixtures.py`
  - 职责：在 `positive_v1` preset 中新增 group memory case，构建固定正例与跨群 decoy `GroupMemory` 数据。
- 修改：`evals/baselines/rag_benchmark.json`
  - 职责：同步 stable gate 的真实 metrics 与 case_scores，新增 `group_memory_fixture_positive_001`。
- 修改：`docs/evals.md`
  - 职责：记录 RAG stable gate 已包含 memory、knowledge、sticker 与 group memory fixture 正例。
- 修改：`docs/todo.md`
  - 职责：把 P4-5G 状态写入路线项 8。
- 修改：`docs/plan_walkthrough.md`
  - 职责：记录本阶段提交、红绿灯和验证结果。
- 修改：`.Codex/plans/rag-group-memory-fixture.md`
  - 职责：执行时勾选步骤并记录真实验证输出。

## 前置完成

- [x] **步骤 1：完成 P4-5G 设计文档**

设计文档已写入：

```text
docs/superpowers/specs/2026-06-20-rag-group-memory-fixture-design.md
```

提交：

```text
fa1f387 docs(评测): 设计 group_memory fixture 正例
```

- [x] **步骤 2：确认本阶段不混入通用过滤约束 fixture**

P4-5G 只验证 group memory positive case，并在同一 seed 中加入跨群 decoy。通用过滤约束 fixture 保留为后续独立阶段，避免把多 source 过滤语义和正例覆盖空洞混在一起。

## 任务 1：测试先行固定 group memory fixture 合同

**文件：**
- 修改：`tests/test_rag_benchmark.py`
- 修改：`.Codex/plans/rag-group-memory-fixture.md`

- [ ] **步骤 1：新增 group memory fixture 红灯测试**

在 `tests/test_rag_benchmark.py` 中新增测试，放在现有 `test_rag_benchmark_fixture_db_supports_sticker_positive_case` 后面：

```python
def test_rag_benchmark_fixture_db_supports_group_memory_positive_case(tmp_path):
    from evals.rag_benchmark.fixtures import (
        GROUP_MEMORY_CANDIDATE_ID,
        GROUP_MEMORY_CASE_ID,
        GROUP_MEMORY_DECOY_CANDIDATE_ID,
        GROUP_MEMORY_GROUP_ID,
        build_fixture_db,
    )
    from evals.rag_benchmark.run import run_benchmark

    fixture_db = tmp_path / "positive.db"

    cases = build_fixture_db(fixture_db, preset="positive_v1")
    results, scores = run_benchmark(fixture_db, cases, provider_mode="deterministic")

    by_case = {case.id: case for case in cases}
    by_result = {result.case_id: result for result in results}
    by_score = {score.case_id: score for score in scores}

    assert GROUP_MEMORY_CASE_ID in by_case
    assert by_case[GROUP_MEMORY_CASE_ID].source_type == "group_memory"
    assert by_case[GROUP_MEMORY_CASE_ID].filters["group_id"] == GROUP_MEMORY_GROUP_ID
    assert by_case[GROUP_MEMORY_CASE_ID].expected.requires_group_id is True
    assert by_case[GROUP_MEMORY_CASE_ID].expected.candidate_ids == [GROUP_MEMORY_CANDIDATE_ID]
    assert by_case[GROUP_MEMORY_CASE_ID].expected.forbidden_candidate_ids == [
        GROUP_MEMORY_DECOY_CANDIDATE_ID
    ]

    group_memory_result = by_result[GROUP_MEMORY_CASE_ID]
    group_memory_score = by_score[GROUP_MEMORY_CASE_ID]
    assert GROUP_MEMORY_CANDIDATE_ID in group_memory_result.candidate_ids
    assert GROUP_MEMORY_DECOY_CANDIDATE_ID not in group_memory_result.candidate_ids
    assert any(
        candidate.candidate_id == GROUP_MEMORY_CANDIDATE_ID
        and candidate.group_id == GROUP_MEMORY_GROUP_ID
        for candidate in group_memory_result.candidates
    )
    assert group_memory_score.ok is True
    assert group_memory_score.hit_at["5"] is True
    assert group_memory_score.forbidden_hits == []
    assert group_memory_score.checks["group_filter"] is True
```

- [ ] **步骤 2：运行 group memory fixture 红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_group_memory_positive_case -v -p no:cacheprovider
```

预期：FAIL，失败原因包含 `ImportError`，因为 `GROUP_MEMORY_CASE_ID` 或 `GROUP_MEMORY_CANDIDATE_ID` 尚未定义。

- [ ] **步骤 3：更新 CLI fixture gate 测试红灯**

在 `test_rag_benchmark_cli_runs_manual_fixture_positive_gate` 中，把临时 baseline 的 `overall.total_cases` 改为 `5`、`overall.positive_cases` 改为 `4`，并在 `case_scores` 中新增 group memory fixture：

```python
{
    "case_id": "group_memory_fixture_positive_001",
    "ok": True,
    "rank": 1,
    "hit_at": {"1": True, "3": True, "5": True},
    "checks": {"citation": None, "sendable": None, "group_filter": True},
    "forbidden_hits": [],
    "errors": [],
}
```

在该测试结尾增加或更新断言：

```python
scores = {
    str(item.get("case_id") or ""): item
    for item in report["case_scores"]
}
assert report["metrics"]["overall"]["positive_cases"] == 4
assert report["metrics"]["source:knowledge"]["positive_cases"] == 1
assert report["metrics"]["source:sticker"]["positive_cases"] == 1
assert report["metrics"]["source:group_memory"]["positive_cases"] == 1
assert scores["knowledge_fixture_positive_001"]["checks"]["citation"] is True
assert scores["sticker_fixture_positive_001"]["checks"]["sendable"] is True
assert scores["group_memory_fixture_positive_001"]["ok"] is True
assert scores["group_memory_fixture_positive_001"]["checks"]["group_filter"] is True
assert scores["group_memory_fixture_positive_001"].get("forbidden_hits", []) == []
```

- [ ] **步骤 4：运行 CLI fixture gate 红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate -v -p no:cacheprovider
```

预期：FAIL，失败原因是当前 fixture preset 只有 3 个 positive case，report 中没有 `group_memory_fixture_positive_001`，或 `overall.positive_cases` 仍为 `3`。

- [ ] **步骤 5：更新 baseline 合同红灯**

在 `test_rag_benchmark_baseline_file_matches_manual_gate_contract` 末尾增加：

```python
group_memory_fixture_score = baseline_scores["group_memory_fixture_positive_001"]
assert group_memory_fixture_score["ok"] is True
assert group_memory_fixture_score["hit_at"]["5"] is True
assert group_memory_fixture_score["checks"]["group_filter"] is True
assert group_memory_fixture_score.get("forbidden_hits", []) == []
assert baseline["metrics"]["source:group_memory"]["positive_cases"] == 1
```

同时把已有 positive case 断言收紧为：

```python
assert baseline["metrics"]["overall"]["positive_cases"] == 4
```

- [ ] **步骤 6：运行 baseline 合同红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：FAIL，失败原因是 baseline 还没有 `group_memory_fixture_positive_001`。

- [ ] **步骤 7：记录红灯结果并延后提交**

任务 1 的红灯测试会让仓库处于 failing 状态。本阶段不单独提交红灯测试；测试变更将与任务 2 的最小实现一起提交，保证提交点为绿色状态。

## 任务 2：实现 group memory fixture seed 与 case

**文件：**
- 修改：`evals/rag_benchmark/fixtures.py`
- 修改：`.Codex/plans/rag-group-memory-fixture.md`

- [ ] **步骤 1：扩展 imports 和常量**

修改 `evals/rag_benchmark/fixtures.py` 顶部业务 import：

```python
from core.database import Base, GroupMemory, KnowledgeChunk, KnowledgeDocument, StickerMemory
```

在 sticker 常量后新增：

```python
GROUP_MEMORY_CASE_ID = "group_memory_fixture_positive_001"
GROUP_MEMORY_ID = 9201
GROUP_MEMORY_DECOY_ID = 9202
GROUP_MEMORY_CANDIDATE_ID = f"group_memory:{GROUP_MEMORY_ID}:memory"
GROUP_MEMORY_DECOY_CANDIDATE_ID = f"group_memory:{GROUP_MEMORY_DECOY_ID}:memory"
GROUP_MEMORY_GROUP_ID = "group_rag_fixture_memory"
GROUP_MEMORY_DECOY_GROUP_ID = "group_rag_fixture_other"
GROUP_MEMORY_QUERY = "群体记忆 RAG fixture 正例"
```

- [ ] **步骤 2：新增 `_group_memory_positive_case()`**

在 `_sticker_positive_case()` 后新增：

```python
def _group_memory_positive_case() -> BenchmarkCase:
    return BenchmarkCase(
        id=GROUP_MEMORY_CASE_ID,
        suite="rag_benchmark",
        source_type="group_memory",
        case_type="positive",
        query=GROUP_MEMORY_QUERY,
        filters={
            "group_id": GROUP_MEMORY_GROUP_ID,
            "recent_messages": [],
            "max_chars": 1200,
        },
        expected={
            "candidate_ids": [GROUP_MEMORY_CANDIDATE_ID],
            "forbidden_candidate_ids": [GROUP_MEMORY_DECOY_CANDIDATE_ID],
            "hit_at": 5,
            "expected_source_type": "group_memory",
            "requires_group_id": True,
        },
        meta={
            "origin": "fixture_exact",
            "sensitivity": "safe",
            "fixture": FIXTURE_PRESET,
        },
    )
```

- [ ] **步骤 3：把 fixture case 顺序扩为四类**

修改 `fixture_cases()`：

```python
return [
    _memory_positive_case(),
    _knowledge_positive_case(),
    _sticker_positive_case(),
    _group_memory_positive_case(),
]
```

顺序固定为 memory、knowledge、sticker、group memory，减少 baseline diff 噪声。

- [ ] **步骤 4：新增 `_seed_group_memory_positive_fixture()`**

在 `_seed_sticker_positive_fixture()` 后新增：

```python
def _seed_group_memory_positive_fixture(db: Session) -> None:
    now = datetime(2026, 6, 20, 0, 0, 0)
    meta = {"fixture": FIXTURE_PRESET, "evidence_short_summary": GROUP_MEMORY_QUERY}
    rows = [
        GroupMemory(
            id=GROUP_MEMORY_ID,
            group_id=GROUP_MEMORY_GROUP_ID,
            memory_type="topic",
            content="群体记忆 RAG fixture 正例：本群固定用来验证 group_memory 检索命中。",
            content_hash="fixture-group-memory-positive-001",
            cluster_key="rag fixture group memory",
            evidence_log_ids_json=json.dumps([920101, 920102]),
            confidence=0.9,
            evidence_count=2,
            first_seen=now,
            last_seen=now,
            updated_at=now,
            decay_score=1.0,
            status="active",
            inject_policy="auto",
            source="fixture",
            meta_json=json.dumps(meta, ensure_ascii=False, sort_keys=True),
            created_at=now,
        ),
        GroupMemory(
            id=GROUP_MEMORY_DECOY_ID,
            group_id=GROUP_MEMORY_DECOY_GROUP_ID,
            memory_type="topic",
            content="群体记忆 RAG fixture 正例：其他群的 decoy 用来验证 group filter 不泄漏。",
            content_hash="fixture-group-memory-decoy-001",
            cluster_key="rag fixture group memory decoy",
            evidence_log_ids_json=json.dumps([920201, 920202, 920203]),
            confidence=0.95,
            evidence_count=3,
            first_seen=now,
            last_seen=now,
            updated_at=now,
            decay_score=1.0,
            status="active",
            inject_policy="auto",
            source="fixture",
            meta_json=json.dumps(meta, ensure_ascii=False, sort_keys=True),
            created_at=now,
        ),
    ]
    db.add_all(rows)
    db.flush()
```

说明：`GROUP_MEMORY_GROUP_ID` 已是 `group_` 前缀形式，`GroupMemoryRetrievalService.select()` 再调用 `normalize_group_session_id()` 时保持不变。

- [ ] **步骤 5：接入 seed 流程**

修改 `seed_positive_fixture_db()`，在 `_seed_sticker_positive_fixture(db)` 后追加：

```python
_seed_group_memory_positive_fixture(db)
```

- [ ] **步骤 6：运行 group memory fixture 绿灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_group_memory_positive_case -v -p no:cacheprovider
```

预期：PASS。若失败于 `low_relevance`，优先调整 fixture `content` 和 `GROUP_MEMORY_QUERY` 的词面重叠，不降低生产阈值。

- [ ] **步骤 7：运行全部 fixture 定向测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_knowledge_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_sticker_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_group_memory_positive_case \
  -v -p no:cacheprovider
```

预期：4 个 fixture 正例全部 PASS。

## 任务 3：更新 baseline 并验证 stable gate

**文件：**
- 修改：`evals/baselines/rag_benchmark.json`
- 修改：`tests/test_rag_benchmark.py`
- 修改：`.Codex/plans/rag-group-memory-fixture.md`

- [ ] **步骤 1：运行 RAG stable gate 生成新报告**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
PYTHONDONTWRITEBYTECODE=1 NANOBOT_TESTING=1 DATABASE_URL=sqlite:///:memory: \
python -B -m evals.rag_benchmark.run \
  --manual evals/cases/rag_benchmark/manual \
  --generated tmp/rag_benchmark/empty \
  --provider-mode deterministic \
  --manual-only \
  --fixture positive_v1 \
  --fixture-db tmp/rag_benchmark/fixtures/positive_v1.db \
  --baseline evals/baselines/rag_benchmark.json \
  --min-pass-rate 1.0 \
  --min-hit-at-5 1.0 \
  --min-mrr 1.0 \
  --max-new-failures 0 \
  --max-degraded-rate 0.0 \
  --max-unexpected-source-rate 0.0
```

预期：当前 baseline 尚未更新时会 FAIL，报告里应显示新增 fixed case 或 metric delta。

- [ ] **步骤 2：用真实报告更新 baseline**

从 `tmp/rag_benchmark/reports/latest.json` 复制稳定字段到 `evals/baselines/rag_benchmark.json`：

- `case_scope == "manual+fixture"`。
- `metrics.overall.total_cases == 13`。
- `metrics.overall.positive_cases == 4`。
- `metrics.overall.hit@5 == 1.0`。
- `metrics.overall.mrr == 1.0`。
- `metrics["source:group_memory"].positive_cases == 1`。
- `case_scores` 新增 `group_memory_fixture_positive_001`。
- `case_scores["group_memory_fixture_positive_001"].checks.group_filter == true`。
- `case_scores["group_memory_fixture_positive_001"].forbidden_hits == []`。

如果报告结构和当前 baseline 顺序不同，保留报告生成顺序，不手工重排无关字段。

- [ ] **步骤 3：运行 CLI fixture gate 绿灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate -v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 4：运行 baseline 合同绿灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 5：重新运行 RAG stable gate**

运行任务 3 步骤 1 的 gate 命令。

预期：`cases=13 passed=13 failed=0`，并输出 `Gate passed`。

## 任务 4：相邻回归、全量验证和代码阶段提交

**文件：**
- 修改：`tests/test_rag_benchmark.py`
- 修改：`evals/rag_benchmark/fixtures.py`
- 修改：`evals/baselines/rag_benchmark.json`
- 修改：`.Codex/plans/rag-group-memory-fixture.md`

- [ ] **步骤 1：运行 RAG 相邻回归**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 2：运行 group memory 相邻回归**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_group_memory_rag.py \
  tests/test_group_memory_injection.py \
  tests/test_semantic_adapters.py::test_group_memory_one_row_one_chunk \
  tests/test_rag_debug.py::test_rag_debug_group_memory_uses_retrieval_service_not_stub \
  -v -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 3：运行 PR gate**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
bash scripts/run_eval_pr_gate.sh
```

预期：TimingGate、capability gates 和 RAG stable gate 全部 `Gate passed`；RAG 输出 `cases=13 passed=13 failed=0`。

- [ ] **步骤 4：运行 periodic gate**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
bash scripts/run_eval_periodic.sh
```

预期：稳定 gate 全部通过；RAG 输出 `cases=13 passed=13 failed=0`。

- [ ] **步骤 5：运行全量测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -m pytest tests/ -v
```

预期：0 failures。

- [ ] **步骤 6：运行 diff 检查**

运行：

```bash
git diff --check -- tests/test_rag_benchmark.py evals/rag_benchmark/fixtures.py evals/baselines/rag_benchmark.json .Codex/plans/rag-group-memory-fixture.md
```

预期：无输出，退出码 0。

- [ ] **步骤 7：提交绿色代码阶段**

显式暂存：

```bash
git add tests/test_rag_benchmark.py evals/rag_benchmark/fixtures.py evals/baselines/rag_benchmark.json .Codex/plans/rag-group-memory-fixture.md
```

提交：

```bash
git commit -m "feat(评测): 增加 group_memory fixture 正例"
```

## 任务 5：文档收口

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/rag-group-memory-fixture.md`

- [ ] **步骤 1：更新 `docs/evals.md`**

同步 RAG stable gate 说明：

- stable gate case 数从 12 更新为 13。
- `positive_v1` 从 memory + knowledge + sticker 三正例更新为四正例。
- 新增 `group_memory_fixture_positive_001`，固定命中 `group_memory:9201:memory`。
- 记录 `requires_group_id=true` 与跨群 decoy forbidden check。

- [ ] **步骤 2：更新 `docs/todo.md`**

在路线项 8 中新增 P4-5G 验证状态：

```markdown
- **P4-5G 验证状态（2026-06-20）**：RAG `positive_v1` fixture 已从 memory + knowledge + sticker 三正例扩展为 memory + knowledge + sticker + group_memory 四正例；新增 `group_memory_fixture_positive_001`，固定命中 `group_memory:9201:memory`，并通过 `requires_group_id=true` 的 group filter check；RAG stable gate 输出 `cases=13 passed=13 failed=0` 和 `Gate passed`；...
```

把下一步改为过滤约束 fixture 或真实样本运营动作。

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

新增 P4-5G 完成记录：

- 设计提交：`fa1f387 docs(评测): 设计 group_memory fixture 正例`。
- 计划提交：`docs(计划): 记录 group_memory fixture 计划`。
- 代码提交：`feat(评测): 增加 group_memory fixture 正例`。
- 记录红灯、定向测试、RAG gate、PR gate、periodic gate 和全量测试结果。

- [ ] **步骤 4：勾选本计划执行进度**

在 `.Codex/plans/rag-group-memory-fixture.md` 中把已完成任务勾选，并记录真实验证输出。不要把未执行的后续运营动作标记完成。

- [ ] **步骤 5：运行文档自检**

运行：

```bash
rg -n "待[定]|TB[D]|XX[X]|PLACEHOLDE[R]" docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-group-memory-fixture.md
LC_ALL=C rg -n $'\xef\xbf\xbd' docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-group-memory-fixture.md
git diff --check -- docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-group-memory-fixture.md
```

预期：除历史章节中描述「占位符扫描」的已有记录外，不出现新增占位内容；`git diff --check` 无输出。

- [ ] **步骤 6：提交文档收口阶段**

显式暂存：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-group-memory-fixture.md
```

提交：

```bash
git commit -m "docs(评测): 收口 group_memory fixture 状态"
```
