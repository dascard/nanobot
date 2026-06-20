# P4-5E RAG knowledge fixture 引用正例门禁设计

日期：2026-06-20

## 背景

P4-5D 已为 RAG benchmark 增加 `positive_v1` fixture preset，但当前 preset 只包含 `memory_fixture_positive_001`。`docs/plan_walkthrough.md` 和 `docs/todo.md` 的最新路线都指向继续扩展 fixture source：优先补齐 knowledge fixture positive case，用固定 citation 覆盖 `requires_citation`。

现有 knowledge manual case 已声明 `requires_citation=true`，但这些 case 都是 `constraint_only`，且允许空结果。评分器在 `allow_empty=true` 且无候选时会让 citation check 通过，因此它们无法证明「真实返回候选必须带 citation」。本阶段要补上这个正例门禁。

## 目标

在现有 RAG stable gate 中新增一个仓库自包含、确定性可复现的 knowledge fixture positive case。该 case 必须在 `provider_mode=deterministic` 下返回固定 knowledge candidate，并通过 `requires_citation=true` 的评分检查。

成功标准：

- `fixture_cases("positive_v1")` 返回 memory 与 knowledge 两类 fixture 正例。
- fixture SQLite DB 同时包含固定 `KnowledgeDocument`、`KnowledgeChunk` 和对应 `SemanticIndexItem`。
- 新增 case `knowledge_fixture_positive_001` 的 expected candidate 为固定 ID，且 `requires_citation=true`。
- RAG benchmark 运行后该 case `ok=true`、`hit@5=true`、`checks.citation=true`。
- `evals/baselines/rag_benchmark.json` 与新增 case 后的 stable gate 合同一致。
- PR gate 与 periodic gate 不需要新增脚本参数，继续通过 `--fixture positive_v1` 自动覆盖新增 knowledge fixture。

## 非目标

- 不从生产 DB 采样，不引入真实用户数据，不扩展 candidates 到 labeled 的运营闭环。
- 不改 Admin / WebUI，不新增 RAG Benchmark 页面能力，不改标注工作台。
- 不调 TimingGate 阈值、RAG 召回阈值、`hit@5` / `mrr` 门槛或 rerank 权重。
- 不启用 runtime provider，fixture 只服务离线 deterministic gate。
- 不改生产 DB schema。
- 不做通用 citation 产品化，不设计引用展示 UI 或跨平台引用渲染。
- 不重构 RAG 主流程；除非新增 fixture 暴露确定性 bug，否则不改 query、rerank 或 filter 算法。

## 现有合同

RAG benchmark 的 fixture 入口在 `evals/rag_benchmark/fixtures.py`：

- `FIXTURE_PRESET = "positive_v1"`。
- `fixture_cases()` 当前只返回 `memory_fixture_positive_001`。
- `build_fixture_db()` 会覆盖创建 fixture SQLite DB，并调用 `seed_positive_fixture_db()` 写入 fixture 数据。

RAG runner 已支持 fixture：

- `evals/rag_benchmark/run.py` 在启用 `--fixture positive_v1` 时追加 fixture cases。
- 启用 fixture 时 benchmark DB 切换为 `--fixture-db`。
- case scope 自动变为 `manual+fixture`。

scoring 已支持 citation 约束：

- `BenchmarkExpected.requires_citation` 已存在。
- `BenchmarkCandidate.citation` 是布尔值。
- `score_case()` 会在 `requires_citation=true` 时检查所有返回候选的 citation 是否为 `True`。

knowledge RAG 的 citation 来源链路为：

- `KnowledgeChunk.citation_json` 保存 citation。
- `chunk_from_knowledge_chunk()` 将 citation 写入 `SemanticChunk.metadata["citation"]`。
- `upsert_semantic_chunks()` 将 metadata 写入 `SemanticIndexItem.meta_json`。
- `KnowledgeRagService.query()` 从 semantic index 的 `meta_json` 读取 citation；citation 无效时跳过候选。

## 方案

采用最小变更方案：复用 `positive_v1` fixture preset，在同一个 preset 中新增 knowledge fixture positive case，不新增 `positive_v2` 或单独 preset。

新增固定常量：

- `KNOWLEDGE_CASE_ID = "knowledge_fixture_positive_001"`
- `KNOWLEDGE_DOCUMENT_ID = 9001`
- `KNOWLEDGE_CHUNK_ID = "chunk:0"`
- `KNOWLEDGE_CANDIDATE_ID = "knowledge:9001:chunk:0"`
- `KNOWLEDGE_QUERY` 使用与 fixture 文本强相关的中文查询，例如「RAG 引用门禁」。
- `KNOWLEDGE_INDEX_VERSION = "fixture:v1:knowledge"`

新增 `_knowledge_positive_case()`：

- `source_type="knowledge"`。
- `case_type="positive"`。
- `expected.candidate_ids=[KNOWLEDGE_CANDIDATE_ID]`。
- `expected.hit_at=5`。
- `expected.expected_source_type="knowledge"`。
- `expected.requires_citation=true`。
- `meta.origin="fixture_exact"`。
- `meta.fixture="positive_v1"`。

扩展 `fixture_cases("positive_v1")`：

- 返回顺序固定为 `[memory_case, knowledge_case]`。
- 保持 memory case 的 ID 与 expected candidate 不变，避免已有 baseline case 语义漂移。

扩展 `seed_positive_fixture_db()`：

- 先保留已有 memory semantic chunk 写入。
- 再插入固定 ID 的 `KnowledgeDocument`：
  - `id=9001`
  - `document_kind="manual_file"`
  - `title="RAG 引用门禁说明"`
  - `status="active"`
  - `trust_level="medium"`
  - `published_at="2026-06-20"`
  - `latest_seen` 使用固定或当前时间均可，但测试只依赖固定 candidate 与 citation。
- 插入 `KnowledgeChunk`：
  - `document_id=9001`
  - `chunk_id="chunk:0"`
  - `title="RAG 引用门禁说明"`
  - `text` 包含查询关键词和唯一内容。
  - `citation_json` 包含 `document_id`、`chunk_id`、`title`、`trust_level`、`published_at`。
- 使用 `chunk_from_knowledge_chunk(chunk, document=document)` 生成 `SemanticChunk`。
- 使用 `upsert_semantic_chunks()` 写入 semantic index 和 FTS，`index_version` 固定为 `KNOWLEDGE_INDEX_VERSION`。

## 数据流

1. `build_fixture_db()` 创建空 SQLite fixture DB。
2. `seed_positive_fixture_db()` 写入 memory 与 knowledge fixture 数据。
3. RAG runner 加载 manual cases，再追加 `fixture_cases("positive_v1")`。
4. `run_benchmark()` 在 fixture DB 上执行所有 stable cases。
5. knowledge case 进入 `KnowledgeRagService.query()`。
6. service 从 `SemanticIndexItem.meta_json` 读取 citation，生成带 citation 的 debug candidate。
7. adapter 将 debug candidate 转为 `BenchmarkCandidate(citation=True)`。
8. scoring 命中 `KNOWLEDGE_CANDIDATE_ID`，并通过 citation check。
9. baseline diff 与 gate 使用新增后的 metrics 和 case_scores。

## 测试策略

测试先行，先新增或更新以下断言：

- 新增 `test_rag_benchmark_fixture_db_supports_knowledge_positive_case`：
  - 构建 fixture DB。
  - 运行 deterministic benchmark。
  - 断言 `knowledge_fixture_positive_001` 存在。
  - 断言结果包含 `knowledge:9001:chunk:0`。
  - 断言 score `ok=true`、`hit@5=true`、`checks.citation=true`。
- 更新 `test_rag_benchmark_cli_runs_manual_fixture_positive_gate`：
  - 临时 baseline 包含 memory 与 knowledge 两个 fixture 正例。
  - 断言 overall positive cases 为 2。
  - 断言 `source:knowledge` 有 1 个 positive case。
  - 断言新增 case 的 citation check 为 true。
- 更新 `test_rag_benchmark_baseline_file_matches_manual_gate_contract`：
  - baseline case set 与 manual + fixture cases 精确一致。
  - 断言 baseline 中的 knowledge fixture case 通过 citation check。
- 可补充 scorer 守卫：
  - 构造 `requires_citation=true` 且候选 `citation=False` 的结果，断言评分失败。
  - 这条主要固定评分边界，当前实现大概率已通过，不作为核心红灯。

## Baseline 与 gate

实现后必须运行 deterministic RAG stable gate 生成报告，再用真实报告更新 `evals/baselines/rag_benchmark.json`。预期合同变化：

- `overall.total_cases` 从 10 增至 11。
- `overall.positive_cases` 从 1 增至 2。
- `overall_fixture.total_cases` 从 1 增至 2。
- `overall_fixture.positive_cases` 从 1 增至 2。
- `source:knowledge.positive_cases` 从 0 增至 1。
- `case_scores` 新增 `knowledge_fixture_positive_001`，且 `checks.citation=true`。

`scripts/run_eval_pr_gate.sh` 和 `scripts/run_eval_periodic.sh` 已使用 `--fixture positive_v1`，最小方案下不需要修改。

## 验收

定向测试：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_knowledge_positive_case \
  -v -p no:cacheprovider
```

CLI 与 baseline 合同测试：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate \
  tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract \
  -v -p no:cacheprovider
```

citation 相邻回归：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_knowledge_rag.py::test_knowledge_query_returns_citations \
  tests/test_knowledge_rag.py::test_knowledge_result_without_citation_is_dropped \
  tests/test_rag_debug.py::test_rag_debug_query_runs_knowledge_search_with_citation \
  -v -p no:cacheprovider
```

RAG stable gate：

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

提交前全量验证：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -m pytest tests/ -v
```
