# WebUI App.jsx 拆分

## 背景

`webui/src/App.jsx` 已经集中承载认证、布局、导航、所有页面和多个复杂调试视图。近期 UI 改造继续加重了该文件的职责，导致：

- 页面改动需要审查整个应用入口文件。
- AgentRun、LLM API 日志、ReplyEval 等复杂视图难以独立测试。
- 错误边界和代码所有权粒度过粗。
- 继续新增页面会让 `App.jsx` 变成事实上的前端旧 bridge。

## 目标

- 第一批先拆最容易膨胀、依赖边界相对清晰的模块，并额外迁移 Prompt V2 运行时页面。
- `App.jsx` 保留认证、布局、路由和暂未迁移页面。
- 新页面模块放入 `webui/src/features/*`，按业务域组织。
- 共享 trace 工具继续放在 `components/TraceView.jsx`，但后续页面从 feature 目录导入。

## 第一批拆分范围

- `features/agent-runs/AgentRunsPage.jsx`
- `features/agent-runs/AgentRunDetailPage.jsx`
- `features/agent-runs/LLMApiLogsPage.jsx`
- `features/reply-eval/ReplyEvalPage.jsx`
- `features/prompt/PromptPages.jsx`
- `features/logs/ModelRepliesTab.jsx`
- `features/agent-runs/ToolCallsPage.jsx`
- `features/models/ModelsPage.jsx`
- `features/tools/ToolsPage.jsx`
- `features/evals/EvalsPage.jsx`

## 非目标

- 不一次性拆完整个 `App.jsx`。
- 不改变路由 URL、API 调用和页面行为。
- 不在这一步引入状态管理库或路由框架重写。
- 不在本轮拆分 groups、stickers、blocks、configs、settings、logs、db 等剩余页面。

## 验收标准

- `App.jsx` 不再定义 `AgentRunsPage`、`AgentRunDetailPage`、`LLMApiLogsPage`、`ToolCallsPage`、`ReplyEvalPage`、`PromptV2TemplatesPage`、`EffectivePromptPreviewPage`、`ModelsPage`、`ToolsPage`、`EvalsPage`。
- 新 feature 文件各自导出对应页面组件。
- 原路由仍然使用相同组件名称接入。
- 前端测试、lint、生产构建通过；lint 允许保留既有 hook dependency warning，但不能有 error。
