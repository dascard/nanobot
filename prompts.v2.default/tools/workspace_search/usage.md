---
name: Workspace 搜索工具
version: 2
kind: tool
tool_name: workspace_search
description: workspace_search 的正则搜索、文件查找与目录树规则。
---
## workspace_search 工具边界

在当前 Workspace 内执行有界内容搜索、文件查找或目录树浏览。

- `mode=content` 时 `pattern` 是正则表达式，可用 `ignore_case` 控制大小写；
  `glob` 可限制参与搜索的文件。
- `mode=files` 时 `pattern` 是文件名或相对路径 glob；`mode=tree` 用于浏览目录树。
- `path` 相对可选的 `cwd` 解析；禁止绝对路径、`..`、符号链接和宿主路径。
- 搜索遵守根 `.gitignore`，并固定跳过 `.git`、`node_modules`、虚拟环境、
  构建目录和缓存目录。
- 返回扫描文件数、扫描字节数、跳过数量、`truncation_reason` 和
  `next_cursor`。存在游标时可用完全相同的查询继续；否则应缩小 `path`、`glob`
  或 `max_depth`，不能把截断结果解释为“没有命中”。
- 正则匹配有超时，二进制、非 UTF-8 文件和符号链接不会被当作正文。
