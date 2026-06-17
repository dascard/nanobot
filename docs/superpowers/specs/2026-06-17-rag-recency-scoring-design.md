# RAG recency 评分设计

## 背景

`knowledge_rag`、`memory_rag` 和 `sticker_rag` 的最终评分都包含 `recency`
权重，但当前实现统一写死为 `0.5`。这会让新旧程度这个维度失效，也让三处
服务继续维护重复的最终评分拼装逻辑。

## 目标

- 提供一个共享的 recency 评分函数，输入一个或多个时间戳，输出 `0.0` 到
  `1.0` 之间的分数。
- 缺失时间戳时保持兼容，返回中性分 `0.5`。
- 让知识、记忆和表情包 RAG 的最终评分使用真实时间戳，而不是常量。
- 在 `score_breakdown` 中暴露 `recency`，方便调试面板解释排序结果。

## 非目标

- 不调整召回、reranker、语义相似度或 relevance gate。
- 不新增数据库字段或迁移。
- 不改变 RAG 权重常量，只替换现有 `recency` 组件的取值来源。

## 方案

在 `core.semantic.scoring` 中新增 `recency_score()`：

- 过滤空值和非法时间戳。
- 多个时间戳取最新值。
- 未来时间按最新处理，返回 `1.0`。
- 使用半衰期衰减：`score = floor + (1 - floor) * 0.5 ** (age_days / half_life_days)`。
- 默认半衰期为 90 天，默认下限为 `0.05`，默认缺失值为 `0.5`。

各 RAG 服务选择自己的业务时间戳：

- Knowledge：优先 `KnowledgeDocument.latest_seen`、`KnowledgeDocument.updated_at`，
  再回退到 `SemanticIndexItem.source_updated_at`、`updated_at`、`indexed_at`。
- Memory：使用 `SemanticIndexItem.source_updated_at`、`updated_at`、`indexed_at`。
- Sticker：优先 `StickerMemory.last_used`、`last_seen`、`created_at`，
  再回退到索引行时间。

服务层按上述顺序选择一个业务时间戳；索引行的 `updated_at` / `indexed_at`
只作为兜底，避免「刚重新索引的旧内容」被误判为新内容。

## 测试策略

- 为 `recency_score()` 写单元测试，覆盖缺失时间、最新时间、旧时间和未来时间。
- 为三类 RAG 服务补回归测试，断言同等 relevance 下较新的候选 recency 更高，
  且结果的 `score_breakdown` 包含真实 `recency`。
- 运行相关 RAG 测试后，再运行完整测试集。

## 风险与兼容性

recency 在三处最终权重中占比都较低（2% 到 5%），因此排序变化应只影响
相关性相近的候选。缺失时间戳继续返回 `0.5`，避免旧数据因为没有时间字段
被直接打到最低分。
