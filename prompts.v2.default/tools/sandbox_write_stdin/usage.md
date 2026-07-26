---
name: Sandbox stdin 工具
version: 1
kind: tool
tool_name: sandbox_write_stdin
description: sandbox_write_stdin 的标准输入规则。
---
## sandbox_write_stdin 工具边界

向当前授权 Profile 明确允许 stdin 的活动 Lease 进程写入字符。

- `process_id` 必须来自本会话当前有效 Lease；旧 Lease、其他会话或已终态句柄均不可访问。
- `chars` 会原样写入，不自动追加换行；交互式程序需要回车时显式包含 `\n`。
- stdin 内容不写入持久 Trace。工具返回成功只确认写入，不代表目标程序已经处理。
