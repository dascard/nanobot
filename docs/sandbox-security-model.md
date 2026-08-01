# Nanobot Sandbox 安全模型

## 1. 适用范围

本文描述 Restricted 与 Developer Sandbox 的生产安全边界、事实源、生命周期和验收要求。更细的网络攻击矩阵见：

- [Restricted Sandbox 威胁模型](sandbox-threat-model-restricted.md)
- [Developer Sandbox 威胁模型](sandbox-threat-model-developer.md)
- [Sandbox 私有仓库凭据范围](sandbox-private-repository-credentials.md)

目标是在允许模型处理持久 Workspace、授权资产和受控代码执行的同时，阻止其访问宿主、Docker Engine、其他 owner、其他 Lease、未授权资产、私网和未批准的公网目标。

当前设计不把 Docker 当成虚拟机级对抗边界，也不承诺抵御 Docker Engine、runc、Linux 内核、AppArmor、seccomp 或代理软件本身的零日漏洞。宿主修补、最小权限、固定镜像和真实隔离验收共同降低该风险。

## 2. 保护资产与不可信输入

需要保护的资产包括：

- Nanobot 数据库、Server 配置和 Token；
- `/srv/nanobot/workspaces/` 中各 owner 的持久内容；
- `/srv/nanobot/assets/` 中的不可变资产；
- `/srv/nanobot/runtime/` 中各 Workspace 的可重建缓存；
- Docker Socket、宿主文件系统、内核接口和其他服务容器；
- canonical session、Grant、quota、Lease、Run 与审计账本；
- Profile manifest、镜像 IMAGE ID、AppArmor profile 和网络策略。

以下内容一律视为不可信：

- 模型生成的命令、路径、环境变量、stdin 和补丁；
- Workspace 文件、导入仓库、依赖包、构建脚本和测试；
- Sandbox 内的 stdout、stderr、退出码和网络响应；
- 来自客户端的 `workspace_id`、`lease_id`、`process_id` 和 Profile 请求；
- Docker 中与预期同名但所有权标签、镜像或安全参数不一致的资源。

## 3. 组件与信任边界

| 组件 | 可以访问 | 不可以访问 | 权威事实 |
|---|---|---|---|
| Nanobot Server | 数据库、sandboxd Unix Socket、运行时 Profile manifest | Docker Socket、`/srv/nanobot` 宿主路径 | session 身份、Grant、Workspace 绑定、desired quota、Lease／Run 业务账本 |
| sandboxd | Docker Socket、`/srv/nanobot`、quota helper、运行时 Profile manifest | Nanobot 数据库、外部业务身份 | 容器、Process、controller epoch、实际镜像、挂载、网络与宿主 quota 事实 |
| Restricted Sandbox | 当前 `/workspace`、当前 `/runtime`、授权只读 `/inputs` | Docker Socket、宿主路径、网络、其他 Workspace | 无控制面事实 |
| Developer Sandbox | Restricted 可见内容、当前 Lease loopback、受控代理地址 | Docker Socket、宿主网关、共享出口网络、其他 Lease | 无控制面事实 |
| 出口代理 | 当前 Lease 内部网络、专用 uplink | Docker Socket、宿主挂载、凭据、其他 Lease 内部网络 | allowlist、目标端口与拒绝 CIDR 的执行点 |

只有 sandboxd 可以调用 Docker Engine。把 `/var/run/docker.sock` 挂入 Server、Worker 或 Sandbox 等价于授予宿主 root 权限，属于禁止配置。

Server 与 sandboxd 之间没有 sandboxd 主动写数据库的反向通道。sandboxd 返回权威运行事实；Server 的 leader-fenced 周期 reconciler 主动拉取 controller、Lease 和 Process 状态，再收敛 `sandbox_leases` 与 `sandbox_runs`。sandboxd 不直接写 Server 终态。

## 4. 授权与对象归属

Sandbox 唯一授权主体是 canonical chat session：

```text
platform:encoded_external_session_id:chat_type
```

`user_id`、`private_superuser` 和通用 ToolOverride 不能授权 Sandbox。能力等级固定为：

```text
off < workspace < assets < exec
```

每次工具调用都必须重新验证 session、Grant、Workspace、quota generation 和 Profile。涉及 Lease／Process 的调用还必须核对：

- `lease_id` 属于该 Grant 与 Workspace；
- `process_id` 属于该 Lease；
- Profile、controller epoch、catalog generation 和完整策略 SHA 未漂移；
- 调用所需能力仍然有效。

`process_id` 是不透明能力句柄，但不是独立授权。攻击者只知道其他进程的句柄，仍不能 poll、写 stdin 或 terminate。所有对象归属失败都必须返回授权拒绝，不得通过“对象不存在”与“对象属于他人”的差异泄漏跨 owner 信息。

## 5. Profile 模型

### 5.1 Restricted

Restricted 用于短时、离线、一次性执行：

- `execution_mode=oneshot`；
- 每条命令创建独立容器，结束后整体回收；
- `network=none`；
- 不支持 `sandbox_poll`、`sandbox_write_stdin`、`sandbox_terminate`、长任务续接或后台服务；
- `/workspace` 与 `/runtime` 跨命令保留，`/tmp` 每条命令后消失；
- 使用 `nanobot-sandbox-restricted` AppArmor profile。

### 5.2 Developer

Developer 用于持久代码工作台：

- `execution_mode=lease`；
- 同一 Workspace 的命令通过 Docker Exec 进入同一 Lease；
- 支持前台长任务、增量 poll、stdin 和同 Lease loopback；
- 不支持 detached 进程；
- `/workspace` 与 `/runtime` 跨命令和 Lease 重建保留；
- `/tmp` 只在当前 Lease 内存在；
- 使用 `nanobot-sandbox-developer` AppArmor profile；
- 只允许经固定代理匿名访问 canonical allowlist。

### 5.3 trusted_developer

`trusted_developer` 是未来能力占位，当前必须同时满足：

```text
grantable=false
image_allowlist=[]
```

管理台、Server 和 sandboxd 都不得授权该 Profile。当前里程碑不支持私有仓库、PAT、SSH key、GitHub App Token、宿主 SSH Agent、任意公网或写入型外部凭据。

## 6. 容器安全基线

两个执行 Profile 都由服务端固定以下参数，模型不能覆盖：

- 专用固定镜像引用与完整 IMAGE ID；
- 非 root 用户 `10001:10001`；
- 只读根文件系统；
- `cap-drop=ALL`；
- `no-new-privileges`；
- Docker builtin seccomp；
- Profile 对应的 AppArmor；
- 非 privileged；
- 无额外 capability、设备、Docker Socket、宿主凭据目录或任意 bind mount；
- 固定 CPU、内存、PID、tmpfs、执行时间和输出上限；
- `/tmp` 使用有限 tmpfs；
- 只挂载当前 Workspace、当前 Runtime 和授权只读 Inputs。

操作系统依赖由预构建镜像提供。Sandbox 内没有 root 或 sudo，不允许执行 `apt install`。模型不能指定镜像、Docker 参数、volume、network mode、device、namespace、用户或 capability。

同名 Docker 资源不能仅凭名称复用。sandboxd 必须同时校验固定名称格式、`com.nanobot.managed=true`、`com.nanobot.managed-by=sandboxd`、对象 ID、controller epoch、完整策略 SHA 和实际 Docker inspect 参数。事实漂移时失败关闭并定向回收。

## 7. Lease、Process 与终止语义

Developer Lease 是最小进程隔离和终止边界。每条命令使用新的 `/bin/bash -lc`；shell 的当前目录、环境变量、alias 和激活状态不跨命令保存。

命令在 `yield_time_ms` 内未结束时，sandboxd 返回 `process_id`。Server 通过显式 API 执行：

- `sandbox_poll`：按 cursor 读取有界增量输出；
- `sandbox_write_stdin`：向仍在运行且归属匹配的进程写入；
- `sandbox_terminate`：回收所属 Lease，并终止其中全部活动进程。

PGID 不是安全承诺。超时、输出上限、terminate、kill switch、策略漂移和 controller 重启都通过回收整个 Lease 终止进程树。该方案不承诺只杀单个子进程，也不承诺后台进程跨 sandboxd 重启存活。

sandboxd 启动时生成新的 controller epoch，并定向回收所有旧 epoch Lease、代理和内部网络。Workspace 与 Runtime 保留，`/tmp` 和旧 `process_id` 失效。Server 主动 reconciler 将相关活跃 Run 收敛为 `controller_restarted`。下一次已授权执行透明创建新 Lease。

周期 reconciler 还会处理：

- idle TTL 与 max TTL；
- Lease、容器或网络丢失；
- Profile、generation、完整策略 SHA、镜像或 AppArmor 漂移；
- 孤儿代理和内部网络。

它只能定向处理带完整 Nanobot 所有权事实的资源，不得扫描后模糊删除其他容器或网络。

## 8. 存储、配额与连续性

长期事实源位于独立的 XFS／ext4 project quota 文件系统：

```text
/srv/nanobot/workspaces/
/srv/nanobot/assets/
/srv/nanobot/runtime/
```

挂载契约如下：

| 容器路径 | 来源 | 权限 | 生命周期 |
|---|---|---|---|
| `/workspace` | 当前 Workspace | 读写 | 跨容器、Lease 和 controller 重启保留 |
| `/runtime` | 当前 Workspace 的 Runtime | 读写 | 可重建缓存；跨 Lease 保留，不进入备份 |
| `/inputs` | 已授权不可变资产 staging | 只读 | 随 Run／Lease staging 回收 |
| `/tmp` | 有界 tmpfs | 读写 | Restricted 每命令清空；Developer 每 Lease 清空 |

Workspace 与 Runtime 必须使用独立 project ID 和硬配额。目录扫描、`used_bytes` 或水位统计只用于核算与拒绝提前量，不能代替内核硬限制。

配额变更按 Workspace 定向 quiesce：

1. 阻止目标 Workspace 的新写入和执行；
2. 停止目标 Lease；
3. 应用并读回 Workspace／Runtime quota；
4. 按原 Profile 重建；
5. 解除 quiesce。

quota helper 只按精确 `com.nanobot.workspace-id` 标签判断目标 Workspace。其他 Workspace 的 Lease 活跃时，目标配额修改仍必须成功。禁止用全局 `docker ps` 作为配额门禁。

Asset Store 不以可写方式挂入 Sandbox。生成内容先写 Workspace，再由 `asset_publish` 发布为不可变资产。

## 9. Developer 网络边界

`HTTP_PROXY`、`HTTPS_PROXY` 和 Git／pip／npm 代理配置只负责应用选路，不是安全边界。模型可以删除或篡改环境变量，因此强制边界必须来自网络拓扑。

每个 Developer Lease 有独立内部 bridge：

- `internal=true`；
- `enable_ip_masquerade=false`；
- `inhibit_ipv4=true`；
- 不可 attach；
- 只有当前 Sandbox 与当前固定代理加入。

Sandbox 不加入 uplink，也没有默认公网路由或可达的宿主网关。固定代理同时加入当前 Lease 内部网络与专用 uplink。uplink 禁止容器间互通，且 Sandbox 永不加入该网络。不同 Lease 不共享内部 bridge。

代理只允许 canonical manifest 中的 GitHub、PyPI 与 npm 域名；HTTP 目标端口仅为 80／443，CONNECT 仅为 443。域名通过后仍拒绝 IPv4／IPv6 loopback、RFC1918、链路本地、共享地址、云元数据、ULA、保留地址和组播。

以下行为都必须失败：

- 清空代理变量后直连公网；
- 直接连接公网 IP；
- 访问宿主网关、Docker bridge 网关或其他服务容器；
- 访问其他 Lease 的 Sandbox 或代理；
- 通过 allowlist 域名解析到私网；
- CONNECT 到非 443 端口；
- 重定向到非 allowlist、私网或保留地址。

Server 与 sandboxd 的 `NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED` 是独立硬开关，必须同时为 `true`。数据库 Grant 不能越过任一硬上限。

## 10. 出口代理与凭据

出口代理镜像必须固定完整 IMAGE ID，并满足：

- 非 root 用户 `13:13`；
- 只读根文件系统；
- `cap-drop=ALL`、`no-new-privileges`；
- 无端口发布、宿主挂载、Docker Socket、设备或凭据；
- 只开放内部 `3128/tcp`；
- IPv4／IPv6 forwarding 为 0；
- 有界 CPU、内存、PID、tmpfs 与本地日志；
- 与 Lease 同生共死，不配置自动重启。

当前代理只提供匿名访问，不进行 TLS 中间人解密。它限制目标域名、最终解析地址和端口，不检查加密后的 URL 路径或响应内容。allowlist 站点、仓库和依赖包仍不可信，系统不会自动执行陌生仓库脚本。

Secret 不得进入 Profile manifest、Workspace、Runtime、镜像、容器环境、命令参数、stdout／stderr、Trace 或 Web 页面。私有仓库凭据在完成独立威胁模型、短期派生、最小权限、撤销、脱敏与泄漏测试前保持冻结。

## 11. Profile manifest 与部署一致性

canonical 源文件是：

```text
config/sandbox-execution-profiles.v1.json
```

构建脚本严格生成部署 manifest，并写入 Restricted、Developer 与代理的实际完整 IMAGE ID。`trusted_developer` 仍保持空 allowlist。渲染器拒绝：

- 重复 JSON 字段；
- 符号链接；
- `latest`；
- 非完整 SHA256 IMAGE ID；
- 代理引用或 IMAGE ID 与 canonical allowlist 不一致。

部署 manifest 原子写入，生产安装到 `/etc/nanobot/sandbox-execution-profiles.v1.json`。systemd 在 sandboxd 启动前复制同一字节到 `/run/nanobot-sandboxd/profile-manifest.json`，Server 通过既有只读挂载读取该文件。

双方必须比较完整 `policy_sha256`，而不是只比较镜像 digest。Profile 的网络、AppArmor、资源、TTL、并发、stdin、执行模式或授权状态任一变化，都会改变策略 SHA 并使旧 Lease 失效。

发布与回滚的最小一致单元包括：

- Nanobot Server；
- sandboxd；
- 数据库 schema；
- 部署 Profile manifest；
- Restricted 镜像；
- Developer 镜像；
- 出口代理镜像；
- 两个 AppArmor profile。

禁止新 Server 与旧 manifest 混用，也禁止只回滚 tag 而保留不匹配的 IMAGE ID。

## 12. Kill switch 与失败关闭

kill switch 必须执行以下动作：

1. 关闭 `sandbox.enabled` 与 `sandbox.exec_enabled`；
2. 拒绝新的 Lease 与 Process；
3. 通过管理通道定向终止活动进程并回收托管 Lease；
4. 返回 `terminated` 与 `failed` 计数；
5. 保留 Workspace、Runtime、Asset、Grant、quota、Lease／Run 历史和审计。

只修改数据库开关、但让活动容器继续运行，不符合 kill switch 契约。失败项必须按明确 `lease_id` 重试，不能执行全局 Docker prune。

以下条件任一出现都必须拒绝新执行或回收现有 Lease：

- Profile manifest 缺失、schema 无效或策略不匹配；
- 镜像、用户、AppArmor、seccomp、挂载、资源参数或网络拓扑漂移；
- quota desired／applied 不一致；
- 数据盘水位、inode 或 project quota 不满足门禁；
- controller epoch 不一致；
- 对象归属校验失败；
- Developer 网络任一硬开关关闭；
- `trusted_developer` 被错误配置为可授权。

## 13. 日志、审计与隐私

可记录的运行元数据包括：

- request ID、Run ID、Lease ID、Profile ID；
- Workspace 的内部 UUID；
- 状态、时间、退出码、输出截断标记和终止原因；
- catalog generation、策略 SHA 和镜像 IMAGE ID；
- quota generation 与有界用量；
- 管理操作、操作者与审计原因。
- Agent 工具调用 Trace 中经凭据脱敏且有界的 `sandbox_exec` 命令。

不得记录或展示：

- 未经脱敏或超过 16 KiB 上限的原始命令；
- stdout／stderr 正文；
- 文件正文或补丁正文；
- Secret、Token、Cookie、Authorization header；
- 宿主真实路径；
- 完整外部身份；
- 代理响应正文。

## 14. 真实验收

生产验收必须运行 `scripts/sandbox-smoke-test.sh` 的六组矩阵：

1. 基础安全；
2. Lease；
3. Process；
4. Developer 工具链；
5. 网络；
6. 数据连续性。

任一失败、skip、0 tests、JUnit 缺失、解析失败或宿主前置条件缺失都不能通过。结构化 `summary.json` 只有在六组均有真实测试且 0 failure、0 error、0 skip 时才可写入 `smoke-passed` 凭据。

模型体验验收还必须使用真实 Agent 事件 artifact 运行 `evals/sandbox_agent_compatibility.py`。静态 case 与合成 artifact 只能测试评估器，不能证明生产 Agent 已完成任务。

截至 2026-07-26，当前开发宿主状态为：

- EUID 为 `1000`；
- Docker 没有报告 AppArmor；
- `/sys/kernel/security/apparmor/profiles` 不可读；
- `/srv/nanobot` 不存在；
- `xfs_quota` 与 `quota` 不可用。

因此当前只能声明实现和静态回归完成，生产宿主真实隔离验收为 `BLOCKED`。不得把该状态描述为通过。

## 15. 剩余风险与变更规则

主要剩余风险包括：

- Docker、runc、内核、AppArmor、seccomp、Squid 与 DNS 解析器漏洞；
- allowlist 站点或依赖供应链恶意内容；
- 同一 Workspace 内的并发命令互相影响；
- `/runtime` 中被污染的可重建缓存；
- 代理不能检查 TLS 加密后的 URL 路径和内容；
- 最小 v1 不保留跨 sandboxd 重启的后台进程。

以下变更必须重新做威胁建模和真实矩阵，不能仅修改配置：

- 开放 `trusted_developer`；
- 注入私有仓库或其他外部凭据；
- 开放任意公网、内网、宿主服务或额外端口；
- 让多个 owner 共享可写 Workspace；
- 引入 GPU、宿主设备、额外 capability 或新 namespace；
- 允许 detached 进程或跨 controller 重启恢复；
- 改用其他容器运行时或远程执行平台；
- 改变 Workspace／Runtime／Asset 的持久化与备份边界。
