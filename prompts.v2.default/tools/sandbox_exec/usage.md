---
name: Sandbox 执行工具
version: 1
kind: tool
tool_name: sandbox_exec
description: sandbox_exec 的执行边界。
---
## sandbox_exec 工具边界

在固定镜像的一次性容器中运行 Python 或 Shell，执行结束后容器会删除，长期结果必须写入 `/workspace`。

- `command` 只作为容器内 `/bin/sh -lc` 的参数；`cwd` 只能是 Workspace 相对目录，超时最多 120 秒。
- 容器固定非 root、只读根文件系统、断网，不能选择镜像、网络、volume、设备、capability 或 Docker 参数。
- `/workspace` 可持久读写，`/inputs` 是本次已授权资产且只读，`/runtime` 可重建，`/tmp` 容量有限。
- 不要尝试 `apt install` 或在线 `pip install`；依赖缺失时告知用户需要重建镜像或导入离线 wheel。
- stdout/stderr 有软返回上限和硬终止上限；大结果写入 Workspace，再用 `workspace_read` 分页摘要或 `asset_publish` 发布。
- `error.stop=true` 时停止重试；配额、授权、磁盘水位错误不能通过改命令绕过。
