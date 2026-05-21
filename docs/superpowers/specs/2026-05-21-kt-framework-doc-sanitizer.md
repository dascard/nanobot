# KT 自动工具文档清理设计

## 背景

`docs/goal.md` Phase 3 要求最终模型请求中不再出现 KT 框架自动生成的英文工具说明，包括：

- `Available Sub-Agents`
- `Available Functions`
- `Skills`
- `Tool Usage`
- `Background Execution`

源码定位显示这些内容来自 `vendor/KohakuTerrarium` 的 prompt aggregator、plugins、subagent manager 和 framework hints。为了避免修改 vendor，本阶段在主仓真实请求出口前清理 system messages。

## 目标

- 新增 `core/llm_request_sanitizer.py`。
- 只清理 `role == "system"` 的 KT 框架工具说明段落，避免误删用户引用。
- `NewAPIClient._build_payload()` 在返回 payload 前清理 messages。
- `core/llm_sdk_tracing.py` 在记录和调用 OpenAI SDK 原始 `create()` 前清理 kwargs messages。
- 清理后 request log 与真实发送内容保持一致。

## 清理范围

移除以下 Markdown 段落：

- `## Available Sub-Agents`
- `## Available Functions`
- `## Available Tools`
- `## Skills`
- `## Tool Usage`
- `### Background Execution`

同时移除残留单行：

- `Use the info tool...`
- `Sub-agents are called as tools...`
- native tool calling 英文说明
- 只能调用 Available Functions 的英文限制

## 非目标

- 不修改 `vendor/KohakuTerrarium`。
- 不删除业务 prompt 中的中文工具策略。
- 不清理 user/tool/assistant role 文本。

## 验证

- sanitizer 单测验证保留业务 system 文本、删除 KT 段落、不改 user 文本。
- NewAPI payload 测试验证直接 HTTP 出口已清理。
- OpenAI SDK tracer 测试验证真实 SDK 调用和 request log 都使用清理后的 messages。
- final tools / request tracing / linter / KT / history 回归。
