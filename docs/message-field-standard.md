# Message 字段标准

本文档定义 Nanobot 对外消息入口的字段语义、命名规则和多平台适配标准。目标是让 QQ、Web、业务系统、未来的飞书/微信等适配器在进入 Nanobot 前使用一致的身份、会话、消息、文件和元信息约定，避免同名字段在不同平台上表达不同含义。

当前稳定入口：

- `POST /api/v1/chat`：私聊、Web 聊天、业务系统到 Nanobot 的标准入口。
- `POST /api/v1/group/message`：群聊统一入口，当前主要服务 QQ/OneBot/NapCat 场景。

## 总体原则

1. 入口字段表达的是 Nanobot 运行时需要的标准消息信息，不是某个平台的原始 payload。
2. 平台原始字段必须在 adapter 层完成映射；只有确实需要排查或回溯的原始信息放入 `client_meta`。
3. `user_id` 表示“消息发送者身份”，`session_id` 表示“上下文会话范围”，`message_id` 表示“平台原始消息幂等键”，三者不能混用。
4. ID 必须带命名空间前缀，避免不同平台 ID 撞库。
5. `query` / `message` 放用户可见的自然语言内容；业务上下文、平台上下文、权限信息、trace 信息放 `client_meta`。
6. `files` 只放 Nanobot 能识别或后续工具能解析的文件引用，不放完整文件对象。
7. `client_meta` 是结构化扩展字段，不是任意垃圾桶；新增字段必须使用稳定 key，并优先放在已定义的子对象下。
8. 前端、QQbot 或业务系统不得把平台 token、用户私密凭据、数据库连接串等敏感信息放入任何 message 字段。

## ID 命名规则

所有跨平台 ID 都采用：

```text
<platform>:<kind>:<raw_id>
```

若现有字段只允许一个业务 ID，也至少保留 `<platform>:` 前缀。

推荐前缀：

| 场景 | 示例 | 说明 |
| --- | --- | --- |
| SynergyOpt 用户 | `synergy:user:u-demo-admin` | 业务系统用户 |
| SynergyOpt 会话 | `synergy:session:conv-001` | 业务系统会话 |
| Web 用户 | `web:user:123` | Nanobot Web 或其他 Web 客户端 |
| Web 会话 | `web:session:abc` | Web 私聊会话 |
| QQ 用户 | `qq:user:0000000000` | QQ 发送者 |
| QQ 群 | `qq:group:987654` | 原始群号 |
| QQ 群运行时会话 | `group_987654` | 兼容现有群聊上下文；新代码可在边界层生成 |
| 飞书用户 | `feishu:user:<open_id>` | 未来 adapter 使用 |
| 微信用户 | `wechat:user:<openid>` | 未来 adapter 使用 |

禁止：

- 私聊把 `session_id` 写成裸用户 ID。
- 群聊把 `user_id` 写成群号。
- 同一个字段有时放平台 ID，有时放业务系统用户 ID。
- 不带平台前缀地传入 `123456` 这类裸 ID，除非是兼容旧接口且只在单平台内部使用。

## `/api/v1/chat`

`/chat` 是私聊、Web、业务系统调用 Nanobot 的首选入口。它不进入群聊 TimingGate，不处理 @、群引用、群现场上下文。

### 请求字段

| 字段 | 类型 | 必填 | 标准含义 | 写入规则 |
| --- | --- | --- | --- | --- |
| `user_id` | string | 否，默认 `default_user` | 消息发送者的稳定身份 ID。用于用户记忆、画像、权限绑定、日志归属。 | 生产调用必须显式传入，格式建议 `<platform>:user:<id>` 或 `<app>:user:<id>`。业务系统调用时用业务系统用户 ID，不用浏览器临时 session。 |
| `session_id` | string | 否，默认 `default_session` | 对话上下文范围。相同 `session_id` 会共享历史注入和串行处理锁。 | 私聊通常用 `<platform>:session:<id>`；若没有独立会话，可用 `<platform>:private:<user_id>`。不同业务页面、Agent scope 或任务流需要隔离时必须使用不同 `session_id`。 |
| `query` | string | 否，默认空字符串 | 本轮用户输入的自然语言正文。 | 只放用户希望 Nanobot 回答的内容。不要把大段原始 JSON、平台 envelope、权限列表、trace 信息直接拼进来。业务分析可附带经过服务端整理的简短上下文，但应有清晰标题。 |
| `files` | list[string] \| null | 否 | 本轮关联的文件引用。 | 每项必须是可由 Nanobot 或工具解析的字符串引用，如受控本地路径、已缓存文件 ID、可访问 URL。不要放 base64、完整文件对象、平台临时 token。 |
| `sender_name` | string \| null | 否 | 发送者展示名。 | 用于日志、上下文展示和回复风格。可传昵称、真实名或业务系统显示名；不要作为身份判断依据。 |
| `session_name` | string \| null | 否 | 会话展示名。 | 用于管理 UI 和日志展示，如群名、页面名、业务模块名。不要用于权限或上下文隔离。 |
| `stream` | boolean | 否，默认 `false` | 是否启用 SSE 流式响应。 | Web UI 可设为 `true`；服务端到服务端集成优先 `false`，便于错误处理和重试。 |
| `classification_request` | boolean | 否，默认 `false` | 标记该请求是否为分类/判定类内部请求。 | 仅 Nanobot 内部或明确的分类链路使用。普通聊天、业务系统 Agent 问答保持 `false`。 |
| `merged_messages` | list[string] \| null | 否 | adapter 在等待窗口内合并的原始文本片段。 | 只在上游合并多条短消息时传入，按时间顺序排列。`query` 应为合并后的最终文本。 |
| `message_id` | string \| null | 否 | 本轮平台原始消息 ID，用于幂等、排查、消息链路追踪。 | 有平台消息 ID 时必须传。业务系统没有平台消息 ID 时可传请求 ID 或业务消息 ID，但要在 `client_meta.trace.request_id` 同步记录。 |
| `source_message_ids` | list[string] \| null | 否 | 参与合并的原始消息 ID 列表。 | 当 `merged_messages` 非空时同步传入。若未包含 `message_id`，服务端持久化时会补入。 |
| `client_meta` | object \| null | 否 | 客户端、平台、业务和追踪元信息。 | 必须是 JSON object。按本文档的标准结构写入，不要塞未命名的大块原始 payload。 |

### `/chat` 标准示例

```json
{
  "user_id": "synergy:user:u-demo-admin",
  "session_id": "synergy:session:conv-001",
  "query": "请解释最近 7 天能耗趋势，并指出异常点。",
  "files": null,
  "sender_name": "管理员",
  "session_name": "SynergyOpt 能耗分析",
  "stream": false,
  "classification_request": false,
  "merged_messages": null,
  "message_id": "synergy:message:req-20260612-001",
  "source_message_ids": null,
  "client_meta": {
    "platform": "synergy",
    "adapter": "synergy-opt",
    "chat_type": "private",
    "task": {
      "name": "energy_explain",
      "version": "v1"
    },
    "trace": {
      "request_id": "req-20260612-001",
      "source": "synergy-backend"
    },
    "business": {
      "app": "synergy-opt",
      "dataset_refs": ["steel_industry_energy_consumption"],
      "chart_refs": ["energy-trend"]
    }
  }
}
```

## `/api/v1/group/message`

`/group/message` 是群聊入口。它会记录群现场消息，执行去重、用户屏蔽、内容安全、消息指向性推导、TimingGate 和群聊上下文处理。

普通业务系统、Web 私聊、单用户 Agent 不应走这个入口。

### 请求字段

| 字段 | 类型 | 必填 | 标准含义 | 写入规则 |
| --- | --- | --- | --- | --- |
| `group_id` | string | 是 | 平台原始群 ID 或群会话 ID。 | QQ/NapCat 传原始群号字符串。跨平台 adapter 建议传 `<platform>:group:<id>`，若调用现有 QQ 逻辑需在边界层兼容为原始群号。 |
| `sender_id` | string | 否，默认空 | 群消息发送者 ID。 | 建议传平台原始用户 ID；跨平台新 adapter 可传 `<platform>:user:<id>`。必须能和屏蔽规则、bot 自身 ID 做一致比较。 |
| `sender_name` | string | 否，默认空 | 群消息发送者展示名。 | 用于格式化群上下文，如 `[张三]: 文本`。不要用于身份判断。 |
| `message` | string | 否，默认空 | 适配器提取后的群消息纯文本。 | 放去掉平台控制段后的可读文本。图片、表情、文件摘要可用简短文本表达，但结构化信息应放 `segments` / `files` / `client_meta.stickers`。 |
| `files` | list[string] \| null | 否 | 群消息关联的文件、图片、表情引用。 | 只传可解析引用。图片、mface、文件段里的 URL 或 file_id 可同步放这里，便于预缓存和工具处理。 |
| `client_meta` | object \| null | 否 | 平台和 adapter 扩展信息。 | 放平台、adapter、trace、stickers 等结构化字段。不要把整份 OneBot event 原样塞入。 |
| `message_id` | string \| null | 否 | 平台原始消息 ID。 | 强烈建议传入。服务端用它在同一群会话内去重。 |
| `session_name` | string \| null | 否 | 群会话展示名。 | 通常传群名。仅用于展示和日志，不参与上下文隔离。 |
| `is_at_bot` | boolean | 否，默认 `false` | 适配器判断该消息是否 @ 当前 bot。 | 若 `segments` 有 `at` 当前 bot，也会被服务端推导为 true；显式字段和结构化字段应保持一致。 |
| `is_reply_to_bot` | boolean | 否，默认 `false` | 适配器判断该消息是否回复当前 bot。 | 若有 `reply_to.is_bot=true`，服务端也会推导。 |
| `bot_aliases` | list[string] | 否，默认空 | 当前 bot 的别名列表。 | 用于判断文本是否叫 bot 名字。传当前平台上实际可被用户使用的别名。 |
| `segments` | list[object] | 否，默认空 | OneBot/NapCat 消息段或 adapter 归一化后的段列表。 | 支持 `text`、`at`、`reply`、`image`、`mface`、`file`、`forward`。最多保留 30 段，字段会被服务端裁剪。 |
| `raw_message` | string | 否，默认空 | 平台原始消息文本表达。 | 用于回溯和调试，可传 OneBot 的 raw_message；最多按服务端规则入库前裁剪。不要放完整 JSON event。 |
| `self_id` | string | 否，默认空 | 当前机器人在该平台的账号 ID。 | QQ/NapCat 传 bot QQ。用于判断 @/回复/发送者是否当前 bot。 |
| `bot_id` | string | 否，默认空 | 当前机器人逻辑 ID。 | 通常和 `self_id` 一致；如果平台区分应用 ID 和账号 ID，可传更稳定的 bot 身份。 |
| `bot_name` | string | 否，默认空 | 当前机器人展示名。 | 用于日志和回复元信息，不用于身份判断。 |
| `sender_is_bot` | boolean | 否，默认 `false` | adapter 显式标记发送者是否为机器人。 | 用于阻止 bot 消息进入 TimingGate。优先使用结构化平台信号，不要靠昵称猜。 |
| `mentions` | list[object] | 否，默认空 | 显式 @ 列表。 | 每项格式为 `{ "user_id": "...", "nickname": "...", "is_bot": true/false }`。若 `segments` 有 `at`，服务端会自动推导并合并。 |
| `reply_to` | object \| null | 否 | 被回复消息的结构化信息。 | 推荐优先使用该字段，格式见下文。旧字段可兼容，但不要同时给出冲突信息。 |
| `reply_to_message_id` | string \| null | 否 | 被回复消息 ID。 | 兼容旧字段。新 adapter 优先填 `reply_to.message_id`。 |
| `reply_to_sender_id` | string \| null | 否 | 被回复消息发送者 ID。 | 兼容旧字段。新 adapter 优先填 `reply_to.sender_id`。 |
| `reply_to_sender_name` | string \| null | 否 | 被回复消息发送者展示名。 | 兼容旧字段。新 adapter 优先填 `reply_to.sender_name`。 |
| `reply_to_content` | string \| null | 否 | 被回复消息内容摘要。 | 兼容旧字段。新 adapter 优先填 `reply_to.content`，内容应短摘要，不传长历史。 |
| `is_directed_to_other` | boolean | 否，默认 `false` | adapter 判断该消息是否明确指向其他人而不是 bot。 | 当消息只 @ 其他人或只回复其他人时可设为 true。若同时 @ bot 或回复 bot，服务端会优先认为和 bot 相关。 |

### `segments` 标准

`segments` 每项是：

```json
{
  "type": "text",
  "data": {}
}
```

当前支持类型：

| type | data 字段 | 说明 |
| --- | --- | --- |
| `text` | `text` | 文本段。 |
| `at` | `qq` | @ 用户。QQ 场景传 QQ 号；跨平台 adapter 可在边界层转换，但要能和 `self_id` / `bot_id` 比较。 |
| `reply` | `id` | 回复段，只包含被回复消息 ID。若可拿到更多信息，应使用 `reply_to`。 |
| `image` | `file`, `url`, `file_id`, `summary`, `sub_type` | 图片段。 |
| `mface` | `file`, `url`, `emoji_id`, `summary`, `key`, `emoji_package_id` | QQ 表情/超级表情段。 |
| `file` | `file`, `name`, `file_name`, `file_size`, `url`, `file_id` | 文件段。 |
| `forward` | `id`, `summary` | 合并转发段。 |

服务端会做数量和长度裁剪：最多 30 段，`text` 段文本最多 500 字符，非文本段每个允许字段最多 500 字符。

### `mentions` 标准

```json
{
  "user_id": "123456",
  "nickname": "张三",
  "is_bot": false
}
```

规则：

- `user_id` 必须能和 `self_id` / `bot_id` 比较。
- `is_bot=true` 表示该 mention 指向当前 bot 或其他明确机器人。
- 若 `segments` 中已有 `at`，adapter 可以不传 `mentions`；服务端会推导。
- 若平台能提供 nickname，建议传入，便于日志和调试。

### `reply_to` 标准

```json
{
  "message_id": "724390001",
  "sender_id": "123456",
  "sender_name": "张三",
  "content": "上一条消息的短摘要",
  "is_bot": false
}
```

规则：

- 新 adapter 优先传 `reply_to`，旧字段作为兼容。
- `content` 是短摘要，不是完整历史；服务端当前按最多 300 字符处理。
- `is_bot` 应来自平台结构化信号，或由 `sender_id == self_id/bot_id` 判断。

### `/group/message` 标准示例

```json
{
  "group_id": "987654",
  "sender_id": "0000000000",
  "sender_name": "张三",
  "message": "帮我看看今天能耗有没有异常",
  "files": [],
  "client_meta": {
    "platform": "qq",
    "adapter": "napcat",
    "chat_type": "group",
    "trace": {
      "request_id": "qq-msg-724390002"
    },
    "raw": {
      "post_type": "message",
      "message_type": "group"
    }
  },
  "message_id": "724390002",
  "session_name": "生产一群",
  "is_at_bot": true,
  "is_reply_to_bot": false,
  "bot_aliases": ["nanobot", "小南"],
  "segments": [
    {
      "type": "at",
      "data": {
        "qq": "10000"
      }
    },
    {
      "type": "text",
      "data": {
        "text": " 帮我看看今天能耗有没有异常"
      }
    }
  ],
  "raw_message": "[CQ:at,qq=10000] 帮我看看今天能耗有没有异常",
  "self_id": "10000",
  "bot_id": "10000",
  "bot_name": "nanobot",
  "sender_is_bot": false,
  "mentions": [
    {
      "user_id": "10000",
      "nickname": "nanobot",
      "is_bot": true
    }
  ],
  "reply_to": null,
  "is_directed_to_other": false
}
```

## `client_meta` 标准结构

`client_meta` 允许按需扩展，但推荐使用以下顶层 key：

| key | 类型 | 说明 |
| --- | --- | --- |
| `platform` | string | 平台名，如 `qq`、`web`、`synergy`、`feishu`、`wechat`。 |
| `adapter` | string | adapter 名称，如 `napcat`、`webui`、`synergy-opt`。 |
| `adapter_version` | string | adapter 版本，便于排查字段变化。 |
| `chat_type` | string | `private`、`group`、`business`、`system`。 |
| `task` | object | 业务任务信息，如 `name`、`version`、`intent`。 |
| `trace` | object | 链路追踪信息，如 `request_id`、`correlation_id`、`source`。 |
| `business` | object | 业务系统上下文，如 app、tenant、dataset_refs、chart_refs。 |
| `stickers` | list[object] | 表情包注册信息，群聊图片/表情场景使用。 |
| `raw` | object | 必要的原始平台摘要。只保留排查必需字段，不放完整 event。 |

### `client_meta.trace`

```json
{
  "request_id": "req-20260612-001",
  "correlation_id": "corr-abc",
  "source": "synergy-backend"
}
```

规则：

- `request_id` 标识本次 HTTP 调用或平台消息。
- `correlation_id` 标识跨服务链路，可选。
- `source` 标识发起服务或 adapter。

### `client_meta.business`

```json
{
  "app": "synergy-opt",
  "tenant_id": "default",
  "dataset_refs": ["steel_industry_energy_consumption"],
  "chart_refs": ["energy-trend"],
  "permission_scope": ["gwp:agent:assistant:use"]
}
```

规则：

- 只放 Nanobot 生成回答需要知道的业务上下文引用。
- 权限结果应由业务系统自己校验；`permission_scope` 只能作为解释性上下文，不能被 Nanobot 当成授权依据。
- 业务数据明细优先由业务后端整理成 `query` 中的短上下文，或通过受控工具查询，不要把大表塞进 `client_meta`。

### `client_meta.stickers`

```json
[
  {
    "file_ref": "https://example.com/sticker.png",
    "source": "mface",
    "summary": "笑哭表情"
  }
]
```

规则：

- `file_ref` 必填，可来自 `file`、`url`、本地缓存路径或文件 ID。
- 仅用于表情包/贴纸注册和检索。
- 普通图片也可以走 `files`，不一定要放 `stickers`。

## 多平台适配建议

adapter 应执行三步：

```text
平台原始事件 -> adapter 标准化 -> /chat 或 /group/message
```

私聊、Web、业务系统：

- 使用 `/api/v1/chat`。
- 生成稳定 `user_id` 和 `session_id`。
- 把用户可见输入写入 `query`。
- 把平台、业务、trace 信息写入 `client_meta`。

群聊：

- 使用 `/api/v1/group/message`。
- 保留 `group_id`、`sender_id`、`message_id`。
- 将平台消息段转为 `segments`。
- 将 @ 和回复信息转为 `mentions` / `reply_to`，或至少提供可推导的 `segments`。
- 明确传入 `self_id` / `bot_id`，避免 bot 自己的消息再次触发。

业务系统从群聊触发时：

- Nanobot 不应直接使用业务数据库凭据。
- 业务身份绑定和权限校验应由业务系统 API 完成。
- Nanobot 可通过受控工具调用业务系统后端，工具请求中携带平台用户 ID，由业务系统完成绑定校验。

## 常见错误

| 错误 | 后果 | 正确做法 |
| --- | --- | --- |
| `user_id` 在 Web 传业务用户 ID，在 QQ 传群号 | 用户记忆、画像和权限绑定串号 | `user_id` 永远表示发送者 |
| `session_id` 每条消息随机生成 | 无法形成连续上下文 | 同一对话窗口或同一群使用稳定会话 ID |
| 把完整平台 event 放进 `query` | 模型看到大量噪声，prompt 不稳定 | 提取自然语言到 `query` / `message`，必要摘要放 `client_meta.raw` |
| 把所有扩展都塞进 `client_meta` 顶层 | 后续字段冲突、难以排查 | 使用 `trace`、`business`、`task`、`raw` 等子对象 |
| 图片 base64 放入 `files` | 请求体膨胀，工具无法统一处理 | 先缓存，再传 URL、文件 ID 或受控路径 |
| 群聊不传 `message_id` | 重试时可能重复入库或重复回复 | 能拿到平台消息 ID 时必须传 |
| 群聊不传 `self_id` / `bot_id` | 难以判断是否 @ bot 或 bot 自己发言 | adapter 必须传当前 bot ID |
| 用 `sender_name` 判断身份 | 昵称可变且可能重复 | 身份判断只用 ID |

## 兼容性说明

现有代码仍兼容部分裸 QQ ID 和旧式字段，例如 `reply_to_message_id`、`reply_to_sender_id`、`reply_to_content`。新 adapter 应优先按本文档写入结构化字段；旧字段只用于兼容已有 QQbot 链路。

`/api/v1/chat` 当前字段已经足够干净。只有多个平台直接接入同一入口且缺少上述 ID、文件和 `client_meta` 规范时，才会出现字段语义漂移。
