---
name: Workspace 读取工具
version: 1
kind: tool
tool_name: workspace_read
description: workspace_read 的有界读取规则。
---
## workspace_read 工具边界

按字节偏移有界读取当前 Workspace 的文本文件。

- 只传相对文件路径；使用 `offset`、`limit` 和返回的 `eof` 分页读取。
- 二进制文件只返回元数据，不要反复扩大 limit；需要处理时用 `sandbox_exec` 在本地读取。
- 大文件优先让容器生成摘要或派生小文件，不要把完整正文送进模型上下文。
