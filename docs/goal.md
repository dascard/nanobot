# Nanobot Prompt / Context / Tool Schema 重构计划

## 0. 结论

这次真实私聊和群聊 request 暴露的问题不是单个提示词写得不好，而是整个模型请求组装链路缺少单一出口。

当前至少存在 5 套内容来源同时注入：

1. legacy `prompt.md` 巨型 system prompt
2. bridge 运行时动态 system context
3. context_builder 历史消息
4. KT 框架自动生成的英文工具/skill/sub-agent 文档
5. OpenAI native tools schema

这些来源之间没有统一裁剪、去重、溯源和最终校验，导致：

- system prompt 重复
- 群聊规则在私聊出现
- 工具说明重复
- ToolPolicy 和实际 tools_schema 不一致
- 被禁用工具仍在 prompt 或 API tools 里出现
- 历史消息过旧且污染当前上下文
- 内部控制消息以 user role 注入
- 工具执行后的框架消息污染下一次模型请求
- retry prompt 被作为普通 user message 追加

核心修复方向：

> 统一组装入口
> 硬裁剪 tools_schema
> 每段消息带 source
> 历史只注入可信、近期、当前话题相关内容
> 框架自动注入内容必须可控
> 最终 request 发出前必须 lint

---

# 1. 从真实请求中看到的问题

## 1.1 私聊 request 的问题

私聊 request 里有 50 条 messages，其中前 9 条是 system。

主要异常：

1. 第一条 system 长达约 9500 字，包含公共规则、群聊规则、工具路由、工具说明、最终回复纪律、报告工具规则等全部内容。
2. 私聊场景仍然包含大量群聊行为规则。
3. “本轮简短处理……”出现两次，说明动态 system 清理不彻底。
4. `[ToolPolicy]` 声称本轮只有 4 个工具可用。
5. 但 API `tools` 数组实际发出了 7 个工具：`image_summary`、`sticker_search`、`reply`、`no_reply`、`skill`、`memory_read`、`memory_write`。
6. `skill`、`memory_read`、`memory_write` 没有被 ToolPolicy 真正硬裁剪。
7. history 中出现大量过旧内容，从 05-19 到当前相隔很久，不符合私聊 30 分钟窗口预期。
8. gap marker 被作为 user role 注入，例如“系统生成的上下文提示，不是用户发言……”。
9. 当前工具循环中出现 `[Tool None completed]` 这类内部消息，并且以 user role 进入模型请求。
10. reply contract retry prompt 也以 user role 追加到了同一轮对话尾部。

这些会让模型误以为用户正在发系统控制消息，或者误判当前任务。

## 1.2 群聊 request 的问题

群聊 request 中有 36 条 messages，前 8 条 system。

主要异常：

1. 第一条 system 仍然是巨型 legacy prompt，已经包含群聊行为和上下文规则。
2. 后面又额外注入 `## 群聊行为` 和 `## 群聊上下文使用规则`，造成重复。
3. `[ToolPolicy]` 声明 11 个可用工具，但实际 API `tools` 数组有 12 个，多了 `skill`。
4. `python_sandbox` 在 ToolPolicy 中被禁用，但 config / prompt 里仍然出现其说明。
5. KT 框架英文段落仍然出现：`Available Sub-Agents`、`Available Functions`、`Skills`、`Tool Usage`、`Background Execution`。
6. 群聊历史注入了 05-11、05-19 的很旧表情包历史，与当前“发个表情包”关联很弱。
7. 工具调用后，`sticker_search` 返回了结果，但后面出现 `[Tool None completed]` user message，可能导致下一步模型不知道该用 reply 发送 `reply_token`。
8. 当前请求中既有真实 tool role，又有框架伪造的 user 控制消息，角色边界混乱。

---

# 2. 源码定位

## 2.1 legacy prompt 过大

`creatures/nanobot/prompt.md` 当前包含：

- 交互定位
- 输出契约
- 上下文权限
- 安全规则
- 说话风格
- 群聊行为
- 群聊上下文使用规则
- 工具路由
- 工具调用纪律
- 工具说明
- 最终回复纪律
- 注意事项
- 报告工具结束规则

其中工具相关说明与 API `tools` schema 重复，群聊规则也会进入私聊。

## 2.2 KT 配置会触发自动工具文档

`creatures/nanobot/config.yaml` 中配置了：

- tools
- builtin tools
- subagents
- `tool_format: native`
- `system_prompt_file: prompt.md`

真实 request 里的英文段落大概率是 KT 根据 config 自动生成的工具文档。

例如：

- `Available Sub-Agents`
- `Available Functions`
- `Skills`
- `Tool Usage`
- `Background Execution`

这些内容不在仓库 prompt 文件中，说明它们不是你自己写的模板，而是框架自动注入。

## 2.3 ToolPolicy 只是软提示

`core/tool_policy_service.py` 会解析 enabled / disabled，并生成 `[ToolPolicy]` 文本。

但 bridge 里目前只裁剪：

```python
self._agent.registry._tools
```

这无法覆盖：

- `skill`
- sub-agents
- `memory_read`
- `memory_write`
- KT 框架自动生成的 tool docs
- 最终 OpenAI payload 的 `tools` 数组

所以 ToolPolicy 和实际 API tools 不一致。

## 2.4 bridge 清理 system 消息靠字符串前缀

`NanobotBridge.DYNAMIC_SYSTEM_PREFIXES` 通过前缀清理动态 system 消息。

问题：

1. 前缀清理脆弱。
2. 未知来源 system 无法定位。
3. `effort_constraint` 这类纯文本控制消息没有稳定 marker，导致“本轮简短处理……”重复。
4. KT 自动工具文档没有被识别和清理。

## 2.5 build_session_memory 没有真正使用时间窗口

`core/context_builder.py` 定义了：

- `PRIVATE_CONTEXT_MAX_AGE_MIN = 30`
- `GROUP_CONTEXT_MAX_AGE_MIN = 10`

但 `build_session_memory()` 当前主要是按数量倒序取 ConversationTurn，并没有真正用 `created_at >= cutoff` 过滤时间窗口。

结果：

- 私聊注入了几小时前甚至几天前的内容。
- 群聊注入了 05-11 的旧表情包历史。
- gap marker 大量出现。

## 2.6 gap marker 被作为 user role 注入

`build_session_memory()` 在相邻消息间隔超过阈值时插入：

```python
role = "user"
content = "[系统生成的上下文提示，不是用户发言：距离上一条消息间隔约...]"
```

这会污染 role 语义。

它应该是 system metadata，或者直接不注入给模型。

## 2.7 工具循环内部消息污染模型请求

真实请求中出现：

```text
[Tool None completed]
```

并且以 user role 进入 messages。

这不是用户输入，也不是可回答内容。

它应该被过滤，或者转成内部 trace，不应该进入模型上下文。

## 2.8 reply retry prompt 被当成 user 消息

当前 retry prompt 类似：

```text
你刚才没有调用 reply 或 no_reply 工具
...
这轮必须只调用一个工具
```

它被直接追加进 conversation，作为 user role 进入下一次模型请求。

这会污染历史，也容易被持久化。

应改为专门的 internal correction message，或者构造一个独立 retry request，不污染主 conversation。

---

# 3. 修改目标

## 3.1 统一 request 组装

新增统一组装器：

```python
ConversationAssembler
```

它负责：

- 收集 base prompt
- 收集 runtime context
- 收集 persona
- 收集 history
- 收集 group recent context
- 收集 tool policy
- 收集 managed prompt
- 收集 retry correction
- 生成最终 messages
- 生成最终 tools schema
- 生成 request provenance
- 做 lint

bridge 不再到处 `conv.append()`。

## 3.2 每段消息必须带 source

定义：

```python
@dataclass
class MessageBlock:
    role: str
    content: str
    source: str
    key: str = ""
    path: str = ""
    trust: str = "system|runtime|history|tool|user|internal"
    ttl: str = "request|session|base"
    order: int = 0
    sha256: str = ""
```

所有 system message 必须有来源。

例如：

- `legacy_prompt`
- `kt_framework_tools_doc`
- `identity_context`
- `runtime_context`
- `persona_reference`
- `history_header`
- `private_behavior`
- `group_rules`
- `context_control`
- `group_recent_context`
- `tool_policy`
- `effort_constraint`
- `managed_prompt`
- `reply_contract_retry`

LLM API 日志需要展示 message source。

## 3.3 最终 tools_schema 是唯一硬权限

规则：

- `resolve_effective_tools()` 得到 `final_allowed_tools`
- API `tools` 数组只允许包含 `final_allowed_tools`
- prompt 中工具说明只能基于 `final_allowed_tools` 生成
- 不在 tools_schema 的工具不能出现在 prompt 工具说明里
- `skill`、sub-agents、memory tools 也必须被统一过滤

ToolPolicy 只保留短提示和审计用途，不能再承担权限控制。

---

# 4. 具体修改计划

## Phase 1：先加 request lint 和 provenance

### 4.1 LLM API 日志增加字段

给 `LLMApiRequestLog` 增加：

```text
message_sources_json
request_lint_json
actual_sent_tools_json
policy_enabled_tools_json
policy_disabled_tools_json
framework_injected_tools_json
```

### 4.2 发请求前做 lint

新增：

```python
core/llm_request_linter.py
```

检查：

- system exact duplicate
- same heading duplicate
- unknown system source
- `ToolPolicy` 和 `tools` 不一致
- disabled tool 出现在 tools_schema
- disabled tool 出现在 prompt 文本
- `Available Functions` 出现但未经过裁剪
- `Available Sub-Agents` 出现
- `Skills` 出现
- `Tool Usage` 出现
- `Background Execution` 出现
- `[Tool None completed]` 出现
- user role 中出现“系统生成的上下文提示”
- user role 中出现 retry correction
- history 超过时间窗口
- private request 中出现群聊专属规则
- group request 中出现私聊专属规则

lint 结果写入日志，不先阻塞。

### 4.3 WebUI LLM API 日志展示 lint

在 LLM API 日志详情增加：

- Request Lint
- Message Sources
- Actual Sent Tools
- Policy Tools

红色显示 P0：tools 不一致、内部 user 消息、历史过期。

---

## Phase 2：硬裁剪 tools_schema

### 4.4 新增 final tool resolver

新增：

```python
core/final_tools.py
```

接口：

```python
resolve_final_tools(chat_type, group_id, user_id, tool_policy, db) -> FinalToolSet
```

返回：

```python
class FinalToolSet:
    allowed: set[str]
    disabled: dict[str, str]
    sent_tools: list[str]
    hidden_framework_tools: list[str]
```

### 4.5 `skill` 纳入 TOOL_METADATA 或彻底禁用

两种选择：

推荐：主回复模型彻底禁用 `skill`。

原因：

- `skill` 会绕过 ToolPolicy
- 用户启用/禁用的是具体业务工具，不是 skill wrapper
- 模型会误用 `skill(name="group_analysis")` 代替真实 `group_analysis`

如果保留 `skill`，必须：

- 加入 `TOOL_METADATA`
- 默认 false
- 仅 admin/debug 模式开启
- skill 内部也要校验 `final_allowed_tools`

### 4.6 sub-agents 统一过滤

`memory_read` / `memory_write` 不能绕过 policy。

要求：

- ToolPolicy 禁用时，不出现在 API tools
- 不出现在 `Available Sub-Agents`
- 不出现在 `Skills`
- 不出现在 prompt 文本说明

### 4.7 发请求前二次过滤 payload tools

即使 KT 内部漏了，也要在 OpenAI 请求出口做最后过滤：

```python
payload["tools"] = [t for t in payload["tools"] if tool_name(t) in final_allowed_tools]
```

如果过滤后发现 prompt 里还出现禁用工具说明，lint 报 P0。

---

## Phase 3：关闭或接管 KT 自动英文工具文档

### 4.8 找到自动文档来源

要在 KT Agent 初始化或 controller 构造处定位以下段落生成源：

- `Available Sub-Agents`
- `Available Functions`
- `Skills`
- `Tool Usage`
- `Background Execution`

如果 KT 支持配置关闭，直接关闭。

如果不能关闭，则在 request 发送前做 system message sanitizer：

```python
strip_kt_framework_tool_docs(messages)
```

识别并删除这些段落，或替换为由 `final_allowed_tools` 生成的极简中文说明。

### 4.9 禁止英文工具说明进入最终 prompt

最终 prompt 中不应出现：

- `Use the info tool`
- `Background Execution`
- `Sub-agents are called as tools`
- `Tools are called via the API's native function calling mechanism`

这些是框架操作说明，不适合业务模型。

---

## Phase 4：精简 legacy prompt

### 4.10 拆分 `prompt.md`

把 `prompt.md` 改成只包含 common base：

- 身份/自然对话定位
- 输出必须 reply/no_reply
- 不泄露内部
- 当前 user_input 优先

删除：

- 工具路由大段说明
- 工具参数说明
- 群聊行为大段
- 群聊上下文大段
- 私聊行为大段
- 报告工具结束规则重复描述

### 4.11 群聊/私聊规则只按 chat_type 注入一次

私聊：

- common base
- private behavior
- runtime context
- identity
- persona
- history
- final tools

群聊：

- common base
- group behavior
- group context rules
- runtime context
- identity
- group memory/recent context
- final tools

不能让 common base 内部再包含完整群聊规则。

### 4.12 工具规则极简化

只保留：

```text
最终可见回复必须调用 reply(content)
不回复调用 no_reply(reason)
本轮只会提供实际可用工具
```

工具用途、参数交给 API `tools` schema。

---

## Phase 5：修复 history 注入

### 4.13 真正启用时间窗口

在 `build_session_memory()` 里加入：

```python
age_cutoff = now - timedelta(minutes=PRIVATE_CONTEXT_MAX_AGE_MIN if not is_group else GROUP_CONTEXT_MAX_AGE_MIN)
query = query.filter(ConversationTurn.created_at >= age_cutoff)
```

默认：

- private：30 分钟
- group：10 分钟

如果需要更早历史，让模型用 `sql_analysis` 或专门 memory tool 查。

### 4.14 gap marker 不再作为 user role

删除：

```python
role="user"
content="[系统生成的上下文提示，不是用户发言...]"
```

替代方案：

- 不注入 gap marker
- 或在 history_header 中写一句“历史中已按话题断裂裁剪”
- 或作为 system metadata 注入，但不作为 user message

### 4.15 过滤内部消息

ConversationTurn / history injection 必须过滤：

- `[Tool None completed]`
- reply contract retry prompt
- tool error control text
- no_send system log
- empty placeholder
- `（无回复内容）`
- `group_analysis 现在被禁用` 这类工具权限错误，除非当前问题明确相关
- HTML 报告正文，只保留 artifact summary
- context_gap_marker

### 4.16 ConversationTurn 持久化时标记 kind

新增 kind：

- `chat`
- `artifact_summary`
- `tool_internal`
- `reply_contract_retry`
- `no_send`
- `empty_reply`
- `context_gap_marker`
- `system_control`

history 默认只注入 kind=`chat` 和少量 `artifact_summary`。

### 4.17 历史不要注入过长 prompt-like 用户消息

用户发过很长角色卡、prompt、代码时：

- ConversationTurn 存摘要
- 原文留 ChatLog
- 下轮不直接塞全文

例如 `<summer_lover>` 这种角色卡不应该作为普通历史全文注入。

---

## Phase 6：修复工具循环和 retry

### 4.18 禁止 `[Tool None completed]` 进模型 messages

找到 KT 或 bridge 里把工具完成事件追加为 user message 的位置。

处理方式：

- 不追加到 conversation
- 或发送请求前过滤
- 或转换为 internal trace，不进入 messages

### 4.19 工具结果 role 必须正确

工具结果只能是 role=`tool`。

不能再额外插入：

```text
[Tool None completed]
```

作为 role=`user`。

### 4.20 reply retry 不污染主 conversation

当前 retry prompt 不应 append 为普通 user。

改成：

1. 构造独立 retry messages：
   - 复用原始 system
   - 复用最后一次 assistant raw output
   - 加一条 internal correction system

2. 或者追加 role=`system` 的短 correction，并在下一轮请求后立即移除。

不要把 retry prompt 持久化为历史。

### 4.21 工具结果可以自动收口

对于 `sticker_search`，工具返回 `reply_token` 后，可以在 bridge 层自动完成：

```text
如果最后一个成功工具是 sticker_search
且返回 results[0].reply_token
且本轮还没有 reply/no_reply
则自动 reply(content=reply_token)
```

这样不必再赌模型第二步是否调用 reply。

对 `news_search`、`group_analysis` 这类报告工具也可做同类收口。

---

## Phase 7：修复 system cleanup

### 4.22 不再保留旧 system 消息

现在 bridge reset 时保留所有 system，再靠前缀删除动态 system。

建议改成：

- 每轮彻底清空 conversation
- 重新加载 immutable base system
- 再按 assembler 生成动态 system

如果 KT 不允许清空 base system，则至少保留 `source=base` 的 immutable message，其余全部删除。

### 4.23 source-based cleanup 替代 prefix cleanup

不要再依赖字符串前缀。

每条 system message 写入时记录 source metadata。

清理时删除所有 ttl=`request` 的 system block。

---

## Phase 8：PromptManager / legacy prompt 关系收口

### 4.24 明确唯一主链路

需要决定：

- managed 模式下是否完全替换 legacy prompt
- shadow 模式是否只记录不生效
- legacy prompt 是否只是 fallback

当前 managed 只是额外 append `[ManagedPrompt]`，不是替换。

建议：

- `legacy`：只使用 legacy common/private/group fragments
- `shadow`：使用 legacy，另渲染 PromptManager 仅记录，不注入
- `managed`：使用 PromptManager 输出替换 legacy 行为规则，不再额外保留同类 legacy 段落

### 4.25 AgentRun 记录两种 source

区分：

```text
prompt_render_source: PromptManager 渲染来源
prompt_effective_source: 实际生效来源
prompt_mode: legacy/shadow/managed
```

避免 shadow 模式误导。

---

## Phase 9：WebUI 工具管理修复

### 4.26 工具管理页显示三层状态

每个工具显示：

1. 配置默认启用
2. 本轮策略允许
3. 实际发送给模型

否则“Web 启用了”但本轮 limited 禁掉，用户会误解。

### 4.27 LLM API 日志工具对照

在每条 LLM API request 展示：

- ToolPolicy enabled
- ToolPolicy disabled
- Actual API tools
- Prompt mentioned tools
- Mismatch warnings

### 4.28 新增“实际请求预检”页

输入：

- chat_type
- session_id
- user_id
- group_id
- tool_policy
- query

输出：

- messages with source
- final tools_schema
- lint result
- estimated tokens

---

# 5. 验收标准

## 5.1 私聊 request

必须满足：

- 不出现群聊行为大段规则
- 不出现重复“本轮简短处理”
- 不出现 `Available Functions`
- 不出现 `Available Sub-Agents`
- 不出现 `Skills`
- 不出现 `Tool Usage`
- 不出现 `Background Execution`
- 不出现禁用工具说明
- tools_schema 只包含 final_allowed_tools
- 如果 ToolPolicy 说 4 个，API tools 就只能 4 个
- 不出现 `[Tool None completed]`
- 不出现 gap marker user message
- history 不超过私聊时间窗口
- 当前 user_input 是最后一个真实 user message

## 5.2 群聊 request

必须满足：

- group behavior 只出现一次
- group context rules 只出现一次
- 不注入几天前无关历史
- 当前 `<user_input>` 只出现一次
- sticker_search 工具结果后能自动或稳定调用 reply
- 不出现 `skill`，除非显式允许
- memory_read/write 不允许时不出现在 tools_schema
- `ToolPolicy`、prompt 工具说明、API tools 三者一致

## 5.3 LLM API 日志

必须能一眼看到：

- 每条 system message 来源
- 每条 system message token/字符数
- 哪些段落重复
- 哪些工具被策略允许
- 哪些工具实际发送
- 哪些工具被 prompt 提到但没有发送
- 是否存在内部消息污染
- history 是否过期

---

# 6. 推荐提交顺序

## Commit 1：Request provenance + lint

```text
feat(trace): record message sources and request lint flags
```

内容：

- MessageBlock source
- LLMApiRequestLog 增加 message_sources_json / request_lint_json
- LLM API 日志展示 lint

## Commit 2：Hard filter tools schema

```text
fix(tools): hard filter actual tools schema by final policy
```

内容：

- final_allowed_tools
- API tools 硬裁剪
- skill/subagent/memory 过滤
- actual_sent_tools_json

## Commit 3：Disable KT framework tool docs

```text
fix(prompt): remove unfiltered KT auto tool documentation
```

内容：

- 关闭或清理 Available Functions / Skills / Tool Usage
- 用 final tools 生成极简说明

## Commit 4：Split legacy prompt

```text
refactor(prompt): split common/private/group prompt fragments
```

内容：

- 精简 prompt.md
- 私聊不再含群聊大段规则
- 工具说明交给 schema

## Commit 5：Fix history injection

```text
fix(context): enforce age window and remove user-role gap markers
```

内容：

- created_at 时间窗口过滤
- gap marker 不再 role=user
- internal kind 过滤
- 长 prompt-like 历史摘要化

## Commit 6：Fix tool-loop internal messages

```text
fix(agent): strip internal tool completion messages from model context
```

内容：

- 删除 `[Tool None completed]`
- tool result role 规范
- retry correction 不作为 user message

## Commit 7：Auto close common tool results

```text
feat(reply): auto close sticker/report tool results with reply
```

内容：

- sticker_search 返回 reply_token 后自动 reply
- report tools 成功后停止

## Commit 8：Request preview page

```text
feat(admin): add final request preview with sources and tools
```

内容：

- WebUI 预检最终 messages/tools/lint

---

# 7. 给开发 agent 的压缩任务

```md
你需要修复 Nanobot 模型请求组装混乱问题。

请以真实 LLM API request 为验收对象，而不是只看 WebUI 配置。

当前问题：

1. legacy prompt、bridge 动态 system、KT 自动工具文档、history、tools schema 多套来源混在一起。
2. prompt 中重复工具说明，API tools schema 又有参数说明。
3. ToolPolicy 只是软约束，实际 API tools 没严格跟它一致。
4. skill/memory_read/memory_write/subagents 绕过 ToolPolicy。
5. 私聊中出现群聊规则。
6. 群聊规则重复注入。
7. history 没按时间窗口裁剪，注入了过旧内容。
8. gap marker 被作为 user role 注入。
9. [Tool None completed] 这类内部控制消息进入 user role。
10. reply retry prompt 被作为 user message 追加，污染上下文。

目标：

1. 建立 ConversationAssembler，所有 messages 统一组装。
2. 每段消息带 source/key/path/sha256/order。
3. LLM API 日志记录 message_sources_json 和 request_lint_json。
4. final_allowed_tools 是唯一真相。
5. API tools_schema 必须硬裁剪到 final_allowed_tools。
6. prompt 中不得出现未发送工具的说明。
7. 关闭或清理 KT 自动 Available Functions / Skills / Tool Usage 英文说明。
8. 精简 legacy prompt，拆 common/private/group。
9. build_session_memory 必须按时间窗口过滤。
10. gap marker 不得作为 user role 注入。
11. internal tool completion/retry correction 不得污染 messages。
12. WebUI 显示配置启用、本轮允许、实际发送三层工具状态。

验收：

- 私聊 request 无群聊规则重复。
- 群聊 request 无重复 group rules。
- 不出现 Available Functions / Skills / Tool Usage / Background Execution。
- 不出现 [Tool None completed]。
- ToolPolicy enabled 与 API tools 完全一致。
- disabled tools 不出现在 prompt 文本。
- history 不超过配置时间窗口。
- 所有 system message 在 WebUI 可追踪来源。
```

---

# 8. ToolPolicy 是否还需要存在

结论：`tool_policy` 不应该再作为权限控制机制存在。

真正的权限控制必须是：

```text
final_allowed_tools -> 动态裁剪 API tools_schema
```

也就是说：

- 允许的工具才出现在 `tools` 数组里
- 禁用的工具不出现在 `tools` 数组里
- 禁用的工具不出现在 prompt 工具说明里
- 禁用的工具也不能通过 `skill` / sub-agent 间接调用

`tool_policy` 如果保留，只能作为审计字段或 WebUI 解释字段。

## 8.1 推荐改名

把 `tool_policy` 从 prompt 概念中移除，改成内部字段：

```text
tool_mode: full | limited | none | custom
```

它只参与计算：

```text
tool_mode + WebUI 工具配置 + chat_type 强制规则 -> final_allowed_tools
```

最终发送给模型的不是 `tool_policy` 文本，而是被硬裁剪后的 `tools_schema`。

## 8.2 Prompt 中不再写大段 ToolPolicy

删除这种 system 注入：

```text
[ToolPolicy]
本轮可调用工具...
已禁用工具...
规则...
```

最多保留极短契约：

```text
本轮可用工具已由 tools_schema 提供。
需要发消息时调用 reply。
不回复时调用 no_reply。
```

甚至如果 `reply/no_reply` schema 足够清晰，这段也可以不要。

## 8.3 WebUI 里显示三层状态

WebUI 仍然可以解释工具为什么没发出：

- 配置启用
- 本轮模式允许
- 最终已发送

但这只是给人看的，不再作为 prompt 软约束。

---

# 9. Expression / Jargon 学习污染修复

当前 `core/expression_learner.py` 只扫描 `ChatLog.role == "ambient"`，理论上不会直接学习 assistant 的回复。

但真实运行里仍可能污染：

1. 客户端或中间层把工具错误、内部提示、bot 输出误写成 ambient。
2. 群消息 content 里包含 `[Tool ...]`、`tool_error`、`Traceback`、`reply/no_reply` 等内部词。
3. 学习器只过滤 `no_learn`，没有过滤 system/internal/tool_error/prompt-like 内容。
4. `jargon` 定义句式太宽，遇到“xxx = error”或“tool_error就是...”这类日志文本会被学成黑话。
5. expression 学习只要求重复和多 sender，如果多人讨论报错，也会把错误词当群表达。

## 9.1 学习源硬过滤

新增函数：

```python
def should_learn_from_chatlog(row: ChatLog) -> tuple[bool, str]:
```

过滤条件：

- role 不是 ambient -> false
- meta_json.no_learn -> false
- meta_json.message_type 不是 group_message -> false
- meta_json.internal/control/no_send/tool_error 为 true -> false
- content 为空或过长 -> false
- content 包含内部标记 -> false
- sender_name 是 nanobot/bot/self -> false
- directed.bot/system 生成消息 -> false

内部标记包括：

```text
[Tool
Tool completed
tool_error
Traceback
Exception
HTTPError
response_json
request_json
AgentRun
PromptRender
ToolPolicy
Available Functions
Available Sub-Agents
Background Execution
reply/no_reply
<runtime_context>
<user_input>
<history_context>
<persona_reference>
[CQ:image,file=http://127.0.0.1
```

## 9.2 学习内容清洗

新增：

```python
def sanitize_learnable_group_text(text: str) -> str
```

规则：

- 去掉 CQ 码
- 去掉 URL
- 去掉日志时间戳
- 去掉 markdown/code block
- 去掉 JSON 大对象
- 去掉 stack trace
- 去掉系统标签
- 过长文本直接不学习

## 9.3 Jargon 候选更严格

现有定义句式：

```python
(.{1,10})就是(.{1,30})
(.{1,10})的意思是(.{1,30})
什么叫(.{1,10}).{0,4}就是(.{1,30})
(.{1,10})[=＝](.{1,20})
```

需要增加限制：

- term 不能包含英文日志词、工具名、系统标签
- meaning 不能包含 error/trace/json/http/工具说明
- term 不能是 `tool`、`reply`、`no_reply`、`sql_analysis`、`group_analysis`、`memory_read` 等工具名
- meaning 不能是 URL / CQ 码 / JSON
- 候选默认 `candidate`，必须人工 checked 或多轮自然出现后才能 active

## 9.4 Expression 候选更严格

表达学习不能学习：

- 工具名
- 报错词
- 代码片段
- 系统提示
- bot 回复内容
- 表情包 URL
- 纯日志词

增加 `BAD_LEARN_TERMS`：

```text
tool_error
reply
no_reply
ToolPolicy
AvailableFunctions
memory_read
memory_write
sql_analysis
group_analysis
python_sandbox
image_summary
sticker_search
Traceback
Exception
HTTPError
request_json
response_json
```

## 9.5 给 ChatLog 写入方补 meta

所有内部/工具/错误消息写 ChatLog 时都必须写：

```json
{
  "no_learn": true,
  "no_context": true,
  "internal": true,
  "source": "tool_error|reply_contract_retry|agent_trace|no_send"
}
```

包括：

- `_log_group_no_reply`
- tool error
- reply contract retry
- LLM parser error
- sticker auto describe error
- system control/timer

## 9.6 学习器日志和 WebUI 审核

每轮学习输出：

```text
scanned
accepted_rows
rejected_rows
reject_reasons
expression_candidates
jargon_candidates
```

WebUI 显示候选来源 examples 和 reject reason，方便发现污染。

---

# 10. 最终优先级

最高优先级不是继续改人设 prompt，而是先修请求组装管线。

顺序：

1. 请求来源追踪和 lint
2. tools_schema 硬裁剪
3. 将 `tool_policy` 降级为内部审计字段，不再作为 prompt 软约束
4. 禁止 KT 自动工具文档污染
5. history 时间窗口和内部消息过滤
6. expression/jargon 学习源过滤和 no_learn 标记
7. 精简 prompt
8. reply 工具闭环

否则你会继续遇到：

- WebUI 显示启用，但实际没发出去
- ToolPolicy 说禁用，但模型还能看到
- Prompt 里说一套，tools schema 又是另一套
- 历史里塞进奇怪的系统提示
- tool_error 被学习成群黑话
- 模型不调用 reply，或者调用错工具
