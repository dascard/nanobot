---
name: Sandbox 执行工具
version: 2
kind: tool
tool_name: sandbox_exec
description: sandbox_exec 的执行边界。
---
## sandbox_exec 工具边界

在当前授权 Profile 的固定镜像中运行命令。Profile、镜像、网络和资源限制全部由服务端决定。

- `command`、`cwd`、`yield_time_ms` 和 `timeout_seconds` 之外不接受身份、Docker 或宿主参数；`cwd` 只能是 Workspace 相对目录。
- 静态 schema 的 3600 秒只是协议绝对上限；本轮实际时长、网络、工具链、长进程和 stdin 能力以工具 schema 中附带的当前 Sandbox Profile 说明为准。
- 每次调用都会启动新的登录 shell，前一条命令中的 `cd`、`export`、alias 或环境激活不会自动延续。
- 长命令和 dev server 使用前台命令加 `yield_time_ms`；返回 `running` 后保存 `process_id` 并调用 `sandbox_poll`，不要使用 `cmd &`。
- 容器固定非 root、只读根文件系统，不能选择镜像、网络、volume、设备、capability 或 Docker 参数。
- `/workspace` 可持久读写，`/inputs` 是已授权资产且只读，`/runtime` 保存可重建环境，`/tmp` 容量有限。
- stdout/stderr 有软返回上限和硬终止上限；大结果写入 Workspace，再用 `workspace_read` 分页摘要或 `asset_publish` 发布。
- `error.stop=true` 时停止重试；配额、授权、磁盘水位错误不能通过改命令绕过。
