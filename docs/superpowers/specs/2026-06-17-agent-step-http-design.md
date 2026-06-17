# Agent Step HTTP 半 ReAct 设计

## 背景

SynergyOpt 需要让 Agent 基于业务问题选择受控工具，但 Nanobot 不能访问 SynergyOpt API、数据库或用户 JWT。原 WebSocket remote tool bridge 方案会引入长连接注册、反向路由和项目适配成本，本轮改为 HTTP step/resume 协议。

## 决策

新增 `POST /api/v1/chat-step`。该端点不替代现有 `/api/v1/chat`，只服务 SynergyOpt 等外部编排方。Nanobot 在每次请求中接收完整上下文、工具 schema 和历史 tool results，返回以下之一：

- `tool_call`：模型选择一个或多个工具及参数，由调用方执行。
- `final`：模型给出最终回答和建议追问。
- `error`：协议错误或模型调用失败。

当请求 `stream=true` 或 `Accept: text/event-stream` 时，端点复用现有 SSE framing，输出 `progress`、`delta`、`tool_call`、`final`、`error`、`heartbeat` 事件。

## 边界

- Nanobot 不执行 SynergyOpt 工具。
- Nanobot 不访问 SynergyOpt API/DB。
- SynergyOpt 负责权限、工具执行、artifact 生成和持久化。
- 工具必须是显式 schema，不提供 `remote_tool`、`call_api`、`query` 等 catch-all。
- `/api/v1/chat` 行为保持不变。

## 协议

请求：

```json
{
  "protocol": "agent-step.v1",
  "run_id": "run_1",
  "input": { "user_message": "上周哪种负荷类型能耗最高？" },
  "tools": [
    {
      "name": "synergy.energy.load_types",
      "description": "按负荷类型汇总能耗、碳排和成本。",
      "input_schema": {
        "type": "object",
        "properties": {
          "ef": { "type": "number" },
          "price": { "type": "number" }
        },
        "additionalProperties": false
      }
    }
  ],
  "tool_results": [],
  "instructions": {
    "language": "zh-CN",
    "artifact_policy": "side_panel",
    "do_not_fabricate": true
  },
  "client_meta": {
    "app": "synergy-opt",
    "conversation_id": "conv_1",
    "request_id": "req_1"
  },
  "stream": false
}
```

`tool_call` 响应：

```json
{
  "protocol": "agent-step.v1",
  "run_id": "run_1",
  "status": "tool_call",
  "tool_calls": [
    {
      "id": "call_1",
      "name": "synergy.energy.load_types",
      "arguments": { "ef": 0.57, "price": 0.8 }
    }
  ]
}
```

`final` 响应：

```json
{
  "protocol": "agent-step.v1",
  "run_id": "run_1",
  "status": "final",
  "answer": "Medium_Load 类型能耗最高。",
  "suggested_questions": ["查看 Medium_Load 小时峰值？"]
}
```

## 实现

新增 `core/agent_step.py`：

- Pydantic DTO。
- OpenAI tools schema 生成。
- prompt/messages 构造。
- LLM 响应解析。
- SSE 事件序列化。

修改 `api/routes.py`：

- 引入 DTO 和 runner。
- 新增 `POST /chat-step`。
- 复用 `verify_token`。
- 支持 JSON 和 SSE 响应。

新增 `tests/test_agent_step_api.py`：

- 验证工具 schema 请求会调用 `NewAPIClient.chat_completion`。
- 验证 tool call 归一化。
- 验证 final JSON 归一化。
- 验证 SSE 事件包含 progress 和 tool_call/final。

## 验证

运行：

```bash
python -m pytest tests/test_agent_step_api.py -v
```

必要时再运行：

```bash
python -m pytest tests/test_api.py -v
```
