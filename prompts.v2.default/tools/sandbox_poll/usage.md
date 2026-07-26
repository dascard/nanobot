---
name: Sandbox 进程轮询工具
version: 1
kind: tool
tool_name: sandbox_poll
description: sandbox_poll 的进程状态与增量输出规则。
---
## sandbox_poll 工具边界

使用 `sandbox_exec` 返回的 `process_id` 读取当前 Lease 进程状态和增量输出。

- 首次可省略 `cursor`；后续传回上一响应的 `next_cursor`，避免重复读取。
- 只可访问当前 canonical session、Grant、Workspace、Profile 和 Lease 全部匹配的进程句柄。
- 返回的 `active_processes` 只用于保存本 Lease 的活动句柄，不代表 detached 进程获得支持。
- 进程进入终态后不要继续轮询；需要停止运行中的进程时调用 `sandbox_terminate`。
