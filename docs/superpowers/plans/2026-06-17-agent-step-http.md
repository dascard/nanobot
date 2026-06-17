# Agent Step HTTP 半 ReAct 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增 Nanobot `agent-step.v1` HTTP step/resume endpoint，让 SynergyOpt 执行工具、Nanobot 只负责工具选择和最终回答。

**架构：** `core/agent_step.py` 负责协议 DTO、prompt 构造、LLM 响应解析和 SSE 事件生成；`api/routes.py` 只暴露 `/api/v1/chat-step` 并做认证/响应包装。现有 `/api/v1/chat` 不改行为。

**技术栈：** FastAPI、Pydantic、NewAPIClient、pytest、SSE text/event-stream。

---

### 任务 1：协议测试

**文件：**
- 创建：`tests/test_agent_step_api.py`

- [x] **步骤 1：编写失败测试**

测试 `/api/v1/chat-step` 在 mock LLM 返回 tool call 时输出 `status=tool_call`，并检查传给 LLM 的 tools schema。

- [x] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_agent_step_api.py -v`
预期：导入或接口不存在导致失败。

### 任务 2：协议核心实现

**文件：**
- 创建：`core/agent_step.py`

- [x] **步骤 1：定义 DTO**

实现 `AgentStepRequest`、`AgentStepResponse`、`AgentStepToolCall`、`AgentStepToolResult`。

- [x] **步骤 2：实现 runner**

实现 `run_agent_step(req)`，调用 `NewAPIClient.chat_completion(messages, tools=...)`，解析 OpenAI tool calls 或 JSON final。

- [x] **步骤 3：运行测试验证通过**

运行：`python -m pytest tests/test_agent_step_api.py -v`

### 任务 3：路由接入

**文件：**
- 修改：`api/routes.py`

- [x] **步骤 1：新增 `/chat-step`**

引入 `AgentStepRequest` 和 `run_agent_step`，根据 `stream` 或 `Accept: text/event-stream` 返回 JSON 或 SSE。

- [x] **步骤 2：运行测试验证通过**

运行：`python -m pytest tests/test_agent_step_api.py -v`

### 任务 4：回归验证

**文件：**
- 修改：无

- [x] **步骤 1：运行目标测试**

运行：`python -m pytest tests/test_agent_step_api.py tests/test_api.py -v`

- [x] **步骤 2：确认旧聊天接口未破坏**

检查 `tests/test_api.py::test_proxy_chat` 通过。
