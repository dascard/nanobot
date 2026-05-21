# 最终 tools_schema 硬裁剪设计

## 背景

`docs/goal.md` Phase 2 要求 `resolve_effective_tools()` 的结果成为真实 OpenAI-compatible `tools` 数组的硬权限边界。此前 bridge 只移除了 KT registry 中部分工具，无法覆盖 SDK 出口、`skill` wrapper、sub-agent 和其他框架自动注入的工具 schema。

## 目标

- 新增 `core/final_tools.py` 作为最终工具集的单一运行时上下文。
- bridge 在每轮 reply run 内解析 `FinalToolSet` 并设置 ContextVar。
- `clients/new_api_client.py` 在构建 payload 时按当前 `FinalToolSet` 裁剪 `tools`。
- `core/llm_sdk_tracing.py` 在调用 OpenAI SDK 原始 `create()` 之前裁剪 `kwargs["tools"]`，确保记录和真实发送一致。
- 如果全部工具都被裁掉，移除 `tools` 和 `tool_choice`，避免发送空 schema。

## 字段语义

`FinalToolSet` 包含：

- `allowed`：允许进入真实 API tools_schema 的工具名集合。
- `disabled`：禁用原因。
- `sent_tools`：期望发送工具名，供审计使用。
- `hidden_framework_tools`：需要被隐藏的框架工具，例如 `skill`。
- `enabled`：原始 ToolPolicy enabled map，供 bridge 生成短审计提示和恢复旧逻辑。

## 非目标

- 本阶段不清理 KT 自动英文工具文档文本。
- 本阶段不重写 ConversationAssembler。
- 本阶段不改变 WebUI 工具默认配置。

## 验证

- 单测验证未允许工具、`skill`、空工具集都会被正确处理。
- OpenAI SDK tracer 测试验证原始 SDK 调用和日志记录都只包含裁剪后的工具。
- NewAPIClient payload 测试验证直接 HTTP 出口也应用裁剪。
- KT/history/trace 相关回归验证 bridge 接入未破坏现有流程。
