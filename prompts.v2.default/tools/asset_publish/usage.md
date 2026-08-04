---
name: Asset 发布工具
version: 1
kind: tool
tool_name: asset_publish
description: asset_publish 的不可变发布规则。
---
## asset_publish 工具边界

把当前 Workspace 中的普通文件发布为按 SHA-256 寻址的不可变资产。

- `path` 只能是当前 Workspace 相对普通文件；符号链接、FIFO、socket 和设备文件会被拒绝。
- 相同内容会物理去重，但访问权限仍由当前 Workspace 的授权链接决定。
- 每次内容变化都会生成新的不可变版本；返回的 `artifact://...` 和 `[artifact:<id>]` 是 owner-scoped 稳定引用。
- 需要把资产发给当前用户时，把返回的 `reply_token` 原样放进最终 `reply(content)`；系统只在最终发送出口校验 ACL 并签发短期下载链接。
- `asset://sha256/...` 只是内容寻址引用，不要把它当成跨会话授权或公开下载凭据，也不要自行拼 URL 或 CQ 文件码。
- 需要继续修改时编辑 Workspace 原文件并重新发布，不能修改已发布 Asset。
