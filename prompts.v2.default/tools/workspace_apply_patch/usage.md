---
name: Workspace 补丁工具
version: 2
kind: tool
tool_name: workspace_apply_patch
description: workspace_apply_patch 的退役兼容说明。
---
## workspace_apply_patch 工具边界

这是已退役的模型工具名。新的编辑请求统一使用 `workspace_edit`；运行时对旧名
执行 fail-closed 拒绝，避免新旧契约同时暴露。

- 内部迁移或历史 Trace 可以识别该名称，但不得重新加入 ToolPlan。
