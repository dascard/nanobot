---
name: Workspace 写入工具
version: 2
kind: tool
tool_name: workspace_write
description: workspace_write 的小文本原子写入规则。
---
## workspace_write 工具边界

向当前持久 Workspace 原子写入小段 UTF-8 文本。

- `path` 相对可选的 `cwd` 解析，必须是 Workspace 相对文件路径；禁止绝对
  路径、`..`、符号链接和宿主路径。
- 新建文件使用 `overwrite=false`，确认需要整文件替换现有文件时才设为 true；
  修改既有文件通常优先使用 `workspace_edit`。
- 单次仅适合小文本；大文件、二进制和附件使用资产上传或 `sandbox_exec` 在 Workspace 内生成。
- 工具不会删除文件，也不能写 FIFO、socket、设备或符号链接目标。
- 配额或磁盘水位返回 `stop=true` 时停止重试，不要通过拆分写入规避限制。
