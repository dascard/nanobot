# LLM 请求 lint 与 provenance 设计

## 背景

`docs/goal.md` 要求真实模型请求发出前能够看到最终 messages、tools_schema 与 ToolPolicy 的一致性问题。现有 `LLMApiRequestLog` 只保存完整 request/response，缺少可直接排查的结构化 lint 结果和 message source 信息。

## 目标

- 在真实 `/chat/completions` 出口记录实际发送的工具名。
- 从最终 `messages` 推断每条消息来源，至少识别 ToolPolicy、KT 自动工具文档、内部工具完成消息、reply retry 修正提示和历史 gap marker。
- 非阻塞 lint：只记录问题，不阻断请求。
- WebUI LLM API 日志详情可直接查看 Request Lint、Message Sources、Actual Sent Tools、Policy Tools。

## 日志字段

`LLMApiRequestLog` 新增：

- `message_sources_json`
- `request_lint_json`
- `actual_sent_tools_json`
- `policy_enabled_tools_json`
- `policy_disabled_tools_json`
- `framework_injected_tools_json`

旧数据库通过 `init_db()` 热迁移补列。

## lint 规则

第一阶段覆盖：

- system exact duplicate
- system heading duplicate
- unknown system source
- ToolPolicy enabled 与实际发送 tools 不一致
- disabled tool 仍出现在 tools_schema
- disabled tool 仍出现在非 ToolPolicy prompt 文本
- KT 自动工具文档进入 prompt
- `[Tool None completed]` 以 user role 注入
- reply contract retry 以 user role 注入
- history gap marker 以 user role 注入

## 非目标

- 本阶段不硬裁剪 tools_schema。
- 本阶段不重写 ConversationAssembler。
- 本阶段不阻断请求，只提供证据。

## 验证

- linter 单测验证工具提取、ToolPolicy 解析和 P0/P1/P2 issue。
- tracer 单测验证新增字段落库。
- LLM tracing / prompt admin / reply admin 回归。
- WebUI build 验证前端展示可打包。
