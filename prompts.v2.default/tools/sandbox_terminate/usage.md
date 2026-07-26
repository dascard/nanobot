---
name: Sandbox 终止工具
version: 1
kind: tool
tool_name: sandbox_terminate
description: sandbox_terminate 的 Lease 级终止规则。
---
## sandbox_terminate 工具边界

用本会话的活动 `process_id` 请求终止；v1 的强制终止边界是整个 Lease，不是单个 Docker Exec。

- 目标句柄只用于授权和定位，调用会终止同一 Lease 内全部活动进程。
- 回收后 `/workspace` 与 `/runtime` 保留，`/tmp` 和该 Lease 的全部旧 `process_id` 失效。
- 下一次已授权 `sandbox_exec` 会按最新 Grant 和 Profile 透明创建新 Lease。
- 对已经回收的目标重试是幂等查询，不会作用于后来创建的新 Lease。
