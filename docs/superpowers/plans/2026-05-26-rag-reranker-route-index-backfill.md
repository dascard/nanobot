# RAG reranker 路由与索引回填实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 reranker 进入本地模型组件目录，并让 RAG Debug 能从已有数据库文本回填构建语义索引。

**架构：** `rag_reranker` 作为本地组件展示在模型页；`core.semantic.provider_factory` 优先加载本地 `LocalCrossEncoderRerankerProvider`，旧 HTTP 环境变量只作为显式兼容 fallback。新增 `core.semantic.backfill` 负责从业务表生成 chunks 并 upsert，Admin RAG Debug 暴露状态和构建接口，Web 只调用这两个接口。

**技术栈：** FastAPI、SQLAlchemy、SQLite FTS5、pytest、React。

---

### 任务 1：本地 reranker 组件红灯测试

**文件：**
- 修改：`tests/test_semantic_provider_factory.py`
- 修改：`tests/test_admin_api.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_provider_factory_builds_local_reranker_from_model_path(monkeypatch):
    values = {
        "rag.reranker.model_path": "./models/bge-reranker-v2-m3",
        "rag.reranker.score_mode": "identity",
        "rag.reranker.max_text_chars": 256,
    }
    monkeypatch.setattr("core.settings_service.settings.get", lambda key, default=None: values.get(key, default))
    provider = provider_factory.get_reranker_provider()
    assert provider.model_name.endswith("models/bge-reranker-v2-m3")
    assert provider.download_repo_id == "BAAI/bge-reranker-v2-m3"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_semantic_provider_factory.py::test_provider_factory_builds_local_reranker_from_model_path -q`

- [ ] **步骤 3：实现 route 元数据、配置和 provider factory**

修改 `core/config_registry.py`、`core/semantic/provider_factory.py`、`api/admin_routes.py`。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_semantic_provider_factory.py tests/test_model_router.py -q`

### 任务 2：索引回填红灯测试

**文件：**
- 创建：`tests/test_semantic_backfill.py`
- 创建：`core/semantic/backfill.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_build_index_from_existing_memory_digest(db_session):
    digest = MemoryDigest(...)
    db_session.add(digest)
    db_session.commit()
    preview = preview_semantic_index_backfill(db_session, source_type="memory")
    assert preview["needs_build"] is True
    result = build_semantic_index_from_existing_data(db_session, source_type="memory")
    assert result["indexed_chunks"] == 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_semantic_backfill.py -q`

- [ ] **步骤 3：实现回填模块**

实现 source 映射、preview 统计、直接 upsert。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_semantic_backfill.py tests/test_semantic_index_worker.py -q`

### 任务 3：Admin RAG Debug 接口

**文件：**
- 修改：`api/admin/rag_routes.py`
- 修改：`tests/test_rag_debug.py`

- [ ] **步骤 1：编写失败测试**

新增测试覆盖 `/rag/debug/status` 和 `/rag/debug/build-index`。

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_rag_debug.py::test_rag_debug_status_reports_empty_index_and_reranker_route -q`

- [ ] **步骤 3：实现接口**

接口调用 `preview_semantic_index_backfill()`、`build_semantic_index_from_existing_data()`、`describe_reranker_provider_config()`。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_rag_debug.py -q`

### 任务 4：Web Debug 页面

**文件：**
- 修改：`webui/src/features/rag/RagDebugPage.jsx`

- [ ] **步骤 1：实现状态栏和构建按钮**

加载 `/rag/debug/status`，展示 reranker route、索引数量、可构建数量；构建按钮调用 `/rag/debug/build-index`。

- [ ] **步骤 2：构建检查**

运行：`npm --prefix webui run build`

### 任务 5：回归验证

- [ ] 运行：`python -m pytest tests/test_semantic_provider_factory.py tests/test_model_router.py tests/test_semantic_backfill.py tests/test_rag_debug.py -q`
- [ ] 运行：`python -m pytest tests/ -q`
- [ ] 不执行 git commit，等待用户明确说“提交”。
