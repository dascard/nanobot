# Agent Link v1 WebSocket 接入

Nanobot 在根路径 `/agent-link` 提供 Agent Link v1 WebSocket 服务端。MeaPet
等桌面端作为客户端主动连接该地址；聊天、取消、动态工具发现和前端工具调用
复用同一条连接。

这不是任意 JSON WebSocket。客户端必须遵循 `agent-link.v1` 的固定信封、握手、
关联和幂等约定。核心字段不能通过配置改名；第三方内部协议不同的，应在自己的
连接器内做字段映射。厂商附加数据放在带命名空间的 `extensions` 中。

## 连接配置

Nanobot 使用以下环境变量：

```env
# 生产使用独立凭据；留空只保留旧部署兼容，会回退 NANOBOT_API_TOKEN
NANOBOT_AGENT_LINK_TOKEN=<independent-agent-link-token>

# 可选资源限制
NANOBOT_AGENT_LINK_MAX_FRAME_BYTES=16777216
NANOBOT_AGENT_LINK_HANDSHAKE_TIMEOUT_SECONDS=15
NANOBOT_AGENT_LINK_SEND_TIMEOUT_SECONDS=10
NANOBOT_AGENT_LINK_CHAT_TIMEOUT_SECONDS=300
NANOBOT_AGENT_LINK_TOOL_TIMEOUT_SECONDS=120
NANOBOT_AGENT_LINK_MAX_ACTIVE_CHATS=4
NANOBOT_AGENT_LINK_MAX_PENDING_TOOLS=16
NANOBOT_AGENT_LINK_MAX_TOOLS=128
NANOBOT_AGENT_LINK_MAX_TERMINAL_CHATS=256
NANOBOT_AGENT_LINK_OUTGOING_QUEUE_SIZE=256
NANOBOT_AGENT_LINK_MAX_INLINE_ATTACHMENT_BYTES=5242880
```

兼容回退不会输出 Token 正文，但启动日志会告警
`source=api_token_fallback`，Admin Runtime Overview 也只显示
`configured/source/fallback`。完成现有客户端切换与回滚演练后，生产应取消
回退并独立轮换 API Token 与 Agent Link Token。

本机或显式信任的内网可以连接：

```text
ws://<服务器 IP>:8000/agent-link
```

跨主机生产部署应由反向代理提供 TLS，然后连接：

```text
wss://<域名或服务器 IP>/agent-link
```

Bearer Token 位于第一条应用消息中。远程链路不得通过明文 `ws://` 发送真实
令牌。

MeaPet 配置示例：

```json
{
  "llm": {
    "mode": "agent",
    "agent": {
      "kind": "agent_link",
      "base_url": "ws://192.0.2.10:8000/agent-link",
      "auth_token": "$AGENT_LINK_TOKEN",
      "allow_insecure_ws": true
    }
  }
}
```

`allow_insecure_ws=true` 只适合用户明确认可的可信内网。公网部署应使用 WSS，
并保持该值为 `false`。

## 固定信封

每条消息都是 UTF-8 JSON 文本对象：

```json
{
  "version": "1.0",
  "type": "chat.submit",
  "id": "turn-unique-001",
  "session_id": "meapet-session-1",
  "reply_to": "",
  "payload": {},
  "extensions": {}
}
```

字段要求：

| 字段 | 必需 | 约束 |
| --- | ---: | --- |
| `version` | 是 | `major.minor`；当前为 `1.0`，主版本必须为 1 |
| `type` | 是 | 小写点分类型，例如 `tool.call` |
| `id` | 是 | 消息 ID，不超过 256 字符，不能含换行或 NUL |
| `session_id` | 是 | 握手及后续消息必须保持一致 |
| `reply_to` | 否 | 响应所关联的请求 `id` |
| `payload` | 是 | 当前消息类型的对象 |
| `extensions` | 是 | 无扩展时为 `{}`；键必须带命名空间，最大 64 KiB |

未知的可选顶层字段会被忽略。`payload.required_extensions` 非空时，Nanobot
当前会拒绝该消息。

## 握手

客户端建连后必须首先发送 `control.hello`：

```json
{
  "version": "1.0",
  "type": "control.hello",
  "id": "hello-1",
  "session_id": "meapet-session-1",
  "reply_to": "",
  "payload": {
    "client": {
      "id": "meapet",
      "name": "MeaPet",
      "version": "1.0.0"
    },
    "device": {"id": "stable-device-id"},
    "auth": {"scheme": "bearer", "token": "configured-token"},
    "resume": {"session_id": "meapet-session-1"},
    "capabilities": {
      "chat": {"submit": true, "streaming": true, "cancel": true},
      "tools": {"dynamic": true, "call": true, "cancel": true}
    },
    "required_extensions": []
  },
  "extensions": {}
}
```

`client.id` 是客户端类型的稳定平台 ID，必须匹配
`^[a-z][a-z0-9_-]{0,31}$`。它不是协议名、设备 ID 或会话 ID：

- `agent-link.v1` 是传输协议；
- `client.id` 标识客户端平台，例如 `meapet`；
- `device.id` 标识一个具体客户端实例；
- `session_id` 标识 Agent 会话。

Nanobot 不维护平台白名单。合法的新 `client.id` 会随连接自动登记，断开最后
一条对应连接后从进程内登记表移除。客户端不能指定 Prompt 策略；通过
Agent Link 接入的外部客户端统一由服务端分配 `external_private`，即使把
`client.id` 写成 `qq` 或 `internal` 也不会取得内部策略。Nanobot 其他受信
入口仍保留已有的平台策略映射。握手会在返回 `control.ready` 前验证外部私聊
分支，配置缺失时返回 `PROMPT_POLICY_UNAVAILABLE`，不会先宣告连接可用。

认证成功后 Nanobot 返回关联到 `hello.id` 的 `control.ready`，其中
`payload.client_context` 会回传服务端采用的 `platform_id`、
`policy_profile` 和 `chat_type`，并声明：

- `chat.submit=true`
- `chat.streaming=false`：当前版本以 `chat.final.payload.text` 一次返回完整结果
- `chat.cancel=true`
- `tools.dynamic=true`
- `tools.call=true`
- `tools.cancel=true`

认证、版本或字段不满足时返回 `control.error`，随后关闭连接。服务端不会把
令牌或消息正文写入普通连接日志。

## 工具快照

握手完成后，客户端必须先发送完整 `tools.snapshot`，再发送聊天请求：

```json
{
  "version": "1.0",
  "type": "tools.snapshot",
  "id": "tools-1",
  "session_id": "meapet-session-1",
  "reply_to": "",
  "payload": {
    "revision": 1,
    "tools": [
      {
        "name": "meapet.get_state",
        "description": "读取前端能力与状态摘要。",
        "input_schema": {
          "type": "object",
          "properties": {}
        },
        "output_schema": {
          "type": "object"
        }
      }
    ]
  },
  "extensions": {}
}
```

Nanobot 会原子替换该连接的工具清单，并在对应 KT 会话的下一次请求中：

1. 按 `input_schema` 把工具加入模型 API tools schema；
2. 把同名代理工具加入该会话的执行器；
3. 模型选择工具时通过当前 WebSocket 发送 `tool.call`；
4. 等待关联的 `tool.result` 或 `tool.error`；
5. 若连接已离线，立即向 Agent Loop 返回 `OFFLINE`，不缓存、不补发旧操作。

动态工具不能覆盖 Nanobot 内置工具。工具快照 `revision` 在同一连接内不能
倒退。截图类结果会作为模型多模态图片输入，不把 base64 复制进普通文本结果。

## 聊天

客户端发送：

```json
{
  "version": "1.0",
  "type": "chat.submit",
  "id": "turn-unique-001",
  "session_id": "meapet-session-1",
  "reply_to": "",
  "payload": {
    "content": "包含角色状态、输出约束和本轮输入的完整内容",
    "user_text": "用户原始输入",
    "history": [],
    "frontend_context": {},
    "attachments": [],
    "response_format": "meapet-segments-v1",
    "idempotent": true
  },
  "extensions": {}
}
```

Nanobot 可以先返回 `chat.accepted`，完成后返回：

```json
{
  "version": "1.0",
  "type": "chat.final",
  "id": "final-generated",
  "session_id": "meapet-session-1",
  "reply_to": "turn-unique-001",
  "payload": {
    "text": "<MEAPET_SEGMENT>...</MEAPET_SEGMENT><MEAPET_DONE />",
    "replace": true
  },
  "extensions": {}
}
```

失败时返回 `chat.error`。Agent 返回空字符串也属于失败，Nanobot 返回
`EMPTY_AGENT_RESULT`，不会伪造“暂时无法生成回复”的成功终态。客户端取消时
发送 `chat.cancel`，其中 `reply_to` 和 `payload.request_id` 指向原
`chat.submit.id`。

`chat.submit.id` 是幂等键：

- 相同 ID、相同内容不会再次运行 Agent；
- 原任务仍运行时，新连接会重新订阅原任务；
- 原任务已完成时，Nanobot 直接重放缓存的终态；
- 相同 ID 对应不同内容时返回 `IDEMPOTENCY_CONFLICT`。

终态缓存有容量上限且只存在于当前进程内。容器重启后，客户端可以重发请求，
但不能依赖进程内缓存跨重启去重。

## 前端工具调用

Nanobot 的 Agent Loop 调用前端工具时发送：

```json
{
  "version": "1.0",
  "type": "tool.call",
  "id": "call-unique-001",
  "session_id": "meapet-session-1",
  "reply_to": "",
  "payload": {
    "name": "meapet.get_state",
    "arguments": {}
  },
  "extensions": {}
}
```

客户端可以先返回 `tool.accepted`，最终必须返回关联到 `tool.call.id` 的
`tool.result` 或 `tool.error`。Nanobot 超时或取消调用时发送 `tool.cancel`。

同一运行时方法也允许在没有进行中聊天时发送 `tool.call`，因此主动消息可以
通过快照中的 `meapet.say` 复用当前连接，不需要第二个 WebSocket。

## 连接与离线语义

- 同一 `device.id + session_id` 新连接会替换旧连接。
- 同一连接只有一个有界写队列，聊天终态和工具调用不会并发写 Socket。
- 连接断开时，所有等待中的前端工具立即以 `OFFLINE` 失败。
- 离线期间的新工具操作直接失败，不进入队列，恢复连接后也不会补发。
- 正在运行的聊天可以继续；客户端重连、发送工具快照并以原 ID 重放
  `chat.submit` 后，会继续订阅或取得已缓存终态。
- `control.ping` 会收到关联的 `control.pong`；底层 WebSocket ping/pong 由
  ASGI Server 负责。
