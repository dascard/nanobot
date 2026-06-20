# RAG fixture 正例门禁设计

设计日期：2026-06-20

## 背景

P4-4 已为 `evals.rag_benchmark` 建立专用 baseline diff 和 gate，P4-5A / P4-5B 已把 RAG deterministic gate 接入 PR gate、周期性复跑和报告归档。P4-5C 已把仓库内 manual 样本从 3 个扩充到 9 个，并收紧 baseline 与 enabled manual case 集合的一致性。

当前 RAG baseline 仍全部是 `constraint_only`：

- `metrics.overall.positive_cases = 0`
- `metrics.overall.hit@5 = 0.0`
- `metrics.overall.mrr = 0.0`

这意味着 gate 能证明过滤、source 类型、citation、sendable、group id 和候选数量上限等约束没有退化，但不能证明「查询能召回某个固定目标候选」。`score_case()`、`BenchmarkExpected.candidate_ids` 和 `aggregate_scores()` 已经支持 positive case；缺口在稳定数据来源与 gate 入口。

`evals.rag_benchmark.run` 当前默认用 `--db data/nanobot.db`，执行时通过 `_readonly_session()` 以只读 SQLite URI 打开数据库。PR gate 和周期性 gate 都直接运行：

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

如果只把 positive JSON 放进 manual 目录，gate 会在空库或真实库上找不到对应 candidate id，变成环境相关。因此本阶段要先引入仓库自包含 fixture DB，再把 stable gate 切到「manual constraint + fixture positive」。

## 目标

- 新增固定 RAG fixture DB builder，在进入只读 runner 之前创建 SQLite 文件库。
- 首个 fixture-backed positive case 使用 `memory` / `memory_digest`，验证 deterministic provider 下能命中固定 candidate id。
- 让 PR gate 和周期性 gate 都运行同一套 `manual+fixture` 稳定范围。
- 更新 `evals/baselines/rag_benchmark.json`，使 baseline 至少包含 1 个 positive case，`hit@5` 和 `mrr` 有真实信号。
- 保持 fixture seed、runner、scorer、case loader 的职责边界清晰。
- 补测试守卫，防止后续退回全 `constraint_only` baseline。

## 非目标

- 不接入 runtime provider，不依赖外部 embedding / reranker 模型。
- 不从真实生产 DB 复制 candidate id，不提交真实 DB 派生数据。
- 不改 Admin API 或 WebUI；运营页面仍可运行已有 manual / generated case。
- 不在 scorer 或 adapter 内写 fixture 数据。
- 不重构三路 RAG 查询主链路；H30 的大规模拆分后续单独做。
- 不在首个正例中覆盖 knowledge citation、sticker sendable 或 group memory 选择策略；这些作为后续 fixture 扩展。

## 方案对比

### 方案 A：只在测试里建临时 fixture DB

在 `tests/test_rag_benchmark.py` 内增加 helper，插入一条 `SemanticChunk`，直接调用 `run_benchmark()` 验证 positive score。

优点：改动最小，能快速证明 runner 的只读路径可命中 positive candidate。缺点：PR gate 和周期性 gate 仍然跑 9 个 `constraint_only` case，baseline 的 `positive_cases` 仍为 0，无法解决当前指标空洞。

### 方案 B：新增 eval 专用 fixture DB builder，并让 gate 显式启用 fixture

新增 `evals/rag_benchmark/fixtures.py`。CLI 增加 `--fixture positive_v1` 和 `--fixture-db`，启用后先创建固定 SQLite fixture DB，再把 fixture cases 合并到已加载的 manual cases 中，最后用现有只读 runner 执行。PR gate、周期性 gate 和 baseline 合同测试都切到 `manual+fixture`。

优点：仓库自包含、可重复、覆盖真实只读 runner 与 gate 入口；baseline 能记录 positive metrics。缺点：需要更新 runner 参数、脚本、baseline 和测试合同。

### 方案 C：直接把 fixture seed 字段写进 case JSON

给 case JSON 增加 `fixture` 或 `seed` 字段，runner 按 case 自动写库。

优点：case 和数据种子绑定更直观。缺点：会把 case loader、runner 和 DB 写入耦合起来；manual case 不再是纯输入描述，未来 Admin / WebUI 保存 case 时也要理解 seed schema，范围扩大。

## 决策

采用方案 B。

fixture 是 eval 运行环境的一部分，不是 case schema 的一部分。`fixtures.py` 负责建库和返回与 seed 数据匹配的 `BenchmarkCase`；`run.py` 只负责在 fixture 启用时调用 builder、合并 cases、选择正确 DB 路径；`scoring.py` 继续保持纯评分。

## Fixture 数据设计

首个 positive case 使用 `memory` 路径，底层 seed 一条 `memory_digest` 语义索引：

| 字段 | 值 |
|------|----|
| `source_type` | `memory_digest` |
| `source_id` | `fixture-memory-positive-001` |
| `source_sub_id` | `card:0` |
| `title` | `KohakuVQ 端口冲突排查` |
| `text` | 包含 `KohakuVQ`、`端口冲突`、`uvicorn`、`8000` 等查询词 |
| `lexical_text` / `embedding_text` | 与召回查询词强重叠 |
| `metadata.user_id` | `rag_fixture_user` |
| `metadata.session_id` | `rag_fixture_session` |
| `visibility` | `recall` |
| `quality_score` | `0.9` |
| `source_prior` | `0.65` |

写入方式：

1. 创建文件 SQLite。
2. `Base.metadata.create_all(bind=engine)` 创建 ORM 表。
3. 构造 `SemanticChunk`。
4. 调用 `upsert_semantic_chunks(db, [chunk], index_version="fixture:v1:memory")`。
5. `upsert_semantic_chunks()` 负责创建 `semantic_index_fts`、写 `semantic_index_items` 和同步 FTS rowid。

不手写 `semantic_index_fts`。FTS5 虚表不由 SQLAlchemy metadata 管理，手写容易造成 rowid 与 `semantic_index_items.id` 不一致。

对应 case：

```json
{
  "id": "memory_fixture_positive_001",
  "suite": "rag_benchmark",
  "source_type": "memory",
  "case_type": "positive",
  "query": "KohakuVQ 端口冲突",
  "filters": {
    "source": "digest",
    "user_id": "rag_fixture_user",
    "session_id": "rag_fixture_session"
  },
  "expected": {
    "candidate_ids": ["memory_digest:fixture-memory-positive-001:card:0"],
    "hit_at": 5,
    "expected_source_type": "memory_digest"
  },
  "meta": {
    "origin": "fixture_exact",
    "sensitivity": "safe",
    "fixture": "positive_v1"
  }
}
```

`MemoryRagService` 的最终业务 `items` 会按父级聚合，但 benchmark adapter 读取 `debug_trace.final_candidates`，candidate id 仍是 `memory_digest:<source_id>:<source_sub_id>`。因此 expected candidate id 使用 `memory_digest:fixture-memory-positive-001:card:0`。

## 新增接口

### `evals/rag_benchmark/fixtures.py`

提供以下接口：

```python
FIXTURE_PRESET = "positive_v1"

def fixture_cases(preset: str = FIXTURE_PRESET) -> list[BenchmarkCase]:
    """返回 fixture preset 对应的 case 描述，不写数据库。"""

def seed_positive_fixture_db(db: Session) -> list[BenchmarkCase]:
    """向已创建 schema 的数据库写入 positive fixture 数据，并返回 case。"""

def build_fixture_db(path: str | Path, *, preset: str = FIXTURE_PRESET) -> list[BenchmarkCase]:
    """覆盖创建 fixture SQLite 文件库，写入 preset 数据，并返回 fixture cases。"""
```

`fixture_cases()` 供 baseline 合同测试读取 case id，不需要建库；`build_fixture_db()` 供 CLI 和集成测试使用。

### `evals/rag_benchmark/run.py`

新增参数：

```text
--fixture {none,positive_v1}
--fixture-db tmp/rag_benchmark/fixtures/positive_v1.db
```

行为：

- 默认 `--fixture none`，完全保留现有行为。
- 启用 `--fixture positive_v1` 时，runner 调用 `build_fixture_db(args.fixture_db, preset=args.fixture)`。
- fixture cases 追加到 `load_cases()` 返回的 case 列表后面。
- benchmark DB 路径改为 fixture DB；`--db` 仍保留给非 fixture 模式使用。
- `case_scope` 从 `manual` 变为 `manual+fixture`；如果以后同时启用 generated，则使用 `manual+generated+fixture`。

fixture DB 默认写到 `tmp/rag_benchmark/fixtures/positive_v1.db`，避免误覆盖 `data/nanobot.db`。

### `evals/rag_benchmark/scoring.py`

新增 origin 分组：

```python
if origin == "fixture_exact":
    return "overall_fixture"
```

`aggregate_scores()` 输出增加 `overall_fixture`，保留已有 `overall_exact`、`overall_weak`、`overall_manual`。baseline diff 和 gate 仍使用 `metrics.overall`，不会破坏已有阈值逻辑。

## Baseline 合同

更新 `tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract`，合同范围从 manual 改为 manual + fixture：

- enabled manual case id 集合必须包含在 baseline case id 中。
- `fixture_cases("positive_v1")` 的 case id 必须包含在 baseline case id 中。
- baseline case id 集合必须等于 manual enabled case id 与 fixture case id 的并集。
- `baseline["case_scope"] == "manual+fixture"`。
- `baseline["metrics"]["overall"]["total_cases"] == len(manual_cases) + len(fixture_cases)`。
- `baseline["metrics"]["overall"]["positive_cases"] >= 1`。
- `baseline["metrics"]["overall"]["hit@5"] > 0`。
- `baseline["metrics"]["overall"]["mrr"] > 0`。
- `memory_fixture_positive_001` 的 `case_scores` 必须 `ok=true` 且 `hit_at["5"] == true`。

这样后续如果删除 fixture、忘记更新 baseline、或 positive case 退化为空结果，合同测试会红灯。

## 脚本接入

`scripts/run_eval_pr_gate.sh` 与 `scripts/run_eval_periodic.sh` 的 RAG 步骤改为：

```bash
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

首个 stable fixture 只有 1 个 positive case，因此 `--min-hit-at-5 1.0` 和 `--min-mrr 1.0` 是合理硬门槛；如果后续加入 weak fixture 或允许非首位命中，需要同步调整阈值和 baseline。

## Baseline 更新规则

实现完成后运行 fixture gate，用 `tmp/rag_benchmark/reports/latest.json` 更新 `evals/baselines/rag_benchmark.json`：

- `provider_mode` 仍为 `deterministic`。
- `case_scope` 改为 `manual+fixture`。
- `total_cases` 从 9 增加到 10。
- `positive_cases` 从 0 增加到 1。
- `hit@1`、`hit@3`、`hit@5` 和 `mrr` 预期为 `1.0`。
- 新增 `memory_fixture_positive_001` 的 `case_scores`，`ok=true`、`rank=1`、`hit_at["5"]=true`。
- `failed_cases` 继续为空。

如果 fixture positive 未命中，不刷新 baseline，应先修 fixture seed、query 或 runner 接入。

## 测试策略

### 红灯 1：fixture builder 缺失

新增 `tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case`：

- 调用 `build_fixture_db(tmp_path / "fixture.db", preset="positive_v1")`。
- 用返回的 cases 调 `run_benchmark(fixture_db, cases, provider_mode="deterministic")`。
- 断言 `memory_fixture_positive_001` 命中 `memory_digest:fixture-memory-positive-001:card:0`。
- 断言 score `ok`、`rank == 1`、`hit_at["5"] is True`、`mrr == 1.0`。

旧代码没有 `evals.rag_benchmark.fixtures`，预期红灯为 `ModuleNotFoundError`。

### 红灯 2：CLI 不支持 fixture

新增 `tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate`：

- 构造一个临时 manual `constraint_only` case。
- 构造包含 manual + fixture case 的临时 baseline。
- 调 `rag_run.main(["--fixture", "positive_v1", "--fixture-db", str(tmp_path / "fixture.db")])`，实际测试中补齐 manual、generated、report 和 baseline 参数。
- 断言 exit code 为 0、输出 `Gate passed`、报告 `case_scope == "manual+fixture"`、`positive_cases == 1`。

旧代码不认识 `--fixture`，预期红灯为 argparse 退出或 exit code 非 0。

### 红灯 3：仓库 baseline 合同仍是全 constraint-only

更新现有 baseline 合同测试，要求 fixture case 与 positive metrics 存在。旧 baseline 仍 `case_scope=manual` 且 `positive_cases=0`，预期红灯。

### 绿灯与回归

实现后运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py -v -p no:cacheprovider
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
bash scripts/run_eval_pr_gate.sh
bash scripts/run_eval_periodic.sh
python -B -m pytest tests/ -v -p no:cacheprovider
```

## 风险与边界

- `semantic_index_fts` 必须由 `upsert_semantic_chunks()` 创建，不能只依赖 `Base.metadata.create_all()`。
- fixture DB builder 会覆盖 `--fixture-db` 指向的文件；默认路径必须在 `tmp/` 下，避免误删生产数据。
- deterministic reranker 只看 query 与候选文本重叠，不读取 expected；fixture 文本必须包含查询词，避免 hash tie 影响排序。
- manual `constraint_only` case 在 fixture DB 上也会执行；新增 fixture 数据不能破坏 sentinel filter case 的空结果约束。
- baseline `case_scope` 改为 `manual+fixture` 后，脚本和 Admin 默认 baseline 行为要区分。CLI 默认不启用 fixture，只有脚本显式启用；Admin / WebUI 不随本阶段改动切换默认行为。
- 如果后续增加 knowledge 或 sticker fixture，需要分别处理 citation、scope、sendable 和业务表硬过滤，不能复用 memory seed 的简化假设。

## 验收标准

- `evals/rag_benchmark/fixtures.py` 能创建固定 SQLite fixture DB。
- `evals.rag_benchmark.run --fixture positive_v1` 会自动创建 fixture DB、追加 fixture case，并以只读方式执行。
- `scripts/run_eval_pr_gate.sh` 和 `scripts/run_eval_periodic.sh` 均运行 `manual+fixture` RAG gate。
- `evals/baselines/rag_benchmark.json` 包含 `memory_fixture_positive_001`，`positive_cases >= 1`，`hit@5 > 0`，`mrr > 0`。
- RAG fixture gate 输出 `cases=10 passed=10 failed=0` 和 `Gate passed`。
- 定向回归、PR gate、周期性 gate 和全量测试通过。
- 文档同步 `docs/evals.md`、`docs/todo.md`、`docs/plan_walkthrough.md` 和实现计划状态。
