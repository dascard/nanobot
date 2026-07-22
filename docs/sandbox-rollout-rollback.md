# Nanobot Sandbox 灰度与回滚手册

## 1. 身份与授权边界

首期只面向受信网关下的私聊 session。共享 Bearer Token 的持有者仍可能声明 QQ 身份，因此该方案不能直接视为公网多租户身份系统。

Sandbox 唯一授权键是 canonical chat_stream_id：

~~~text
platform:encoded_external_session_id:chat_type
~~~

user_id、private_superuser 和通用 ToolOverride 均不能授予 Sandbox 能力。七个 Sandbox 工具已经退出通用工具配置链；旧 Sandbox ToolOverride 会由幂等数据迁移删除。

同一人的不同 session 默认使用不同 Workspace。首期不自动合并；未来共享或合并必须通过显式绑定、冲突检查和独立审计实现。

## 2. 灰度前置条件

以下任一项未满足都不得开放：

- 全量 pytest 为 0 failures；
- WebUI lint 和生产 build 通过；
- 真实 Docker 隔离矩阵全部通过，而不是被跳过；
- 独立数据盘、project quota、总容量和磁盘水位门禁通过；
- Docker builtin seccomp 与精确 nanobot-sandbox AppArmor profile 生效；
- Nanobot Server、Worker 和 Sandbox 容器均看不到 Docker Socket；
- 固定镜像 digest/IMAGE ID allowlist 已核对；
- 普通与管理 Token 已分离并符合权限要求；
- sandboxd operation runner 的重试、租约和重启恢复已验证；
- 备份、恢复和 kill switch 已演练；
- 旧 bash/read/write/edit/grep/glob 与通用宿主写入口继续硬禁用；
- Trace 中没有文件正文、完整命令、stdout/stderr、transport token 或宿主路径。

## 3. 灰度顺序

1. 部署数据库迁移、Nanobot 客户端、Web 管理页和 sandboxd。
2. 保持宿主硬上限、sandbox.enabled、sandbox.exec_enabled 全部关闭。
3. 在 feature off 状态完成 UDS health/ready、镜像、AppArmor、磁盘和真实 Docker Smoke。
4. 由 root 设置 infrastructure_enable_allowed=true；业务开关仍保持关闭。
5. 在 Web 中选择一个真实私聊 canonical session，授予 Workspace 能力并设置配额。
6. 等待 set_access operation 为 succeeded，quota 为 applied，desired 与 applied 一致。
7. 只打开 sandbox.enabled，验证 workspace_list/read/search/write、容量和 Trace。
8. 把同一 session 升级为 Assets；等待 operation 成功后验证上传、授权、Range 下载和 session 绑定 Token。
9. 把同一 session 升级为 Exec；确认后再单独打开 sandbox.exec_enabled，验证超时、OOM、输出限制和容器回收。
10. 观察至少一个完整使用周期，再逐个增加私聊 session。
11. 群聊保持固定关闭，直到群 Workspace、身份、成员权限和硬配额经过独立设计与验收。

HTTP 202 只代表操作已持久化入队，不代表宿主 Workspace 或 project quota 已生效。每一步都必须以 operation 和 applied 状态为准。

## 4. 配额灰度

配额可在 Web 中修改：

- 扩容仍受 Sandbox 总预算和磁盘水位约束。
- 缩容不得低于 used_bytes。
- 修改后必须等待新 generation 应用完成。
- 失败时权限失败关闭，不允许继续沿用 desired/applied 不一致的 Workspace。
- project ID 由数据库自动分配，运维不得手填或复用。

多 session 共享同一 Workspace 尚未开放；不要通过直接改数据库模拟共享。

## 5. 立即回滚

1. 调用 Web kill switch 或 POST /api/v1/admin/sandbox/kill-switch，关闭 sandbox.enabled 与 sandbox.exec_enabled。
2. 如需撤销单个会话，在 Web 把该 canonical session 能力设为 off；这不会删除数据。
3. 取消必要的活动运行；sandboxd 只能处理名称前缀和双标签都匹配的自身容器。
4. 将宿主 infrastructure_enable_allowed 恢复为 false，作为第二道上限。
5. 回滚 Nanobot Runtime 和 sandboxd 到最近一个已验证版本。
6. 保留当前与最近一个已验证 Sandbox 镜像。
7. 保留新增数据库表、Workspace、Asset、sandbox_access_grants、workspace_quota_bindings、sandbox_admin_operations 与备份。
8. 保留旧 KT 文件/Bash 工具的硬禁用，避免应用回滚形成绕过。

回滚不得：

- 删除 ToolOverride 以外的通用工具配置；
- 删除数据库新表或降级 schema；
- 删除 `/srv/nanobot`、Workspace、Asset 或 project quota 元数据；
- 执行全局 Docker prune、volume prune 或 compose down -v；
- 临时恢复宿主 bash/read/write 作为降级路径。

## 6. 故障分流

- runtime_unavailable：检查 UDS、sandboxd、Docker、管理 Token、固定镜像和 AppArmor；不降级到宿主命令。
- disk_pressure：拒绝新写入和执行，保留已有数据；检查 df、inode、总配额和水位。
- workspace_quota_exceeded：停止模型重试，核对 used、desired、applied 和宿主 project quota。
- operation_superseded：说明更高 generation/version 的管理操作已取代旧请求，刷新页面确认最终期望状态。
- operation retry_wait：检查 next_attempt_at 和 error_code，不重复手工提交同一业务请求。
- execution_timeout、process_oom_killed、output_limit_exceeded：检查脱敏运行账本与 Docker inspect，不提高模型可申请上限。
- asset_not_authorized：按 canonical session 和 Workspace 链接排查，不能仅凭 hash 放行。
- 下载失败：保持短期 HMAC、session 收件人和 Range 校验，不改成公开静态 URL 或宿主路径。

## 7. 回滚验收

回滚完成后至少确认：

- 宿主硬上限和两个业务开关均为 false；
- 七个 Sandbox 工具不进入任何未授权 session 的 wire schema；
- active/running Sandbox 运行已处置；
- 非 Nanobot 容器、镜像和 volume 未变化；
- Workspace、Asset、grant、quota binding、operation 和审计记录仍存在；
- 主聊天、固定 Worker 和非 Sandbox 工具继续健康；
- Trace 与 Web 页面未泄漏命令、文件正文、Token 或宿主路径。
