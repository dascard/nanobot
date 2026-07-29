---
name: Workspace 读取工具
version: 2
kind: tool
tool_name: workspace_read
description: workspace_read 的按行读取与分页规则。
---
## workspace_read 工具边界

按行有界读取当前 Workspace 的 UTF-8 文本文件，并返回稳定行号。

- `offset` 是从 0 开始的行偏移，`limit` 是读取行数；继续读取时使用返回的
  `next_offset`，不要把它当作字节位置。
- `path` 相对可选的 `cwd` 解析；只能使用 Workspace 相对路径，不能传绝对路径、
  `..`、符号链接或宿主路径。
- 返回 `total_lines`、`eof`、行截断与输出截断信息；达到扫描预算时
  `total_lines` 可以为空，应缩小目标文件或用 `sandbox_exec` 生成派生小文件。
- 二进制或非 UTF-8 文件只返回元数据，不要反复扩大 `limit`。
- 大文件优先分段读取或在容器中生成摘要，不要把完整正文送入模型上下文。
