---
name: Session Plan 写入工具
version: 1
kind: tool
tool_name: session_plan_write
description: 在 Plan Mode 中创建新的不可变计划版本。
---
## session_plan_write 工具边界

只在服务端确认当前 Session Goal 处于可写 Plan Mode 时可见和可执行。

- 每次调用提交完整 Markdown 计划，并创建不可变新版本，不做宿主文件或工作区写入。
- `expected_version` 必须使用当前 Goal 版本；冲突时先重新读取，不能覆盖并发变化。
- 本工具不能批准计划、退出 Plan Mode 或开启实施；这些动作只能由服务端控制面完成。
