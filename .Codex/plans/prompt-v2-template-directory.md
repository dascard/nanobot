# Prompt V2 模板目录化整理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Prompt V2 模板从扁平文件收口为 `chat/`、`tools/`、`tasks/` 三类目录，并让真实运行链路、Admin API、WebUI 和工具内部 LLM 调用共用同一模板索引。

**Architecture:** `template_registry` 负责 key 解析、路径生成、alias 和分类；`template_loader` 负责运行时覆盖优先读取和 frontmatter 继承；`template_store` 负责 WebUI CRUD 和 tree 响应。compiler、runtime tool prompt、tool schema preview 和内部工具 prompt 全部只依赖 canonical slash key。

**Tech Stack:** Python 3.12、FastAPI、pytest、React、Vite、ESLint。

---

### Task 1: 建立模板 Registry 和 Loader 兼容层

**Files:**
- Create: `core/prompt_v2/template_registry.py`
- Modify: `core/prompt_v2/template_loader.py`
- Test: `tests/test_prompt_v2_template_registry.py`

- [x] **Step 1: Write the failing test**

覆盖 slash key、旧 alias、运行时覆盖 frontmatter 继承、非法 key。

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
/home/dascard/anaconda3/bin/python -m pytest -s tests/test_prompt_v2_template_registry.py -v
```

Expected before implementation: `ModuleNotFoundError: core.prompt_v2.template_registry` 或非法 key 行为不符合预期。

- [x] **Step 3: Implement registry and loader support**

新增 canonical key 解析、路径 helper、默认/运行时目录读取、alias 兼容和 frontmatter merge。

- [x] **Step 4: Run test to verify it passes**

Run:

```bash
/home/dascard/anaconda3/bin/python -m pytest -s tests/test_prompt_v2_template_registry.py -v
```

Expected: 6 passed。

### Task 2: 迁移默认模板目录结构

**Files:**
- Modify: `prompts.v2.default/**`
- Modify: `core/prompt_v2/flow.py`
- Modify: `core/prompt_v2/context_adapters.py`
- Test: `tests/test_prompt_v2.py`

- [x] **Step 1: Move flat files into target directories**

把 `chat_main.md` 移到 `chat/main.md`，把工具模板移到 `tools/<tool>/...`，把任务模板移到 `tasks/...`。

- [x] **Step 2: Update flow keys**

`chat/flow.json` 中模板节点使用 `chat/main`、`chat/branch_group`、`chat/branch_private`、`chat/identity_context`。

- [x] **Step 3: Update compiler-adjacent code**

`default_flow_path()`、`runtime_flow_path()` 和 identity context loader 使用新目录。

- [x] **Step 4: Run compiler tests**

Run:

```bash
/home/dascard/anaconda3/bin/python -m pytest -s tests/test_prompt_v2.py -v
```

Expected: V2 编译、分支隔离、重复注入约束全部通过。

### Task 3: 补齐 Admin CRUD 和 tree 响应

**Files:**
- Modify: `core/prompt_v2/template_store.py`
- Modify: `api/admin_routes.py`
- Test: `tests/test_prompt_v2_template_admin.py`

- [x] **Step 1: Write admin tests**

覆盖 list tree、nested path get/put、create、delete runtime override、reset。

- [x] **Step 2: Implement store and routes**

新增 `{template_key:path}` 路由和只写 `data/prompts_v2` 的 CRUD。

- [x] **Step 3: Run admin tests**

Run:

```bash
/home/dascard/anaconda3/bin/python -m pytest -s tests/test_prompt_v2_template_admin.py -v
```

Expected: Admin CRUD 和默认工具覆盖测试通过。

### Task 4: 接入工具模板到真实运行链路

**Files:**
- Modify: `core/prompt_v2/tool_templates.py`
- Modify: `core/runtime_tool_service.py`
- Modify: `core/tool_schema_preview.py`
- Modify: `core/final_tools.py`
- Modify: `creatures/nanobot/prompts/skills/group_analysis/analyzer.py`
- Modify: `creatures/nanobot/prompts/skills/news_search/prompts.py`
- Modify: `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/summarize_quality.py`
- Modify: `creatures/nanobot/prompts/skills/image_summary/tool.py`
- Test: `tests/test_prompt_v2_tool_template_integration.py`

- [x] **Step 1: Write integration tests**

覆盖 schema description、runtime tool prompt、group_analysis、news_search、ai_daily、image_summary。

- [x] **Step 2: Replace flat keys with slash keys**

运行时工具说明优先读取 `tools/<tool>/usage`，内部 LLM prompt 使用各自子模板。

- [x] **Step 3: Run integration tests**

Run:

```bash
/home/dascard/anaconda3/bin/python -m pytest -s tests/test_prompt_v2_tool_template_integration.py -v
```

Expected: 工具模板集成全部通过。

### Task 5: 更新 WebUI 资源树和旧入口标识

**Files:**
- Modify: `webui/src/App.jsx`
- Modify: `tests/test_webui_prompt_runtime_ui.py`

- [x] **Step 1: Write source assertions**

覆盖资源树、三类工作区、运行时覆盖 CRUD、slash path 编码、真实 schema 预览。

- [x] **Step 2: Implement UI changes**

`/prompt-v2-templates` 左侧使用资源树，工具按目录折叠，任务模板独立工作区；旧页面标题标记为 v1/legacy。

- [x] **Step 3: Run UI tests and frontend build**

Run:

```bash
/home/dascard/anaconda3/bin/python -m pytest -s tests/test_webui_prompt_runtime_ui.py -v
cd webui && npm run lint && npm run build
```

Expected: UI 源断言通过，lint 0 errors，build 通过。

### Task 6: Final verification

**Files:**
- Verify all changed behavior.

- [x] **Step 1: Run targeted Python verification**

Run:

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
/home/dascard/anaconda3/bin/python -m pytest -s tests/test_prompt_v2.py tests/test_prompt_v2_template_admin.py tests/test_prompt_v2_template_registry.py tests/test_prompt_v2_tool_template_integration.py tests/test_webui_prompt_runtime_ui.py tests/test_prompt_trace_admin.py::test_admin_prompt_and_trace_endpoints tests/test_final_tools.py -v
```

Expected: 35 passed。

- [x] **Step 2: Run group analysis regression**

Run:

```bash
/home/dascard/anaconda3/bin/python -m pytest -s tests/test_group_analysis_tool.py -v
```

Expected: 20 passed。

- [x] **Step 3: Run final formatting check**

Run:

```bash
git diff --check
```

Expected: no output。
