# Nanobot Sandbox 运维手册

## 1. 控制面与事实源

Sandbox 由宿主 systemd 服务 sandboxd 通过 Docker Engine API 创建受控容器。`restricted` Profile 每条命令使用一次性容器；`developer` Profile 使用可重建 Lease，并在同一 Lease 内通过多次 Docker Exec 支持长任务和本地开发服务。Nanobot Server 只访问 Unix Socket，不挂载 Docker Socket 或 `/srv/nanobot`；Worker 不访问 sandboxd。

生产控制面有三类彼此独立的状态：

- 宿主基础设施许可：`sandbox.infrastructure_enable_allowed` 只允许由 root 管理的环境变量设置，生产默认开启，Web 不可修改；它只允许 Web 进一步开启能力，不直接授权 session 或启用工具，维护与应急时仍可显式关闭。
- 业务开关：`sandbox.enabled`、`sandbox.exec_enabled` 与 `sandbox.group_enabled`，由 Web「Sandbox 管理」页控制；宿主硬开关、会话授权与 Profile 门禁仍可独立拒绝执行。
- 网络硬上限：Server 和 sandboxd 的 `NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED` 必须同时为 `true`，缺少任一侧都拒绝新的 Developer 网络执行。
- 会话授权：`sandbox_access_grants` 是 11 个 Sandbox 工具的唯一授权事实源，通用 ToolOverride 和 `private_superuser` 均不能授权 Sandbox。
- 执行策略：部署 Profile manifest 是 Server 与 sandboxd 共享的完整策略事实源。双方必须同时核对 `catalog_generation` 和完整 `policy_sha256`，不能只比较镜像摘要。

构建阶段从 `config/sandbox-execution-profiles.v1.json` 生成固定镜像身份的部署 manifest。生产安装位置为：

```text
/etc/nanobot/sandbox-execution-profiles.v1.json
```

sandboxd 启动前把同一文件以 `root:nanobot-sandboxd`、`0640` 复制到：

```text
/run/nanobot-sandboxd/profile-manifest.json
```

sandboxd 和 Nanobot Server 都读取该运行时文件。禁止分别维护两份 Profile 配置，也禁止把新 Server 与旧 manifest 混合部署。

### 1.1 执行 Profile

| Profile | 执行模式 | 网络 | 长任务与 stdin | 授权状态 |
|---|---|---|---|---|
| `restricted` | 每条命令一次性容器 | `network=none` | 不支持 | 可授权 |
| `developer` | Workspace 级 Lease + Docker Exec | 每个 Lease 的受控代理网络 | 支持前台长任务、轮询和 stdin；不支持 detached | 可授权，但受双网络硬开关限制 |
| `trusted_developer` | 预留 Lease | 未开放 | 未开放 | `grantable=false`，镜像 allowlist 为空 |

`trusted_developer` 只是不可授权占位。当前里程碑不注入 PAT、SSH key、GitHub App Token、宿主 SSH Agent 或其他私有仓库凭据。

授权主体是 canonical chat session：

~~~text
chat_stream_id = platform:encoded_external_session_id:chat_type
~~~

`user_id` 只用于页面显示、操作者识别和审计，不能作为授权键。首期每个私聊 session 默认拥有独立 Workspace；未来如需共享，必须通过显式 Workspace 绑定功能实现，不能自动按 `user_id` 合并。

project quota 的唯一事实源是 `workspace_quota_bindings`。project ID 由数据库从 10000 起原子分配，宿主 TSV 不再参与日常分配。

## 2. 上线硬门禁

以下条件全部满足前，必须保持 `sandbox.enabled`、`sandbox.exec_enabled`、session 执行硬开关和两侧 Developer 网络硬开关关闭；生产默认开启的宿主基础设施许可不能越过这些业务、授权与隔离门禁：

1. /srv/nanobot 是独立 XFS/ext4 文件系统挂载点，并启用 project quota；允许使用受控的 16 GiB 预分配 XFS loopback。
2. Workspace、Asset Store、runtime 的容量预算、备份和磁盘水位已配置。
3. Docker SecurityOptions 包含 builtin seccomp 和 AppArmor。
4. `nanobot-sandbox-restricted` 与 `nanobot-sandbox-developer` 两个 AppArmor profile 已精确加载；任一 profile 缺失时必须失败关闭。
5. Restricted、Developer 和出口代理三个固定镜像都存在，实际 IMAGE ID 与部署 manifest 一致；两个执行镜像的默认用户为 `10001:10001`，代理默认用户为 `13:13`。
6. 普通 sandboxd Token 与独立管理 Token 均为 root 管理的 0600 文件。
7. Server 与 sandboxd 读取同一份部署 manifest，并同时匹配 generation 与完整策略 SHA。
8. `scripts/sandbox-smoke-test.sh` 的六组真实 Docker 隔离矩阵全部通过；任何失败、跳过、0 tests、JUnit 缺失或前置条件缺失都不得记为通过。
9. Developer 网络只在 Server 与 sandboxd 两侧硬开关都打开时可用，`trusted_developer` 仍不可授权。
10. Nanobot Server、Worker 和 Sandbox 容器均看不到 Docker Socket。
11. 协调备份、恢复、controller 重启回收和无损 kill switch 已演练。

代码测试通过、healthz 成功或 Docker 配置看起来正确，都不能替代真实隔离 Smoke。

### 2.1 当前宿主验收状态

截至 2026-07-26，当前开发宿主只能完成实现和静态回归，不能作为生产隔离验收通过的证据：

- 当前会话 EUID 为 `1000`，不能运行要求 root 的真实 Smoke；
- Docker SecurityOptions 只有 builtin seccomp 与 cgroup namespace，没有报告 AppArmor；
- `/sys/kernel/security/apparmor/profiles` 不可读；
- `/srv/nanobot` 尚未建立为独立 project quota 数据盘；
- `xfs_quota` 与 `quota` 工具未安装。

因此当前状态是 `BLOCKED`，不是 `passed`。必须在满足本节全部宿主前置条件的生产候选机上重新运行完整六组矩阵。

## 3. 安装与部署顺序

以下完整链路只用于首次安装，或 Sandbox 镜像输入确实变化的版本。生产脚本只负责基础
设施，不再接受 owner ID 或 project ID：

~~~bash
sudo scripts/manage-sandbox-production.sh configure ...
sudo scripts/manage-sandbox-production.sh prepare-host --initialize-storage
sudo scripts/manage-sandbox-production.sh build-image
sudo scripts/manage-sandbox-production.sh smoke
sudo scripts/manage-sandbox-production.sh install-control-plane
~~~

`build-image` 构建并固定以下发布单元：

- `nanobot-sandbox-python:<VERSION>`；
- `nanobot-sandbox-developer:<VERSION>`；
- `nanobot-sandbox-egress-proxy:2026.07.25`；
- 包含以上三个实际 IMAGE ID 的部署 Profile manifest。

`image-built` 阶段凭据同时绑定三个镜像引用、三个 IMAGE ID 和部署 manifest SHA256。`--reuse-built-image` 只有在 Restricted、Developer、代理的全部构建输入、canonical manifest 和 manifest 渲染器均未漂移时才允许复用。

`deploy` 与 `deploy-runtime` 已停用。Runtime 只能从独立发布树通过完整 OCI digest、
SBOM、验证结果和 ReleaseManifest 交给 `scripts/deploy-production-coordinated.sh`；Sandbox 管理脚本
不会再调用 `docker-build.sh` 或切换 `nanobot-runtime:latest`。正式部署前后 Sandbox
业务、session 执行和 Developer 网络开关仍应关闭；宿主基础设施许可生产默认开启，
但不会自动启用工具或授予 session。provision-owner、enable-workspace、enable-assets、enable-exec 和
disable-owner 是兼容拒绝入口，不会执行旧 ToolOverride 或 TSV 操作。

### 3.1 日常快速部署

日常发布先按变更影响选择 1 个正式入口，不再逐个执行 `update-release`、`prepare-host`、
`smoke`、备份、Prompt 审计和 Runtime 部署。

| 变更影响 | 正式入口 | 自动跳过的工作 |
|---|---|---|
| 仅 Sandbox 控制面，镜像输入未变 | `manage-sandbox-production.sh upgrade-control-plane` | Sandbox 镜像构建、Runtime、数据库迁移、Prompt、协调备份 |
| Runtime 变更 | `deploy-production-coordinated.sh` | 未变化的 Prompt 审计、未变化的 migration 备份；同版本则全部跳过 |
| Sandbox 镜像输入变化 | 本节完整链路 | 不复用旧 Sandbox 镜像，仍要求完整真实 Smoke |

控制面快速升级命令：

~~~bash
sudo scripts/manage-sandbox-production.sh upgrade-control-plane \
  --release "$(git rev-parse HEAD)" \
  --release-ref origin/master
~~~

它在单个 sudo 进程内按回执执行 `update-release → prepare-host → smoke →
install-control-plane`。宿主 apt 包已经齐全时不会运行 `apt update/install`；Smoke Python
环境按 Python 版本与 `requirements-sandbox-smoke.lock` Hash 复用；sandboxd 依赖按
`requirements-sandboxd.lock` Hash 复用。失败后重复执行同一命令，已通过的阶段不会重做。
控制面安装前后的 Developer 网络硬开关保持原值。

Runtime 协调部署命令：

~~~bash
sudo NANOBOT_PRODUCTION_ROOT="${NANOBOT_PRODUCTION_ROOT}" \
  NANOBOT_RUNTIME_IMAGE="${NANOBOT_RUNTIME_IMAGE}" \
  NANOBOT_RELEASE_MANIFEST="${NANOBOT_RELEASE_MANIFEST}" \
  scripts/deploy-production-coordinated.sh
~~~

该入口先输出结构化部署计划。判断规则固定如下：

- 目标 Runtime 与四个固定容器已经一致且健康：不关闭业务开关，不重复部署；
- `prompt_defaults` Hash 未变化：不执行 Prompt 审计；
- migration head 未变化：不执行协调备份；
- 首次尝试已经生成目标版本备份：失败重试复用同一目录，拒绝创建第二份；
- Runtime 已切换、只剩业务状态恢复：重跑只恢复维护前开关并清理续跑状态。

协调入口只在确有 Runtime 切换时临时关闭 Sandbox 与群学习相关业务开关，并在退出 trap
中恢复前态。它不会执行 Docker prune，也不会把 Token 或环境变量写入发布状态。

质量门禁会把整个 push 的 Release Impact 作为 artifact 交给 Runtime workflow。影响报告
不包含 `nanobot-runtime` 时，CI 跳过 Runtime 镜像、SBOM、GHCR 推送和发布包。Git SHA
仍随提交变化，但宿主运维脚本或纯控制面变更不再因此产生无效 Runtime 发布。

接受单盘故障风险时，配置必须显式写出同盘模式和风险确认；不能通过省略参数或调用普通 Compose 绕过：

~~~bash
sudo scripts/manage-sandbox-production.sh configure \
  --storage-mode loopback \
  --loopback-size-gib 16 \
  --backup-mode local_same_disk \
  --backup-mount /var/backups/nanobot-sandbox \
  --accept-local-same-disk-risk \
  --release <已发布的精确提交>
~~~

该模式固定单次协调备份上限为 16 GiB，并在备份前后强制保留至少 60 GiB 根分区可用空间。同盘备份仅用于逻辑回滚，不提供硬盘灾备；这些约束不放宽真实 Docker Smoke、AppArmor、seccomp、断网、权限或配额门禁。

loopback 镜像必须同时满足逻辑大小和实际分配空间均为 16 GiB。脚本使用
`mkfs.xfs -K` 禁止格式化阶段 discard 已预分配的 backing file 块，并在构建、
Smoke、控制面安装和协调备份前重复执行实际分配门禁；正式 Runtime 部署器另行执行
pull-only 系统水位门禁。可用下列只读命令
独立核对；返回 0 且 `actual_allocated_bytes` 不小于
`17179869184` 才算通过：

~~~bash
scripts/check-loopback-image-allocation.sh \
  /var/lib/nanobot-sandbox-storage/data.xfs \
  17179869184
~~~

loop 设备会向上报告 discard 能力，宿主的周期性 `fstrim` 可能再次释放 backing
file 的未用块。因此官方 fstab 行固定包含 `X-fstrim.notrim`；脚本只会把自己
生成的旧精确行原子迁移到该配置，并保留 `/etc/fstab` 备份。发现其他同目标
挂载行或正在运行的 `fstrim.service` 时拒绝迁移。

如果旧版脚本已建立 XFS，但后续 `image-built` 等阶段尚未开始，且数据盘只含
tmpfiles 创建的空目录，可以使用官方原地修复入口。先将修复提交发布到
`origin/master`，在干净 checkout 上只更新 RELEASE；该操作保留既有存储、
备份、配额和 GID 配置：

~~~bash
sudo scripts/manage-sandbox-production.sh update-release \
  --release <修复提交的完整哈希> \
  --version <修复提交短哈希>-<日期>

sudo scripts/manage-sandbox-production.sh prepare-host \
  --repair-loopback-allocation
~~~

修复入口要求交互输入精确确认文本，只会在卸载既有 XFS 后使用
`fallocate --keep-size` 补足 backing file 的洞；它不格式化、不删除、不重建
文件系统，并在重新挂载前核对 XFS UUID 和运行 `xfs_repair -n`。检测到任何
用户 Workspace/Asset/runtime 内容、活动 Sandbox、运行中的 sandboxd、额外
loop 关联或后续阶段凭据时都会失败关闭。

如果 `image-built` 已完成，但真实 Smoke 在宿主测试依赖安装阶段失败，修复提交
没有修改 `scripts/build-sandbox-image.sh` 或 `docker/sandbox/python/`，可以显式
复用已经核验过的固定镜像：

~~~bash
sudo scripts/manage-sandbox-production.sh update-release \
  --release <修复提交的完整哈希> \
  --reuse-built-image

sudo scripts/manage-sandbox-production.sh prepare-host
sudo scripts/manage-sandbox-production.sh smoke
~~~

该入口要求新提交是旧 RELEASE 的快进后代，且 `smoke-passed`、
`control-plane-ready`、`runtime-deployed` 均不存在。复用时 VERSION、IMAGE ID
和 `image-built` 凭据保持不变；旧 RELEASE 的失败 Smoke worktree 作为现场证据
保留，不会自动删除。

如果真实 Smoke 已通过、但控制面安装尚未通过，且修复提交仍未修改 Sandbox
镜像输入，可以显式归档旧 Smoke 阶段凭据，并要求新 RELEASE 重新执行完整
Smoke：

~~~bash
sudo scripts/manage-sandbox-production.sh update-release \
  --release <修复提交的完整哈希> \
  --reuse-built-image \
  --rerun-smoke

sudo scripts/manage-sandbox-production.sh prepare-host
sudo scripts/manage-sandbox-production.sh smoke
sudo scripts/manage-sandbox-production.sh install-control-plane
~~~

`--rerun-smoke` 只能与 `--reuse-built-image` 同时使用；已有
`control-plane-ready` 或 `runtime-deployed` 时仍会失败关闭。脚本会先校验旧
Smoke 证据与当前固定镜像一致，再将阶段凭据改名归档；不会删除原始证据目录，
也不会把旧 Smoke 结果沿用到新 RELEASE。

RELEASE 始终由完整提交哈希固定。默认发布来源为 `origin/master`；为了避免把宿主
验收期间发现的问题逐次写入主分支，也可以显式使用
`origin/release-candidates/<名称>`。其他本地分支、tag 和任意远端分支都会被拒绝。
候选提交必须已经推送到该远端候选引用，生产 checkout 必须精确位于候选提交且
保持干净：

~~~bash
sudo scripts/manage-sandbox-production.sh update-release \
  --release <候选提交的完整哈希> \
  --release-ref origin/release-candidates/sandbox-control-plane \
  --reuse-built-image \
  --rerun-smoke
~~~

同一候选哈希完成 Smoke 与控制面验收前，不进入 `master`。验收通过后必须将该提交
原样 fast-forward 到 `master`，不能 squash、amend 或重新生成提交；否则提交哈希
变化，原阶段证据失效。确认远端
`origin/master` 已包含完全相同的 RELEASE 后，执行：

~~~bash
sudo scripts/manage-sandbox-production.sh promote-release
~~~

该命令要求 `smoke-passed` 与 `control-plane-ready` 已完成，只更新 Sandbox 控制面
配置中的发布来源，不修改 RELEASE、VERSION、镜像或阶段凭据。Runtime 的
current/pending/rollback 由 ReleaseManifest 部署器独立管理。`origin/master` 或候选引用后续前进时，脚本只验证
固定 RELEASE 仍属于对应引用历史，不要求 RELEASE 等于远端最新 tip，也不会静默
替换提交。每个生产阶段都会重新验证发布来源，`status` 会显示当前来源。

`install-control-plane` 先从目标发布目录安装锁定依赖和 systemd 文件；确认没有
活动 Sandbox 容器后，才把 `/opt/nanobot-server` 原子切换到目标 RELEASE 并重启
sandboxd。旧发布目录继续保留用于回滚。当前链接若不属于受管发布目录、版本标记
无效，或不是目标 RELEASE 的祖先，脚本都会失败关闭，不会覆盖该链接。

宿主基础设施许可在生产 Runtime 中默认为：

~~~text
NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED=true
~~~

该值只代表“允许 Web 开启”，不会自动打开业务开关或授予任何 session。维护或应急期间
如曾由 root 显式设为 `false`，只能在基础设施门禁恢复后再改回 `true`。

## 4. 数据盘与目录

按变更单准备独立块设备文件系统，或经明确风险确认准备 16 GiB XFS loopback，再执行只读门禁：

~~~bash
scripts/check-sandbox-data-disk.sh /srv/nanobot
~~~

参考模板：

- deploy/storage/sandbox-xfs-prjquota.example
- deploy/storage/sandbox-ext4-project-quota.example

随后安装 tmpfiles 配置并核验权限：

~~~bash
install -m 0644 deploy/systemd/nanobot-sandboxd.tmpfiles.conf \
  /etc/tmpfiles.d/nanobot-sandboxd.conf
systemd-tmpfiles --create /etc/tmpfiles.d/nanobot-sandboxd.conf
namei -l /srv/nanobot/workspaces /srv/nanobot/assets /srv/nanobot/runtime
~~~

Workspace/Asset 必须位于单独挂载到 `/srv/nanobot` 的受控文件系统，不得直接写入根文件系统目录、仓库目录、容器 writable layer 或 WSL `/mnt/d`。`local_same_disk` 只表示 loopback backing file 与逻辑备份可位于根盘，不表示取消 `/srv/nanobot` 的独立文件系统、project quota 或水位门禁。

## 5. sandboxd 管理接口与权限

sandboxd 使用两个独立 Token：

- 普通 Token：文件、资产、Lease 和 Process 执行接口。
- 管理 Token：Workspace ensure、project quota apply/inspect、Lease 管理和 controller 状态接口。

管理接口只接受 request_id、workspace UUID、project ID、quota bytes 和 generation，不接受宿主路径、命令或 Docker 参数。普通 Token 调用管理接口必须返回拒绝。

Lease 和 Process 的对象级授权每次都重新核对：

- canonical session 与 Grant；
- `workspace_id`；
- `lease_id`；
- `process_id` 归属；
- `profile_id`、controller epoch、catalog generation 与完整策略 SHA。

只知道 `process_id` 不能读取输出、写 stdin 或终止其他 session／Workspace 的进程。`sandbox_terminate` 的最小安全终止边界是整个 Lease：它会回收容器并终止该 Lease 内全部活动进程，不承诺只杀单个 PID。

systemd 单元因 project quota helper 需要 CAP_SYS_ADMIN；sandboxd 同时持有 Docker Socket，本身就是 root 等价控制面。必须保持：

- 固定 helper 路径 /opt/nanobot-server/scripts/assign-sandbox-project-quota.sh；
- helper 为 root 拥有的普通文件，不是符号链接，group/other 不可写；
- 结构化 argv，shell=False；
- subprocess stdout/stderr 不进入 API、日志或 Web 错误详情。

## 5.1 Lease 与 Process 生命周期

Developer 执行流程如下：

1. Server 根据 Grant 选择 `developer` Profile，确认 quota 和双网络硬开关。
2. sandboxd 创建或复用当前 Workspace 的 Lease，并重新验证容器、挂载、镜像、AppArmor、网络拓扑和 controller epoch。
3. 每次 `sandbox_exec` 都以新的 `/bin/bash -lc` 创建 Docker Exec；`cd`、`export`、`alias` 和 shell 激活状态不跨命令保存。
4. 命令在 `yield_time_ms` 内未结束时返回不透明 `process_id`；后续通过 `sandbox_poll` 读取增量输出，通过 `sandbox_write_stdin` 写入 stdin。
5. 命令完成后由 Server 根据 sandboxd 的权威结果写入 `SandboxRun` 终态；没有 sandboxd 主动反向写数据库的通道。

以下事件会整体回收 Lease：

- `sandbox_terminate`；
- 命令硬超时；
- 输出硬上限；
- controller epoch 漂移；
- sandboxd 正常停止或重启；
- kill switch；
- 管理员 stop、destroy 或 recreate；
- 周期 reconciler 发现策略、镜像、网络或归属事实漂移。

sandboxd 重启时不尝试恢复旧 Docker Exec。旧 Lease 被定向回收，相关运行由 Server 主动 reconcile 为 `controller_restarted`。`/workspace` 和 `/runtime` 保留，`/tmp`、旧容器与旧 `process_id` 失效；下一次已授权执行会创建新 Lease。

后台进程不得通过 `cmd &`、`nohup` 或 detached 模式逃离控制。需要运行 dev server 时，必须以前台命令启动，使用较短 `yield_time_ms` 返回，再用第二条 `sandbox_exec` 访问同一 Lease 的 loopback。

## 6. Web 会话授权

在 Web「Sandbox 管理」页完成授权：

1. 从服务端真实出现过的私聊 session 中选择目标。
2. 核对 canonical chat_stream_id；user_id 仅作显示。
3. 选择 off、workspace、assets、exec 四档能力。
4. 选择 `restricted` 或 `developer` 执行 Profile；`trusted_developer` 不得出现在可授权选项中。
5. 核对页面展示的执行模式、网络、工具链、最长命令时间、stdin 与长期进程能力摘要。
6. 输入 Workspace 配额和审计原因。
7. 一次保存产生一个 set_access operation。
8. 等待 operation succeeded，且 quota 状态为 applied、desired 与 applied 完全一致。
9. 最后再按灰度阶段打开对应业务开关；Developer 网络还要单独满足两侧硬开关。

能力包含关系固定为：

~~~text
off < workspace < assets < exec
~~~

降级和关闭立即收窄权限；升级必须等待 sandboxd 创建 Workspace 并确认 project quota。关闭能力不会删除 Workspace、Asset、project ID、grant 或 operation 账本。

## 7. 配额修改

Workspace 配额可从 Web 独立修改：

- 缩容不得低于 Workspace 当前 used_bytes。
- 总 desired quota 不得超过 sandbox.total_quota_bytes。
- 每次修改增加或沿用受 fencing 保护的 generation。
- desired 与 applied 不一致、状态非 applied 或应用失败时，该 Workspace 的全部 Sandbox 工具保持拒绝。
- project ID 只读且不会因关闭能力而回收。
- Workspace 与 `/runtime` 使用独立 project ID 和硬配额；`/runtime` 不能只依赖目录用量软核算。

管理写操作返回 202 和 operation_id。持久化 runner 使用租约、幂等 request ID、有限重试、指数退避、generation/version fencing 和重启恢复；不得把 HTTP 202 误判为宿主配额已经生效。

配额 helper 只按 `com.nanobot.workspace-id=<目标 Workspace UUID>` 查询活动 Lease。修改目标 Workspace A 的配额时，流程固定为：

1. 定向 quiesce A；
2. 停止 A 的 Lease；
3. 分别应用并读回 Workspace 与 Runtime quota；
4. 按 A 原 Profile 重建 Lease；
5. 解除 A 的 quiesce。

其他 Workspace 的活动 Lease 不构成阻塞条件。特别是 Workspace B 的 Lease 活跃时，仍必须能够为 A 应用配额。禁止恢复为全局 `docker ps` 判定，也禁止为了修改单个 Workspace 配额而停止所有 Lease。

## 8. 旧 TSV 一次性迁移

旧 /etc/nanobot/sandbox-projects.tsv 只允许通过一次性迁移工具读取。先完整预检：

~~~bash
python scripts/migrate-sandbox-project-map.py \
  --database /opt/nanobot-server/nanobot.db \
  --map /etc/nanobot/sandbox-projects.tsv \
  --data-root /srv/nanobot
~~~

确认预检结果后再显式应用：

~~~bash
python scripts/migrate-sandbox-project-map.py \
  --database /opt/nanobot-server/nanobot.db \
  --map /etc/nanobot/sandbox-projects.tsv \
  --data-root /srv/nanobot \
  --apply
~~~

迁移会校验数据库迁移版本、Workspace 状态、quota、project ID 冲突和宿主 xattr，并创建 root-only 固定备份。导入绑定初始为 pending、applied_quota_bytes=0，不创建 grant；必须再由 Web/operation runner 重新确认后才能授权。迁移完成后 TSV 只作历史证据，不再写入。

## 9. runtime TTL 清理

runtime 只保存可重建缓存，不进入备份。默认先预览：

~~~bash
sudo scripts/manage-sandbox-production.sh runtime-cleanup
~~~

实际执行要求 kill switch 已关闭执行入口、没有活动 Sandbox 容器，并显式批准：

~~~bash
sudo scripts/manage-sandbox-production.sh runtime-cleanup --apply
~~~

清理不得删除 Workspace 或 Asset，不得执行 Docker 全局 prune。

## 10. 真实隔离 Smoke

默认测试会跳过真实 Docker 隔离项。生产验收必须显式运行：

~~~bash
sudo scripts/sandbox-smoke-test.sh \
  --manifest /run/nanobot-sandboxd/profile-manifest.json \
  --data-root /srv/nanobot \
  --evidence-root /var/cache/nanobot/sandbox-smoke
~~~

生产管理入口会在宿主机临时 worktree 中创建独立 Python 3.11 测试环境，
仅按 `requirements-sandbox-smoke.lock` 安装带哈希的 pytest 与 Docker SDK
依赖。该环境不是模型执行 Sandbox，也不得安装 Nanobot 完整测试依赖、
KT、Torch 或执行未锁定的 pip 自升级。

脚本先执行 root、Docker、seccomp、AppArmor、两个已加载 profile、独立数据盘、project quota、三个固定镜像和 `trusted_developer` 不可授权等前置检查，再运行六组矩阵：

1. 基础安全；
2. Lease；
3. Process；
4. Developer 工具链；
5. 网络；
6. 数据连续性。

每组保存独立 pytest 日志、JUnit XML 和退出码，最终生成 `summary.json`。只有六组都包含真实测试，且 `failures=0`、`errors=0`、`skipped=0`，才允许写入 `smoke-passed` 阶段凭据。任一 skip、0 tests、JUnit 缺失、解析失败或前置检查失败都必须记为 `blocked` 或 `failed`。

矩阵至少验证非 root、只读根、无 Docker Socket、cap drop、AppArmor、seccomp、CPU／内存／PID／tmpfs 限制、Lease 重建、Docker Exec loopback、增量输出、stdin、整 Lease 终止、controller 重启回收、Developer 工具链、真实出口拒绝矩阵、Workspace／Runtime project quota、跨 Lease 数据连续性和 A／B Workspace 隔离。

Smoke 前后保存 df、inode、Docker 占用、容器、网络、镜像和 AppArmor 清单。脚本只清理本轮随机 Lease、Workspace、网络和 project ID，禁止全局 prune，也禁止删除未知资源。

只检查宿主前置条件时可运行：

```bash
sudo scripts/sandbox-smoke-test.sh \
  --manifest /run/nanobot-sandboxd/profile-manifest.json \
  --data-root /srv/nanobot \
  --evidence-root /var/cache/nanobot/sandbox-smoke \
  --preflight-only
```

## 11. 监控与应急关闭

Web 页面展示 sandboxd 健康、镜像 ID、AppArmor、磁盘水位、Workspace 占用、operation、运行账本和审计；不展示命令、stdout/stderr、文件正文或宿主路径。

出现控制面、磁盘、配额或隔离异常时：

1. 调用 POST /api/v1/admin/sandbox/kill-switch。
2. 确认 sandbox.enabled 与 sandbox.exec_enabled 均为 false。
3. 核对返回的 `terminated` 与 `failed` 计数；kill switch 必须拒绝新 Lease／Process，并真实回收全部托管 Lease，不能只改数据库状态。
4. 对失败项通过管理 Token 定向重试，不得按名称模糊删除容器。
5. 只检查同时满足固定名称前缀、`com.nanobot.managed=true` 与 `com.nanobot.managed-by=sandboxd` 的资源。
6. 保留数据库、Workspace、Runtime、Asset、grant、quota binding、operation 和 Run 历史。

禁止全局 prune、删除 volume 或通过恢复旧 KT bash/read/write/edit/grep/glob 绕过故障。
