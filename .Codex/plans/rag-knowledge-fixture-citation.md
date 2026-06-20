# P4-5E RAG knowledge fixture 引用正例门禁实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 RAG benchmark 的 `positive_v1` fixture preset 中新增 knowledge 正例，固定验证 `requires_citation=true` 的非空返回候选。

**架构：** 复用现有 `positive_v1` preset，不新增 CLI 参数或新 preset。fixture builder 同时写入 memory 与 knowledge fixture 数据；knowledge fixture 使用 `KnowledgeDocument`、`KnowledgeChunk`、`chunk_from_knowledge_chunk()` 和 `upsert_semantic_chunks()` 进入现有 knowledge RAG 链路，再由 benchmark scoring 检查 fixed candidate 与 citation bool。

**技术栈：** Python、pytest、SQLAlchemy SQLite fixture DB、RAG benchmark、Knowledge RAG、现有 deterministic embedding / reranker provider。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-20-rag-knowledge-fixture-citation-design.md`
- 设计提交：`d694d53 docs(评测): 设计 knowledge fixture 引用门禁`
- 当前范围：fixture builder、RAG benchmark 测试、baseline 合同、文档状态同步。
- 不纳入本计划：真实样本运营、Admin / WebUI、runtime provider、生产 DB schema、RAG 主流程重构、阈值调参。

## 子 agent 分工约定

本阶段可并行的部分有限，核心写入集中在 `tests/test_rag_benchmark.py`、`evals/rag_benchmark/fixtures.py` 和 `evals/baselines/rag_benchmark.json`。为了减少冲突，默认由主线程完成测试与实现；可委派的只读或验证任务如下：

- 只读核对 agent：检查 `tmp/rag_benchmark/reports/latest.json` 与 baseline 更新是否一致，不修改文件。
- 验证 agent：在主线程实现后运行相邻测试或脚本，返回命令、退出码和关键输出，不修改文件。
- 禁止多个 worker 同时修改 `tests/test_rag_benchmark.py` 或 `evals/rag_benchmark/fixtures.py`。

## 文件结构

- 修改：`tests/test_rag_benchmark.py`
  - 职责：新增 knowledge fixture 正例红灯测试，更新 CLI fixture gate 和 baseline 合同断言，补齐 citation scoring 守卫。
- 修改：`evals/rag_benchmark/fixtures.py`
  - 职责：在 `positive_v1` preset 中新增 knowledge case，构建固定 `KnowledgeDocument` / `KnowledgeChunk` / semantic index 数据。
- 修改：`evals/baselines/rag_benchmark.json`
  - 职责：同步 stable gate 的真实 metrics 与 case_scores，新增 `knowledge_fixture_positive_001`。
- 修改：`docs/evals.md`
  - 职责：记录 RAG stable gate 已包含 memory 与 knowledge fixture 正例。
- 修改：`docs/todo.md`
  - 职责：把 P4-5E 状态写入路线项 8。
- 修改：`docs/plan_walkthrough.md`
  - 职责：记录本阶段提交、红绿灯和验证结果。
- 修改：`.Codex/plans/rag-knowledge-fixture-citation.md`
  - 职责：执行时勾选步骤并记录真实验证输出。

## 任务 1：测试先行固定 knowledge fixture 合同

**文件：**
- 修改：`tests/test_rag_benchmark.py`
- 修改：`.Codex/plans/rag-knowledge-fixture-citation.md`

- [x] **步骤 1：新增 knowledge fixture 红灯测试**

在 `tests/test_rag_benchmark.py` 中新增测试，放在现有 `test_rag_benchmark_fixture_db_supports_memory_positive_case` 后面：

```python
def test_rag_benchmark_fixture_db_supports_knowledge_positive_case(tmp_path):
    from evals.rag_benchmark.fixtures import (
        KNOWLEDGE_CANDIDATE_ID,
        KNOWLEDGE_CASE_ID,
        build_fixture_db,
    )
    from evals.rag_benchmark.run import run_benchmark

    fixture_db = tmp_path / "positive.db"

    cases = build_fixture_db(fixture_db, preset="positive_v1")
    results, scores = run_benchmark(fixture_db, cases, provider_mode="deterministic")

    by_case = {case.id: case for case in cases}
    by_result = {result.case_id: result for result in results}
    by_score = {score.case_id: score for score in scores}

    assert KNOWLEDGE_CASE_ID in by_case
    assert by_case[KNOWLEDGE_CASE_ID].expected.requires_citation is True
    assert by_case[KNOWLEDGE_CASE_ID].expected.candidate_ids == [KNOWLEDGE_CANDIDATE_ID]

    knowledge_result = by_result[KNOWLEDGE_CASE_ID]
    knowledge_score = by_score[KNOWLEDGE_CASE_ID]
    assert knowledge_result.candidate_ids[0] == KNOWLEDGE_CANDIDATE_ID
    assert any(
        candidate.candidate_id == KNOWLEDGE_CANDIDATE_ID and candidate.citation is True
        for candidate in knowledge_result.candidates
    )
    assert knowledge_score.ok is True
    assert knowledge_score.rank == 1
    assert knowledge_score.hit_at["5"] is True
    assert knowledge_score.checks["citation"] is True
```

- [x] **步骤 2：运行 knowledge fixture 红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_knowledge_positive_case -v -p no:cacheprovider
```

预期：FAIL，失败原因包含 `ImportError`，因为 `KNOWLEDGE_CASE_ID` 或 `KNOWLEDGE_CANDIDATE_ID` 尚未定义。

实际：FAIL，`ImportError: cannot import name 'KNOWLEDGE_CANDIDATE_ID' from 'evals.rag_benchmark.fixtures'`，符合红灯预期。

- [x] **步骤 3：补 scorer citation 守卫测试**

在 `tests/test_rag_benchmark.py` 的 scoring 测试区域新增：

```python
def test_scorer_fails_requires_citation_when_candidate_lacks_citation():
    from evals.rag_benchmark.schema import BenchmarkCandidate, BenchmarkCase, BenchmarkResult
    from evals.rag_benchmark.scoring import score_case

    case = BenchmarkCase(
        id="knowledge_requires_citation",
        source_type="knowledge",
        case_type="positive",
        query="RAG 引用",
        expected={
            "candidate_ids": ["knowledge:9001:chunk:0"],
            "requires_citation": True,
            "expected_source_type": "knowledge",
        },
    )
    result = BenchmarkResult(
        case_id=case.id,
        source_type="knowledge",
        candidate_ids=["knowledge:9001:chunk:0"],
        candidates=[
            BenchmarkCandidate(
                candidate_id="knowledge:9001:chunk:0",
                source_type="knowledge",
                rank=1,
                citation=False,
            )
        ],
    )

    score = score_case(case, result)

    assert score.ok is False
    assert score.checks["citation"] is False
    assert "citation check failed" in score.errors
```

- [x] **步骤 4：运行 scorer 守卫测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_scorer_fails_requires_citation_when_candidate_lacks_citation -v -p no:cacheprovider
```

预期：PASS。该测试固定已有评分边界，若失败说明 scoring 已偏离设计，需要先修评分器。

实际：PASS，`1 passed, 1 warning in 0.76s`。

- [x] **步骤 5：更新 CLI fixture gate 测试红灯**

在 `test_rag_benchmark_cli_runs_manual_fixture_positive_gate` 中，把临时 baseline 的 `overall.total_cases` 改为 `3`、`overall.positive_cases` 改为 `2`，并在 `case_scores` 中新增 knowledge fixture：

```python
{
    "case_id": "knowledge_fixture_positive_001",
    "ok": True,
    "rank": 1,
    "hit_at": {"1": True, "3": True, "5": True},
    "checks": {"citation": True, "sendable": None, "group_filter": None},
    "errors": [],
}
```

在该测试结尾增加断言：

```python
scores = {
    str(item.get("case_id") or ""): item
    for item in report["case_scores"]
}
assert report["metrics"]["overall"]["positive_cases"] == 2
assert report["metrics"]["source:knowledge"]["positive_cases"] == 1
assert scores["knowledge_fixture_positive_001"]["ok"] is True
assert scores["knowledge_fixture_positive_001"]["checks"]["citation"] is True
```

- [x] **步骤 6：运行 CLI fixture gate 红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate -v -p no:cacheprovider
```

预期：FAIL，失败原因是当前 fixture preset 只返回 1 个 positive case，report 中没有 `knowledge_fixture_positive_001`。

实际：FAIL，断言失败于 `assert 1 == 2`，当前 report 的 `metrics.overall.positive_cases` 仍为 1，符合红灯预期。

- [x] **步骤 7：更新 baseline 合同红灯**

在 `test_rag_benchmark_baseline_file_matches_manual_gate_contract` 末尾增加：

```python
knowledge_fixture_score = baseline_scores["knowledge_fixture_positive_001"]
assert knowledge_fixture_score["ok"] is True
assert knowledge_fixture_score["hit_at"]["5"] is True
assert knowledge_fixture_score["checks"]["citation"] is True
assert baseline["metrics"]["source:knowledge"]["positive_cases"] == 1
```

- [x] **步骤 8：运行 baseline 合同红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：FAIL，失败原因是 baseline 还没有 `knowledge_fixture_positive_001`。

实际：FAIL，`KeyError: 'knowledge_fixture_positive_001'`，符合红灯预期。

- [x] **步骤 9：记录红灯结果并延后提交**

任务 1 已记录红灯和 scorer 守卫结果。由于这些测试在生产代码实现前会让仓库处于 failing 状态，本阶段不单独提交红灯测试；测试变更将与任务 2 的最小实现一起提交，保证提交点为绿色状态。

## 任务 2：实现 knowledge fixture seed 与 case

**文件：**
- 修改：`evals/rag_benchmark/fixtures.py`
- 修改：`.Codex/plans/rag-knowledge-fixture-citation.md`

- [x] **步骤 1：扩展 imports 和常量**

修改 `evals/rag_benchmark/fixtures.py` 顶部 import：

```python
import json
from datetime import datetime
from pathlib import Path
```

修改业务 import：

```python
from core.database import Base, KnowledgeChunk, KnowledgeDocument
from core.semantic.adapters import SemanticChunk, chunk_from_knowledge_chunk
```

在 memory 常量后新增：

```python
KNOWLEDGE_CASE_ID = "knowledge_fixture_positive_001"
KNOWLEDGE_DOCUMENT_ID = 9001
KNOWLEDGE_CHUNK_ID = "chunk:0"
KNOWLEDGE_CANDIDATE_ID = f"knowledge:{KNOWLEDGE_DOCUMENT_ID}:{KNOWLEDGE_CHUNK_ID}"
KNOWLEDGE_QUERY = "RAG 引用门禁"
KNOWLEDGE_INDEX_VERSION = "fixture:v1:knowledge"
```

- [x] **步骤 2：新增 knowledge case builder**

在 `_memory_positive_case()` 后新增：

```python
def _knowledge_positive_case() -> BenchmarkCase:
    return BenchmarkCase(
        id=KNOWLEDGE_CASE_ID,
        suite="rag_benchmark",
        source_type="knowledge",
        case_type="positive",
        query=KNOWLEDGE_QUERY,
        filters={
            "min_trust_level": "low",
            "source_type": "manual_file",
        },
        expected={
            "candidate_ids": [KNOWLEDGE_CANDIDATE_ID],
            "hit_at": 5,
            "expected_source_type": "knowledge",
            "requires_citation": True,
        },
        meta={
            "origin": "fixture_exact",
            "sensitivity": "safe",
            "fixture": FIXTURE_PRESET,
        },
    )
```

把 `fixture_cases()` 的返回值改为：

```python
return [_memory_positive_case(), _knowledge_positive_case()]
```

- [x] **步骤 3：新增 knowledge seed helper**

在 `seed_positive_fixture_db()` 前新增：

```python
def _seed_knowledge_positive_fixture(db: Session) -> None:
    now = datetime(2026, 6, 20, 0, 0, 0)
    document = KnowledgeDocument(
        id=KNOWLEDGE_DOCUMENT_ID,
        document_kind="manual_file",
        title="RAG 引用门禁说明",
        published_at="2026-06-20",
        status="active",
        trust_level="medium",
        created_by="fixture",
        updated_by="fixture",
        latest_seen=now,
        meta_json=json.dumps({"fixture": FIXTURE_PRESET}, ensure_ascii=False, sort_keys=True),
        created_at=now,
        updated_at=now,
    )
    db.add(document)
    db.flush()

    citation = {
        "document_id": str(KNOWLEDGE_DOCUMENT_ID),
        "chunk_id": KNOWLEDGE_CHUNK_ID,
        "title": "RAG 引用门禁说明",
        "trust_level": "medium",
        "published_at": "2026-06-20",
    }
    chunk = KnowledgeChunk(
        document_id=KNOWLEDGE_DOCUMENT_ID,
        chunk_id=KNOWLEDGE_CHUNK_ID,
        order_index=0,
        title="RAG 引用门禁说明",
        text=(
            "RAG 引用门禁要求 knowledge 检索返回项必须携带 citation。"
            "固定 fixture 用于验证 requires_citation 评分不会被空结果绕过。"
        ),
        citation_json=json.dumps(citation, ensure_ascii=False, sort_keys=True),
        status="active",
        trust_level="medium",
        meta_json=json.dumps({"fixture": FIXTURE_PRESET}, ensure_ascii=False, sort_keys=True),
        created_at=now,
        updated_at=now,
    )
    db.add(chunk)
    db.flush()

    semantic_chunk = chunk_from_knowledge_chunk(chunk, document=document)
    upsert_semantic_chunks(
        db,
        [semantic_chunk],
        index_version=KNOWLEDGE_INDEX_VERSION,
    )
```

- [x] **步骤 4：接入 seed helper**

在 `seed_positive_fixture_db()` 中，memory `upsert_semantic_chunks()` 后新增：

```python
_seed_knowledge_positive_fixture(db)
```

确保函数仍返回：

```python
return fixture_cases(FIXTURE_PRESET)
```

- [x] **步骤 5：运行任务 2 定向绿灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_knowledge_positive_case \
  tests/test_rag_benchmark.py::test_scorer_fails_requires_citation_when_candidate_lacks_citation \
  -v -p no:cacheprovider
```

预期：全部通过，knowledge fixture score 的 `checks.citation` 为 `True`。

实际：第一次运行中 knowledge 新测试和 scorer 守卫通过，旧 memory 测试失败于仍假设 fixture preset 只有 1 个 case；修正为按 case id 查找 memory 结果后重跑，结果 `3 passed, 1 warning in 1.29s`。

- [x] **步骤 6：运行 citation 相邻回归**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_knowledge_rag.py::test_knowledge_query_returns_citations \
  tests/test_knowledge_rag.py::test_knowledge_result_without_citation_is_dropped \
  tests/test_rag_debug.py::test_rag_debug_query_runs_knowledge_search_with_citation \
  -v -p no:cacheprovider
```

预期：全部通过。

实际：`3 passed, 21 warnings in 1.84s`。

- [x] **步骤 7：延后任务 2 提交**

任务 2 已转绿，但 baseline 合同测试在 baseline 更新前仍会失败。为避免提交点包含 failing tests，任务 2 与任务 3 合并为一个绿色代码提交。

## 任务 3：更新 baseline 与 stable gate 合同

**文件：**
- 修改：`tests/test_rag_benchmark.py`
- 修改：`evals/baselines/rag_benchmark.json`
- 修改：`.Codex/plans/rag-knowledge-fixture-citation.md`

- [x] **步骤 1：运行 CLI fixture gate 测试确认当前状态**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate -v -p no:cacheprovider
```

预期：任务 2 后该测试通过。如果失败，优先检查临时 baseline 是否包含 memory 与 knowledge 两个 fixture score。

实际：PASS，`1 passed, 1 warning in 1.10s`。

- [x] **步骤 2：运行 RAG stable gate 生成待更新报告**

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

预期：baseline 更新前可能失败，原因是 `total_delta` 或 baseline case set 尚未同步。无论通过或失败，必须读取 `tmp/rag_benchmark/reports/latest.json`，只把真实 report 中的 `metrics` 与 `case_scores` 复制进 baseline。

实际：旧 baseline 下 gate 仍通过，输出 `cases=11 passed=11 failed=0` 和 `Gate passed`；latest report 显示 `overall.total_cases=11`、`overall.positive_cases=2`、`overall_fixture.total_cases=2`、`source:knowledge.positive_cases=1`，`knowledge_fixture_positive_001` 的 `checks.citation=true`。

- [x] **步骤 3：更新 baseline 文件**

将 `evals/baselines/rag_benchmark.json` 更新为 latest report 的稳定字段：

```json
{
  "suite": "rag_benchmark",
  "provider_mode": "deterministic",
  "case_scope": "manual+fixture",
  "metrics": {
    "overall": {
      "total_cases": 11,
      "positive_cases": 2
    },
    "overall_fixture": {
      "total_cases": 2,
      "positive_cases": 2
    },
    "source:knowledge": {
      "positive_cases": 1
    }
  },
  "failed_cases": [],
  "case_scores": []
}
```

实际文件必须保留完整 metrics 子字段和完整 `case_scores`，不得只写上面的摘录。

- [x] **步骤 4：运行 baseline 合同绿灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：PASS，baseline case set 与 manual + fixture cases 完全一致，并包含 `knowledge_fixture_positive_001`。

实际：PASS，`1 passed, 1 warning in 1.11s`。

- [x] **步骤 5：运行 RAG stable gate 绿灯**

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

预期：输出 `cases=11 passed=11 failed=0` 和 `Gate passed`。

实际：输出 `cases=11 passed=11 failed=0` 和 `Gate passed`。

- [x] **步骤 6：运行 RAG benchmark 相邻回归**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
```

预期：全部通过。`tests/test_eval_baseline.py` 不需要修改，因为 PR gate 与 periodic gate 脚本参数保持 `--fixture positive_v1` 不变。

实际：`37 passed, 1 warning in 2.78s`。

- [x] **步骤 7：提交任务 1-3 绿色代码阶段**

运行：

```bash
git add tests/test_rag_benchmark.py evals/rag_benchmark/fixtures.py evals/baselines/rag_benchmark.json .Codex/plans/rag-knowledge-fixture-citation.md
git commit -m "feat(评测): 增加 knowledge fixture 引用正例"
```

提交前确认 RAG stable gate 和相邻回归结果已记录到本计划。额外全量验证已完成：`1374 passed, 6 skipped, 139 warnings in 113.51s`。提交：`1d19b95 feat(评测): 增加 knowledge fixture 引用正例`。

## 任务 4：文档收口与最终验证

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/rag-knowledge-fixture-citation.md`

- [x] **步骤 1：更新 `docs/evals.md`**

记录 RAG stable gate 当前 scope：

```markdown
RAG stable gate 当前使用 `manual+fixture` scope：仓库内 manual `constraint_only` cases 加 `positive_v1` fixture cases。`positive_v1` 现在包含 memory 与 knowledge 两个正例，其中 knowledge fixture 固定验证 `requires_citation=true` 的非空候选。
```

- [x] **步骤 2：更新 `docs/todo.md`**

在路线项 8 的 P4-5D 之后追加 P4-5E 状态：

```markdown
- **P4-5E 验证状态（2026-06-20）**：RAG `positive_v1` fixture 已从 memory 单正例扩展为 memory + knowledge 双正例；新增 `knowledge_fixture_positive_001`，固定命中 `knowledge:9001:chunk:0`，并通过 `requires_citation=true` 的 citation check。RAG stable gate 输出 `cases=11 passed=11 failed=0` 和 `Gate passed`。
```

同步路线下一步：更多 fixture source 覆盖可继续，但当前 knowledge citation 正例已完成。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

在 P4-5D 后新增 P4-5E 阶段记录：

```markdown
## 当前详细计划：P4-5E RAG knowledge fixture 引用正例门禁

状态：P4-5E 已完成。设计文档为 `docs/superpowers/specs/2026-06-20-rag-knowledge-fixture-citation-design.md`，实现计划为 `.Codex/plans/rag-knowledge-fixture-citation.md`。本阶段复用 `positive_v1` fixture preset，不新增 gate 脚本参数，不改 Admin / WebUI，不改生产 DB schema。
```

同时记录任务提交 SHA、红灯、绿灯、RAG stable gate 和全量测试结果。

- [x] **步骤 4：更新本计划执行记录**

把任务 1 到任务 3 的真实命令输出摘要写回本计划，包括：

- 红灯失败原因。
- 定向测试通过数量。
- RAG stable gate 输出。
- 相邻回归输出。
- 各任务提交短 SHA。

- [x] **步骤 5：文档自检**

运行：

```bash
rg -n "T[O]DO|待[定]|T[B]D|F[I]XME|x{3}|X{3}|\\.\\.\\." docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-knowledge-fixture-citation.md
rg -n $'\357\277\275' docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-knowledge-fixture-citation.md
git diff --check -- docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-knowledge-fixture-citation.md
```

预期：前两个命令无匹配，`git diff --check` 无输出。

实际：占位符扫描无匹配，U+FFFD 扫描无匹配，`git diff --check` 无输出。

- [x] **步骤 6：运行最终相邻验证**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_rag_benchmark.py \
  tests/test_eval_baseline.py \
  tests/test_knowledge_rag.py \
  tests/test_rag_debug.py \
  -v -p no:cacheprovider
```

预期：全部通过。

实际：文档相邻验证结果为 `61 passed, 21 warnings in 7.07s`。

- [x] **步骤 7：运行 PR gate**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
bash scripts/run_eval_pr_gate.sh
```

预期：所有子 gate 通过，RAG gate 输出 `cases=11 passed=11 failed=0` 和 `Gate passed`。

实际：评测守卫结果为 `27 passed, 1 warning in 1.83s`，TimingGate 和 capability gates 均输出 `Gate passed`；RAG gate 输出 `cases=11 passed=11 failed=0` 和 `Gate passed`。

- [x] **步骤 8：运行全量测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failures。

实际：`1374 passed, 6 skipped, 139 warnings in 105.13s`。

- [ ] **步骤 9：提交任务 4**

运行：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-knowledge-fixture-citation.md
git commit -m "docs(评测): 收口 knowledge fixture 引用状态"
```

提交前确认文档自检、PR gate 和全量测试结果已记录到本计划。

## 提交边界

- 计划提交：`a8ab8b8 docs(计划): 记录 knowledge fixture 引用计划`
- 任务 1-3：`1d19b95 feat(评测): 增加 knowledge fixture 引用正例`
- 任务 4：`docs(评测): 收口 knowledge fixture 引用状态`
