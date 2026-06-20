# P4-5F RAG sticker fixture sendable 正例门禁实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 RAG benchmark 的 `positive_v1` fixture preset 中新增 sticker 正例，固定验证 `requires_sendable=true` 的可发送表情包候选。

**架构：** 复用现有 `positive_v1` preset，不新增 CLI 参数或新 preset。fixture builder 同时写入 memory、knowledge 与 sticker fixture 数据；sticker fixture 使用固定 `StickerMemory`、`chunk_from_sticker()` 和 `upsert_semantic_chunks()` 进入现有 sticker RAG 链路，再由 benchmark scoring 检查 fixed candidate 与 sendable bool。

**技术栈：** Python、pytest、SQLAlchemy SQLite fixture DB、RAG benchmark、Sticker RAG、现有 deterministic embedding / reranker provider。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-20-rag-fixture-sticker-sendable-design.md`
- 设计提交：`9008b0e docs(评测): 设计 sticker fixture 发送门禁`
- 当前范围：fixture builder、RAG benchmark 测试、baseline 合同、文档状态同步。
- 不纳入本计划：group_memory 正例、真实样本运营、Admin / WebUI、runtime provider、生产 DB schema、RAG 主流程重构、阈值调参。

## 子 agent 分工约定

本阶段核心写入集中在 `tests/test_rag_benchmark.py`、`evals/rag_benchmark/fixtures.py` 和 `evals/baselines/rag_benchmark.json`。为了减少冲突，默认由主线程完成测试与实现；可委派的任务只限以下类型：

- 只读核对 agent：检查 `tmp/rag_benchmark/reports/latest.json` 与 baseline 更新是否一致，不修改文件。
- 验证 agent：在主线程实现后运行相邻测试或 gate 脚本，返回命令、退出码和关键输出，不修改文件。
- 禁止多个 worker 同时修改 `tests/test_rag_benchmark.py` 或 `evals/rag_benchmark/fixtures.py`。
- 禁止把 group_memory 正例交给同阶段 worker 实现；它需要独立设计选择逻辑、证据约束和预算边界。

## 文件结构

- 修改：`tests/test_rag_benchmark.py`
  - 职责：新增 sticker fixture 正例红灯测试，补齐 sendable scoring 守卫，更新 CLI fixture gate 和 baseline 合同断言。
- 修改：`evals/rag_benchmark/fixtures.py`
  - 职责：在 `positive_v1` preset 中新增 sticker case，构建固定 `StickerMemory` / semantic index 数据。
- 修改：`evals/baselines/rag_benchmark.json`
  - 职责：同步 stable gate 的真实 metrics 与 case_scores，新增 `sticker_fixture_positive_001`。
- 修改：`docs/evals.md`
  - 职责：记录 RAG stable gate 已包含 memory、knowledge 与 sticker fixture 正例。
- 修改：`docs/todo.md`
  - 职责：把 P4-5F 状态写入路线项 8。
- 修改：`docs/plan_walkthrough.md`
  - 职责：记录本阶段提交、红绿灯和验证结果。
- 修改：`.Codex/plans/rag-fixture-sticker-sendable.md`
  - 职责：执行时勾选步骤并记录真实验证输出。

## 前置完成

- [x] **步骤 1：完成 P4-5F 设计文档**

设计文档已写入：

```text
docs/superpowers/specs/2026-06-20-rag-fixture-sticker-sendable-design.md
```

提交：

```text
9008b0e docs(评测): 设计 sticker fixture 发送门禁
```

- [x] **步骤 2：确认本阶段不混入 group_memory**

P4-5F 只验证 sticker sendable 正例。group_memory source 覆盖保留为后续独立阶段，避免在同一提交里混入不同选择逻辑、证据约束和渲染预算。

## 任务 1：测试先行固定 sticker fixture 合同

**文件：**
- 修改：`tests/test_rag_benchmark.py`
- 修改：`.Codex/plans/rag-fixture-sticker-sendable.md`

- [x] **步骤 1：新增 sticker fixture 红灯测试**

在 `tests/test_rag_benchmark.py` 中新增测试，放在现有 `test_rag_benchmark_fixture_db_supports_knowledge_positive_case` 后面：

```python
def test_rag_benchmark_fixture_db_supports_sticker_positive_case(tmp_path):
    from evals.rag_benchmark.fixtures import (
        STICKER_CANDIDATE_ID,
        STICKER_CASE_ID,
        STICKER_CHAT_STREAM_ID,
        build_fixture_db,
    )
    from evals.rag_benchmark.run import run_benchmark

    fixture_db = tmp_path / "positive.db"

    cases = build_fixture_db(fixture_db, preset="positive_v1")
    results, scores = run_benchmark(fixture_db, cases, provider_mode="deterministic")

    by_case = {case.id: case for case in cases}
    by_result = {result.case_id: result for result in results}
    by_score = {score.case_id: score for score in scores}

    assert STICKER_CASE_ID in by_case
    assert by_case[STICKER_CASE_ID].source_type == "sticker"
    assert by_case[STICKER_CASE_ID].filters["chat_stream_id"] == STICKER_CHAT_STREAM_ID
    assert by_case[STICKER_CASE_ID].filters["include_global"] is False
    assert by_case[STICKER_CASE_ID].expected.requires_sendable is True
    assert by_case[STICKER_CASE_ID].expected.candidate_ids == [STICKER_CANDIDATE_ID]

    sticker_result = by_result[STICKER_CASE_ID]
    sticker_score = by_score[STICKER_CASE_ID]
    assert sticker_result.candidate_ids[0] == STICKER_CANDIDATE_ID
    assert any(
        candidate.candidate_id == STICKER_CANDIDATE_ID and candidate.sendable is True
        for candidate in sticker_result.candidates
    )
    assert sticker_score.ok is True
    assert sticker_score.rank == 1
    assert sticker_score.hit_at["5"] is True
    assert sticker_score.checks["sendable"] is True
```

- [x] **步骤 2：运行 sticker fixture 红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_sticker_positive_case -v -p no:cacheprovider
```

预期：FAIL，失败原因包含 `ImportError`，因为 `STICKER_CASE_ID` 或 `STICKER_CANDIDATE_ID` 尚未定义。

实际：FAIL，`ImportError: cannot import name 'STICKER_CANDIDATE_ID' from 'evals.rag_benchmark.fixtures'`，符合红灯预期。

- [x] **步骤 3：补 sendable scoring 守卫测试**

在 `tests/test_rag_benchmark.py` 的 scoring 测试区域新增：

```python
def test_scorer_fails_requires_sendable_when_candidate_lacks_sendable():
    from evals.rag_benchmark.schema import BenchmarkCandidate, BenchmarkCase, BenchmarkResult
    from evals.rag_benchmark.scoring import score_case

    case = BenchmarkCase(
        id="sticker_requires_sendable",
        source_type="sticker",
        case_type="positive",
        query="开心拍桌表情包",
        expected={
            "candidate_ids": ["sticker:9101:sticker"],
            "requires_sendable": True,
            "expected_source_type": "sticker",
        },
    )
    result = BenchmarkResult(
        case_id=case.id,
        source_type="sticker",
        candidate_ids=["sticker:9101:sticker"],
        candidates=[
            BenchmarkCandidate(
                candidate_id="sticker:9101:sticker",
                source_type="sticker",
                rank=1,
                sendable=False,
            )
        ],
    )

    score = score_case(case, result)

    assert score.ok is False
    assert score.checks["sendable"] is False
    assert "sendable check failed" in score.errors
```

- [x] **步骤 4：运行 sendable 守卫测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_scorer_fails_requires_sendable_when_candidate_lacks_sendable -v -p no:cacheprovider
```

预期：PASS。该测试固定已有评分边界；若失败，先修 `evals/rag_benchmark/scoring.py` 的 `requires_sendable` 检查。

实际：PASS，`1 passed, 1 warning in 0.84s`。

- [x] **步骤 5：更新 CLI fixture gate 测试红灯**

在 `test_rag_benchmark_cli_runs_manual_fixture_positive_gate` 中，把临时 baseline 的 `overall.total_cases` 改为 `4`、`overall.positive_cases` 改为 `3`，并在 `case_scores` 中新增 sticker fixture：

```python
{
    "case_id": "sticker_fixture_positive_001",
    "ok": True,
    "rank": 1,
    "hit_at": {"1": True, "3": True, "5": True},
    "checks": {"citation": None, "sendable": True, "group_filter": None},
    "errors": [],
}
```

在该测试结尾增加或更新断言：

```python
scores = {
    str(item.get("case_id") or ""): item
    for item in report["case_scores"]
}
assert report["metrics"]["overall"]["positive_cases"] == 3
assert report["metrics"]["source:knowledge"]["positive_cases"] == 1
assert report["metrics"]["source:sticker"]["positive_cases"] == 1
assert scores["knowledge_fixture_positive_001"]["checks"]["citation"] is True
assert scores["sticker_fixture_positive_001"]["ok"] is True
assert scores["sticker_fixture_positive_001"]["checks"]["sendable"] is True
```

- [x] **步骤 6：运行 CLI fixture gate 红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate -v -p no:cacheprovider
```

预期：FAIL，失败原因是当前 fixture preset 只有 2 个 positive case，report 中没有 `sticker_fixture_positive_001`，或 `overall.positive_cases` 仍为 `2`。

实际：FAIL，断言失败于 `assert 2 == 3`，当前 report 的 `metrics.overall.positive_cases` 仍为 2，符合红灯预期。

- [x] **步骤 7：更新 baseline 合同红灯**

在 `test_rag_benchmark_baseline_file_matches_manual_gate_contract` 末尾增加：

```python
sticker_fixture_score = baseline_scores["sticker_fixture_positive_001"]
assert sticker_fixture_score["ok"] is True
assert sticker_fixture_score["hit_at"]["5"] is True
assert sticker_fixture_score["checks"]["sendable"] is True
assert baseline["metrics"]["source:sticker"]["positive_cases"] == 1
```

同时把已有宽松断言收紧为：

```python
assert baseline["metrics"]["overall"]["positive_cases"] == 3
```

- [x] **步骤 8：运行 baseline 合同红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：FAIL，失败原因是 baseline 还没有 `sticker_fixture_positive_001`。

实际：FAIL，断言失败于 `assert 2 == 3`，当前 baseline 的 `metrics.overall.positive_cases` 仍为 2，符合红灯预期。

- [x] **步骤 9：记录红灯结果并延后提交**

任务 1 的红灯测试会让仓库处于 failing 状态。本阶段不单独提交红灯测试；测试变更将与任务 2 的最小实现一起提交，保证提交点为绿色状态。

## 任务 2：实现 sticker fixture seed 与 case

**文件：**
- 修改：`evals/rag_benchmark/fixtures.py`
- 修改：`.Codex/plans/rag-fixture-sticker-sendable.md`

- [x] **步骤 1：扩展 imports**

修改 `evals/rag_benchmark/fixtures.py` 的业务 import：

```python
from core.database import Base, KnowledgeChunk, KnowledgeDocument, StickerMemory
from core.semantic.adapters import SemanticChunk, chunk_from_knowledge_chunk, chunk_from_sticker
```

- [x] **步骤 2：新增 sticker 常量**

在 knowledge 常量后新增：

```python
STICKER_CASE_ID = "sticker_fixture_positive_001"
STICKER_ID = 9101
STICKER_CANDIDATE_ID = f"sticker:{STICKER_ID}:sticker"
STICKER_CHAT_STREAM_ID = "group:rag-fixture-sticker"
STICKER_QUERY = "开心拍桌表情包"
STICKER_INDEX_VERSION = "fixture:v1:sticker"
```

- [x] **步骤 3：新增 sticker case builder**

在 `_knowledge_positive_case()` 后新增：

```python
def _sticker_positive_case() -> BenchmarkCase:
    return BenchmarkCase(
        id=STICKER_CASE_ID,
        suite="rag_benchmark",
        source_type="sticker",
        case_type="positive",
        query=STICKER_QUERY,
        filters={
            "chat_stream_id": STICKER_CHAT_STREAM_ID,
            "include_global": False,
        },
        expected={
            "candidate_ids": [STICKER_CANDIDATE_ID],
            "hit_at": 5,
            "expected_source_type": "sticker",
            "requires_sendable": True,
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
return [_memory_positive_case(), _knowledge_positive_case(), _sticker_positive_case()]
```

- [x] **步骤 4：新增 sticker seed helper**

在 `seed_positive_fixture_db()` 前新增：

```python
def _seed_sticker_positive_fixture(db: Session) -> None:
    now = datetime(2026, 6, 20, 0, 0, 0)
    sticker = StickerMemory(
        id=STICKER_ID,
        chat_stream_id=STICKER_CHAT_STREAM_ID,
        sticker_hash="fixture-sticker-positive-001",
        file_ref="https://example.com/fixture-sticker-positive-001.png",
        send_code="[CQ:image,file=https://example.com/fixture-sticker-positive-001.png]",
        name="开心拍桌",
        description="开心拍桌表情包，适合表达高兴、赞同和突然兴奋。",
        tags_json=json.dumps(["开心", "拍桌", "表情包"], ensure_ascii=False),
        emotions_json=json.dumps(["happy"], ensure_ascii=False),
        source_type="fixture",
        source_count=1,
        status="active",
        usage_count=0,
        first_seen=now,
        last_seen=now,
        meta_json=json.dumps({"fixture": FIXTURE_PRESET}, ensure_ascii=False, sort_keys=True),
        preview_status="pending",
        content_hash="fixture-sticker-positive-001",
        dedupe_status="unique",
        describe_status="ok",
        described_at=now,
        created_at=now,
    )
    db.add(sticker)
    db.flush()

    semantic_chunk = chunk_from_sticker(sticker)
    assert semantic_chunk is not None
    upsert_semantic_chunks(
        db,
        [semantic_chunk],
        index_version=STICKER_INDEX_VERSION,
    )
```

该 seed 必须满足 sticker hard gate：

- `status == "active"`
- `describe_status == "ok"`
- `dedupe_status != "duplicate"`
- `duplicate_of_id is None`
- `chunk_from_sticker(sticker)` 返回非空
- `send_code` 可被 `is_sticker_replyable()` 识别

- [x] **步骤 5：接入 seed helper**

在 `seed_positive_fixture_db()` 中，knowledge seed 后新增：

```python
_seed_sticker_positive_fixture(db)
```

确保函数仍返回：

```python
return fixture_cases(FIXTURE_PRESET)
```

- [x] **步骤 6：运行任务 2 定向绿灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_knowledge_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_sticker_positive_case \
  tests/test_rag_benchmark.py::test_scorer_fails_requires_sendable_when_candidate_lacks_sendable \
  -v -p no:cacheprovider
```

预期：全部通过，sticker fixture score 的 `checks.sendable` 为 `True`。

实际：第一次运行中 memory、knowledge 和 sendable 守卫通过，sticker fixture 失败于 `IndexError: list index out of range`。根因是 seed 写入未归一化 `chat_stream_id`，而 `StickerRagService` 查询 scope 会归一化；改为 seed 使用 `normalize_sticker_stream_id(chat_stream_id=STICKER_CHAT_STREAM_ID)` 后重跑，结果 `4 passed, 1 warning in 1.09s`。

- [x] **步骤 7：运行 sticker 相邻回归**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_sticker_rag.py \
  tests/test_sticker_memory.py \
  tests/test_semantic_adapters.py::test_sticker_chunk_excludes_send_code_and_file_path \
  tests/test_rag_debug.py::test_rag_debug_query_runs_sticker_search \
  -v -p no:cacheprovider
```

预期：全部通过。若失败，优先检查 fixture seed 是否污染了 sticker hard gate 或 semantic chunk 合同。

实际：`23 passed, 21 warnings in 3.26s`。

- [x] **步骤 8：延后任务 2 提交**

任务 2 已转绿后，baseline 合同测试在 baseline 更新前仍会失败。为避免提交点包含 failing tests，任务 1 与任务 2 跟任务 3 合并为一个绿色代码提交。

## 任务 3：更新 baseline 与 stable gate 合同

**文件：**
- 修改：`tests/test_rag_benchmark.py`
- 修改：`evals/baselines/rag_benchmark.json`
- 修改：`.Codex/plans/rag-fixture-sticker-sendable.md`

- [x] **步骤 1：运行 CLI fixture gate 测试确认当前状态**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate -v -p no:cacheprovider
```

预期：任务 2 后该测试通过。如果失败，优先检查临时 baseline 是否包含 memory、knowledge 与 sticker 三个 fixture score。

实际：PASS，`1 passed, 1 warning in 0.97s`。

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

预期：输出 `cases=12 passed=12 failed=0` 和 `Gate passed`。无论旧 baseline 是否已通过，都必须读取 `tmp/rag_benchmark/reports/latest.json`，只把真实 report 中的 `metrics` 与 `case_scores` 复制进 baseline。

实际：输出 `cases=12 passed=12 failed=0`、`Gate passed`，并写入 `tmp/rag_benchmark/reports/latest.json`。

- [x] **步骤 3：核对 latest report 的关键字段**

运行：

```bash
python - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("tmp/rag_benchmark/reports/latest.json").read_text(encoding="utf-8"))
scores = {str(item.get("case_id") or ""): item for item in report["case_scores"]}
print(report["metrics"]["overall"]["total_cases"])
print(report["metrics"]["overall"]["positive_cases"])
print(report["metrics"]["overall_fixture"]["total_cases"])
print(report["metrics"]["overall_fixture"]["positive_cases"])
print(report["metrics"]["source:sticker"]["positive_cases"])
print(scores["sticker_fixture_positive_001"]["checks"]["sendable"])
PY
```

预期输出：

```text
12
3
3
3
1
True
```

实际：核对结果为 `overall.total_cases=12`、`overall.positive_cases=3`、`overall_fixture.total_cases=3`、`overall_fixture.positive_cases=3`、`source:sticker.positive_cases=1`、`sticker_fixture_positive_001.checks.sendable=True`。

- [x] **步骤 4：更新 baseline 文件**

将 `evals/baselines/rag_benchmark.json` 更新为 latest report 的稳定字段。必须保留完整 metrics 子字段和完整 `case_scores`，关键合同如下：

```json
{
  "suite": "rag_benchmark",
  "provider_mode": "deterministic",
  "case_scope": "manual+fixture",
  "metrics": {
    "overall": {
      "total_cases": 12,
      "positive_cases": 3
    },
    "overall_fixture": {
      "total_cases": 3,
      "positive_cases": 3
    },
    "source:sticker": {
      "positive_cases": 1
    }
  },
  "failed_cases": [],
  "case_scores": [
    {
      "case_id": "sticker_fixture_positive_001",
      "ok": true,
      "checks": {
        "sendable": true
      }
    }
  ]
}
```

实际文件不能只写上面的摘录；需要同步 `tmp/rag_benchmark/reports/latest.json` 中完整的 `metrics` 与 `case_scores`。

实际：使用 `jq '{suite, provider_mode, case_scope, metrics, failed_cases, case_scores}' tmp/rag_benchmark/reports/latest.json` 机械更新 baseline。

- [x] **步骤 5：运行 baseline 合同绿灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：PASS，baseline case set 与 manual + fixture cases 完全一致，并包含 `sticker_fixture_positive_001`。

实际：PASS，`1 passed, 1 warning in 0.87s`。

- [x] **步骤 6：运行 RAG stable gate 绿灯**

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

预期：输出 `cases=12 passed=12 failed=0` 和 `Gate passed`。

实际：输出 `cases=12 passed=12 failed=0` 和 `Gate passed`。

- [x] **步骤 7：运行 RAG benchmark 相邻回归**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
```

预期：全部通过。`tests/test_eval_baseline.py` 不需要修改，因为 PR gate 与 periodic gate 脚本参数保持 `--fixture positive_v1` 不变。

实际：`39 passed, 1 warning in 2.27s`。

- [x] **步骤 8：提交任务 1-3 绿色代码阶段**

提交前确认 RAG stable gate 和相邻回归结果已记录到本计划。运行全量测试：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failures。

实际：`1377 passed, 6 skipped, 139 warnings in 105.89s`。

提交：

```bash
git add tests/test_rag_benchmark.py evals/rag_benchmark/fixtures.py evals/baselines/rag_benchmark.json .Codex/plans/rag-fixture-sticker-sendable.md
git commit -m "feat(评测): 增加 sticker fixture 发送正例"
```

## 任务 4：文档收口与最终验证

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/rag-fixture-sticker-sendable.md`

- [ ] **步骤 1：更新 `docs/evals.md`**

记录 RAG stable gate 当前 scope：

```markdown
RAG stable gate 当前使用 `manual+fixture` scope：仓库内 manual `constraint_only` cases 加 `positive_v1` fixture cases。`positive_v1` 现在包含 memory、knowledge 与 sticker 三个正例，其中 knowledge fixture 固定验证 `requires_citation=true`，sticker fixture 固定验证 `requires_sendable=true` 的可发送候选。
```

- [ ] **步骤 2：更新 `docs/todo.md`**

在路线项 8 的 P4-5E 之后追加 P4-5F 状态：

```markdown
- **P4-5F 验证状态（2026-06-20）**：RAG `positive_v1` fixture 已从 memory + knowledge 双正例扩展为 memory + knowledge + sticker 三正例；新增 `sticker_fixture_positive_001`，固定命中 `sticker:9101:sticker`，并通过 `requires_sendable=true` 的 sendable check。RAG stable gate 输出 `cases=12 passed=12 failed=0` 和 `Gate passed`。
```

同步路线下一步：sticker sendable 正例完成后，后续可继续 group_memory fixture 正例或真实样本运营动作。

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

在 P4-5E 后新增 P4-5F 阶段记录：

```markdown
## 当前详细计划：P4-5F RAG sticker fixture sendable 正例门禁

状态：P4-5F 已完成。设计文档为 `docs/superpowers/specs/2026-06-20-rag-fixture-sticker-sendable-design.md`，实现计划为 `.Codex/plans/rag-fixture-sticker-sendable.md`。本阶段复用 `positive_v1` fixture preset，不新增 gate 脚本参数，不改 Admin / WebUI，不改生产 DB schema，不启用 runtime provider。
```

同时记录任务提交 SHA、红灯、绿灯、RAG stable gate 和全量测试结果。

- [ ] **步骤 4：更新本计划执行记录**

把任务 1 到任务 3 的真实命令输出摘要写回本计划，包括：

- 红灯失败原因。
- 定向测试通过数量。
- RAG stable gate 输出。
- 相邻回归输出。
- 全量测试输出。
- 各任务提交短 SHA。

- [ ] **步骤 5：文档自检**

运行：

```bash
rg -n "T[O]DO|待[定]|T[B]D|F[I]XME|x{3}|X{3}|\\.\\.\\." docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-fixture-sticker-sendable.md
rg -n $'\357\277\275' docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-fixture-sticker-sendable.md
git diff --check -- docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-fixture-sticker-sendable.md
```

预期：前两个命令无匹配，`git diff --check` 无输出。

- [ ] **步骤 6：运行最终相邻验证**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_rag_benchmark.py \
  tests/test_eval_baseline.py \
  tests/test_sticker_rag.py \
  tests/test_sticker_memory.py \
  tests/test_semantic_adapters.py \
  tests/test_rag_debug.py \
  -v -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 7：运行统一评测脚本**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
bash scripts/run_eval_pr_gate.sh
bash scripts/run_eval_periodic.sh
```

预期：两个脚本均退出码 0。RAG gate 子项继续通过 `--fixture positive_v1` 自动覆盖新增 sticker fixture。

- [ ] **步骤 8：提交文档收口阶段**

提交前运行全量测试：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failures。

提交：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-fixture-sticker-sendable.md
git commit -m "docs(评测): 收口 sticker fixture 发送状态"
```

## 风险与回滚

- 如果 sticker fixture 未命中，优先检查 `chat_stream_id` scope、`send_code` 是否被 `is_sticker_replyable()` 接受，以及 `chunk_from_sticker()` 是否返回非空 chunk。
- 如果 deterministic reranker 排名不稳定，增强 `STICKER_QUERY` 与 `description` / `tags_json` 的词面重叠，不降低 gate 阈值。
- 如果 `checks.sendable` 为 `None` 或 `False`，说明 benchmark adapter 没有从 debug candidate 读到 `reply_token` / `send_code`，应修 fixture 或 adapter 边界。
- 回滚时只移除 sticker case、fixture seed、baseline 中对应 case，并把 positive case 数恢复为 2；memory 与 knowledge fixture 不应受影响。
