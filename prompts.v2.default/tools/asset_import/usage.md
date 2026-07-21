---
name: Asset 导入工具
version: 1
kind: tool
tool_name: asset_import
description: asset_import 的授权链接规则。
---
## asset_import 工具边界

把当前请求附件引用或已经授权的 `asset://sha256/<hash>` 链接到当前 Workspace。

- 不接受 URL、宿主路径或任意文件路径；知道 SHA-256 不代表具有读取权限。
- `logical_name` 是可选相对逻辑名，不能覆盖同名的其他资产。
- 导入只建立授权链接，不修改不可变资产内容；资产在 Sandbox 中只读出现在 `/inputs`。
