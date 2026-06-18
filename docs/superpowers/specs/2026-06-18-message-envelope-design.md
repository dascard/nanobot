# P2-2 标准化请求 / 响应信封设计

日期：2026-06-18

## 背景

P2-1 已完成工具 platform 维度配置闭环，下一步进入 P2-2「messages 接口统一为标准化请求 / 响应信封」。当前请求侧已经基本遵循 `docs/message-field-standard.md`，但响应侧仍分裂：

- 私聊 / Web `/chat` 非流式成功响应返回 `status`、`user_id`、`answer`、`answer_chunks` 和 `unprocessed_logs`。
- `/chat` SSE done 事件返回 `status="done"` 和 `answer`。
- 群聊 `/group/message` 成功响应返回 `action="continue"`、`reply`、`reply_meta`、`generation` 和 `reason`。
- 定时任务和流式断连后台 push 仍通过 `push_to_qq(target_type, target_id, message) -> bool` 发送三元组。

这些差异让调用方必须按入口分别处理，也让 `reply_meta` 在私聊成功路径中被弹出后丢弃，无法表达模型的发送意图。

## 审计结论

本设计基于 2026-06-18 的只读审计：

- `/chat` 成功路径会把 Bridge 返回文本展开为传输层 `answer`，再按 HTML / 换行规则生成 `answer_chunks`。数据库仍保存未展开的原始 `answer`。
- `/chat` 已经通过 `_pop_bridge_reply_meta(bridge, session_id)` 取得 `reply_meta`，但正常成功路径只用它判断 `prompt_v2_audit_failed`，不会进入 HTTP 响应或 SSE done。
- `/group/message` 已把 `reply_meta` 作为顶层字段返回给 QQbot，并在持久化群聊回复时写入 assistant `ChatLog.meta_json.reply_meta`。
- 群聊 TimingGate 的 `action` / `generation` / `delay_seconds` / `reason` 是调度语义，不能简单替换为私聊的 `status`。
- `push_to_qq` 当前签名被测试和调用方依赖，P2-2 不应直接改成只接收新信封。
- `docs/message-field-standard.md` 目前主要规范入站字段，缺少响应信封章节。

## 目标

P2-2 的目标是引入一个可被四类出口共享的响应信封：

- `/chat` 非流式响应。
- `/chat` SSE done 事件。
- `/group/message` 响应。
- push / 定时任务出站适配。

第一阶段采用兼容双写策略：旧字段保持不变，新字段并行新增。调用方可以逐步迁移到标准信封，不要求 QQbot、WebUI 或已有测试在同一个阶段全部改造。

## 非目标

以下内容属于 P2-3「QQ 出站渲染契约」，不纳入 P2-2 实现：

- 出站 `segments` 的最终协议，例如 `text`、`image`、`html`、`at`、`reply`。
- `[generated_image:...]`、`[sticker:...]` 到 CQ 码或 URL 的统一 renderer。
- `NANOBOT_PUBLIC_BASE_URL` 未配置时的完整降级策略。
- HTML 回复如何在 QQbot 端 `html_to_pic` 渲染。
- `send_mode`、引用、@ 发送、多段发送的具体 QQ 渲染行为。
- 是否彻底停止在响应正文里内联 CQ 码。

P2-2 只保证信封能稳定承载这些信息，具体渲染留给 P2-3。

## 推荐方案

采用「内部统一 builder + 外部兼容双写」方案。

新增内部响应信封 builder，统一产出：

```json
{
  "status": "ok",
  "action": "continue",
  "reply": "传输层正文",
  "messages": [
    {
      "type": "text",
      "text": "传输层正文"
    }
  ],
  "reply_meta": {
    "send_mode": "normal",
    "reply_to_message_id": null,
    "mentions": []
  },
  "meta": {
    "platform": "qq",
    "chat_type": "group",
    "reason": "user requested",
    "generation": 1
  }
}
```

旧字段仍保留：

- `/chat` 继续返回 `answer`、`answer_chunks`、`status`、`user_id` 和 `unprocessed_logs`。
- `/chat` SSE done 继续返回 `status="done"` 和 `answer`。
- `/group/message` 继续返回 `action`、`reply`、`reply_meta`、`generation`、`delay_seconds`、`reason`、`diagnostics` 等现有顶层字段。
- `push_to_qq(target_type, target_id, message)` 继续可用，返回值仍是 bool。

## 字段映射

### 标准字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | HTTP / 客户端状态。推荐值：`ok`、`silent`、`no_reply`、`wait`、`error`、`done`。 |
| `action` | string | 群聊 TimingGate / 出站动作。推荐值：`continue`、`wait`、`no_reply`。私聊可为空或由 `status` 派生。 |
| `reply` | string | 传输层正文，等价于旧 `/chat.answer` 或旧 `/group/message.reply`。 |
| `messages` | list[object] | 标准化输出消息数组。P2-2 首版只由现有正文派生，不定义完整 QQ 渲染协议。 |
| `reply_meta` | object | 发送意图元数据，来源于 reply tool / Bridge store。 |
| `meta` | object | 调试、路由、时机、平台和追踪信息。 |

### `messages` 首版结构

P2-2 首版只使用保守结构：

```json
{
  "type": "text",
  "text": "正文"
}
```

HTML 正文使用：

```json
{
  "type": "html",
  "text": "<article>...</article>"
}
```

图片、at、reply segments 的最终结构留给 P2-3。P2-2 可以把现有 CQ 或短 token 原样保留在 `text` 中，不做额外渲染解释。

### `reply_meta` 过滤规则

对外响应中的 `reply_meta` 只保留发送协议字段：

- `send_mode`
- `reply_to_message_id`
- `mentions`
- `quote`
- `at_sender`

内部字段不得直接暴露为渲染协议：

- `_agent_result`
- `_no_reply`
- `_no_reply_reason`

这些内部字段如需排查，可放入 `meta.agent_result` 或 `meta.diagnostics`。

## 出口设计

### `/chat` 非流式

保留旧响应：

```json
{
  "status": "ok",
  "user_id": "u1",
  "answer": "你好",
  "answer_chunks": ["你好"],
  "unprocessed_logs": 3
}
```

新增标准字段：

```json
{
  "reply": "你好",
  "messages": [{"type": "text", "text": "你好"}],
  "reply_meta": {},
  "meta": {
    "user_id": "u1",
    "session_id": "private_u1",
    "platform": "qq",
    "chat_type": "private",
    "unprocessed_logs": 3
  }
}
```

casual、silent、no_reply、blocked 等短路分支也应补空 `reply`、空 `messages` 和基础 `meta`，但保持原有顶层字段。

### `/chat` SSE done

保留旧 done：

```json
{
  "status": "done",
  "answer": "最终答案"
}
```

并行新增：

```json
{
  "reply": "最终答案",
  "messages": [{"type": "text", "text": "最终答案"}],
  "reply_meta": {},
  "meta": {
    "user_id": "u1",
    "session_id": "private_u1",
    "platform": "qq",
    "chat_type": "private"
  }
}
```

SSE `progress`、`delta`、`heartbeat` 和 `error` 事件暂不强制改造，只保证 done 事件有标准信封字段。

### `/group/message`

保留旧成功响应：

```json
{
  "action": "continue",
  "reply": "群聊回复",
  "reply_meta": {},
  "generation": 1,
  "reason": "user requested"
}
```

并行新增：

```json
{
  "status": "ok",
  "messages": [{"type": "text", "text": "群聊回复"}],
  "meta": {
    "platform": "qq",
    "chat_type": "group",
    "generation": 1,
    "reason": "user requested"
  }
}
```

`wait` 和 `no_reply` 响应保留顶层 `action`、`delay_seconds`、`generation` 和 `reason`，同时新增 `status`、空 `reply`、空 `messages` 和 `meta` 镜像。

### Push / 定时任务

保留旧 helper：

```python
await push_to_qq(target_type, target_id, message)
```

新增适配层，而不是直接破坏旧签名：

```python
await push_envelope_to_qq(target_type, target_id, envelope)
```

`push_envelope_to_qq()` 从信封读取 `reply` 或 `messages` 派生 `message`，再调用旧 `push_to_qq()`。`run_scheduled_tasks() -> int` 保持不变。

## 状态映射

| 场景 | 旧字段 | 新字段 |
| --- | --- | --- |
| 私聊成功 | `status="ok"` | `status="ok"`，`reply=answer` |
| 私聊静默 | `status="silent"` | `status="silent"`，`reply=""`，`messages=[]` |
| 私聊不回复 | `status="no_reply"` | `status="no_reply"`，`reply=""`，`messages=[]` |
| SSE 完成 | `status="done"` | `status="done"`，`reply=answer` |
| 群聊继续 | `action="continue"` | `status="ok"`，`action="continue"` |
| 群聊等待 | `action="wait"` | `status="wait"`，`action="wait"` |
| 群聊不回复 | `action="no_reply"` | `status="no_reply"`，`action="no_reply"` |
| 错误 | HTTP error 或 `status="error"` | 保留旧错误，同时在可控 JSON / SSE 中补 `meta.error_type` |

## 实现边界

建议新增独立模块，例如 `core/message_envelope.py`，集中放置：

- `build_text_messages(reply: str) -> list[dict]`
- `build_response_meta(...) -> dict`
- `sanitize_reply_meta(reply_meta: dict | None) -> dict`
- `build_chat_response_envelope(...) -> dict`
- `build_group_response_envelope(...) -> dict`
- `push_envelope_to_qq(...)`

这样避免继续把响应字段拼装散落在 `api/routes.py`、`app/group_ingress/service.py` 和 `core/daily_digest.py` 中。

## 测试计划

### 红灯测试

先添加失败测试，证明当前缺口存在：

- `/chat` 非流式成功响应包含 `reply`、`messages`、`reply_meta` 和 `meta`。
- `/chat` 成功路径会返回经过过滤的私聊 `reply_meta`。
- `/chat` SSE done 包含 `reply`、`messages`、`reply_meta` 和 `meta`，同时保留旧 `answer`。
- `/group/message` continue 响应包含 `status`、`messages` 和 `meta`，同时保留旧 `action`、`reply` 和 `reply_meta`。
- `/group/message` wait / no_reply 响应包含空 `reply`、空 `messages` 和 `meta`，同时保留旧字段。
- `push_envelope_to_qq()` 不改变 `push_to_qq()` 旧签名，并能从信封派生 message。
- `docs/message-field-standard.md` 增加响应信封章节。

### 回归测试

实现后至少运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_api.py \
  tests/test_streaming_api.py \
  tests/test_daily_digest.py \
  tests/test_reply_contract.py \
  tests/test_bridge_integration.py \
  -v -p no:cacheprovider
```

最终阶段运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

## 风险与缓解

- **旧调用方依赖顶层字段**：第一阶段只新增字段，不删除旧字段。
- **`reply_meta` 弹出即消费**：实现时必须先保存弹出的 meta，再同时用于 audit 判断和响应构造。
- **内部字段泄漏**：对外 `reply_meta` 必须过滤 `_agent_result` 等内部键。
- **群聊空回复语义复杂**：先保留原 `action/reason/diagnostics`，只镜像到 `meta`。
- **P2-2 和 P2-3 范围混淆**：本阶段不定义完整出站 segments，不重写 CQ 渲染。

## 验收标准

- `/chat` 非流式、`/chat` SSE done、`/group/message` 和 push 适配都能生成标准响应信封字段。
- 所有旧字段仍然存在，旧测试不需要因为字段删除而重写。
- 私聊成功路径返回过滤后的 `reply_meta`。
- `docs/message-field-standard.md` 记录响应信封标准和兼容字段。
- 定向测试和全量测试通过。
