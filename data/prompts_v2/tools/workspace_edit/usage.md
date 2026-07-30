---
name: Workspace 编辑工具
version: 1
kind: tool
tool_name: workspace_edit
description: workspace_edit 的精确替换与 unified diff 规则。
---
## workspace_edit 工具边界

用于原子编辑当前授权 Workspace 中的 UTF-8 文本文件。

- `operations` 可包含多个精确替换，或包含完整文件头的多文件 unified diff。
- 精确替换必须提供 `path`、非空 `old` 和 `new`。`old` 必须精确命中；命中 0
  处会失败，命中多处时必须显式设置 `replace_all=true`。
- diff 必须包含可校验的 `---`/`+++` 或 `diff --git` 文件头；当前不支持创建、
  删除或重命名文件。
- 所有路径都相对可选 `cwd` 解析，禁止绝对路径、`..`、符号链接和宿主路径。
- 整批操作先完成路径、内容、配额和磁盘水位校验，再进入写阶段；任一项失败时
  不把部分写入当作成功。
- 返回每个文件的旧/新 SHA-256、替换次数、diff 行统计及恢复状态。失败后先用
  `workspace_read` 读取最新内容，再构造新的精确操作。
- 不要把文件正文或 diff 复制到最终用户回复中；只概述实际修改和验证结果。
