# Nanobot 三类 RAG 设计规格

## 目标

依据 `docs/goal.md` 的最终修订版，为 Nanobot 建设三类 RAG 能力：记忆 RAG、表情包 RAG、外挂知识库 RAG，并为 group_analysis 提供主题化临时 evidence retrieval。

## 核心原则

- FTS5 和 embedding 只负责召回，不能替代 reranker。
- 非 degraded 模式下，reranker 低分是硬否决，semantic/lexical 高分不能推翻。
- degraded mode 可运行，但不算生产验收通过。
- source quota 与 source_prior 分离，source 权重必须按当前启用来源归一化。
- 不全量索引原始 ChatLog，不把外挂知识库自动注入 Prompt。
- RAG 内容一律视为不可信资料，注入 Prompt 前必须 sanitize，并声明不是系统指令。
- 每个实施阶段必须有 Web 审查入口、测试报告和性能摘要。

## 架构

统一索引层使用 `semantic_index_items` 存主数据，`semantic_index_fts` 做 FTS5 召回，`semantic_index_jobs` 管理异步索引任务，`rag_debug_runs` 保存调试运行。业务源通过 SourceAdapter 产出 `SemanticChunk`，Indexer 计算 source_hash/index_version 后写入统一索引。

查询流程为：SQL filter -> FTS5 lexical recall -> embedding semantic recall -> merge/dedupe -> pre-score topN -> reranker -> relevance gate -> final weighted score -> score_breakdown。Reranker 支持 LocalCrossEncoder 和 HTTP provider，生产推荐独立 HTTP 服务。

## 业务边界

- `memory_query` 只返回 MemoryDigest 与 RollingSessionSummary，不返回原始 ChatLog。
- GroupMemory 运行时注入只在 `group_profile_mode=on/preview` 且有当前输入或 recent messages 时启用，必须有短 timeout 和缓存。
- `sticker_search` 只使用文本标签、描述、情绪和适用场景检索，不做图片 embedding，并过滤不可发送表情。
- `knowledge_query` 只返回带 citation 的结果；普通聊天工具只能查 active 且 trust 合规的文档。
- `ai_daily` 只入库摘要与来源元数据，不保存网页全文，不向量化 HTML。
- `group_analysis` 只在主题化指令启用临时 RAG，不长期写入 semantic index，不污染群统计。

## 验收

最终验收以 `docs/goal.md` 的“最终验收标准”为准。任何阶段完成声明都必须提供对应 pytest 输出、`git diff --check`、Web debug 证据和阶段测试报告。
