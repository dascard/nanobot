# Session 摘要质量修复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 降低摘要页面原始对话泄露感，并提供 Web 手动 LLM 摘要重生成能力。

**架构：** 后端保留现有 rolling summary 和 session summary job 架构，只调整兜底摘要渲染、LLM 提示词和 enqueue 行为。前端在现有 `SessionSummaryBrowser` 内增加操作区和 job 列表，不拆新页面。

**技术栈：** FastAPI、SQLAlchemy、pytest、React、Vite。

---

### 任务 1：摘要降噪测试

**文件：**
- 修改：`tests/test_session_memory.py`
- 修改：`app/session_memory/summarizer.py`

- [ ] **步骤 1：编写失败测试**

新增测试断言 deterministic fallback 不在展示正文里输出 `turn_id=`、`[user]`、`[assistant]`、时间戳标签，但保留 `evidence_turn_ids`。

- [ ] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_session_memory.py::test_deterministic_summary_uses_clean_snippets_without_turn_metadata -q`

- [ ] **步骤 3：实现摘要降噪**

增加干净 snippet formatter，并让 payload/render 使用 snippet 而非 raw turn line。

- [ ] **步骤 4：运行测试验证通过**

运行同上测试。

### 任务 2：手动 LLM 重生成后端

**文件：**
- 修改：`app/session_memory/jobs.py`
- 修改：`api/admin/session_memory_routes.py`
- 修改：`tests/test_session_memory.py`

- [ ] **步骤 1：编写失败测试**

新增 active LLM summary force enqueue 测试，确认 done job 不阻断新 job，pending/running 不重复。

- [ ] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_session_memory.py::test_admin_force_enqueue_llm_summary_from_active_llm_summary -q`

- [ ] **步骤 3：实现 force enqueue**

`enqueue_session_summary_job()` 增加 `force` 参数；admin enqueue route 增加 `force`、`summary_id`，支持从 active LLM 摘要重生成。

- [ ] **步骤 4：运行测试验证通过**

运行相关 session memory 测试。

### 任务 3：Web 操作入口

**文件：**
- 修改：`webui/src/App.jsx`
- 修改：`tests/test_webui_admin_redesign.py`

- [ ] **步骤 1：编写源码断言测试**

断言页面包含“重新生成 LLM 摘要”“重试失败摘要任务”“代码兜底”等关键文案和 API 调用。

- [ ] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_webui_admin_redesign.py::test_session_summary_browser_exposes_llm_regeneration_controls -q`

- [ ] **步骤 3：实现前端操作区**

在近期摘要模式加载 `/session-memory/{session_id}/rolling-summary` jobs，添加 enqueue/retry 按钮和状态提示。

- [ ] **步骤 4：运行测试和构建**

运行 Web 源码测试和 `npm run build -- --outDir /tmp/nanobot-webui-build`。
