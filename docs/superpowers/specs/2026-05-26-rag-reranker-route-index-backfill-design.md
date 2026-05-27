# RAG reranker 路由与索引回填设计

## 目标

把 RAG reranker 纳入本地模型组件目录，并在 RAG 索引为空时允许管理员从数据库已有文本直接构建 `semantic_index_items`。

## 背景

当前 RAG 服务已经通过 `get_reranker_provider()` 接入 reranker，但 provider 只从 `RAG_RERANKER_*` 环境变量解析。Web 模型页无法看到本地 reranker 组件，也无法测试当前 rerank 模型是否真的可用。RAG Debug 页能展示 trace，但当 `semantic_index_items` 为空时只会得到 0 candidates，不会提示已有业务数据可以构建索引。

## 方案

1. 新增本地组件 `rag_reranker`。
   - 在 `/models/status` 的 `local_components` 中展示。
   - 在 `config_registry` 中注册 `rag.reranker.model_path`、`hf_model`、`score_mode`、`max_text_chars`。
   - 在 `/models/local/rag_reranker/test` 和 `/models/local/rag_reranker/warmup` 中测试/预热。
   - `get_reranker_provider()` 优先使用本地 `LocalCrossEncoderRerankerProvider`；只有显式设置 `RAG_RERANKER_URL` 时才使用旧 HTTP fallback。

2. 本地模型加载方式。
   - 默认模型目录为 `./models/bge-reranker-v2-m3`。
   - 默认下载源为 `BAAI/bge-reranker-v2-m3`，首次加载时下载到上述目录。
   - 也可用 `RAG_LOCAL_RERANKER_MODEL` 或 `rag.reranker.model_path` 指向其他本地模型目录。
   - 自定义目录需要显式设置 `RAG_RERANKER_HF_MODEL` 或 `rag.reranker.hf_model` 才会自动下载。
   - 加载器为 `sentence-transformers CrossEncoder`，与本地 BGE embedding 组件保持一致。

3. 新增语义索引回填模块。
   - 复用现有 adapters，从 `MemoryDigest`、`RollingSessionSummary`、`GroupMemory`、`StickerMemory`、`KnowledgeChunk` 生成 `SemanticChunk`。
   - `memory` 映射到 `memory_digest + session_summary`。
   - `all` 映射到所有可索引源。
   - 默认直接 upsert，适合 Debug 页手动修复空索引；后台 worker 仍负责异步任务。

4. RAG Debug 增加状态与构建入口。
   - `GET /api/v1/admin/rag/debug/status?source_type=memory` 返回索引数量、可构建 chunk 数、reranker route 状态。
   - `POST /api/v1/admin/rag/debug/build-index` 从已有数据构建索引。
   - Web Debug 页展示 reranker 是否配置、当前 source 是否已有索引、可构建数量和构建按钮。

## 错误处理

- 本地 reranker 模型目录不存在时，provider factory 返回 `None`，生产严格模式仍由业务层按 `RAG_ALLOW_DEGRADED=0` 阻断。
- `RAG_RERANKER_URL` 仅作为显式 legacy HTTP fallback，不作为默认生产路径。
- 索引回填跳过不满足 adapter 条件的源记录，例如未描述成功的 sticker、禁用的 knowledge chunk。

## 测试

- provider factory 优先从本地模型路径构建 `LocalCrossEncoderRerankerProvider`。
- local component test 能用 reranker provider 发起一次真实重排调用。
- 索引回填预览能统计已有数据和空索引状态。
- 索引回填能从已有 memory/sticker/knowledge 数据写入 `semantic_index_items` 和 FTS。
- RAG Debug 状态接口返回 reranker 与索引状态。
