---
name: Workspace 补丁工具
version: 1
kind: tool
tool_name: workspace_apply_patch
description: workspace_apply_patch 的单文件统一 diff 规则。
---
## workspace_apply_patch 工具边界

对当前 Workspace 中一个既有 UTF-8 文本文件原子应用严格 unified diff。

- `path` 必须是 Workspace 相对路径，禁止绝对路径、反斜杠和 `..`；补丁不能修改其他文件。
- `patch` 可包含匹配同一路径的 `---` / `+++` 文件头，也可只包含 `@@` hunks。
- 应用器不做模糊匹配；上下文不一致时整个操作失败且文件保持不变。失败后先用 `workspace_read` 重新读取，再生成新补丁。
- 与 `workspace_write` 互斥，并受同一磁盘水位、单次大小和 Workspace 硬配额约束。
