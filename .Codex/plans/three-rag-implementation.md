# Nanobot 三类 RAG 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 完整实施 `docs/goal.md` 的三类 RAG 计划。

**架构：** 先落地统一评分、FTS5、reranker、debug 骨架，再实现 SourceAdapter/Indexer/Worker，最后逐步接入 Memory、GroupMemory、Sticker、Knowledge、ai_daily 和 group_analysis。每个阶段都用 TDD、Web 审查入口和测试报告闭环。

**技术栈：** Python、FastAPI、SQLAlchemy、SQLite FTS5、pytest、React/Vite。

---

### 任务 1：评分、FTS5、Reranker、Debug 骨架

**文件：**
- 创建：`core/semantic/scoring.py`
- 创建：`core/semantic/fts.py`
- 创建：`core/semantic/reranker.py`
- 创建：`core/semantic/schema.py`
- 创建：`api/admin/rag_routes.py`
- 创建：`webui/src/features/rag/RagDebugPage.jsx`
- 创建：`tests/fakes/semantic.py`
- 创建：`tests/test_semantic_scoring.py`
- 创建：`tests/test_rag_debug.py`
- 修改：`core/database.py`
- 修改：`core/schema_migrations.py`
- 修改：`api/admin_routes.py`
- 修改：`webui/src/App.jsx`
- 报告：`docs/superpowers/test-reports/rag/01-semantic-scoring-fts5-reranker.md`

- [ ] **步骤 1：编写失败的测试**

覆盖：
- `weighted_score` 对 `None` 重分配权重，对 `0` 不重分配。
- SQLite BM25 越小越相关。
- FTS5 不可用时标记 degraded。
- FTS5 query 需要 escape 特殊字符，短中文返回空 fallback。
- reranker score 归一化，reranker 缺失触发 degraded。
- source quota 在来源数超过 total_k 时不超额。
- RAG debug API 可保存/查询 run。
- WebUI 注册 `/rag-debug` 页面与导航。

- [ ] **步骤 2：运行测试验证失败**

运行：
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py -q`

预期：FAIL，原因是模块、函数、API 或页面尚不存在。

- [ ] **步骤 3：写最小实现**

实现 Commit 1 的基础能力，不接入具体业务 RAG。

- [ ] **步骤 4：运行测试验证通过**

运行：
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py -q`

预期：PASS。

- [ ] **步骤 5：生成阶段报告**

运行：
`python scripts/rag_write_test_report.py --phase 01-semantic-scoring-fts5-reranker --pytest-output /tmp/pytest-rag-phase1.txt --web-debug-output /tmp/rag-debug-phase1.json --out docs/superpowers/test-reports/rag/01-semantic-scoring-fts5-reranker.md`

### 任务 2：Adapter / Chunker / Indexer

**文件：**
- 创建：`core/semantic/adapters.py`
- 创建：`core/semantic/chunkers.py`
- 创建：`core/semantic/indexer.py`
- 测试：`tests/test_semantic_adapters.py`
- 报告：`docs/superpowers/test-reports/rag/02-source-adapter-chunker-indexer.md`

- [ ] 先写失败测试，覆盖 MemoryDigest、RollingSessionSummary、GroupMemory、Sticker、ai_daily、source_hash canonical JSON 和 index_version。
- [ ] 实现最小 adapter/chunker/indexer。
- [ ] 运行阶段测试和 `git diff --check`，生成报告。

### 任务 3：索引 Worker

**文件：**
- 创建：`core/semantic/jobs.py`
- 创建：`workers/semantic_index_worker.py`
- 测试：`tests/test_semantic_index_worker.py`
- 报告：`docs/superpowers/test-reports/rag/03-semantic-index-worker.md`

- [ ] 先写失败测试，覆盖原子 claim、retry/recover、done_with_warning、deleted source、item+FTS 同事务。
- [ ] 实现 worker 与 job 状态机。
- [ ] 运行阶段测试和报告。

### 任务 4：Memory RAG

**文件：**
- 修改：`nanobot_kt/tools/memory_query.py`
- 修改：`creatures/nanobot/prompts/skills/memory_query/tool.py`
- 测试：`tests/test_memory_query_rag.py`
- 报告：`docs/superpowers/test-reports/rag/04-memory-rag.md`

- [ ] 先写失败测试，覆盖 hybrid recall、reranker、raw ChatLog 禁止返回、fallback summary、digest card 聚合。
- [ ] 实现 `memory_query` 的 digest/session_summary/all source。
- [ ] 同步工具 usage 和 Prompt 边界。
- [ ] 运行阶段测试和报告。

### 任务 5：GroupMemory RAG

**文件：**
- 修改：`app/group_memory/retrieval_service.py`
- 修改：`app/group_memory/injection_service.py`
- 测试：`tests/test_group_memory_rag.py`
- 报告：`docs/superpowers/test-reports/rag/05-group-memory-rag.md`

- [ ] 先写失败测试，覆盖不再 top100 截断、旧但相关记忆、manual/disabled 过滤、reranker gate、preview 不记录 injection、timeout fallback。
- [ ] 实现缓存、短 timeout 和 debug 字段。
- [ ] 运行阶段测试和报告。

### 任务 6：Sticker RAG

**文件：**
- 修改：`core/sticker_memory.py`
- 修改：`nanobot_kt/tools/sticker_search.py`
- 测试：`tests/test_sticker_rag.py`
- 报告：`docs/superpowers/test-reports/rag/06-sticker-rag.md`

- [ ] 先写失败测试，覆盖文本标签召回、reply_token、duplicate/inactive/undescribed/unreplyable 过滤、reranker 在 usage boost 前执行。
- [ ] 实现 Sticker text RAG。
- [ ] 同步 `sticker_search` usage。
- [ ] 运行阶段测试和报告。

### 任务 7：Knowledge Library

**文件：**
- 创建：`core/knowledge_library.py`
- 创建：`nanobot_kt/tools/knowledge_query.py`
- 创建：`prompts.v2.default/tools/knowledge_query/usage.md`
- 测试：`tests/test_knowledge_rag.py`
- 报告：`docs/superpowers/test-reports/rag/07-knowledge-library.md`

- [ ] 先写失败测试，覆盖 reranker、citation、trust/date filter、chunk expand、无 citation 丢弃、disabled 文档仅 Admin debug。
- [ ] 实现 knowledge tables、chunkers、query 和 Admin 审计。
- [ ] 同步工具注册、ToolPlan 和 usage。
- [ ] 运行阶段测试和报告。

### 任务 8：ai_daily 入库

**文件：**
- 修改：`creatures/nanobot/prompts/skills/news_search/news_daily/`
- 测试：`tests/test_ai_daily_knowledge_ingest.py`
- 报告：`docs/superpowers/test-reports/rag/08-ai-daily-ingest.md`

- [ ] 先写失败测试，覆盖摘要元数据入库、失败不阻断工具、URL/title/summary 去重、warning meta。
- [ ] 实现 best-effort ingest。
- [ ] 运行阶段测试和报告。

### 任务 9：group_analysis 局部 RAG

**文件：**
- 修改：`creatures/nanobot/prompts/skills/group_analysis/`
- 测试：`tests/test_group_analysis_local_rag.py`
- 报告：`docs/superpowers/test-reports/rag/09-group-analysis-local-rag.md`

- [ ] 先写失败测试，覆盖主题化触发、普通日报不启用、bundle rerank、临时索引、neighbor expansion、统计不污染、embedding 限于 lexical top candidates。
- [ ] 实现局部 RAG pipeline。
- [ ] 运行阶段测试和报告。

### 任务 10：Web / Admin 整合

**文件：**
- 修改：`api/admin/rag_routes.py`
- 修改：`webui/src/features/rag/`
- 测试：`tests/test_rag_admin_web.py`
- 报告：`docs/superpowers/test-reports/rag/10-admin-web-debug.md`

- [ ] 先写失败测试，覆盖 score_breakdown、reranker columns、citation columns、stats/prompt logs、debug run reopen、脱敏。
- [ ] 完成统一导航、导出 JSON、跨 source debug 和脱敏策略。
- [ ] 运行全量 `python -m pytest tests/ -v` 与生产验收命令。
