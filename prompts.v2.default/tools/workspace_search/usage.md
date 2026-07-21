---
name: Workspace 搜索工具
version: 1
kind: tool
tool_name: workspace_search
description: workspace_search 的有界字面量搜索规则。
---
## workspace_search 工具边界

在当前 Workspace 内执行有界字面量搜索。

- `query` 按字面量匹配，不是任意正则；`path` 只能是相对目录，`glob` 只用于简单文件筛选。
- 返回命中行的短预览、扫描文件数和截断状态；结果截断时应缩小目录或 glob，而不是无限增大 limit。
- 二进制文件和符号链接不会被当作可搜索正文。
