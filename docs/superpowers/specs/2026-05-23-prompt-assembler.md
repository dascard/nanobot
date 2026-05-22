# Nanobot 提示词编排重构设计

## 背景

当前主回复链路同时存在三类 prompt 来源：

- legacy fragment / `prompt.md` 构建产物
- bridge 中手工追加的 persona、history、group/private fragment、runtime tool
- PromptManager 的 `group_chat` / `private_chat` managed 模板

这会导致 Web 有效预览、真实 bridge 请求和 Reply Eval 可能看到不同 messages；managed 模板变厚后，还会出现 `user_input`、画像、历史和 runtime tool 被重复注入。

## 目标

新增 `core/prompt_assembler.py`，让 `PromptAssembler` 成为主回复请求的唯一编排入口。它负责把结构化上下文、PromptManager 模板、legacy rollback prompt、运行时工具说明和最终 user event 编译成 OpenAI-compatible `messages`。

## 编排模式

- `managed`：真实发送 `PromptManager` 模板作为第一条 system 消息，并由 assembler 追加一次 identity、runtime、persona、conversation、tool 和当前 user event。managed 模板只承载规则，不直接内嵌这些动态变量，避免重复。
- `legacy`：rollback 模式，读取 legacy runtime/default prompt，并按旧行为追加 group/private fragment 与运行时上下文。
- `shadow`：真实发送 legacy messages，同时渲染 managed messages、记录 render log 和 diff，用于上线前对比。

## PromptBuildResult

`PromptBuildResult` 需要包含：

- `messages`：真实最终 messages
- `request_json`：预览和日志可直接对比的请求体骨架
- `managed_messages` / `legacy_messages`
- `diff`：legacy vs managed 的消息数量、sha 和首条 system 摘要
- `prompt_key`、`prompt_mode`、`prompt_source`、`prompt_runtime_path`、`prompt_default_path`、`prompt_sha256`
- `warnings`
- `tool_schemas`
- `pre_event_messages` 和 `event_content`，供 KT bridge 注入 conversation 与 event

## 真实运行与预览一致性

bridge 和 `/prompt/effective-preview` 必须用同一个 `PromptAssembler.build()`。预览只负责补齐数据库上下文、工具 schema 和表单字段，不再自己拼 messages。

真实 bridge 中清空 KT conversation 后，只写入 `result.pre_event_messages`，当前输入只从 `result.event_content` 创建 event。这样预览 `request_json.messages` 和 OpenAI SDK tracer 记录的真实 `request_json.messages` 在同样输入下保持一致。

## 群聊上下文

群聊真实入口和预览入口统一调用 `build_chat_context()`。`group_recent_context` 不再作为真实链路注入块出现；旧 `build_group_recent_context()` 保留为兼容测试/手工排查接口，但标记 deprecated，不能被 bridge 或 preview 调用。

## Reply Eval

`baseline`、`prompt_only`、`code_retry` 明确分离：

- `baseline`：legacy prompt，关闭 reply contract retry
- `prompt_only`：managed prompt，关闭 retry
- `code_retry`：legacy prompt，开启 retry

每条结果保存 `prompt_sha256`、`trace_id`、`agent_run_id`，批次 summary 统计 prompt sha，避免 prompt_only 只是关闭 retry 的假实验。

## 模板规则

`prompts.default/group_chat.md` 合并群聊身份、输出契约、安全规则、上下文权限、聊天风格、工具纪律和群聊规则，不包含私聊行为章节。

`prompts.default/private_chat.md` 合并通用规则和私聊行为，不包含群聊行为、群聊发言时机和群聊上下文专属规则。

两者都必须保留 `reply(content)` / `no_reply(reason)` 输出契约。

## Legacy 迁移

`core/legacy_prompt_runtime.py` 保留为 rollback/deprecated。新增迁移脚本把系统 fragment 的核心内容同步到 managed 默认模板，避免以后继续修改旧 fragment 却忘记 managed 模板。
