# P4-4 RAG 标注闭环与 baseline gate 设计

## 背景

P4-1 到 P4-3 已经完成通用 `candidates → labeled → dataset case` 闭环、Admin 标注工作台契约化，以及 `capability_model_routing`、`capability_reply_contract`、`capability_rendering_contract` 三组能力数据集门禁。RAG 侧目前已有独立 benchmark 体系：`evals/rag_benchmark/` 定义 `BenchmarkCase`、`BenchmarkResult`、`CaseScore`，支持 manual / generated case、只读 DB 采样、deterministic provider、Admin WebUI 运行和 JSON / Markdown 报告。

当前缺口是 RAG benchmark 还没有仓库内稳定 baseline 与命令行 gate。`docs/evals.md` 也明确 RAG benchmark 因召回样本、索引上下文和评分口径独立，暂不并入通用 `EvalCase`。因此 P4-4 的第一刀应补齐专用 baseline diff / gate 和标注归档规则，而不是重写 RAG 查询主链路。

## 目标

- 为 RAG benchmark 增加仓库内稳定 baseline 文件，记录 metrics、failed case 和 case 级结果摘要。
- 为 `python -m evals.rag_benchmark.run` 增加 baseline diff 与 gate 参数，让本地可一条命令判断是否回归。
- 固化 manual / generated case 的标注归档流程：generated case 继续作为 DB 指纹绑定的候选来源，人工确认后保存为 manual case，manual case 才进入稳定 baseline gate。
- 保留 RAG 专用指标：`pass_rate`、`hit@1`、`hit@3`、`hit@5`、`mrr`、false positive、unexpected source、degraded、latency 和 reranker candidate 规模。
- 同步 `docs/evals.md`、`docs/todo.md` 和 `docs/plan_walkthrough.md`，把 P4-4 的运行命令、baseline 更新规则和下一步状态写清楚。

## 非目标

- 不把 `evals/rag_benchmark/` 强行并入通用 `evals.run` / `EvalCase` / `EvalOutput`。
- 不调整 `KnowledgeRagService`、`MemoryRagService`、`StickerRagService` 或 `GroupMemoryRetrievalService` 的召回、rerank、排序和阈值逻辑。
- 不修 H30 RAG `query()` 巨函数拆分；该维护项单独设计。
- 不接入 CI / PR workflow；更多 suite PR gate 留到 P4-5。
- 不引入真实外部 embedding / reranker 依赖作为默认 gate；默认 gate 使用 deterministic provider。

## 方案对比

### 方案 A：保留专用 RAG benchmark，并补 baseline gate

在 `evals/rag_benchmark/` 下新增专用 baseline 模块，复用现有 `aggregate_scores()`、`CaseScore` 和报告结构。CLI 增加 `--baseline`、`--min-pass-rate`、`--min-hit-at-5`、`--max-new-failures`、`--max-degraded-rate` 等参数。正式 gate 默认只加载 manual case，使用 deterministic provider 和只读 SQLite。

优点：指标完整、改动集中、与现有 RAG Admin 页面一致。缺点：与通用 `evals.run` 有两套 gate 实现，但两者领域模型不同，重复是可接受的。

### 方案 B：包装成通用 eval suite

新增 `rag_benchmark` runner，让通用 `evals.run --suite rag_benchmark` 调用 RAG adapter，再把 RAG 指标塞进 `EvalOutput.raw`。优点是 CLI 统一。缺点是通用 scorer 不理解 hit@K / MRR / source constraint，最终仍要绕回自定义 `raw` 评分，边界会变混乱。

### 方案 C：只增强 Admin WebUI 标注能力

继续使用现有 `/api/v1/admin/rag/benchmark/*`，增加页面提示和操作引导，不新增命令行 gate。优点是交互成本低。缺点是无法满足 baseline gate 目标，也不利于后续 P4-5 的自动化门禁。

## 决策

采用方案 A。P4-4 保留 RAG benchmark 专用模型，新增专用 baseline diff / gate，并把 Admin 已有 manual case 保存能力作为人工标注归档入口。通用 candidates 闭环仍用于聊天、TimingGate、模型路由和渲染等 `EvalCase` 数据集；RAG 的 manual case 由 `BenchmarkCase` 直接表达，避免损失召回指标。

## 架构设计

### 文件边界

- `evals/rag_benchmark/baseline.py`：新增 RAG benchmark baseline 读取、diff 和 gate 纯函数。
- `evals/rag_benchmark/run.py`：新增 CLI 参数，运行后附加 baseline diff / gate，并按 gate 结果返回退出码。
- `evals/rag_benchmark/report.py`：在报告 payload 中写入 baseline diff / gate，Markdown 报告展示关键门禁结果。
- `evals/baselines/rag_benchmark.json`：新增稳定 baseline，首版只覆盖仓库内 safe manual case。
- `tests/test_rag_benchmark.py`：补 baseline diff / gate 纯函数和 CLI 行为测试。
- `tests/test_rag_benchmark_admin.py`：补 Admin 运行响应中 gate 字段的测试，确保页面可以消费。
- `docs/evals.md`、`docs/todo.md`、`docs/plan_walkthrough.md`：同步 P4-4 状态与命令。

### Baseline 数据结构

RAG baseline 不复用 `SuiteReport`，使用更贴合 benchmark 的 JSON：

```json
{
  "suite": "rag_benchmark",
  "provider_mode": "deterministic",
  "case_scope": "manual",
  "metrics": {
    "overall": {
      "total_cases": 3,
      "passed_cases": 3,
      "pass_rate": 1.0,
      "hit@5": 0.0,
      "mrr": 0.0,
      "degraded_rate": 0.0
    }
  },
  "failed_cases": [],
  "case_scores": [
    {
      "case_id": "knowledge_manual_citation_constraint_001",
      "ok": true,
      "errors": []
    }
  ]
}
```

`metrics` 保留 `aggregate_scores()` 的完整输出；`case_scores` 只保存 case id、source type、case type、ok、rank、hit_at、mrr、errors、degraded 和 latency 等稳定摘要，不保存候选全文，避免 baseline 因文本片段或 metadata 变化产生噪声。

### Baseline diff

`build_rag_baseline_diff(current, baseline)` 输出：

- `baseline_path`
- `baseline_suite`
- `total_delta`
- `pass_rate_delta`
- `hit_at_5_delta`
- `mrr_delta`
- `degraded_rate_delta`
- `new_failed_cases`
- `fixed_cases`
- `still_failed_cases`
- `metric_deltas`

失败 case 集合以 `case_scores[*].ok == false` 为准。`metric_deltas` 至少包含 `overall.pass_rate`、`overall.hit@5`、`overall.mrr`、`overall.degraded_rate`、`overall.case_false_positive_rate` 和 `overall.unexpected_source_rate`。

### Gate 规则

CLI 支持以下参数：

- `--baseline evals/baselines/rag_benchmark.json`
- `--min-pass-rate 1.0`
- `--min-hit-at-5 0.0`
- `--min-mrr 0.0`
- `--max-new-failures 0`
- `--max-degraded-rate 0.0`
- `--max-unexpected-source-rate 0.0`
- `--manual-only`

首版正式命令使用 manual-only 和 deterministic provider：

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

`--generated` 默认仍指向 `tmp/rag_benchmark/generated`，便于人工本地审查 generated case；但正式 gate 必须显式排除 generated case，避免 baseline 依赖本地 DB 指纹。

### 标注归档闭环

RAG 的人工闭环采用 `generated → manual → baseline`：

1. 运行 Admin 或 CLI sampler，从只读 DB 生成 `tmp/rag_benchmark/generated/*.jsonl`。
2. 人工在 Admin RAG Benchmark 页面审查 generated case 的 query、expected candidate、召回候选和 metadata。
3. 人工确认后点击保存为 manual case；保存路径仍是 `evals/cases/rag_benchmark/manual/<case_id>.json`。
4. manual case 进入 `rag_benchmark` gate；generated case 只用于本地候选审查，不进入仓库 baseline。
5. 更新 baseline 时必须同时审查 manual case 内容和 gate 输出，不能只刷新 baseline 掩盖回归。

该闭环不经过 `EvalCandidate.expected` 契约，因为 RAG 的 `BenchmarkExpected` 字段包含 `candidate_ids`、`forbidden_candidate_ids`、`requires_citation`、`requires_sendable`、`requires_group_id` 和 candidate 数量 / 延迟约束；这些字段不是通用 scorer 的可评分字段。

## 错误处理

- baseline 文件缺失：如果传入 `--baseline`，CLI 返回非 0，并提示路径不存在。
- suite 不匹配：baseline 的 `suite` 不是 `rag_benchmark` 时 gate 失败。
- provider mode 不匹配：baseline 记录的 `provider_mode` 与当前 run 不一致时 gate 失败，避免 runtime provider 与 deterministic provider 混用。
- case scope 不匹配：baseline 记录 `case_scope=manual`，当前 run 使用 generated case 时 gate 失败或要求显式 `--allow-generated-baseline`；首版不实现允许开关。
- 无 case 执行：gate 失败，错误为 `no_cases_executed`。
- preflight 不通过：Admin route 保持现有返回结构，不写 latest report，不更新 baseline。

## 测试策略

### TDD 红灯

- `tests/test_rag_benchmark.py::test_rag_baseline_diff_reports_new_fixed_and_metric_deltas`
  - 先断言缺少 `evals.rag_benchmark.baseline` 或缺少函数失败。
- `tests/test_rag_benchmark.py::test_rag_benchmark_cli_fails_gate_on_new_failure`
  - 构造临时 manual case 与 baseline，模拟新增失败，断言 CLI 返回非 0。
- `tests/test_rag_benchmark.py::test_rag_benchmark_cli_passes_manual_deterministic_gate`
  - 使用 safe manual case 和临时 baseline，断言 gate 输出 `Gate passed`。
- `tests/test_rag_benchmark_admin.py::test_benchmark_run_returns_gate_when_baseline_requested`
  - P4-4B 验证 Admin 响应包含 `baseline_diff` 和 `gate`，并能被 WebUI 展示。

### 回归命令

```bash
python -B -m pytest tests/test_rag_benchmark.py tests/test_rag_benchmark_admin.py -v -p no:cacheprovider
```

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

```bash
python -B -m pytest tests/ -v -p no:cacheprovider
```

## 分阶段提交

- P4-4 设计文档：`docs(评测): 设计 RAG baseline 门禁`
- P4-4 实现计划：`docs(计划): 记录 RAG baseline 门禁计划`
- P4-4A baseline 纯函数和 CLI gate：`feat(评测): 支持 RAG baseline 门禁`
- P4-4B Admin / 文档闭环：`feat(评测): 展示 RAG 门禁结果`
- P4-4C 收口验证：`docs(评测): 收口 RAG 门禁状态`

## 验收标准

- 设计文档通过占位词扫描、U+FFFD 扫描和 `git diff --check`。
- 实现阶段存在仓库内 `evals/baselines/rag_benchmark.json`。
- RAG benchmark CLI 支持 baseline diff / gate 参数，gate 失败时返回非 0。
- 正式 RAG gate 命令使用 deterministic provider 和 manual-only case，输出 `Gate passed`。
- `docs/evals.md` 记录 RAG gate 命令、baseline 更新规则和 generated case 不入稳定 baseline 的原因。
- `docs/todo.md` 与 `docs/plan_walkthrough.md` 标记 P4-4 的设计、计划、实现和收口状态。
