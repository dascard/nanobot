# RAG Benchmark 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:test-driven-development 逐步实现。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 memory、group_memory、sticker、knowledge 建立只读、可重复运行的 RAG 检索质量 benchmark。

**架构：** 在 `evals/rag_benchmark/` 下新增 case schema、sampler、adapter、scorer、runner、reporter。Sampler 从 `data/nanobot.db` 只读抽取 generated case；runner 通过 adapter 直接调用 RAG 服务并标准化结果，scorer 不依赖服务内部返回结构。

**技术栈：** Python、Pydantic、SQLAlchemy、SQLite readonly URI、pytest。

---

### 任务 1：Case 与结果模型

**文件：**
- 创建：`evals/rag_benchmark/schema.py`
- 创建：`evals/rag_benchmark/cases.py`
- 测试：`tests/test_rag_benchmark.py`

- [x] 编写失败测试：case loader 能合并 manual/generated，并支持 `positive`、`negative`、`constraint_only`。
- [x] 实现 `BenchmarkCase`、`BenchmarkExpected`、`BenchmarkCandidate`、`BenchmarkResult`。
- [x] 实现 JSON/JSONL case loader。
- [x] 运行：`python -B -m pytest tests/test_rag_benchmark.py -q`。

### 任务 2：Scorer 与聚合指标

**文件：**
- 创建：`evals/rag_benchmark/scoring.py`
- 测试：`tests/test_rag_benchmark.py`

- [x] 编写失败测试：hit@K、MRR、false positive、constraint-only、citation/sendable/group filter 约束。
- [x] 实现单 case scoring 和汇总 metrics。
- [x] 运行 scorer 相关测试。

### 任务 3：Sampler

**文件：**
- 创建：`evals/rag_benchmark/sample.py`
- 测试：`tests/test_rag_benchmark.py`

- [x] 编写失败测试：SQLite readonly 下 sampler 不写 DB。
- [x] 编写失败测试：group_memory gate、sticker gate、knowledge citation 对齐。
- [x] 实现四类 sampler，默认输出 `tmp/rag_benchmark/generated/`。
- [x] 实现 CLI：`python -m evals.rag_benchmark.sample`。

### 任务 4：Adapter 与 Runner

**文件：**
- 创建：`evals/rag_benchmark/adapters.py`
- 创建：`evals/rag_benchmark/run.py`
- 测试：`tests/test_rag_benchmark.py`

- [x] 编写失败测试：四类 adapter 返回统一 `BenchmarkResult`。
- [x] 实现 memory/sticker/knowledge/group_memory adapter。
- [x] 实现 runner，默认不走 HTTP，不写 `rag_debug_runs`。
- [x] 实现 CLI：`python -m evals.rag_benchmark.run`。

### 任务 5：Reporter 与手写 case

**文件：**
- 创建：`evals/rag_benchmark/report.py`
- 创建：`evals/cases/rag_benchmark/manual/*.json`
- 测试：`tests/test_rag_benchmark.py`

- [x] 编写失败测试：报告默认写入 `tmp/rag_benchmark/reports/`。
- [x] 实现 JSON/Markdown 报告。
- [x] 添加少量 safe manual hard case，覆盖 sticker 泛词、knowledge citation、group filter。
- [x] 运行相关测试与全量测试。
