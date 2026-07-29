# Nanobot Sandbox 灰度与回滚手册

## 1. 身份与授权边界

首期只面向受信网关下的私聊 session。共享 Bearer Token 的持有者仍可能声明 QQ 身份，因此该方案不能直接视为公网多租户身份系统。

Sandbox 唯一授权键是 canonical chat_stream_id：

~~~text
platform:encoded_external_session_id:chat_type
~~~

`user_id`、`private_superuser` 和通用 ToolOverride 均不能授予 Sandbox 能力。10 个模型可见 Sandbox 工具已经退出通用工具配置链；旧
`workspace_list`、`workspace_apply_patch` 只保留退役兼容标识，不进入 ToolPlan。
旧 Sandbox ToolOverride 会由幂等数据迁移删除。

同一人的不同 session 默认使用不同 Workspace。首期不自动合并；未来共享或合并必须通过显式绑定、冲突检查和独立审计实现。

## 2. 灰度前置条件

以下任一项未满足都不得开放：

- 全量 pytest 为 0 failures；
- WebUI lint 和生产 build 通过；
- 真实 Docker 隔离矩阵全部通过，而不是被跳过；
- 受控 XFS/ext4 数据文件系统、project quota、总容量和磁盘水位门禁通过；单盘方案必须有 `single_disk_logical_rollback_only` 风险标记；
- Docker builtin seccomp 与精确的 `nanobot-sandbox-restricted`、`nanobot-sandbox-developer` AppArmor profile 生效；
- Nanobot Server、Worker 和 Sandbox 容器均看不到 Docker Socket；
- Restricted、Developer、出口代理三个固定镜像的实际 IMAGE ID 与部署 manifest 完全一致；
- Server 与 sandboxd 读取同一份 `/run/nanobot-sandboxd/profile-manifest.json`，并匹配 `catalog_generation` 与完整 `policy_sha256`；
- 普通与管理 Token 已分离并符合权限要求；
- sandboxd operation runner 的重试、租约和重启恢复已验证；
- 备份、恢复和 kill switch 已演练；
- sandboxd 重启已验证为回收旧 Lease、保留 Workspace／Runtime，并由 Server 把相关 Run 收敛为 `controller_restarted`；
- `trusted_developer` 仍为 `grantable=false`，没有私有仓库凭据注入路径；
- Sandbox Agent 兼容性 Eval 已使用真实 Agent 事件 artifact 运行，静态 case 或合成单元测试不能代替生产体验证据；
- 旧 bash/read/write/edit/grep/glob 与通用宿主写入口继续硬禁用；
- Trace 中没有文件正文、完整命令、stdout/stderr、transport token 或宿主路径。

截至 2026-07-26，当前开发宿主缺少 AppArmor、`/srv/nanobot` 独立 project quota 数据盘和 quota 工具，且当前会话不是 root，因此生产灰度状态为 `BLOCKED`。代码与静态测试通过不能解除该阻断。

## 3. 灰度顺序

1. 部署数据库迁移、Nanobot 客户端、Web 管理页和 sandboxd。
2. 保持 `sandbox.enabled`、`sandbox.exec_enabled`、session 执行硬开关，以及 Server／sandboxd 两侧 Developer 网络硬开关全部关闭；生产默认开启的 `infrastructure_enable_allowed` 不直接启用工具。
3. 核对安装 manifest 与运行时 manifest 字节一致；在业务 feature off 状态完成 UDS health/ready、三个镜像、两个 AppArmor profile、磁盘和六组真实 Docker Smoke。
4. 核对 `infrastructure_enable_allowed=true`；如维护期间曾显式关闭，只能在上述门禁通过后由 root 恢复，业务开关仍保持关闭。
5. 在 Web 中选择一个真实私聊 canonical session，授予 Workspace 能力并设置配额。
6. 等待 set_access operation 为 succeeded，quota 为 applied，desired 与 applied 一致。
7. 只打开 sandbox.enabled，验证 workspace_read/search/write/edit、容量和 Trace；
   确认旧 workspace_list/apply_patch 不进入模型 ToolPlan。
8. 把同一 session 升级为 Assets；等待 operation 成功后验证上传、授权、Range 下载和 session 绑定 Token。
9. 选择 `restricted` Profile，把同一 session 升级为 Exec；确认后再单独打开 `sandbox.exec_enabled` 与 session 执行硬开关，验证 `network=none`、一次性容器、超时、OOM、输出限制和回收。
10. 选择一个独立的灰度 session 使用 `developer` Profile；先保持两侧网络硬开关关闭，验证 Developer Lease 在无网络状态下失败关闭。
11. 同时打开 Server 与 sandboxd 的 Developer 网络硬开关，验证匿名 GitHub／PyPI／npm allowlist、IP 直连拒绝、私网／宿主／其他 Lease 拒绝，以及代理环境变量被清空后仍无直连路径。
12. 验证长任务 poll、stdin、dev server loopback、整 Lease terminate、Lease 重建和 controller 重启语义。
13. 运行真实 Agent 兼容性 Eval，保存每个 case 的唯一 artifact 和汇总报告。
14. 观察至少一个完整使用周期，再逐个增加私聊 session。
15. `trusted_developer`、私有仓库凭据和群聊保持关闭，直到分别完成独立设计、威胁建模与验收。

HTTP 202 只代表操作已持久化入队，不代表宿主 Workspace 或 project quota 已生效。每一步都必须以 operation 和 applied 状态为准。

Profile 变更不是只改数据库字符串。切换 Profile 必须定向 quiesce 目标 Workspace、回收旧 Lease、按新 Profile 重建，并验证 controller 返回的镜像、AppArmor、网络策略和完整策略 SHA。其他 Workspace 的活动 Lease 不应阻塞该操作。

## 4. 配额灰度

配额可在 Web 中修改：

- 扩容仍受 Sandbox 总预算和磁盘水位约束。
- 缩容不得低于 used_bytes。
- 修改后必须等待新 generation 应用完成。
- 失败时权限失败关闭，不允许继续沿用 desired/applied 不一致的 Workspace。
- project ID 由数据库自动分配，运维不得手填或复用。
- Workspace 和 Runtime 使用独立 project ID 与硬配额，不能把 `/runtime` 的软用量核算当成隔离边界。
- 配额修改只 quiesce 目标 Workspace；其他 Workspace 的活动 Lease 不得导致目标配额操作失败。

多 session 共享同一 Workspace 尚未开放；不要通过直接改数据库模拟共享。

## 5. 立即回滚

1. 调用 Web kill switch 或 `POST /api/v1/admin/sandbox/kill-switch`，关闭 `sandbox.enabled` 与 `sandbox.exec_enabled`，并核对全部活动 Process／Lease 的定向回收结果。
2. 如需撤销单个会话，在 Web 把该 canonical session 能力设为 off；这不会删除数据。
3. 对 kill switch 返回的失败项使用管理接口按 `lease_id` 重试；sandboxd 只能处理名称前缀、`com.nanobot.managed=true` 与 `com.nanobot.managed-by=sandboxd` 都匹配的自身资源。
4. 将宿主 infrastructure_enable_allowed 恢复为 false，作为第二道上限。
5. 同时关闭 Server 与 sandboxd 的 Developer 网络硬开关。
6. 选择一个内部一致的回滚发布单元：Nanobot Runtime、sandboxd、部署 Profile manifest、Restricted 镜像、Developer 镜像和代理镜像必须来自同一次已验证发布。
7. 回滚过程中保持 `/etc/nanobot/sandbox-execution-profiles.v1.json` 与 `/run/nanobot-sandboxd/profile-manifest.json` 一致；禁止新 Server 与旧 manifest 混用，也禁止旧 sandboxd 使用新 manifest。
8. sandboxd 重启会回收全部旧 Lease；确认 Workspace／Runtime 保留，并等待 Server 把未完成 Run 收敛为 `controller_restarted`。
9. 保留当前与最近一个已验证的三镜像发布单元。
10. 保留新增数据库表、Workspace、Runtime、Asset、`sandbox_access_grants`、`workspace_quota_bindings`、`sandbox_admin_operations` 与备份。
11. 保留旧 KT 文件／Bash 工具的硬禁用，避免应用回滚形成绕过。

回滚不得：

- 删除 ToolOverride 以外的通用工具配置；
- 删除数据库新表或降级 schema；
- 删除 `/srv/nanobot`、Workspace、Asset 或 project quota 元数据；
- 执行全局 Docker prune、volume prune 或 compose down -v；
- 把代理环境变量当作网络隔离的替代品；
- 为了恢复 Developer 网络而临时开放 bridge 直连、私网或任意公网；
- 临时恢复宿主 bash/read/write 作为降级路径。

## 6. 故障分流

- runtime_unavailable：检查 UDS、sandboxd、Docker、管理 Token、固定镜像和 AppArmor；不降级到宿主命令。
- Profile policy mismatch：核对 Server 与 sandboxd 的 manifest 文件 SHA、`catalog_generation`、`policy_sha256` 和三个 IMAGE ID；不要只更新其中一个服务。
- `controller_restarted`：这是最小 v1 的预期终止原因。确认旧 Lease 已回收、Workspace／Runtime 保留，再由新执行透明创建 Lease；不要尝试复用旧 `process_id`。
- Developer 网络拒绝：先核对两侧硬开关、每 Lease 内部网络、固定代理和 uplink；不要通过设置 `NO_PROXY`、共享 bridge 或开放宿主网关绕过。
- disk_pressure：拒绝新写入和执行，保留已有数据；检查 df、inode、总配额和水位。
- workspace_quota_exceeded：停止模型重试，核对 used、desired、applied 和宿主 project quota。
- operation_superseded：说明更高 generation/version 的管理操作已取代旧请求，刷新页面确认最终期望状态。
- operation retry_wait：检查 next_attempt_at 和 error_code，不重复手工提交同一业务请求。
- execution_timeout、process_oom_killed、output_limit_exceeded：确认所属 Lease 已整体回收，检查脱敏运行账本与 Docker inspect，不提高模型可申请上限。
- asset_not_authorized：按 canonical session 和 Workspace 链接排查，不能仅凭 hash 放行。
- 下载失败：保持短期 HMAC、session 收件人和 Range 校验，不改成公开静态 URL 或宿主路径。

## 7. 回滚验收

回滚完成后至少确认：

- 宿主硬上限和两个业务开关均为 false；
- session 执行硬开关与两侧 Developer 网络硬开关均为 false；
- 11 个 Sandbox 工具不进入任何未授权 session 的 wire schema；
- active/running Sandbox Process 与 Lease 已真实回收，相关 Run 已进入明确终态；
- Server／sandboxd manifest、三个镜像和两个 AppArmor profile 属于同一已验证发布单元；
- 非 Nanobot 容器、镜像和 volume 未变化；
- Workspace、Runtime、Asset、grant、quota binding、operation 和审计记录仍存在；
- 主聊天、固定 Worker 和非 Sandbox 工具继续健康；
- Trace 与 Web 页面未泄漏命令、文件正文、Token 或宿主路径。
