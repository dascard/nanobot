# 全会话专属指导设计

## 状态

- 日期：2026-07-12
- 状态：设计已批准，实施计划已就绪
- 范围：Nanobot Server 与 Admin WebUI
- 不涉及：QQbot 端

## 背景

当前 Prompt Runtime 只提供全局公共规则、平台规则、群聊或私聊规则、用户画像、
历史上下文和工具契约。不同群聊或私聊无法配置稳定的专属回答风格。管理员若要
调整某个会话的称呼、表达习惯或领域背景，只能改全局模板，容易影响其他会话。

项目已经使用 `ChatStreamConfig` 保存会话级策略，但会话身份存在两种表示：聊天
日志和 Bridge 通常使用 `group_<id>` 或 `private_<id>`，配置表使用
`<platform>:<id>:<chat_type>`。群聊已有部分规范化函数，私聊和 `/chat` 兼容入口
尚未共享完整的配置身份解析契约。Admin 通用配置接口也允许直接写入未经规范化
的主键，可能让同一会话产生多条配置。

本设计为所有群聊和私聊增加受限的会话专属指导（Session Guidance）。它作为
独立的 system section 进入 Prompt Runtime，但不能覆盖核心身份、安全规则、
鉴权结果、工具权限或最终输出契约。

## 目标

- 所有群聊和私聊 session 均可配置一段专属指导。
- 配置身份同时包含 `platform`、`chat_type` 和规范化后的外部会话 ID。
- 群聊、私聊、实时请求和 Admin 预览使用同一个配置解析器。
- 专属指导以独立、可空、唯一的 `session_guidance` system section 注入。
- 空配置完全保持当前 Prompt 行为，不增加额外 system message。
- Admin WebUI 支持发现、新建、编辑、草稿预览、保存和清空专属指导。
- 配置保存后从该 session 的下一次请求开始生效。
- 普通日志和 Admin 操作审计不复制指导正文。
- 数据库和 Prompt flow 迁移可重复执行，并提供明确回滚路径。

## 非目标

- 不允许会话指导覆盖全局 Prompt 模板。
- 不允许会话指导改变鉴权、模型路由、工具白名单或工具参数权限。
- 不为每个 session 复制 `chat/main` 或完整 Prompt flow。
- 不增加提示词草稿发布、审批、版本历史或回滚版本库。
- 不增加多个 guidance kind，也不支持多个 session 共享命名模板。
- 不修改 QQbot 入站协议、推送协议或 QQbot 代码。
- 不改变聊天历史、入站幂等 claim 和投递 outbox 使用的原始运行时 `session_id`。

## 2026-07-13 实施契约澄清

- 本功能在 `.codex/plans/prompt-runtime-contract-remediation.md` 完成后实施，依赖其中
  已强化的鉴权事实、strict audit、wire tool schema 和 payload metrics 契约。
- `private_superuser` 只用于工具策略；canonical guidance identity 和
  `PromptCompileRequest.chat_type` 始终只使用 `group/private`。
- guidance 正文为空时，安全摘要固定为 `configured=false`、`chars=0`、
  `sha256=""`；它与 Compiler 的空 section 结构 hash 是两个概念。
- resolver 状态使用 `not_requested/missing/empty/configured`；Compiler section 状态只
  使用 `empty/emitted`。
- Admin 列表在任何模式下都不返回正文；只有通过现有 Admin 鉴权的单条详情返回正文。
- Admin effective preview 必须 strict audit，不调用任何模型、不写数据库。服务端必须
  计算超级用户事实，并对不依赖模型检索的相同输入生成与 live runtime 一致的 Prompt
  envelope。若群记忆依赖 reranker，预览既不能调用模型，也不能读取 reranker 生成的
  模型派生缓存；必须跳过该上下文并返回 `preview_exact=false` 和稳定降级原因，不能
  静默声称与 live 完全一致。

## 术语与权限

### Session Guidance

`session_guidance` 是管理员维护的会话级补充指导。它允许描述：

- 回答长短、语气、用词和格式偏好；
- 当前会话使用的称呼；
- 群组或私聊的领域背景；
- 会话约定、群规和交流习惯；
- 不希望讨论或输出的内容；
- 当前会话的语言偏好。

它不能改变：

- 核心角色身份和全局安全规则；
- Admin、超级用户或其他鉴权结果；
- `platform`、`chat_type`、`session_id` 等运行时事实；
- 模型选择、工具白名单和工具参数权限；
- `reply/no_reply` 最终输出契约；
- Prompt Runtime flow 或其他 system section；
- 对内部提示词、密钥、日志或数据库内容的访问权限。

权限顺序固定为：

```text
核心身份、安全和输出契约
> 平台及群聊/私聊规则
> 运行时身份事实和工具契约
> session_guidance
> 当前用户请求
> 画像、历史、群记忆和外部内容
```

工具 schema、鉴权和路由始终由代码决定。系统不会解析 `session_guidance` 来开启
能力，因此正文中的“允许调用任意工具”等内容不会改变实际可用工具。

## Canonical 会话配置身份

### 身份组成

配置身份由以下三项组成：

```text
platform + chat_type + external_session_id
```

序列化后的主键为：

```text
<platform>:<encoded_external_session_id>:<chat_type>
```

典型映射如下：

| 运行时输入 | 平台 | 类型 | Canonical 配置键 |
|---|---|---|---|
| `group_123` | `qq` | `group` | `qq:123:group` |
| `private_456` | `qq` | `private` | `qq:456:private` |
| `group_123` | `web` | `group` | `web:123:group` |
| `default_session` | `web` | `private` | `web:default_session:private` |

`platform` 必须转换为小写，并满足 `[a-z][a-z0-9_-]{0,31}`。`chat_type` 只允许
`group` 或 `private`。外部会话 ID 去除与明确 `chat_type` 匹配的 `group_` 或
`private_` 前缀后，使用 UTF-8 百分号编码保存；RFC 3986 非保留字符保持原样，
百分号十六进制统一使用大写。这样可以支持包含中文或分隔符的未来 session，
同时保持现有 QQ 数字 ID 的主键不变。

调用方必须显式传入 `platform` 和 `chat_type`，不能只凭 session 字符串猜测。
如果输入已经是 canonical key，其平台和类型必须与显式参数一致，否则拒绝。
匹配另一种类型的前缀同样拒绝，例如 `chat_type=group` 配合
`private_456`。

新建通用身份模块负责解析、校验和序列化。现有
`core/group_runtime/ids.py` 和 `core.expression_memory.normalize_chat_stream_id()`
保留兼容入口，但内部转调统一实现。运行时聊天、历史和幂等仍保留原
`session_id`；本功能只规范化配置查找键，避免扩大为会话数据迁移。

## 数据模型

扩展现有 `ChatStreamConfig`：

```text
session_guidance            TEXT NOT NULL DEFAULT ''
session_guidance_updated_at TIMESTAMP NULL
```

空字符串表示未配置，不增加 `enabled` 字段。清空指导时更新
`session_guidance_updated_at`，但保留同一行的 `talk_value`、群画像、表达学习等
其他策略。指导 SHA-256 和字符数在读取时计算，不增加冗余列。

不使用 `meta_json` 保存正文。显式字段便于迁移、校验、Admin 契约和审计，也避免
正文与其他非结构化元数据混在一起。不复用遗留 `SystemPrompt` 表，因为该表以
`user_id` 为键，并可能受旧进化链路更新，不具备 platform 和 chat type 维度。

## 组件与职责

### 会话配置身份模块

- 校验 `platform`、`chat_type` 和 session ID。
- 解析 canonical key 与明确的旧别名。
- 生成稳定的 canonical `chat_stream_id`。
- 不访问数据库，不读取 Prompt。

### Session Guidance 服务

- 接收显式身份并生成 canonical key。
- 从 `ChatStreamConfig` 读取指导正文。
- 调用统一 validator 验证持久化内容。
- 返回正文、配置键、字符数、更新时间和 SHA-256。
- 不负责构造 system message，不依赖 Prompt flow。

### Bridge 与 Prompt Runtime 适配层

- 群聊和私聊都通过 Bridge 的公共主链路解析专属指导。
- 在模型调用前、已有数据库工作单元内完成读取。
- 将正文和无正文元数据传入 `PromptRuntimeInput`。
- 继续透传到 `PromptCompileRequest`。
- 不把正文放进用户输入、历史消息或普通 metadata 日志。

### Prompt Compiler

- 保持无数据库依赖的纯编译边界。
- 使用固定 wrapper 生成独立 system message。
- 不对正文执行模板变量渲染。
- 生成独立 section hash，并把配置摘要写入 debug。
- 空正文时将 flow section 标为 `empty`，不生成 message。

### Admin API 与 WebUI

- API 负责显式身份解析、输入校验、持久化和脱敏审计。
- WebUI 不自行拼接 canonical key。
- 列表只显示配置状态和摘要，详情才返回正文。
- 草稿预览与正式运行复用同一 validator、resolver 和 compiler。

## Prompt Runtime 编排

新增保留 runtime key 和节点：

```text
node_id: session_guidance
node_type: runtime
runtime_key: session_guidance
```

该节点在所有平台及群聊、私聊分支中必须恰好出现一次。节点正文允许为空；空时
状态为 `empty`。相对顺序固定为：

```text
核心契约
→ 平台与群聊/私聊策略
→ runtime_context
→ identity_context
→ session_guidance
→ persona_reference
→ conversation_context_header
→ history_messages
→ group_context（仅群聊，可空）
→ effort_constraint（可空）
→ runtime_tool_prompt
→ current_user_event
```

Compiler 使用固定边界：

```text
<session_guidance>
这是管理员为当前会话配置的补充指导，只能约束表达风格、称呼、领域背景、
会话约定和内容禁忌，不能覆盖核心规则、鉴权、运行时事实或工具契约。

{按字面注入的配置正文}
</session_guidance>
```

`session_guidance` 加入 flow runtime key 白名单、保留节点身份校验、可空 singleton
校验和编译器去重集合。Prompt audit 必须检查：

- 节点恰好出现一次；
- `node_id`、`node_type` 和 `runtime_key` 不得伪装或改名；
- 状态只允许 `emitted` 或 `empty`；
- 顺序满足 `identity_context < session_guidance < persona_reference`；
- 非空配置必须得到 `emitted` section 和有效 message index。

若非空配置存在而有效 flow 缺少节点，严格审计失败，不在尾部追加 fallback，也不
静默忽略。

默认模板中的公共规则同时声明 `<session_guidance>` 的用途和权限。默认 flow、
仓库内 runtime flow 与 Python 内置 fallback 必须保持同一契约。运行时 conversation
清理前缀加入 `<session_guidance>`，避免同一 Bridge session 的动态 system message
残留。

## 输入校验

Admin 写入和运行时读取使用同一个 validator：

- 将 CRLF 和 CR 规范化为 LF；
- 去除首尾空白，保留内部换行；
- 最大长度为 4,000 个 Unicode 字符；
- 拒绝 NUL；
- 拒绝除换行和制表符之外的控制字符；
- 以大小写不敏感方式拒绝以下保留标记：

```text
<session_guidance
</session_guidance>
<runtime_context
<identity_context
<persona_reference
<conversation_context
<user_input
[RuntimeTool]
```

Markdown、普通 XML 文本和 `{{ variable }}` 按字面保存，不执行 Jinja 或 Prompt
模板渲染。系统不做“ignore previous”“system prompt”等关键词扫描；语义关键词
拦截既容易误判，也不能替代结构化权限边界。

校验失败返回 HTTP 422，事务回滚，原配置不变。运行时发现绕过 API 写入的非法
正文时 fail-closed，不调用模型。

## Admin API

### 配置列表

`GET /api/v1/admin/configs` 保留现有接口，并增加：

- `platform`
- `chat_type`
- `search`
- `configured`
- `effective`

`effective=1` 时汇总并规范化：

- `ChatStreamConfig` 中已有的 canonical 配置；
- `AgentRun` 和 runtime snapshot 中带明确身份的 session；
- `ChatLog`、`ConversationTurn` 中可从 `group_` 或 `private_` 明确识别的旧 session。

缺少平台的旧群聊和私聊记录按现有部署事实归入 `qq`。裸 ID 不做类型猜测，仍可
通过新建表单以显式身份配置。

列表不返回指导正文，只返回：

```json
{
  "chat_stream_id": "qq:123:group",
  "platform": "qq",
  "chat_type": "group",
  "session_guidance_configured": true,
  "session_guidance_chars": 120,
  "session_guidance_sha256": "...",
  "session_guidance_updated_at": "2026-07-12T12:00:00"
}
```

### 通用写入

新增 `PUT /api/v1/admin/configs`。请求必须显式携带：

```json
{
  "platform": "qq",
  "chat_type": "private",
  "session_id": "private_456",
  "session_guidance": "回答保持简洁。"
}
```

服务端生成 canonical key 后 upsert。响应返回 canonical key、完整有效配置及指导
摘要。`session_guidance=""` 只清空专属指导，不删除同一行的其他策略。

现有 `GET/PUT/DELETE /configs/{chat_stream_id:path}` 保留兼容，并统一调用新的身份
解析和配置服务。canonical key 可直接使用；`group_123`、`private_456` 等明确别名
可以转换；动态路径中无法判断类型的裸 ID 返回 422，不再默认为群聊。

`DELETE /configs/{chat_stream_id:path}` 继续表示删除整条配置覆写，恢复全部默认值，
与清空专属指导保持不同语义。

### 草稿预览

现有 Prompt Runtime 有效预览请求增加 Admin-only 字段：

```text
session_guidance_override: string | null
```

语义固定为：

- 未提供或为 `null`：读取数据库有效配置；
- 非空字符串：预览未保存草稿；
- 空字符串：预览清空后的 Prompt。

预览执行完整 Prompt 编译和严格审计，但不调用模型、不写数据库。响应继续包含
section 顺序、section hash、messages 和最终 request JSON。私聊超级用户由服务端
身份函数判定；guidance identity 仍使用 `private`，只有 ToolPlan 使用
`private_superuser`。群聊和私聊滚动摘要在预览中使用只读试算：允许构造未挂入 ORM
Session 的临时摘要用于渲染，但不得归档旧摘要、保存 fallback 或创建异步摘要任务。

群记忆检索使用显式 no-model 策略。当前配置要求 reranker 时，不创建 provider、
不调用 HTTP 或本地模型，也不读取 reranker 生成的模型派生缓存，并以
`group_memory_model_calls_forbidden` 标记降级。响应中的 `preview_exact` 和
`preview_degraded_reasons` 必须让 Admin 与 WebUI 能区分精确预览和降级预览。

### 鉴权与审计

只有通过现有 `verify_admin` 的请求可以读取正文、保存或预览草稿。会话成员、群
管理员消息、外部网页和工具结果均不能更新配置。

Admin 操作审计不保存正文，只记录：

```json
{
  "chat_stream_id": "qq:123:group",
  "session_guidance_changed": true,
  "old_chars": 80,
  "new_chars": 120,
  "old_sha256": "...",
  "new_sha256": "..."
}
```

## Admin WebUI

现有“群聊策略配置”页面改名为“会话策略配置”，同时覆盖群聊和私聊。

列表提供：

- 平台筛选；
- 群聊/私聊类型筛选；
- 已配置/未配置筛选；
- canonical stream ID 与显示名称搜索；
- 专属指导状态、字符数和更新时间；
- legacy alias 与身份冲突提示。

页面增加“新增会话配置”，允许在 session 尚未出现在聊天记录前，通过
`platform + chat_type + session_id` 提前配置。编辑弹窗保留现有流策略字段，并
增加：

- 多行指导文本框；
- 4,000 字符计数；
- 允许范围与非秘密存储提示；
- “预览草稿”；
- “保存”；
- “清空专属指导”；
- “删除整条覆写”。

“清空专属指导”保存空字符串，不影响其他策略。“删除整条覆写”保留现有二次
确认，并明确会恢复该 session 的全部默认策略。草稿预览展示有效 section 顺序和
最终 messages，不调用模型；模型依赖上下文被跳过时必须显示降级警告。

## 日志与敏感信息边界

`session_guidance` 不能保存 Token、密码、个人隐私或其他秘密。正文必然会：

- 发送给当前模型供应商；
- 出现在 Admin 有效 Prompt 预览中；
- 出现在现有高权限 `LLMApiRequestLog.request_json` 完整请求记录中。

普通应用日志不输出正文。Prompt debug 和 Prompt trace 只额外记录：

```text
session_guidance_configured
session_guidance_chat_stream_id
session_guidance_chars
session_guidance_sha256
session_guidance_resolution_status
session_guidance_status
```

其中 `session_guidance_resolution_status` 使用
`not_requested/missing/empty/configured`，`session_guidance_status` 只表示 Compiler
section 的 `empty/emitted`。

`section_hashes["session_guidance"]` 和完整 Prompt 的 `prompt_sha256` 会随指导变化，
用于定位一次请求使用的有效版本。它们不替代现有高权限完整 LLM 请求日志。

## 失败策略

运行时采用 fail-closed：

- 不存在配置行或正文为空：正常继续，不生成 section；
- canonical identity 无法构造：不查找其他 alias，不调用模型；
- 数据库读取失败：不绕过可能存在的会话禁忌，不调用模型；
- 持久化正文校验失败：拒绝注入，不调用模型；
- 非空指导存在但 flow 缺少合法节点：严格 Prompt audit 失败，不调用模型。

错误进入现有技术失败、入站 claim/recovery 和投递保护路径。trace 只记录配置键、
错误类型、字符数和 hash，不记录正文。Admin 写入和草稿预览中的身份或正文校验
失败返回 422；有效 Prompt flow 编译或严格审计失败返回 400。两类错误都不产生
部分写入。

resolver 在 Bridge 公共主链路内执行，因此 resolver 异常时 Bridge 已进入，但
ToolPlan、Prompt compiler 和模型循环不得继续。路由层将该异常作为技术失败结算；
同一入站消息恢复重试时沿用既有 fenced claim/recovery，且私聊不得登记空投递
outbox 或调用 QQ push。

私聊流式成功结果只有在 claim `complete()` 返回 `True` 后才能发送 `done`。
结算失败时发送安全错误，不登记断连 outbox，也不调用 QQ push；`done` 已交给传输层
后关闭迭代器不得补发。只有 `done` 交接前的真实断连才由 owned finalizer 登记 outbox、
完成同一个 claim settlement 并尝试 QQ 投递。传输取消不能取消已启动的 settlement，
后台路径必须等待同一个 shielded task，禁止重复完成。若断连发生时 settlement 已经
启动，必须先确认该任务成功再登记 outbox；任务返回 `False` 或抛错时只执行失败结算，
不得留下可被 worker 后续投递的 pending outbox。

本功能不缓存 Prompt 编译结果，也不缓存专属指导。Admin 保存后下一次请求直接
读取新配置；后续若增加缓存，缓存键必须包含 canonical stream ID 和配置版本或
更新时间，并在 Admin 更新、清空和删除时失效。

## 数据库迁移与历史身份兼容

增加两个幂等 schema migration：

```text
20260712_chat_stream_session_guidance_columns
20260712_chat_stream_identity_normalization
```

第一个 migration 向 `chat_stream_configs` 增加正文和更新时间字段。旧行默认正文
为空，不改变现有 Prompt。新数据库由 SQLAlchemy 模型直接创建字段。

第二个 migration 在 SQLite backup 后执行历史身份规范化：

- `group_123` 可确定转换为 `qq:123:group`；
- `private_456` 可确定转换为 `qq:456:private`；
- canonical 目标不存在时，安全更新旧主键；
- alias 与 canonical 同时存在时，不自动覆盖、合并或删除；
- canonical 行作为有效配置，alias 行保留并在 Admin 标记冲突；
- 裸 ID 和无法解析的记录保留，但不用于专属指导解析。

所有新写入只产生 canonical key。迁移不得修改聊天历史、用户画像、群记忆、入站
claim 或投递 outbox 的 session ID。

## Prompt Flow 迁移

服务器上的 `data/prompts_v2/chat/flow.json` 优先于默认 flow，因此不能只修改
`prompts.v2.default`。Prompt Runtime 初始化增加幂等结构迁移：

1. 读取并备份现有 runtime flow；
2. 已有合法 `session_guidance` 节点时不重复处理；
3. 缺少节点时，重连 `identity_context` 下游边并保留原有
   `platforms/chat_types` 条件；
4. 不删除或覆盖其他自定义节点；
5. 对 QQ/Web 与群聊/私聊组合执行 flow contract 校验；
6. 使用临时文件和原子替换写回；
7. 重复启动得到相同 JSON 结构。

如果现有自定义 flow 缺少必要身份节点、无法安全重连或验证失败，恢复原文件并
终止启动。系统不得带着“配置存在但实际未注入”的状态继续服务。

## 发布与回滚

发布时需要：

- 部署 Nanobot Server 新代码；
- 重启服务以运行数据库和 flow 迁移；
- 重新构建并部署 Admin WebUI 静态资源。

发布不需要：

- 修改 QQbot；
- 增加环境变量；
- 手工执行 SQL；
- 清理历史聊天、画像、群记忆或现有会话配置。

未配置指导的 session 保持原行为。发现 legacy identity 冲突时，服务使用 canonical
行并在 Admin 显示待处理项，不自动丢弃数据。

数据库新增列向后兼容，旧代码可以忽略。旧代码不认识 `session_guidance` runtime
key，因此代码回滚必须同时恢复自动生成的旧 flow 备份。实现必须提供可执行的
回滚命令或脚本入口，不要求管理员手工编辑 JSON。指导正文可以保留在数据库中，
重新升级后继续生效。

## 测试设计

### Canonical Identity

- 群聊、私聊、QQ、Web 的规范化结果正确；
- 明确 alias、百分号编码和 canonical key 可稳定往返；
- platform 或 chat type 不匹配时拒绝；
- 同名 session 在不同平台不共享配置；
- 空值、控制字符和非法 canonical key 被拒绝；
- 原运行时 session ID 不被配置规范化改写。

### 数据库与迁移

- 旧数据库幂等补列；
- 重复迁移不改变现有配置；
- alias 无冲突时安全重命名；
- alias 冲突时保留两行并报告；
- 数据库和 flow 备份可以恢复；
- flow 迁移重复运行结果稳定；
- 自定义节点和条件边不丢失；
- 迁移失败不会留下半份数据库或 flow 文件。

### Prompt Compiler

- QQ/Web × 群聊/私聊四条分支均只有一个 `session_guidance`；
- 顺序满足 `identity_context < session_guidance < persona_reference`；
- 空配置不增加 system message；
- 非空配置生成独立 section、message index 和 hash；
- `{{...}}` 保持字面文本；
- 超长、控制字符和保留标记被拒绝；
- flow 缺节点、节点重复或顺序错误触发严格审计；
- 当前用户输入仍是最后一条 user message；
- 工具 schema 与 `reply/no_reply` 契约不受指导正文影响。

### 运行时隔离与故障

- session A 的指导不进入 session B；
- 群聊配置不进入私聊，反之亦然；
- QQ 配置不进入 Web 同名 session；
- Admin 更新后下一次请求使用新 hash；
- 清空指导后恢复旧 Prompt 行为；
- 数据库失败、非法持久化正文和 canonical 失败时不调用模型；
- 非空配置缺少 flow 节点时返回可诊断技术失败；
- 普通日志和 Admin 审计不包含正文；
- 入站 claim/recovery 正确结算技术失败。

### Admin API 与 WebUI

- 未认证请求不能读取或修改正文；
- 列表只返回状态、长度和 hash；
- 单条详情可以读取正文；
- 新建、编辑、保存、清空和删除整条覆写语义正确；
- 草稿预览不写数据库、不调用模型；无模型依赖时精确一致，模型依赖上下文被跳过时
  返回显式降级状态；
- 群聊和私聊 session 都可发现或手工创建；
- WebUI 显示 canonical ID、字符计数、校验错误和身份冲突；
- WebUI 生产构建通过。

## 验收标准

实现完成前必须执行：

```bash
python -m pytest tests/ -v
npm --prefix webui run build
```

验收条件：

- 全量测试为 `0 failures`；
- WebUI 生产构建成功；
- 无配置 session 的最终 Prompt 与当前行为一致；
- 群聊、私聊、QQ 和 Web 的专属指导均能正确隔离并注入；
- Admin 草稿预览使用与真实运行相同的身份、ToolPlan 和编译路径；无模型依赖时结果
  完全一致，无法无模型重放的检索上下文必须显式标记降级；
- 普通日志和 Admin 操作审计不出现指导正文；
- 数据库与 flow 迁移、备份和回滚验证通过；
- 不需要修改 QQbot 或手工修改数据库。

## 实现范围

实现计划至少覆盖以下区域：

- 通用 chat stream identity 模块与兼容 wrapper；
- `ChatStreamConfig` 模型和 schema migration；
- Session Guidance validator、resolver 和运行时服务；
- Bridge、`PromptRuntimeInput`、`PromptCompileRequest` 与 compiler；
- Prompt flow、audit、默认模板、runtime 模板与 bootstrap 迁移；
- Admin 配置 API、Prompt 有效预览与脱敏审计；
- Admin WebUI 会话策略页；
- 单元、集成、迁移、隔离、故障和 WebUI 测试；
- flow 备份与可执行回滚入口。
