# H30 RAG query 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不改变 RAG 查询外部契约的前提下，拆分 `KnowledgeRagService.query()` 和 `MemoryRagService.query()` 的巨型流程，降低下一阶段流式分段、debug trace 和 benchmark 维护成本。

**架构：** 第一阶段先补 query contract characterization tests，锁住 `stats`、`debug_trace`、`score_breakdown`、degraded 和 recall 越界行为。第二阶段分别在 `core/knowledge_rag.py` 与 `core/memory_rag.py` 内抽模块私有 dataclass 和 helper；第一轮不抽跨模块基类。第三阶段同步 `docs/todo.md`、`docs/plan_walkthrough.md` 和本计划状态。

**技术栈：** Python 3.12、SQLAlchemy ORM、in-memory SQLite、semantic index、RAG reranker provider、pytest。

---

## 当前状态

- [x] 已完成 H30 只读审计，覆盖 `docs/todo.md`、`core/knowledge_rag.py`、`core/memory_rag.py` 和相关测试。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-h30-rag-query-refactor-design.md`。
- [x] 设计阶段已提交：`417f09b docs(检索): 设计 RAG 查询拆分`。
- [x] 设计阶段验证已运行：红旗词扫描无输出，`git diff --check` 无输出，`python -m pytest tests/ -v` 结果 `1469 passed, 6 skipped, 139 warnings in 114.20s`。
- [x] 任务 1：补 query contract characterization tests。提交 `c319b4f test(检索): 锁定 RAG 查询契约`；验证为目标用例 `10 passed`、RAG 相邻回归 `67 passed`、全量 `1477 passed, 6 skipped`。
- [x] 任务 2：拆分 `KnowledgeRagService.query()`。提交 `ba512f6 refactor(检索): 拆分知识查询流程`；验证为 knowledge 定向 `17 passed`、RAG 相邻回归 `67 passed`、全量 `1477 passed, 6 skipped`。
- [x] 任务 3：拆分 `MemoryRagService.query()`。提交 `5391274 refactor(检索): 拆分记忆查询流程`；验证为 memory 定向 `19 passed`、RAG 相邻回归 `67 passed`、全量 `1477 passed, 6 skipped`。
- [x] 任务 4：同步文档状态并收口。

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-21-h30-rag-query-refactor-design.md`。
- 待办来源：`docs/todo.md` 中 H30 条目。
- 关键实现范围：`core/knowledge_rag.py:122-459`、`core/memory_rag.py:126-349`。
- 关联路线：`docs/todo.md` 路线项 6，H30 是 SSE 真 token 流式重构的维护性依赖。

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `core/knowledge_rag.py` | 保留 `KnowledgeRagService.query()` public facade，新增 knowledge 私有 recall/debug/filter/rerank/gate/result helper |
| `core/memory_rag.py` | 保留 `MemoryRagService.query()` public facade，新增 memory 私有 recall/debug/filter/rerank/gate/result helper |
| `tests/test_knowledge_rag.py` | 新增 knowledge query contract、degraded、FTS 越界、无向量行不调用 embedding provider 测试 |
| `tests/test_memory_query_rag.py` | 新增 memory query contract、source all、reranker budget、skip reason、degraded 测试 |
| `tests/test_rag_debug.py` | 作为 Admin debug 跨层契约回归，不优先修改 |
| `tests/test_rag_benchmark.py` | 作为 benchmark adapter 跨层契约回归，不优先修改 |
| `docs/todo.md` | H30 完成后标记状态 |
| `docs/plan_walkthrough.md` | 记录 H30 阶段提交、验证命令和下一步 |
| `.Codex/plans/h30-rag-query-refactor.md` | 本计划唯一 source of truth |

## 接口不变约束

- `KnowledgeRagService.query()` signature 不变：`query`、`limit`、`min_trust_level`、`source_type`、`domain`、`date_start`、`date_end`、`published_after`、`published_before`、`include_debug`。
- `MemoryRagService.query()` signature 不变：`query`、`source`、`user_id`、`session_id`、`limit`、`include_debug`。
- 顶层 result 保持 `query`、`source`、`degraded`、`fallback_reason`、`stats`、`items`，debug 模式继续附加 `debug_trace`。
- recall 合并顺序保持 `fts -> vector -> recent`，FTS / vector 命中必须能越过 recent rows limit。
- 没有向量索引行时不调用 embedding provider。
- `limit <= 0` 继续通过 `max(1, int(limit))` 兜底。
- 不新增 `asyncio.run()`，不新增同步函数包 awaitable。
- 不修改 prompt runtime、RAG baseline、Admin WebUI 或 tool schema。

## 并行执行策略

任务 1 的测试补强可分派给两个子 agent，写入范围必须互斥：

| 角色 | 可修改文件 | 禁止修改 |
| --- | --- | --- |
| Agent A | `tests/test_knowledge_rag.py` | `core/knowledge_rag.py`、memory 测试、文档 |
| Agent B | `tests/test_memory_query_rag.py` | `core/memory_rag.py`、knowledge 测试、文档 |
| Agent C | `docs/todo.md`、`docs/plan_walkthrough.md`、本计划 | 生产代码和测试 |
| 主线程 | `core/knowledge_rag.py`、`core/memory_rag.py`、最终集成验证和提交 | 回滚无关脏项 |

生产代码拆分默认主线程串行执行，因为两个文件虽独立，但都依赖相同的 semantic retriever 语义。若需要并行，必须把 worker 写入范围限定为单一模块，并在主线程合并前运行定向测试。

子 agent 提示词模板：

```markdown
你只负责本任务列出的文件。不得修改未列入的文件，不得暂存或提交。
先写测试并运行指定命令，记录失败或通过的真实输出。
如果测试已覆盖当前行为，说明它是 characterization guard；如果失败，说明真实缺口。
返回：改动文件、测试命令、输出摘要、风险点、建议 commit message。
```

## 任务 1：补 query contract characterization tests

**文件：**
- 修改：`tests/test_knowledge_rag.py`
- 修改：`tests/test_memory_query_rag.py`

- [x] **步骤 1：为 knowledge debug contract 写测试**

在 `tests/test_knowledge_rag.py` 增加：

```python
def test_knowledge_query_debug_contract_keys(db_session):
    from core.knowledge_rag import KnowledgeRagService

    doc = _manual_doc(
        db_session,
        "debug-contract.md",
        "# RAG\nRAG debug contract citation。",
        title="Debug Contract",
        trust_level="high",
        published_at="2026-06-01",
    )
    _index_doc(db_session, doc)

    service = KnowledgeRagService(
        db_session,
        reranker_provider=IdentityRerankerProvider({f"knowledge:{doc.id}:chunk:0": 0.9}),
    )
    result = service.query("RAG debug contract", limit=1, include_debug=True)

    assert set(result) == {
        "query", "source", "degraded", "fallback_reason", "stats", "items", "debug_trace",
    }
    assert set(result["stats"]) == {
        "fts_candidates", "vector_candidates", "embedding_candidates", "merged_candidates",
        "reranker_candidates", "final_items", "skipped_no_citation", "skipped_filter",
    }
    assert set(result["debug_trace"]) >= {
        "sql_filters", "fts_hits", "vector_hits", "embedding_hits", "merged_candidates",
        "reranker_input_pairs", "final_candidates", "relevance_gate", "skipped",
    }
    item = result["items"][0]
    assert set(item) == {
        "candidate_id", "document_id", "chunk_id", "title", "text", "citation",
        "trust_level", "score", "score_breakdown",
    }
    assert set(item["score_breakdown"]) == {
        "lexical", "semantic", "reranker", "raw_reranker", "trust", "recency", "final",
    }
    assert result["debug_trace"]["reranker_input_pairs"][0]["metadata"]["document_id"] == str(doc.id)
```

- [x] **步骤 2：为 knowledge degraded contract 写测试**

在 `tests/test_knowledge_rag.py` 增加：

```python
def test_knowledge_query_degraded_contract_without_reranker(db_session):
    from core.knowledge_rag import KnowledgeRagService

    doc = _manual_doc(
        db_session,
        "degraded.md",
        "# RAG\nRAG degraded contract。",
        title="Degraded",
        trust_level="medium",
    )
    _index_doc(db_session, doc)

    result = KnowledgeRagService(db_session).query("RAG degraded", limit=1, include_debug=True)

    assert result["degraded"] is True
    assert result["fallback_reason"] == "reranker_unavailable"
    assert result["stats"]["reranker_candidates"] == 0
    assert result["debug_trace"]["reranker_input_pairs"] == []
```

- [x] **步骤 3：为 knowledge FTS 越界召回写测试**

在 `tests/test_knowledge_rag.py` 增加：

```python
def test_knowledge_rag_uses_fts_recall_before_recent_row_limit(db_session):
    from core.knowledge_rag import KnowledgeRagService
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks

    chunks = [
        SemanticChunk(
            source_type="knowledge",
            source_id="old-fts-doc",
            source_sub_id="chunk:old-fts",
            title="旧知识",
            text="KohakuVQ RAG 召回排查。",
            lexical_text="KohakuVQ RAG 召回排查。",
            embedding_text="KohakuVQ RAG 召回排查。",
            metadata={"citation": {"url": "https://example.com/old-fts", "title": "旧知识", "trust_level": "medium"}},
        )
    ]
    chunks.extend(
        SemanticChunk(
            source_type="knowledge",
            source_id=f"noise-fts-{index}",
            source_sub_id=f"chunk:noise-fts-{index}",
            title=f"噪声知识 {index}",
            text="午饭咖啡闲聊。",
            lexical_text="午饭咖啡闲聊。",
            embedding_text="午饭咖啡闲聊。",
            metadata={"citation": {"url": f"https://example.com/noise-fts-{index}", "title": f"噪声知识 {index}", "trust_level": "medium"}},
        )
        for index in range(605)
    )
    upsert_semantic_chunks(db_session, chunks, index_version="fake:v1:knowledge")

    result = KnowledgeRagService(db_session).query("KohakuVQ", limit=3, include_debug=True)

    assert result["items"][0]["document_id"] == "old-fts-doc"
    assert result["stats"]["fts_candidates"] >= 1
    assert result["debug_trace"]["fts_hits"][0]["candidate_id"] == "knowledge:old-fts-doc:chunk:old-fts"
```

- [x] **步骤 4：为 knowledge 无向量行不调用 embedding provider 写测试**

在 `tests/test_knowledge_rag.py` 增加：

```python
def test_knowledge_rag_does_not_embed_when_index_has_no_vectors(db_session):
    from core.knowledge_rag import KnowledgeRagService

    class CountingEmbeddingProvider:
        def __init__(self):
            self.text_batches = []

        def embed(self, texts):
            self.text_batches.append(list(texts))
            return [[1.0, 0.0, 0.0] for _ in texts]

    doc = _manual_doc(
        db_session,
        "no-vector.md",
        "# RAG\nRAG 无向量行。",
        title="No Vector",
    )
    _index_doc(db_session, doc)

    provider = CountingEmbeddingProvider()
    result = KnowledgeRagService(db_session, embedding_provider=provider).query("RAG", limit=1)

    assert provider.text_batches == []
    assert result["stats"]["embedding_candidates"] == 0
```

- [x] **步骤 5：扩展 knowledge 无 citation debug 断言**

修改 `test_knowledge_result_without_citation_is_dropped`，将查询改为 debug 模式并增加断言：

```python
    result = KnowledgeRagService(db_session).query("RAG", limit=5, include_debug=True)

    assert result["items"] == []
    assert result["stats"]["skipped_no_citation"] == 1
    assert result["debug_trace"]["skipped"]["no_citation"] == 1
```

- [x] **步骤 6：为 memory debug contract 写测试**

在 `tests/test_memory_query_rag.py` 增加：

```python
def test_memory_query_debug_contract_keys(db_session):
    from core.memory_rag import MemoryRagService

    digest = _digest_row(601, cards=[
        {"title": "端口", "text": "uvicorn 8000 端口冲突。", "keywords": ["端口"]},
    ])
    _index_chunks(db_session, chunks_from_memory_digest(digest))

    result = MemoryRagService(
        db_session,
        reranker_provider=FixedRerankerProvider({"memory_digest:601:card:0": 0.9}),
    ).query("端口", source="digest", user_id="u1", session_id="s1", limit=1, include_debug=True)

    assert set(result) == {
        "query", "source", "degraded", "fallback_reason", "stats", "items", "debug_trace",
    }
    assert set(result["stats"]) == {
        "fts_candidates", "vector_candidates", "lexical_candidates", "embedding_candidates",
        "merged_candidates", "reranker_candidates", "reranker_latency_ms", "final_items",
    }
    assert set(result["debug_trace"]) >= {
        "sql_filters", "fts_hits", "vector_hits", "embedding_hits", "merged_candidates",
        "reranker_input_pairs", "final_candidates", "relevance_gate",
    }
    parent = result["items"][0]
    assert set(parent) >= {
        "source_type", "source", "source_id", "parent_score", "source_prior",
        "matched_cards", "score_breakdown", "digest_id", "digest_source_id",
        "matched_digest_row_ids",
    }
    card = parent["matched_cards"][0]
    assert set(card) == {
        "candidate_id", "source_type", "source_id", "source_sub_id", "title", "text",
        "lexical", "semantic", "reranker", "final_score", "score_breakdown",
    }
    assert set(card["score_breakdown"]) == {"lexical", "semantic", "reranker", "recency", "final"}
```

- [x] **步骤 7：为 memory source all 写真实服务测试**

在 `tests/test_memory_query_rag.py` 增加：

```python
def test_memory_query_source_all_returns_digest_and_session_summary(db_session):
    from core.memory_rag import MemoryRagService

    digest = _digest_row(602, cards=[
        {"title": "端口摘要", "text": "端口冲突摘要。", "keywords": ["端口"]},
    ])
    summary = RollingSessionSummary(
        id=603,
        session_id="s1",
        user_id="u1",
        status="active",
        summary_kind="llm",
        summary_text="端口冲突会导致部署失败。",
        summary_json=json.dumps({"summary": "端口冲突会导致部署失败。"}, ensure_ascii=False),
    )
    db_session.add_all([digest, summary])
    db_session.commit()
    _index_chunks(db_session, chunks_from_memory_digest(digest) + chunks_from_session_summary(summary))

    result = MemoryRagService(
        db_session,
        reranker_provider=FixedRerankerProvider({
            "memory_digest:602:card:0": 0.9,
            "session_summary:603:section:summary": 0.9,
        }),
    ).query("端口冲突", source="all", user_id="u1", session_id="s1", limit=5, include_debug=True)

    assert result["debug_trace"]["sql_filters"]["source_types"] == ["memory_digest", "session_summary"]
    assert {item["source_type"] for item in result["items"]} == {"memory_digest", "session_summary"}
```

- [x] **步骤 8：为 memory reranker budget 写测试**

在 `tests/test_memory_query_rag.py` 增加：

```python
def test_memory_rag_marks_reranker_budget_skipped_candidates(db_session):
    from core.memory_rag import MemoryRagService
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks

    chunks = [
        SemanticChunk(
            source_type="memory_digest",
            source_id=f"budget-{index}",
            source_sub_id="card:0",
            title=f"端口预算 {index}",
            text=f"KohakuVQ 端口预算 {index}",
            lexical_text=f"KohakuVQ 端口预算 {index}",
            embedding_text=f"KohakuVQ 端口预算 {index}",
            metadata={"user_id": "u1", "session_id": "s1"},
        )
        for index in range(55)
    ]
    upsert_semantic_chunks(db_session, chunks, index_version="fake:v1:v1")

    result = MemoryRagService(
        db_session,
        reranker_provider=FixedRerankerProvider({f"memory_digest:budget-{index}:card:0": 0.9 for index in range(55)}),
    ).query("KohakuVQ 端口预算", source="digest", user_id="u1", session_id="s1", limit=5, include_debug=True)

    skipped = [
        item for item in result["debug_trace"]["merged_candidates"]
        if item.get("skipped_reason") == "reranker_budget"
    ]
    assert result["stats"]["reranker_candidates"] == 50
    assert len(skipped) == 5
```

- [x] **步骤 9：扩展 memory weak fallback skip reason 断言**

修改 `test_memory_rag_skips_low_overlap_fallback_before_rerank`，增加：

```python
    by_id = {
        item["candidate_id"]: item
        for item in result["debug_trace"]["merged_candidates"]
    }
    assert by_id["memory_digest:weak:card:0"]["skipped_reason"] == "weak_lexical_fallback"
```

- [x] **步骤 10：为 memory degraded contract 写测试**

在 `tests/test_memory_query_rag.py` 增加：

```python
def test_memory_query_degraded_contract_without_reranker(db_session):
    from core.memory_rag import MemoryRagService

    digest = _digest_row(604, cards=[
        {"title": "端口", "text": "端口 degraded contract。", "keywords": ["端口"]},
    ])
    _index_chunks(db_session, chunks_from_memory_digest(digest))

    result = MemoryRagService(db_session).query(
        "端口 degraded",
        source="digest",
        user_id="u1",
        session_id="s1",
        limit=1,
        include_debug=True,
    )

    assert result["degraded"] is True
    assert result["fallback_reason"] == "reranker_unavailable"
    assert result["stats"]["reranker_candidates"] == 0
    assert result["debug_trace"]["reranker_input_pairs"] == []
```

- [x] **步骤 11：运行新增测试**

运行：

```bash
python -m pytest \
  tests/test_knowledge_rag.py::test_knowledge_query_debug_contract_keys \
  tests/test_knowledge_rag.py::test_knowledge_query_degraded_contract_without_reranker \
  tests/test_knowledge_rag.py::test_knowledge_rag_uses_fts_recall_before_recent_row_limit \
  tests/test_knowledge_rag.py::test_knowledge_rag_does_not_embed_when_index_has_no_vectors \
  tests/test_knowledge_rag.py::test_knowledge_result_without_citation_is_dropped \
  tests/test_memory_query_rag.py::test_memory_query_debug_contract_keys \
  tests/test_memory_query_rag.py::test_memory_query_source_all_returns_digest_and_session_summary \
  tests/test_memory_query_rag.py::test_memory_rag_marks_reranker_budget_skipped_candidates \
  tests/test_memory_query_rag.py::test_memory_rag_skips_low_overlap_fallback_before_rerank \
  tests/test_memory_query_rag.py::test_memory_query_degraded_contract_without_reranker \
  -v
```

预期：新增 characterization guard 可以通过当前实现；如果某个新增断言失败，先判断是否是当前 contract 真实缺口，修正测试或记录为任务内生产修复，不进入大拆分。

- [x] **步骤 12：运行 RAG query 定向回归**

运行：

```bash
python -m pytest tests/test_knowledge_rag.py tests/test_memory_query_rag.py tests/test_rag_debug.py tests/test_rag_benchmark.py -v
```

预期：全部通过。

- [x] **步骤 13：运行全量测试**

运行：

```bash
python -m pytest tests/ -v
```

预期：0 failures。

- [x] **步骤 14：Commit**

```bash
git add tests/test_knowledge_rag.py tests/test_memory_query_rag.py
git commit -m "test(检索): 锁定 RAG 查询契约"
```

## 任务 2：拆分 KnowledgeRagService.query()

**文件：**
- 修改：`core/knowledge_rag.py`
- 修改：`tests/test_knowledge_rag.py`（仅当任务 1 暴露必要调整）

- [x] **步骤 1：新增 `_KnowledgeRecallResult`**

在 `_KnowledgeCandidate` 后新增：

```python
@dataclass
class _KnowledgeRecallResult:
    rows: list[SemanticIndexItem]
    rows_by_id: dict[int, SemanticIndexItem]
    fts_hits: list[Any]
    vector_hits: list[Any]
    lexical_by_id: dict[int, float]
    bm25_by_id: dict[int, float]
    semantic_by_id: dict[int, float]
    query_vector: list[float] | None
```

如果类型检查不接受 `Any`，保留 `list[Any]`，不要为 retriever result 引入新公开类型。

- [x] **步骤 2：抽 `_recall()`**

在 `KnowledgeRagService` 内新增：

```python
    def _recall(self, query: str) -> _KnowledgeRecallResult:
        has_vector_rows = has_vector_recall_rows(
            self.db,
            source_types={"knowledge"},
            ensure_schema=not self.readonly,
        )
        query_vector = _query_vector(query, self.embedding_provider) if has_vector_rows else None
        fts_hits = fts_recall_hits(
            self.db,
            query,
            source_types={"knowledge"},
            limit=300,
            ensure_schema=not self.readonly,
        )
        lexical_by_id = {hit.item_id: hit.lexical_score for hit in fts_hits}
        bm25_by_id = {hit.item_id: hit.bm25_raw for hit in fts_hits}
        vector_hits = vector_recall_hits(
            self.db,
            query_vector=query_vector,
            source_types={"knowledge"},
            limit=300,
            ensure_schema=not self.readonly,
        )
        semantic_by_id = {hit.item_id: hit.semantic_score for hit in vector_hits}
        recent_rows = load_recall_rows(
            self.db,
            source_types={"knowledge"},
            limit=600,
            ensure_schema=not self.readonly,
        )
        rows_by_id = {int(row.id): row for row in recent_rows}
        recall_ids = [hit.item_id for hit in fts_hits] + [hit.item_id for hit in vector_hits]
        missing_ids = [item_id for item_id in recall_ids if item_id not in rows_by_id]
        rows_by_id.update(load_recall_rows_by_ids(
            self.db,
            missing_ids,
            ensure_schema=not self.readonly,
        ))
        fts_ordered = [rows_by_id[hit.item_id] for hit in fts_hits if hit.item_id in rows_by_id]
        vector_ordered = [rows_by_id[hit.item_id] for hit in vector_hits if hit.item_id in rows_by_id]
        rows = _merge_recall_rows(fts_ordered, vector_ordered, recent_rows)
        return _KnowledgeRecallResult(
            rows=rows,
            rows_by_id=rows_by_id,
            fts_hits=fts_hits,
            vector_hits=vector_hits,
            lexical_by_id=lexical_by_id,
            bm25_by_id=bm25_by_id,
            semantic_by_id=semantic_by_id,
            query_vector=query_vector,
        )
```

- [x] **步骤 3：抽 `_build_debug_trace()`**

新增：

```python
    def _build_debug_trace(
        self,
        *,
        recall: _KnowledgeRecallResult,
        min_trust_level: str,
        source_type: str,
        domain: str,
        published_after: str,
        published_before: str,
    ) -> dict[str, Any]:
        return {
            "sql_filters": {
                "source_types": ["knowledge"],
                "status": "active",
                "visibility": "recall",
                "min_trust_level": min_trust_level,
                "source_type": source_type,
                "domain": domain,
                "published_after": published_after,
                "published_before": published_before,
                "citation_required": True,
            },
            "fts_hits": [
                {
                    "item_id": hit.item_id,
                    "bm25_raw": hit.bm25_raw,
                    "lexical_score": hit.lexical_score,
                    "candidate_id": self._row_candidate_id(recall.rows_by_id.get(hit.item_id)),
                }
                for hit in recall.fts_hits
                if hit.item_id in recall.rows_by_id
            ],
            "vector_hits": [
                {
                    "item_id": hit.item_id,
                    "semantic_score": hit.semantic_score,
                    "candidate_id": self._row_candidate_id(recall.rows_by_id.get(hit.item_id)),
                }
                for hit in recall.vector_hits
                if hit.item_id in recall.rows_by_id
            ],
            "embedding_hits": [],
            "merged_candidates": [],
            "reranker_input_pairs": [],
            "final_candidates": [],
            "relevance_gate": [],
            "skipped": {"no_citation": 0, "filter": 0},
        }
```

- [x] **步骤 4：抽 `_filter_candidates()`**

新增：

```python
    def _filter_candidates(
        self,
        query: str,
        *,
        recall: _KnowledgeRecallResult,
        documents: dict[int, KnowledgeDocument],
        min_trust_level: str,
        published_after: str,
        published_before: str,
        source_type: str,
        domain: str,
    ) -> tuple[list[_KnowledgeCandidate], dict[str, int]]:
        candidates: list[_KnowledgeCandidate] = []
        skipped_no_citation = 0
        skipped_filter = 0
        semantic_hits = 0
        for row in recall.rows:
            meta = _safe_json(row.meta_json)
            citation = meta.get("citation") if isinstance(meta.get("citation"), dict) else {}
            if not _has_valid_citation(citation):
                skipped_no_citation += 1
                continue
            document = documents.get(_safe_int(row.source_id) or -1)
            if document is not None and str(document.status or "active") != "active":
                skipped_filter += 1
                continue
            if not self._passes_filters(
                row,
                citation,
                document,
                min_trust_level,
                published_after,
                published_before,
                source_type,
                domain,
            ):
                skipped_filter += 1
                continue
            lexical = recall.lexical_by_id.get(int(row.id))
            if lexical is None:
                lexical = lexical_overlap_score(query, row.lexical_text or row.text or "")
            semantic = recall.semantic_by_id.get(int(row.id))
            if semantic is None:
                semantic = semantic_score_for_row(row, query_vector=recall.query_vector, embedding_provider=None)
            if semantic is not None and semantic > 0:
                semantic_hits += 1
            if lexical > 0 or (semantic is not None and semantic >= 0.10):
                candidates.append(_KnowledgeCandidate(
                    row=row,
                    citation=citation,
                    document=document,
                    lexical=lexical,
                    semantic=semantic,
                ))
        candidates.sort(key=self._pre_score, reverse=True)
        return candidates[:100], {
            "skipped_no_citation": skipped_no_citation,
            "skipped_filter": skipped_filter,
            "semantic_hits": semantic_hits,
        }
```

- [x] **步骤 5：抽 debug update helpers**

新增：

```python
    def _update_candidate_debug(
        self,
        debug_trace: dict[str, Any] | None,
        *,
        candidates: list[_KnowledgeCandidate],
        bm25_by_id: dict[int, float],
        skipped: dict[str, int],
    ) -> None:
        if debug_trace is None:
            return
        debug_trace["embedding_hits"] = [
            self._debug_candidate(item, bm25_by_id=bm25_by_id)
            for item in candidates
            if item.semantic is not None and item.semantic > 0
        ]
        debug_trace["merged_candidates"] = [
            self._debug_candidate(item, bm25_by_id=bm25_by_id)
            for item in candidates
        ]
        debug_trace["skipped"] = {
            "no_citation": skipped["skipped_no_citation"],
            "filter": skipped["skipped_filter"],
        }
```

- [x] **步骤 6：抽 `_rerank()` 和 `_apply_relevance_gate()`**

保留 `_apply_reranker()` 的现有逻辑，新增 query 阶段包装：

```python
    def _rerank(
        self,
        query: str,
        candidates: list[_KnowledgeCandidate],
        debug_trace: dict[str, Any] | None,
    ) -> None:
        rerank_inputs = self._apply_reranker(query, candidates)
        if debug_trace is not None:
            debug_trace["reranker_input_pairs"] = [
                {
                    "candidate_id": candidate.candidate_id,
                    "source_type": candidate.source_type,
                    "query": query,
                    "title": candidate.title,
                    "text": candidate.text,
                    "metadata": candidate.metadata,
                }
                for candidate in rerank_inputs
            ]

    def _apply_relevance_gate(
        self,
        candidates: list[_KnowledgeCandidate],
        *,
        degraded: bool,
        debug_trace: dict[str, Any] | None,
    ) -> list[_KnowledgeCandidate]:
        gated: list[_KnowledgeCandidate] = []
        gate_debug: list[dict[str, Any]] = []
        for item in candidates:
            passed = passes_relevance_gate(
                {"reranker": item.reranker, "semantic": item.semantic, "lexical": item.lexical},
                degraded=degraded,
                min_reranker=self.min_reranker,
            )
            if passed:
                gated.append(item)
            if debug_trace is not None:
                gate_debug.append({
                    "candidate_id": item.candidate_id,
                    "passed": passed,
                    "degraded": degraded,
                    "components": {
                        "reranker": item.reranker,
                        "semantic": item.semantic,
                        "lexical": item.lexical,
                    },
                })
        if debug_trace is not None:
            debug_trace["relevance_gate"] = gate_debug
        return gated
```

- [x] **步骤 7：抽 `_build_result()`**

新增：

```python
    def _build_result(
        self,
        query: str,
        *,
        ranked: list[_KnowledgeCandidate],
        candidates: list[_KnowledgeCandidate],
        recall: _KnowledgeRecallResult,
        skipped: dict[str, int],
        limit: int,
        degraded: bool,
        debug_trace: dict[str, Any] | None,
    ) -> dict[str, Any]:
        capped_limit = max(1, int(limit))
        items = [self._result_item(item) for item in ranked[:capped_limit]]
        if debug_trace is not None:
            debug_trace["merged_candidates"] = [
                self._debug_candidate(item, bm25_by_id=recall.bm25_by_id)
                for item in candidates
            ]
            debug_trace["final_candidates"] = [
                self._debug_candidate(item, bm25_by_id=recall.bm25_by_id)
                for item in ranked[:capped_limit]
            ]
        result = {
            "query": query,
            "source": "knowledge",
            "degraded": degraded,
            "fallback_reason": "reranker_unavailable" if degraded else "",
            "stats": {
                "fts_candidates": len(recall.fts_hits),
                "vector_candidates": len(recall.vector_hits),
                "embedding_candidates": skipped["semantic_hits"],
                "merged_candidates": len(candidates),
                "reranker_candidates": len(candidates[:100]) if self.reranker_provider else 0,
                "final_items": len(items),
                "skipped_no_citation": skipped["skipped_no_citation"],
                "skipped_filter": skipped["skipped_filter"],
            },
            "items": items,
        }
        if debug_trace is not None:
            result["debug_trace"] = debug_trace
        return result
```

- [x] **步骤 8：重写 `query()` 为阶段串联**

将 `KnowledgeRagService.query()` 主体替换为：

```python
        published_after = str(published_after or date_start or "")
        published_before = str(published_before or date_end or "")
        recall = self._recall(query)
        documents = self._load_documents(recall.rows)
        debug_trace = (
            self._build_debug_trace(
                recall=recall,
                min_trust_level=min_trust_level,
                source_type=source_type,
                domain=domain,
                published_after=published_after,
                published_before=published_before,
            )
            if include_debug
            else None
        )
        candidates, skipped = self._filter_candidates(
            query,
            recall=recall,
            documents=documents,
            min_trust_level=min_trust_level,
            published_after=published_after,
            published_before=published_before,
            source_type=source_type,
            domain=domain,
        )
        self._update_candidate_debug(
            debug_trace,
            candidates=candidates,
            bm25_by_id=recall.bm25_by_id,
            skipped=skipped,
        )
        self._rerank(query, candidates, debug_trace)
        degraded = self.reranker_provider is None
        gated = self._apply_relevance_gate(candidates, degraded=degraded, debug_trace=debug_trace)
        ranked = sorted(gated, key=self._final_score, reverse=True)
        return self._build_result(
            query,
            ranked=ranked,
            candidates=candidates,
            recall=recall,
            skipped=skipped,
            limit=limit,
            degraded=degraded,
            debug_trace=debug_trace,
        )
```

- [x] **步骤 9：运行 knowledge 定向测试**

运行：

```bash
python -m pytest tests/test_knowledge_rag.py tests/test_rag_debug.py::test_rag_debug_query_runs_knowledge_search_with_citation -v
```

预期：全部通过。

- [x] **步骤 10：运行 RAG 相邻回归**

运行：

```bash
python -m pytest tests/test_knowledge_rag.py tests/test_memory_query_rag.py tests/test_rag_debug.py tests/test_rag_benchmark.py -v
```

预期：全部通过。

- [x] **步骤 11：运行全量测试**

运行：

```bash
python -m pytest tests/ -v
```

预期：0 failures。

- [x] **步骤 12：Commit**

```bash
git add core/knowledge_rag.py tests/test_knowledge_rag.py
git commit -m "refactor(检索): 拆分知识查询流程"
```

## 任务 3：拆分 MemoryRagService.query()

**文件：**
- 修改：`core/memory_rag.py`
- 修改：`tests/test_memory_query_rag.py`（仅当任务 1 暴露必要调整）

- [x] **步骤 1：新增 `_MemoryRecallResult`**

在 `_Candidate` 后新增：

```python
@dataclass
class _MemoryRecallResult:
    rows: list[SemanticIndexItem]
    rows_by_id: dict[int, SemanticIndexItem]
    fts_hits: list[Any]
    vector_hits: list[Any]
    lexical_by_id: dict[int, float]
    bm25_by_id: dict[int, float]
    semantic_by_id: dict[int, float]
    query_vector: list[float] | None
```

- [x] **步骤 2：抽 `_recall()`**

在 `MemoryRagService` 内新增：

```python
    def _recall(
        self,
        query: str,
        *,
        source_types: set[str],
        user_id: str,
        session_id: str,
    ) -> _MemoryRecallResult:
        has_vector_rows = has_vector_recall_rows(
            self.db,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            ensure_schema=not self.readonly,
        )
        query_vector = _query_vector(query, self.embedding_provider) if has_vector_rows else None
        fts_hits = fts_recall_hits(
            self.db,
            query,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            limit=200,
            ensure_schema=not self.readonly,
        )
        lexical_by_id = {hit.item_id: hit.lexical_score for hit in fts_hits}
        bm25_by_id = {hit.item_id: hit.bm25_raw for hit in fts_hits}
        vector_hits = vector_recall_hits(
            self.db,
            query_vector=query_vector,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            limit=200,
            ensure_schema=not self.readonly,
        )
        semantic_by_id = {hit.item_id: hit.semantic_score for hit in vector_hits}
        recent_rows = load_recall_rows(
            self.db,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            limit=400,
            ensure_schema=not self.readonly,
        )
        rows_by_id = {int(row.id): row for row in recent_rows}
        recall_ids = [hit.item_id for hit in fts_hits] + [hit.item_id for hit in vector_hits]
        missing_ids = [item_id for item_id in recall_ids if item_id not in rows_by_id]
        rows_by_id.update(load_recall_rows_by_ids(
            self.db,
            missing_ids,
            ensure_schema=not self.readonly,
        ))
        fts_ordered = [rows_by_id[hit.item_id] for hit in fts_hits if hit.item_id in rows_by_id]
        vector_ordered = [rows_by_id[hit.item_id] for hit in vector_hits if hit.item_id in rows_by_id]
        rows = _merge_recall_rows(fts_ordered, vector_ordered, recent_rows)
        return _MemoryRecallResult(
            rows=rows,
            rows_by_id=rows_by_id,
            fts_hits=fts_hits,
            vector_hits=vector_hits,
            lexical_by_id=lexical_by_id,
            bm25_by_id=bm25_by_id,
            semantic_by_id=semantic_by_id,
            query_vector=query_vector,
        )
```

- [x] **步骤 3：抽 `_build_debug_trace()`**

新增：

```python
    def _build_debug_trace(
        self,
        *,
        recall: _MemoryRecallResult,
        source_types: set[str],
        user_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        return {
            "sql_filters": {
                "source_types": sorted(source_types),
                "user_id": user_id,
                "session_id": session_id,
                "status": "active",
                "visibility": "recall",
            },
            "fts_hits": [
                {
                    "item_id": hit.item_id,
                    "bm25_raw": hit.bm25_raw,
                    "lexical_score": hit.lexical_score,
                    "candidate_id": self._row_candidate_id(recall.rows_by_id.get(hit.item_id)),
                }
                for hit in recall.fts_hits
                if hit.item_id in recall.rows_by_id
            ],
            "vector_hits": [
                {
                    "item_id": hit.item_id,
                    "semantic_score": hit.semantic_score,
                    "candidate_id": self._row_candidate_id(recall.rows_by_id.get(hit.item_id)),
                }
                for hit in recall.vector_hits
                if hit.item_id in recall.rows_by_id
            ],
            "embedding_hits": [],
            "merged_candidates": [],
            "reranker_input_pairs": [],
            "final_candidates": [],
            "relevance_gate": [],
        }
```

- [x] **步骤 4：抽 `_filter_candidates()`**

新增：

```python
    def _filter_candidates(
        self,
        query: str,
        *,
        recall: _MemoryRecallResult,
    ) -> tuple[list[_Candidate], dict[str, int]]:
        candidates: list[_Candidate] = []
        fts_candidate_count = 0
        semantic_hits = 0
        for row in recall.rows:
            lexical = recall.lexical_by_id.get(int(row.id))
            if lexical is None:
                lexical = lexical_overlap_score(query, row.lexical_text or row.text or "")
            if lexical > 0:
                fts_candidate_count += 1
            semantic = recall.semantic_by_id.get(int(row.id))
            if semantic is None:
                semantic = semantic_score_for_row(row, query_vector=recall.query_vector, embedding_provider=None)
            if semantic is not None and semantic > 0:
                semantic_hits += 1
            if lexical > 0 or (semantic is not None and semantic >= 0.10):
                candidates.append(_Candidate(row=row, lexical=lexical, semantic=semantic))
        candidates.sort(key=lambda item: self._pre_score(item), reverse=True)
        return candidates[:80], {
            "fts_candidate_count": fts_candidate_count,
            "semantic_hits": semantic_hits,
        }
```

- [x] **步骤 5：抽 debug update helper**

新增：

```python
    def _update_candidate_debug(
        self,
        debug_trace: dict[str, Any] | None,
        *,
        candidates: list[_Candidate],
        bm25_by_id: dict[int, float],
    ) -> None:
        if debug_trace is None:
            return
        debug_trace["embedding_hits"] = [
            self._debug_candidate(item, bm25_by_id=bm25_by_id)
            for item in candidates
            if item.semantic is not None and item.semantic > 0
        ]
        debug_trace["merged_candidates"] = [
            self._debug_candidate(item, bm25_by_id=bm25_by_id)
            for item in candidates
        ]
```

- [x] **步骤 6：抽 `_prepare_rerank_candidates()`**

新增：

```python
    def _prepare_rerank_candidates(
        self,
        candidates: list[_Candidate],
        *,
        bm25_by_id: dict[int, float],
    ) -> list[_Candidate]:
        rerank_candidates: list[_Candidate] = []
        if self.reranker_provider is None:
            return rerank_candidates
        for item in candidates:
            item.reranker_skip_reason = self._reranker_skip_reason(item, bm25_by_id=bm25_by_id)
            if not item.reranker_skip_reason:
                rerank_candidates.append(item)
        if len(rerank_candidates) > 50:
            for item in rerank_candidates[50:]:
                item.reranker_skip_reason = "reranker_budget"
            rerank_candidates = rerank_candidates[:50]
        return rerank_candidates
```

- [x] **步骤 7：抽 `_rerank()`**

新增：

```python
    def _rerank(
        self,
        query: str,
        *,
        candidates: list[_Candidate],
        rerank_candidates: list[_Candidate],
        debug_trace: dict[str, Any] | None,
    ) -> int:
        if self.reranker_provider is None or not rerank_candidates:
            return 0
        rerank_inputs = [
            SemanticCandidate(
                candidate_id=item.candidate_id,
                source_type=item.row.source_type,
                title=item.row.title or "",
                text=item.row.embedding_text or item.row.text or "",
                metadata=_safe_json(item.row.meta_json),
            )
            for item in rerank_candidates
        ]
        if debug_trace is not None:
            debug_trace["reranker_input_pairs"] = [
                {
                    "candidate_id": candidate.candidate_id,
                    "source_type": candidate.source_type,
                    "query": query,
                    "title": candidate.title,
                    "text": candidate.text,
                    "metadata": candidate.metadata,
                }
                for candidate in rerank_inputs
            ]
        reranker_started = time.perf_counter()
        reranked = self.reranker_provider.rerank(query, rerank_inputs, top_k=50)
        reranker_latency_ms = int((time.perf_counter() - reranker_started) * 1000)
        if debug_trace is not None:
            debug_trace.setdefault("timings", {})["reranker_latency_ms"] = reranker_latency_ms
        scores = {item.candidate_id: item for item in reranked}
        for item in candidates:
            score = scores.get(item.candidate_id)
            if score is not None:
                item.reranker = score.score
                item.raw_reranker = score.raw_score
        return reranker_latency_ms
```

- [x] **步骤 8：抽 `_apply_relevance_gate()` 与 `_build_result()`**

新增：

```python
    def _apply_relevance_gate(
        self,
        candidates: list[_Candidate],
        *,
        degraded: bool,
        debug_trace: dict[str, Any] | None,
    ) -> list[_Candidate]:
        gated: list[_Candidate] = []
        gate_debug: list[dict[str, Any]] = []
        for item in candidates:
            passed = passes_relevance_gate(
                {"reranker": item.reranker, "semantic": item.semantic, "lexical": item.lexical},
                degraded=degraded,
            )
            if passed:
                gated.append(item)
            if debug_trace is not None:
                gate_debug.append({
                    "candidate_id": item.candidate_id,
                    "passed": passed,
                    "degraded": degraded,
                    "components": {
                        "reranker": item.reranker,
                        "semantic": item.semantic,
                        "lexical": item.lexical,
                    },
                })
        if debug_trace is not None:
            debug_trace["relevance_gate"] = gate_debug
        return gated

    def _build_result(
        self,
        query: str,
        *,
        source: str,
        parent_items: list[dict[str, Any]],
        candidates: list[_Candidate],
        recall: _MemoryRecallResult,
        rerank_candidates: list[_Candidate],
        reranker_latency_ms: int,
        counters: dict[str, int],
        degraded: bool,
        debug_trace: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if debug_trace is not None:
            debug_trace["merged_candidates"] = [
                self._debug_candidate(item, bm25_by_id=recall.bm25_by_id)
                for item in candidates
            ]
            final_candidate_ids = {
                card["candidate_id"]
                for parent in parent_items
                for card in parent["matched_cards"]
            }
            debug_trace["final_candidates"] = [
                self._debug_candidate(item, bm25_by_id=recall.bm25_by_id)
                for item in candidates
                if item.candidate_id in final_candidate_ids
            ]
        result = {
            "query": query,
            "source": source,
            "degraded": degraded,
            "fallback_reason": "reranker_unavailable" if degraded else "",
            "stats": {
                "fts_candidates": len(recall.fts_hits),
                "vector_candidates": len(recall.vector_hits),
                "lexical_candidates": counters["fts_candidate_count"],
                "embedding_candidates": counters["semantic_hits"],
                "merged_candidates": len(candidates),
                "reranker_candidates": len(rerank_candidates) if self.reranker_provider else 0,
                "reranker_latency_ms": reranker_latency_ms,
                "final_items": len(parent_items),
            },
            "items": parent_items,
        }
        if debug_trace is not None:
            result["debug_trace"] = debug_trace
        return result
```

- [x] **步骤 9：重写 `query()` 为阶段串联**

将 `MemoryRagService.query()` 主体替换为：

```python
        source_types = _source_types(source)
        recall = self._recall(
            query,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
        )
        debug_trace = (
            self._build_debug_trace(
                recall=recall,
                source_types=source_types,
                user_id=user_id,
                session_id=session_id,
            )
            if include_debug
            else None
        )
        candidates, counters = self._filter_candidates(query, recall=recall)
        self._update_candidate_debug(debug_trace, candidates=candidates, bm25_by_id=recall.bm25_by_id)
        degraded = self.reranker_provider is None
        rerank_candidates = self._prepare_rerank_candidates(candidates, bm25_by_id=recall.bm25_by_id)
        reranker_latency_ms = self._rerank(
            query,
            candidates=candidates,
            rerank_candidates=rerank_candidates,
            debug_trace=debug_trace,
        )
        gated = self._apply_relevance_gate(candidates, degraded=degraded, debug_trace=debug_trace)
        parent_items = self._group_by_parent(gated, limit=limit)
        return self._build_result(
            query,
            source=source,
            parent_items=parent_items,
            candidates=candidates,
            recall=recall,
            rerank_candidates=rerank_candidates,
            reranker_latency_ms=reranker_latency_ms,
            counters=counters,
            degraded=degraded,
            debug_trace=debug_trace,
        )
```

- [x] **步骤 10：运行 memory 定向测试**

运行：

```bash
python -m pytest tests/test_memory_query_rag.py tests/test_rag_debug.py::test_rag_debug_memory_uses_real_pipeline_trace -v
```

预期：全部通过。

- [x] **步骤 11：运行 RAG 相邻回归**

运行：

```bash
python -m pytest tests/test_knowledge_rag.py tests/test_memory_query_rag.py tests/test_rag_debug.py tests/test_rag_benchmark.py -v
```

预期：全部通过。

- [x] **步骤 12：运行全量测试**

运行：

```bash
python -m pytest tests/ -v
```

预期：0 failures。

- [x] **步骤 13：Commit**

```bash
git add core/memory_rag.py tests/test_memory_query_rag.py
git commit -m "refactor(检索): 拆分记忆查询流程"
```

## 任务 4：同步 H30 文档状态

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/h30-rag-query-refactor.md`

- [x] **步骤 1：更新 `docs/todo.md`**

将 H30 条目从未完成改为已完成，并保留第一轮边界说明：

```markdown
- [x] **H30 RAG query() ~337 行** · `core/knowledge_rag.py:122-459` / `core/memory_rag.py:126-349` · HIGH(可维护) · L ·〔呼应路线图 §6〕
  已完成第一轮拆分：knowledge / memory 两个 `query()` 已按 recall、filter、rerank、gate、result 模块内私有边界拆分；public signature、result envelope、`stats`、`debug_trace`、degraded 语义和 RAG benchmark / Admin debug 消费契约保持不变。跨模块公共 recall helper 暂不抽取，保留为第二阶段评估项。
```

- [x] **步骤 2：更新 `docs/plan_walkthrough.md`**

在文件末尾追加 H30 章节，记录：

```markdown
## 2026-06-21 H30 RAG query 拆分计划

状态：H30 第一轮拆分已完成。`KnowledgeRagService.query()` 和 `MemoryRagService.query()` 的 public signature、result envelope、`stats`、`debug_trace`、degraded 语义、RAG benchmark adapter 和 Admin debug 消费契约保持不变；内部已按 recall、filter、rerank、gate 和 result 阶段拆出模块内私有边界。

已完成：

- [x] 分派只读 explorer 审计 H30 范围、测试覆盖和 public contract。
- [x] 写入设计文档：`docs/superpowers/specs/2026-06-21-h30-rag-query-refactor-design.md`，提交 `417f09b docs(检索): 设计 RAG 查询拆分`。
- [x] 写入实现计划：`.Codex/plans/h30-rag-query-refactor.md`。
- [x] 任务 1：补 query contract characterization tests。
- [x] 任务 2：拆分 `KnowledgeRagService.query()`。
- [x] 任务 3：拆分 `MemoryRagService.query()`。
- [x] 任务 4：同步 `docs/todo.md`、`docs/plan_walkthrough.md` 和计划状态。

H30 计划列表：

- [x] 阶段 0：只读审计、方案选择和设计文档。
- [x] 阶段 1：补 query contract characterization tests。
- [x] 阶段 2：拆分 knowledge RAG 查询流程。
- [x] 阶段 3：拆分 memory RAG 查询流程。
- [x] 阶段 4：文档状态收口。

执行约束：

- 不新增 `asyncio.run()`，不新增同步函数包 awaitable。
- 第一轮不抽跨模块公共 RAG base，避免 knowledge citation/document filter 与 memory parent grouping 过早绑定。
- 每个阶段完成后运行指定定向回归和 `python -m pytest tests/ -v`，再按文件显式暂存并提交。

下一步：

默认回到 `docs/todo.md` 的剩余 P3/P4 项。H30 公共 recall helper 可在两个模块稳定运行后单独评估；不作为第一轮拆分阻塞项。
```

- [x] **步骤 3：更新本计划当前状态**

将本计划顶部任务状态全部勾选，并补每个任务的提交号和验证摘要。

- [x] **步骤 4：运行文档扫描**

运行：

```bash
rg -n "T[O]DO|F[I]XME|待[定]|待[补]|占[位]|后续[实]现|类似[任]务" \
  docs/todo.md docs/plan_walkthrough.md .Codex/plans/h30-rag-query-refactor.md
```

预期：无输出。

- [x] **步骤 5：运行文档 diff 检查**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/h30-rag-query-refactor.md
```

预期：无输出。

- [x] **步骤 6：运行最终 RAG 回归**

运行：

```bash
python -m pytest tests/test_knowledge_rag.py tests/test_memory_query_rag.py tests/test_rag_debug.py tests/test_rag_benchmark.py -v
```

预期：全部通过。

- [x] **步骤 7：运行全量测试**

运行：

```bash
python -m pytest tests/ -v
```

预期：0 failures。

- [x] **步骤 8：Commit**

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/h30-rag-query-refactor.md
git commit -m "docs(计划): 收口 RAG 查询拆分状态"
```

## 总体验证矩阵

每个实现阶段必须至少运行：

```bash
python -m pytest tests/test_knowledge_rag.py tests/test_memory_query_rag.py tests/test_rag_debug.py tests/test_rag_benchmark.py -v
python -m pytest tests/ -v
```

最终收口前必须确认：

- `rg -n "asyncio\\.run\\(" core tests api clients nanobot_kt creatures` 没有新增非 main 使用。
- `git diff --check` 无输出。
- `python -m pytest tests/ -v` 0 failures。
- `git status --short` 中 staged 文件只包含当前阶段文件。
