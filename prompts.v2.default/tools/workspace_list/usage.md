---
name: Workspace 列表工具
version: 1
kind: tool
tool_name: workspace_list
description: workspace_list 的分页与路径边界。
---
## workspace_list 工具边界

分页列出当前身份的持久 Workspace，只返回相对路径、类型、大小和修改时间。

- `path` 为空表示根目录，只能传相对目录；模型不能传 owner、Workspace UUID 或宿主路径。
- 使用返回的 `next_cursor` 继续翻页，不要用猜测的游标。
- 列表不跟随符号链接；看不到的路径不能据此判断其他用户是否存在同名文件。
