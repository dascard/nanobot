# 长期摘要 LLM 生成实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 test-driven-development 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将长期 `memory_digests` 从纯规则摘要升级为 LLM 主生成、规则兜底的结构化摘要链路。

**架构：** 新增 `app/memory_digest/llm_builder.py` 封装 LLM prompt、解析、审计和兜底；`core/daily_digest.py` 在每个 session/date 生成时调用该入口。现有 `MemoryDigestBuilder` 保留为清洗和兜底实现。

**技术栈：** Python、pytest、SQLAlchemy、现有 `NewAPIClient`、现有 `MemoryDigest` v2 renderer。

---

### 任务 1：LLM builder 成功路径测试

**文件：**
- 创建：`app/memory_digest/llm_builder.py`
- 修改：`tests/test_memory_digest.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_memory_digest.py` 添加测试，构造 fake summarizer 返回严格 JSON，断言结果为 active、`generator=llm`，level 2 包含 LLM recall card。

- [ ] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_memory_digest.py::test_llm_memory_digest_builder_promotes_clean_llm_summary -q`
预期：FAIL，模块或函数不存在。

- [ ] **步骤 3：实现最少代码**

创建 `build_llm_memory_digest(...)`，先调用 `MemoryDigestBuilder().build(...)` 获取 fallback，再调用注入的 summarizer，解析 JSON，合并 meta，调用 `render_digest_levels`。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -B -m pytest tests/test_memory_digest.py::test_llm_memory_digest_builder_promotes_clean_llm_summary -q`
预期：PASS。

### 任务 2：审计失败降级测试

**文件：**
- 修改：`app/memory_digest/llm_builder.py`
- 修改：`tests/test_memory_digest.py`

- [ ] **步骤 1：编写失败的测试**

新增测试，fake summarizer 返回含 URL 的 recall card，断言结果降级为 `generator=deterministic_fallback`，`llm_status=fallback`，level 2 不包含 URL。

- [ ] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_memory_digest.py::test_llm_memory_digest_builder_falls_back_when_audit_rejects_url_card -q`
预期：FAIL，审计尚未拒绝 URL。

- [ ] **步骤 3：实现审计**

增加 `_audit_llm_meta()`，检查 active 必需字段、quality、recall card、URL、列表字段类型。失败时返回带 `llm_error` 的 fallback。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -B -m pytest tests/test_memory_digest.py::test_llm_memory_digest_builder_falls_back_when_audit_rejects_url_card -q`
预期：PASS。

### 任务 3：daily digest 接入测试

**文件：**
- 修改：`core/daily_digest.py`
- 修改：`tests/test_memory_digest.py`

- [ ] **步骤 1：编写失败的测试**

新增测试，monkeypatch `daily_digest.build_memory_digest` 或 summarizer，断言 `generate_daily_digest_for_date()` 默认写入 LLM meta。

- [ ] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_memory_digest.py::test_generate_daily_digest_uses_llm_memory_digest_by_default -q`
预期：FAIL，daily digest 仍直接调用 `MemoryDigestBuilder`。

- [ ] **步骤 3：接入 daily digest**

在 `core/daily_digest.py` 增加 `_build_memory_digest_result()`，默认调用 LLM builder；环境变量关闭时使用规则 builder。测试中通过注入 summarizer 避免真实网络。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -B -m pytest tests/test_memory_digest.py::test_generate_daily_digest_uses_llm_memory_digest_by_default -q`
预期：PASS。

### 任务 4：回归验证

**文件：**
- 修改：相关测试文件按失败输出最小调整。

- [ ] **步骤 1：运行目标测试**

运行：`python -B -m pytest tests/test_memory_digest.py tests/test_memory_digest_builder_quality.py -q`
预期：全部通过。

- [ ] **步骤 2：运行相关 RAG 测试**

运行：`python -B -m pytest tests/test_semantic_adapters.py tests/test_memory_query_rag.py -q`
预期：全部通过。

- [ ] **步骤 3：运行全量测试**

运行：`python -m pytest tests/ -v`
预期：0 failures。
