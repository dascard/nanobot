---
name: Workspace 列表工具
version: 2
kind: tool
tool_name: workspace_list
description: workspace_list 的内部兼容说明。
---
## workspace_list 工具边界

这是旧版内部兼容入口，不再注册给模型。目录树和文件查找统一使用
`workspace_search` 的 `tree` / `files` 模式。

- 兼容调用仍只接受 Workspace 相对路径，不允许传 owner、Workspace UUID 或
  宿主路径。
