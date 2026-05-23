# WebUI App.jsx 拆分实现计划

**目标：** 将高复杂度页面从 `webui/src/App.jsx` 拆到 feature 模块，降低入口文件职责。

**架构：** `App.jsx` 继续负责认证、布局和路由；AgentRun、LLM API 日志、ReplyEval 的页面组件迁移到 `webui/src/features/*`。共享 trace 展示组件继续放在 `components/TraceView.jsx`。

**技术栈：** React、Vite、pytest、ESLint。

---

### 任务 1：结构测试

**文件：**
- 创建：`tests/test_webui_app_split.py`

- [x] 检查 `App.jsx` 不再定义第一批迁移页面。
- [x] 检查 feature 文件存在并导出页面组件。
- [x] 检查 `App.jsx` 从 feature 模块导入页面。

### 任务 2：抽 AgentRun / LLM 日志

**文件：**
- 创建：`webui/src/features/agent-runs/AgentRunsPage.jsx`
- 创建：`webui/src/features/agent-runs/AgentRunDetailPage.jsx`
- 创建：`webui/src/features/agent-runs/LLMApiLogsPage.jsx`
- 修改：`webui/src/App.jsx`

- [x] 从 `App.jsx` 剪出 `AgentRunsPage`。
- [x] 从 `App.jsx` 剪出 `AgentRunDetailPage`。
- [x] 从 `App.jsx` 剪出 `LLMApiLogsPage`。
- [x] 在新文件补齐 React、router、api、ui、TraceView 依赖。
- [x] 在 `App.jsx` 添加 feature import 并保留原路由。

### 任务 3：抽 ReplyEval

**文件：**
- 创建：`webui/src/features/reply-eval/ReplyEvalPage.jsx`
- 修改：`webui/src/App.jsx`

- [x] 从 `App.jsx` 剪出 `replyEvalTone`、`caseToDraft`、`draftToPayload`、结果表组件和 `ReplyEvalPage`。
- [x] 在新文件补齐 `api`、`NavLink`、ui 组件和 trace 依赖。
- [x] 保留 `/reply-eval` 路由行为不变。

### 任务 4：抽 Prompt 运行时页面

**文件：**
- 创建：`webui/src/features/prompt/PromptPages.jsx`
- 创建：`webui/src/features/logs/ModelRepliesTab.jsx`
- 修改：`webui/src/App.jsx`
- 修改：`tests/test_webui_prompt_runtime_ui.py`

- [x] 从 `App.jsx` 剪出 legacy Prompt、managed prompt、V2 模板编辑和 V2 preview。
- [x] 保留 `/prompt-preview`、`/prompt-v2-templates`、`/prompt-legacy` 路由行为不变。
- [x] 将日志页内部的模型回复表移动到独立 logs feature，避免 prompt 模块反向承载日志组件。

### 任务 5：抽模型、工具和评测页面

**文件：**
- 创建：`webui/src/features/agent-runs/ToolCallsPage.jsx`
- 创建：`webui/src/features/models/ModelsPage.jsx`
- 创建：`webui/src/features/tools/ToolsPage.jsx`
- 创建：`webui/src/features/evals/EvalsPage.jsx`
- 修改：`webui/src/App.jsx`

- [x] 从 `App.jsx` 剪出工具调用页面。
- [x] 从 `App.jsx` 剪出模型列表、路由配置、供应商和本地组件页面。
- [x] 从 `App.jsx` 剪出工具管理页面。
- [x] 从 `App.jsx` 剪出 Eval 评测页面。
- [x] 保留原路由路径和组件接入名不变。

### 任务 6：验证

**命令：**
- [x] `python -m pytest tests/test_webui_app_split.py tests/test_webui_admin_redesign.py tests/test_webui_prompt_runtime_ui.py -v`
- [x] `npm run lint`
- [x] `npm run build`
